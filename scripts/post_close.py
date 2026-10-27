#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""post-close 投影重试 + 下一步推荐（不自动开工）。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import List, Optional

from estimate_stats import parse_card, parse_meta, parse_range_hours
from close_transaction import atomic_write_text, tx_dir, OUTBOX_ACTIONS, OPTIONAL_OUTBOX_ACTIONS
from menu_parse import parse_pending_table

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

BUSY = {"running", "done_pending", "rework_required", "accepted", "rejected"}
NAV_NOTE = "允许导航，禁止未经授权自动开工"


def _protocol_major(instance_root: Path) -> int:
    """Read the instance protocol without importing or mutating legacy state."""

    manifest = instance_root / "INSTANCE.json"
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 3
    raw = str(payload.get("protocol_version") or "")
    match = re.match(r"\s*(\d+)", raw)
    try:
        return int(match.group(1)) if match else 3
    except (TypeError, ValueError):
        return 3


def _v4_snapshot(instance_root: Path) -> dict:
    # Import lazily so the v3 entrypoint keeps its established dependencies and
    # parser behavior.  One snapshot is collected for each projection call.
    import sjs_v4

    return sjs_v4.load_snapshot(instance_root)


def _numeric_shape(card: dict) -> Optional[int]:
    try:
        return int(str(card.get("shape")).strip().split()[0])
    except (AttributeError, TypeError, ValueError):
        return None


def _midpoint_hours(raw) -> Optional[float]:
    """PLAN 的 baseline/forecast 可以是单值或区间（如 '49-81'），取中点做方差。"""
    if raw in (None, ""):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    low, high = parse_range_hours(str(raw))
    if low is None and high is None:
        return None
    if low is None:
        return high
    if high is None:
        return low
    return (low + high) / 2.0


def closed_shapes(instance_root: Path) -> set:
    if _protocol_major(instance_root) == 4:
        snapshot = _v4_snapshot(instance_root)
        import sjs_v4

        return {
            shape
            for card in snapshot.get("cards") or []
            if (shape := _numeric_shape(card)) is not None
            and sjs_v4.artifact_is_valid(card)
        }
    done = set()
    jobs = instance_root / "jobs"
    if not jobs.exists():
        return done
    for path in jobs.glob("*.md"):
        front = parse_card(path) or {}
        if str(front.get("status")) == "accepted":
            try:
                done.add(int(str(front.get("shape")).split()[0]))
            except (TypeError, ValueError):
                continue
    return done


def busy_shapes(instance_root: Path) -> set:
    if _protocol_major(instance_root) == 4:
        snapshot = _v4_snapshot(instance_root)
        return {
            shape
            for card in snapshot.get("cards") or []
            if (shape := _numeric_shape(card)) is not None
            and str(card.get("status") or "") in {"doing", "awaiting_acceptance"}
        }
    found = set()
    jobs = instance_root / "jobs"
    if not jobs.exists():
        return found
    for path in jobs.glob("*.md"):
        front = parse_card(path) or {}
        if str(front.get("status")) in BUSY:
            try:
                found.add(int(str(front.get("shape")).split()[0]))
            except (TypeError, ValueError):
                continue
    return found


def plan_forecast(instance_root: Path) -> dict:
    plan = instance_root / "PLAN.md"
    if not plan.is_file():
        return {}
    try:
        meta = parse_meta(plan)
        baseline = meta.get("baseline_total")
        forecast = meta.get("forecast_total")
        # remaining 优先用 PLAN 头的 forecast（区间原样保留）；解析失败则方差留空
        remaining = forecast
        b_mid = _midpoint_hours(baseline)
        f_mid = _midpoint_hours(forecast)
        return {
            "baseline_total": baseline,
            "forecast_total": forecast,
            "remaining_delivery_workhours": remaining,
            "scope_variance": None
            if b_mid is None or f_mid is None
            else round(f_mid - b_mid, 4),
        }
    except (TypeError, ValueError, OSError):
        return {
            "baseline_total": None,
            "forecast_total": None,
            "remaining_delivery_workhours": None,
            "scope_variance": None,
        }


