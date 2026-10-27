#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Plan and apply the serial-job-session v3 -> v4 card migration.

The migration is deliberately an adapter around the public ``sjs_v4`` core:
``load_snapshot``, ``parse_card_text``, ``build_context``, ``render_card``,
``render_projections``, ``apply_command`` and ``recover_transaction``.  It does
not maintain a second card database and it never writes output evidence.

The current core may expose the transaction as ``apply_migration`` or
``apply_command`` with ``file_operations``.  A real apply refuses to fall back
to direct filesystem writes when neither public capability is present.  This
keeps archive copies, card replacements, projections and ready-card creation in
one core transaction node.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

try:
    import sjs_v4 as core
except ImportError as exc:  # pragma: no cover - exercised by CLI installation errors
    raise SystemExit("sjs_v4 core is required: %s" % exc)


MIGRATION_SCHEMA = "serial-job-session-v4-migration/v1"
CARD_SCHEMA = "serial-job-session-card/v4"
OLD_TO_V4_STATUS = {
    "ready": "ready",
    "running": "doing",
    "doing": "doing",
    "done_pending": "awaiting_acceptance",
    "awaiting_acceptance": "awaiting_acceptance",
    "rework_required": "doing",
    "accepted": "accepted",
    "rejected": "cancelled",
    "cancelled": "cancelled",
}
V4_SECTION_KEYS = (
    "goal",
    "dependencies",
    "progress",
    "execution",
    "artifacts",
    "closure",
    "knowledge",
)
V4_SECTION_TITLES = (
    "目标范围验收",
    "依赖输入影响",
    "进展问题决策",
    "执行耗时",
    "产物验证反馈",
    "结案补丁",
    "事实经验变化",
)
# Direction-review nodes are per-instance configuration, not skill defaults.
DEFAULT_DIRECTION_NODES = ()


class MigrationError(RuntimeError):
    """A migration planning or transaction capability error."""


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha_text(text: str) -> str:
    return _sha_bytes(text.encode("utf-8"))


def _root(value: Any) -> Path:
    return Path(str(value)).expanduser().resolve()


def _repo_root(root: Path) -> Path:
    for candidate in (root, *root.parents):
        if (candidate / ".serial-job-session").is_dir() or (candidate / ".git").exists():
            return candidate
    return root


def _json_write(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MigrationError("cannot read JSON %s: %s" % (path, exc))
    if not isinstance(value, dict):
        raise MigrationError("JSON root must be object: %s" % path)
    return value


def _relative(root: Path, path: Path) -> str:
    try:
        # Keep the lexical path for a private rehearsal whose output directory
        # is a junction to the real evidence tree.  Resolving the junction
        # would falsely report that an unchanged output file escaped the copy.
        return path.absolute().relative_to(root.absolute()).as_posix()
    except ValueError as exc:
        try:
            return path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            raise MigrationError("path escapes instance: %s" % path) from exc


def _safe_id(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-") or "card"


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("[") and text.endswith("]"):
            text = text[1:-1].strip()
        return [part.strip().strip("'\"") for part in text.split(",") if part.strip()]
    return [value]


def _short(text: str, limit: int = 3200) -> str:
    value = re.sub(r"\n{3,}", "\n\n", str(text or "").strip())
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 90)].rstrip() + "\n\n[正文已归档；此处为上下文摘要。]"


def _join_sections(card: Mapping[str, Any], names: Sequence[str], limit: int = 3200) -> str:
    sections = card.get("sections") if isinstance(card.get("sections"), Mapping) else {}
    chunks: List[str] = []
    for name in names:
        value = str(sections.get(name) or "").strip()
        if value:
            chunks.append(value)
    return _short("\n\n".join(chunks), limit)


def _legacy_heading_sections(card: Mapping[str, Any]) -> Dict[str, str]:
    """Return legacy Markdown sections by title, including Chinese titles.

    ``sjs_v4`` intentionally normalizes unknown legacy headings to ``section``
    keys.  Migration needs the original titles to compress them correctly, so
    this small read-only title pass is kept in the adapter rather than changing
    the core parser's stable public shape.
    """

    body = str(card.get("body") or "")
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", body, flags=re.M))
    result: Dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        title = match.group(1).strip()
        result[title] = body[match.end() : end].strip()
    return result


def _raw_status(card: Mapping[str, Any]) -> str:
    metadata = card.get("metadata") if isinstance(card.get("metadata"), Mapping) else {}
    value = metadata.get("legacy_status") or metadata.get("status") or card.get("status") or "ready"
    return str(value)


def _mapped_status(raw_status: str) -> str:
    try:
        return OLD_TO_V4_STATUS[raw_status]
    except KeyError as exc:
        raise MigrationError("unsupported card status: %s" % raw_status) from exc


def _archive_path(card_id: str) -> str:
    return "knowledge/v4-migration/archive/%s.md" % _safe_id(card_id)


def _section_map(card: Mapping[str, Any]) -> Dict[str, str]:
    """Compress legacy sections into the seven v4 semantic sections.

    Keys intentionally match the public core's stable keys.  The core owns the
    final heading rendering; this adapter never invents a second renderer.
    """

    by_title = _legacy_heading_sections(card)
    normalized = {re.sub(r"\s+", "", title).lower(): value for title, value in by_title.items()}

    def pick(key_words: Sequence[str], aliases: Sequence[str], limit: int) -> str:
        chunks: List[str] = []
        for title, value in by_title.items():
            compact = re.sub(r"\s+", "", title).lower()
            if any(word in compact for word in key_words) or compact in aliases:
                chunks.append(value)
        # Already-v4 cards expose stable English keys through the core parser.
        sections = card.get("sections") if isinstance(card.get("sections"), Mapping) else {}
        for alias in aliases:
            if sections.get(alias):
                chunks.append(str(sections.get(alias)))
        return _short("\n\n".join(chunks), limit)

    return {
        "goal": pick(("目标", "范围验收"), ("goal", "target"), 3000),
        "dependencies": pick(("依赖", "数据流", "下游接口", "官方约束"), ("dependencies",), 4200),
        "progress": pick(("进展", "问题", "决策", "重开依据"), ("progress", "questions", "decisions"), 3200),
        "execution": pick(("估分", "执行区间", "执行耗时"), ("execution",), 3000),
        "artifacts": pick(("验收", "产物路径", "live", "产物验证反馈"), ("artifacts", "acceptance", "feedback"), 4600),
        "closure": pick(("结案", "拟写入facts"), ("closure",), 2200),
        "knowledge": pick(("事实经验", "拟写入norms", "拟写入facts"), ("knowledge", "facts", "norms"), 2600),
    }


