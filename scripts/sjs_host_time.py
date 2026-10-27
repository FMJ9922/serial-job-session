#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only host timing adapters for serial-job-session v5.

The v5 core owns persistence and estimation policy.  This module only reads
host rollout JSONL / history SQLite records and returns explicit timing
records for the core to persist.  It never writes Codex databases, card files,
configuration, or a local ledger.

The timing model keeps four facts separate:

* active work intervals (unioned, so nested tool/item intervals do not count
  twice),
* response wall time (received/start through completion),
* business/human waiting intervals, and
* child execution effort.

An absent completion is represented as an incomplete record.  The resolver is
safe to call again on the next invocation, at which point a matching
``task_complete`` for the same thread and turn can reconcile it.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple


TIME_SCHEMA = "serial-job-session-time/v5"

_MISSING = object()
_THREAD_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_EVENT_TYPES = {"task_started", "task_complete", "item_completed", "turn_started", "turn_complete"}


def _value(obj: Any, *names: str, default: Any = None) -> Any:
    if obj is None:
        return default
    for name in names:
        if isinstance(obj, Mapping):
            value = obj.get(name, _MISSING)
        else:
            value = getattr(obj, name, _MISSING)
        if value is not _MISSING and value is not None:
            return value
    return default


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)


def timestamp_ms(value: Any) -> Optional[int]:
    """Parse epoch seconds/ms or ISO-8601 into integer UTC milliseconds."""

    if value is None or value == "":
        return None
    if isinstance(value, Mapping):
        nested = _value(
            value,
            "timestamp_ms",
            "received_at_ms",
            "started_at_ms",
            "completed_at_ms",
            "timestamp",
            "received_at",
            "started_at",
            "completed_at",
            "at",
            default=None,
        )
        if nested is value:
            return None
        return timestamp_ms(nested)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if abs(number) < 100_000_000_000:
            number *= 1000.0
        return int(round(number))
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(round(dt.timestamp() * 1000.0))
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        number = None
    if number is not None:
        if abs(number) < 100_000_000_000:
            number *= 1000.0
        return int(round(number))
    try:
        iso = text[:-1] + "+00:00" if text.endswith("Z") else text
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(round(dt.timestamp() * 1000.0))


