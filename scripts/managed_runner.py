#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""托管执行：授权合同、风险分级、lease/资源锁、夜跑 preset。不直接改业务产物。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import calendar_hours
import schema_validate
from close_transaction import atomic_write_text, set_yaml_field

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SINGLE_WRITER_RESOURCES = {"houdini", "unity", "heu"}
CODE_FACES = {
    "python-scripts",
    "client-code",
    "protocol-md",
    "sop-mdc",
    "shader",
    "houdini-hda",
    "unity-csharp",
}
CONTROL_FILES = {
    "CURRENT.md",
    "JOB-SHAPES.md",
    "FACTS.md",
    "LOG.md",
    "NORMS.md",
    "PLAN.md",
}
STOP_REASONS = {
    "dependency_ambiguous",
    "split_required",
    "human_only",
    "probe_failed",
    "over_grant",
    "lease_conflict",
    "budget_exhausted",
    "consecutive_failures",
    "high_risk_unconfirmed",
    "drain",
    "worker_lost",
}
RISK_RANK = {"low": 0, "medium": 1, "high": 2}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def validate_grant(grant: dict) -> List[str]:
    return schema_validate.validate_named(grant, "managed-run.schema.json")


def validate_result(result: dict) -> List[str]:
    return schema_validate.validate_named(result, "worker-result.schema.json")


def has_hard_gates(job: dict) -> bool:
    if job.get("gate") is True:
        return True
    if job.get("negative_fixtures"):
        return True
    if job.get("gates"):
        return True
    return False


def classify_risk(
    job: dict,
    history_pass_rate: Optional[float] = None,
    sample_n: int = 0,
) -> str:
    faces = set(job.get("faces") or [])
    human = str(job.get("human_accept") or "none").lower()
    irreversible = bool(job.get("irreversible"))
    production = bool(job.get("production_write")) or "production" in str(
        job.get("paths") or ""
    ).lower()
    estimate = job.get("estimate")
    try:
        large = bool(job.get("large_scope")) or float(estimate or 0) > 4
    except (TypeError, ValueError):
        large = bool(job.get("large_scope"))
    weak_gate = not has_hard_gates(job)
    low_pass = (
        history_pass_rate is not None
        and sample_n >= 3
        and history_pass_rate < 0.6
    )
    high_bits = (
        irreversible
        or production
        or large
        or weak_gate
        or human not in {"none", "", "null"}
        or low_pass
    )
    if sample_n < 3:
        return "high" if high_bits else "medium"
    if high_bits:
        return "high"
    if faces & SINGLE_WRITER_RESOURCES or faces & CODE_FACES:
        return "medium"
    return "low"


def reviewer_required(risk: str, grant: Optional[dict] = None) -> bool:
    policy = (grant or {}).get("reviewer_policy") or {}
    wanted = policy.get(risk)
    if wanted == "skip":
        return False
    if wanted == "required":
        return True
    return risk != "low"


def acquire_resource_lock(held: List[str], requested: List[str]) -> Optional[str]:
    held_set = {str(item).lower() for item in held}
    for face in requested:
        name = str(face).lower()
        if name in SINGLE_WRITER_RESOURCES and name in held_set:
            return "lease_conflict"
    return None


def overnight_preset(grant: dict) -> dict:
    grant = dict(grant)
    grant["preset"] = "overnight"
    grant["count_human_wait_offhours"] = False
    grant.setdefault("max_parallel", 1)
    return grant


def begin_drain(grant: dict) -> dict:
    grant = dict(grant)
    grant["drain"] = True
    return grant


def human_wait_hours(grant: dict, idle_start: Optional[str], idle_end: Optional[str]) -> Optional[float]:
    """夜跑/默认托管都不把工作窗口外的等待计入 human wait。"""
    if grant.get("count_human_wait_offhours") is True and grant.get("preset") != "overnight":
        start = calendar_hours.parse_iso(idle_start)
        end = calendar_hours.parse_iso(idle_end)
        if start is None or end is None:
            return None
        return calendar_hours.hours_from_seconds((end - start).total_seconds())
    return calendar_hours.business_wait_hours(idle_start, idle_end)


def _allowlist_hit(allowed: Optional[list], value: Any) -> bool:
    if not allowed:
        return True
    if value is None:
        return False
    if isinstance(value, list):
        return all(item in allowed for item in value)
    return value in allowed