def _new_metadata(card: Mapping[str, Any], archive_ref: str) -> dict:
    original = card.get("metadata") if isinstance(card.get("metadata"), Mapping) else {}
    metadata = copy.deepcopy(dict(original))
    raw_status = _raw_status(card)
    metadata["schema"] = CARD_SCHEMA
    metadata["status"] = _mapped_status(raw_status)
    metadata["legacy_status"] = raw_status
    metadata["archive_ref"] = archive_ref
    for field in ("depends_on", "faces", "negative_fixtures", "expected_live_failures", "branch_pass", "branch_fail"):
        if field in metadata:
            metadata[field] = _as_list(metadata.get(field))
    metadata.setdefault("planning_started_at", None)
    metadata.setdefault("planning_ended_at", None)
    metadata.setdefault("planning_elapsed", None)
    metadata.setdefault("time_status", "untracked" if metadata.get("ai_net") == "untracked" else "open")
    metadata.setdefault("patch_count", len(metadata.get("patches") or []) if isinstance(metadata.get("patches"), list) else 0)
    metadata.setdefault("direction_review", False)
    metadata.setdefault("direction_review_after", None)
    if metadata.get("status") == "accepted":
        # A migration must not invent a new validation run.  The old accepted
        # verdict is the evidence that this dependency was valid; carry that
        # fact into the v4 eligibility fields so every downstream card keeps
        # the same dependency meaning until a real patch invalidates it.
        if metadata.get("artifact_valid") is None:
            metadata["artifact_valid"] = True
        if metadata.get("pending_verification") is None:
            metadata["pending_verification"] = False
        metadata.setdefault("artifact_valid_basis", "legacy accepted status; migration did not revalidate artifacts")
        metadata.setdefault("artifact_valid_evidence", archive_ref)
    # A rejected v3 card is intentionally cancelled in v4, but its old verdict
    # and every unknown historical metadata field remain in this block/archive.
    return metadata


def _facts_by_job(root: Path) -> Dict[str, List[dict]]:
    """Parse the legacy FACTS table without treating every row as a rule.

    A v4 card receives structured records so ``sjs_v4.render_projections`` can
    retain the old facts.  Records keep their source date and line, and an
    explicit replacement/revocation hint is retained.  Rows without a clear
    relation stay active historical facts; they are not promoted to NORMS by
    this parser.
    """
    path = root / "FACTS.md"
    if not path.is_file():
        return {}
    result: Dict[str, List[dict]] = {}
    relation_re = re.compile(r"(?:已被|被|已|曾被)?(?:取代|替代|代替|撤销|废止|失效|过时)")
    inactive_re = re.compile(r"(?:已被[^。；\n]{0,40}(?:取代|替代|废止|撤销)|(?:已过时|已废止|已撤销|已失效))")
    for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        match = re.match(r"^\|\s*(.*?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$", line)
        if not match:
            continue
        fact, job_id, date = (part.strip() for part in match.groups())
        if not fact or fact == "事实" or set(fact) <= {"-", "—"}:
            continue
        if job_id in {"来源 job-id", "job-id", "—", "-", ""}:
            continue
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
            continue
        relation = relation_re.search(fact)
        record = {
            "kind": "legacy_fact",
            "active": not bool(inactive_re.search(fact)),
            "historical": True,
            "source_job_id": job_id,
            "source_date": date,
            "source_file": "FACTS.md",
            "source_line": line_no,
            # FACTS.md is regenerated as a projection during migration.  The
            # stable citation must therefore point to the immutable control
            # archive, not to the new projection line (which can move when a
            # later card is patched).
            "source_archive": "knowledge/v4-migration/archive/control/FACTS.md",
            "evidence": "knowledge/v4-migration/archive/control/FACTS.md#L%s" % line_no,
            "text": fact,
        }
        if relation:
            record["relation_hint"] = relation.group(0)
        result.setdefault(job_id, []).append(record)
    return result


def _log_events_by_job(root: Path) -> Dict[str, List[dict]]:
    """Import card-specific legacy LOG rows; non-card prose remains archived."""
    path = root / "LOG.md"
    if not path.is_file():
        return {}
    result: Dict[str, List[dict]] = {}
    for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        match = re.match(r"^\|\s*(\d{4}-\d{2}-\d{2})\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|\s*$", line)
        if not match:
            continue
        date, shape, job_id, result_text = (part.strip() for part in match.groups())
        if shape == "编号" or job_id == "job-id":
            continue
        result.setdefault(job_id, []).append({
            "event": "legacy_close",
            "at": date,
            "shape": shape,
            "result": result_text,
            "source": "knowledge/v4-migration/archive/control/LOG.md#L%s" % line_no,
        })
    return result


def _projection_archives(root: Path) -> List[dict]:
    """Archive every legacy projection before core regenerates it from cards."""
    operations: List[dict] = []
    for name in ("JOB-SHAPES.md", "CURRENT.md", "LOG.md", "FACTS.md", "PLAN.md"):
        source = root / name
        if not source.is_file():
            continue
        raw = source.read_bytes()
        operations.append({
            "kind": "archive_projection_source",
            "path": "knowledge/v4-migration/archive/control/%s" % name,
            "source_path": name,
            "source_sha256": _sha_bytes(raw),
            "bytes_before": len(raw),
            "content": raw.decode("utf-8", errors="replace"),
            "link": "The v4 core regenerates %s from cards; this archive is the complete pre-migration source." % name,
        })
    return operations


def _search_ignore(root: Path) -> Optional[dict]:
    """Keep historical card archives out of ordinary rg discovery.

    This is deliberately an instance-local `.ignore`; it does not hide jobs,
    output, or any other evidence.  Historical text remains addressable via
    each card's archive_ref and an explicit `rg --no-ignore` path.
    """
    path = root / ".ignore"
    raw = path.read_bytes() if path.is_file() else b""
    text = raw.decode("utf-8", errors="replace")
    marker = "/knowledge/v4-migration/archive/"
    if any(line.strip() == marker for line in text.splitlines()):
        return None
    content = text.rstrip("\r\n")
    if content:
        content += "\n"
    content += "# v4 historical source archive; inspect a card archive_ref explicitly.\n"
    content += marker + "\n"
    return {
        "kind": "write_search_ignore",
        "path": ".ignore",
        "content": content,
        "source_sha256": _sha_bytes(raw) if path.is_file() else None,
        "archive_scope": marker,
    }


