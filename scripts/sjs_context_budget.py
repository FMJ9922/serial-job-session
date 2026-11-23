"""Estimate the remaining context budget of one Codex thread.

The host does not expose an authoritative live context counter to this skill.
This module therefore reads the selected thread's persisted history in read-only
mode, estimates tokens from the stored item JSON, and combines that observation
with the model cache's context-window metadata.  The result is an estimate and
includes the source and confidence fields so callers can make an explicit
dispatch decision.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping

from sjs_config import require
from sjs_context_budget_core import (
    DEFAULT_ACCEPTABLE_MAX_PERCENT,
    DEFAULT_ACCEPTABLE_MIN_PERCENT,
    DEFAULT_EFFECTIVE_PERCENT,
    DEFAULT_RESERVE_TOKENS,
    DEFAULT_TARGET_UTILIZATION_PERCENT,
    DEFAULT_TASK_OUTPUT_TOKENS,
    DEFAULT_TASK_TOOL_TOKENS,
    classify_task,
    dispatch_recommendation,
    estimate_task_allocation,
    evaluate_recent_utilization,
)


DEFAULT_CONTEXT_WINDOW = 272_000


def _read_only_connect(path: os.PathLike[str] | str) -> sqlite3.Connection:
    resolved = Path(path).expanduser().resolve()
    return sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True)


def _codex_home(codex_home: os.PathLike[str] | str | None = None) -> Path:
    return Path(codex_home or os.environ.get("CODEX_HOME") or (Path.home() / ".codex")).expanduser()


def _state_db(codex_home: Path, state_db: os.PathLike[str] | str | None) -> Path:
    return Path(state_db).expanduser() if state_db else codex_home / "state_5.sqlite"


def _history_db(codex_home: Path, history_db: os.PathLike[str] | str | None) -> Path:
    return Path(history_db).expanduser() if history_db else codex_home / "thread_history_1.sqlite"


def _number(value: Any, default: int | None = None) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number >= 0 else default


def _is_cjk(character: str) -> bool:
    code = ord(character)
    return (
        0x3040 <= code <= 0x30FF
        or 0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xAC00 <= code <= 0xD7AF
    )


def estimate_text_tokens(text: str) -> int:
    """Estimate tokens without a third-party tokenizer.

    The heuristic treats ASCII text as roughly four characters per token and
    CJK characters as roughly one token.  It is intentionally deterministic,
    cheap, and conservative enough for dispatch planning.
    """

    ascii_count = 0
    cjk_count = 0
    other_count = 0
    for character in text:
        if ord(character) < 128:
            ascii_count += 1
        elif _is_cjk(character):
            cjk_count += 1
        else:
            other_count += 1
    return max(1, math.ceil(ascii_count / 4 + cjk_count + other_count / 2)) if text else 0


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _load_thread(state_db: Path, thread_id: str) -> dict[str, Any]:
    require(state_db.is_file(), "CONTEXT_STATE_DB_NOT_FOUND", path=str(state_db))
    connection = _read_only_connect(state_db)
    try:
        columns = {row[1] for row in connection.execute("pragma table_info(threads)")}
        require("id" in columns, "CONTEXT_STATE_DB_UNSUPPORTED", path=str(state_db))
        wanted = [
            name for name in (
                "id", "model", "reasoning_effort", "tokens_used", "rollout_path",
                "title", "updated_at_ms", "history_mode",
            ) if name in columns
        ]
        row = connection.execute(
            "select %s from threads where id = ?" % ",".join(wanted), (str(thread_id),)
        ).fetchone()
    finally:
        connection.close()
    require(row is not None, "CONTEXT_THREAD_NOT_FOUND", thread_id=str(thread_id))
    return dict(zip(wanted, row))


def _load_items(history_db: Path, thread_id: str) -> list[dict[str, Any]]:
    require(history_db.is_file(), "CONTEXT_HISTORY_DB_NOT_FOUND", path=str(history_db))
    connection = _read_only_connect(history_db)
    try:
        columns = {row[1] for row in connection.execute("pragma table_info(thread_items)")}
        required = {"thread_id", "item_json"}
        require(required <= columns, "CONTEXT_HISTORY_DB_UNSUPPORTED", path=str(history_db))
        selected = [name for name in ("turn_id", "item_id", "item_type", "created_at_ms", "item_json") if name in columns]
        rows = connection.execute(
            "select %s from thread_items where thread_id = ? order by created_at_ms, rowid" % ",".join(selected),
            (str(thread_id),),
        ).fetchall()
    finally:
        connection.close()
    items = []
    for row in rows:
        item = dict(zip(selected, row))
        raw = item.get("item_json")
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            payload = raw
        item["payload"] = payload
        item["estimated_tokens"] = estimate_text_tokens(_json_text(payload))
        items.append(item)
    return items


def _model_metadata(codex_home: Path, model: str | None) -> dict[str, Any]:
    path = codex_home / "models_cache.json"
    if not path.is_file():
        return {"source": "default", "context_window": DEFAULT_CONTEXT_WINDOW,
                "effective_percent": DEFAULT_EFFECTIVE_PERCENT, "instruction_template_tokens": 0}
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"source": "default", "context_window": DEFAULT_CONTEXT_WINDOW,
                "effective_percent": DEFAULT_EFFECTIVE_PERCENT, "instruction_template_tokens": 0}
    models = document.get("models", []) if isinstance(document, Mapping) else []
    match = next((entry for entry in models if entry.get("slug") == model), None)
    if not isinstance(match, Mapping):
        return {"source": "default", "context_window": DEFAULT_CONTEXT_WINDOW,
                "effective_percent": DEFAULT_EFFECTIVE_PERCENT, "model": model,
                "instruction_template_tokens": 0}
    window = _number(match.get("context_window"), DEFAULT_CONTEXT_WINDOW) or DEFAULT_CONTEXT_WINDOW
    percent = _number(match.get("effective_context_window_percent"), DEFAULT_EFFECTIVE_PERCENT) or DEFAULT_EFFECTIVE_PERCENT
    return {
        "source": "models_cache",
        "context_window": window,
        "max_context_window": _number(match.get("max_context_window")),
        "effective_percent": percent,
        "model": match.get("slug"),
        "instruction_template_tokens": estimate_text_tokens(
            str(match.get("model_messages", {}).get("instructions_template", ""))
        ),
    }


def task_samples_from_state(state: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Read comparable utilization samples recorded in accepted task cards."""

    samples = []
    for task in state.get("tasks", {}).values():
        if not isinstance(task, Mapping) or task.get("status") != "accepted":
            continue
        budget = task.get("parameters", {}).get("context_budget", {})
        if not isinstance(budget, Mapping):
            continue
        value = budget.get("actual_utilization_percent", budget.get("utilization_percent"))
        if not isinstance(value, (int, float)) or value < 0:
            continue
        samples.append({
            "task_id": task.get("id"),
            "status": "accepted",
            "group": budget.get("group", task.get("domain", "default")),
            "utilization_percent": float(value),
            "intentional_small": budget.get("intentional_small") is True,
            "accepted_at": task.get("accepted_at", ""),
        })
    return sorted(samples, key=lambda sample: str(sample.get("accepted_at", "")))


