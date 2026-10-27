"""Opt-in estimates, trustworthy timing and append-only shared calibration."""
from __future__ import annotations

import math
import statistics
from pathlib import Path

from sjs_config import ROOT, digest, number, require


def normalize_sample(raw):
    require(isinstance(raw, dict) and raw.get("id"), "TIMING_SAMPLE_ID_REQUIRED")
    source = raw.get("source", "untracked")
    if source == "host":
        require(raw.get("thread_id") and raw.get("turn_id"), "TIMING_CONTEXT_REQUIRED")
        import sjs_host_time as host
        if "records" in raw:
            records = raw["records"]
        else:
            # Only an explicit host collection request reaches local session
            # files. Disabled modules never import this adapter.
            require(raw.get("collect_host") is True, "HOST_TIMING_RECORDS_REQUIRED")
            records = host.collect_time_records(thread_id=raw["thread_id"], turn_id=raw["turn_id"],
                       rollout_path=raw.get("rollout_path"), codex_home=raw.get("codex_home"),
                       history_db=raw.get("history_db"), state_db=raw.get("state_db"))["records"]
        resolved = host.resolve_timing(records, thread_id=raw["thread_id"], turn_id=raw["turn_id"],
                    host_received_at=raw.get("host_received_at"), host_started_at=raw.get("host_started_at"))
        keys = ("thread_id", "turn_id", "started_at", "completed_at", "active_work_ms", "wall_response_ms",
                "business_wait_ms", "complete", "count_for_calibration", "underbound", "start_source")
        sample = {k: resolved.get(k) for k in keys}
        sample.update(id=raw["id"], source="host", evidence=raw.get("evidence", []))
        work, wait = [], []
        for record in resolved["evidence_records"]:
            interval = host._interval_from_record(record)
            if interval:
                (wait if host._record_kind(record) in {"human_wait", "business_wait", "waiting"} else work).append(interval)
        if not work and resolved.get("started_at_ms") is not None and resolved.get("completed_at_ms") is not None:
            work = [(resolved["started_at_ms"], resolved["completed_at_ms"])]
        sample["active_intervals"] = host.subtract_intervals(work, wait)
        sample["wait_intervals"] = host.intersect_intervals(wait, work)
        if not sample["complete"]:
            sample.update(source="untracked", reason="No trusted same-thread/turn completion")
        return sample
    if source == "manual":
        require(raw.get("evidence"), "MANUAL_TIMING_EVIDENCE_REQUIRED")
        import sjs_host_time as host
        intervals = []
        for interval in raw.get("active_intervals", []):
            a, b = map(host.timestamp_ms, interval)
            require(a is not None and b is not None and a <= b, "INVALID_TIMING_INTERVAL")
            intervals.append((a, b))
        if intervals:
            union = host.union_intervals(intervals)
            waiting = []
            for interval in raw.get("wait_intervals", []):
                a, b = map(host.timestamp_ms, interval)
                require(a is not None and b is not None and a <= b, "INVALID_TIMING_INTERVAL")
                waiting.append((a, b))
            active = host.subtract_intervals(union, waiting)
            wait = host.intersect_intervals(waiting, union)
            return {"id": raw["id"], "source": "manual", "evidence": raw["evidence"], "complete": True,
                    "active_work_ms": sum(b - a for a, b in active), "business_wait_ms": sum(b - a for a, b in wait),
                    "wall_response_ms": max(b for a, b in union) - min(a for a, b in union),
                    "count_for_calibration": True, "active_intervals": active, "wait_intervals": wait}
    return {"id": raw["id"], "source": "untracked", "complete": False, "count_for_calibration": False,
            "active_work_ms": None, "reason": raw.get("reason", "No trustworthy active-work intervals")}


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * q
    lo, hi = math.floor(position), math.ceil(position)
    return values[lo] + (values[hi] - values[lo]) * (position - lo)


