#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""轻量 Workflow 深度/越权/拆卡/并行写冲突/父卡 Close 校验。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

CONTROL_FILES = {
    "CURRENT.md",
    "JOB-SHAPES.md",
    "FACTS.md",
    "LOG.md",
    "NORMS.md",
    "PLAN.md",
    "ENV.md",
    "INSTANCE.json",
}
SHARED_EDITORS = {"unity", "houdini", "heu"}
SPLIT_FLAGS = (
    "independent_schedule",
    "independent_accept",
    "independent_reject",
    "independent_depends",
    "human_decision",
)
INCOMPLETE = {"pending", "running", None, ""}


def _norm(path: str) -> str:
    return str(path).replace("\\", "/").strip().strip("/")


def _name(path: str) -> str:
    return Path(_norm(path)).name


def _overlap(a: str, b: str) -> bool:
    left, right = _norm(a), _norm(b)
    if not left or not right:
        return False
    if left == right:
        return True
    return left.startswith(right + "/") or right.startswith(left + "/")


def _writable(node: dict) -> bool:
    if node.get("readonly"):
        return False
    return bool(node.get("writes"))


def validate_light_workflow(
    path: Path,
    expected_parent: Optional[str] = None,
    expected_revision: Optional[int] = None,
) -> List[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return validate_light_workflow_data(data, expected_parent, expected_revision)


def validate_light_workflow_data(
    data: dict,
    expected_parent: Optional[str] = None,
    expected_revision: Optional[int] = None,
) -> List[str]:
    errors: List[str] = []
    if data.get("schema") != "serial-job-session-light-workflow/v1":
        errors.append("WORKFLOW_SCHEMA")
    if expected_parent and data.get("parent_job_id") != expected_parent:
        errors.append("WORKFLOW_PARENT_MISMATCH")
    if expected_revision is not None and int(data.get("revision") or 0) != int(expected_revision):
        errors.append("WORKFLOW_REVISION_MISMATCH")
    if int(data.get("max_depth") or 1) > 1:
        errors.append("WORKFLOW_DEPTH")
    root = _norm(str(data.get("execution_root") or ""))
    if "reports/workflows" in root:
        errors.append("WORKFLOW_GLOBAL_REPORTS")
    nodes = data.get("nodes") or []
    max_nodes = int(data.get("max_nodes") or 4)
    if len(nodes) > max_nodes or len(nodes) > 4:
        errors.append("WORKFLOW_TOO_MANY_NODES")
    seen = set()
    for node in nodes:
        nid = node.get("id")
        if nid in seen:
            errors.append("WORKFLOW_DUP_NODE")
        seen.add(nid)
        if node.get("nested_agents"):
            errors.append("WORKFLOW_CHILD_RECURSE")
        if any(node.get(flag) for flag in SPLIT_FLAGS):
            errors.append("WORKFLOW_SPLIT_REQUIRED")
        if node.get("close_requested"):
            errors.append("WORKFLOW_CHILD_CLOSE")
        if node.get("role") == "review" and node.get("writes"):
            errors.append("REVIEWER_MUST_NOT_WRITE")
        for write in node.get("writes") or []:
            name = _name(str(write))
            if name in CONTROL_FILES:
                errors.append("WORKFLOW_CONTROL_PLANE_WRITE")
            if "reports/workflows" in _norm(str(write)):
                errors.append("WORKFLOW_GLOBAL_REPORTS")
    writers = [n for n in nodes if _writable(n)]
    for i, left in enumerate(writers):
        for right in writers[i + 1 :]:
            for a in left.get("writes") or []:
                for b in right.get("writes") or []:
                    if _overlap(str(a), str(b)):
                        errors.append("WORKFLOW_WRITE_OVERLAP")
            if _editor_resources(left) & _editor_resources(right):
                errors.append("WORKFLOW_SHARED_EDITOR")
    return _unique(errors)


def _editor_resources(node: dict) -> set:
    found = {_norm(x).lower() for x in (node.get("resources") or [])} & SHARED_EDITORS
    for write in node.get("writes") or []:
        parts = {p.lower() for p in _norm(str(write)).split("/")}
        found |= parts & SHARED_EDITORS
    return found


def required_close_blockers(data: dict) -> List[str]:
    blockers: List[str] = []
    for node in data.get("nodes") or []:
        if not node.get("required"):
            continue
        status = node.get("status")
        if status == "failed":
            blockers.append("WORKFLOW_REQUIRED_FAILED")
        elif status == "cancelled":
            blockers.append("WORKFLOW_REQUIRED_CANCELLED")
        elif status in INCOMPLETE:
            blockers.append("WORKFLOW_REQUIRED_INCOMPLETE")
    return _unique(blockers)


def parent_may_close(
    data: dict,
    expected_parent: Optional[str] = None,
    expected_revision: Optional[int] = None,
) -> bool:
    errs = validate_light_workflow_data(data, expected_parent, expected_revision)
    return not errs and not required_close_blockers(data)


def parent_close_errors(
    data: dict,
    expected_parent: Optional[str] = None,
    expected_revision: Optional[int] = None,
) -> List[str]:
    return _unique(
        validate_light_workflow_data(data, expected_parent, expected_revision)
        + required_close_blockers(data)
    )


def locate_manifest(instance_root: Path, front: dict) -> Optional[Path]:
    ref = front.get("workflow_ref")
    job_id = front.get("id")
    candidates: List[Path] = []
    if ref:
        candidates.append(instance_root / str(ref))
    if job_id:
        candidates.append(instance_root / "output" / str(job_id) / "runtime" / "light-workflow.json")
    for path in candidates:
        if path.is_file():
            return path
    return None


def _unique(items: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest")
    parser.add_argument("--parent")
    parser.add_argument("--revision", type=int)
    parser.add_argument("--close-check", action="store_true")
    args = parser.parse_args(argv)
    path = Path(args.manifest)
    data = json.loads(path.read_text(encoding="utf-8"))
    errs = validate_light_workflow_data(data, args.parent, args.revision)
    if args.close_check:
        errs = _unique(errs + required_close_blockers(data))
    if errs:
        print("\n".join(errs))
        return 1
    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
