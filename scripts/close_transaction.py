#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确定性 Close 事务：prepare / commit / recover。核心结案成功后不因 outbox 失败回滚。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from estimate_stats import parse_card, parse_duration_hours
from instantiate_domain import runtime_backup_dir
from time_ledger import compute_ledger

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

OUTBOX_ACTIONS = ("plan_rollup", "library_append", "progress_snapshot")
OPTIONAL_OUTBOX_ACTIONS = ("feishu_sync",)


def _protocol_major(instance_root: Path) -> int:
    """Dispatch legacy close callers without letting them write v4 sources."""

    try:
        payload = json.loads((instance_root / "INSTANCE.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 3
    raw = str(payload.get("protocol_version") or "")
    match = re.match(r"\s*(\d+)", raw)
    try:
        return int(match.group(1)) if match else 3
    except (TypeError, ValueError):
        return 3


def _v4_card(instance_root: Path, job_id: str) -> tuple[dict, dict]:
    import sjs_v4

    snapshot = sjs_v4.load_snapshot(instance_root)
    card = (snapshot.get("cards_by_id") or {}).get(str(job_id))
    if card is None:
        for item in snapshot.get("cards") or []:
            if str(item.get("shape")) == str(job_id):
                card = item
                break
    if card is None:
        raise SystemExit("CARD_NOT_FOUND")
    return snapshot, card


def _prepare_v4(instance_root: Path, job_id: str, status: str, command_id: str) -> dict:
    """Use the v4 semantic operation while preserving the old CLI shape."""

    import sjs_v4

    snapshot, card = _v4_card(instance_root, job_id)
    operation = "accept" if status == "accepted" else "cancel"
    command = {
        "command_id": str(command_id),
        "expected_revision": int(snapshot.get("revision") or 0),
        "card_id": str(card.get("id")),
        "operation": {"op": operation, "card_id": str(card.get("id"))},
    }
    result = sjs_v4.apply_command(instance_root, command)
    result.setdefault("schema", "serial-job-session-v4-close-wrapper")
    result.setdefault("phase", "committed")
    result.setdefault("job_id", card.get("id"))
    result.setdefault("status", status)
    return result


def pending_outbox_actions(instance_root: Path) -> List[str]:
    actions = list(OUTBOX_ACTIONS)
    bind = instance_root / "feishu.json"
    if bind.is_file():
        try:
            data = json.loads(bind.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {"enabled": True}
        if data.get("enabled") is not False:
            actions.append("feishu_sync")
    return actions

TX_DIRNAME = ".sjs-tx"
FORBIDDEN_ROLES = {"reviewer": "REVIEWER_MUST_NOT_CLOSE", "worker": "WORKER_MUST_NOT_CLOSE"}
CONTROL_FILES = ("CURRENT.md", "LOG.md", "JOB-SHAPES.md", "FACTS.md", "NORMS.md")


def maybe_fault(point: str) -> None:
    wanted = os.environ.get("SJS_FAULT_INJECT") or ""
    if wanted and wanted == point:
        raise RuntimeError("FAULT_INJECT:" + point)


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def tx_dir(instance_root: Path) -> Path:
    return instance_root / TX_DIRNAME


def journal_path(instance_root: Path) -> Path:
    return tx_dir(instance_root) / "journal.json"


def load_journal(instance_root: Path) -> Optional[dict]:
    path = journal_path(instance_root)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def job_path(instance_root: Path, job_id: str) -> Path:
    return instance_root / "jobs" / f"{job_id}.md"


def file_sha(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def set_yaml_field(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"^({re.escape(key)}\s*:).*$", re.M)
    if pattern.search(text):
        return pattern.sub(rf"\1 {value}", text, count=1)
    fence = re.search(r"(```ya?ml\s*\n)(.*?)(\n```)", text, flags=re.S)
    if fence:
        body = fence.group(2)
        if not body.endswith("\n"):
            body += "\n"
        body += f"{key}: {value}"
        return text[: fence.start()] + fence.group(1) + body + fence.group(3) + text[fence.end() :]
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[:end] + f"\n{key}: {value}" + text[end:]
    return text


def format_elapsed(hours: Optional[float]) -> Optional[str]:
    if hours is None:
        return None
    total = int(round(float(hours) * 3600))
    if total < 0:
        total = 0
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%dh %dm %02ds" % (h, m, s)
    return "%dm %02ds" % (m, s)


def extract_facts_bullets(job_text: str) -> List[str]:
    match = re.search(
        r"^##\s*拟写入 FACTS[^\n]*\n(.*?)(?=^##\s|\Z)",
        job_text or "",
        flags=re.M | re.S,
    )
    if not match:
        return []
    bullets = []
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            item = stripped[2:].strip()
            if item.startswith("（") or item in {"无", "无。"}:
                continue
            bullets.append(item)
    return bullets


def move_menu_to_closed(text: str, shape: int, job_id: str, estimate, attempt, elapsed: str, ai_net, variance) -> str:
    title = ""
    pending = re.search(r"(^##\s*待做[^\n]*\n)(.*?)(?=^##\s|\Z)", text or "", flags=re.M | re.S)
    if pending:
        body = pending.group(2)
        for line in body.splitlines():
            if re.match(r"^\|\s*%d\s*\|" % shape, line.strip()):
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if len(cells) >= 3:
                    title = cells[2]
                break
        body = re.sub(r"^\|\s*%d\s*\|.*\n" % shape, "", body, count=1, flags=re.M)
        text = text[: pending.start(2)] + body + text[pending.end(2) :]
    archived = "| %s | %s | %s | %s | %s | %s | %s | %s |" % (
        shape,
        title or job_id,
        job_id,
        estimate if estimate is not None else "",
        attempt,
        elapsed or "",
        ("%sh" % ai_net) if ai_net is not None else "",
        variance if variance is not None else "",
    )
    closed = re.search(r"(^##\s*已结档[^\n]*\n)(.*?)(?=^##\s|\Z)", text, flags=re.M | re.S)
    if not closed:
        return text
    block = closed.group(2)
    if job_id in block:
        return text
    if "|---" in block:
        lines = block.splitlines(keepends=True)
        inserted = False
        new_lines = []
        for line in lines:
            new_lines.append(line)
            if (not inserted) and re.search(r"^\|[-: ]+\|", line.strip()):
                new_lines.append(archived + "\n")
                inserted = True
        if not inserted:
            new_lines.append(archived + "\n")
        block = "".join(new_lines)
    else:
        block = block.rstrip() + "\n" + archived + "\n"
    return text[: closed.start(2)] + block + text[closed.end(2) :]


def apply_current_idle(text: str, journal: dict, now: str) -> str:
    """只改 YAML 字段与「现场」段，禁止对全文做 running→idle 替换。"""
    status = journal.get("status")
    job_id = journal.get("job_id")
    text = set_yaml_field(text, "status", "idle")
    text = set_yaml_field(text, "job_id", "null")
    text = set_yaml_field(text, "active_job", "null")
    text = set_yaml_field(text, "active_transaction", "null")
    text = set_yaml_field(text, "updated_at", now)
    if status == "accepted":
        text = set_yaml_field(text, "last_accepted", str(job_id))
    elif status == "rejected":
        text = set_yaml_field(text, "last_rejected", str(job_id))
    scene = "`idle`。`%s` 已结档为 %s。禁止自动开工。" % (job_id, status)
    text = re.sub(
        r"(##\s*现场\s*\n)(.*?)(?=\n##\s|\Z)",
        r"\1\n" + scene + "\n",
        text,
        count=1,
        flags=re.S,
    )
    return text


def prepare(instance_root: Path, job_id: str, status: str, command_id: str, closer_role: str) -> dict:
    role = str(closer_role or "").strip().lower()
    if role in FORBIDDEN_ROLES:
        raise SystemExit(FORBIDDEN_ROLES[role])
    if _protocol_major(instance_root) == 4:
        if status not in {"accepted", "rejected"}:
            raise SystemExit("ILLEGAL_CLOSE_STATUS")
        return _prepare_v4(instance_root, job_id, status, command_id)
    existing = load_journal(instance_root)
    if existing and existing.get("phase") == "prepared":
        if existing.get("command_id") == command_id and existing.get("job_id") == job_id:
            return existing
        raise SystemExit("UNFINISHED_TRANSACTION")
    path = job_path(instance_root, job_id)
    if not path.exists():
        raise SystemExit("JOB_NOT_FOUND")
    text = path.read_text(encoding="utf-8")
    front = parse_card(path) or {}
    if front.get("id") != job_id:
        raise SystemExit("JOB_ID_MISMATCH")
    current_status = str(front.get("status") or "")
    if current_status not in ("done_pending", "rework_required", "running"):
        raise SystemExit("ILLEGAL_CLOSE_FROM_%s" % current_status)
    if status not in ("accepted", "rejected"):
        raise SystemExit("ILLEGAL_CLOSE_STATUS")
    if status == "accepted" and front.get("gate") is True and front.get("ruler_ok") is not True:
        raise SystemExit("GATE_NOT_OK")
    if status == "accepted" and str(front.get("execution_mode") or "direct") == "embedded_workflow":
        from validate_light_workflow import locate_manifest, parent_close_errors

        manifest_path = locate_manifest(instance_root, front)
        if manifest_path is None:
            raise SystemExit("WORKFLOW_MANIFEST_MISSING")
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        expected_rev = front.get("workflow_revision")
        blockers = parent_close_errors(payload, front.get("id"), expected_rev)
        if blockers:
            raise SystemExit(blockers[0])
    ledger = compute_ledger(front, text)
    interval_errors = ledger.get("interval_errors") or []
    if interval_errors:
        raise SystemExit("INTERVAL_INVALID:" + ",".join(interval_errors))
    expected_rev = int(front.get("closure_revision") or 0)
    targets = [str(path.relative_to(instance_root))]
    hashes = {str(path.relative_to(instance_root)): file_sha(path)}
    for name in CONTROL_FILES:
        rel = name
        p = instance_root / name
        if p.is_file():
            targets.append(rel)
            hashes[rel] = file_sha(p)
    journal = {
        "schema": "serial-job-session-closure/v1",
        "command_id": command_id,
        "job_id": job_id,
        "shape": front.get("shape"),
        "status": status,
        "phase": "prepared",
        "expected_revision": expected_rev,
        "next_revision": expected_rev + 1,
        "closer_role": closer_role,
        "ledger": {k: v for k, v in ledger.items() if k != "interval_errors"},
        "interval_errors": [],
        "attempt": front.get("attempt") or 1,
        "estimate": front.get("estimate_total") or front.get("estimate"),
        "facts_bullets": extract_facts_bullets(text),
        "targets": targets,
        "before_sha256": hashes,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    tx_dir(instance_root).mkdir(parents=True, exist_ok=True)
    atomic_write_text(journal_path(instance_root), json.dumps(journal, ensure_ascii=False, indent=2) + "\n")
    return journal


def _apply_job(text: str, journal: dict, now: str) -> str:
    text = set_yaml_field(text, "status", journal["status"])
    text = set_yaml_field(text, "closure_revision", str(journal["next_revision"]))
    text = set_yaml_field(text, "closed_at", now)
    front_end = None
    match = re.search(r"^ended_at:\s*(.*)$", text, flags=re.M)
    if match:
        raw = match.group(1).strip()
        if raw and raw not in {"null", "None"}:
            front_end = raw
    if not front_end:
        text = set_yaml_field(text, "ended_at", now)
        front_end = now
    ledger = journal.get("ledger") or {}
    for key in (
        "ai_first_pass",
        "ai_rework",
        "ai_net",
        "ai_effort",
        "business_wait",
        "delivery_workhours",
        "wall_elapsed",
        "ai_variance",
        "delivery_variance",
    ):
        if ledger.get(key) is not None:
            text = set_yaml_field(text, key, str(ledger[key]))
    wall = ledger.get("wall_elapsed")
    human = format_elapsed(wall) if wall is not None else None
    if human:
        text = set_yaml_field(text, "elapsed", human)
    elif ledger.get("ai_net") is not None:
        text = set_yaml_field(text, "elapsed", format_elapsed(ledger["ai_net"]) or str(ledger["ai_net"]))
    var = ledger.get("ai_variance")
    if var is not None:
        text = set_yaml_field(text, "variance", str(round(float(var), 2)))
    return text


def _append_log(instance_root: Path, journal: dict, elapsed: str) -> None:
    log_path = instance_root / "LOG.md"
    if not log_path.exists():
        return
    day = datetime.now().date().isoformat()
    job_id = journal["job_id"]
    status = journal["status"]
    existing = log_path.read_text(encoding="utf-8")
    if "| %s |" % job_id in existing and ("%s；耗时" % status) in existing:
        return
    ledger = journal.get("ledger") or {}
    ai_net = ledger.get("ai_net")
    estimate = journal.get("estimate")
    variance = ledger.get("ai_variance")
    attempt = journal.get("attempt") or 1
    shape = journal.get("shape") or ""
    line = (
        "| %s | %s | %s | %s；耗时 墙钟 %s / 净 %sh（估 %sh，偏差 %s）attempt %s |\n"
        % (
            day,
            shape,
            job_id,
            status,
            elapsed or "—",
            ai_net if ai_net is not None else "—",
            estimate if estimate is not None else "—",
            round(float(variance), 2) if variance is not None else "—",
            attempt,
        )
    )
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(line)


def _append_facts(instance_root: Path, journal: dict) -> None:
    if journal.get("status") != "accepted":
        return
    facts_path = instance_root / "FACTS.md"
    if not facts_path.exists():
        return
    bullets = journal.get("facts_bullets") or []
    if not bullets:
        return
    day = datetime.now().date().isoformat()
    job_id = journal["job_id"]
    text = facts_path.read_text(encoding="utf-8")
    extra = []
    for item in bullets:
        if job_id in text and item[:24] in text:
            continue
        extra.append("| %s | %s | %s |" % (item, job_id, day))
    if extra:
        atomic_write_text(facts_path, text.rstrip() + "\n" + "\n".join(extra) + "\n")


def _write_outbox(instance_root: Path, journal: dict) -> None:
    outbox = {
        "schema": "serial-job-session-outbox/v1",
        "command_id": journal["command_id"],
        "job_id": journal["job_id"],
        "pending": pending_outbox_actions(instance_root),
        "done": [],
    }
    atomic_write_text(tx_dir(instance_root) / "outbox.json", json.dumps(outbox, ensure_ascii=False, indent=2) + "\n")


def _backup(instance_root: Path, journal: dict) -> Path:
    backup = runtime_backup_dir(instance_root, "tx-backup", str(journal["command_id"]))
    for rel in journal.get("targets") or []:
        src = instance_root / rel
        if src.is_file():
            dest = backup / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
    return backup


def commit(instance_root: Path, command_id: Optional[str] = None) -> dict:
    if _protocol_major(instance_root) == 4:
        import sjs_v4

        try:
            return sjs_v4.recover_transaction(
                instance_root,
                command_id=command_id,
                action="resume",
            )
        except sjs_v4.SJSV4Error as exc:
            if exc.code != "NO_TRANSACTION":
                raise
            # ``prepare`` applies the semantic v4 command atomically.  A
            # second legacy commit is therefore an idempotent no-op when the
            # transaction journal has already been compacted/removed.
            return {
                "schema": "serial-job-session-v4-close-wrapper",
                "phase": "committed",
                "command_id": command_id,
                "replayed": True,
            }
    existing = load_journal(instance_root)
    if not existing:
        raise SystemExit("NO_PREPARED_TX")
    if command_id and existing.get("command_id") != command_id:
        raise SystemExit("COMMAND_ID_MISMATCH")
    if existing.get("phase") == "committed":
        return existing
    if existing.get("phase") != "prepared":
        raise SystemExit("NOT_PREPARED")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    backup = _backup(instance_root, existing)
    existing["backup_dir"] = str(backup)
    atomic_write_text(journal_path(instance_root), json.dumps(existing, ensure_ascii=False, indent=2) + "\n")
    maybe_fault("after_backup")
    path = job_path(instance_root, existing["job_id"])
    text = path.read_text(encoding="utf-8")
    updated = _apply_job(text, existing, now)
    atomic_write_text(path, updated)
    maybe_fault("after_job")
    ledger = existing.get("ledger") or {}
    elapsed = format_elapsed(ledger.get("wall_elapsed")) or format_elapsed(ledger.get("ai_net")) or ""
    shapes_path = instance_root / "JOB-SHAPES.md"
    if shapes_path.is_file():
        try:
            shape = int(str(existing.get("shape")).split()[0])
        except (TypeError, ValueError):
            shape = None
        if shape is not None:
            atomic_write_text(
                shapes_path,
                move_menu_to_closed(
                    shapes_path.read_text(encoding="utf-8"),
                    shape,
                    existing["job_id"],
                    existing.get("estimate"),
                    existing.get("attempt") or 1,
                    elapsed,
                    ledger.get("ai_net"),
                    round(float(ledger["ai_variance"]), 2) if ledger.get("ai_variance") is not None else None,
                ),
            )
    _append_facts(instance_root, existing)
    _append_log(instance_root, existing, elapsed)
    current = instance_root / "CURRENT.md"
    if current.exists():
        atomic_write_text(current, apply_current_idle(current.read_text(encoding="utf-8"), existing, now))
    maybe_fault("after_current")
    existing["phase"] = "committed"
    existing["committed_at"] = now
    atomic_write_text(journal_path(instance_root), json.dumps(existing, ensure_ascii=False, indent=2) + "\n")
    maybe_fault("after_journal")
    _write_outbox(instance_root, existing)
    maybe_fault("after_outbox")
    return existing


def recover(instance_root: Path, action: str = "commit") -> dict:
    if _protocol_major(instance_root) == 4:
        import sjs_v4

        return sjs_v4.recover_transaction(
            instance_root,
            action="rollback" if action == "rollback" else "resume",
        )
    journal = load_journal(instance_root)
    if not journal:
        raise SystemExit("NO_TX")
    if action == "rollback":
        backup = journal.get("backup_dir")
        if backup and Path(backup).is_dir():
            for path in Path(backup).rglob("*"):
                if path.is_file():
                    rel = path.relative_to(backup)
                    dest = instance_root / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, dest)
        journal["phase"] = "rolled_back"
        atomic_write_text(journal_path(instance_root), json.dumps(journal, ensure_ascii=False, indent=2) + "\n")
        return journal
    if journal.get("phase") == "prepared":
        return commit(instance_root, journal.get("command_id"))
    return journal


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Close 事务")
    parser.add_argument("action", choices=["prepare", "commit", "recover"])
    parser.add_argument("--instance")
    parser.add_argument("--job-id")
    parser.add_argument("--status", choices=["accepted", "rejected"])
    parser.add_argument("--command-id", default="cmd-1")
    parser.add_argument("--closer-role", default="control_owner")
    parser.add_argument("--recover-action", default="commit", choices=["commit", "rollback"])
    args = parser.parse_args(argv)
    root = Path(args.instance).resolve()
    if args.action == "prepare":
        journal = prepare(root, args.job_id, args.status, args.command_id, args.closer_role)
        print(json.dumps(journal, ensure_ascii=False, indent=2))
        return 0
    if args.action == "commit":
        print(json.dumps(commit(root, args.command_id), ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(recover(root, args.recover_action), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