def _budget_stop(grant: dict, now: Optional[str] = None) -> Optional[str]:
    completed = int(grant.get("completed_count") or 0)
    max_jobs = int(grant.get("max_jobs") or 0)
    if max_jobs and completed >= max_jobs:
        return "budget_exhausted"
    tokens = int(grant.get("agent_tokens_used") or 0)
    budget = int(grant.get("agent_budget") or 0)
    if budget and tokens >= budget:
        return "budget_exhausted"
    deadline = grant.get("deadline")
    if deadline:
        current = calendar_hours.parse_iso(now or now_iso())
        due = calendar_hours.parse_iso(str(deadline))
        if current is not None and due is not None and current > due:
            return "budget_exhausted"
    retries = int(grant.get("max_retries") or 0)
    fails = int(grant.get("consecutive_fail_count") or 0)
    if retries and fails >= retries:
        return "consecutive_failures"
    return None


def dispatch_allowed(
    grant: dict,
    job: dict,
    held_faces: List[str],
    now: Optional[str] = None,
) -> dict:
    errors = validate_grant(grant) if grant.get("schema") else ["MISSING_FIELD:$.schema"]
    if errors and grant.get("schema"):
        named = [err for err in errors if err.startswith("MISSING_FIELD")]
        return {"ok": False, "stop": "over_grant", "risk": None, "errors": errors, "missing": named}

    if grant.get("drain"):
        return {"ok": False, "stop": "drain", "risk": None}

    budget = _budget_stop(grant, now)
    if budget:
        return {"ok": False, "stop": budget, "risk": None}

    if str(job.get("actor") or "agent") == "human":
        return {"ok": False, "stop": "human_only", "risk": None}
    human = str(job.get("human_accept") or "none").lower()
    if human not in {"none", "", "null"} and grant.get("human_accept_policy", "stop") == "stop":
        return {"ok": False, "stop": "human_only", "risk": None}
    if job.get("split_required") or job.get("status") == "split_required":
        return {"ok": False, "stop": "split_required", "risk": None}
    if job.get("probe_failed") or job.get("status") == "probe_failed":
        return {"ok": False, "stop": "probe_failed", "risk": None}
    if job.get("dependency_ambiguous"):
        return {"ok": False, "stop": "dependency_ambiguous", "risk": None}

    sample_n = int(grant.get("sample_n") or 0)
    risk = classify_risk(job, grant.get("history_pass_rate"), sample_n)
    ceiling = str(grant.get("risk_ceiling") or "high")
    if RISK_RANK.get(risk, 9) > RISK_RANK.get(ceiling, 9):
        return {"ok": False, "stop": "over_grant", "risk": risk}

    if risk == "high" and not grant.get("high_risk_confirmed"):
        return {"ok": False, "stop": "high_risk_unconfirmed", "risk": risk}
    if job.get("irreversible") and grant.get("irreversible_policy") == "stop":
        return {"ok": False, "stop": "over_grant", "risk": risk}

    allow = grant.get("allowlist") or {}
    if not _allowlist_hit(allow.get("shapes"), job.get("shape")):
        return {"ok": False, "stop": "over_grant", "risk": risk}
    if not _allowlist_hit(allow.get("phases"), job.get("phase") or job.get("phase_id")):
        return {"ok": False, "stop": "over_grant", "risk": risk}
    if not _allowlist_hit(allow.get("faces"), job.get("faces") or []):
        return {"ok": False, "stop": "over_grant", "risk": risk}
    if not _allowlist_hit(allow.get("paths"), job.get("paths") or []):
        return {"ok": False, "stop": "over_grant", "risk": risk}
    if not _allowlist_hit(allow.get("tools"), job.get("tools") or []):
        return {"ok": False, "stop": "over_grant", "risk": risk}

    faces = list(job.get("faces") or [])
    conflict = acquire_resource_lock(held_faces, faces)
    if conflict:
        return {"ok": False, "stop": conflict, "risk": risk}

    return {
        "ok": True,
        "risk": risk,
        "reviewer": reviewer_required(risk, grant),
        "worker_may_close": False,
        "orchestrator_closes": True,
        "bold_high_risk": risk == "high",
    }


def parse_current_field(text: str, key: str) -> Optional[str]:
    match = re.search(rf"^{re.escape(key)}\s*:\s*(.*)$", text, flags=re.M)
    if not match:
        return None
    value = match.group(1).strip()
    if value in {"null", "None", ""}:
        return None
    return value


def held_resources_from_current(text: str) -> List[str]:
    raw = parse_current_field(text, "lease_resources") or "[]"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = re.findall(r"[A-Za-z0-9_-]+", raw)
    return [str(item) for item in data]


