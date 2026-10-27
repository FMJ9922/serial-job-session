#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""串行任务会话 · 估分偏差统计。

扫领域包 jobs/*.md 的任务卡受限 YAML，按 estimate_group、faces 数量与面标签分组，算 ai_net 的
p50/p90 与偏差率分布；结果落 JSON，并回写 JOB-SHAPES.md 的
<!-- AUTO:ESTIMATE-STATS --> 块。

另外两件事（口径见同目录上一层的 ESTIMATE.md §七 / §八）：

- `--plan`：把实测滚进 PLAN.md 的 <!-- AUTO:PLAN-ROLLUP --> 块（阶段实测、剩余区间、墙钟完成度）。
- `--library-append` / `--library-stats`：跨实例估分库，让新工作流冷启动时有历史可参考。

需要 Python 3.8+，无第三方依赖。

    python estimate_stats.py --jobs <领域包>/jobs --job-shapes <领域包>/JOB-SHAPES.md
    python estimate_stats.py --jobs ... --job-shapes ... --dry-run
    python estimate_stats.py --jobs ... --job-shapes ... --backfill --backfill-map mapping.json
    python estimate_stats.py --jobs ... --job-shapes ... --plan <领域包>/PLAN.md --library-append
    python estimate_stats.py --library-stats --library <库路径> --group ui-assembly

Windows 上默认的 `python` 可能是 2.x，用 `py -3` 或 `python3`。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

for _stream in (sys.stdout, sys.stderr):  # Windows 控制台默认非 UTF-8，中文会乱码
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

AUTO_BEGIN = "<!-- AUTO:ESTIMATE-STATS -->"
AUTO_END = "<!-- /AUTO:ESTIMATE-STATS -->"
PLAN_AUTO_BEGIN = "<!-- AUTO:PLAN-ROLLUP -->"
PLAN_AUTO_END = "<!-- /AUTO:PLAN-ROLLUP -->"

LIBRARY_SCHEMA = "serial-job-session-estimate-library/v2"
LIBRARY_SCHEMA_V1 = "serial-job-session-estimate-library/v1"
LIBRARY_FIELDS = (
    "estimate_group",
    "faces",
    "estimate",
    "estimate_source",
    "ai_net",
    "elapsed",
    "variance",
    "status",
    "closed_at",
    "accepted_at",
)

CLOSED = {"accepted", "rejected"}
PHASE_DONE = {"done", "dropped"}
SKILL_ROOT = Path(__file__).resolve().parent.parent
import instantiate_domain  # 仓库相对路径解析与 INSTANCE.json 同口径
# ---------------------------------------------------------------- 解析


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def parse_duration_hours(raw) -> Optional[float]:
    """'11m 01s' / '1h 23m' / '13h' / '0.5h' / '90m' / 裸数字(小时) → 小时。"""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().strip("\"'")
    if not text or text.lower() in {"null", "none", "~", "-"}:
        return None
    text = text.lstrip("≈~约")
    total = 0.0
    matched = False
    for value, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([hms])", text, flags=re.I):
        matched = True
        value = float(value)
        unit = unit.lower()
        total += value if unit == "h" else value / 60 if unit == "m" else value / 3600
    if matched:
        return total
    try:
        return float(text)
    except ValueError:
        return None


def parse_scalar(raw: str):
    text = raw.strip()
    if text.startswith("#"):
        return None
    text = re.sub(r"\s+#.*$", "", text).strip()
    if not text or text in {"null", "None", "~"}:
        return None
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [p.strip().strip("\"'") for p in inner.split(",") if p.strip()]
    if text in {"true", "false", "True", "False"}:
        return text.lower() == "true"
    return text.strip("\"'")


def parse_card(path: Path) -> Optional[dict]:
    """取任务卡里第一个 ```yaml 块，按 key: value 平铺解析。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"```ya?ml\s*\n(.*?)\n```", text, flags=re.S)
    if not match:
        return None
    data: dict = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")):  # 嵌套块（如 live:）不参与统计
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        data[key.strip()] = parse_scalar(value)
    return data


# ---------------------------------------------------------------- 统计


@dataclass
class Row:
    job_id: str
    shape: str
    estimate_group: str = "unknown"
    faces: List[str] = field(default_factory=list)
    estimate: Optional[float] = None
    estimate_source: str = "unknown"
    ai_net: Optional[float] = None
    elapsed: Optional[float] = None
    variance: Optional[float] = None
    status: str = ""
    ended_at: Optional[str] = None
    gate: Optional[bool] = None
    human_accept: str = ""
    risk: str = ""
    live_editor: bool = False
    untracked: bool = False

    @property
    def backfilled(self) -> bool:
        return self.estimate_source == "backfill"


def percentile(values: List[float], pct: float) -> Optional[float]:
    """线性插值分位数。样本只有 1 个时 p90 退化为该值，不假装有分布。"""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * pct
    low = int(pos)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)


def summarize(rows: List[Row]) -> dict:
    nets = [r.ai_net for r in rows if r.ai_net is not None]
    variances = [r.variance for r in rows if r.variance is not None]
    distribution_ready = len(nets) >= 5
    return {
        "n": len(rows),
        "n_with_net": len(nets),
        "ai_net_p50": round(percentile(nets, 0.5), 2) if nets else None,
        "ai_net_p90": round(percentile(nets, 0.9), 2) if distribution_ready else None,
        "ai_net_max": round(max(nets), 2) if nets else None,
        "variance_p50": round(percentile(variances, 0.5), 2) if variances else None,
        "variance_p90": round(percentile(variances, 0.9), 2) if len(variances) >= 5 else None,
        "distribution_ready": distribution_ready,
        "over_2x": sum(1 for v in variances if v > 2),
        "under_half": sum(1 for v in variances if v < 0.5),
        "jobs": [r.job_id for r in rows],
    }


def build_stats(rows: List[Row]) -> dict:
    complete = [r for r in rows if r.estimate is not None and r.ai_net is not None and not r.untracked]
    success = [r for r in complete if r.status == "accepted" and not r.backfilled]
    rejected = [r for r in complete if r.status == "rejected" and not r.backfilled]
    main = success
    back = [r for r in complete if r.backfilled]
    untracked = [r for r in rows if r.untracked]

    grouped_by_count: Dict[str, List[Row]] = {}
    for row in main:
        grouped_by_count.setdefault(str(len(row.faces)), []).append(row)

    grouped_by_face: Dict[str, List[Row]] = {}
    for row in main:
        for face in row.faces:
            grouped_by_face.setdefault(face, []).append(row)

    grouped_by_estimate_group: Dict[str, List[Row]] = {}
    for row in main:
        grouped_by_estimate_group.setdefault(row.estimate_group, []).append(row)

    extra_layers = {
        "by_gate": {},
        "by_human_accept": {},
        "by_risk": {},
        "by_live_editor": {},
    }
    for row in main:
        extra_layers["by_gate"].setdefault(str(bool(row.gate)), []).append(row)
        extra_layers["by_human_accept"].setdefault(row.human_accept or "none", []).append(row)
        extra_layers["by_risk"].setdefault(row.risk or "unset", []).append(row)
        extra_layers["by_live_editor"].setdefault(str(bool(row.live_editor)), []).append(row)

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "overall": summarize(main),
        "by_estimate_group": {
            k: summarize(v) for k, v in sorted(grouped_by_estimate_group.items())
        },
        "by_face_count": {k: summarize(v) for k, v in sorted(grouped_by_count.items())},
        "by_face": {k: summarize(v) for k, v in sorted(grouped_by_face.items())},
        "by_gate": {k: summarize(v) for k, v in sorted(extra_layers["by_gate"].items())},
        "by_human_accept": {k: summarize(v) for k, v in sorted(extra_layers["by_human_accept"].items())},
        "by_risk": {k: summarize(v) for k, v in sorted(extra_layers["by_risk"].items())},
        "by_live_editor": {k: summarize(v) for k, v in sorted(extra_layers["by_live_editor"].items())},
        "backfilled": summarize(back),
        "rejected": summarize(rejected),
        "untracked": summarize(untracked),
        "rows": [
            {
                "job_id": r.job_id,
                "shape": r.shape,
                "estimate_group": r.estimate_group,
                "faces": r.faces,
                "estimate": r.estimate,
                "estimate_source": r.estimate_source,
                "ai_net": r.ai_net,
                "elapsed": r.elapsed,
                "variance": r.variance,
                "status": r.status,
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------- PLAN 阶段


@dataclass
class Phase:
    name: str
    criterion: str = ""
    low: Optional[float] = None
    high: Optional[float] = None
    unc: str = ""
    basis: str = ""
    shapes: List[str] = field(default_factory=list)
    status: str = "pending"

    @property
    def closed(self) -> bool:
        return self.status in PHASE_DONE


def parse_meta(path: Path) -> dict:
    """PLAN.md 等控制文件的首个 yaml 块，与任务卡同一套受限 YAML 口径。"""
    return parse_card(path) or {}


def parse_range_hours(raw: str) -> Tuple[Optional[float], Optional[float]]:
    """'2–5' / '2-5' / '2~5' / '1.5' / '30m–2h' → (low, high)；写不出数就 (None, None)。"""
    text = str(raw or "").strip().strip("`").strip()
    if not text or text in {"—", "-", "–", "?", "TBD", "tbd"}:
        return None, None
    parts = [p for p in re.split(r"\s*[–—~]\s*|\s+-\s+|(?<=[hms\d])\s*-\s*(?=\d)", text) if p]
    values = [parse_duration_hours(part) for part in parts]
    values = [v for v in values if v is not None]
    if not values:
        return None, None
    if len(values) == 1:
        return values[0], values[0]
    return min(values), max(values)


def split_shapes(raw: str) -> List[str]:
    text = str(raw or "").strip()
    if not text or text in {"—", "-", "–"}:
        return []
    return [p.strip().strip("`") for p in re.split(r"[,，、/\s]+", text) if p.strip().strip("`") not in {"", "—", "-"}]


def parse_plan(path: Path) -> Tuple[dict, List[Phase], List[str]]:
    """读 PLAN.md：受限 YAML 头 + 「阶段」表。返回 (meta, phases, errors)。"""
    text = path.read_text(encoding="utf-8", errors="replace")
    meta = parse_meta(path)
    errors: List[str] = []

    section = re.search(r"^##[^\n]*阶段[^\n]*\n(.*?)(?=^##\s|\Z)", text, flags=re.M | re.S)
    if not section:
        return meta, [], ["PLAN.md 缺「## 阶段」小节"]

    phases: List[Phase] = []
    for line in section.group(1).splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells or all(re.fullmatch(r":?-{2,}:?", c or "") for c in cells):
            continue
        if cells[0] in {"阶段", "阶段名"}:
            continue
        if "{{" in stripped:  # 未填写的模板行
            continue
        if len(cells) < 7:
            errors.append("阶段行列数不足（需要 7 列）：%s" % stripped)
            continue
        low, high = parse_range_hours(cells[2])
        if high is not None and high > 24:
            errors.append("PHASE_OVER_24H：%s" % cells[0])
        phases.append(
            Phase(
                name=cells[0],
                criterion=cells[1],
                low=low,
                high=high,
                unc=cells[3].strip("`").lower(),
                basis=cells[4].strip("`"),
                shapes=split_shapes(cells[5]),
                status=cells[6].strip("`").lower() or "pending",
            )
        )
    return meta, phases, errors


def build_plan_rollup(meta: dict, phases: List[Phase], rows: List[Row]) -> dict:
    """按阶段「已拆卡编号」把已结档卡的实测归到阶段，算剩余量与墙钟完成度。"""
    closed = [r for r in rows if r.status in CLOSED]
    accepted = [r for r in closed if r.status == "accepted"]
    by_shape: Dict[str, List[Row]] = {}
    for row in accepted:
        by_shape.setdefault(row.shape, []).append(row)

    both = [r for r in accepted if r.ai_net is not None and r.elapsed is not None]
    net_sum = sum(r.ai_net for r in both)
    wall_sum = sum(r.elapsed for r in both)
    # 少于 3 张就不给系数：两张卡算出来的“膨胀率”只是噪音。
    inflation = round(wall_sum / net_sum, 2) if len(both) >= 3 and net_sum > 0 else None

    mapped: set = set()
    phase_reports: List[dict] = []
    remaining_low = remaining_high = 0.0
    range_known = True

    for phase in phases:
        cards = [row for shape in phase.shapes for row in by_shape.get(shape, [])]
        mapped.update(row.job_id for row in cards)
        nets = [r.ai_net for r in cards if r.ai_net is not None]
        walls = [r.elapsed for r in cards if r.elapsed is not None]
        net_actual = round(sum(nets), 2) if nets else None
        wall_actual = round(sum(walls), 2) if walls else None

        if phase.closed:
            rem_low = rem_high = 0.0
        elif phase.low is None or phase.high is None:
            rem_low = rem_high = None
            range_known = False
        else:
            done_net = sum(nets)
            rem_low = max(0.0, round(phase.low - done_net, 2))
            rem_high = max(0.0, round(phase.high - done_net, 2))
        if rem_low is not None:
            remaining_low += rem_low
            remaining_high += rem_high

        verdict = "—"
        if phase.status == "done" and net_actual is not None and phase.low is not None:
            if net_actual > phase.high:
                verdict = "超区间"
            elif net_actual < phase.low:
                verdict = "低于区间"
            else:
                verdict = "在区间内"
        elif phase.status == "dropped":
            verdict = "已作废"

        phase_reports.append(
            {
                "name": phase.name,
                "status": phase.status,
                "unc": phase.unc,
                "basis": phase.basis,
                "low": phase.low,
                "high": phase.high,
                "shapes": phase.shapes,
                "cards": len(cards),
                "net_actual": net_actual,
                "wall_actual": wall_actual,
                "remaining_low": rem_low,
                "remaining_high": rem_high,
                "verdict": verdict,
            }
        )

    orphans = sorted(row.job_id for row in accepted if row.job_id not in mapped)
    net_done = round(sum(r.ai_net for r in accepted if r.ai_net is not None), 2)
    wall_done = round(sum(r.elapsed for r in accepted if r.elapsed is not None), 2)

    wall_remaining_low = wall_remaining_high = None
    if range_known and inflation is not None:
        wall_remaining_low = round(remaining_low * inflation, 2)
        wall_remaining_high = round(remaining_high * inflation, 2)

    completion_low = completion_high = None
    if wall_remaining_low is not None and (wall_done + wall_remaining_high) > 0:
        completion_low = round(100 * wall_done / (wall_done + wall_remaining_high), 1)
        completion_high = round(100 * wall_done / (wall_done + wall_remaining_low), 1)

    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "plan_status": str(meta.get("plan_status") or "missing"),
        "plan_version": meta.get("plan_version"),
        "phases": phase_reports,
        "phase_count": len(phases),
        "phases_done": sum(1 for p in phases if p.status == "done"),
        "phases_dropped": sum(1 for p in phases if p.status == "dropped"),
        "closed_cards": len(closed),
        "net_done": net_done,
        "wall_done": wall_done,
        "remaining_net_low": round(remaining_low, 2) if range_known else None,
        "remaining_net_high": round(remaining_high, 2) if range_known else None,
        "remaining_wall_low": wall_remaining_low,
        "remaining_wall_high": wall_remaining_high,
        "total_net_low": round(net_done + remaining_low, 2) if range_known else None,
        "total_net_high": round(net_done + remaining_high, 2) if range_known else None,
        "inflation": inflation,
        "inflation_samples": len(both),
        "wall_clock_completion_low": completion_low,
        "wall_clock_completion_high": completion_high,
        "orphan_cards": orphans,
    }


def render_plan_block(rollup: dict) -> str:
    lines = [
        "_由 `estimate_stats.py --plan` 于 %s 生成，勿手改本块。_" % rollup["generated_at"],
        "",
    ]
    if rollup.get("source_hash"):
        lines += [
            "source_revision: %s" % rollup.get("source_revision"),
            "source_hash: %s" % rollup["source_hash"],
            "",
        ]
    if rollup["plan_status"] != "confirmed":
        lines += [
            "**`plan_status: %s`——本单尚未经人确认，不得作为对外承诺的总量。**"
            % rollup["plan_status"],
            "",
        ]
    if not rollup["phases"]:
        lines.append("「## 阶段」表还没有已填写的阶段行。Estimator 先落阶段与区间再跑本脚本。")
        return "\n".join(lines)

    lines += [
        "| 阶段 | 状态 | 区间 h | 已结档卡 | 实测净 h | 实测墙钟 h | 剩余净 h | 实测 vs 区间 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for phase in rollup["phases"]:
        span = (
            "%s–%s" % (fmt(phase["low"]), fmt(phase["high"]))
            if phase["low"] is not None
            else "—"
        )
        remaining = (
            "%s–%s" % (fmt(phase["remaining_low"]), fmt(phase["remaining_high"]))
            if phase["remaining_low"] is not None
            else "—"
        )
        lines.append(
            "| %s | %s | %s | %d | %s | %s | %s | %s |"
            % (
                phase["name"],
                phase["status"],
                span,
                phase["cards"],
                fmt(phase["net_actual"]),
                fmt(phase["wall_actual"]),
                remaining,
                phase["verdict"],
            )
        )

    lines += [
        "",
        "**实测**：已结档 %d 张卡，净耗时合计 %sh，墙钟合计 %sh；阶段 %d/%d 完成"
        % (
            rollup["closed_cards"],
            fmt(rollup["net_done"]),
            fmt(rollup["wall_done"]),
            rollup["phases_done"],
            rollup["phase_count"],
        )
        + ("，已作废 %d 个。" % rollup["phases_dropped"] if rollup["phases_dropped"] else "。"),
    ]

    if rollup["remaining_net_low"] is None:
        lines.append(
            "**剩余**：有阶段没写区间，无法合计总量。先把未拆阶段补成区间（见 `ESTIMATE.md` §七）。"
        )
    else:
        lines.append(
            "**剩余净耗时** %sh–%sh；**总量净耗时** %sh–%sh。"
            % (
                fmt(rollup["remaining_net_low"]),
                fmt(rollup["remaining_net_high"]),
                fmt(rollup["total_net_low"]),
                fmt(rollup["total_net_high"]),
            )
        )

    if rollup["inflation"] is None:
        lines.append(
            "**墙钟换算**：样本不足 3 张，暂不给净/墙钟膨胀系数，也不据此推日历排期。"
        )
    else:
        lines.append(
            "**净/墙钟膨胀系数** %s（%d 张卡实测）：剩余墙钟约 %sh–%sh，墙钟完成度约 %s%%–%s%%。"
            % (
                fmt(rollup["inflation"]),
                rollup["inflation_samples"],
                fmt(rollup["remaining_wall_low"]),
                fmt(rollup["remaining_wall_high"]),
                fmt(rollup["wall_clock_completion_low"]),
                fmt(rollup["wall_clock_completion_high"]),
            )
        )

    if rollup["orphan_cards"]:
        lines.append(
            "_有 %d 张已结档卡的 shape 不在任何阶段的「已拆卡编号」里，未计入阶段实测：%s。补进对应阶段或新增阶段。_"
            % (len(rollup["orphan_cards"]), "、".join(rollup["orphan_cards"][:8]))
        )

    lines.append(
        "_口径：区间与净耗时是 `ai_net`（AI 主动执行时间），完成度与排期是墙钟 `elapsed`。"
        "**AI 净耗时 ≠ 人力工时**，不含等人时间，也不含人自己动手的部分。_"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------- 输出


def fmt(value) -> str:
    return "—" if value is None else "%g" % value


def render_block(stats: dict) -> str:
    overall = stats["overall"]
    lines = ["_由 `estimate_stats.py` 于 %s 生成，勿手改本块。_" % stats["generated_at"], ""]

    if not overall["n_with_net"]:
        lines.append("尚无带 `estimate` / `ai_net` 的已结档卡。新卡填齐字段后重跑本脚本。")
    else:
        lines += [
            "**总体**（%d 张已结档卡）：净耗时 p50 %sh / p90 %sh；偏差率 p50 %s / p90 %s；"
            "估低 2 倍以上 %d 张，估高一半以上 %d 张。"
            % (
                overall["n_with_net"],
                fmt(overall["ai_net_p50"]),
                fmt(overall["ai_net_p90"]),
                fmt(overall["variance_p50"]),
                fmt(overall["variance_p90"]),
                overall["over_2x"],
                overall["under_half"],
            ),
            "",
            "| 分组 | 卡数 | 净耗时 p50 | 净耗时 p90 | 偏差率 p50 | 偏差率 p90 |",
            "|---|---|---|---|---|---|",
        ]
        if not overall["distribution_ready"]:
            lines.insert(
                -2,
                "_主样本少于 5 张：展示 p50，p90 暂记为“—”，不得据此声称已有稳定分布。_",
            )
        for label, key in (
            ("估分类", "by_estimate_group"),
            ("面数", "by_face_count"),
            ("面", "by_face"),
            ("gate", "by_gate"),
            ("人验", "by_human_accept"),
            ("风险", "by_risk"),
            ("live-editor", "by_live_editor"),
        ):
            for name, group in (stats.get(key) or {}).items():
                if not group["n_with_net"]:
                    continue
                lines.append(
                    "| %s %s | %d | %sh | %sh | %s | %s |"
                    % (
                        label,
                        name,
                        group["n_with_net"],
                        fmt(group["ai_net_p50"]),
                        fmt(group["ai_net_p90"]),
                        fmt(group["variance_p50"]),
                        fmt(group["variance_p90"]),
                    )
                )

    back = stats["backfilled"]
    if back["n_with_net"]:
        lines += [
            "",
            "_回溯补录 %d 张（`estimate_source: backfill`）单列，净耗时 p50 %sh、偏差率 p50 %s。"
            "它的 ai_net 是墙钟冒充的，偏高，只看趋势，不作为新卡 `similar:` 依据。_"
            % (back["n_with_net"], fmt(back["ai_net_p50"]), fmt(back["variance_p50"])),
        ]
    return "\n".join(lines)


def atomic_write_text(path: Path, text: str) -> None:
    """同目录临时文件 + os.replace，避免中断留下半截文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def write_block(
    job_shapes: Path,
    block: str,
    dry_run: bool,
    begin: str = AUTO_BEGIN,
    end: str = AUTO_END,
) -> bool:
    text = job_shapes.read_text(encoding="utf-8")
    if begin not in text or end not in text:
        print("[warn] %s 里没有 %s 块，跳过回写" % (job_shapes, begin), file=sys.stderr)
        return False
    updated = re.sub(
        re.escape(begin) + r".*?" + re.escape(end),
        begin + "\n" + block + "\n" + end,
        text,
        flags=re.S,
    )
    if updated == text:
        return False
    if not dry_run:
        atomic_write_text(job_shapes, updated)
    return True


# ---------------------------------------------------------------- 跨实例估分库


def load_instance(instance_root: Path) -> dict:
    """读实例 INSTANCE.json；缺失或损坏都返回 {}，由调用方决定是否硬停。"""
    path = instance_root / "INSTANCE.json"
    if not path.is_file():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def library_key(record: dict) -> str:
    rev = record.get("closure_revision")
    if record.get("schema") == LIBRARY_SCHEMA or rev is not None:
        return "%s::%s::%s" % (
            record.get("instance_id") or "?",
            record.get("job_id") or "?",
            rev if rev is not None else 1,
        )
    return "%s::%s" % (record.get("instance_id") or "?", record.get("job_id") or "?")


def latest_library_records(records: List[dict]) -> List[dict]:
    """同一 job 只保留最新有效 closure；acceptance_revoked 使该修订失效。"""
    ranked: Dict[str, dict] = {}
    for record in records:
        job_key = "%s::%s" % (record.get("instance_id") or "?", record.get("job_id") or "?")
        rev = int(record.get("closure_revision") or (0 if record.get("schema") == LIBRARY_SCHEMA_V1 else 1))
        prev = ranked.get(job_key)
        prev_rev = int((prev or {}).get("closure_revision") or 0)
        if prev is None or rev >= prev_rev:
            ranked[job_key] = dict(record, closure_revision=rev)
    effective = []
    for record in ranked.values():
        if record.get("event") == "acceptance_revoked":
            continue
        effective.append(record)
    return effective


def read_library(path: Path) -> Tuple[List[dict], List[str]]:
    """读 JSONL 库。坏行只报告不丢弃其它行——库是追加式的，一行坏了不能连累历史。"""
    records: List[dict] = []
    errors: List[str] = []
    if not path.is_file():
        return records, errors
    for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append("第 %d 行不是合法 JSON：%s" % (lineno, exc))
            continue
        if not isinstance(record, dict):
            errors.append("第 %d 行不是 JSON object" % lineno)
            continue
        records.append(record)
    return records, errors


def build_library_records(rows: List[Row], instance: dict) -> List[dict]:
    """只带估分字段与溯源标识；不带产物路径、目标描述或证据内容。"""
    instance_id = str(instance.get("instance_id") or "")
    domain = str(instance.get("domain_name") or "")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    records: List[dict] = []
    for row in rows:
        if row.status not in CLOSED or row.estimate is None or row.ai_net is None:
            continue
        records.append(
            {
                "schema": LIBRARY_SCHEMA,
                "instance_id": instance_id,
                "domain": domain,
                "job_id": row.job_id,
                "estimate_group": row.estimate_group,
                "faces": list(row.faces),
                "estimate": row.estimate,
                "estimate_source": row.estimate_source,
                "ai_net": row.ai_net,
                "elapsed": row.elapsed,
                "variance": row.variance,
                "status": row.status,
                "event": "closed",
                "closure_revision": 1,
                "closed_at": row.ended_at,
                "accepted_at": row.ended_at if row.status == "accepted" else None,
                "appended_at": now,
            }
        )
    return records


def append_library(path: Path, records: List[dict], dry_run: bool) -> Tuple[List[str], List[str]]:
    """按 instance_id + job_id 幂等追加。已在库中的跳过，绝不改写历史行。"""
    existing, _ = read_library(path)
    known = {library_key(record) for record in existing}
    appended: List[str] = []
    skipped: List[str] = []
    pending: List[dict] = []
    for record in records:
        key = library_key(record)
        if key in known:
            skipped.append(record.get("job_id") or "?")
            continue
        known.add(key)
        pending.append(record)
        appended.append(record.get("job_id") or "?")
    if pending and not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8", newline="\n") as stream:
            for record in pending:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    return appended, skipped


def library_rows(records: List[dict], group: Optional[str] = None) -> List[Row]:
    rows: List[Row] = []
    for record in records:
        if group and str(record.get("estimate_group") or "") != group:
            continue
        faces = record.get("faces")
        rows.append(
            Row(
                job_id="%s/%s" % (record.get("domain") or "?", record.get("job_id") or "?"),
                shape="-",
                estimate_group=str(record.get("estimate_group") or "unknown"),
                faces=list(faces) if isinstance(faces, list) else [],
                estimate=parse_duration_hours(record.get("estimate")),
                estimate_source=str(record.get("estimate_source") or "unknown"),
                ai_net=parse_duration_hours(record.get("ai_net")),
                elapsed=parse_duration_hours(record.get("elapsed")),
                variance=(
                    float(record["variance"]) if isinstance(record.get("variance"), (int, float)) else None
                ),
                status=str(record.get("status") or ""),
                ended_at=record.get("closed_at") or record.get("accepted_at"),
            )
        )
    return rows


def resolve_library_path(
    explicit: Optional[str],
    instance: dict,
    anchor: Optional[Path] = None,
) -> Optional[Path]:
    """explicit / INSTANCE.json 的库路径：相对路径相对仓库根，由 anchor 定位仓库。"""
    base = (anchor or Path.cwd()).resolve()
    if explicit:
        return instantiate_domain.resolve_declared(explicit, base)
    configured = instance.get("estimate_library")
    if configured:
        return instantiate_domain.resolve_declared(configured, base)
    return None


# ---------------------------------------------------------------- backfill


def load_backfill_map(path: Path) -> Dict[str, float]:
    """读取人工核对的 shape → estimate JSON；不从自然语言菜单猜测。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("backfill map 必须是 JSON object")
    result: Dict[str, float] = {}
    for shape, estimate in raw.items():
        value = float(estimate)
        if value <= 0:
            raise ValueError("shape %s 的 estimate 必须 > 0" % shape)
        result[str(shape)] = value
    return result


def backfill_card(path: Path, estimate: Optional[float], elapsed_h: Optional[float], dry_run: bool) -> bool:
    """给已结档但缺字段的旧卡补 estimate / ai_net / variance，一律标 backfill。"""
    if estimate is None or elapsed_h is None:
        return False
    text = path.read_text(encoding="utf-8")
    match = re.search(r"(```ya?ml\s*\n)(.*?)(\n```)", text, flags=re.S)
    if not match:
        return False
    body = match.group(2)
    variance = round(elapsed_h / estimate, 2) if estimate else None
    additions = {
        "estimate": "%g" % estimate,
        "ai_net": "%gh" % elapsed_h,
        "variance": "%g" % variance if variance is not None else "null",
    }
    changed = False
    for key, value in additions.items():
        pattern = re.compile(r"^%s:\s*(.*)$" % key, flags=re.M)
        existing = pattern.search(body)
        if existing and parse_scalar(existing.group(1)) is not None:
            continue  # 已有真值，不覆盖
        line = "%s: %s  # backfill" % (key, value)
        body = pattern.sub(line, body, count=1) if existing else body.rstrip() + "\n" + line
        changed = True
    # 只要 ai_net 来自历史墙钟，整行就必须进入 backfill 分布，不能冒充主样本。
    source_pattern = re.compile(r"^estimate_source:\s*(.*)$", flags=re.M)
    source_line = "estimate_source: backfill  # backfill"
    existing_source = source_pattern.search(body)
    if not existing_source or parse_scalar(existing_source.group(1)) != "backfill":
        body = (
            source_pattern.sub(source_line, body, count=1)
            if existing_source
            else body.rstrip() + "\n" + source_line
        )
        changed = True
    if not changed:
        return False
    if not dry_run:
        atomic_write_text(path, text[: match.start(2)] + body + text[match.end(2) :])
    return True


# ---------------------------------------------------------------- main


def report_library(library_path: Path, group: Optional[str]) -> int:
    records, errors = read_library(library_path)
    for item in errors:
        print("[warn] 估分库 %s" % item, file=sys.stderr)
    if not records:
        print("估分库为空或不存在：%s" % library_path)
        print("第一批卡结档后跑 --library-append 才会有历史；在此之前只能 fresh 估。")
        return 0
    rows = library_rows(latest_library_records(records), group)
    if not rows:
        print("估分库里没有 estimate_group=%s 的记录：%s" % (group, library_path))
        return 0
    stats = build_stats(rows)
    print("估分库：%s（%d 条记录%s）" % (library_path, len(rows), "，已按 group 过滤" if group else ""))
    print(render_block(stats))
    print()
    print("_跨领域引用前先确认 `estimate_group` 是不是同一件事；名字撞了但活不同，另起一个类。_")
    return 0


def _protocol_major(instance_root: Path) -> int:
    instance = load_instance(instance_root)
    try:
        return int(str(instance.get("protocol_version") or "0").split(".", 1)[0])
    except (TypeError, ValueError):
        return 0


def _v4_stats_projection(instance_root: Path, out_path: Optional[Path], dry_run: bool) -> int:
    """Read v4 timing from authoritative cards without touching v3 fields.

    The v4 core persists timing records in the card transaction.  This legacy
    entrypoint is therefore a read/projection wrapper for v4: it never runs
    the old fenced-YAML backfill or rewrites JOB-SHAPES/PLAN/FACTS.  The JSON
    output is disposable projection data and can be regenerated from cards.
    """
    try:
        import sjs_v4
        import sjs_v4_stats
    except ImportError as exc:
        print("[error] v4 stats adapters unavailable: %s" % exc, file=sys.stderr)
        return 2
    snapshot = sjs_v4.load_snapshot(instance_root)
    instance = snapshot.get("instance") or {}
    samples: List[dict] = []
    cards_summary: List[dict] = []
    for card in snapshot.get("cards") or []:
        meta = card.get("metadata") if isinstance(card.get("metadata"), dict) else card
        records = meta.get("timing_records")
        if not isinstance(records, list):
            records = [meta.get("timing_last")] if isinstance(meta.get("timing_last"), dict) else []
        for record in records:
            if not isinstance(record, dict):
                continue
            item = dict(record)
            item.setdefault("instance_id", instance.get("instance_id"))
            item.setdefault("job_id", card.get("id"))
            item.setdefault("shape", card.get("shape"))
            item.setdefault("closure_revision", meta.get("revision") if card.get("status") == "accepted" else None)
            samples.append(item)
        cards_summary.append({
            "card_id": card.get("id"),
            "shape": card.get("shape"),
            "status": card.get("status"),
            "revision": card.get("revision", 0),
            "initial_estimate": meta.get("estimate_total", meta.get("estimate")),
        })
    projection = {
        "schema": "serial-job-session-v4-estimate-projection",
        "instance_id": instance.get("instance_id"),
        "domain_name": instance.get("domain_name"),
        "revision": snapshot.get("revision", 0),
        "cards": cards_summary,
        "timing": sjs_v4_stats.statistics_projection(samples),
        "sample_count": len(samples),
        "calibration_basis": ["v4_host_timing"],
        "source": "card.metadata.timing_records",
    }
    if out_path is not None and not dry_run:
        atomic_write_text(out_path, json.dumps(projection, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(projection, ensure_ascii=False, indent=2))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="串行任务会话估分偏差统计")
    parser.add_argument("--jobs", default=None, help="任务卡目录（领域包的 jobs/）")
    parser.add_argument("--instance", default=None, help="v4 实例根目录；等同于 --jobs <instance>/jobs")
    parser.add_argument("--job-shapes", default=None, help="JOB-SHAPES.md 路径，回写统计块")
    parser.add_argument("--out", default=None, help="JSON 输出路径，缺省为 JOB-SHAPES 同级的 estimate-stats.json")
    parser.add_argument("--plan", default=None, help="PLAN.md 路径，回写 AUTO:PLAN-ROLLUP 阶段滚动块")
    parser.add_argument(
        "--library",
        default=None,
        help="跨实例估分库 JSONL 路径；缺省取实例 INSTANCE.json 的 estimate_library",
    )
    parser.add_argument("--library-append", action="store_true", help="把已结档卡幂等追加进估分库")
    parser.add_argument("--library-stats", action="store_true", help="读估分库出分组分布；可不带 --jobs")
    parser.add_argument("--group", default=None, help="只看某个 estimate_group（配 --library-stats）")
    parser.add_argument("--calibrate", action="store_true", help="对 --group 用估分库做在线校准并打印")
    parser.add_argument("--prior-hours", type=float, default=None, help="校准先验小时数")
    parser.add_argument("--backfill", action="store_true", help="给缺字段的已结档旧卡补 backfill 估分")
    parser.add_argument(
        "--backfill-map",
        default=None,
        help='人工核过的 shape→estimate JSON，如 {"12": 0.5}；--backfill 时必填',
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写文件")
    args = parser.parse_args(argv)

    skill_root = SKILL_ROOT.resolve()

    # 只查库：不需要领域包，Estimator 冷启动时用。
    if args.library_stats and not args.jobs:
        library_path = resolve_library_path(args.library, {})
        if library_path is None:
            print("[error] --library-stats 不带 --jobs 时必须显式给 --library <库路径>", file=sys.stderr)
            return 2
        if is_within(library_path, skill_root):
            print("[error] library 指向 skill 只读母版，拒绝运行：%s" % library_path, file=sys.stderr)
            return 2
        return report_library(library_path, args.group)

    instance_hint = Path(args.instance).expanduser().resolve() if args.instance else None
    if instance_hint is not None and not args.jobs:
        args.jobs = str(instance_hint / "jobs")
    if instance_hint is not None and not args.job_shapes:
        args.job_shapes = str(instance_hint / "JOB-SHAPES.md")
    if not args.jobs or not args.job_shapes:
        print("[error] 需要 --jobs 与 --job-shapes（只查库请用 --library-stats --library <路径>）", file=sys.stderr)
        return 2

    jobs_dir = Path(args.jobs).expanduser().resolve()
    instance_root = instance_hint or jobs_dir.parent
    if _protocol_major(instance_root) == 4:
        if args.backfill or args.library_append or args.plan:
            print("[error] v4 估分由卡事务和 sjs_v4_stats 投影负责；旧 backfill/library/PLAN 写入口已拒绝", file=sys.stderr)
            return 2
        out_hint = Path(args.out).expanduser().resolve() if args.out else jobs_dir.parent / "estimate-stats.json"
        if is_within(out_hint, skill_root):
            print("[error] out 指向 skill 只读母版，拒绝运行：%s" % out_hint, file=sys.stderr)
            return 2
        return _v4_stats_projection(instance_root, out_hint, args.dry_run)

    job_shapes = Path(args.job_shapes).expanduser().resolve()
    out_path = Path(args.out).expanduser().resolve() if args.out else job_shapes.parent / "estimate-stats.json"
    plan_path = Path(args.plan).expanduser().resolve() if args.plan else None

    guarded = [("jobs", jobs_dir), ("job-shapes", job_shapes), ("out", out_path)]
    if plan_path is not None:
        guarded.append(("plan", plan_path))
    for label, path in guarded:
        if is_within(path, skill_root):
            print(
                "[error] %s 指向 skill 只读母版，拒绝运行：%s" % (label, path),
                file=sys.stderr,
            )
            return 2

    if not jobs_dir.is_dir():
        print("[error] 任务卡目录不存在：%s" % jobs_dir, file=sys.stderr)
        return 1
    if not job_shapes.is_file():
        print("[error] JOB-SHAPES 不存在：%s" % job_shapes, file=sys.stderr)
        return 1
    if plan_path is not None and not plan_path.is_file():
        print("[error] PLAN.md 不存在：%s" % plan_path, file=sys.stderr)
        return 1

    instance = load_instance(jobs_dir.parent)
    library_path: Optional[Path] = None
    if args.library_append or args.library_stats:
        library_path = resolve_library_path(
            args.library, instance, jobs_dir.parent
        )
        if library_path is None:
            print(
                "[error] 估分库路径未知：实例 INSTANCE.json 缺 estimate_library，也没给 --library",
                file=sys.stderr,
            )
            return 2
        if is_within(library_path, skill_root):
            print("[error] library 指向 skill 只读母版，拒绝运行：%s" % library_path, file=sys.stderr)
            return 2
        if args.library_append and not instance.get("instance_id"):
            print(
                "[error] 追加估分库需要 INSTANCE.json 的 instance_id 做幂等键；"
                "请用 instantiate_domain.py 创建实例",
                file=sys.stderr,
            )
            return 2

    if args.backfill and not args.backfill_map:
        print("[error] --backfill 必须同时提供 --backfill-map，禁止从菜单正文猜工时", file=sys.stderr)
        return 2
    try:
        menu = load_backfill_map(Path(args.backfill_map).expanduser().resolve()) if args.backfill else {}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print("[error] backfill map 无效：%s" % exc, file=sys.stderr)
        return 2

    rows: List[Row] = []
    skipped: List[str] = []
    backfilled: List[str] = []

    for path in sorted(jobs_dir.glob("*.md")):
        if path.name.startswith("_"):  # _template.md 之类
            continue
        card = parse_card(path)
        if not card:
            skipped.append("%s（无 yaml 块）" % path.name)
            continue
        status = str(card.get("status") or "")
        if status not in CLOSED:
            continue

        shape = str(card.get("shape") or "?")
        elapsed = parse_duration_hours(card.get("elapsed"))

        if args.backfill and (card.get("estimate") is None or card.get("ai_net") is None):
            existing_estimate = parse_duration_hours(card.get("estimate"))
            chosen_estimate = existing_estimate if existing_estimate is not None else menu.get(shape)
            if backfill_card(path, chosen_estimate, elapsed, args.dry_run):
                backfilled.append(path.name)
                if args.dry_run:
                    card["estimate"] = chosen_estimate
                    card["estimate_source"] = "backfill"
                    card["ai_net"] = elapsed
                else:
                    card = parse_card(path) or card

        estimate = parse_duration_hours(card.get("estimate"))
        raw_net = card.get("ai_net")
        untracked = str(raw_net).strip().lower() == "untracked"
        ai_net = None if untracked else parse_duration_hours(raw_net)
        try:
            variance = float(card["variance"]) if card.get("variance") is not None else None
        except (TypeError, ValueError):
            variance = None
        if variance is None and estimate and ai_net is not None:
            variance = round(ai_net / estimate, 2)

        if not untracked and (estimate is None or ai_net is None):
            skipped.append("%s（缺 estimate/ai_net）" % path.name)

        faces = card.get("faces") or []
        if isinstance(faces, str):
            faces = [faces]
        live = any(face in {"houdini-hda", "unity-csharp", "shader"} for face in faces)

        rows.append(
            Row(
                job_id=str(card.get("id") or path.stem),
                shape=shape,
                estimate_group=str(card.get("estimate_group") or "legacy:" + shape),
                faces=list(faces),
                estimate=estimate,
                estimate_source=str(card.get("estimate_source") or "unknown"),
                ai_net=ai_net,
                elapsed=elapsed,
                variance=variance,
                status=status,
                ended_at=(str(card.get("ended_at")) if card.get("ended_at") is not None else None),
                gate=card.get("gate") if isinstance(card.get("gate"), bool) else None,
                human_accept=str(card.get("human_accept") or ""),
                risk=str(card.get("risk") or ""),
                live_editor=live,
                untracked=untracked,
            )
        )

    stats = build_stats(rows)
    stats["jobs_dir"] = instantiate_domain.to_portable(jobs_dir, jobs_dir)
    stats["skipped"] = skipped
    stats["backfilled_cards"] = backfilled

    block = render_block(stats)
    if not args.dry_run:
        atomic_write_text(out_path, json.dumps(stats, ensure_ascii=False, indent=2) + "\n")
    wrote = write_block(job_shapes, block, args.dry_run)

    print(block)
    print()
    print("扫到已结档卡 %d 张；缺字段 %d 张；回溯补录 %d 张。" % (len(rows), len(skipped), len(backfilled)))
    if skipped:
        print("  缺字段：" + "、".join(skipped[:10]) + ("…" if len(skipped) > 10 else ""))
    print("JSON：%s%s" % (out_path, "（dry-run 未写）" if args.dry_run else ""))
    print("JOB-SHAPES 回写：%s%s" % ("已更新" if wrote else "未变化", "（dry-run）" if args.dry_run else ""))

    if plan_path is not None:
        meta, phases, plan_errors = parse_plan(plan_path)
        for item in plan_errors:
            print("[warn] PLAN.md：%s" % item, file=sys.stderr)
        rollup = build_plan_rollup(meta, phases, rows)
        try:
            from validate_domain_pack import compute_plan_source_hash

            rollup["source_hash"] = compute_plan_source_hash(plan_path.parent)
            rollup["source_revision"] = meta.get("plan_version")
        except Exception:
            rollup["source_hash"] = None
        plan_block = render_plan_block(rollup)
        plan_wrote = write_block(
            plan_path, plan_block, args.dry_run, PLAN_AUTO_BEGIN, PLAN_AUTO_END
        )
        print()
        print(plan_block)
        print()
        print("PLAN 回写：%s%s" % ("已更新" if plan_wrote else "未变化", "（dry-run）" if args.dry_run else ""))

    if library_path is not None and args.library_append:
        records = build_library_records(rows, instance)
        appended, skipped_lib = append_library(library_path, records, args.dry_run)
        print()
        print(
            "估分库：新增 %d 条，已存在跳过 %d 条 → %s%s"
            % (len(appended), len(skipped_lib), library_path, "（dry-run 未写）" if args.dry_run else "")
        )

    if library_path is not None and args.library_stats:
        print()
        report_library(library_path, args.group)
    if args.calibrate:
        import json as _json
        import calibrate

        cfg_path = SKILL_ROOT / "estimate-config.json"
        cfg = _json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.is_file() else {}
        lib_path = library_path or resolve_library_path(args.library, instance, jobs_dir.parent)
        records, _ = read_library(lib_path) if lib_path else ([], [])
        latest = latest_library_records(records)
        group_rows = [
            r
            for r in library_rows(latest, args.group)
            if r.status == "accepted" and r.ai_net and r.estimate
        ]
        global_rows = [
            r for r in library_rows(latest) if r.status == "accepted" and r.ai_net and r.estimate
        ]
        prior = args.prior_hours
        if prior is None and group_rows:
            prior = group_rows[-1].estimate
        if prior is None:
            print("[error] --calibrate 需要 --prior-hours 或库内样本", file=sys.stderr)
            return 2
        result = calibrate.calibrate_estimate(
            prior,
            [r.ai_net for r in group_rows],
            [r.estimate for r in group_rows],
            [r.ai_net for r in global_rows],
            [r.estimate for r in global_rows],
            quantile=float(cfg.get("quantile") or 0.7),
            prior_strength=float(cfg.get("prior_strength") or 4.0),
            speed_prior=float(cfg.get("speed_prior") or 1.0),
        )
        print()
        print(_json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
