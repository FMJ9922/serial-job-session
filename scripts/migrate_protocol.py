#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""协议 3.0 可恢复迁移器：dry-run / apply / recover。禁止原地无记录改写。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from close_transaction import atomic_write_text, set_yaml_field
from estimate_stats import parse_card
from instantiate_domain import runtime_backup_dir
import registry as sjs_registry

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

READY = {"ready"}
TERMINAL = {"accepted", "rejected"}
V2_DEFAULTS = {
    "estimate_total": None,
    "closure_revision": "0",
    "execution_mode": "direct",
}


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def journal_file(instance_root: Path) -> Path:
    return instance_root / ".sjs-tx" / "migrate-journal.json"


def backup_dir(instance_root: Path) -> Path:
    return runtime_backup_dir(instance_root, "migrate-backup")


def is_live_registered(instance_root: Path, repo_root: Path) -> bool:
    resolved = instance_root.resolve()
    for entry in sjs_registry.list_instances(repo_root):
        raw = str(entry.get("instance_root") or "")
        if not raw:
            continue
        path = Path(raw)
        if not path.is_absolute():
            path = repo_root / raw
        try:
            if path.resolve() == resolved:
                return True
        except OSError:
            continue
    return False


def planned_instance_payload(instance: dict, route_key: str) -> dict:
    data = dict(instance)
    data["schema"] = "serial-job-session-instance/v2"
    data["route_key"] = route_key
    data["protocol_version"] = data.get("protocol_version") or "3.0.0"
    data["registry_ref"] = ".serial-job-session/instances.json"
    data["protocol_entry"] = data.get("protocol_entry") or "protocol/PROTOCOL.md"
    data["intent_entry"] = data.get("intent_entry") or "protocol/INTENTS.md"
    data["required_reads"] = data.get("required_reads") or ["ENV.md", "CURRENT.md", "JOB-SHAPES.md"]
    return data


def patch_ready_text(text: str, card: dict) -> str:
    if str(card.get("status")) not in READY:
        return text
    out = text
    if "estimate_total" not in card:
        estimate = card.get("estimate")
        out = set_yaml_field(out, "estimate_total", "null" if estimate is None else str(estimate))
    if "closure_revision" not in card:
        out = set_yaml_field(out, "closure_revision", "0")
    if "execution_mode" not in card:
        out = set_yaml_field(out, "execution_mode", "direct")
    return out


def collect_plan(instance_root: Path, route_key: str) -> dict:
    instance_path = instance_root / "INSTANCE.json"
    instance = json.loads(instance_path.read_text(encoding="utf-8"))
    files: List[dict] = []
    new_instance = planned_instance_payload(instance, route_key)
    before = instance_path.read_text(encoding="utf-8")
    after = json.dumps(new_instance, ensure_ascii=False, indent=2) + "\n"
    files.append(
        {
            "path": "INSTANCE.json",
            "before_sha256": sha256_text(before),
            "after_sha256": sha256_text(after),
            "changed": before != after,
            "risk": "upgrade_instance_v2",
        }
    )
    ready_jobs = []
    skipped_terminal = []
    jobs_dir = instance_root / "jobs"
    if jobs_dir.is_dir():
        for path in sorted(jobs_dir.glob("*.md")):
            if path.name.startswith("_"):
                continue
            card = parse_card(path) or {}
            status = str(card.get("status") or "")
            rel = "jobs/" + path.name
            original = path.read_text(encoding="utf-8")
            if status in TERMINAL:
                skipped_terminal.append(path.name)
                files.append(
                    {
                        "path": rel,
                        "before_sha256": sha256_text(original),
                        "after_sha256": sha256_text(original),
                        "changed": False,
                        "risk": "terminal_readonly",
                    }
                )
                continue
            if status not in READY:
                continue
            patched = patch_ready_text(original, card)
            ready_jobs.append(path.name)
            files.append(
                {
                    "path": rel,
                    "before_sha256": sha256_text(original),
                    "after_sha256": sha256_text(patched),
                    "changed": original != patched,
                    "risk": "ready_v2_defaults",
                }
            )
    return {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "instance_root": str(instance_root),
        "from_schema": instance.get("schema"),
        "route_key": route_key,
        "ready_jobs": ready_jobs,
        "skipped_terminal": skipped_terminal,
        "files": files,
        "phase": "planned",
        "old_version": instance.get("protocol_version"),
    }


