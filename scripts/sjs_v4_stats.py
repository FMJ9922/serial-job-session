#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure v4 timing statistics and estimate-library projections.

The v4 core owns transactions and persistence.  This module accepts already
read card/timing records and returns deterministic projections for display or
for the core to persist.  It keeps legacy card elapsed values separate from
host verified v4 intervals and never fabricates reasoning/read subcategories.
"""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple


STATS_SCHEMA = "serial-job-session-stats/v4"
TIMING_BASIS_V4 = "v4_host_timing"
TIMING_BASIS_LEGACY = "legacy_card_elapsed"
EVENT_TYPES = {"closed", "revoked", "correction"}


def _value(obj: Any, *names: str, default: Any = None) -> Any:
    if obj is None:
        return default
    for name in names:
        if isinstance(obj, Mapping):
            value = obj.get(name)
        else:
            value = getattr(obj, name, None)
        if value is not None:
            return value
    return default


def _safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_safe(v) for v in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    return str(value)


def _canonical(value: Any) -> str:
    return json.dumps(_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def timing_basis(sample: Mapping[str, Any]) -> str:
    """Classify a sample without mixing old elapsed fields with v4 evidence."""

    explicit = sample.get("timing_basis")
    if explicit:
        return str(explicit)
    if str(sample.get("timing_source") or "").casefold() in {"v4_host_timing", "host_timing", "serial-job-session-time/v4"}:
        return TIMING_BASIS_V4
    schema = str(sample.get("schema") or "")
    if schema.endswith("time/v4") or any(
        sample.get(key) is not None
        for key in ("active_work_ms", "wall_response_ms", "business_wait_ms", "start_source", "evidence_records")
    ):
        return TIMING_BASIS_V4
    if any(sample.get(key) is not None for key in ("elapsed", "ai_net", "ai_effort", "wall_elapsed", "delivery_workhours")):
        return TIMING_BASIS_LEGACY
    return str(sample.get("timing_basis") or "unknown")


def _sample_value(sample: Mapping[str, Any]) -> Optional[float]:
    basis = timing_basis(sample)
    if basis == TIMING_BASIS_V4:
        value = sample.get("active_work_ms")
        if value is None:
            value = sample.get("duration_ms")
        return float(value) / 3_600_000.0 if value is not None else None
    if sample.get("value_hours") is not None:
        try:
            return float(sample["value_hours"])
        except (TypeError, ValueError):
            pass
    for key in ("ai_net", "ai_effort", "elapsed", "wall_elapsed"):
        value = sample.get(key)
        if value is None or value == "untracked":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _timing_payload(sample: Mapping[str, Any]) -> dict:
    """Accept either a timing sample or an engine card containing one."""

    metadata = sample.get("metadata") if isinstance(sample.get("metadata"), Mapping) else None
    nested = metadata.get("timing_last") if metadata is not None else None
    if not isinstance(nested, Mapping):
        nested = sample.get("timing_last")
    if not isinstance(nested, Mapping):
        return dict(sample)
    result = dict(nested)
    result.setdefault("instance_id", sample.get("instance_id"))
    result.setdefault("job_id", sample.get("job_id", sample.get("card_id", sample.get("id", sample.get("shape")))))
    return result


def sample_record(sample: Mapping[str, Any], *, instance_id: Any = None, job_id: Any = None) -> dict:
    """Normalise a sample for stats while preserving source/confidence flags."""

    sample = _timing_payload(sample)
    basis = timing_basis(sample)
    complete = bool(sample.get("complete", basis == TIMING_BASIS_LEGACY))
    count_for_calibration = bool(
        sample.get("count_for_calibration", complete and basis == TIMING_BASIS_LEGACY)
    )
    value_hours = _sample_value(sample)
    if basis == TIMING_BASIS_V4 and sample.get("underbound"):
        count_for_calibration = False
    if value_hours is None or not complete:
        count_for_calibration = False
    result = {
        "schema": STATS_SCHEMA,
        "instance_id": instance_id if instance_id is not None else sample.get("instance_id"),
        "job_id": job_id if job_id is not None else sample.get("job_id", sample.get("card_id")),
        "timing_basis": basis,
        "value_hours": value_hours,
        "active_work_ms": sample.get("active_work_ms"),
        "wall_response_ms": sample.get("wall_response_ms"),
        "business_wait_ms": sample.get("business_wait_ms"),
        "start_source": sample.get("start_source"),
        "source_confidence": sample.get("start_confidence", sample.get("source_confidence")),
        "complete": complete,
        "underbound": bool(sample.get("underbound")),
        "count_for_calibration": count_for_calibration,
        "closure_revision": sample.get("closure_revision"),
        "patch_revision": sample.get("patch_revision"),
    }
    return result


def eligible_sample(sample: Mapping[str, Any]) -> bool:
    record = sample_record(sample)
    return bool(record["count_for_calibration"] and record["value_hours"] is not None)


def group_samples(
    samples: Iterable[Mapping[str, Any]],
    *,
    keys: Sequence[str] = ("job_id", "timing_basis"),
) -> Dict[Tuple[Any, ...], List[dict]]:
    """Group only calibration-eligible samples; legacy and v4 stay separate."""

    grouped: Dict[Tuple[Any, ...], List[dict]] = defaultdict(list)
    for sample in samples:
        record = sample_record(sample)
        if not eligible_sample(record):
            continue
        key = tuple(record.get(key) for key in keys)
        grouped[key].append(record)
    return dict(grouped)


def _quantile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summarize_samples(samples: Iterable[Mapping[str, Any]]) -> dict:
    """Compute a compact, source-aware summary with no fabricated subcategories."""

    records = [sample_record(sample) for sample in samples]
    eligible = [record for record in records if eligible_sample(record)]
    values = [float(record["value_hours"]) for record in eligible]
    return {
        "schema": STATS_SCHEMA,
        "count": len(records),
        "eligible_count": len(eligible),
        "ineligible_count": len(records) - len(eligible),
        "timing_basis": sorted({record["timing_basis"] for record in records}),
        "mean_hours": statistics.fmean(values) if values else None,
        "median_hours": statistics.median(values) if values else None,
        "p75_hours": _quantile(values, 0.75),
        "min_hours": min(values) if values else None,
        "max_hours": max(values) if values else None,
        "business_wait_hours": sum((record.get("business_wait_ms") or 0) for record in eligible) / 3_600_000.0,
        "underbound_excluded": sum(1 for record in records if record.get("underbound")),
        "samples": eligible,
    }


def grouped_statistics(samples: Iterable[Mapping[str, Any]]) -> dict:
    grouped = group_samples(samples)
    result = {}
    for key, values in sorted(grouped.items(), key=lambda item: tuple(str(x) for x in item[0])):
        result["|".join("" if value is None else str(value) for value in key)] = summarize_samples(values)
    return {"schema": STATS_SCHEMA, "groups": result}


def initial_estimate(
    estimate_hours: Any,
    *,
    estimate_source: Optional[str] = None,
    frozen_at: Optional[str] = None,
    card_id: Optional[str] = None,
) -> dict:
    """Represent the initial estimate as immutable input to future forecasts."""

    try:
        value = float(estimate_hours)
    except (TypeError, ValueError):
        value = None
    return {
        "estimate_hours": value,
        "estimate_source": estimate_source,
        "initial_frozen": True,
        "frozen_at": frozen_at,
        "card_id": card_id,
    }


def forecast_view(
    initial: Mapping[str, Any],
    *,
    forecast_hours: Any = None,
    forecast_revision: Any = None,
    samples: Iterable[Mapping[str, Any]] = (),
) -> dict:
    """Return a forecast projection while leaving the initial estimate frozen."""

    initial_hours = initial.get("estimate_hours")
    if forecast_hours is None:
        summary = summarize_samples(samples)
        forecast_hours = summary.get("median_hours")
    try:
        forecast_value = float(forecast_hours) if forecast_hours is not None else None
    except (TypeError, ValueError):
        forecast_value = None
    return {
        "schema": STATS_SCHEMA,
        "initial": dict(initial),
        "initial_estimate_hours": initial_hours,
        "initial_frozen": bool(initial.get("initial_frozen", True)),
        "forecast_hours": forecast_value,
        "forecast_revision": forecast_revision,
        "revised": forecast_value is not None and forecast_value != initial_hours,
    }


def _event_identity(event: Mapping[str, Any]) -> str:
    explicit = event.get("event_id")
    if explicit:
        return str(explicit)
    identity = {
        "instance_id": event.get("instance_id"),
        "job_id": event.get("job_id"),
        "closure_revision": event.get("closure_revision"),
        "patch_revision": event.get("patch_revision"),
        "event_type": event.get("event_type"),
    }
    return hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest()


def library_event(
    *,
    instance_id: Any,
    job_id: Any,
    event_type: str = "closed",
    closure_revision: Any = None,
    patch_revision: Any = None,
    timing: Optional[Mapping[str, Any]] = None,
    estimate: Optional[Mapping[str, Any]] = None,
    forecast_revision: Any = None,
    event_id: Optional[str] = None,
    recorded_at: Optional[str] = None,
    **extra: Any,
) -> dict:
    """Create an idempotent shared-library event without writing it."""

    kind = str(event_type)
    if kind not in EVENT_TYPES:
        raise ValueError("unsupported estimate-library event_type: %s" % kind)
    # Do not silently turn every event into revision 1.  None means the core
    # has not supplied a closure revision yet; a patch revision is independent.
    result = {
        "schema": "serial-job-session-estimate-event/v4",
        "instance_id": str(instance_id),
        "job_id": str(job_id),
        "event_type": kind,
        "closure_revision": closure_revision,
        "patch_revision": patch_revision,
        "timing_basis": timing_basis(timing or {}) if timing is not None else None,
        "timing": _safe(timing) if timing is not None else None,
        "estimate": _safe(estimate) if estimate is not None else None,
        "forecast_revision": forecast_revision,
        "recorded_at": recorded_at,
    }
    result.update({key: _safe(value) for key, value in extra.items() if value is not None})
    result["event_id"] = str(event_id) if event_id else _event_identity(result)
    return result


def deduplicate_events(events: Iterable[Mapping[str, Any]]) -> List[dict]:
    seen: Dict[str, dict] = {}
    order: List[str] = []
    for event in events:
        item = dict(_safe(event))
        event_id = str(item.get("event_id") or _event_identity(item))
        item["event_id"] = event_id
        if event_id not in seen:
            order.append(event_id)
        seen[event_id] = item
    return [seen[event_id] for event_id in order]


def project_library(events: Iterable[Mapping[str, Any]]) -> dict:
    """Project idempotent events into latest active/revoked records."""

    deduped = deduplicate_events(events)
    latest: Dict[Tuple[str, str], dict] = {}
    latest_rank: Dict[Tuple[str, str], tuple] = {}

    def revision_value(value: Any) -> Tuple[int, int, str]:
        if value is None or value == "":
            return (0, -1, "")
        try:
            return (1, int(value), "")
        except (TypeError, ValueError):
            return (1, -1, str(value))

    def event_rank(event: Mapping[str, Any], sequence: int) -> tuple:
        # Closure/patch revisions establish the primary version.  When the
        # core emits two event types at the same revision, recorded_at is the
        # next explicit ordering signal; otherwise input order is the stable
        # append order (so a later revoked/correction event wins).
        return (
            revision_value(event.get("closure_revision")),
            revision_value(event.get("patch_revision")),
            str(event.get("recorded_at") or ""),
            sequence,
        )

    for sequence, event in enumerate(deduped):
        key = (str(event.get("instance_id")), str(event.get("job_id")))
        previous = latest.get(key)
        if previous is None:
            latest[key] = event
            latest_rank[key] = event_rank(event, sequence)
            continue
        current_key = event_rank(event, sequence)
        if current_key >= latest_rank[key]:
            latest[key] = event
            latest_rank[key] = current_key
    return {
        "schema": STATS_SCHEMA,
        "events": deduped,
        "latest": list(latest.values()),
        "count": len(deduped),
    }


def estimate_library_record(**kwargs: Any) -> dict:
    return library_event(**kwargs)


def statistics_projection(samples: Iterable[Mapping[str, Any]]) -> dict:
    return grouped_statistics(samples)


__all__ = [
    "STATS_SCHEMA",
    "TIMING_BASIS_V4",
    "TIMING_BASIS_LEGACY",
    "timing_basis",
    "sample_record",
    "eligible_sample",
    "group_samples",
    "summarize_samples",
    "grouped_statistics",
    "statistics_projection",
    "initial_estimate",
    "forecast_view",
    "library_event",
    "estimate_library_record",
    "deduplicate_events",
    "project_library",
]
