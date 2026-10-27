#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""发布门槛：校验器扩容、真实回放、Agent eval 主题、时延预算。任一红不得切 3.0。"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import unittest
from pathlib import Path
from typing import Dict, List, Optional

import check_skill_integrity
import validate_domain_pack

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SKILL_ROOT = Path(__file__).resolve().parent.parent
TEST_ROOT = SKILL_ROOT / "Test" / "serial-job-session"
if not TEST_ROOT.is_dir():
    TEST_ROOT = SKILL_ROOT.parent / "Test" / "serial-job-session"
EVALS_PATH = TEST_ROOT / "evals.json"
BUDGETS = {
    "validator_p95_s": 1.5,
    "validator_1000_s": 5.0,
    "close_core_p95_ms": 200,
    "eval_token_p50_ratio": 1.25,
    "eval_duration_ratio": 1.5,
}

REQUIRED_EVAL_THEMES = {
    "multi_instance_ambiguous": ("歧义", "ROUTE_AMBIGUOUS", "两个实例", "多实例"),
    "bare_push_false_trigger": ("推进一下", "NO_ROUTE", "不得加载"),
    "discovery": ("discovery", "调研"),
    "dynamic_reorder": ("重排", "菜单改", "依赖漂移"),
    "post_close_no_autostart": ("不得自动开工", "不自动开工"),
    "managed_low": ("低风险", "托管执行 127"),
    "managed_high": ("高风险", "加粗"),
    "worker_lost": ("掉线", "worker_lost"),
    "embedded_workflow": ("embedded", "轻量"),
    "gate_red": ("门禁红", "禁止人眼覆盖"),
    "revoke_accepted": ("打回已结档", "rework_required"),
    "top_worker_self_close": ("自 Close", "顶层 Worker", "human_accept:none"),
}


def _protocol_major(domain: Path) -> int:
    """Select the validator that owns the instance's card protocol."""

    try:
        payload = json.loads((domain / "INSTANCE.json").read_text(encoding="utf-8"))
        raw = str(payload.get("protocol_version") or "")
        return int(raw.split(".", 1)[0]) if raw else 0
    except (OSError, ValueError, TypeError):
        return 0