def _migrate_card(
    root: Path,
    relative: str,
    raw: bytes,
    card: Mapping[str, Any],
    *,
    facts_by_job: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
    log_events_by_job: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
) -> dict:
    # ``load_snapshot`` is intentionally bounded and omits card bodies.  Parse
    # the exact source bytes once here so Chinese legacy headings are retained
    # in the seven-section compression.
    legacy = core.parse_card_text(raw.decode("utf-8", errors="replace"), relative)
    card_id = str(card.get("id") or Path(relative).stem)
    archive_ref = _archive_path(card_id)
    metadata = _new_metadata(legacy, archive_ref)
    metadata["id"] = card_id
    metadata.setdefault("shape", legacy.get("shape", card.get("shape")))
    facts = [copy.deepcopy(dict(item)) for item in (facts_by_job or {}).get(card_id, []) if isinstance(item, Mapping)]
    if facts:
        metadata["fact_records"] = facts
    events = [copy.deepcopy(dict(item)) for item in (log_events_by_job or {}).get(card_id, []) if isinstance(item, Mapping)]
    if events:
        metadata["events"] = events
    sections = _section_map(legacy)
    # Every migrated card has the same seven semantic sections.  Keeping an
    # empty section explicit is important for both the compact context reader
    # and round-trip checks: a missing section otherwise becomes a silent
    # schema fork that each future card has to repair by hand.
    section_hint = "- 迁移前未提供本节；原始正文见 `%s`。" % archive_ref
    sections = {
        key: str(sections.get(key) or section_hint)
        for key in V4_SECTION_KEYS
    }
    if facts:
        fact_note = "- 已导入 %d 条历史 FACTS 记录（来源日期和替代/撤销提示保留；未自动提升为 NORMS）。" % len(facts)
        sections["knowledge"] = (str(sections.get("knowledge") or "").rstrip() + "\n\n" + fact_note).strip()
    if events:
        event_note = "- 已导入 %d 条卡级 LOG 结案事件；其余 LOG 规范补记见控制文档归档。" % len(events)
        sections["progress"] = (str(sections.get("progress") or "").rstrip() + "\n\n" + event_note).strip()
    new_card = {
        "metadata": metadata,
        "sections": sections,
        "path": relative.replace("\\", "/"),
    }
    new_text = core.render_card(new_card)
    return {
        "kind": "rewrite_card",
        "path": relative.replace("\\", "/"),
        "card_id": card_id,
        "shape": card.get("shape"),
        "status": metadata.get("status"),
        "legacy_status": metadata.get("legacy_status"),
        "source_sha256": _sha_bytes(raw),
        "bytes_before": len(raw),
        "bytes_after": len(new_text.encode("utf-8")),
        "archive_ref": archive_ref,
        "archive_sha256": _sha_bytes(raw),
        "archive_content": raw.decode("utf-8", errors="replace"),
        "metadata": metadata,
        "sections": sections,
        "new_text": new_text,
    }


def _parse_table_rows(text: str, heading: str) -> List[dict]:
    lines = text.replace("\r\n", "\n").splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip().startswith("## ") and heading in line:
            start = index + 1
            break
    if start is None:
        return []
    headers: Optional[List[str]] = None
    context_lines: List[str] = []
    rows: List[dict] = []
    for line in lines[start:]:
        if line.strip().startswith("## "):
            break
        if not line.strip().startswith("|"):
            if headers is None and line.strip():
                context_lines.append(line.strip().lstrip("> "))
            continue
        cells = [part.strip() for part in line.strip().strip("|").split("|")]
        if headers is None:
            if any("编号" in cell or "shape" in cell for cell in cells):
                headers = cells
            continue
        if not cells or all(re.fullmatch(r"[-: ]+", cell or "") for cell in cells):
            continue
        if len(cells) < len(headers):
            cells.extend([""] * (len(headers) - len(cells)))
        row = {headers[i]: cells[i] for i in range(min(len(headers), len(cells)))}
        number = row.get("编号") or row.get("shape") or row.get("shape_id")
        match = re.match(r"^\s*(\d+)\b", str(number or ""))
        if not match:
            continue
        row["shape"] = int(match.group(1))
        row["_section_context"] = "\n".join(context_lines).strip()
        rows.append(row)
    return rows


def _shape_detail(text: str, shape: Any) -> str:
    """Read the prose contract following a menu row, when one exists."""

    token = re.escape(str(shape))
    patterns = [
        r"^\s*编号\s+\*\*%s\*\*.*?$" % token,
        r"^\s*编号\s+%s\b.*?$" % token,
    ]
    match = None
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.M)
        if match:
            break
    if not match:
        return ""
    matched_line = match.group(0).strip()
    tail = text[match.end() :]
    next_match = re.search(r"^[ \t]*编号\s+(?:\*\*)?\d+", tail, flags=re.M)
    if next_match:
        tail = tail[: next_match.start()]
    heading = re.search(r"^##\s+", tail, flags=re.M)
    if heading:
        tail = tail[: heading.start()]
    return _short("%s\n\n%s" % (matched_line, tail.strip()), 5200)


def _find_column(row: Mapping[str, Any], *names: str) -> str:
    for name in names:
        if name in row:
            return str(row.get(name) or "").strip()
    for key, value in row.items():
        if any(name in str(key) for name in names):
            return str(value or "").strip()
    return ""


def _ready_card_operation(row: Mapping[str, Any], root: Path, existing_shapes: set) -> Optional[dict]:
    shape = row.get("shape")
    if shape in existing_shapes:
        return None
    short_name = _find_column(row, "短名", "name") or "shape-%s" % shape
    slug = _safe_id(short_name)[:48]
    card_id = "v4-ready-%s-%s" % (shape, slug)
    depends = _find_column(row, "依赖", "depends")
    dependencies = [int(item) if item.isdigit() else item for item in re.split(r"[,，、 ]+", depends) if item]
    faces = _find_column(row, "面", "faces")
    face_values = [item for item in re.split(r"[,，、 ]+", faces) if item]
    estimate_group = _find_column(row, "estimate_group", "估分类") or "backfill"
    estimate_raw = _find_column(row, "预计") or _find_column(row, "estimate")
    try:
        estimate = float(estimate_raw) if estimate_raw else None
        if estimate is not None and estimate.is_integer():
            estimate = int(estimate)
    except ValueError:
        estimate = None
    actor = _find_column(row, "actor") or "agent"
    human_accept = _find_column(row, "human_accept", "人验") or "none"
    goal = _find_column(row, "目标") or short_name
    detail = str(row.get("_detail") or "").strip()
    condition = str(row.get("_section_context") or "").strip()
    precondition_shape = 61 if condition and "4m" in condition else None
    precondition_result = "4m" if precondition_shape else None
    metadata = {
        "schema": CARD_SCHEMA,
        "id": card_id,
        "shape": shape,
        "estimate_group": estimate_group,
        "depends_on": dependencies,
        "faces": face_values,
        "actor": actor,
        "human_accept": human_accept,
        "status": "ready",
        "legacy_status": None,
        "estimate": estimate,
        "estimate_total": estimate,
        "estimate_source": "menu",
        "attempt": 1,
        "revision": 0,
        "archive_ref": None,
        "patch_count": 0,
        "direction_review": False,
        "precondition_shape": precondition_shape,
        "precondition_result": precondition_result,
        "decision": shape == 61,
        "branch_pass": ["8m→62"] if shape == 61 else [],
        "branch_fail": ["4m→96→97→98→99→62"] if shape == 61 else [],
    }
    sections = {
        "goal": "- 短名：%s\n- 目标：%s\n- 来源菜单：shape %s\n\n%s" % (short_name, goal, shape, detail or "（菜单无额外细则）"),
        "dependencies": "- 依赖：%s\n- 输入与影响：待开工补齐。\n\n- 条件/分支上下文：%s" % (", ".join(map(str, dependencies)) or "无", condition or "无"),
        "progress": "- ready 卡；开工后记录问题与决策。",
        "execution": "- 估分：%s h（来源：菜单）。\n- 计时：尚未开始。" % (estimate if estimate is not None else "待估"),
        "artifacts": "- 验收与证据路径：开工时补齐。",
        "closure": "- 结案补丁：无。",
        "knowledge": "- 自动经验：尚无执行证据。",
    }
    card = {"metadata": metadata, "sections": sections, "path": "jobs/%s.md" % card_id}
    new_text = core.render_card(card)
    return {
        "kind": "create_ready_card",
        "path": "jobs/%s.md" % card_id,
        "card_id": card_id,
        "shape": shape,
        "status": "ready",
        "source_sha256": None,
        "bytes_before": 0,
        "bytes_after": len(new_text.encode("utf-8")),
        "archive_ref": None,
        "metadata": metadata,
        "sections": sections,
        "new_text": new_text,
        "menu_row": dict(row),
    }