def task_measurement(task):
    samples = list(task.get("timing", {}).values())
    complete = [s for s in samples if s.get("complete") and s.get("count_for_calibration") and number(s.get("active_work_ms"))]
    tracked = bool(samples) and len(complete) == len(samples)
    active_ms, wait_ms = 0, 0
    if tracked:
        import sjs_host_time as host
        for thread in {s.get("thread_id", "manual") for s in complete}:
            group = [s for s in complete if s.get("thread_id", "manual") == thread]
            active_ms += sum(b - a for a, b in host.union_intervals([i for s in group for i in s.get("active_intervals", [])]))
            wait_ms += sum(b - a for a, b in host.union_intervals([i for s in group for i in s.get("wait_intervals", [])]))
    hours = active_ms / 3600000 if tracked else None
    estimate = task.get("initial_estimate", task.get("estimate", {}))
    return {"task_id": task["id"], "status": task["status"], "group": estimate.get("group", "default"),
            "parent_id": task.get("parent_id"), "revision": task["revision"],
            "estimate_hours": estimate.get("hours"), "ai_net_hours": hours,
            "forecast_hours": task.get("estimate", {}).get("hours"),
            "elapsed_hours": sum(s.get("wall_response_ms") or 0 for s in complete) / 3600000 if tracked else None,
            "business_wait_hours": wait_ms / 3600000 if tracked else None,
            "variance": (hours / estimate["hours"] - 1) if hours is not None and estimate.get("hours", 0) > 0 else None,
            "timing": "tracked" if tracked else "untracked", "sample_count": len(samples),
            "eligible": bool(tracked and task["status"] == "accepted")}


def _read_library(path):
    import json
    events, latest = [], {}
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                require(line.endswith("\n"), "LIBRARY_TORN_TAIL")
                event = json.loads(line)
                require(event.get("schema") == "sjs-estimate/v5", "UNSUPPORTED_ESTIMATE_LIBRARY")
                events.append(event)
                latest[event["key"]] = event
    return events, latest


def stats(instance, state, *, append_library=False, dry_run=False):
    from sjs_store import file_lock, _append
    cfg = state["config"]
    require(cfg["modules"]["estimates"], "ESTIMATES_DISABLED")
    path = (Path(cfg["workspace"]) / cfg["estimates"]["library"]).resolve()
    require(path.suffix == ".jsonl" and not path.is_relative_to(ROOT.resolve()) and not path.is_relative_to(Path(instance).resolve())
            and ".git" not in path.parts, "INVALID_LIBRARY_PATH")
    measurements = [task_measurement(t) for t in state["tasks"].values()]
    def report(write):
        events, latest = _read_library(path)
        appended = 0
        for row in measurements:
            if not row["eligible"]:
                continue
            key = cfg["instance_id"] + "/" + row["task_id"]
            event = {"schema": "sjs-estimate/v5", "key": key, "instance_id": cfg["instance_id"], **row}
            event["sample_hash"] = digest(event)
            if latest.get(key, {}).get("sample_hash") != event["sample_hash"]:
                if write:
                    _append(path, event)
                if append_library:
                    appended += 1
                    latest[key] = event
        groups = {}
        for row in measurements:
            group = groups.setdefault(row["group"], {"count": 0, "eligible": [], "untracked": 0})
            group["count"] += 1
            group["untracked"] += row["timing"] == "untracked"
            if row["eligible"]:
                group["eligible"].append(row["ai_net_hours"])
        summary = {}
        for key, group in groups.items():
            values = group["eligible"]
            summary[key] = {"count": group["count"], "eligible": len(values), "untracked": group["untracked"],
                            "mean_hours": statistics.fmean(values) if values else None, "p70_hours": percentile(values, .7)}
        ratios = [r["ai_net_hours"] / r["estimate_hours"] for r in latest.values() if r.get("estimate_hours", 0) > 0]
        # Same log-space prior shrinkage as the original calibration algorithm.
        ratio = statistics.median(ratios) if ratios else 1
        factor = math.exp(len(ratios) / (len(ratios) + 4) * math.log(ratio)) if ratio > 0 else 1
        return {"ok": True, "measurements": measurements, "groups": summary, "library": str(path),
                "library_latest_count": len(latest), "library_appended": appended, "dry_run": dry_run,
                "calibration_factor": round(factor, 4), "note": "Per-task effort; do not sum nested parent and child wall intervals."}
    if append_library and not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(path.with_suffix(".lock")):
            return report(True)
    return report(False)