def render_after(instance_root: Path, rel: str, route_key: str) -> str:
    path = instance_root / rel
    if rel == "INSTANCE.json":
        data = json.loads(path.read_text(encoding="utf-8"))
        return json.dumps(planned_instance_payload(data, route_key), ensure_ascii=False, indent=2) + "\n"
    card = parse_card(path) or {}
    return patch_ready_text(path.read_text(encoding="utf-8"), card)


def apply_plan(instance_root: Path, plan: dict, route_key: str) -> dict:
    backups = backup_dir(instance_root)
    backups.mkdir(parents=True, exist_ok=True)
    changed = [item["path"] for item in plan.get("files") or [] if item.get("changed")]
    for rel in changed:
        src = instance_root / rel
        dest = backups / rel.replace("/", "__")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    plan["phase"] = "prepared"
    plan["applied"] = []
    plan["backup_dir"] = str(backups)
    journal_file(instance_root).parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(journal_file(instance_root), json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    applied = []
    for rel in changed:
        atomic_write_text(instance_root / rel, render_after(instance_root, rel, route_key))
        applied.append(rel)
    plan["phase"] = "applied"
    plan["applied"] = applied
    return plan


def recover_plan(instance_root: Path) -> dict:
    journal = json.loads(journal_file(instance_root).read_text(encoding="utf-8"))
    recorded = journal.get("backup_dir")
    backups = Path(recorded) if recorded else backup_dir(instance_root)
    restored = []
    targets = journal.get("applied") or [
        item["path"] for item in journal.get("files") or [] if item.get("changed")
    ]
    for rel in targets:
        src = backups / rel.replace("/", "__")
        if not src.is_file():
            continue
        atomic_write_text(instance_root / rel, src.read_text(encoding="utf-8"))
        restored.append(rel)
    journal["phase"] = "rolled_back"
    journal["restored"] = restored
    atomic_write_text(journal_file(instance_root), json.dumps(journal, ensure_ascii=False, indent=2) + "\n")
    return journal


def register_instance(instance: dict, repo_root: Path) -> None:
    sjs_registry.register(
        {
            "route_key": instance["route_key"],
            "aliases": list(instance.get("aliases") or []),
            "instance_id": instance["instance_id"],
            "domain_name": instance["domain_name"],
            "instance_root": instance["instance_root"],
            "protocol_entry": instance.get("protocol_entry") or "protocol/PROTOCOL.md",
            "intent_entry": instance.get("intent_entry") or "protocol/INTENTS.md",
            "required_reads": instance.get("required_reads") or ["ENV.md", "CURRENT.md", "JOB-SHAPES.md"],
            "enabled": True,
            "protocol_version": instance.get("protocol_version") or "3.0.0",
        },
        repo_root,
        require_exists=False,
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["dry-run", "apply", "recover"])
    parser.add_argument("--instance", required=True)
    parser.add_argument("--route-key", required=True)
    parser.add_argument("--repo-root")
    parser.add_argument("--allow-live", action="store_true")
    parser.add_argument("--register", action="store_true", help="apply 后写入注册表；默认只改实例副本")
    args = parser.parse_args(argv)
    root = Path(args.instance).resolve()
    repo = Path(args.repo_root).resolve() if args.repo_root else sjs_registry.find_repo_root(root)
    if args.action != "recover" and is_live_registered(root, repo) and not args.allow_live:
        print("LIVE_INSTANCE_FORBIDDEN")
        return 1
    if args.action == "recover":
        print(json.dumps(recover_plan(root), ensure_ascii=False, indent=2))
        return 0
    current = (root / "CURRENT.md").read_text(encoding="utf-8") if (root / "CURRENT.md").is_file() else ""
    if "status: running" in current or "status: awaiting_acceptance" in current:
        print("HARD STOP：实例非 idle，拒绝迁移")
        return 1
    plan = collect_plan(root, args.route_key)
    if args.action == "dry-run":
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    plan = apply_plan(root, plan, args.route_key)
    if args.register:
        instance = json.loads((root / "INSTANCE.json").read_text(encoding="utf-8"))
        register_instance(instance, repo)
        plan["registered"] = True
    atomic_write_text(journal_file(root), json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