def _direction_config(root: Path, path: Optional[str]) -> List[Tuple[str, str, str]]:
    candidate = Path(path).expanduser() if path else root / "direction-review.json"
    if candidate.is_file():
        data = _read_json(candidate)
        rows = data.get("nodes") or data.get("direction_review_nodes")
        if isinstance(rows, list):
            result = []
            for row in rows:
                if isinstance(row, Mapping):
                    result.append((str(row.get("after") or ""), str(row.get("before") or ""), str(row.get("label") or "方向纠正")))
            if result:
                return result
    return list(DEFAULT_DIRECTION_NODES)


def _direction_operation(after: str, before: str, label: str, existing_shapes: set, root: Path) -> Optional[dict]:
    # Direction reviews occupy the next empty shape numbers rather than
    # overwriting a historical shape.  The caller assigns 100..104 for Example.
    return None


def _output_manifest(root: Path) -> List[dict]:
    output = root / "output"
    if not output.is_dir():
        return []
    records: List[dict] = []
    for path in sorted(item for item in output.rglob("*") if item.is_file()):
        raw = path.read_bytes()
        records.append({"path": _relative(root, path), "bytes": len(raw), "sha256": _sha_bytes(raw)})
    return records


def _norms_snapshot(root: Path) -> Optional[dict]:
    path = root / "NORMS.md"
    if not path.is_file():
        return None
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    # Preserve source sections/tags rather than rewriting NORMS in place.
    lines = text.splitlines()
    selected: List[str] = []
    keep = False
    for line in lines:
        if re.search(r"v3(?:\.1|\.2|\.3|\.4)?", line, flags=re.I):
            keep = True
        if keep:
            selected.append(line)
            if line.startswith("## ") and selected and len(selected) > 1:
                keep = bool(re.search(r"v3(?:\.1|\.2|\.3|\.4)?", line, flags=re.I))
    body = "# v4 effective NORMS snapshot\n\n> Source: NORMS.md; generated by sjs_v4_migrate.py. Each retained rule keeps its source heading and applicability tags.\n\n"
    body += "\n".join(selected).strip() if selected else "(no v3 overlay headings found; source hash is recorded in the migration plan)"
    return {"kind": "write_norms_snapshot", "path": "knowledge/v4-migration/norms-effective.md", "content": body.rstrip() + "\n", "source_sha256": _sha_bytes(raw)}


def _constraint_snapshot(root: Path) -> Optional[dict]:
    path = root / "JOB-SHAPES.md"
    if not path.is_file():
        return None
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    body = "# v4 dynamic constraint snapshot\n\n> Source: JOB-SHAPES.md; source hash is recorded in the migration plan. This file stores route/gate references only; card Markdown remains authoritative for goals and details.\n\n"
    override = root / "dynamic-constraints.md"
    if override.is_file():
        body += override.read_text(encoding="utf-8").rstrip() + "\n"
    else:
        body += "(no dynamic-constraints.md supplied; card dependencies and the generated menu remain authoritative)\n"
    return {"kind": "write_constraint_snapshot", "path": "knowledge/v4-migration/dynamic-constraints.md", "content": body.rstrip() + "\n", "source_sha256": _sha_bytes(raw)}


def _archive_source(root: Path, relative: str, archive_name: str) -> Optional[dict]:
    source = root / relative
    if not source.is_file():
        return None
    raw = source.read_bytes()
    archive_ref = "knowledge/v4-migration/archive/%s" % archive_name
    return {
        "kind": "archive_source",
        "path": archive_ref,
        "source_path": relative,
        "source_sha256": _sha_bytes(raw),
        "bytes_before": len(raw),
        "content": raw.decode("utf-8", errors="replace"),
    }


def _effective_norms(root: Path) -> Optional[dict]:
    source = root / "NORMS.md"
    if not source.is_file():
        return None
    raw = source.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    override = root / "norms-v4.md"
    if override.is_file():
        body = override.read_text(encoding="utf-8").rstrip() + "\n"
    else:
        body = (
            "# v4 effective NORMS\n\n"
            "> `jobs/` cards are authoritative for task facts, dependencies, decisions and evidence. "
            "This file contains the current domain rules. The pre-migration NORMS is archived once at "
            "`knowledge/v4-migration/archive/NORMS.md`.\n"
            ">\n"
            "> No curated v4 rules were supplied for this instance. Add a `norms-v4.md` next to the "
            "instance before migrating, or keep the archived source as the human-readable baseline. "
            "Dynamic routes and review gates live in cards and "
            "`knowledge/v4-migration/dynamic-constraints.md`; they are not duplicated here.\n"
        )
    # Provenance stays addressable after the rewrite: normalize old source
    # citations to point at the immutable archived copy.
    body = body.replace("`NORMS.md:", "`knowledge/v4-migration/archive/NORMS.md#")
    body = body.replace("`NORMS.md#", "`knowledge/v4-migration/archive/NORMS.md#")
    body = body.replace("`NORMS.md`", "`knowledge/v4-migration/archive/NORMS.md`")
    body = body.rstrip() + "\n"
    # Seed the core's structured norm projection from the curated text.
    # Protected constraints and evolvable practices have separate kinds; a
    # later evidence-backed norm operation can retire one stable id without
    # leaving a second active owner in context.
    norm_records: List[dict] = []
    record_re = re.compile(
        r"^###\s+(NORM-[A-Z0-9-]+)[^\n]*\n"
        r"- rule:\s*(.*?)\n"
        r"- tags:\s*\[(.*?)\]\n"
        r"- source:\s*(.*?)\n"
        r"- evidence:\s*(.*?)(?=\n\n###\s+|\n##\s+|\Z)",
        flags=re.M | re.S,
    )
    for match in record_re.finditer(body):
        key, rule, tags_text, source_ref, evidence = (part.strip() for part in match.groups())
        tags = [item.strip().strip("`'") for item in tags_text.split(",") if item.strip()]
        evolvable = key.startswith("NORM-EXP-")
        norm_records.append({
            "key": key,
            "id": key,
            "kind": "practice" if evolvable else "protected_constraint",
            "active": True,
            "protected": not evolvable,
            "evolvable": evolvable,
            "tags": tags,
            "text": rule,
            "source": source_ref,
            "evidence": evidence,
            "owner": "curated-norms",
            "replacement_policy": "same-stable-id-single-active" if evolvable else "human-decision-required",
        })
    return {
        "kind": "replace_source",
        "path": "NORMS.md",
        "archive_ref": "knowledge/v4-migration/archive/NORMS.md",
        "source_sha256": _sha_bytes(raw),
        "archive_content": text,
        "content": body,
        "norm_records": norm_records,
    }