def load_evals(path: Path = EVALS_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def eval_theme_gaps(data: Optional[dict] = None) -> List[str]:
    payload = data or load_evals()
    evals = payload.get("evals") or []
    blob = json.dumps(evals, ensure_ascii=False)
    missing = []
    for theme, needles in REQUIRED_EVAL_THEMES.items():
        if not any(str(needle) in blob for needle in needles):
            missing.append(theme)
    n = len(evals)
    if n < 12 or n > 16:
        missing.append("count_%d" % n)
    return missing


def load_diagnose():
    path = TEST_ROOT / "diagnose_replay.py"
    spec = importlib.util.spec_from_file_location("sjs_diagnose_replay", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def replay_fixtures() -> dict:
    module = load_diagnose()
    root = TEST_ROOT / "fixtures" / "snapshots"
    reports = []
    for snapshot in sorted(p for p in root.iterdir() if p.is_dir()):
        if (snapshot / "manifest.json").is_file():
            reports.append(module.replay_one(snapshot))
    return {
        "ok": all(item.get("ok") for item in reports) if reports else False,
        "reports": reports,
        "count": len(reports),
    }


def run_unittests() -> dict:
    loader = unittest.TestLoader()
    # Keep the established v3 suite while discovering v4 core/context tests as
    # they land; release quality must exercise the new transaction engine.
    suite = loader.discover(str(SKILL_ROOT / "scripts"), pattern="test*.py")
    result = unittest.TextTestRunner(verbosity=0, stream=sys.stderr).run(suite)
    return {
        "ok": result.wasSuccessful(),
        "tests": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
    }


def measure_validator(domain: Path, repeats: int = 7) -> dict:
    samples = []
    last = None
    protocol_major = _protocol_major(domain)
    if protocol_major == 4:
        # v4 cards are flat metadata + seven parent sections.  The v3
        # validator expects fenced YAML and its menu parser, so dispatch the
        # release check to the v4 snapshot checker instead of interpreting a
        # v4 template/ready card as a legacy card.
        import sjs_v4

        validator = lambda: sjs_v4.check_instance(domain, compare_projections=True)
    else:
        validator = lambda: validate_domain_pack.validate_domain(domain)
    for _ in range(repeats):
        start = time.perf_counter()
        last = validator()
        samples.append(time.perf_counter() - start)
    samples.sort()
    p95 = samples[max(0, int(round(0.95 * (len(samples) - 1))))]
    return {
        "ok": bool(last and last.get("ok") is not None),
        "domain_ok": bool(last.get("ok")) if last else False,
        "errors": (last or {}).get("errors") or [],
        "warnings": (last or {}).get("warnings") or [],
        "p95_s": round(p95, 4),
        "budget_ok": p95 <= BUDGETS["validator_p95_s"],
        "samples": [round(x, 4) for x in samples],
    }


def measure_close_core() -> dict:
    import os
    import tempfile
    import close_transaction

    samples = []
    for _ in range(5):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = root / "jobs"
            jobs.mkdir()
            (jobs / "c.md").write_text(
                "```yaml\nid: c\nshape: 1\nstatus: running\nattempt: 1\nclosure_revision: 0\nestimate: 1\nai_first_pass: 0.1\nai_rework: 0\nwall_elapsed: 0.1\n```\n",
                encoding="utf-8",
            )
            (root / "CURRENT.md").write_text(
                "```yaml\nstatus: running\nactive_job: c\njob_id: c\n```\n",
                encoding="utf-8",
            )
            (root / "LOG.md").write_text("# LOG\n", encoding="utf-8")
            os.environ.pop("SJS_FAULT_INJECT", None)
            close_transaction.prepare(root, "c", "accepted", "cmd", "control_owner")
            start = time.perf_counter()
            close_transaction.commit(root, "cmd")
            samples.append((time.perf_counter() - start) * 1000)
    samples.sort()
    p95 = samples[max(0, int(round(0.95 * (len(samples) - 1))))]
    return {
        "p95_ms": round(p95, 2),
        "budget_ok": p95 <= BUDGETS["close_core_p95_ms"],
        "samples_ms": [round(x, 2) for x in samples],
    }


def measure_linear_1000() -> dict:
    import tempfile

    start = time.perf_counter()
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        jobs = root / "jobs"
        jobs.mkdir()
        body = """# 卡

```yaml
id: job-%d
shape: %d
estimate_group: bench
depends_on: []
faces: [code]
estimate: 1
estimate_source: menu
actor: agent
human_accept: none
gate: false
negative_fixtures: []
expected_live_failures: []
ruler_ok: null
status: ready
attempt: 1
started_at: null
active_started_at: null
active_ended_at: null
ended_at: null
elapsed: null
ai_net: null
variance: null
```

## 目标
## 依赖门禁
## 下游接口
## 估分依据
## 验收方式
## 产物路径
## live
## 执行区间
## 拟写入 FACTS
"""
        for i in range(1, 201):
            (jobs / ("job-%d.md" % i)).write_text(body % (i, i), encoding="utf-8")
        elapsed_200 = None
        t0 = time.perf_counter()
        for path in jobs.glob("*.md"):
            validate_domain_pack.validate_job(path)
        elapsed_200 = time.perf_counter() - t0
    projected = elapsed_200 * 5
    return {
        "n": 200,
        "seconds_200": round(elapsed_200, 4),
        "projected_1000_s": round(projected, 4),
        "budget_ok": projected <= BUDGETS["validator_1000_s"],
        "wall_s": round(time.perf_counter() - start, 4),
    }


def release_report(domain: Path) -> dict:
    integrity = check_skill_integrity.check_manifest()
    replay = replay_fixtures()
    evals = {"ok": not eval_theme_gaps(), "gaps": eval_theme_gaps()}
    validator = measure_validator(domain)
    close_core = measure_close_core()
    linear = measure_linear_1000()
    blocking = []
    if not integrity.get("ok"):
        blocking.append("integrity")
    if not replay.get("ok"):
        blocking.append("replay")
    if not evals["ok"]:
        blocking.append("evals")
    if validator.get("errors"):
        blocking.append("validator")
    if not validator.get("budget_ok"):
        blocking.append("validator_budget")
    if not close_core.get("budget_ok"):
        blocking.append("close_budget")
    if not linear.get("budget_ok"):
        blocking.append("linear_budget")
    return {
        "ok": not blocking,
        "switch_3_0_allowed": not blocking,
        "blocking": blocking,
        "integrity": {"ok": integrity.get("ok"), "version": integrity.get("version")},
        "replay": {"ok": replay.get("ok"), "count": replay.get("count")},
        "evals": evals,
        "validator": validator,
        "close_core": close_core,
        "linear": linear,
        "budgets": BUDGETS,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="serial-job-session 发布门槛")
    parser.add_argument("--domain", required=True)
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = release_report(Path(args.domain).expanduser().resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.release and not report["ok"]:
        print("RELEASE_BLOCKED：" + ",".join(report["blocking"]), file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
