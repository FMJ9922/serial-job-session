#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析 JOB-SHAPES 待做表与编号细则，供校验器做环/重复/依赖漂移检查。"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

TABLE_HEADER_HINTS = ("shape_id", "编号")
DETAIL_LINE = re.compile(
    r"^(?P<num>\d+)\.\s+\*\*(?P<title>.+?)\*\*.*?依赖：(?P<deps>[^）)]+)",
    re.M,
)


def _cells(line: str) -> List[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_pending_table(text: str) -> Tuple[List[dict], List[str]]:
    errors: List[str] = []
    indexed = re.search(
        r"<!-- AUTO:PENDING-INDEX -->(.*?)<!-- /AUTO:PENDING-INDEX -->",
        text or "",
        flags=re.S,
    )
    if indexed:
        body = indexed.group(1)
    else:
        section = re.search(r"^##\s*待做[^\n]*\n(.*?)(?=^##\s|\Z)", text or "", flags=re.M | re.S)
        if not section:
            return [], ["MENU_TABLE_MISSING"]
        body = section.group(1)
    rows: List[dict] = []
    header: Optional[List[str]] = None
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = _cells(stripped)
        if not cells:
            continue
        if all(re.fullmatch(r":?-{2,}:?", c or "") for c in cells):
            continue
        if header is None and any(h in "".join(cells) for h in ("编号", "shape_id", "目标", "短名")):
            header = cells
            continue
        if header is None:
            continue
        if "{{" in stripped:
            continue
        mapped = {}
        for i, name in enumerate(header):
            if i < len(cells):
                mapped[name] = cells[i]
        shape_raw = mapped.get("shape_id") or mapped.get("编号") or ""
        try:
            shape = int(str(shape_raw).split()[0])
        except (TypeError, ValueError):
            errors.append("MENU_SHAPE_UNPARSED:%s" % shape_raw)
            continue
        deps_raw = mapped.get("depends_on") or mapped.get("依赖") or ""
        deps = [int(p) for p in re.findall(r"\d+", deps_raw)]
        phase = mapped.get("phase_id") or mapped.get("阶段") or ""
        goal = mapped.get("目标（一件，可二值验收）") or mapped.get("目标") or ""
        short = (mapped.get("短名") or "").strip()
        rows.append(
            {
                "shape": shape,
                "phase_id": phase,
                "short_name": short,
                "goal": goal,
                "title": short or goal,
                "estimate_group": mapped.get("estimate_group") or mapped.get("估分类") or "",
                "depends_on": deps,
                "faces": mapped.get("faces") or mapped.get("面") or "",
                "actor": mapped.get("actor") or "",
                "human_accept": mapped.get("human_accept") or "",
                "risk": mapped.get("风险") or mapped.get("risk") or "",
                "estimate": mapped.get("预计") or mapped.get("预计 h") or "",
            }
        )
    if not rows and header is None:
        errors.append("MENU_TABLE_UNPARSED")
    return rows, errors


def parse_detail_deps(text: str) -> Dict[int, List[int]]:
    found: Dict[int, List[int]] = {}
    for match in DETAIL_LINE.finditer(text or ""):
        num = int(match.group("num"))
        deps = [int(p.strip()) for p in match.group("deps").split(",") if p.strip().isdigit()]
        found[num] = deps
    return found


def duplicate_shapes(rows: List[dict]) -> List[int]:
    seen = {}
    dups = []
    for row in rows:
        shape = row["shape"]
        if shape in seen:
            dups.append(shape)
        seen[shape] = True
    return sorted(set(dups))


def cycle_shapes(rows: List[dict]) -> List[int]:
    graph = {row["shape"]: list(row.get("depends_on") or []) for row in rows}
    visiting = set()
    seen = set()
    cyclic: List[int] = []

    def dfs(node: int) -> None:
        if node in visiting:
            cyclic.append(node)
            return
        if node in seen:
            return
        visiting.add(node)
        for nxt in graph.get(node) or []:
            if nxt in graph:
                dfs(nxt)
        visiting.remove(node)
        seen.add(node)

    for node in graph:
        dfs(node)
    return sorted(set(cyclic))


def short_name_issues(name: str) -> List[str]:
    text = (name or "").strip()
    if not text:
        return ["SHORT_NAME_MISSING"]
    issues: List[str] = []
    if text.count(" + ") >= 2:
        issues.append("SHORT_NAME_PILE")
    if len(text) > 24:
        issues.append("SHORT_NAME_TOO_LONG")
    if text.count("`") >= 4:
        issues.append("SHORT_NAME_PILE")
    return issues