def recommend(instance_root: Path, limit: int = 5) -> dict:
    if _protocol_major(instance_root) == 4:
        return _recommend_v4(instance_root, limit)
    shapes_path = instance_root / "JOB-SHAPES.md"
    text = shapes_path.read_text(encoding="utf-8") if shapes_path.exists() else ""
    pending_rows, _ = parse_pending_table(text)
    done = closed_shapes(instance_root)
    busy = busy_shapes(instance_root)
    legal = []
    for row in pending_rows:
        shape = row["shape"]
        if shape in busy:
            continue
        deps = row.get("depends_on") or []
        if all(dep in done for dep in deps):
            legal.append(
                {
                    "shape": shape,
                    "title": row.get("title") or "",
                    "depends_on": deps,
                    "risk": row.get("risk") or "",
                    "actor": row.get("actor") or "",
                    "human_accept": row.get("human_accept") or "",
                    "estimate": row.get("estimate") or "",
                }
            )
    if len(legal) <= 2:
        picks = legal
    else:
        picks = legal[: max(3, min(limit, len(legal)))]
        if limit < 5:
            picks = legal[:limit]
    preferred = picks[0]["shape"] if picks else None
    payload = {
        "phase": "current",
        "pending_count": len(pending_rows),
        "unblocked_count": len(legal),
        "completed_count": len(done),
        "blocked_count": max(0, len(pending_rows) - len(legal)),
        "candidates": picks,
        "preferred": preferred,
        "note": NAV_NOTE,
    }
    payload.update(plan_forecast(instance_root))
    return payload


def _recommend_v4(instance_root: Path, limit: int = 5) -> dict:
    """Recommend from authoritative cards; never parse/write a v3 menu."""

    import sjs_v4

    snapshot = _v4_snapshot(instance_root)
    cards = list(snapshot.get("cards") or [])
    done = closed_shapes(instance_root)
    busy = busy_shapes(instance_root)
    legal = []
    for card in cards:
        shape = _numeric_shape(card)
        if shape is None or shape in done or shape in busy:
            continue
        status = str(card.get("status") or "ready")
        if status not in {"ready", "cancelled"}:
            continue
        eligibility = sjs_v4.dependency_eligibility(card, snapshot)
        if not eligibility.get("ok"):
            continue
        meta = card.get("metadata") if isinstance(card.get("metadata"), dict) else card
        deps = meta.get("depends_on") or []
        if not isinstance(deps, list):
            deps = [deps]
        legal.append({
            "shape": shape,
            "id": card.get("id"),
            "title": sjs_v4._safe_goal(card),
            "depends_on": deps,
            "risk": meta.get("risk") or "",
            "actor": meta.get("actor") or "",
            "human_accept": meta.get("human_accept") or "",
            "estimate": meta.get("estimate_total", meta.get("estimate", "")),
        })
    legal.sort(key=lambda row: (row["shape"], str(row.get("id") or "")))
    picks = legal if len(legal) <= 2 else legal[: max(3, min(limit, len(legal)))]
    if limit < 5:
        picks = legal[:limit]
    payload = {
        "phase": "current",
        "pending_count": sum(1 for card in cards if str(card.get("status") or "ready") in {"ready", "doing", "awaiting_acceptance"}),
        "unblocked_count": len(legal),
        "completed_count": len(done),
        "blocked_count": max(0, len(cards) - len(done) - len(busy) - len(legal)),
        "candidates": picks,
        "preferred": picks[0]["shape"] if picks else None,
        "note": NAV_NOTE,
        "revision": snapshot.get("revision", 0),
    }
    return payload