def reserve(instance_root: Path, grant: dict, job: dict, now: Optional[str] = None) -> dict:
    current_path = Path(instance_root) / "CURRENT.md"
    text = current_path.read_text(encoding="utf-8") if current_path.is_file() else ""
    held = held_resources_from_current(text)
    decision = dispatch_allowed(grant, job, held, now=now)
    if not decision.get("ok"):
        return decision
    stamp = now or now_iso()
    resources = [f for f in (job.get("faces") or []) if str(f).lower() in SINGLE_WRITER_RESOURCES]
    text = set_yaml_field(text, "control_mode", "managed")
    text = set_yaml_field(text, "managed_run_id", str(grant.get("run_id") or ""))
    text = set_yaml_field(text, "lease_holder", str(job.get("id") or job.get("job_id") or ""))
    text = set_yaml_field(text, "lease_resources", json.dumps(resources, ensure_ascii=False))
    text = set_yaml_field(text, "lease_acquired_at", stamp)
    if "active_transaction:" not in text:
        text = set_yaml_field(text, "active_transaction", "null")
    atomic_write_text(current_path, text)
    decision["reserved"] = True
    decision["lease_holder"] = parse_current_field(text, "lease_holder")
    return decision


def release_lease(instance_root: Path) -> None:
    current_path = Path(instance_root) / "CURRENT.md"
    if not current_path.is_file():
        return
    text = current_path.read_text(encoding="utf-8")
    text = set_yaml_field(text, "lease_holder", "null")
    text = set_yaml_field(text, "lease_resources", "[]")
    text = set_yaml_field(text, "lease_acquired_at", "null")
    atomic_write_text(current_path, text)


def ingest_result(result: dict, grant: Optional[dict] = None) -> dict:
    if result.get("dropped") or str(result.get("status") or "") == "worker_lost":
        return {"ok": False, "stop": "worker_lost"}
    errors = validate_result(result)
    if errors:
        return {"ok": False, "stop": "over_grant", "errors": errors}
    if result.get("close_requested"):
        return {"ok": False, "stop": "over_grant", "reason": "WORKER_MUST_NOT_CLOSE"}
    control = list(result.get("control_writes") or [])
    for path in list(result.get("writes") or []) + control:
        name = Path(str(path)).name
        if name in CONTROL_FILES or str(path) in CONTROL_FILES:
            control.append(str(path))
    if control:
        return {"ok": False, "stop": "over_grant", "reason": "CONTROL_PLANE_WRITE"}
    status = str(result.get("status") or "")
    if status in STOP_REASONS or status in {"split_required", "probe_failed", "human_only", "dependency_ambiguous"}:
        if status != "done_pending":
            return {"ok": False, "stop": status, "reviewer": result.get("reviewer")}
    if result.get("reviewer") == "blocking":
        return {"ok": False, "stop": "consecutive_failures", "reviewer": "blocking"}
    return {
        "ok": True,
        "close_by": "orchestrator",
        "reviewer": result.get("reviewer") or "skipped",
        "worker_may_close": False,
    }


def next_in_queue(pending: List[dict], grant: dict, now: Optional[str] = None) -> dict:
    if grant.get("drain"):
        return {"ok": False, "stop": "drain"}
    budget = _budget_stop(grant, now)
    if budget:
        return {"ok": False, "stop": budget}
    if not pending:
        return {"ok": False, "stop": None, "empty": True}
    first = pending[0]
    return dispatch_allowed(grant, first, [], now=now)


def load_json(path: Optional[str]) -> dict:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="?", default="dispatch")
    parser.add_argument("--grant")
    parser.add_argument("--job")
    parser.add_argument("--result")
    parser.add_argument("--held-faces", default="")
    parser.add_argument("--instance")
    parser.add_argument("--now")
    args = parser.parse_args(argv)
    grant = load_json(args.grant)
    job = load_json(args.job)
    if args.command == "overnight":
        print(json.dumps(overnight_preset(grant), ensure_ascii=False, indent=2))
        return 0
    if args.command == "drain":
        print(json.dumps(begin_drain(grant), ensure_ascii=False, indent=2))
        return 0
    if args.command == "validate-grant":
        errors = validate_grant(grant)
        print(json.dumps({"ok": not errors, "errors": errors}, ensure_ascii=False, indent=2))
        return 0 if not errors else 2
    if args.command == "ingest":
        result = load_json(args.result)
        print(json.dumps(ingest_result(result, grant), ensure_ascii=False, indent=2))
        return 0
    if args.command == "reserve":
        if not args.instance:
            print("[error] reserve 需要 --instance", file=sys.stderr)
            return 2
        payload = reserve(Path(args.instance), grant, job, now=args.now)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("ok") else 2
    held = [p for p in args.held_faces.split(",") if p]
    print(json.dumps(dispatch_allowed(grant, job, held, now=args.now), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