def _stable_north_star(root: Path) -> Optional[dict]:
    source = root / "NORTH-STAR.md"
    if not source.is_file():
        return None
    raw = source.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    output: List[str] = []
    skip_section = False
    for line in lines:
        if line.startswith("## 当前纠偏基线"):
            output.extend([
                "## 动态路线与当前约束",
                "",
                "动态任务路线与旧路线不属于稳定北极星目标；迁移快照见 `knowledge/v4-migration/dynamic-constraints.md`，当前卡和 core context 是运行时入口。",
                "",
            ])
            skip_section = True
            continue
        if re.match(r"##\s+.*Go\s*/\s*No-Go", line):
            output.extend([
                "## 动态门禁引用",
                "",
                "卡号、分支和开工条件属于实例运行状态；迁移后以任务卡依赖、方向审阅卡和 `knowledge/v4-migration/dynamic-constraints.md` 为准。北极星只保留目标与生产真相层级，不重复当前路线菜单。",
                "",
            ])
            skip_section = True
            continue
        if skip_section and line.startswith("## "):
            skip_section = False
        if skip_section:
            continue
        if "JOB-SHAPES.md" in line and "菜单合同" in line:
            line = line.replace("`JOB-SHAPES.md`（菜单合同）", "`jobs/`（菜单由 core 从卡投影）")
        output.append(line)
    body = "\n".join(output).rstrip() + "\n"
    return {"kind": "replace_source", "path": "NORTH-STAR.md", "archive_ref": "knowledge/v4-migration/archive/NORTH-STAR.md", "source_sha256": _sha_bytes(raw), "archive_content": text, "content": body}


def _upgrade_instance(root: Path, direction_nodes: Sequence[Tuple[str, str, str]] = ()) -> Optional[dict]:
    path = root / "INSTANCE.json"
    if not path.is_file():
        return None
    raw = path.read_bytes()
    data = _read_json(path)
    old = raw.decode("utf-8", errors="replace")
    data["protocol_version"] = "4.0.0"
    data["required_reads"] = ["INSTANCE.json", "ENV.md", "NORTH-STAR.md", "CURRENT.md"]
    data["v4"] = {
        "cards_authoritative": True,
        "context_api": "sjs_v4.build_context",
        "direction_review_nodes": [dict(after=a, before=b, label=c) for a, b, c in direction_nodes],
    }
    new = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return {"kind": "upgrade_instance", "path": "INSTANCE.json", "archive_ref": "knowledge/v4-migration/archive/INSTANCE.json", "source_sha256": _sha_bytes(raw), "archive_content": old, "content": new}


def _upgrade_registry(root: Path) -> Optional[dict]:
    repo = _repo_root(root)
    registry = repo / ".serial-job-session" / "instances.json"
    if not registry.is_file():
        return None
    data = _read_json(registry)
    instance_path = root.resolve()
    target_id = None
    manifest = root / "INSTANCE.json"
    if manifest.is_file():
        target_id = _read_json(manifest).get("instance_id")
    if not target_id:
        raise MigrationError("registry update requires INSTANCE.json.instance_id")
    changed = False
    matched = 0
    for item in data.get("instances") or []:
        if not isinstance(item, dict):
            continue
        candidate_raw = str(item.get("instance_root") or "")
        candidate = (repo / candidate_raw).resolve() if not Path(candidate_raw).is_absolute() else Path(candidate_raw).resolve()
        # Match exactly one registry entry for this instance.  In particular,
        # do not upgrade another entry merely because its path has a suffix.
        if item.get("instance_id") == target_id and candidate == instance_path:
            item["protocol_version"] = "4.0.0"
            item["required_reads"] = ["INSTANCE.json", "ENV.md", "NORTH-STAR.md", "CURRENT.md"]
            item["v4"] = {"cards_authoritative": True, "context_api": "sjs_v4.build_context", "registry_scope": "this-instance"}
            changed = True
            matched += 1
    if not changed:
        return None
    if matched != 1:
        raise MigrationError("registry target match count is %s, expected 1" % matched)
    raw = registry.read_bytes()
    old = raw.decode("utf-8", errors="replace")
    new = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    return {
        "kind": "upgrade_registry",
        "path": str(registry.resolve()),
        "archive_ref": "knowledge/v4-migration/archive/instances.json",
        "source_sha256": _sha_bytes(raw),
        "archive_content": old,
        "content": new,
        "external_root": True,
        "registry_scope": "repository-instance-entry-only",
        "registry_target_id": target_id,
        "allowed_external_path": str(registry.resolve()),
    }


def _protocol_v4(root: Path) -> Optional[dict]:
    path = root / "protocol" / "PROTOCOL.md"
    if not path.is_file():
        return None
    raw = path.read_bytes()
    text = raw.decode("utf-8", errors="replace")
    body = """# 串行任务会话 v4 · 实例协议

本实例使用 `serial-job-session` v4。任务卡 Markdown 是唯一任务事实源；CURRENT、菜单、LOG、FACTS 与 context 由 core 从卡投影。ENV 只描述环境边界，NORTH-STAR 只描述稳定目标。

## 冷启动

读取 `INSTANCE.json`、`ENV.md`、`NORTH-STAR.md`、`CURRENT.md`，再调用 `sjs_v4.load_snapshot/build_context` 获取被点名卡摘要。只按卡内路径读取依赖与证据，不扫描 output。

## 路线

当前施工路线由卡依赖表达。v4 动态路线快照在 `knowledge/v4-migration/dynamic-constraints.md`；方向纠正节点由 INSTANCE 配置生成，需高级模型审阅并与人讨论后才通过。

## 写入

主会话是控制面写者；Worker 只写卡正文与证据。状态为 `ready/doing/awaiting_acceptance/accepted/cancelled`。问题未决保持 doing，小反馈追加结案补丁，重大上游影响先讨论。迁移、归档、投影和 registry 更新必须使用 core transaction，一次一个节点。

## 证据

output 路径保持原样，迁移只核对路径与 hash，不复制、移动或重写 output。原卡和控制文件只归档一次并回链。
"""
    return {"kind": "upgrade_protocol", "path": "protocol/PROTOCOL.md", "archive_ref": "knowledge/v4-migration/archive/PROTOCOL.md", "source_sha256": _sha_bytes(raw), "archive_content": text, "content": body}


