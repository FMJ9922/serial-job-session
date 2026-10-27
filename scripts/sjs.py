#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Small command-line adapter for serial-job-session v4.

The command is intentionally a thin wrapper.  Cards and transactions live in
``sjs_v4``; this module only resolves an instance, validates input, and emits
plain JSON for an Agent or shell script.  A v3 instance is reported as legacy
and continues through its existing entrypoints; this command never rewrites it
implicitly.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any, List, Mapping, Optional

import sjs_v4


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    return str(value)


def _resolve_instance(args: argparse.Namespace) -> Path:
    if args.instance:
        return Path(args.instance).expanduser().resolve()
    if args.route_key:
        try:
            import registry

            entry = registry.resolve(args.route_key)
            if not entry:
                raise sjs_v4.SJSV4Error("ROUTE_KEY_NOT_FOUND", route_key=args.route_key)
            root = registry.find_repo_root()
            path = Path(str(entry.get("instance_root") or ""))
            return (path if path.is_absolute() else root / path).resolve()
        except sjs_v4.SJSV4Error:
            raise
        except Exception as exc:
            raise sjs_v4.SJSV4Error("ROUTE_RESOLUTION_FAILED", str(exc))
    raise sjs_v4.SJSV4Error("INSTANCE_REQUIRED")


def _protocol_major(root: Path) -> int:
    try:
        data = json.loads((root / "INSTANCE.json").read_text(encoding="utf-8"))
        raw = str(data.get("protocol_version") or "0")
        return int(raw.split(".", 1)[0])
    except (OSError, ValueError, TypeError):
        return 0


def _emit(payload: Any) -> None:
    print(json.dumps(_json_safe(payload), ensure_ascii=False, indent=2, sort_keys=True))


def _read_input(value: str) -> dict:
    if value == "-":
        text = sys.stdin.read()
    else:
        text = Path(value).read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise sjs_v4.SJSV4Error("INPUT_JSON_INVALID", str(exc))
    if not isinstance(payload, dict):
        raise sjs_v4.SJSV4Error("INPUT_OBJECT_REQUIRED")
    return payload


def _error(exc: Exception) -> int:
    if isinstance(exc, sjs_v4.SJSV4Error):
        _emit({"ok": False, "error": exc.code, "message": str(exc), "details": exc.details})
    else:
        _emit({"ok": False, "error": "SJS_FAILED", "message": str(exc)})
    return 1


def _context(root: Path, card_id: Optional[str], max_chars: int, *, shape: Any = None, full: bool = False) -> dict:
    snapshot = sjs_v4.load_snapshot(root, max_chars=max_chars)
    if card_id is None and shape is not None:
        for item in snapshot.get("cards") or []:
            if str(item.get("shape")) == str(shape):
                card_id = str(item.get("id"))
                break
    payload = sjs_v4.context_plugin(root, snapshot, card_id=card_id, budget_chars=max_chars)
    if payload is None:
        payload = sjs_v4.build_context(snapshot, card_id, max_chars=max_chars)
    menu = sjs_v4._adapter_call(
        "sjs_v4_context",
        "compact_menu",
        snapshot.get("cards") or [],
        current_card_id=card_id,
    )
    if menu is None:
        menu = {
            "schema": "serial-job-session-menu/v4",
            "cards": [
                {"card_id": card.get("id"), "shape": card.get("shape"), "status": card.get("status")}
                for card in snapshot.get("cards") or []
            ],
            "historical_scan": False,
            "output_evidence_read": False,
        }
    payload.setdefault("revision", snapshot.get("revision", 0))
    payload.setdefault("card_id", card_id)
    payload.setdefault("historical_scan", False)
    if not full:
        # The default is one bounded rendered context plus a compact menu.
        # Callers that need every structured section can request --full-context.
        # Keep the selected card and a bounded ready-card window in the menu;
        # the explicit full-context option remains available for a complete
        # machine-readable menu on large migrated instances.
        menu_cards = list(menu.get("cards") or []) if isinstance(menu, Mapping) else []
        if len(menu_cards) > 32:
            selected = [item for item in menu_cards if item.get("current") or item.get("card_id") == card_id]
            selected_ids = {str(item.get("card_id")) for item in selected}
            for item in menu_cards:
                if len(selected) >= 32:
                    break
                if str(item.get("card_id")) in selected_ids:
                    continue
                selected.append(item)
                selected_ids.add(str(item.get("card_id")))
            menu = dict(menu)
            menu["cards"] = selected
            menu["menu_truncated"] = True
            menu["omitted_cards"] = max(0, len(menu_cards) - len(selected))
        return {
            "schema": "serial-job-session-context/v4",
            "revision": payload.get("revision", snapshot.get("revision", 0)),
            "card_id": payload.get("card_id") or card_id,
            # The context adapter already applies its soft budget while
            # preserving protected constraints.  Do not truncate a protected
            # tail again at this CLI boundary.
            "text": str(payload.get("text") or ""),
            "menu": menu,
            "warnings": list(payload.get("warnings") or []),
            "historical_scan": False,
        }
    payload["menu"] = menu
    return payload


