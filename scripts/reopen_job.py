#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""accepted → rework_required 安全撤销。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from estimate_stats import parse_card
from close_transaction import atomic_write_text, set_yaml_field
from menu_parse import parse_pending_table

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

STARTED = {"running", "done_pending", "rework_required", "accepted", "rejected"}


def load_jobs(instance_root: Path) -> List[dict]:
    jobs_dir = instance_root / "jobs"
    rows = []
    if not jobs_dir.exists():
        return rows
    for path in jobs_dir.glob("*.md"):
        text = path.read_text(encoding="utf-8")
        front = parse_card(path) or {}
        front["_path"] = str(path)
        front["_text"] = text
        rows.append(front)
    return rows


def shape_int(value) -> Optional[int]:
    try:
        return int(str(value).split()[0])
    except (TypeError, ValueError):
        return None


def downstream_started(target_shape: int, jobs: List[dict]) -> List[str]:
    hits = []
    for job in jobs:
        deps = job.get("depends_on") or []
        if not isinstance(deps, list):
            continue
        if target_shape in [shape_int(x) for x in deps]:
            if str(job.get("status")) in STARTED:
                hits.append(str(job.get("id")))
    return hits


def parallel_consumers(instance_root: Path, job_id: str) -> List[str]:
    current = instance_root / "CURRENT.md"
    if not current.is_file():
        return []
    text = current.read_text(encoding="utf-8")
    found = []
    in_parallel = False
    for line in text.splitlines():
        if line.strip().startswith("parallel_pending_jobs:"):
            in_parallel = True
            if "[]" in line:
                return []
            continue
        if in_parallel:
            if re.match(r"^[a-z_]", line) and not line.startswith(" ") and not line.startswith("-"):
                break
            match = re.search(r"job_id:\s*([^\s#]+)", line)
            if match and match.group(1) not in {"null", job_id}:
                found.append(match.group(1))
    return found


def tombstone_facts(instance_root: Path, job_id: str, rev: int) -> None:
    facts = instance_root / "FACTS.md"
    if not facts.is_file():
        return
    text = facts.read_text(encoding="utf-8")
    marker = "TOMBSTONE job_id=%s revision=%s" % (job_id, rev)
    if marker in text:
        return
    extra = "\n> %s 失效，不删除历史行。\n" % marker
    atomic_write_text(facts, text.rstrip() + extra)


def restore_menu_row(instance_root: Path, shape: int, title: str, job_id: str) -> None:
    path = instance_root / "JOB-SHAPES.md"
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    pending, _ = parse_pending_table(text)
    if any(row["shape"] == shape for row in pending):
        return
    note = "| %s | | %s（返工 %s） | | | | | | | |\n" % (shape, title or job_id, job_id)
    section = re.search(r"(^##\s*待做[^\n]*\n)(.*?)(?=^##\s|\Z)", text, flags=re.M | re.S)
    if section:
        block = section.group(2)
        if "|---" in block:
            lines = block.splitlines(keepends=True)
            new_lines = []
            inserted = False
            for line in lines:
                new_lines.append(line)
                if (not inserted) and re.search(r"^\|[-: ]+\|", line.strip()):
                    new_lines.append(note)
                    inserted = True
            block = "".join(new_lines)
            text = text[: section.start(2)] + block + text[section.end(2) :]
    atomic_write_text(path, text)


def reopen(
    instance_root: Path,
    job_id: str,
    reason: str,
    irreversible: str = "unknown",
    contract_holds: str = "unknown",
) -> dict:
    if not str(reason or "").strip():
        raise SystemExit("REVOKE_REASON_REQUIRED")
    if contract_holds != "yes":
        raise SystemExit("CONTRACT_NOT_CONFIRMED")
    jobs = load_jobs(instance_root)
    target = next((j for j in jobs if j.get("id") == job_id), None)
    if not target:
        raise SystemExit("JOB_NOT_FOUND")
    if str(target.get("status")) != "accepted":
        raise SystemExit("NOT_ACCEPTED")
    if irreversible == "unknown":
        raise SystemExit("IRREVERSIBLE_UNKNOWN")
    if irreversible == "yes":
        raise SystemExit("IRREVERSIBLE_BLOCKED")
    shape = shape_int(target.get("shape"))
    if shape is None:
        raise SystemExit("SHAPE_MISSING")
    downstream = downstream_started(shape, jobs)
    if downstream:
        raise SystemExit("DOWNSTREAM_STARTED:" + ",".join(downstream))
    consumers = parallel_consumers(instance_root, job_id)
    if consumers:
        raise SystemExit("PARALLEL_CONSUMER:" + ",".join(consumers))
    path = Path(target["_path"])
    text = target["_text"]
    old_rev = int(target.get("closure_revision") or 1)
    attempt = int(target.get("attempt") or 1) + 1
    rev = old_rev + 1
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    text = set_yaml_field(text, "status", "rework_required")
    text = set_yaml_field(text, "attempt", str(attempt))
    text = set_yaml_field(text, "closure_revision", str(rev))
    text = set_yaml_field(text, "ruler_ok", "null")
    text = set_yaml_field(text, "acceptance_revoked_at", now)
    text = set_yaml_field(text, "acceptance_revoked_reason", json.dumps(reason, ensure_ascii=False))
    if "## 撤销记录" not in text:
        text = text.rstrip() + "\n\n## 撤销记录\n\n- revision %s → %s：%s\n" % (old_rev, rev, reason)
    atomic_write_text(path, text)
    tombstone_facts(instance_root, job_id, old_rev)
    restore_menu_row(instance_root, shape, str(target.get("id")), job_id)
    log_path = instance_root / "LOG.md"
    if log_path.exists():
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(
                "| %s | %s | %s | acceptance_revoked；attempt %s |\n"
                % (datetime.now().date().isoformat(), shape, job_id, attempt)
            )
    return {"ok": True, "job_id": job_id, "attempt": attempt, "closure_revision": rev}


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", required=True)
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--irreversible", default="unknown", choices=["unknown", "yes", "no"])
    parser.add_argument("--contract-holds", default="unknown", choices=["unknown", "yes", "no"])
    args = parser.parse_args(argv)
    print(
        json.dumps(
            reopen(
                Path(args.instance),
                args.job_id,
                args.reason,
                args.irreversible,
                args.contract_holds,
            ),
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