def _direction_cards(root: Path, snapshot: Mapping[str, Any], config: Sequence[Tuple[str, str, str]]) -> List[dict]:
    if not config:
        return []
    cards = [
        card
        for card in (snapshot.get("cards") or [])
        if Path(str(card.get("path") or "")).name != "_template.md"
    ]
    by_shape = {str(card.get("shape")): card for card in cards}
    existing_shapes = {str(card.get("shape")) for card in cards if card.get("shape") is not None}
    next_shape = 100
    operations: List[dict] = []
    for after, before, label in config:
        while str(next_shape) in existing_shapes:
            next_shape += 1
        shape = next_shape
        next_shape += 1
        after_deps = [int(item) for item in re.split(r"[/,，、 ]+", after) if item.isdigit()]
        deps = after_deps[:1] or after_deps
        conditional_dependencies = after_deps[1:]
        decision_paths: List[dict] = []
        if after == "61/99":
            # 99 is only the 4m branch.  It must not become an unconditional
            # predecessor of the direction review when 61 chooses 8m.
            decision_paths = [
                {"when": "61=8m", "requires": []},
                {"when": "61=4m", "requires": [99]},
            ]
        card_id = "v4-direction-review-%s" % shape
        metadata = {
            "schema": CARD_SCHEMA,
            "id": card_id,
            "shape": shape,
            "estimate_group": "direction-review",
            "depends_on": deps,
            "faces": ["docs"],
            "actor": "agent",
            "human_accept": "human",
            "status": "ready",
            "attempt": 1,
            "estimate": 0.5,
            "estimate_total": 0.5,
            "estimate_source": "fresh",
            "direction_review": True,
            "direction_review_after": after,
            "requires_advanced_model": True,
            "before_shape": before,
            "gates_shape": before,
            "conditional_dependencies": conditional_dependencies,
            "conditional_dependency_mode": "decision_path" if decision_paths else None,
            "decision_paths": decision_paths,
            "precondition_shape": 61 if before == "62" else None,
            "precondition_result": "8m|4m" if before == "62" else None,
            "revision": 0,
        }
        sections = {
            "goal": "- 方向纠正节点：%s\n- 判断已完成与待做是否仍符合 NORTH-STAR；只交付判断、证据和建议，不施工。" % label,
            "dependencies": "- 上游完成节点：%s\n- 目标下游节点：%s\n- 需读取实例 NORTH-STAR、ENV 摘要、相关卡和证据。\n- 条件依赖：%s%s" % (
                after,
                before,
                ", ".join(map(str, conditional_dependencies)) or "无",
                "\n- 分支语义：61=8m 时无需 99；61=4m 时必须先完成 99。" if decision_paths else "",
            ),
            "progress": "- 必须由高级模型审阅，并和人讨论后记录通过/不通过。\n- 若目标或验收需变更，保持 doing 并等待决策。",
            "execution": "- 估分：0.5h；包含方向阅读、比较和讨论记录。\n- 缺失计时可写 untracked，不补造。",
            "artifacts": "- 证据：目标对齐矩阵、冲突项、建议路线。\n- 人类结论：待审。",
            "closure": "- 通过后才允许按建议推进；不自动开工。",
            "knowledge": "- 经验：方向审阅结论带证据沉淀。",
        }
        card = {"metadata": metadata, "sections": sections, "path": "jobs/%s.md" % card_id}
        new_text = core.render_card(card)
        operations.append({"kind": "create_ready_card", "path": "jobs/%s.md" % card_id, "card_id": card_id, "shape": shape, "status": "ready", "metadata": metadata, "sections": sections, "new_text": new_text, "direction_review": True, "after": after, "before": before, "gates_shape": before})
        existing_shapes.add(str(shape))
    return operations