def _timing_for_command(root: Path, command: dict, args: argparse.Namespace, *, operation: str) -> tuple[dict, Optional[dict]]:
    """Read host timing and attach its sample to the same card transaction."""
    thread_id = command.get("thread_id") or args.thread_id
    turn_id = command.get("turn_id") or args.turn_id
    if (thread_id and not turn_id) or (turn_id and not thread_id):
        raise sjs_v4.SJSV4Error("TIMING_CONTEXT_INCOMPLETE")
    if not thread_id:
        # A command may be issued outside a host thread (for example by a
        # shell retry).  Keep an auditable, non-calibrating record in the same
        # transaction rather than silently losing the timing decision.  It is
        # deliberately marked untracked; a later host reconciliation can add a
        # real sample with explicit identifiers.
        operations = sjs_v4._command_operations(command)
        if any(sjs_v4._operation_name(item) in {"timing", "time", "record_timing", "reconcile_timing", "timing_record"} for item in operations):
            return command, {"schema": "serial-job-session-time/v4", "warning": "timing supplied by command", "sample": None}
        first_ref = operations[0] if operations else {}
        card_ref = command.get("card_id") or command.get("job_id") or first_ref.get("card_id") or first_ref.get("job_id")
        shape = command.get("shape") if command.get("shape") is not None else first_ref.get("shape")
        command_key = str(command.get("command_id") or uuid.uuid4().hex)
        sample = {
            "schema": "serial-job-session-time/v4",
            "thread_id": "untracked",
            "turn_id": "command:%s" % command_key,
            "complete": False,
            "count_for_calibration": False,
            "timing_source": "missing",
            "warning": "untracked: missing trusted thread_id/turn_id",
            "reconcile_next_invocation": False,
        }
        timing_operation = {"op": "record_timing", "timing": sample}
        if card_ref is not None:
            timing_operation["card_id"] = card_ref
        if shape is not None:
            timing_operation["shape"] = shape
        enriched = dict(command)
        enriched.pop("operation", None)
        enriched["operations"] = operations + [timing_operation]
        return enriched, {"schema": "serial-job-session-time/v4", "sample": sample, "warning": sample["warning"]}
    try:
        timing = sjs_v4.time_adapter(root, thread_id=thread_id, turn_id=turn_id, operation=operation)
    except Exception as exc:
        raise sjs_v4.SJSV4Error("TIMING_ADAPTER_FAILED", str(exc))
    sample = timing.get("sample") if isinstance(timing, Mapping) else None
    if not isinstance(sample, Mapping):
        return command, timing if isinstance(timing, dict) else None
    if command.get("timing") or command.get("time_record"):
        return command, timing
    operations = sjs_v4._command_operations(command)
    first_ref = operations[0] if operations else {}
    card_ref = command.get("card_id") or command.get("job_id") or first_ref.get("card_id") or first_ref.get("job_id")
    shape = command.get("shape") if command.get("shape") is not None else first_ref.get("shape")
    timing_operation = {"op": "record_timing", "timing": dict(sample)}
    if card_ref is not None:
        timing_operation["card_id"] = card_ref
    if shape is not None:
        timing_operation["shape"] = shape
    operations.append(timing_operation)
    enriched = dict(command)
    enriched.pop("operation", None)
    enriched["operations"] = operations
    return enriched, timing


def _start(root: Path, args: argparse.Namespace) -> dict:
    snapshot = sjs_v4.load_snapshot(root)
    if args.input:
        command = _read_input(args.input)
    else:
        if not args.card_id and args.shape is None:
            raise sjs_v4.SJSV4Error("CARD_ID_OR_SHAPE_REQUIRED")
        reference = args.card_id or "shape-%s" % args.shape
        if args.command_id:
            command_id = args.command_id
        else:
            suffix = [str(args.thread_id), str(args.turn_id)] if args.thread_id and args.turn_id else [uuid.uuid4().hex]
            command_id = "start-%s-%s" % (reference, "-".join(suffix))
        operation = {"op": "start"}
        if args.card_id:
            operation["card_id"] = args.card_id
        else:
            operation["shape"] = args.shape
        command = {
            "command_id": command_id,
            "expected_revision": args.expected_revision if args.expected_revision is not None else snapshot.get("revision", 0),
            "operation": operation,
        }
    command, timing = _timing_for_command(root, command, args, operation="start")
    result = sjs_v4.apply_command(root, command)
    selected_id = args.card_id
    if selected_id is None and args.shape is not None:
        selected = next((item for item in sjs_v4.load_snapshot(root).get("cards") or [] if str(item.get("shape")) == str(args.shape)), None)
        selected_id = selected.get("id") if selected else None
    context = _context(root, selected_id, args.max_chars, shape=args.shape, full=args.full_context)
    result["context"] = context
    if timing is not None:
        result["timing"] = timing
    return result