def _task_text(payload: Mapping[str, Any] | None) -> str:
    if not payload:
        return ""
    value = payload.get("task_text", payload.get("taskText", ""))
    return value if isinstance(value, str) else _json_text(value)


def estimate_context_budget(
    *,
    thread_id: str,
    state_db: os.PathLike[str] | str | None = None,
    history_db: os.PathLike[str] | str | None = None,
    codex_home: os.PathLike[str] | str | None = None,
    model: str | None = None,
    context_limit: int | None = None,
    effective_percent: int | None = None,
    overhead_tokens: int = 0,
    reserve_tokens: int = DEFAULT_RESERVE_TOKENS,
    task_text: str = "",
    task_output_tokens: int = DEFAULT_TASK_OUTPUT_TOKENS,
    task_tool_tokens: int = DEFAULT_TASK_TOOL_TOKENS,
    target_utilization_percent: int = DEFAULT_TARGET_UTILIZATION_PERCENT,
    acceptable_min_percent: int = DEFAULT_ACCEPTABLE_MIN_PERCENT,
    acceptable_max_percent: int = DEFAULT_ACCEPTABLE_MAX_PERCENT,
    recent_tasks: Iterable[Mapping[str, Any]] | None = None,
    task_group: str | None = None,
) -> dict[str, Any]:
    require(isinstance(thread_id, str) and thread_id.strip(), "CONTEXT_THREAD_ID_REQUIRED")
    home = _codex_home(codex_home)
    state_path = _state_db(home, state_db)
    history_path = _history_db(home, history_db)
    thread = _load_thread(state_path, thread_id)
    resolved_model = model or thread.get("model")
    metadata = _model_metadata(home, resolved_model)
    limit = _number(context_limit, metadata["context_window"]) or DEFAULT_CONTEXT_WINDOW
    percent = _number(effective_percent, metadata["effective_percent"]) or DEFAULT_EFFECTIVE_PERCENT
    require(0 < percent <= 100, "CONTEXT_EFFECTIVE_PERCENT_INVALID")
    explicit_overhead = _number(overhead_tokens, 0) or 0
    automatic_overhead = metadata.get("instruction_template_tokens", 0) or 0
    overhead = explicit_overhead + automatic_overhead
    reserve = _number(reserve_tokens, DEFAULT_RESERVE_TOKENS)
    output = _number(task_output_tokens, DEFAULT_TASK_OUTPUT_TOKENS)
    tool_tokens = _number(task_tool_tokens, DEFAULT_TASK_TOOL_TOKENS)
    require(reserve is not None and output is not None and tool_tokens is not None,
            "CONTEXT_TOKEN_VALUE_INVALID")
    items = _load_items(history_path, thread_id)
    observed = sum(item["estimated_tokens"] for item in items)
    task_tokens = estimate_text_tokens(task_text)
    allocation = estimate_task_allocation(
        observed_history_tokens=observed,
        context_limit_tokens=limit,
        effective_percent=percent,
        overhead_tokens=overhead,
        reserve_tokens=reserve,
        task_prompt_tokens=task_tokens,
        task_output_tokens=output,
        task_tool_tokens=tool_tokens,
        target_utilization_percent=target_utilization_percent,
        acceptable_min_percent=acceptable_min_percent,
        acceptable_max_percent=acceptable_max_percent,
    )
    fit = classify_task(allocation["projected_tokens"], allocation["available_tokens"])
    recent = evaluate_recent_utilization(
        recent_tasks or [], group=task_group, low_percent=acceptable_min_percent
    )
    return {
        "ok": True,
        "schema": "sjs-context-budget/v1",
        "thread_id": str(thread_id),
        "model": resolved_model,
        "reasoning_effort": thread.get("reasoning_effort"),
        "source": {
            "state_db": str(state_path),
            "history_db": str(history_path),
            "model_metadata": metadata["source"],
        },
        "confidence": "estimated",
        "context": {
            "configured_limit_tokens": allocation["configured_limit_tokens"],
            "effective_percent": allocation["effective_percent"],
            "usable_limit_tokens": allocation["usable_limit_tokens"],
            "observed_history_tokens": allocation["observed_history_tokens"],
            "overhead_tokens": overhead,
            "automatic_overhead_tokens": automatic_overhead,
            "explicit_overhead_tokens": explicit_overhead,
            "reserve_tokens": allocation["reserve_tokens"],
            "remaining_before_task_tokens": allocation["remaining_before_task_tokens"],
            "remaining_before_task_percent": allocation["remaining_before_task_percent"],
        },
        "history": {
            "item_count": len(items),
            "item_types": _item_type_counts(items),
        },
        "task": {
            "prompt_tokens": task_tokens,
            "expected_output_tokens": output,
            "projected_tokens": allocation["projected_tokens"],
            "available_tokens": allocation["available_tokens"],
            "remaining_after_task_tokens": allocation["remaining_after_task_tokens"],
            "fit": fit,
            "recommendation": allocation["allocation_recommendation"],
            "fit_recommendation": dispatch_recommendation(fit),
        },
        "allocation": allocation,
        "recent_utilization": recent,
        "note": "Estimated from persisted thread items. Hidden system prompts, tool schemas, compression policy and tokenizer differences are not fully observable.",
    }


def _item_type_counts(items: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        name = str(item.get("item_type") or "unknown")
        counts[name] = counts.get(name, 0) + 1
    return counts

