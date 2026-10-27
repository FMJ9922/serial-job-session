#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""任务卡时间账本：区间权威，合计复算。"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from calendar_hours import business_wait_hours, parse_iso

INTERVAL_RE = re.compile(
    r"^\s*[-*]\s*(?P<label>[^:：]+)[:：]\s*"
    r"(?P<start>\d{4}-\d{2}-\d{2}T[0-9:+\-Z.]+)\s*[~/～至到]\s*"
    r"(?P<end>\d{4}-\d{2}-\d{2}T[0-9:+\-Z.]+)",
    re.M,
)


def parse_hours(value) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().lower().replace("小时", "h")
    if text.endswith("h"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def parse_intervals(markdown: str) -> List[Dict[str, str]]:
    found: List[Dict[str, str]] = []
    for match in INTERVAL_RE.finditer(markdown or ""):
        found.append(
            {
                "label": match.group("label").strip().lower(),
                "start": match.group("start").strip(),
                "end": match.group("end").strip(),
            }
        )
    return found


def classify_interval(label: str) -> str:
    text = label.lower()
    if "wait" in text or "等待" in text or "验收" in text:
        return "wait"
    if "rework" in text or "返工" in text or "attempt 2" in text or "attempt2" in text:
        return "rework"
    if "first" in text or "首次" in text or "attempt 1" in text or "attempt1" in text:
        return "first"
    return "ai"


def wall_hours(start: str, end: str) -> Optional[float]:
    a = parse_iso(start)
    b = parse_iso(end)
    if a is None or b is None or b <= a:
        return None
    return round((b - a).total_seconds() / 3600.0, 4)


def diagnose_intervals(intervals: List[Dict[str, str]]) -> List[str]:
    errors: List[str] = []
    parsed: List[Tuple] = []
    for item in intervals:
        a = parse_iso(item["start"])
        b = parse_iso(item["end"])
        if a is None or b is None:
            errors.append("INTERVAL_UNPARSED")
            continue
        if b <= a:
            errors.append("INTERVAL_REVERSED")
            continue
        parsed.append((a, b, item))
    parsed.sort(key=lambda x: x[0])
    for i in range(1, len(parsed)):
        if parsed[i][0] < parsed[i - 1][1]:
            errors.append("INTERVAL_OVERLAP")
            break
    return errors


def compute_ledger(front: dict, markdown: str = "") -> Dict[str, Optional[float]]:
    intervals = parse_intervals(markdown)
    first = parse_hours(front.get("ai_first_pass"))
    rework = parse_hours(front.get("ai_rework"))
    wait = parse_hours(front.get("business_wait"))
    effort = parse_hours(front.get("ai_effort"))
    wall = parse_hours(front.get("wall_elapsed") or front.get("elapsed"))

    if intervals:
        first_acc = 0.0
        rework_acc = 0.0
        wait_acc = 0.0
        wall_acc = 0.0
        for item in intervals:
            kind = classify_interval(item["label"])
            hours = wall_hours(item["start"], item["end"]) or 0.0
            wall_acc += hours
            if kind == "wait":
                wait_acc += business_wait_hours(item["start"], item["end"]) or 0.0
            elif kind == "rework":
                rework_acc += hours
            else:
                first_acc += hours
        if first is None:
            first = round(first_acc, 4)
        if rework is None:
            rework = round(rework_acc, 4)
        if wait is None:
            wait = round(wait_acc, 4)
        if wall is None:
            wall = round(wall_acc, 4)

        recon = round(first_acc + rework_acc, 4)
        yaml_net = None
        if front.get("ai_net") not in (None, "untracked"):
            yaml_net = parse_hours(front.get("ai_net"))
        extra_errors = []
        if yaml_net is not None and abs(yaml_net - recon) > 0.02:
            extra_errors.append("LEDGER_SUM_MISMATCH")
    else:
        extra_errors = []

    ai_net = None
    if first is not None or rework is not None:
        ai_net = round((first or 0.0) + (rework or 0.0), 4)
    if effort is None:
        effort = ai_net
    delivery = None
    if ai_net is not None or wait is not None:
        delivery = round((ai_net or 0.0) + (wait or 0.0), 4)
    estimate = parse_hours(front.get("estimate_total") or front.get("estimate"))
    ai_var = None
    delivery_var = None
    if estimate and ai_net is not None and estimate > 0:
        ai_var = round(ai_net / estimate, 4)
    if estimate and delivery is not None and estimate > 0:
        delivery_var = round(delivery / estimate, 4)
    return {
        "ai_first_pass": first,
        "ai_rework": rework,
        "ai_net": ai_net,
        "ai_effort": effort,
        "business_wait": wait,
        "delivery_workhours": delivery,
        "wall_elapsed": wall,
        "estimate_total": estimate,
        "ai_variance": ai_var,
        "delivery_variance": delivery_var,
        "interval_errors": diagnose_intervals(intervals) + extra_errors,
    }
