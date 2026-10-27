#!/usr/bin/env python3
"""The single daily CLI for serial-job-session v5 (Python 3.11+, stdlib)."""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

import sjs_store as store
from sjs_config import SJSError, require


def emit(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "context", "start", "apply", "handoff", "check", "rebuild", "recover", "stats", "sync"))
    parser.add_argument("--instance")
    parser.add_argument("--route-key")
    parser.add_argument("--workspace")
    parser.add_argument("--input", help="JSON file or - for stdin")
    parser.add_argument("--task-id")
    parser.add_argument("--command-id")
    parser.add_argument("--expected-revision", type=int)
    parser.add_argument("--actor", default="orchestrator")
    parser.add_argument("--human", action="store_true", help="Use only for an actual human instruction, not agent self-approval")
    parser.add_argument("--role", default="orchestrator")
    parser.add_argument("--domain")
    parser.add_argument("--host-session")
    parser.add_argument("--since", type=int)
    parser.add_argument("--max-chars", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--break-stale-lock", action="store_true")
    parser.add_argument("--append-library", action="store_true")
    parser.add_argument("--event", choices=("probe", "provision", "bind", "flush"), default="flush")
    args = parser.parse_args(argv)
    try:
        request = None
        if args.input:
            request = json.loads(sys.stdin.read()) if args.input == "-" else store.read_json(args.input)
            require(isinstance(request, dict), "INPUT_OBJECT_REQUIRED")
        require(args.instance or args.route_key, "INSTANCE_REQUIRED")
        root = Path(args.instance).resolve() if args.instance else store.resolve_route(args.route_key, args.workspace)
        if args.command == "init":
            require(args.instance and request is not None, "INSTANCE_AND_CONFIG_REQUIRED")
            result = store.initialize(root, request, dry_run=args.dry_run)
        elif args.command == "apply":
            require(request is not None, "INPUT_REQUIRED")
            result = store.apply_command(root, request)
        elif args.command in {"context", "handoff"}:
            from sjs_context import build_context
            if args.command == "handoff":
                require(args.task_id is not None, "TASK_ID_REQUIRED")
            result = build_context(store.load_snapshot(root), task_id=args.task_id, role=args.role, domain=args.domain,
                                   host_session=args.host_session, since=args.since, max_chars=args.max_chars, handoff=args.command == "handoff")
        elif args.command == "start":
            require(args.task_id is not None, "TASK_ID_REQUIRED")
            state = store.load_snapshot(root)
            result = store.apply_command(root, {"command_id": args.command_id or uuid.uuid4().hex,
                       "expected_revision": state["revision"] if args.expected_revision is None else args.expected_revision,
                       "actor": {"id": args.actor, "kind": "human" if args.human else "agent"},
                       "operations": [{"op": "start", "task_id": args.task_id}]})
        elif args.command == "check":
            result = store.check_instance(root)
        elif args.command == "rebuild":
            state = store.rebuild(root, persist=not args.dry_run)
            result = {"ok": True, "revision": state["revision"], "tasks": len(state["tasks"]), "dry_run": args.dry_run}
        elif args.command == "recover":
            result = store.recover(root, dry_run=args.dry_run, break_stale=args.break_stale_lock)
        elif args.command == "stats":
            state = store.load_snapshot(root)
            require(state["config"]["modules"]["estimates"], "ESTIMATES_DISABLED")
            import sjs_estimates
            result = sjs_estimates.stats(root, state, append_library=args.append_library, dry_run=args.dry_run)
        else:
            state = store.load_snapshot(root)
            require(state["config"]["modules"]["feishu"], "FEISHU_DISABLED")
            import sjs_feishu
            result = sjs_feishu.sync(root, event=args.event, dry_run=args.dry_run)
        emit(result)
        return 0 if result.get("ok", True) else 1
    except SJSError as exc:
        emit({"ok": False, "error": exc.code, "message": str(exc), "details": exc.details})
        return 1
    except (OSError, ValueError, TypeError, KeyError) as exc:
        emit({"ok": False, "error": "INVALID_INPUT_OR_STORAGE", "message": str(exc)})
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