def timestamp_iso(value: Any) -> Optional[str]:
    millis = timestamp_ms(value)
    if millis is None:
        return None
    return datetime.fromtimestamp(millis / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def duration_ms(start: Any, end: Any) -> Optional[int]:
    a = timestamp_ms(start)
    b = timestamp_ms(end)
    if a is None or b is None or b < a:
        return None
    return b - a


def _is_external_import(value: Any) -> bool:
    """Identify imported envelope markers without treating normal content as external."""

    if not isinstance(value, Mapping):
        return False
    for key in ("source", "origin", "envelope", "record_type", "history_mode", "thread_source"):
        raw = value.get(key)
        text = str(raw).casefold() if raw is not None else ""
        if any(token in text for token in ("external_import", "external-import", "imported", "vendor_import")):
            return True
    for key in ("payload", "event_msg", "item", "data", "message"):
        nested = value.get(key)
        if isinstance(nested, Mapping) and _is_external_import(nested):
            return True
    return False


def _thread_matches(record: Mapping[str, Any], thread_id: Optional[str]) -> bool:
    if not thread_id:
        return True
    for key in ("thread_id", "threadId", "session_id", "sessionId"):
        value = record.get(key)
        if value is not None:
            return str(value) == str(thread_id)
    # Rollout JSONL often stores session id in session_meta only.  The caller
    # has already selected the filename in that case.
    return True


def _unwrap_event(record: Mapping[str, Any]) -> Optional[dict]:
    if _is_external_import(record):
        return None
    candidates: List[Mapping[str, Any]] = [record]
    for key in ("payload", "event_msg", "event", "data", "message"):
        nested = record.get(key)
        if isinstance(nested, Mapping):
            candidates.append(nested)
    event_type = None
    payload: Mapping[str, Any] = record
    for candidate in candidates:
        kind = _value(candidate, "type", "event_type", "kind", default=None)
        if kind in _EVENT_TYPES:
            event_type = kind
            payload = candidate
            break
    if event_type is None:
        return None
    result = dict(payload)
    result["event_type"] = event_type
    result.setdefault("record_timestamp", _value(record, "timestamp", "created_at", "created_at_ms", default=None))
    result.setdefault("ordinal", record.get("ordinal"))
    result.setdefault("thread_id", _value(payload, "thread_id", "threadId", default=_value(record, "thread_id", "threadId", default=None)))
    result.setdefault("turn_id", _value(payload, "turn_id", "turnId", default=_value(record, "turn_id", "turnId", default=None)))
    return result


def _item_payload(event: Mapping[str, Any]) -> Mapping[str, Any]:
    item = event.get("item")
    return item if isinstance(item, Mapping) else {}


def _record_from_event(event: Mapping[str, Any], *, source: str, path: Optional[str] = None) -> Optional[dict]:
    event_type = str(event.get("event_type") or event.get("type") or "")
    if event_type not in _EVENT_TYPES:
        return None
    item = _item_payload(event)
    turn_id = _value(event, "turn_id", "turnId", default=None)
    thread_id = _value(event, "thread_id", "threadId", default=None)
    started = _value(
        event,
        "started_at",
        "startedAt",
        "started_at_ms",
        "start_ms",
        default=_value(item, "started_at", "startedAt", "started_at_ms", "start_ms", default=None),
    )
    completed = _value(
        event,
        "completed_at",
        "completedAt",
        "completed_at_ms",
        "end_ms",
        default=_value(item, "completed_at", "completedAt", "completed_at_ms", "end_ms", default=None),
    )
    explicit_duration = _value(event, "duration_ms", "durationMs", default=_value(item, "duration_ms", "durationMs", default=None))
    if explicit_duration is None and started is not None and completed is not None:
        explicit_duration = duration_ms(started, completed)
    status = _value(event, "status", "outcome", default=None)
    if status is None:
        status = _value(item, "status", "outcome", default=None)
    item_type = _value(item, "type", "item_type", default=None)
    record = {
        "schema": TIME_SCHEMA,
        "kind": event_type,
        "event_type": event_type,
        "thread_id": thread_id,
        "turn_id": turn_id,
        "item_id": _value(item, "id", "item_id", default=None),
        "item_type": item_type,
        "started_at": timestamp_iso(started),
        "completed_at": timestamp_iso(completed),
        "started_at_ms": timestamp_ms(started),
        "completed_at_ms": timestamp_ms(completed),
        "duration_ms": int(explicit_duration) if explicit_duration is not None else None,
        "status": status,
        "source": source,
        "source_path": path,
        "source_timestamp": timestamp_iso(event.get("record_timestamp")),
        "source_confidence": "verified" if source in {"rollout_jsonl", "thread_history_sqlite", "host"} else "unknown",
        "complete": bool(completed is not None or explicit_duration is not None),
        "count_for_calibration": bool(completed is not None or explicit_duration is not None),
    }
    if event_type in {"task_complete", "turn_complete"} and status in {"interrupted", "cancelled", "canceled", "stopped"}:
        record["outcome"] = "interrupted"
        # An explicitly completed interruption is a valid sample.
        record["complete"] = bool(completed is not None or explicit_duration is not None)
        record["count_for_calibration"] = record["complete"]
    if event_type == "item_completed" and item_type == "SubAgentActivity":
        record["child_thread_id"] = _value(item, "agent_thread_id", "child_thread_id", default=None)
    if event_type == "item_completed" and str(item_type or "").casefold().replace("-", "_") in {"usermessage", "user_message"}:
        record["human_event"] = "question"
        received = _value(
            item,
            "created_at_ms",
            "createdAtMs",
            "received_at_ms",
            "receivedAtMs",
            default=event.get("record_timestamp"),
        )
        record["received_at"] = timestamp_iso(received)
        record["received_at_ms"] = timestamp_ms(received)
    return record


def discover_session_files(
    *,
    codex_home: Optional[os.PathLike[str] | str] = None,
    thread_id: Optional[str] = None,
    rollout_path: Optional[os.PathLike[str] | str] = None,
) -> List[Path]:
    """Discover only the requested rollout, never scan unrelated threads."""

    if rollout_path:
        path = Path(rollout_path).expanduser()
        return [path] if path.is_file() else []
    if not thread_id:
        return []
    home = Path(codex_home or os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()
    root = home / "sessions"
    if not root.is_dir():
        return []
    needle = str(thread_id).casefold()
    return sorted(
        path
        for path in root.rglob("rollout-*.jsonl")
        if needle in path.name.casefold()
    )


def read_rollout_events(
    path: os.PathLike[str] | str,
    *,
    thread_id: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> List[dict]:
    """Read timing events from one JSONL rollout with no write side effects."""

    path = Path(path)
    records: List[dict] = []
    if not path.is_file():
        return records
    with path.open("r", encoding="utf-8", errors="replace") as stream:
        for line_number, line in enumerate(stream, 1):
            try:
                raw = json.loads(line)
            except (TypeError, ValueError):
                continue
            if not isinstance(raw, Mapping):
                continue
            event = _unwrap_event(raw)
            if event is None or not _thread_matches(event, thread_id):
                continue
            event_turn = _value(event, "turn_id", "turnId", default=None)
            if turn_id and event_turn is not None and str(event_turn) != str(turn_id):
                continue
            record = _record_from_event(event, source="rollout_jsonl", path=str(path))
            if record is None:
                continue
            record["line"] = line_number
            records.append(record)
    return records


def _sqlite_candidates(root: Path, required_tables: Sequence[str]) -> List[Path]:
    if root.is_file():
        candidates = [root]
    elif root.is_dir():
        candidates = sorted(root.glob("*.sqlite")) + sorted(root.glob("*.db"))
    else:
        candidates = []
    found: List[Path] = []
    for path in candidates:
        try:
            con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
            names = {row[0] for row in con.execute("select name from sqlite_master where type='table'")}
            con.close()
        except (OSError, sqlite3.Error):
            continue
        if set(required_tables) <= names:
            found.append(path)
    return found


def discover_history_db(codex_home: Optional[os.PathLike[str] | str] = None) -> Optional[Path]:
    home = Path(codex_home or os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()
    candidates = _sqlite_candidates(home, ("thread_turns", "thread_items"))
    return candidates[0] if candidates else None


def discover_state_db(codex_home: Optional[os.PathLike[str] | str] = None) -> Optional[Path]:
    home = Path(codex_home or os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()
    candidates = _sqlite_candidates(home, ("threads",))
    return candidates[0] if candidates else None


def _read_only_connect(path: os.PathLike[str] | str) -> sqlite3.Connection:
    return sqlite3.connect(Path(path).expanduser().resolve().as_uri() + "?mode=ro", uri=True)


def _columns(con: sqlite3.Connection, table: str) -> List[str]:
    return [row[1] for row in con.execute("pragma table_info(%s)" % table)]


def read_thread_history(
    db_path: os.PathLike[str] | str,
    *,
    thread_id: str,
    turn_id: Optional[str] = None,
) -> List[dict]:
    """Read matching ``thread_turns`` and user item timestamps read-only."""

    records: List[dict] = []
    con = _read_only_connect(db_path)
    try:
        turn_columns = _columns(con, "thread_turns")
        fields = [name for name in ("thread_id", "turn_id", "status", "started_at", "completed_at", "duration_ms") if name in turn_columns]
        where = "thread_id = ?"
        params: List[Any] = [str(thread_id)]
        if turn_id and "turn_id" in turn_columns:
            where += " and turn_id = ?"
            params.append(str(turn_id))
        rows = con.execute("select %s from thread_turns where %s" % (", ".join(fields), where), params).fetchall()
        for row in rows:
            item = dict(zip(fields, row))
            started = item.get("started_at")
            completed = item.get("completed_at")
            status = item.get("status")
            complete = completed is not None or item.get("duration_ms") is not None
            records.append(
                {
                    "schema": TIME_SCHEMA,
                    "kind": "thread_turn",
                    "event_type": "task_complete" if complete else "task_started",
                    "thread_id": item.get("thread_id"),
                    "turn_id": item.get("turn_id"),
                    "started_at": timestamp_iso(started),
                    "completed_at": timestamp_iso(completed),
                    "started_at_ms": timestamp_ms(started),
                    "completed_at_ms": timestamp_ms(completed),
                    "duration_ms": int(item["duration_ms"]) if item.get("duration_ms") is not None else duration_ms(started, completed),
                    "status": status,
                    "source": "thread_history_sqlite",
                    "source_path": str(db_path),
                    "source_confidence": "verified",
                    "complete": complete,
                    "count_for_calibration": complete,
                }
            )
        item_columns = _columns(con, "thread_items")
        if {"thread_id", "turn_id", "created_at_ms", "item_json"} <= set(item_columns):
            where = "thread_id = ?"
            params = [str(thread_id)]
            if turn_id:
                where += " and turn_id = ?"
                params.append(str(turn_id))
            rows = con.execute(
                "select turn_id, created_at_ms, item_json, item_type from thread_items where %s order by created_at_ms" % where,
                params,
            ).fetchall()
            for row in rows:
                row_turn, created, item_json, item_type = row
                try:
                    item = json.loads(item_json) if isinstance(item_json, str) else item_json
                except (TypeError, ValueError):
                    item = {}
                item_map = item if isinstance(item, Mapping) else {}
                # Imported external envelopes in SQLite must not become real history.
                if _is_external_import(item_map):
                    continue
                kind = str(_value(item_map, "type", "item_type", default=item_type or ""))
                if kind.casefold().replace("-", "_") not in {"usermessage", "user_message"}:
                    continue
                records.append(
                    {
                        "schema": TIME_SCHEMA,
                        "kind": "human_event",
                        "event_type": "user_message",
                        "thread_id": str(thread_id),
                        "turn_id": row_turn,
                        "item_type": kind,
                        "received_at": timestamp_iso(created),
                        "received_at_ms": timestamp_ms(created),
                        "source": "thread_items_sqlite",
                        "source_path": str(db_path),
                        "source_confidence": "verified",
                        "complete": True,
                        "count_for_calibration": False,
                        "human_event": "question",
                    }
                )
    finally:
        con.close()
    return records


def read_state_locator(
    db_path: os.PathLike[str] | str,
    *,
    thread_id: str,
) -> Optional[dict]:
    """Read optional rollout/model/reasoning/parent metadata for one thread."""

    con = _read_only_connect(db_path)
    try:
        columns = _columns(con, "threads")
        identity_column = "id" if "id" in columns else ("thread_id" if "thread_id" in columns else None)
        wanted = [
            name
            for name in (
                identity_column,
                "rollout_path",
                "model",
                "reasoning_effort",
                "source",
                "thread_source",
                "agent_path",
                "parent_thread_id",
                "parent_id",
                "source_thread_id",
            )
            if name and name in columns
        ]
        if not wanted:
            return None
        if identity_column is None:
            return None
        row = con.execute(
            "select %s from threads where %s = ?" % (", ".join(wanted), identity_column),
            (str(thread_id),),
        ).fetchone()
        if row is None:
            return None
        result = dict(zip(wanted, row))
        # Some hosts keep parent/child provenance in a separate edge table.
        # Read it opportunistically when the schema exposes recognizable
        # columns; never assume a filename or mutate the database.
        table_names = {
            str(item[0])
            for item in con.execute("select name from sqlite_master where type='table'").fetchall()
        }
        for table in ("thread_spawn_edges", "thread_edges", "spawn_edges"):
            if table not in table_names:
                continue
            edge_columns = _columns(con, table)
            child_column = next(
                (name for name in ("child_thread_id", "child_id", "to_thread_id", "thread_id") if name in edge_columns),
                None,
            )
            parent_column = next(
                (name for name in ("parent_thread_id", "parent_id", "from_thread_id", "source_thread_id") if name in edge_columns),
                None,
            )
            if not child_column or not parent_column:
                continue
            edge = con.execute(
                "select %s from %s where %s = ? order by rowid desc limit 1"
                % (parent_column, table, child_column),
                (str(thread_id),),
            ).fetchone()
            if edge and edge[0] is not None:
                result["parent_thread_id"] = edge[0]
                break
        result["source"] = "state_sqlite"
        result["source_path"] = str(db_path)
        result["source_confidence"] = "verified"
        result["complete"] = True
        return result
    finally:
        con.close()


def choose_start_bound(
    *,
    host_received_at: Any = None,
    host_started_at: Any = None,
    current_thread_user_event: Any = None,
    task_started_at: Any = None,
    first_tool_at: Any = None,
) -> Optional[dict]:
    """Choose a start bound in the required precedence order.

    This function requires the caller to provide an explicit thread/turn
    context elsewhere; it never guesses the worker's own thread.
    """

    candidates = (
        ("host_received_or_started", host_received_at if host_received_at is not None else host_started_at, "host", "verified", True),
        ("current_thread_user_event", current_thread_user_event, "current_thread_user_event", "verified", True),
        ("task_started", task_started_at, "task_started", "verified", True),
        ("first_tool", first_tool_at, "first_tool", "lower_bound", False),
    )
    for label, value, source, confidence, calibration in candidates:
        parsed = timestamp_ms(value)
        if parsed is None:
            continue
        return {
            "started_at": timestamp_iso(parsed),
            "started_at_ms": parsed,
            "source": source,
            "source_label": label,
            "confidence": confidence,
            "complete": False,
            "count_for_calibration": calibration,
        }
    return None


def _first_tool_time(records: Sequence[Mapping[str, Any]]) -> Optional[int]:
    times: List[int] = []
    for record in records:
        item_type = str(record.get("item_type") or "")
        kind = _record_kind(record)
        if item_type.casefold() in {"commandexecution", "functioncall", "toolcall", "tool_call"} or kind in {"tool_started", "command_started"}:
            value = timestamp_ms(record.get("started_at_ms") or record.get("started_at"))
            if value is not None:
                times.append(value)
    return min(times) if times else None


def _merge_records(records: Iterable[Mapping[str, Any]]) -> List[dict]:
    result: List[dict] = []
    seen: set[str] = set()
    for item in records:
        safe = dict(_json_safe(item))
        marker = json.dumps(safe, sort_keys=True, ensure_ascii=False)
        if marker not in seen:
            seen.add(marker)
            result.append(safe)
    return result


def collect_time_records(
    *,
    thread_id: Optional[str],
    turn_id: Optional[str] = None,
    codex_home: Optional[os.PathLike[str] | str] = None,
    rollout_path: Optional[os.PathLike[str] | str] = None,
    history_db: Optional[os.PathLike[str] | str] = None,
    state_db: Optional[os.PathLike[str] | str] = None,
    host_records: Optional[Iterable[Mapping[str, Any]]] = None,
) -> dict:
    """Collect records for exactly one explicit thread (and optional turn)."""

    if not thread_id:
        raise ValueError("thread_id is required; timing must not bind to the worker thread implicitly")
    records: List[dict] = []
    paths = discover_session_files(codex_home=codex_home, thread_id=thread_id, rollout_path=rollout_path)
    for path in paths:
        records.extend(read_rollout_events(path, thread_id=thread_id, turn_id=turn_id))
    history = Path(history_db).expanduser() if history_db else discover_history_db(codex_home)
    if history and history.is_file():
        try:
            records.extend(read_thread_history(history, thread_id=thread_id, turn_id=turn_id))
        except sqlite3.Error:
            pass
    supplied = []
    for record in host_records or []:
        if not isinstance(record, Mapping):
            continue
        if str(_value(record, "thread_id", "threadId", default=thread_id)) != str(thread_id):
            continue
        record_turn = _value(record, "turn_id", "turnId", default=None)
        if turn_id and record_turn is not None and str(record_turn) != str(turn_id):
            continue
        if _is_external_import(record):
            continue
        clone = dict(_json_safe(record))
        clone.setdefault("source", "host")
        clone.setdefault("source_confidence", "verified")
        clone.setdefault("complete", bool(clone.get("completed_at") or clone.get("duration_ms") is not None))
        clone.setdefault("count_for_calibration", bool(clone["complete"]))
        supplied.append(clone)
    records.extend(supplied)
    locator = None
    state = Path(state_db).expanduser() if state_db else discover_state_db(codex_home)
    if state and state.is_file():
        try:
            locator = read_state_locator(state, thread_id=thread_id)
        except sqlite3.Error:
            locator = None
    return {
        "schema": TIME_SCHEMA,
        "thread_id": str(thread_id),
        "turn_id": str(turn_id) if turn_id is not None else None,
        "records": _merge_records(records),
        "rollout_paths": [str(path) for path in paths],
        "history_db": str(history) if history else None,
        "state_locator": locator,
        "read_only": True,
    }


def _record_key(record: Mapping[str, Any]) -> Tuple[str, str]:
    return (str(record.get("thread_id") or ""), str(record.get("turn_id") or ""))


def _record_kind(record: Mapping[str, Any]) -> str:
    """Read the event kind from either adapter or host supplied records."""

    return str(record.get("event_type") or record.get("kind") or "").casefold()


def _interval_from_record(record: Mapping[str, Any]) -> Optional[Tuple[int, int]]:
    start = timestamp_ms(record.get("started_at_ms") or record.get("started_at"))
    end = timestamp_ms(record.get("completed_at_ms") or record.get("completed_at"))
    if start is None or end is None or end < start:
        return None
    return start, end


def union_intervals(intervals: Iterable[Tuple[int, int]]) -> List[Tuple[int, int]]:
    ordered = sorted((int(a), int(b)) for a, b in intervals if b >= a)
    merged: List[Tuple[int, int]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def _sum_intervals(intervals: Iterable[Tuple[int, int]]) -> int:
    return sum(max(0, end - start) for start, end in union_intervals(intervals))


def subtract_intervals(
    intervals: Iterable[Tuple[int, int]],
    exclusions: Iterable[Tuple[int, int]],
) -> List[Tuple[int, int]]:
    """Subtract evidenced waiting intervals from active work intervals."""

    remaining = union_intervals(intervals)
    for cut_start, cut_end in union_intervals(exclusions):
        next_remaining: List[Tuple[int, int]] = []
        for start, end in remaining:
            if cut_end <= start or cut_start >= end:
                next_remaining.append((start, end))
                continue
            if start < cut_start:
                next_remaining.append((start, max(start, cut_start)))
            if cut_end < end:
                next_remaining.append((min(end, cut_end), end))
        remaining = next_remaining
    return union_intervals(remaining)


def intersect_intervals(
    intervals: Iterable[Tuple[int, int]],
    bounds: Iterable[Tuple[int, int]],
) -> List[Tuple[int, int]]:
    """Clip evidence intervals to the turn's observed work/response bounds."""

    result: List[Tuple[int, int]] = []
    for start, end in union_intervals(intervals):
        for bound_start, bound_end in union_intervals(bounds):
            left = max(start, bound_start)
            right = min(end, bound_end)
            if right >= left:
                result.append((left, right))
    return union_intervals(result)


def resolve_timing(
    records: Iterable[Mapping[str, Any]],
    *,
    thread_id: str,
    turn_id: str,
    host_received_at: Any = None,
    host_started_at: Any = None,
    current_thread_user_event: Any = None,
    require_explicit_context: bool = True,
) -> dict:
    """Reconcile one same-thread/turn timing sample.

    A task completion is accepted only for the exact supplied thread and
    turn.  If no end exists, ``complete`` remains false and duration remains
    unknown; the next call can reconcile it.
    """

    if require_explicit_context and (not thread_id or not turn_id):
        raise ValueError("thread_id and turn_id are required for timing resolution")
    selected = [
        dict(_json_safe(record))
        for record in records
        if str(record.get("thread_id") or "") == str(thread_id)
        and str(record.get("turn_id") or "") == str(turn_id)
        and not _is_external_import(record)
    ]
    starts = [timestamp_ms(r.get("started_at_ms") or r.get("started_at")) for r in selected]
    starts = [value for value in starts if value is not None]
    completes = [timestamp_ms(r.get("completed_at_ms") or r.get("completed_at")) for r in selected]
    completes = [value for value in completes if value is not None]
    completion_record = next(
        (
            r
            for r in reversed(selected)
            if _record_kind(r) in {"task_complete", "turn_complete"}
            and (r.get("completed_at") or r.get("completed_at_ms") or r.get("duration_ms") is not None)
        ),
        None,
    )
    task_started = next(
        (
            r.get("started_at_ms") or r.get("started_at")
            for r in selected
            if _record_kind(r) in {"task_started", "turn_started"}
        ),
        None,
    )
    if current_thread_user_event is None:
        user_events = [
            r.get("received_at_ms") or r.get("received_at")
            for r in selected
            if str(r.get("kind") or r.get("event_type")) in {"human_event", "user_message"}
        ]
        current_thread_user_event = next((value for value in user_events if timestamp_ms(value) is not None), None)
    first_tool = _first_tool_time(selected)
    start_bound = choose_start_bound(
        host_received_at=host_received_at,
        host_started_at=host_started_at,
        current_thread_user_event=current_thread_user_event,
        task_started_at=task_started,
        first_tool_at=first_tool,
    )
    if start_bound is None and starts:
        # A history row's start is a verified task bound when no higher source
        # is available.  Keep source explicit so calibration can audit it.
        value = min(starts)
        start_bound = {
            "started_at": timestamp_iso(value),
            "started_at_ms": value,
            "source": "recorded_start",
            "source_label": "recorded_start",
            "confidence": "verified",
            "complete": False,
            "count_for_calibration": True,
        }
    # Only an explicit task/turn completion closes the sample.  Completed
    # human-wait or item intervals must never masquerade as the turn end.
    end = timestamp_ms(completion_record.get("completed_at_ms") or completion_record.get("completed_at")) if completion_record else None
    explicit_duration = int(completion_record.get("duration_ms")) if completion_record and completion_record.get("duration_ms") is not None else None
    # Only a task/turn completion's explicit duration can close the sample.
    # A nested item duration is evidence of a child/tool interval and must not
    # become the parent turn total merely because it appears later in JSONL.
    if explicit_duration is not None:
        explicit_duration = max(0, explicit_duration)
    work_intervals = [
        interval
        for record in selected
        if (interval := _interval_from_record(record)) is not None
        and _record_kind(record) not in {"human_wait", "business_wait", "waiting"}
    ]
    # A turn's explicit task duration is authoritative for the total when it
    # exists; nested item intervals remain useful evidence but are not added.
    total_ms = explicit_duration
    if total_ms is None and start_bound and end is not None:
        total_ms = max(0, end - int(start_bound["started_at_ms"]))
    complete = completion_record is not None and (end is not None or explicit_duration is not None)
    status = completion_record.get("status") if completion_record else None
    outcome = completion_record.get("outcome") if completion_record else None
    if status in {"interrupted", "cancelled", "canceled", "stopped"} and complete:
        outcome = "interrupted"
    response_start = None
    for candidate in (host_received_at, host_started_at, current_thread_user_event):
        response_start = timestamp_ms(candidate)
        if response_start is not None:
            break
    if response_start is None and start_bound:
        response_start = int(start_bound["started_at_ms"])
    wall_response_ms = max(0, end - response_start) if response_start is not None and end is not None else None
    raw_waiting_intervals = [
        interval
        for record in selected
        if (interval := _interval_from_record(record)) is not None
        and _record_kind(record) in {"human_wait", "business_wait", "waiting"}
    ]
    observed_bounds = list(work_intervals)
    if not observed_bounds and start_bound and end is not None:
        observed_bounds = [(int(start_bound["started_at_ms"]), end)]
    waiting_intervals = intersect_intervals(raw_waiting_intervals, observed_bounds) if observed_bounds else []
    business_wait_ms = _sum_intervals(waiting_intervals) if waiting_intervals else 0
    lower_bound = bool(start_bound and start_bound.get("confidence") == "lower_bound")
    # When a host reports only a task duration plus a verified start/end, use
    # that observed turn interval as the subtraction base.  This preserves the
    # wait distinction even when no nested tool interval was emitted.
    activity_base = work_intervals or observed_bounds
    active_intervals = subtract_intervals(activity_base, waiting_intervals)
    active_ms = _sum_intervals(active_intervals)
    reliable_start = bool(start_bound and start_bound.get("confidence") in {"verified", "high", "medium"})
    return {
        "schema": TIME_SCHEMA,
        "thread_id": str(thread_id),
        "turn_id": str(turn_id),
        "started_at": start_bound.get("started_at") if start_bound else None,
        "started_at_ms": start_bound.get("started_at_ms") if start_bound else None,
        "start_source": start_bound.get("source") if start_bound else None,
        "start_confidence": start_bound.get("confidence") if start_bound else "unknown",
        "completed_at": timestamp_iso(end),
        "completed_at_ms": end,
        "duration_ms": total_ms,
        "active_work_ms": active_ms if activity_base else (total_ms if complete else None),
        "wall_response_ms": wall_response_ms,
        "business_wait_ms": business_wait_ms,
        "queue_uncertainty_ms": None,
        "queue_uncertainty": "unknown" if start_bound and start_bound.get("source") in {"host", "current_thread_user_event"} else None,
        "child_effort_ms": 0,
        "status": status,
        "outcome": outcome,
        "complete": complete,
        "in_progress": not complete,
        "count_for_calibration": bool(complete and reliable_start and not lower_bound),
        "underbound": lower_bound,
        "evidence_records": selected,
        "reconciled": bool(completion_record),
    }


def reconcile_completed_turn(
    records: Iterable[Mapping[str, Any]],
    *,
    thread_id: str,
    turn_id: str,
    **kwargs: Any,
) -> dict:
    """Public next-invocation resolver for the same thread and turn."""

    return resolve_timing(records, thread_id=thread_id, turn_id=turn_id, **kwargs)


def aggregate_timing(
    samples: Iterable[Mapping[str, Any]],
    *,
    parent_thread_id: Optional[str] = None,
    child_samples: Optional[Iterable[Mapping[str, Any]]] = None,
) -> dict:
    """Aggregate own work, wall response, waiting, and child effort separately."""

    own = [dict(_json_safe(sample)) for sample in samples]
    active_intervals: List[Tuple[int, int]] = []
    fallback_active_ms = 0
    wall_values: List[int] = []
    wait_intervals: List[Tuple[int, int]] = []
    fallback_wait_ms = 0
    for sample in own:
        sample_active: List[Tuple[int, int]] = []
        sample_wait: List[Tuple[int, int]] = []
        for record in sample.get("evidence_records", []):
            if _record_kind(record) in {"human_wait", "business_wait", "waiting"}:
                interval = _interval_from_record(record)
                if interval:
                    sample_wait.append(interval)
            else:
                interval = _interval_from_record(record)
                if interval:
                    sample_active.append(interval)
        sample_active = union_intervals(sample_active)
        sample_wait = union_intervals(sample_wait)
        if sample_active:
            # Wait evidence is meaningful only inside the observed task/tool
            # interval.  Subtract it before aggregating, even when the sample
            # already carries a precomputed active_work_ms value.
            clipped_wait = intersect_intervals(sample_wait, sample_active)
            active_intervals.extend(subtract_intervals(sample_active, clipped_wait))
            wait_intervals.extend(clipped_wait)
        else:
            if sample.get("active_work_ms") is not None and sample.get("complete"):
                fallback_active_ms += int(sample["active_work_ms"])
            fallback_wait_ms += _sum_intervals(sample_wait)
        if sample.get("wall_response_ms") is not None:
            wall_values.append(int(sample["wall_response_ms"]))
        elif sample.get("complete") and sample.get("duration_ms") is not None:
            wall_values.append(int(sample["duration_ms"]))
    own_active = _sum_intervals(active_intervals) + fallback_active_ms
    child_active = 0
    child_records: List[dict] = []
    for child in child_samples or []:
        item = dict(_json_safe(child))
        # A parent user message is response latency evidence, never child effort.
        if str(item.get("kind") or item.get("event_type")) in {"human_event", "user_message"}:
            continue
        if parent_thread_id and str(item.get("thread_id") or "") == str(parent_thread_id):
            # Parent activity remains in own totals; do not duplicate it here.
            continue
        if item.get("active_work_ms") is not None and item.get("complete"):
            child_active += int(item["active_work_ms"])
        elif item.get("duration_ms") is not None and item.get("complete"):
            child_active += int(item["duration_ms"])
        child_records.append(item)
    incomplete = [sample for sample in own if not sample.get("complete")]
    return {
        "schema": TIME_SCHEMA,
        "active_work_ms": own_active,
        "wall_response_ms": sum(wall_values) if wall_values else None,
        "business_wait_ms": _sum_intervals(wait_intervals) + fallback_wait_ms if (wait_intervals or fallback_wait_ms) else sum(int(sample.get("business_wait_ms") or 0) for sample in own),
        "child_effort_ms": child_active,
        "incomplete_samples": len(incomplete),
        "complete": not incomplete,
        "count_for_calibration": not incomplete and all(sample.get("count_for_calibration", False) for sample in own),
        "underbound_samples": sum(1 for sample in own if sample.get("underbound")),
        "queue_uncertainty": "unknown" if any(sample.get("queue_uncertainty") for sample in own) else None,
        "samples": own,
        "child_samples": child_records,
    }