def _handoff(root: Path, args: argparse.Namespace) -> dict:
    snapshot = sjs_v4.load_snapshot(root, max_chars=args.max_chars)
    selected_id = args.card_id
    if selected_id is None and args.shape is not None:
        selected = next((item for item in snapshot.get("cards") or [] if str(item.get("shape")) == str(args.shape)), None)
        selected_id = selected.get("id") if selected else None
    if not selected_id:
        raise sjs_v4.SJSV4Error("CARD_ID_OR_SHAPE_REQUIRED")
    card = snapshot.get("cards_by_id", {}).get(str(selected_id))
    if card is None:
        raise sjs_v4.SJSV4Error("CARD_NOT_FOUND", card_id=args.card_id)
    result = None
    try:
        # The context adapter owns the handoff projection.  Supplying the
        # complete snapshot here preserves decisions, constraints and timing
        # evidence that a card-only call cannot see.
        result = sjs_v4._adapter_call(
            "sjs_v4_context",
            "handoff_context",
            root,
            snapshot,
            str(selected_id),
            max_chars=args.max_chars,
        )
    except Exception as exc:
        raise sjs_v4.SJSV4Error("HANDOFF_ADAPTER_FAILED", str(exc))
    return result or sjs_v4.handoff_context(snapshot, str(selected_id), max_chars=args.max_chars)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="serial-job-session v4 command adapter")
    parser.add_argument("command", choices=["context", "start", "apply", "handoff", "check", "migrate"])
    parser.add_argument("--instance")
    parser.add_argument("--route-key")
    parser.add_argument("--card-id", "--job-id", dest="card_id")
    parser.add_argument("--shape", type=int, help="numeric card shape; avoids requiring a full card id")
    parser.add_argument("--input", help="JSON file or - for stdin")
    parser.add_argument("--command-id")
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--max-chars", type=int, default=12000)
    parser.add_argument("--thread-id")
    parser.add_argument("--turn-id")
    parser.add_argument("--full-context", action="store_true", help="return all structured context fields")
    parser.add_argument("--mode", choices=["dry-run", "apply", "recover", "rollback"], default="dry-run")
    parser.add_argument("--plan", help="migration plan JSON path")
    parser.add_argument("--transaction", help="migration transaction id for apply/recover/rollback")
    parser.add_argument("--direction-config", help="migration direction review configuration")
    parser.add_argument("--compare-projections", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = _resolve_instance(args)
        major = _protocol_major(root)
        if major != 4:
            if major == 3 and args.command == "check":
                # Read-only checks still run the real v3 validator.  A green
                # dispatch marker alone would hide malformed live instances.
                import validate_domain_pack

                legacy = validate_domain_pack.validate_domain(root, allow_draft=False)
                result = {
                    "ok": bool(legacy.get("ok")),
                    "protocol_version": "3",
                    "dispatch": "legacy-v3",
                    "instance": str(root),
                    "check": legacy,
                }
                _emit(result)
                return 0 if result["ok"] else 1
            if major == 3 and args.command in {"context", "handoff"}:
                _emit({"ok": True, "protocol_version": str(major), "dispatch": "legacy-v3", "instance": str(root)})
                return 0
            if major not in {3} or args.command not in {"migrate"}:
                raise sjs_v4.SJSV4Error("UNSUPPORTED_PROTOCOL", protocol_major=major, command=args.command)
        if args.command == "context":
            _emit(_context(root, args.card_id, args.max_chars, shape=args.shape, full=args.full_context))
        elif args.command == "start":
            _emit(_start(root, args))
        elif args.command == "apply":
            if not args.input:
                raise sjs_v4.SJSV4Error("INPUT_REQUIRED")
            command, timing = _timing_for_command(root, _read_input(args.input), args, operation="apply")
            result = sjs_v4.apply_command(root, command)
            if timing is not None:
                result["timing"] = timing
            _emit(result)
        elif args.command == "handoff":
            _emit(_handoff(root, args))
        elif args.command == "check":
            result = sjs_v4.check_instance(root, compare_projections=args.compare_projections)
            result.pop("snapshot", None)
            _emit(result)
            return 0 if result.get("ok") else 1
        elif args.command == "migrate":
            kwargs = {
                key: value
                for key, value in {
                    "plan": args.plan,
                    "transaction": args.transaction,
                    "direction_config": args.direction_config,
                }.items()
                if value is not None
            }
            _emit(sjs_v4.migrate_instance(root, args.mode, **kwargs))
        return 0
    except Exception as exc:
        return _error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