def build_plan(instance: Any, *, direction_config: Optional[str] = None, include_output: bool = True) -> dict:
    root = _root(instance)
    if not root.is_dir():
        raise MigrationError("instance does not exist: %s" % root)
    snapshot = core.load_snapshot(root, include_text=False)
    cards = [
        card
        for card in (snapshot.get("cards") or [])
        if Path(str(card.get("path") or "")).name != "_template.md"
    ]
    operations: List[dict] = []
    existing_shapes = {str(card.get("shape")) for card in cards if card.get("shape") is not None}
    facts_by_job = _facts_by_job(root)
    log_events_by_job = _log_events_by_job(root)
    direction_nodes = _direction_config(root, direction_config)
    for card in cards:
        relative = str(card.get("path") or "")
        if not relative or Path(relative).name.startswith("_"):
            continue
        path = root / relative
        if not path.is_file():
            raise MigrationError("card disappeared while planning: %s" % relative)
        raw = path.read_bytes()
        metadata = card.get("metadata") if isinstance(card.get("metadata"), Mapping) else {}
        # An already v4 card is left intact; its source hash remains a check.
        if metadata.get("schema") == CARD_SCHEMA and all(key in (card.get("sections") or {}) for key in ("goal", "dependencies")):
            continue
        operations.append(_migrate_card(root, relative, raw, card, facts_by_job=facts_by_job, log_events_by_job=log_events_by_job))

    menu_text = (root / "JOB-SHAPES.md").read_text(encoding="utf-8", errors="replace") if (root / "JOB-SHAPES.md").is_file() else ""
    ready_rows = _parse_table_rows(menu_text, "待做") + _parse_table_rows(menu_text, "条件分支")
    for row in ready_rows:
        row["_detail"] = _shape_detail(menu_text, row.get("shape"))
        operation = _ready_card_operation(row, root, existing_shapes)
        if operation:
            operations.append(operation)
            existing_shapes.add(str(operation.get("shape")))

    directions = _direction_cards(root, snapshot, direction_nodes)
    operations.extend(directions)
    direction_by_before = {str(item.get("before")): item for item in directions if item.get("before")}
    for operation in operations:
        if operation.get("kind") != "create_ready_card" or operation.get("direction_review"):
            continue
        gate = direction_by_before.get(str(operation.get("shape")))
        if not gate:
            continue
        metadata = operation.get("metadata") if isinstance(operation.get("metadata"), Mapping) else {}
        metadata = dict(metadata)
        dependencies = _as_list(metadata.get("depends_on"))
        if gate.get("shape") not in dependencies:
            dependencies.append(gate.get("shape"))
        metadata["depends_on"] = dependencies
        sections = dict(operation.get("sections") or {})
        sections["dependencies"] = str(sections.get("dependencies") or "").rstrip() + "\n- 方向纠正门禁：shape %s（高级模型+人讨论通过后才可继续）。" % gate.get("shape")
        card = {"metadata": metadata, "sections": sections, "path": operation.get("path")}
        operation["metadata"] = metadata
        operation["sections"] = sections
        operation["new_text"] = core.render_card(card)
    for operation in operations:
        if operation.get("kind") == "rewrite_card":
            archive_path = root / str(operation["archive_ref"])
            if archive_path.exists():
                existing_hash = _sha_bytes(archive_path.read_bytes())
                if existing_hash != operation["archive_sha256"]:
                    raise MigrationError("archive exists with different content: %s" % operation["archive_ref"])

    file_operations: List[dict] = []
    # JOB-SHAPES/CURRENT/LOG/FACTS are projections after migration.  Archive
    # their complete legacy bytes before core regenerates them from cards;
    # PLAN remains a human planning record and is archived for traceability.
    for archive in _projection_archives(root):
        operations.append(archive)
        file_operations.append({
            "op": "write_file",
            "path": archive["path"],
            "content": archive["content"],
            "if_absent_or_same_sha256": archive["source_sha256"],
        })
    for operation in operations:
        if operation["kind"] == "rewrite_card":
            file_operations.append({"op": "write_file", "path": operation["archive_ref"], "content": operation["archive_content"], "if_absent_or_same_sha256": operation["archive_sha256"]})
            file_operations.append({"op": "replace_card", "path": operation["path"], "card_id": operation["card_id"], "metadata": operation["metadata"], "sections": operation["sections"], "source_sha256": operation["source_sha256"]})
        elif operation["kind"] == "create_ready_card":
            file_operations.append({"op": "create_card", "path": operation["path"], "card_id": operation["card_id"], "metadata": operation["metadata"], "sections": operation["sections"]})
    # Control-plane migration is part of the same transaction.  The
    # source files are archived once, then replaced with the effective v4 view;
    # other instances simply omit files that do not exist.
    control_operations = [
        _constraint_snapshot(root),
        _effective_norms(root),
        _stable_north_star(root),
        _upgrade_instance(root, direction_nodes),
        _protocol_v4(root),
        _upgrade_registry(root),
        _search_ignore(root),
    ]
    norms_control = next((item for item in control_operations if item and item.get("kind") == "replace_source" and item.get("path") == "NORMS.md"), None)
    norm_records = list(norms_control.get("norm_records") or []) if norms_control else []
    if norm_records:
        # The static NORMS file is the human-readable protected baseline.  The
        # core needs the same records on a card so context and later norm
        # operations have one stable-id owner and can retire practices.
        owner_operation = next(
            (
                item
                for item in operations
                if item.get("kind") == "rewrite_card"
                and (str(item.get("shape")) == "6" or str(item.get("card_id")) == "2026-09-16_norms-v2-freeze")
            ),
            None,
        )
        if owner_operation is None:
            raise MigrationError("cannot seed curated NORMS: shape 6 card was not migrated")
        metadata = dict(owner_operation.get("metadata") or {})
        metadata["norms"] = copy.deepcopy(norm_records)
        metadata["norms_owner"] = True
        sections = dict(owner_operation.get("sections") or {})
        sections["knowledge"] = (
            str(sections.get("knowledge") or "").rstrip()
            + "\n\n- v4 规范投影：静态 protected_constraint 与可演进 practice 均以 metadata.norms 的 stable id 为唯一有效记录；NORMS.md 是同一批记录的人读索引。"
        ).strip()
        owner_operation["metadata"] = metadata
        owner_operation["sections"] = sections
        owner_operation["norms"] = copy.deepcopy(norm_records)
        owner_operation["new_text"] = core.render_card({"metadata": metadata, "sections": sections, "path": owner_operation.get("path")})
        # ``file_operations`` is the transaction source consumed by core;
        # updating only the human-readable operation above would make the
        # plan appear seeded while the applied card silently lost norms.
        owner_file_operation = next(
            (
                item
                for item in file_operations
                if item.get("op") == "replace_card"
                and str(item.get("card_id")) == str(owner_operation.get("card_id"))
            ),
            None,
        )
        if owner_file_operation is None:
            raise MigrationError("norm owner is missing from transaction file operations")
        owner_file_operation["metadata"] = copy.deepcopy(metadata)
        owner_file_operation["sections"] = copy.deepcopy(sections)
    for control in control_operations:
        if not control:
            continue
        operations.append(control)
        archive_ref = control.get("archive_ref")
        archive_content = control.get("archive_content")
        if archive_ref and archive_content is not None:
            file_operations.append({"op": "write_file", "path": archive_ref, "content": archive_content, "if_absent_or_same_sha256": control.get("source_sha256")})
        if control.get("kind") == "replace_source":
            target_path = control.get("path")
            file_operations.append({"op": "write_file", "path": target_path, "content": control.get("content", ""), "if_absent_or_same_sha256": control.get("source_sha256")})
        elif control.get("kind") in {"upgrade_instance", "upgrade_protocol", "upgrade_registry"}:
            target_path = control.get("path")
            operation = {
                "op": "write_file",
                "path": target_path,
                "content": control.get("content", ""),
                "if_absent_or_same_sha256": control.get("source_sha256"),
                "external_root": bool(control.get("external_root")),
            }
            if control.get("external_root"):
                operation["registry_scope"] = control.get("registry_scope")
                operation["allowed_external_path"] = control.get("allowed_external_path")
            file_operations.append(operation)
        elif control.get("kind") in {"write_constraint_snapshot", "write_norms_snapshot"}:
            file_operations.append({"op": "write_file", "path": control.get("path"), "content": control.get("content", ""), "if_absent_or_same_sha256": None})
        elif control.get("kind") == "write_search_ignore":
            file_operations.append({
                "op": "write_file",
                "path": control.get("path"),
                "content": control.get("content", ""),
                "if_absent_or_same_sha256": control.get("source_sha256"),
            })

    output_manifest = _output_manifest(root) if include_output else []
    old_statuses = [_raw_status(card) for card in cards]
    summary = {
        "cards": len(cards),
        "accepted": sum(1 for status in old_statuses if status == "accepted"),
        "cancelled": sum(1 for status in old_statuses if status in {"rejected", "cancelled"}),
        "ready_to_create": sum(1 for item in operations if item.get("kind") == "create_ready_card"),
        "direction_reviews": sum(1 for item in operations if item.get("direction_review")),
        "rewrites": sum(1 for item in operations if item.get("kind") == "rewrite_card"),
        "output_files": len(output_manifest),
        "output_bytes": sum(int(item.get("bytes") or 0) for item in output_manifest),
    }
    plan = {
        "schema": MIGRATION_SCHEMA,
        "generated_at": _now(),
        "instance_root": str(root),
        "source_protocol": str((snapshot.get("instance") or {}).get("protocol_version") or "v3"),
        "base_revision": int(snapshot.get("revision") or 0),
        "source_snapshot": {"revision": snapshot.get("revision"), "card_count": len(cards), "files": snapshot.get("files") or {}},
        "output_manifest": output_manifest,
        "operations": operations,
        "file_operations": file_operations,
        "summary": summary,
        "direction_nodes": [dict(after=after, before=before, label=label) for after, before, label in direction_nodes],
    }
    return plan


