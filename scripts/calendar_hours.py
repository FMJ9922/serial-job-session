#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""北京工作窗口与 business_wait / orchestration_wait 计算。"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from typing import Iterable, Optional, Tuple

TZ_CN = timezone(timedelta(hours=8))
DEFAULT_WINDOWS: Tuple[Tuple[time, time], ...] = (
    (time(9, 30), time(12, 30)),
    (time(14, 0), time(19, 0)),
)


def parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ_CN)
    return dt.astimezone(TZ_CN)


def _clip_window(start: datetime, end: datetime, win_start: time, win_end: time) -> timedelta:
    day = start.date()
    window_from = datetime.combine(day, win_start, tzinfo=TZ_CN)
    window_to = datetime.combine(day, win_end, tzinfo=TZ_CN)
    lo = max(start, window_from)
    hi = min(end, window_to)
    if hi <= lo:
        return timedelta(0)
    return hi - lo


def work_seconds(
    start: datetime,
    end: datetime,
    windows: Iterable[Tuple[time, time]] = DEFAULT_WINDOWS,
) -> float:
    """两时刻之间与工作窗口的交集秒数。夜间与午休不计。"""
    if end <= start:
        return 0.0
    start = start.astimezone(TZ_CN)
    end = end.astimezone(TZ_CN)
    total = 0.0
    day = start.date()
    last = end.date()
    wins = tuple(windows)
    while day <= last:
        day_start = datetime.combine(day, time.min, tzinfo=TZ_CN)
        day_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=TZ_CN)
        slice_start = max(start, day_start)
        slice_end = min(end, day_end)
        for win_start, win_end in wins:
            total += _clip_window(slice_start, slice_end, win_start, win_end).total_seconds()
        day += timedelta(days=1)
    return total


def hours_from_seconds(seconds: float) -> float:
    return round(seconds / 3600.0, 4)


def business_wait_hours(idle_start: Optional[str], idle_end: Optional[str]) -> Optional[float]:
    a = parse_iso(idle_start)
    b = parse_iso(idle_end)
    if a is None or b is None:
        return None
    return hours_from_seconds(work_seconds(a, b))


def orchestration_wait_hours(prev_closed_at: Optional[str], next_started_at: Optional[str]) -> Optional[float]:
    """上一卡结案到下一卡启动的工作窗口等待；与 business_wait 同一套窗口，不归罪任务类型。"""
    return business_wait_hours(prev_closed_at, next_started_at)
