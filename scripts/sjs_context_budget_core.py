"""Host-independent context allocation policy.

This module intentionally has no filesystem, SQLite, DSH, or serial-job-session
storage dependency.  A future standalone plugin can copy or import this module
and provide its own adapter for observed context data.
"""
from __future__ import annotations

import math
import statistics
from typing import Any, Iterable, Mapping


DEFAULT_EFFECTIVE_PERCENT = 95
DEFAULT_RESERVE_TOKENS = 16_384
DEFAULT_TASK_OUTPUT_TOKENS = 8_192
DEFAULT_TASK_TOOL_TOKENS = 0
DEFAULT_TARGET_UTILIZATION_PERCENT = 75
DEFAULT_ACCEPTABLE_MIN_PERCENT = 50
DEFAULT_ACCEPTABLE_MAX_PERCENT = 100


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def classify_task(projected_tokens: int, available_tokens: int) -> str:
    if available_tokens <= 0 or projected_tokens > available_tokens:
        return "over_limit"
    ratio = projected_tokens / available_tokens
    if ratio <= 0.25:
        return "light"
    if ratio <= 0.50:
        return "moderate"
    if ratio <= 0.80:
        return "heavy"
    return "near_limit"


def dispatch_recommendation(fit: str) -> str:
    return {
        "light": "dispatch",
        "moderate": "dispatch",
        "heavy": "dispatch_with_checkpoint",
        "near_limit": "split_before_dispatch",
        "over_limit": "split_required",
    }[fit]


def estimate_task_allocation(
    *,
    observed_history_tokens: int,
    context_limit_tokens: int,
    effective_percent: int = DEFAULT_EFFECTIVE_PERCENT,
    overhead_tokens: int = 0,
    reserve_tokens: int = DEFAULT_RESERVE_TOKENS,
    task_prompt_tokens: int = 0,
    task_output_tokens: int = DEFAULT_TASK_OUTPUT_TOKENS,
    task_tool_tokens: int = DEFAULT_TASK_TOOL_TOKENS,
    target_utilization_percent: int = DEFAULT_TARGET_UTILIZATION_PERCENT,
    acceptable_min_percent: int = DEFAULT_ACCEPTABLE_MIN_PERCENT,
    acceptable_max_percent: int = DEFAULT_ACCEPTABLE_MAX_PERCENT,
) -> dict[str, Any]:
    """Calculate one task allocation from already-estimated token counts."""

    values = {
        "observed_history_tokens": observed_history_tokens,
        "context_limit_tokens": context_limit_tokens,
        "effective_percent": effective_percent,
        "overhead_tokens": overhead_tokens,
        "reserve_tokens": reserve_tokens,
        "task_prompt_tokens": task_prompt_tokens,
        "task_output_tokens": task_output_tokens,
        "task_tool_tokens": task_tool_tokens,
        "target_utilization_percent": target_utilization_percent,
        "acceptable_min_percent": acceptable_min_percent,
        "acceptable_max_percent": acceptable_max_percent,
    }
    for name, value in values.items():
        _require(isinstance(value, int) and value >= 0, f"invalid non-negative integer: {name}")
    _require(0 < effective_percent <= 100, "effective_percent must be between 1 and 100")
    _require(0 < target_utilization_percent <= 100, "target_utilization_percent must be between 1 and 100")
    _require(0 < acceptable_min_percent <= acceptable_max_percent <= 100,
             "acceptable utilization band is invalid")
    usable_limit = math.floor(context_limit_tokens * effective_percent / 100)
    remaining_before_task = max(0, usable_limit - observed_history_tokens - overhead_tokens)
    available_tokens = max(0, remaining_before_task - reserve_tokens)
    business_tokens = task_prompt_tokens + task_output_tokens + task_tool_tokens
    projected_tokens = business_tokens + reserve_tokens
    utilization = business_tokens / available_tokens * 100 if available_tokens else 0
    target_tokens = math.floor(available_tokens * target_utilization_percent / 100)
    if projected_tokens > available_tokens or utilization > acceptable_max_percent:
        allocation_band = "over_limit"
        allocation_recommendation = "split_required"
    elif utilization < acceptable_min_percent:
        allocation_band = "underfilled"
        allocation_recommendation = "review_scope"
    else:
        allocation_band = "acceptable"
        allocation_recommendation = (
            "dispatch_with_checkpoint" if utilization > target_utilization_percent else "dispatch"
        )
    return {
        "configured_limit_tokens": context_limit_tokens,
        "effective_percent": effective_percent,
        "usable_limit_tokens": usable_limit,
        "observed_history_tokens": observed_history_tokens,
        "overhead_tokens": overhead_tokens,
        "reserve_tokens": reserve_tokens,
        "remaining_before_task_tokens": remaining_before_task,
        "remaining_before_task_percent": round(remaining_before_task / usable_limit * 100, 2) if usable_limit else 0,
        "available_tokens": available_tokens,
        "target_tokens": target_tokens,
        "target_utilization_percent": target_utilization_percent,
        "acceptable_min_percent": acceptable_min_percent,
        "acceptable_max_percent": acceptable_max_percent,
        "task_prompt_tokens": task_prompt_tokens,
        "task_output_tokens": task_output_tokens,
        "task_tool_tokens": task_tool_tokens,
        "business_tokens": business_tokens,
        "projected_tokens": projected_tokens,
        "task_utilization_percent": round(utilization, 2),
        "allocation_band": allocation_band,
        "allocation_recommendation": allocation_recommendation,
        "remaining_after_task_tokens": max(0, remaining_before_task - projected_tokens),
    }


def evaluate_recent_utilization(
    samples: Iterable[Mapping[str, Any]],
    *,
    group: str | None = None,
    window: int = 3,
    low_percent: int = DEFAULT_ACCEPTABLE_MIN_PERCENT,
) -> dict[str, Any]:
    """Evaluate recent accepted comparable tasks for card adjustment."""

    _require(window > 0, "window must be positive")
    _require(0 <= low_percent <= 100, "low_percent must be between 0 and 100")
    comparable = []
    for sample in samples:
        if not isinstance(sample, Mapping) or sample.get("status") != "accepted":
            continue
        if group is not None and sample.get("group", "default") != group:
            continue
        if sample.get("intentional_small") is True:
            continue
        value = sample.get("utilization_percent", sample.get("task_utilization_percent"))
        if isinstance(value, (int, float)) and value >= 0:
            comparable.append({
                "task_id": sample.get("task_id"),
                "utilization_percent": float(value),
                "group": sample.get("group", "default"),
            })
    recent = comparable[-window:]
    values = [row["utilization_percent"] for row in recent]
    median = statistics.median(values) if values else None
    triggered = len(recent) == window and median is not None and median < low_percent
    return {
        "group": group,
        "window": window,
        "sample_count": len(recent),
        "samples": recent,
        "median_utilization_percent": round(median, 2) if median is not None else None,
        "low_threshold_percent": low_percent,
        "adjustment_triggered": triggered,
        "recommendation": "review_next_cards" if triggered else "keep_card_size",
    }

