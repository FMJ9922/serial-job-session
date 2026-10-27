#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""估分在线校准：log 空间向全局先验收缩，目标分位数 P70/P80。"""

from __future__ import annotations

import math
from typing import Iterable, List, Optional

from estimate_stats import percentile


def shrink_factor(sample_ratio: float, n: int, prior_strength: float = 4.0) -> float:
    if n <= 0 or sample_ratio <= 0:
        return 1.0
    log_ratio = math.log(sample_ratio)
    weight = n / (n + prior_strength)
    return math.exp(weight * log_ratio)


def group_speed(actuals: Iterable[float], forecasts: Iterable[float]) -> Optional[float]:
    pairs = [(a, f) for a, f in zip(actuals, forecasts) if a and f and f > 0]
    if not pairs:
        return None
    ratios = [a / f for a, f in pairs]
    return percentile(ratios, 0.5)


def calibrate_estimate(
    prior_hours: float,
    group_actuals: List[float],
    group_forecasts: List[float],
    global_actuals: List[float],
    global_forecasts: List[float],
    quantile: float = 0.7,
    prior_strength: float = 4.0,
    speed_prior: float = 1.0,
) -> dict:
    group_ratio = group_speed(group_actuals, group_forecasts)
    global_ratio = group_speed(global_actuals, global_forecasts) or speed_prior
    n = len([1 for a, f in zip(group_actuals, group_forecasts) if a and f])
    sample_ratio = group_ratio or global_ratio or speed_prior
    factor = shrink_factor(sample_ratio, n, prior_strength)
    extra = 1.0
    if group_actuals:
        p = percentile(group_actuals, quantile)
        med = percentile(group_actuals, 0.5)
        if p and med and med > 0:
            extra = max(1.0, p / med)
    calibrated = round(prior_hours * factor * extra, 4)
    return {
        "prior_hours": prior_hours,
        "n": n,
        "group_ratio": group_ratio,
        "global_ratio": global_ratio,
        "shrink_factor": round(factor, 4),
        "quantile": quantile,
        "speed_prior": speed_prior,
        "calibrated_hours": calibrated,
    }