def _strip_plan_for_json(plan: Mapping[str, Any]) -> dict:
    # Keep plan self-contained for apply.  ``archive_content`` and ``new_text``
    # are intentional transaction inputs, not a second authoritative database.
    return dict(plan)


def _apply_core_transaction(root: Path, plan: Mapping[str, Any], command_id: str) -> dict:
    expected_revision = int(plan.get("base_revision") or 0)
    repo = _repo_root(root).resolve()
    allowed_registry = (repo / ".serial-job-session" / "instances.json").resolve()
    file_operations = list(plan.get("file_operations") or [])
    for operation in file_operations:
        if not isinstance(operation, Mapping) or not operation.get("external_root"):
            continue
        candidate = Path(str(operation.get("path") or "")).expanduser()
        candidate = (repo / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        if candidate != allowed_registry:
            raise MigrationError("migration external target is outside repository registry: %s" % operation.get("path"))
        if operation.get("registry_scope") not in {None, "repository-instance-entry-only"}:
            raise MigrationError("unsupported external registry scope: %s" % operation.get("registry_scope"))
    command = {
        "command_id": command_id,
        "expected_revision": expected_revision,
        "operations": file_operations,
        "file_operations": file_operations,
        "migration_schema": MIGRATION_SCHEMA,
        "expected_output_manifest": plan.get("output_manifest") or [],
        # The core independently enforces the same narrow exception.  Keeping
        # this explicit in the command prevents a future adapter from turning
        # an absolute path in a plan into a general file writer.
        "allowed_external_paths": [str(allowed_registry)],
        "repository_root": str(repo),
    }
    apply_migration = getattr(core, "apply_migration", None)
    if callable(apply_migration):
        return apply_migration(root, command)
    apply_transaction = getattr(core, "apply_transaction", None)
    if callable(apply_transaction):
        return apply_transaction(root, command)
    apply_command = getattr(core, "apply_command", None)
    if callable(apply_command):
        # Current core versions that support migration advertise this capability
        # explicitly; do not silently send unsupported write_file operations to a
        # card-only transaction implementation.
        capabilities = getattr(core, "MIGRATION_CAPABILITIES", ())
        if "file_operations" not in capabilities and "replace_card" not in capabilities:
            raise MigrationError("core lacks migration transaction capability (replace_card/write_file)")
        return apply_command(root, command)
    raise MigrationError("core has no public migration transaction function")


def _verify_output(root: Path, expected: Sequence[Mapping[str, Any]]) -> dict:
    actual = _output_manifest(root)
    expected_norm = [{"path": item.get("path"), "bytes": int(item.get("bytes") or 0), "sha256": item.get("sha256")} for item in expected]
    actual_norm = [{"path": item.get("path"), "bytes": int(item.get("bytes") or 0), "sha256": item.get("sha256")} for item in actual]
    return {"ok": expected_norm == actual_norm, "expected_files": len(expected_norm), "actual_files": len(actual_norm), "expected_bytes": sum(item["bytes"] for item in expected_norm), "actual_bytes": sum(item["bytes"] for item in actual_norm)}


def _load_plan(path: Path) -> dict:
    plan = _read_json(path)
    if plan.get("schema") != MIGRATION_SCHEMA:
        raise MigrationError("unsupported plan schema: %s" % plan.get("schema"))
    return plan


def _default_plan_path(root: Path) -> Path:
    return root.parent / ".serial-job-session" / "private" / "v4-upgrade-20260920" / ("plan-%s.json" % _safe_id(root.name))


def _cmd_dry_run(args: argparse.Namespace) -> int:
    root = _root(args.instance)
    plan = build_plan(root, direction_config=args.direction_config, include_output=not args.no_output_manifest)
    plan_path = Path(args.plan).expanduser().resolve() if args.plan else _default_plan_path(root)
    _json_write(plan_path, _strip_plan_for_json(plan))
    result = {"ok": True, "mode": "dry-run", "plan": str(plan_path), "summary": plan["summary"], "base_revision": plan["base_revision"], "output_check": _verify_output(root, plan.get("output_manifest") or [])}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    root = _root(args.instance)
    plan_path = Path(args.plan).expanduser().resolve() if args.plan else _default_plan_path(root)
    plan = _load_plan(plan_path)
    if str(_root(plan.get("instance_root"))) != str(root):
        raise MigrationError("plan instance_root differs from --instance")
    before = _verify_output(root, plan.get("output_manifest") or [])
    if not before["ok"]:
        raise MigrationError("output evidence drift before apply: %s" % before)
    command_id = str(args.transaction or "v4-migrate-%s" % datetime.now().strftime("%Y%m%d-%H%M%S"))
    result = _apply_core_transaction(root, plan, command_id)
    after = _verify_output(root, plan.get("output_manifest") or [])
    if not after["ok"]:
        raise MigrationError("output evidence changed during apply: %s" % after)
    print(json.dumps({"ok": True, "mode": "apply", "transaction": command_id, "core": result, "output_check": after, "summary": plan.get("summary")}, ensure_ascii=False, indent=2))
    return 0


def _cmd_recover_or_rollback(args: argparse.Namespace, action: str) -> int:
    root = _root(args.instance)
    command_id = args.transaction
    recover = getattr(core, "recover_transaction", None)
    if not callable(recover):
        raise MigrationError("core lacks recover_transaction")
    result = recover(root, command_id=command_id, action=action)
    print(json.dumps({"ok": True, "mode": action, "transaction": command_id, "core": result}, ensure_ascii=False, indent=2))
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    dry = sub.add_parser("dry-run", help="build a deterministic plan without changing the instance")
    dry.add_argument("--instance", required=True)
    dry.add_argument("--plan")
    dry.add_argument("--direction-config")
    dry.add_argument("--no-output-manifest", action="store_true")
    apply = sub.add_parser("apply", help="apply a previously reviewed plan through core transaction")
    apply.add_argument("--instance", required=True)
    apply.add_argument("--plan")
    apply.add_argument("--transaction")
    for name in ("recover", "rollback"):
        command = sub.add_parser(name)
        command.add_argument("--instance", required=True)
        command.add_argument("--transaction")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.mode == "dry-run":
            return _cmd_dry_run(args)
        if args.mode == "apply":
            return _cmd_apply(args)
        if args.mode == "recover":
            return _cmd_recover_or_rollback(args, "resume")
        if args.mode == "rollback":
            return _cmd_recover_or_rollback(args, "rollback")
        raise MigrationError("unknown mode: %s" % args.mode)
    except (MigrationError, getattr(core, "SJSV4Error", RuntimeError)) as exc:
        print(json.dumps({"ok": False, "mode": getattr(args, "mode", None), "error": str(exc), "code": getattr(exc, "code", "MIGRATION_ERROR")}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