def _run_projection(instance_root: Path, action: str, job_id: Optional[str] = None) -> Optional[str]:
    if _protocol_major(instance_root) == 4 and action in {"plan_rollup", "library_append"}:
        # v4 cards are authoritative and PLAN/JOB-SHAPES are generated views.
        # The v3 estimator's --plan/--library-append flags would mutate those
        # legacy sources and are intentionally rejected by estimate_stats.py.
        # Rebuild the v4 stats projection for both outbox actions instead.  A
        # future cross-instance library consumer can read card-backed events;
        # this action never writes the legacy estimate-library.jsonl.
        import estimate_stats

        try:
            rc = estimate_stats.main(["--instance", str(instance_root)])
        except Exception as exc:
            return str(exc)
        if rc not in (0, None):
            return "estimate_stats_v4_rc=%s" % rc
        return None
    jobs = instance_root / "jobs"
    shapes = instance_root / "JOB-SHAPES.md"
    plan = instance_root / "PLAN.md"
    if action in {"plan_rollup", "library_append"}:
        if not jobs.is_dir() or not shapes.is_file():
            return "missing jobs or JOB-SHAPES"
        import estimate_stats

        argv = ["--jobs", str(jobs), "--job-shapes", str(shapes)]
        if plan.is_file():
            argv += ["--plan", str(plan)]
        if action == "library_append":
            argv.append("--library-append")
        try:
            rc = estimate_stats.main(argv)
        except Exception as exc:
            return str(exc)
        if rc not in (0, None):
            return "estimate_stats_rc=%s" % rc
        return None
    if action == "progress_snapshot":
        snap = tx_dir(instance_root) / "progress_snapshot.json"
        current = instance_root / "CURRENT.md"
        payload = {
            "schema": "serial-job-session-progress/v1",
            "current_exists": current.is_file(),
        }
        atomic_write_text(snap, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        return None
    if action == "feishu_sync":
        import feishu_sync

        inst_path = instance_root / "INSTANCE.json"
        if not inst_path.is_file():
            return "missing INSTANCE.json"
        try:
            inst = json.loads(inst_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return str(exc)
        route_key = str(inst.get("route_key") or "")
        target_job = job_id
        try:
            result = feishu_sync.upsert_job(
                route_key,
                str(target_job or ""),
                instance_root=instance_root,
            )
        except feishu_sync.CliNotReady:
            return "CLI_NOT_READY"
        except Exception as exc:
            return str(exc)
        if result.get("skipped"):
            return None
        if result.get("ok") is False:
            return result.get("error") or "feishu_sync_failed"
        return None
    return "unknown action"


def drain_outbox(instance_root: Path) -> dict:
    path = tx_dir(instance_root) / "outbox.json"
    if not path.exists():
        return {"pending": [], "done": [], "errors": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    pending = list(data.get("pending") or [])
    done = list(data.get("done") or [])
    errors = dict(data.get("errors") or {})
    known = set(OUTBOX_ACTIONS) | set(OPTIONAL_OUTBOX_ACTIONS)
    still = []
    for action in pending:
        if action not in known:
            errors[action] = "unknown_outbox_action"
            still.append(action)
            continue
        err = _run_projection(instance_root, action, job_id=str(data.get("job_id") or "") or None)
        if err:
            errors[action] = err
            still.append(action)
        else:
            done.append(action)
            errors.pop(action, None)
    data["pending"] = still
    data["done"] = done
    data["errors"] = errors
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return data


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", required=True)
    parser.add_argument("--recommend-only", action="store_true")
    args = parser.parse_args(argv)
    root = Path(args.instance).resolve()
    payload = {
        "phase": "current",
        "candidates": [],
        "preferred": None,
        "note": NAV_NOTE,
    }
    try:
        payload = recommend(root)
    except Exception as exc:
        payload["recommend_error"] = str(exc)
    if not args.recommend_only:
        try:
            payload["outbox"] = drain_outbox(root)
        except Exception as exc:
            payload["outbox"] = {
                "pending": [],
                "done": [],
                "errors": {"drain": str(exc)},
            }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
