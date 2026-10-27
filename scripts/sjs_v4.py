#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""serial-job-session v4 core.

Public Python contract (kept deliberately small so adapters can depend on it):

``load_snapshot(instance, *, max_chars=12000, include_text=False) -> dict``
    Reads an instance once and returns ``schema``, ``revision``, ``instance``,
    ``cards`` (a list of card dictionaries), ``cards_by_id``, ``files``
    (relative path/hash records), and bounded ``views``.  A card has flat
    ``metadata`` and stable ``sections``; common fields are also available at
    the card top level for callers that do not want to unwrap metadata.

``parse_card_text(text, path=None) -> dict`` / ``render_card(card) -> str``
    Parse/render the v4 flat metadata block and stable Markdown sections.  The
    parser also accepts a fenced YAML block, which makes migration of existing
    cards lossless enough for a later explicit migration.

``render_projections(snapshot) -> {relative_path: text}``
    Deterministically renders ``JOB-SHAPES.md``, ``CURRENT.md``, ``LOG.md``
    and ``FACTS.md`` from cards.  ``SOURCE-PLAN.md`` is never a generated or
    mutable target.

``apply_command(instance, command) -> dict``
    Applies one command containing ``command_id``, ``expected_revision`` and
    ``operation``/``operations``/``changes``.  It uses per-instance CAS/hash
    guards, an exclusive lease, atomic replacement, durable backups and a
    journal.  Replaying a committed command id is idempotent.

``check_instance(instance) -> dict`` / ``check_snapshot(snapshot) -> dict``
    Read-only consistency checks.  Missing derived views are rebuildable
    warnings; missing timing is non-blocking and excluded from calibration.

Adapters may provide ``sjs_v4_context.py``, ``sjs_v4_time.py`` and
``sjs_v4_migrate.py`` next to this module.  The calls are
``build_context(card, snapshot)``, ``build_time_record(...)``, and
``migrate_instance(instance, mode, **kwargs)``.  Context/time adapters are
part of the v4 contract; a missing adapter is reported explicitly rather than
silently changing the meaning of a command.

The source of truth is the card Markdown.  Runtime journals/staged files are
crash-recovery material only; no extra state database is required for normal
operation.
"""

from __future__ import annotations

import ast
import base64
import copy
import hashlib
import importlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


SCHEMA = "serial-job-session-v4"
CARD_SCHEMA = "serial-job-session-card/v4"
CURRENT_SCHEMA = "serial-job-session-current/v4"
TX_SCHEMA = "serial-job-session-transaction/v4"
MIGRATION_CAPABILITIES = ("file_operations", "replace_card", "write_file", "create_card")
ALLOWED_STATUSES = ("ready", "doing", "awaiting_acceptance", "accepted", "cancelled")
DERIVED_FILES = ("JOB-SHAPES.md", "CURRENT.md", "LOG.md", "FACTS.md")
# Cards have seven stable parent chapters.  Operational events that used to
# be emitted as extra top-level chapters are rendered as ``###`` subsections
# below the appropriate parent.  Keeping this boundary here (rather than in
# the migration adapter) means a question, feedback entry, or patch can be
# appended and re-rendered without changing the card's shape.
SECTION_ORDER = (
    "goal",
    "dependencies",
    "progress",
    "execution",
    "artifacts",
    "closure",
    "knowledge",
)
SECTION_PARENT = {
    "goal": "goal",
    "dependencies": "dependencies",
    "progress": "progress",
    "questions": "progress",
    "decisions": "progress",
    "execution": "execution",
    "artifacts": "artifacts",
    "acceptance": "artifacts",
    "feedback": "artifacts",
    "closure": "closure",
    "patches": "closure",
    "knowledge": "knowledge",
}
SUBSECTION_ORDER = (
    "questions",
    "decisions",
    "acceptance",
    "feedback",
    "patches",
)
META_ORDER = (
    "schema",
    "id",
    "shape",
    "phase_id",
    "estimate_group",
    "depends_on",
    "faces",
    "actor",
    "human_accept",
    "status",
    "revision",
    "attempt",
    "created_at",
    "started_at",
    "closed_at",
    "accepted_at",
    "cancelled_at",
    "artifact_version",
    "validated_artifact_version",
    "artifact_valid",
    "pending_verification",
    "last_command_id",
)


class SJSV4Error(RuntimeError):
    """A structured v4 operation error."""

    def __init__(self, code: str, message: Optional[str] = None, **details: Any) -> None:
        self.code = str(code)
        self.details = details
        super().__init__(message or self.code)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _instance_path(instance: Any) -> Path:
    if isinstance(instance, Path):
        return instance.expanduser().resolve()
    return Path(str(instance)).expanduser().resolve()


def _repo_root(start: Path) -> Path:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists() or (candidate / ".serial-job-session").exists():
            return candidate
    return current


def _instance_id(root: Path, instance_data: Optional[Mapping[str, Any]] = None) -> str:
    data = instance_data or {}
    value = str(data.get("instance_id") or "").strip()
    if value:
        return value
    return hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:24]


def _private_root(root: Path, instance_data: Optional[Mapping[str, Any]] = None) -> Path:
    return _repo_root(root) / ".serial-job-session" / "private" / "sjs-v4" / _instance_id(root, instance_data)


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha_text(text: str) -> str:
    return _sha_bytes(text.encode("utf-8"))


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes() if path.is_file() else b""


def _file_record(root: Path, relative: str, include_text: bool = False, max_chars: int = 12000) -> dict:
    path = root / relative
    exists = path.is_file()
    raw = _read_bytes(path) if exists else b""
    record: Dict[str, Any] = {
        "path": relative.replace("\\", "/"),
        "exists": exists,
        "sha256": _sha_bytes(raw) if exists else None,
        "bytes": len(raw) if exists else 0,
    }
    if include_text and exists:
        text = raw.decode("utf-8", errors="replace")
        record["text"] = text[:max_chars]
        record["truncated"] = len(text) > max_chars
    return record


def _strip_inline_comment(value: str) -> str:
    quote: Optional[str] = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote:
            escaped = True
            continue
        if char in ("'", '"'):
            if quote == char:
                quote = None
            elif quote is None:
                quote = char
            continue
        if char == "#" and quote is None and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.strip()


def _parse_scalar(raw: str) -> Any:
    value = _strip_inline_comment(str(raw).strip())
    if not value:
        return None
    lowered = value.lower()
    if lowered in {"null", "none", "~"}:
        return None
    if lowered in {"true", "yes", "on"}:
        return True
    if lowered in {"false", "no", "off"}:
        return False
    if value.startswith(("[", "{")) and value.endswith(("]", "}")):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            try:
                return ast.literal_eval(value)
            except (SyntaxError, ValueError):
                pass
    if (len(value) >= 2) and value[0] == value[-1] and value[0] in ("'", '"'):
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError):
            return value[1:-1]
    if re.match(r"^-?\d+$", value):
        try:
            return int(value)
        except ValueError:
            pass
    if re.match(r"^-?(?:\d+\.\d*|\d*\.\d+)(?:[eE][+-]?\d+)?$", value):
        try:
            return float(value)
        except ValueError:
            pass
    if "," in value and not any(ch in value for ch in "{}[]"):
        return [part.strip() for part in value.split(",") if part.strip()]
    return value


def _dump_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    text = str(value)
    if not text:
        return '""'
    if re.match(r"^[A-Za-z0-9_./+@=-]+$", text) and text.lower() not in {
        "null",
        "none",
        "true",
        "false",
    }:
        return text
    return json.dumps(text, ensure_ascii=False)


def _parse_metadata_block(block: str) -> dict:
    result: Dict[str, Any] = {}
    pending_key: Optional[str] = None
    pending_items: List[Any] = []
    for raw_line in block.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        # Accept a simple indented list for migration input; canonical output is inline.
        stripped = line.strip()
        if pending_key and stripped.startswith("-") and ":" not in stripped:
            pending_items.append(_parse_scalar(stripped[1:].strip()))
            continue
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*:\s*(.*)$", line)
        if not match:
            continue
        if pending_key:
            result[pending_key] = pending_items
            pending_key = None
            pending_items = []
        key, value = match.groups()
        if value.strip():
            result[key] = _parse_scalar(value)
        else:
            pending_key = key
            pending_items = []
    if pending_key:
        result[pending_key] = pending_items
    return result


def _section_key(title: str) -> str:
    raw = str(title or "").strip()
    compact = re.sub(r"[\s_./\\-]+", "", raw).lower()
    aliases = {
        # The migration adapter uses these seven Chinese parent titles.  Keep
        # the parser bilingual so a copied historical card can be rendered by
        # the same core without losing its sections.
        "目标范围验收": "goal",
        "依赖输入影响": "dependencies",
        "进展问题决策": "progress",
        "执行耗时": "execution",
        "产物验证反馈": "artifacts",
        "结案补丁": "closure",
        "事实经验变化": "knowledge",
        "target": "goal",
        "goal_scope_acceptance": "goal",
        "goalscopeacceptance": "goal",
        "dependency_inputs_downstream": "dependencies",
        "dependencyinputsdownstream": "dependencies",
        "current_progress_questions_decisions": "progress",
        "currentprogressquestionsdecisions": "progress",
        "execution_and_time": "execution",
        "executionandtime": "execution",
        "artifacts_verification": "artifacts",
        "artifactsverification": "artifacts",
        "closure_and_follow_up": "closure",
        "closureandfollowup": "closure",
        "knowledge_changes": "knowledge",
        "knowledgechanges": "knowledge",
        "human_feedback": "feedback",
        "goal": "goal",
        "dependencies": "dependencies",
        "progress": "progress",
        "questions": "questions",
        "question": "questions",
        "decisions": "decisions",
        "decision": "decisions",
        "execution": "execution",
        "artifacts": "artifacts",
        "artifact": "artifacts",
        "acceptance": "acceptance",
        "feedback": "feedback",
        "closure": "closure",
        "patches": "patches",
        "patch": "patches",
        "knowledge": "knowledge",
        "facts": "knowledge",
        "norms": "knowledge",
    }
    if compact in aliases:
        return aliases[compact]
    key = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    return aliases.get(key, key or "section")


def _extract_sections(body: str) -> Dict[str, str]:
    # Match exactly level-two headings.  ``^##\s`` is too permissive for
    # nested ``### Questions`` on some legacy inputs and leaks child headings
    # into the public section map.
    matches = list(re.finditer(r"^##(?!#)\s+(.+?)\s*$", body, flags=re.M))
    sections: Dict[str, str] = {}
    if not matches:
        if body.strip():
            sections["body"] = body.strip()
        return sections
    prefix = body[: matches[0].start()].strip()
    intro_text = ""
    if prefix:
        # Canonical cards begin with ``# Task <id>`` before their sections.
        # Treat that generated title as framing, not as an editable intro, so
        # parse(render(card)) is stable and does not accumulate an Intro block.
        prefix_lines = prefix.splitlines()
        if prefix_lines and re.match(r"^#\s+Task(?:\s|$)", prefix_lines[0].strip(), flags=re.I):
            prefix_lines = prefix_lines[1:]
        prefix = "\n".join(prefix_lines).strip()
        if prefix:
            intro_text = prefix
    def put(key: str, value: str) -> None:
        value = str(value or "").strip()
        if not value:
            return
        if key in sections:
            suffix = 2
            while "%s_%s" % (key, suffix) in sections:
                suffix += 1
            key = "%s_%s" % (key, suffix)
        sections[key] = value

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        key = _section_key(match.group(1))
        block = body[match.end() : end]
        # A legacy card may have ``## Acceptance`` or ``## Questions`` as a
        # top-level section.  Normalize that title into its seven parent and
        # retain the original semantic title as a child heading.
        if key not in SECTION_ORDER:
            parent = SECTION_PARENT.get(key, "progress")
            child = key.replace("_", " ").strip().title()
            put(parent, "### %s\n%s" % (child, block.strip()))
            continue
        # Child headings remain embedded in their parent body.  The helper
        # ``_section_value`` exposes a child to adapters without making it a
        # second top-level source section.
        put(key, block)
    if intro_text:
        put("goal", "### Intro\n%s" % intro_text)
    # Keep the public map within the seven-parent contract.  Empty chapters
    # need no map entry; ``render_card`` still emits their stable headings.
    return sections


def _metadata_and_body(text: str) -> Tuple[dict, str, str]:
    source = text.replace("\r\n", "\n")
    if source.startswith("---"):
        match = re.match(r"^---\s*\n(.*?)(?:\n)?---\s*\n?(.*)$", source, flags=re.S)
        if match:
            return _parse_metadata_block(match.group(1)), match.group(2), "frontmatter"
    fence = re.search(r"```ya?ml\s*\n(.*?)(?:\n)?```", source, flags=re.I | re.S)
    if fence:
        prefix = source[: fence.start()]
        suffix = source[fence.end() :]
        return _parse_metadata_block(fence.group(1)), prefix + suffix, "fenced"
    return {}, source, "none"


def _canonical_meta(raw: Mapping[str, Any], path: Optional[str] = None) -> dict:
    meta: Dict[str, Any] = {}
    for key, value in raw.items():
        if key in {"metadata", "sections", "path", "hash", "sha256", "text", "body", "source_format"}:
            continue
        meta[str(key)] = copy.deepcopy(value)
    meta.setdefault("schema", CARD_SCHEMA)
    if path and not meta.get("id"):
        meta["id"] = Path(path).stem
    if "depends_on" in meta and meta["depends_on"] is None:
        meta["depends_on"] = []
    if "faces" in meta and meta["faces"] is None:
        meta["faces"] = []
    if "revision" in meta:
        try:
            meta["revision"] = int(meta["revision"])
        except (TypeError, ValueError):
            meta["revision"] = 0
    else:
        meta["revision"] = 0
    if "attempt" in meta:
        try:
            meta["attempt"] = int(meta["attempt"])
        except (TypeError, ValueError):
            meta["attempt"] = 1
    else:
        meta["attempt"] = 1
    if "status" in meta and meta["status"] == "running":
        # v3 cards are read compatibly; v4 writes use doing.
        meta["legacy_status"] = "running"
        meta["status"] = "doing"
    return meta


def parse_card_text(text: str, path: Optional[str] = None) -> dict:
    """Parse one card into the stable public card shape."""
    metadata, body, source_format = _metadata_and_body(text)
    meta = _canonical_meta(metadata, path)
    sections = _extract_sections(body)
    card: Dict[str, Any] = {
        "metadata": meta,
        "sections": sections,
        "path": str(path).replace("\\", "/") if path else None,
        "source_format": source_format,
        "body": body,
    }
    card.update(meta)
    card["id"] = str(meta.get("id") or (Path(path).stem if path else ""))
    card["status"] = str(meta.get("status") or "ready")
    card["revision"] = int(meta.get("revision") or 0)
    card["shape"] = meta.get("shape")
    return card


def parse_card(path: Any) -> dict:
    target = Path(path)
    return parse_card_text(target.read_text(encoding="utf-8"), target.as_posix())


def _ordered_meta(meta: Mapping[str, Any]) -> List[Tuple[str, Any]]:
    known = [key for key in META_ORDER if key in meta]
    extra = sorted(key for key in meta if key not in set(known) and not str(key).startswith("_") )
    return [(key, meta[key]) for key in known + extra]


def render_card(card: Mapping[str, Any]) -> str:
    """Render a card deterministically with flat metadata and stable sections."""
    raw_meta = card.get("metadata") if isinstance(card.get("metadata"), Mapping) else card
    meta = _canonical_meta(raw_meta or {}, card.get("path") if isinstance(card.get("path"), str) else None)
    meta["schema"] = CARD_SCHEMA
    sections_raw = card.get("sections") if isinstance(card.get("sections"), Mapping) else {}
    sections = _normalise_sections(sections_raw)
    lines = ["---"]
    for key, value in _ordered_meta(meta):
        lines.append("%s: %s" % (key, _dump_scalar(value)))
    lines.extend(["---", "", "# Task %s" % (meta.get("id") or "") , ""])
    # Always emit the seven parent chapters.  This makes the card outline
    # stable across lifecycle updates; semantic additions are child headings.
    for parent in SECTION_ORDER:
        title = parent.replace("_", " ").strip().title()
        lines.extend(["## %s" % title])
        value = sections.get(parent)
        if value:
            lines.extend([value, ""])
        else:
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _card_record(root: Path, relative: str, raw: bytes, max_chars: int, include_text: bool) -> dict:
    text = raw.decode("utf-8", errors="replace")
    card = parse_card_text(text, relative)
    card["sha256"] = _sha_bytes(raw)
    card["bytes"] = len(raw)
    card["path"] = relative.replace("\\", "/")
    if include_text:
        card["text"] = text[:max_chars]
        card["truncated"] = len(text) > max_chars
    # Keep the source body bounded by default; applying operations rereads the exact file.
    if not include_text:
        card.pop("body", None)
    return card


def _metadata_from_view(text: str) -> dict:
    data, _, _ = _metadata_and_body(text)
    return data


def load_snapshot(instance: Any, *, max_chars: int = 12000, include_text: bool = False) -> dict:
    """Collect an instance once; callers should pass this snapshot to checks/renderers."""
    root = _instance_path(instance)
    instance_data: Dict[str, Any] = {}
    manifest = root / "INSTANCE.json"
    if manifest.is_file():
        try:
            loaded = json.loads(manifest.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                instance_data = loaded
        except (OSError, ValueError):
            instance_data = {}
    file_records: Dict[str, dict] = {}
    cards: List[dict] = []
    jobs_dir = root / "jobs"
    if jobs_dir.is_dir():
        # ``jobs/_template.md`` is a scaffold, never an authoritative card.
        # Keep the same exclusion as the v3 validator so it cannot create a
        # phantom ready card with a non-numeric ``{shape}`` placeholder.
        paths = sorted(
            path for path in jobs_dir.glob("*.md")
            if path.is_file() and not path.name.startswith("_")
        )
    else:
        paths = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        raw = path.read_bytes()
        file_records[relative] = {
            "path": relative,
            "exists": True,
            "sha256": _sha_bytes(raw),
            "bytes": len(raw),
        }
        cards.append(_card_record(root, relative, raw, max_chars, include_text))
    views: Dict[str, dict] = {}
    for relative in DERIVED_FILES:
        record = _file_record(root, relative, include_text=include_text, max_chars=max_chars)
        views[relative] = record
        file_records[relative] = record
    current_text = ""
    current_record = views.get("CURRENT.md") or {}
    if current_record.get("exists"):
        current_text = (root / "CURRENT.md").read_text(encoding="utf-8", errors="replace")
    current_meta = _metadata_from_view(current_text) if current_text else {}
    revision_candidates = [int(card.get("revision") or 0) for card in cards]
    try:
        current_revision = int(current_meta.get("revision") or 0)
    except (TypeError, ValueError):
        current_revision = 0
    revision = max([current_revision] + revision_candidates + [0])
    cards.sort(key=lambda card: (_shape_sort(card.get("shape")), str(card.get("id") or "")))
    by_id = {str(card.get("id")): card for card in cards if card.get("id")}
    snapshot: Dict[str, Any] = {
        "schema": SCHEMA,
        "instance_root": str(root),
        "instance": instance_data,
        "revision": revision,
        "cards": cards,
        "cards_by_id": by_id,
        "files": file_records,
        "views": views,
        "derived": {"paths": list(DERIVED_FILES), "missing": [key for key, item in views.items() if not item.get("exists")]},
        "warnings": [],
    }
    if not instance_data:
        snapshot["warnings"].append("INSTANCE_MANIFEST_MISSING")
    if not cards:
        snapshot["warnings"].append("NO_CARDS")
    return snapshot


def _shape_sort(value: Any) -> Tuple[int, str]:
    if isinstance(value, bool):
        return (10**9, str(value))
    try:
        return (int(value), "")
    except (TypeError, ValueError):
        return (10**9, str(value or ""))


def _card_from_snapshot(snapshot: Mapping[str, Any], card_id: Optional[str] = None, shape: Any = None) -> Optional[dict]:
    if card_id:
        found = (snapshot.get("cards_by_id") or {}).get(str(card_id))
        if found:
            return found
    cards = snapshot.get("cards") or []
    if shape is not None:
        for card in cards:
            if str(card.get("shape")) == str(shape):
                return card
    return None


def _section_value(card: Mapping[str, Any], *keys: str) -> str:
    sections = card.get("sections") if isinstance(card.get("sections"), Mapping) else {}
    for key in keys:
        value = sections.get(key)
        if value:
            return str(value)
        child = str(key)
        parent = SECTION_PARENT.get(child)
        if parent and sections.get(parent):
            body = str(sections[parent])
            title = child.replace("_", " ").strip().title()
            match = re.search(r"^###\s+%s\s*$" % re.escape(title), body, flags=re.I | re.M)
            if match:
                tail = body[match.end():]
                next_child = re.search(r"^###\s+", tail, flags=re.M)
                if next_child:
                    tail = tail[: next_child.start()]
                if tail.strip():
                    return tail.strip()
            # Legacy seven-section cards often put acceptance/feedback prose
            # directly in Artifacts.  Keep that content visible to adapters
            # when no explicit child marker exists.
            if child in {"acceptance", "feedback"}:
                return body
    return ""


def _status(card: Mapping[str, Any]) -> str:
    status = str(card.get("status") or (card.get("metadata") or {}).get("status") or "ready")
    return "doing" if status == "running" else status


def artifact_is_valid(card: Mapping[str, Any]) -> bool:
    """Return whether a historical acceptance is eligible for a new dependency."""
    meta = card.get("metadata") if isinstance(card.get("metadata"), Mapping) else card
    if _status(card) != "accepted":
        return False
    if meta.get("artifact_valid") is not True:
        return False
    version = meta.get("artifact_version")
    validated = meta.get("validated_artifact_version")
    if version is not None and validated is not None and str(version) != str(validated):
        return False
    if bool(meta.get("pending_verification")):
        return False
    return True


def _dependency_card(snapshot: Mapping[str, Any], reference: Any) -> Optional[Mapping[str, Any]]:
    key = str(reference)
    card = (snapshot.get("cards_by_id") or {}).get(key)
    if card:
        return card
    for item in snapshot.get("cards") or []:
        if str(item.get("shape")) == key:
            return item
    return None


def _decision_result(card: Mapping[str, Any]) -> Optional[str]:
    meta = card.get("metadata") if isinstance(card.get("metadata"), Mapping) else card
    for key in ("decision_result", "branch_result", "result", "precondition_result"):
        value = meta.get(key)
        if value not in (None, "", "null"):
            return str(value)
    return None


def _conditional_dependencies(card: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict:
    meta = card.get("metadata") if isinstance(card.get("metadata"), Mapping) else card
    conditional = meta.get("conditional_dependencies") or []
    if not isinstance(conditional, list):
        conditional = [conditional]
    precondition_shape = meta.get("precondition_shape")
    precondition_result = str(meta.get("precondition_result") or "").strip()
    paths = meta.get("decision_paths") or []
    if not isinstance(paths, list):
        paths = []
    missing: List[Any] = []
    invalid: List[Any] = []
    mismatches: List[str] = []
    required: List[Any] = []
    parent: Optional[Mapping[str, Any]] = None
    actual: Optional[str] = None
    if precondition_shape not in (None, "", "null"):
        parent = _dependency_card(snapshot, precondition_shape)
        if parent is None:
            missing.append(precondition_shape)
        elif not artifact_is_valid(parent):
            invalid.append(precondition_shape)
        else:
            actual = _decision_result(parent)
            allowed = [item.strip() for item in precondition_result.split("|") if item.strip()]
            if allowed and (not actual or actual not in allowed):
                mismatches.append("%s!=%s" % (actual or "<unset>", "|".join(allowed)))
    # Explicit decision paths take precedence over a bare conditional list.
    matched_path = None
    for path in paths:
        if not isinstance(path, Mapping):
            continue
        when = str(path.get("when") or "")
        expected = when.split("=", 1)[1].strip() if "=" in when else when.strip()
        if actual and expected and actual == expected:
            matched_path = path
            break
    if paths and matched_path is None and not missing and not invalid and not mismatches:
        mismatches.append("decision_path_unresolved")
    if matched_path is not None:
        required = list(matched_path.get("requires") or [])
    elif precondition_shape not in (None, "", "null"):
        # A plain conditional list is active once its precondition result is
        # known.  For an explicit 8m|4m result, decision_paths above chooses
        # the branch and leaves the list inactive when no requirement applies.
        required = list(conditional) if actual is not None else []
    else:
        required = list(conditional)
    for dependency in required:
        target = _dependency_card(snapshot, dependency)
        if target is None:
            missing.append(dependency)
        elif not artifact_is_valid(target):
            invalid.append(dependency)
    return {
        "missing": missing,
        "invalid": invalid,
        "mismatches": mismatches,
        "required": required,
        "precondition_shape": precondition_shape,
        "precondition_result": precondition_result,
        "actual_result": actual,
    }


def dependency_eligibility(card: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict:
    dependencies = card.get("depends_on")
    if dependencies is None:
        dependencies = (card.get("metadata") or {}).get("depends_on") or []
    if not isinstance(dependencies, list):
        dependencies = [dependencies]
    missing: List[Any] = []
    invalid: List[Any] = []
    cards_by_id = snapshot.get("cards_by_id") or {}
    cards = snapshot.get("cards") or []
    by_shape = {str(item.get("shape")): item for item in cards}
    for dependency in dependencies:
        key = str(dependency)
        target = cards_by_id.get(key) or by_shape.get(key)
        if not target:
            missing.append(dependency)
        elif not artifact_is_valid(target):
            invalid.append(dependency)
    conditional = _conditional_dependencies(card, snapshot)
    missing.extend(conditional["missing"])
    invalid.extend(conditional["invalid"])
    return {
        "ok": not missing and not invalid and not conditional["mismatches"],
        "missing": _unique(missing),
        "invalid": _unique(invalid),
        "precondition": conditional,
    }


def _decision_gate(card: Mapping[str, Any], snapshot: Mapping[str, Any]) -> Optional[str]:
    meta = card.get("metadata") if isinstance(card.get("metadata"), Mapping) else card
    review = meta.get("decision_review") or meta.get("requires_direction_review")
    if meta.get("is_direction_review") or meta.get("is_decision_review") or meta.get("direction_review"):
        # A review card is itself blocked until the advanced-model assessment
        # and the human conclusion are recorded on that card.  The reviewer is
        # not granted an implicit pass merely because its title says review.
        if not review:
            return "DECISION_REVIEW_REQUIRED"
    # Branch cards use the same generic gate shape.  The engine deliberately
    # does not know domain-specific numbers (96-99 or otherwise).
    branch = meta.get("branch_gate") or meta.get("precondition")
    if branch and not review:
        review = branch
    if not review:
        return None
    if isinstance(review, str):
        gate_id = review
    elif isinstance(review, Mapping):
        gate_id = review.get("card_id") or review.get("id")
        status = str(review.get("status") or "").lower()
        model = str(review.get("model") or review.get("advanced_model") or review.get("model_tier") or "").lower()
        human = review.get("human_decision") or review.get("decision") or review
        # The execution model that runs this engine (including Luna Max) is
        # not automatically a direction-review authority.  A review must name
        # an explicitly advanced reviewer tier/model or the designated Astra/
        # Terra class; reasoning-effort labels alone do not count.
        model_ok = bool(model) and (
            model in {"advanced", "gpt-6-astra", "gpt-5.6-terra", "gpt-6"}
            or model.endswith("-advanced")
            or "advanced" in model
        )
        human_ok = isinstance(human, Mapping) and bool(human.get("source") or human.get("by") or human.get("decided_by")) and human.get("approved", True) is not False
        if status in {"accepted", "pass", "approved"} and model_ok and human_ok:
            # Direction/branch reviews are decisions about a particular input
            # snapshot.  A review without a matching fingerprint is not a
            # reusable approval: otherwise a later goal/interface change could
            # accidentally inherit an old review.
            expected_fingerprint = meta.get("review_input_fingerprint")
            actual_fingerprint = review.get("input_fingerprint")
            if expected_fingerprint or actual_fingerprint or meta.get("is_direction_review") or meta.get("direction_review"):
                if not expected_fingerprint or not actual_fingerprint or str(expected_fingerprint) != str(actual_fingerprint):
                    return "DECISION_REVIEW_STALE"
            return None
        if status in {"accepted", "pass", "approved"}:
            return "DECISION_REVIEW_INCOMPLETE"
    else:
        gate_id = None
    if gate_id:
        gate = _card_from_snapshot(snapshot, str(gate_id))
        if gate and _status(gate) == "accepted" and artifact_is_valid(gate):
            return None
        return "DECISION_REVIEW_BLOCKED:%s" % gate_id
    return "DECISION_REVIEW_REQUIRED"


def _status_target(operation: Mapping[str, Any]) -> Optional[str]:
    name = _operation_name(operation)
    if name in {"start", "resume", "continue"}:
        return "doing"
    if name in {"question", "ask", "blocking_question"}:
        return "doing"
    if name in {"feedback", "acceptance_feedback", "small_feedback"}:
        return "doing" if operation.get("resume") is True else None
    if name in {"await", "awaiting_acceptance", "submit", "submit_for_acceptance"}:
        return "awaiting_acceptance"
    if name in {"accept", "close", "accepted"}:
        return "accepted"
    if name in {"cancel", "cancelled", "archive"}:
        return "cancelled"
    if name in {"set_status", "status", "transition"}:
        target = str(operation.get("status") or operation.get("to") or "").strip()
        return "doing" if target == "running" else target or None
    if name in {"update", "edit", "changes", "record_decision", "resolve_question", "human_decision"}:
        changes = operation.get("changes")
        if isinstance(changes, Mapping) and "status" in changes:
            target = str(changes.get("status") or "").strip()
            return "doing" if target == "running" else target or None
    return None


def _validate_status_transition(current: str, target: Optional[str]) -> None:
    if target is None or target == current:
        return
    allowed = {
        "ready": {"doing", "cancelled"},
        "doing": {"doing", "awaiting_acceptance", "cancelled"},
        "awaiting_acceptance": {"doing", "awaiting_acceptance", "accepted", "cancelled"},
        "accepted": {"accepted"},
        "cancelled": {"cancelled"},
    }
    if target not in allowed.get(current, set()):
        raise SJSV4Error("ILLEGAL_STATUS_TRANSITION", current=current, target=target)


def _validate_operation_preconditions(card: Mapping[str, Any], operation: Mapping[str, Any], snapshot: Mapping[str, Any]) -> None:
    current = _status(card)
    target = _status_target(operation)
    name = _operation_name(operation)
    if name in {"record_decision", "resolve_question", "human_decision"} and target in {"awaiting_acceptance", "accepted"}:
        raise SJSV4Error("STATUS_OPERATION_REQUIRED", operation=name, target=target)
    _validate_status_transition(current, target)
    if current == "accepted" and name in {"update", "edit", "changes", "set_status", "status", "transition"}:
        changes = operation.get("changes")
        meaningful = isinstance(changes, Mapping) and any(
            str(key) != "status" or _status(changes) != "accepted" for key in changes
        )
        if meaningful or name not in {"set_status", "status", "transition"}:
            raise SJSV4Error("ACCEPTED_REQUIRES_PATCH_OR_DECISION", card_id=card.get("id"))
    # Only work-start and acceptance transitions consume dependency artifacts.
    # Questions, feedback, decisions, timing and scope notes are auditable
    # records that must remain writable while an upstream artifact is being
    # repaired; they must not be blocked by the construction gate.
    dependency_bound_names = {
        "start", "resume", "continue", "await", "awaiting_acceptance",
        "submit", "submit_for_acceptance", "accept", "close", "accepted",
    }
    if target in {"doing", "awaiting_acceptance", "accepted"} and current != "accepted" and name in dependency_bound_names:
        eligibility = dependency_eligibility(card, snapshot)
        if eligibility["missing"]:
            raise SJSV4Error("DEPENDENCY_NOT_FOUND", card_id=card.get("id"), dependencies=eligibility["missing"])
        if eligibility["invalid"]:
            raise SJSV4Error("DEPENDENCY_ARTIFACT_INVALID", card_id=card.get("id"), dependencies=eligibility["invalid"])
        if (eligibility.get("precondition") or {}).get("mismatches"):
            raise SJSV4Error("PRECONDITION_MISMATCH", card_id=card.get("id"), details=eligibility["precondition"]["mismatches"])
        # A human decision operation is the explicit mechanism for resolving a
        # gate, so it must be able to write the decision that removes the gate.
        meta = card.get("metadata") if isinstance(card.get("metadata"), Mapping) else card
        review_is_self = bool(meta.get("direction_review") or meta.get("is_direction_review") or meta.get("is_decision_review"))
        # A review card may be started and brought to awaiting_acceptance
        # while its analysis is in progress.  The advanced-model + human
        # conclusion gate applies when it is accepted, and to downstream cards
        # that depend on its accepted artifact.
        gate_error = (
            None
            if name in {"record_decision", "resolve_question", "human_decision"} or (review_is_self and target != "accepted")
            else _decision_gate(card, snapshot)
        )
        if gate_error:
            raise SJSV4Error(gate_error, card_id=card.get("id"))


def check_snapshot(snapshot: Mapping[str, Any], *, compare_projections: bool = False) -> dict:
    errors: List[str] = []
    warnings: List[str] = list(snapshot.get("warnings") or [])
    cards = list(snapshot.get("cards") or [])
    seen_ids: set = set()
    seen_shapes: Dict[str, str] = {}
    active_norm_owners: Dict[str, str] = {}
    graph: Dict[str, List[str]] = {}
    cards_by_id = snapshot.get("cards_by_id") or {}
    for card in cards:
        card_id = str(card.get("id") or "")
        if not card_id:
            errors.append("CARD_ID_MISSING")
            continue
        if card_id in seen_ids:
            errors.append("DUPLICATE_CARD_ID:%s" % card_id)
        seen_ids.add(card_id)
        status = _status(card)
        if status not in ALLOWED_STATUSES:
            errors.append("INVALID_STATUS:%s:%s" % (card_id, status))
        if status == "ready":
            shape = card.get("shape")
            if isinstance(shape, bool) or not isinstance(shape, (int, float)):
                errors.append("READY_SHAPE_NOT_NUMERIC:%s" % card_id)
        shape = card.get("shape")
        if shape is not None:
            shape_key = str(shape)
            previous = seen_shapes.get(shape_key)
            if previous and previous != card_id:
                errors.append("DUPLICATE_SHAPE:%s" % shape_key)
            seen_shapes[shape_key] = card_id
        try:
            if int(card.get("revision") or 0) < 0:
                errors.append("NEGATIVE_REVISION:%s" % card_id)
        except (TypeError, ValueError):
            errors.append("INVALID_REVISION:%s" % card_id)
        deps = card.get("depends_on")
        if deps is None:
            deps = (card.get("metadata") or {}).get("depends_on") or []
        if not isinstance(deps, list):
            deps = [deps]
        resolved: List[str] = []
        by_shape = {str(item.get("shape")): str(item.get("id")) for item in cards}
        for dep in deps:
            target_id = str(dep) if str(dep) in cards_by_id else by_shape.get(str(dep))
            if not target_id:
                errors.append("DEPENDENCY_NOT_FOUND:%s:%s" % (card_id, dep))
            else:
                resolved.append(target_id)
        graph[card_id] = resolved
        if status in {"ready", "doing", "awaiting_acceptance"}:
            eligibility = dependency_eligibility(card, snapshot)
            if eligibility["missing"]:
                warnings.append("DEPENDENCY_MISSING:%s:%s" % (card_id, ",".join(map(str, eligibility["missing"]))))
            if eligibility["invalid"]:
                warnings.append("DEPENDENCY_ARTIFACT_INVALID:%s:%s" % (card_id, ",".join(map(str, eligibility["invalid"]))))
            if (eligibility.get("precondition") or {}).get("mismatches"):
                warnings.append("PRECONDITION_MISMATCH:%s:%s" % (card_id, ",".join(map(str, eligibility["precondition"]["mismatches"]))))
            gate_error = _decision_gate(card, snapshot)
            if gate_error:
                warnings.append("%s:%s" % (gate_error, card_id))
        norms = (card.get("metadata") or {}).get("norms")
        if isinstance(norms, list):
            for norm in norms:
                if not isinstance(norm, Mapping) or norm.get("active") is False:
                    continue
                norm_key = str(norm.get("key") or norm.get("id") or norm.get("name") or "").strip()
                if not norm_key:
                    continue
                prior = active_norm_owners.get(norm_key)
                if prior and prior != card_id:
                    errors.append("DUPLICATE_ACTIVE_NORM:%s:%s:%s" % (norm_key, prior, card_id))
                else:
                    active_norm_owners[norm_key] = card_id
    visiting: set = set()
    visited: set = set()

    def visit(node: str) -> None:
        if node in visiting:
            errors.append("DEPENDENCY_CYCLE:%s" % node)
            return
        if node in visited:
            return
        visiting.add(node)
        for dep in graph.get(node, []):
            visit(dep)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)
    if compare_projections:
        expected = render_projections(snapshot)
        for relative, text in expected.items():
            current = snapshot.get("files", {}).get(relative) or {}
            if not current.get("exists"):
                warnings.append("DERIVED_MISSING:%s" % relative)
            elif current.get("sha256") != _sha_text(text):
                warnings.append("DERIVED_STALE:%s" % relative)
    return {"ok": not errors, "errors": errors, "warnings": _unique(warnings), "revision": snapshot.get("revision", 0)}


def _unique(items: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def check_instance(instance: Any, *, compare_projections: bool = False) -> dict:
    root = _instance_path(instance)
    snapshot = load_snapshot(root)
    result = check_snapshot(snapshot, compare_projections=compare_projections)
    manifest = snapshot.get("instance") or {}
    protocol = str(manifest.get("protocol_version") or "")
    if protocol and not protocol.startswith("4"):
        result.setdefault("warnings", []).append("NON_V4_INSTANCE:%s" % protocol)
    result["snapshot"] = snapshot
    return result


def _safe_goal(card: Mapping[str, Any]) -> str:
    text = _section_value(card, "goal", "intro")
    return re.sub(r"\s+", " ", text).strip()[:240]


def _deps_text(card: Mapping[str, Any]) -> str:
    deps = card.get("depends_on")
    if deps is None:
        deps = (card.get("metadata") or {}).get("depends_on") or []
    if not isinstance(deps, list):
        deps = [deps]
    return ", ".join(str(dep) for dep in deps)


def render_projections(snapshot: Mapping[str, Any]) -> Dict[str, str]:
    """Generate all standard views from one snapshot without touching SOURCE-PLAN."""
    cards = sorted(snapshot.get("cards") or [], key=lambda c: (_shape_sort(c.get("shape")), str(c.get("id") or "")))
    revision = int(snapshot.get("revision") or 0)
    instance = snapshot.get("instance") or {}
    title = str(instance.get("domain_name") or "serial-job-session v4")
    menu: List[str] = [
        "# %s · JOB-SHAPES (generated v4)" % title,
        "",
        "> Cards are authoritative. This menu is a deterministic projection; edit the card.",
        "",
        "<!-- sjs-v4:generated revision=%s -->" % revision,
        "",
        "| shape | id | status | goal | depends_on | estimate |",
        "|---:|---|---|---|---|---:|",
    ]
    for card in cards:
        shape = card.get("shape") if card.get("shape") is not None else ""
        estimate = (card.get("metadata") or {}).get("estimate_total")
        if estimate is None:
            estimate = (card.get("metadata") or {}).get("estimate")
        menu.append("| %s | %s | %s | %s | %s | %s |" % (
            shape,
            card.get("id") or "",
            _status(card),
            _safe_goal(card).replace("|", "\\|"),
            _deps_text(card).replace("|", "\\|"),
            "" if estimate is None else estimate,
        ))
    if not cards:
        menu.append("|  |  |  |  |  |  |")

    active = [card for card in cards if _status(card) in {"doing", "awaiting_acceptance"}]
    current: List[str] = [
        "# %s · CURRENT (generated v4)" % title,
        "",
        "---",
        "schema: %s" % CURRENT_SCHEMA,
        "revision: %s" % revision,
        "status: %s" % ("idle" if not active else _status(active[0])),
        "active_ids: %s" % _dump_scalar([str(card.get("id")) for card in active]),
        "---",
        "",
        "## Active",
    ]
    if active:
        current.extend("- `%s` · %s · %s" % (card.get("id"), _status(card), _safe_goal(card) or "(no goal)") for card in active)
    else:
        current.append("- none")
    current.extend(["", "## Constraints", "", "Generated from card metadata and sections; SOURCE-PLAN remains an external read-only plan source.", ""])

    log: List[str] = [
        "# %s · LOG (generated v4)" % title,
        "",
        "<!-- sjs-v4:generated revision=%s -->" % revision,
        "",
        "| id | status | revision | event |",
        "|---|---|---:|---|",
    ]
    for card in cards:
        meta = card.get("metadata") or {}
        events = meta.get("events") if isinstance(meta.get("events"), list) else []
        if not events:
            events = [{"event": "snapshot", "at": meta.get("closed_at") or meta.get("started_at") or meta.get("created_at") or ""}]
        for event in events:
            if isinstance(event, Mapping):
                event_name = event.get("event") or event.get("type") or "event"
                at = event.get("at") or event.get("timestamp") or ""
                description = "%s%s" % (event_name, (" @ " + str(at)) if at else "")
            else:
                description = str(event)
            log.append("| %s | %s | %s | %s |" % (card.get("id"), _status(card), card.get("revision", 0), description.replace("|", "\\|")))

    facts: List[str] = [
        "# %s · FACTS (generated v4)" % title,
        "",
        "> Evidence-backed knowledge is supplied on the card. The script never invents lessons.",
        "",
        "| card | kind | active | evidence | change |",
        "|---|---|---:|---|---|",
    ]
    for card in cards:
        meta = card.get("metadata") or {}
        changes = meta.get("knowledge_changes")
        effective = meta.get("knowledge")
        if effective is None:
            effective = meta.get("fact_records") or meta.get("facts")
        if isinstance(effective, Mapping):
            effective = [effective]
        if isinstance(effective, list):
            # Effective knowledge is generated from the card; inactive entries
            # stay visible as historical facts with their active flag.
            changes = list(effective) + (list(changes) if isinstance(changes, list) else [])
        elif changes is None:
            changes = []
        norms = meta.get("norms")
        if isinstance(norms, Mapping):
            norms = [norms]
        if isinstance(norms, list):
            changes = list(changes or []) + [dict(item, kind=dict(item).get("kind") or "norm") for item in norms if isinstance(item, Mapping)]
        if not changes:
            # Legacy migration keeps the seven-section prose even when no
            # structured fact record was present.  Preserve that evidence as a
            # visible historical record; it is never treated as an active norm
            # until an explicit evidence-backed change names a stable key.
            legacy_text = _section_value(card, "knowledge", "facts", "norms")
            if legacy_text:
                changes = [{
                    "kind": "legacy_knowledge",
                    "active": False,
                    "evidence": meta.get("archive_ref") or card.get("path") or "card",
                    "text": legacy_text,
                }]
        if isinstance(changes, Mapping):
            changes = [changes]
        if not isinstance(changes, list):
            changes = []
        seen_facts = set()
        for change in changes:
            if not isinstance(change, Mapping):
                continue
            kind = change.get("kind") or change.get("action") or "change"
            evidence = change.get("evidence") or change.get("source") or ""
            body = change.get("text") or change.get("value") or change.get("replacement") or ""
            fact_key = (str(change.get("key") or change.get("id") or ""), str(kind), str(evidence), str(body), bool(change.get("active", True)))
            if fact_key in seen_facts:
                continue
            seen_facts.add(fact_key)
            if not evidence:
                evidence = "(missing evidence)"
            facts.append("| %s | %s | %s | %s |" % (card.get("id"), kind, str(evidence).replace("|", "\\|"), str(body).replace("|", "\\|")))
            active = change.get("active", True)
            # Replace the row just appended with the explicit active marker;
            # keeping it in the projection prevents inactive history from
            # being mistaken for effective NORMS by a downstream reader.
            facts[-1] = "| %s | %s | %s | %s | %s |" % (
                card.get("id"), kind, "true" if active is not False else "false",
                str(evidence).replace("|", "\\|"), str(body).replace("|", "\\|"),
            )
    if len(facts) == 6:
        facts.append("|  | none |  |  | no evidence-backed changes supplied |")
    return {
        "JOB-SHAPES.md": "\n".join(menu).rstrip() + "\n",
        "CURRENT.md": "\n".join(current).rstrip() + "\n",
        "LOG.md": "\n".join(log).rstrip() + "\n",
        "FACTS.md": "\n".join(facts).rstrip() + "\n",
    }


def _card_file(root: Path, card: Mapping[str, Any]) -> Path:
    relative = str(card.get("path") or "")
    if not relative:
        raise SJSV4Error("CARD_PATH_MISSING")
    try:
        relative = _safe_relative(root, relative)
    except SJSV4Error as exc:
        raise SJSV4Error("CARD_PATH_ESCAPE", path=relative, cause=exc.code)
    return root / relative


def _safe_relative(root: Path, value: Any) -> str:
    """Normalise a transaction target and reject traversal/junction escapes."""
    raw = str(value or "").strip().replace("\\", "/")
    if not raw or raw.startswith("/") or re.match(r"^[A-Za-z]:", raw):
        raise SJSV4Error("PATH_OUTSIDE_INSTANCE", path=raw)
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        raise SJSV4Error("PATH_TRAVERSAL", path=raw)
    relative = "/".join(parts)
    candidate = (root / Path(*parts)).resolve()
    root_real = root.resolve()
    try:
        candidate.relative_to(root_real)
    except ValueError:
        raise SJSV4Error("PATH_OUTSIDE_INSTANCE", path=raw)
    # Existing path components may be junctions/symlinks.  resolve() above
    # catches an escape while retaining a case-normalised Windows comparison.
    return relative


def _safe_private_path(root: Path, value: Any) -> Path:
    """Validate a journal-provided stage/backup path inside private storage."""
    candidate = Path(str(value or "")).expanduser()
    if not candidate.is_absolute():
        candidate = _private_root(root) / candidate
    resolved = candidate.resolve()
    private = (_repo_root(root) / ".serial-job-session" / "private").resolve()
    try:
        resolved.relative_to(private)
    except ValueError:
        raise SJSV4Error("PRIVATE_PATH_ESCAPE", path=str(value))
    return resolved


def _operation_name(operation: Mapping[str, Any]) -> str:
    value = operation.get("op") or operation.get("operation") or operation.get("type") or "update"
    return str(value).strip().lower().replace("-", "_")


def _append_section(card: dict, section: str, text: str) -> None:
    sections = card.setdefault("sections", {})
    value = str(text or "").strip()
    if not value:
        return
    key = str(section)
    parent = SECTION_PARENT.get(key, "progress")
    previous = str(sections.get(parent) or "").strip()
    if key == parent:
        sections[parent] = (previous + "\n\n" + value).strip() if previous else value
        return
    child_title = key.replace("_", " ").strip().title()
    marker = re.compile(r"(^|\n)###\s+%s\s*\n" % re.escape(child_title), flags=re.I)
    match = marker.search(previous)
    if match:
        start = match.end()
        next_child = re.search(r"\n###\s+", previous[start:], flags=re.I)
        end = start + next_child.start() if next_child else len(previous)
        child_body = previous[start:end].strip()
        replacement = "%s\n%s" % (previous[:start], (child_body + "\n\n" + value).strip())
        replacement += previous[end:]
        sections[parent] = replacement.strip()
    else:
        suffix = (previous + "\n\n") if previous else ""
        sections[parent] = (suffix + "### %s\n%s" % (child_title, value)).strip()


def _normalise_sections(raw: Mapping[str, Any]) -> Dict[str, str]:
    """Convert legacy child keys to seven parent bodies for rendering."""
    result: Dict[str, str] = {}
    for raw_key, raw_value in raw.items():
        value = str(raw_value or "").strip()
        if not value:
            continue
        key = str(raw_key)
        base_key = re.sub(r"_\d+$", "", key)
        parent = SECTION_PARENT.get(base_key, "progress")
        if base_key == parent:
            result[parent] = (result[parent] + "\n\n" + value).strip() if result.get(parent) else value
        else:
            child_title = base_key.replace("_", " ").strip().title()
            child = "### %s\n%s" % (child_title, value)
            result[parent] = (result[parent] + "\n\n" + child).strip() if result.get(parent) else child
    return result


def _set_metadata(card: dict, changes: Mapping[str, Any]) -> None:
    meta = card.setdefault("metadata", {})
    protected = {
        "id",
        "shape",
        "created_at",
        "started_at",
        "cancelled_at",
        "revision",
        "last_command_id",
        "accepted_at",
        "closed_at",
        "estimate_total",
        "estimate_first_pass",
        "estimate_rework",
        "estimate_business_wait",
        "estimate_source",
        "artifact_valid",
        "pending_verification",
        "validated_artifact_version",
        "artifact_verification_fingerprint",
        "artifact_verification_evidence",
        "decision_review",
        "review_input_fingerprint",
        "decision_result",
        "artifact_version",
        "affected_artifacts",
    }
    # These fields are evidence/transaction state.  They are written only by
    # their semantic operation (accept, patch, verify, or record_decision),
    # never by a generic metadata patch.  Check membership independently of
    # the old card so an absent field cannot be smuggled in on first write.
    protected_always = {
        "id", "shape", "created_at", "started_at", "cancelled_at",
        "revision", "last_command_id", "accepted_at", "closed_at",
        "artifact_valid", "pending_verification", "validated_artifact_version",
        "artifact_verification_fingerprint", "artifact_verification_evidence",
        "decision_review", "review_input_fingerprint", "decision_result",
        "artifact_version", "affected_artifacts",
    }
    for key, value in changes.items():
        name = str(key)
        if name in protected_always:
            raise SJSV4Error("PROTECTED_METADATA", field=name)
        if name in protected and name in meta:
            old = meta.get(name)
            # Timestamps are owned by accept/close transitions.  An ordinary
            # update may not invent one while a card is open, nor rewrite one
            # after acceptance.  Human decisions can change protected goal
            # fields through the explicit decision operation, but never the
            # historical acceptance boundary.
            if name in {"accepted_at", "closed_at"}:
                if str(old) != str(value):
                    raise SJSV4Error("PROTECTED_METADATA", field=name)
                continue
            # A first estimate can be recorded once.  Once present, the
            # initial estimate and all of its breakdown fields are immutable;
            # later forecast/revision data belongs in timing/patch records.
            if name.startswith("estimate_") and old is None:
                meta[name] = copy.deepcopy(value)
                card[name] = copy.deepcopy(value)
                continue
            if str(old) != str(value):
                code = "ESTIMATE_IMMUTABLE" if name.startswith("estimate_") else "PROTECTED_METADATA"
                raise SJSV4Error(code, field=name)
            continue
        if name == "status" and str(value).strip() == "running":
            value = "doing"
        meta[name] = copy.deepcopy(value)
        card[name] = copy.deepcopy(value)


def _knowledge_changes(operation: Mapping[str, Any]) -> list:
    changes = operation.get("knowledge_changes")
    if changes is None:
        changes = operation.get("knowledge")
    if changes is None:
        return []
    if isinstance(changes, Mapping):
        return [dict(changes)]
    if not isinstance(changes, list):
        raise SJSV4Error("KNOWLEDGE_CHANGES_INVALID")
    return [dict(item) for item in changes if isinstance(item, Mapping)]


def _apply_knowledge(card: dict, changes: Sequence[Mapping[str, Any]], now: str) -> list:
    """Apply evidence-backed knowledge changes while preserving history.

    Knowledge is kept on the card so there is one editable source.  ``remove``
    and ``deprecate`` mark an entry inactive instead of deleting its evidence;
    this lets FACTS remain auditable and lets a later human review restore it.
    """
    meta = card.setdefault("metadata", {})
    current = meta.get("knowledge")
    if isinstance(current, Mapping):
        current = [dict(current)]
    if not isinstance(current, list):
        current = []
    effective = [copy.deepcopy(item) for item in current if isinstance(item, Mapping)]
    history = meta.get("knowledge_history")
    if not isinstance(history, list):
        history = []
    norms_raw = meta.get("norms")
    if isinstance(norms_raw, Mapping):
        norms_raw = [dict(norms_raw)]
    norms = [copy.deepcopy(item) for item in norms_raw if isinstance(item, Mapping)] if isinstance(norms_raw, list) else []
    protected_keys = {"goal", "acceptance", "constraints", "northstar", "protected_constraints"}
    for raw in changes:
        item = dict(raw)
        evidence = item.get("evidence") or item.get("source") or item.get("source_refs")
        if not evidence:
            raise SJSV4Error("KNOWLEDGE_EVIDENCE_MISSING")
        key = str(item.get("id") or item.get("key") or item.get("name") or "")
        action = str(item.get("action") or item.get("kind") or "add").lower()
        if key.casefold() in protected_keys:
            raise SJSV4Error("HUMAN_DECISION_REQUIRED", change="protected_knowledge:%s" % key)
        record = copy.deepcopy(item)
        record["key"] = key or hashlib.sha256(json.dumps(item, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:16]
        record["active"] = action not in {"remove", "deprecate", "disable", "invalidate"}
        record["updated_at"] = now
        if action in {"replace", "update", "deprecate", "disable", "invalidate", "remove"}:
            replaced = False
            for index, existing in enumerate(effective):
                existing_key = str(existing.get("key") or existing.get("id") or existing.get("name") or "")
                if existing_key == record["key"] or (key and existing_key == key):
                    effective[index] = record
                    replaced = True
                    break
            if not replaced:
                effective.append(record)
        else:
            # An add with an existing key is a deterministic replacement.
            replaced = False
            for index, existing in enumerate(effective):
                existing_key = str(existing.get("key") or existing.get("id") or existing.get("name") or "")
                if existing_key == record["key"]:
                    effective[index] = record
                    replaced = True
                    break
            if not replaced:
                effective.append(record)
        history.append(record)
        # Norm/rule entries have an effective projection of their own.  Keep
        # the inactive record in that projection so a deprecation/removal is
        # auditable and can be explicitly restored by a later evidence-backed
        # change.  Existing ``norms`` metadata opts into the same behavior for
        # domain-specific norm kinds without hard-coding their names.
        kind = str(item.get("kind") or item.get("type") or "").lower()
        if kind in {"norm", "rule", "constraint", "protected_constraint"} or norms_raw is not None:
            replaced_norm = False
            for index, existing in enumerate(norms):
                existing_key = str(existing.get("key") or existing.get("id") or existing.get("name") or "")
                if existing_key == record["key"] or (key and existing_key == key):
                    norms[index] = copy.deepcopy(record)
                    replaced_norm = True
                    break
            if not replaced_norm:
                norms.append(copy.deepcopy(record))
    meta["knowledge"] = effective
    meta["knowledge_history"] = history
    if norms or norms_raw is not None:
        meta["norms"] = norms
    card["knowledge"] = effective
    card["knowledge_history"] = history
    if norms or norms_raw is not None:
        card["norms"] = copy.deepcopy(norms)
    return effective


def _merge_timing(card: dict, record: Mapping[str, Any], now: str) -> dict:
    """Merge one host timing sample without making timing a second source DB.

    A repeated ``thread_id``/``turn_id`` pair replaces its provisional sample
    with the reconciled sample.  Other samples remain as auditable evidence on
    the authoritative card, and the compact summary fields let the statistics
    adapter consume a card without scanning unrelated files.
    """
    if not isinstance(record, Mapping):
        raise SJSV4Error("TIMING_RECORD_INVALID")
    item = copy.deepcopy(dict(record))
    thread_id = str(item.get("thread_id") or "").strip()
    turn_id = str(item.get("turn_id") or "").strip()
    if not thread_id or not turn_id:
        raise SJSV4Error("TIMING_CONTEXT_MISSING")
    item.setdefault("schema", "serial-job-session-time/v4")
    item["thread_id"] = thread_id
    item["turn_id"] = turn_id
    item.setdefault("recorded_at", now)
    meta = card.setdefault("metadata", {})
    records = meta.get("timing_records")
    if not isinstance(records, list):
        records = []
    replaced = False
    for index, existing in enumerate(records):
        if not isinstance(existing, Mapping):
            continue
        if str(existing.get("thread_id") or "") == thread_id and str(existing.get("turn_id") or "") == turn_id:
            records[index] = item
            replaced = True
            break
    if not replaced:
        records.append(item)
    meta["timing_records"] = records
    meta["timing_last"] = item
    meta["timing_source"] = str(item.get("timing_source") or ("missing" if item.get("warning") else "v4_host_timing"))
    meta["timing_complete"] = bool(item.get("complete"))
    meta["timing_reconcile_next_invocation"] = bool(item.get("reconcile_next_invocation") or not item.get("complete"))
    # Keep the common stats fields flat for the v4 stats adapter.  Values are
    # evidence from the host adapter, never estimates invented by this core.
    for key in (
        "active_work_ms", "wall_response_ms", "business_wait_ms", "child_effort_ms",
        "start_source", "start_confidence", "underbound", "count_for_calibration",
        "completed_at", "started_at", "duration_ms",
    ):
        if key in item:
            meta[key] = copy.deepcopy(item[key])
    card["timing_records"] = copy.deepcopy(records)
    card["timing_last"] = copy.deepcopy(item)
    for key in (
        "timing_source", "timing_complete", "timing_reconcile_next_invocation",
        "active_work_ms", "wall_response_ms", "business_wait_ms", "child_effort_ms",
        "start_source", "start_confidence", "underbound", "count_for_calibration",
        "completed_at", "started_at", "duration_ms",
    ):
        if key in meta:
            card[key] = copy.deepcopy(meta[key])
    return item


def _reconcile_global_norms(
    snapshot: Mapping[str, Any],
    updated_cards: Dict[str, dict],
    now: str,
    operation_order: Optional[Sequence[str]] = None,
) -> None:
    """Ensure one active norm key has one owning card across the snapshot."""
    all_cards: Dict[str, dict] = {
        str(card.get("id")): copy.deepcopy(card)
        for card in snapshot.get("cards") or []
        if card.get("id")
    }
    all_cards.update({str(key): copy.deepcopy(value) for key, value in updated_cards.items()})
    owners: Dict[str, Optional[str]] = {}
    retired: set = set()
    for card_id in sorted(all_cards):
        card = all_cards[card_id]
        norms = (card.get("metadata") or {}).get("norms")
        if not isinstance(norms, list):
            continue
        for item in norms:
            if not isinstance(item, Mapping) or item.get("active") is False:
                continue
            key = str(item.get("key") or item.get("id") or item.get("name") or "").strip()
            if not key:
                continue
            prior = owners.get(key)
            if prior is None and key not in owners:
                owners[key] = card_id
                continue
    # Apply explicit knowledge operations in command order.  The last active
    # replacement wins; a retire/remove clears the key globally instead of
    # allowing a stale duplicate on another card to become effective.
    ordered_ids = list(operation_order or []) + [key for key in updated_cards if key not in (operation_order or [])]
    for card_id in ordered_ids:
        card = updated_cards.get(str(card_id))
        if not card:
            continue
        meta = card.get("metadata") or {}
        changes = meta.get("knowledge_changes")
        if not isinstance(changes, list):
            continue
        norms = meta.get("norms") if isinstance(meta.get("norms"), list) else []
        by_key = {str(item.get("key") or item.get("id") or item.get("name") or ""): item for item in norms if isinstance(item, Mapping)}
        for raw in changes:
            if not isinstance(raw, Mapping):
                continue
            norm_key = str(raw.get("key") or raw.get("id") or raw.get("name") or "").strip()
            if not norm_key:
                continue
            action = str(raw.get("action") or raw.get("kind") or "add").lower()
            if action in {"remove", "deprecate", "disable", "invalidate", "retire"} or (by_key.get(norm_key) or {}).get("active") is False:
                owners[norm_key] = None
                retired.add(norm_key)
            elif norm_key in by_key:
                owners[norm_key] = str(card_id)
                retired.discard(norm_key)
    for key, owner in owners.items():
        for card_id, card in all_cards.items():
            if owner is not None and card_id == owner:
                continue
            norms = (card.get("metadata") or {}).get("norms")
            if not isinstance(norms, list):
                continue
            changed = False
            for item in norms:
                if not isinstance(item, dict):
                    continue
                item_key = str(item.get("key") or item.get("id") or item.get("name") or "").strip()
                if item_key == key and item.get("active") is not False and (owner is None or card_id != owner):
                    item["active"] = False
                    item["retired_by"] = owner or "retired"
                    item["retired_at"] = now
                    changed = True
            if changed:
                card.setdefault("metadata", {})["norms"] = norms
                card["norms"] = copy.deepcopy(norms)
                meta_history = card.setdefault("metadata", {}).get("knowledge_history")
                if not isinstance(meta_history, list):
                    meta_history = []
                meta_history.append({"key": key, "kind": "norm", "action": "retire", "active": False, "retired_by": owner or "retired", "updated_at": now, "evidence": "cross-card norm uniqueness"})
                card["metadata"]["knowledge_history"] = meta_history
                updated_cards[card_id] = card


def _apply_operation(card: dict, operation: Mapping[str, Any], now: str) -> Tuple[dict, dict]:
    """Apply one semantic operation to an in-memory card; no filesystem writes."""
    result = copy.deepcopy(card)
    meta = result.setdefault("metadata", {})
    name = _operation_name(operation)
    current = _status(result)
    event: Dict[str, Any] = {"event": name, "at": now}
    if name in {"change_goal", "change_interface", "change_downstream", "major_change", "decision_required"}:
        raise SJSV4Error("HUMAN_DECISION_REQUIRED", change=name)
    if name in {"record_decision", "resolve_question", "human_decision"}:
        decision = operation.get("human_decision") or operation.get("decision")
        if not isinstance(decision, Mapping) or not decision.get("source") and not decision.get("by") and not decision.get("decided_by"):
            raise SJSV4Error("HUMAN_DECISION_REQUIRED", change=name)
        if decision.get("approved") is False:
            raise SJSV4Error("HUMAN_DECISION_NOT_APPROVED", change=name)
        changes = operation.get("changes") or {}
        if changes and not isinstance(changes, Mapping):
            raise SJSV4Error("CHANGES_INVALID")
        if changes:
            major_section_changes = {
                str(key): value
                for key, value in changes.items()
                if str(key).lower() in {"goal", "interface", "downstream", "acceptance", "constraints"}
            }
            metadata_changes = {
                str(key): value
                for key, value in changes.items()
                if str(key).lower() not in major_section_changes
            }
            if metadata_changes:
                _set_metadata(result, metadata_changes)
            for key, value in major_section_changes.items():
                result.setdefault("sections", {})[key] = str(value or "").strip()
        sections = operation.get("sections") or {}
        if sections:
            if not isinstance(sections, Mapping):
                raise SJSV4Error("SECTIONS_INVALID")
            for key, value in sections.items():
                result.setdefault("sections", {})[str(key)] = str(value or "").strip()
        decisions = meta.get("decisions")
        if not isinstance(decisions, list):
            decisions = []
        item = dict(decision)
        item.setdefault("at", now)
        decisions.append(item)
        meta["decisions"] = decisions
        result["decisions"] = decisions
        decision_result = operation.get("decision_result") or operation.get("result") or operation.get("branch_result") or decision.get("decision_result") or decision.get("result") or decision.get("branch_result")
        if decision_result is not None:
            meta["decision_result"] = str(decision_result)
            result["decision_result"] = str(decision_result)
        if meta.get("direction_review") or meta.get("is_direction_review") or meta.get("is_decision_review") or meta.get("requires_direction_review"):
            review = dict(item)
            review.setdefault("status", "accepted" if review.get("approved", True) is not False else "rejected")
            review.setdefault("model", operation.get("model") or operation.get("advanced_model") or review.get("model"))
            review_fingerprint = (
                operation.get("input_fingerprint")
                or review.get("input_fingerprint")
                or meta.get("review_input_fingerprint")
            )
            # A card-provided expected fingerprint is authoritative.  The
            # decision records the observed fingerprint but cannot rewrite the
            # input that the review was meant to approve.
            expected_fingerprint = meta.get("review_input_fingerprint")
            if review_fingerprint and not expected_fingerprint:
                meta["review_input_fingerprint"] = str(review_fingerprint)
                result["review_input_fingerprint"] = str(review_fingerprint)
            if review_fingerprint:
                review["input_fingerprint"] = str(review_fingerprint)
            meta["decision_review"] = review
            result["decision_review"] = review
        _append_section(result, "decisions", json.dumps(item, ensure_ascii=False, sort_keys=True))
        event["decision"] = item
    elif name in {"create", "create_card"}:
        if current != "ready":
            raise SJSV4Error("CREATE_STATUS_INVALID", status=current)
        if not str(meta.get("id") or result.get("id") or "").strip():
            raise SJSV4Error("CARD_ID_MISSING")
        # Creation is already represented by the card assembled by the caller;
        # this event makes it explicit and keeps retries deterministic.
        meta.setdefault("status", "ready")
        result["status"] = "ready"
        event["status"] = "ready"
    elif name in {"set_status", "status", "transition"}:
        target = str(operation.get("status") or operation.get("to") or "").strip()
        if target == "running":
            target = "doing"
        if target not in ALLOWED_STATUSES:
            raise SJSV4Error("INVALID_STATUS", status=target)
        if current == "accepted" and target not in {"accepted"}:
            raise SJSV4Error("ACCEPTED_REQUIRES_PATCH_OR_DECISION")
        meta["status"] = target
        result["status"] = target
        event["status"] = target
    elif name in {"start", "resume", "continue"}:
        if current == "ready":
            meta["status"] = "doing"
            result["status"] = "doing"
            meta.setdefault("started_at", now)
            result["started_at"] = meta.get("started_at")
        elif current not in {"doing", "awaiting_acceptance"}:
            raise SJSV4Error("ILLEGAL_START_FROM_%s" % current)
        event["status"] = result["status"]
    elif name in {"question", "ask", "blocking_question"}:
        if current not in {"doing", "awaiting_acceptance"}:
            raise SJSV4Error("QUESTION_REQUIRES_ACTIVE_CARD")
        _append_section(result, "questions", operation.get("text") or operation.get("question") or "")
        event["question"] = operation.get("text") or operation.get("question") or ""
        # Questions intentionally leave a card in doing.
        if current == "awaiting_acceptance" and operation.get("resume", True):
            meta["status"] = "doing"
            result["status"] = "doing"
            event["status"] = "doing"
    elif name in {"feedback", "acceptance_feedback", "small_feedback"}:
        if current not in {"awaiting_acceptance", "doing"}:
            raise SJSV4Error("FEEDBACK_REQUIRES_ACTIVE_CARD")
        _append_section(result, "feedback", operation.get("text") or operation.get("feedback") or "")
        event["feedback"] = operation.get("text") or operation.get("feedback") or ""
        if operation.get("artifact_version") is not None:
            meta["artifact_version"] = operation.get("artifact_version")
            result["artifact_version"] = operation.get("artifact_version")
        # Small acceptance feedback keeps awaiting_acceptance unless explicitly resumed.
        if current == "awaiting_acceptance" and operation.get("resume") is True:
            meta["status"] = "doing"
            result["status"] = "doing"
    elif name in {"await", "awaiting_acceptance", "submit", "submit_for_acceptance"}:
        if current not in {"doing", "awaiting_acceptance"}:
            raise SJSV4Error("ILLEGAL_AWAIT_FROM_%s" % current)
        meta["status"] = "awaiting_acceptance"
        result["status"] = "awaiting_acceptance"
        event["status"] = "awaiting_acceptance"
    elif name in {"accept", "close", "accepted"}:
        if current != "awaiting_acceptance":
            raise SJSV4Error("ILLEGAL_ACCEPT_FROM_%s" % current)
        meta["status"] = "accepted"
        result["status"] = "accepted"
        # ``setdefault`` would preserve an explicit null from a migrated card;
        # acceptance must establish both immutable historical boundaries.
        if not meta.get("closed_at"):
            meta["closed_at"] = now
        if not meta.get("accepted_at"):
            meta["accepted_at"] = now
        meta["artifact_valid"] = True
        meta["pending_verification"] = False
        result.update({key: meta.get(key) for key in ("closed_at", "accepted_at", "artifact_valid", "pending_verification")})
        event["status"] = "accepted"
    elif name in {"cancel", "cancelled", "archive"}:
        if current == "accepted":
            raise SJSV4Error("ACCEPTED_REQUIRES_PATCH_OR_DECISION")
        meta["status"] = "cancelled"
        result["status"] = "cancelled"
        meta.setdefault("cancelled_at", now)
        result["cancelled_at"] = meta.get("cancelled_at")
        event["status"] = "cancelled"
    elif name in {"patch", "patch_closed", "amend"}:
        if current != "accepted":
            raise SJSV4Error("PATCH_REQUIRES_ACCEPTED_CARD")
        patches = meta.get("patches")
        if not isinstance(patches, list):
            patches = []
        patch = {
            "revision": int(meta.get("revision") or 0) + 1,
            "at": now,
            "summary": operation.get("summary") or operation.get("text") or "",
            "affected_artifacts": operation.get("affected_artifacts") or [],
            "evidence": operation.get("evidence") or [],
            "knowledge_changes": _knowledge_changes(operation),
        }
        patches.append(patch)
        meta["patches"] = patches
        meta["pending_verification"] = True
        meta["artifact_valid"] = False
        meta["affected_artifacts"] = patch["affected_artifacts"]
        result.update({"patches": patches, "pending_verification": True, "artifact_valid": False, "affected_artifacts": patch["affected_artifacts"]})
        _append_section(result, "patches", json.dumps(patch, ensure_ascii=False, sort_keys=True))
        event["patch_revision"] = patch["revision"]
    elif name in {"verify_patch", "verify_artifacts", "artifact_verification"}:
        if current != "accepted":
            raise SJSV4Error("PATCH_VERIFY_REQUIRES_ACCEPTED_CARD")
        evidence = operation.get("evidence") or operation.get("verification_evidence") or operation.get("check") or operation.get("text")
        if evidence in (None, "", [], {}, ()):
            raise SJSV4Error("ARTIFACT_VERIFICATION_EVIDENCE_REQUIRED")
        requested_valid = operation.get("artifact_valid")
        if requested_valid is None:
            requested_valid = True
        artifact_version = operation.get("artifact_version")
        validated_version = operation.get("validated_artifact_version") or artifact_version
        existing_version = meta.get("artifact_version")
        affected = operation.get("affected_artifacts") or meta.get("affected_artifacts") or []
        no_file_artifact = bool(operation.get("no_artifact") or str(operation.get("artifact_kind") or "").lower() in {"text", "none", "documentation"})
        if bool(requested_valid) and affected and artifact_version is None and not no_file_artifact:
            raise SJSV4Error("ARTIFACT_VERSION_REQUIRED")
        if bool(requested_valid) and existing_version is not None and validated_version is not None and str(existing_version) != str(validated_version):
            raise SJSV4Error("ARTIFACT_VERSION_MISMATCH", expected=existing_version, actual=validated_version)
        if artifact_version is not None and validated_version is not None and str(artifact_version) != str(validated_version):
            raise SJSV4Error("ARTIFACT_VERSION_MISMATCH", expected=artifact_version, actual=validated_version)
        if no_file_artifact and artifact_version is None:
            proof = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            artifact_version = "text-proof:" + _sha_text(proof)[:16]
            validated_version = artifact_version
        meta["artifact_valid"] = bool(requested_valid)
        meta["pending_verification"] = not meta["artifact_valid"]
        if artifact_version is not None:
            meta["artifact_version"] = artifact_version
            result["artifact_version"] = artifact_version
        if validated_version is not None:
            meta["validated_artifact_version"] = validated_version
            result["validated_artifact_version"] = validated_version
        proof_text = json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        meta["artifact_verification_fingerprint"] = _sha_text(proof_text)
        meta["artifact_verification_evidence"] = copy.deepcopy(evidence)
        result["artifact_verification_fingerprint"] = meta["artifact_verification_fingerprint"]
        result.update({"artifact_valid": meta["artifact_valid"], "pending_verification": meta["pending_verification"]})
        event["artifact_valid"] = meta["artifact_valid"]
    elif name in {"timing", "time", "record_timing", "reconcile_timing", "timing_record"}:
        record = operation.get("timing") or operation.get("time_record") or operation.get("record")
        item = _merge_timing(result, record, now)
        event["timing"] = {"thread_id": item["thread_id"], "turn_id": item["turn_id"], "complete": bool(item.get("complete"))}
    elif name in {"update", "edit", "changes"}:
        changes = operation.get("changes") or {}
        if not isinstance(changes, Mapping):
            raise SJSV4Error("CHANGES_INVALID")
        forbidden = {"goal", "interface", "downstream", "acceptance", "constraints"}
        protected_internal = {
            "artifact_valid", "pending_verification", "validated_artifact_version",
            "artifact_verification_fingerprint", "artifact_verification_evidence",
            "decision_review", "review_input_fingerprint", "decision_result",
        }
        if protected_internal.intersection(str(key) for key in changes):
            raise SJSV4Error("PROTECTED_METADATA", field=sorted(protected_internal.intersection(str(key) for key in changes))[0])
        if forbidden.intersection(str(key) for key in changes):
            raise SJSV4Error("HUMAN_DECISION_REQUIRED", change="major_field")
        _set_metadata(result, changes)
        sections = operation.get("sections")
        if sections is not None:
            if not isinstance(sections, Mapping):
                raise SJSV4Error("SECTIONS_INVALID")
            if any(str(key).lower() in forbidden for key in sections):
                raise SJSV4Error("HUMAN_DECISION_REQUIRED", change="major_section")
            for key, value in sections.items():
                result.setdefault("sections", {})[str(key)] = str(value or "").strip()
        event["changes"] = sorted(str(key) for key in changes)
    else:
        raise SJSV4Error("UNKNOWN_OPERATION", operation=name)

    changes = operation.get("changes")
    if changes and name not in {"update", "edit", "changes", "record_decision", "resolve_question", "human_decision"}:
        if not isinstance(changes, Mapping):
            raise SJSV4Error("CHANGES_INVALID")
        forbidden = {"goal", "interface", "downstream", "acceptance", "constraints"}
        protected_internal = {
            "artifact_valid", "pending_verification", "validated_artifact_version",
            "artifact_verification_fingerprint", "artifact_verification_evidence",
            "decision_review", "review_input_fingerprint", "decision_result",
        }
        if protected_internal.intersection(str(key) for key in changes) and name not in {"verify_patch", "verify_artifacts", "artifact_verification", "record_decision", "resolve_question", "human_decision"}:
            raise SJSV4Error("PROTECTED_METADATA", field=sorted(protected_internal.intersection(str(key) for key in changes))[0])
        if forbidden.intersection(str(key) for key in changes):
            raise SJSV4Error("HUMAN_DECISION_REQUIRED", change="major_field")
        _set_metadata(result, changes)
        event["changes"] = sorted(str(key) for key in changes)
    knowledge = _knowledge_changes(operation)
    if "knowledge_changes" in operation or "knowledge" in operation:
        # An explicit [] is meaningful: it records that upkeep was considered
        # without fabricating a lesson or forcing a required field.
        effective = _apply_knowledge(result, knowledge, now)
        meta["knowledge_changes"] = knowledge
        result["knowledge_changes"] = knowledge
        result["knowledge"] = effective
        if knowledge:
            _append_section(result, "knowledge", json.dumps(knowledge, ensure_ascii=False, sort_keys=True))
        event["knowledge_changes"] = len(knowledge)
    # A semantic operation may carry a timing record inline.  This allows a
    # start/apply command to commit state and host timing in one journal even
    # when callers do not add a separate ``record_timing`` operation.
    if name not in {"timing", "time", "record_timing", "reconcile_timing", "timing_record"}:
        inline_timing = operation.get("timing") or operation.get("time_record")
        if inline_timing is not None:
            item = _merge_timing(result, inline_timing, now)
            event["timing"] = {"thread_id": item["thread_id"], "turn_id": item["turn_id"], "complete": bool(item.get("complete"))}
    if name in {"accept", "close", "accepted", "patch", "patch_closed", "amend", "cancel", "cancelled", "archive"}:
        # Upkeep is automatic bookkeeping, while the actual lesson/change is
        # always supplied by the agent with evidence.  No synthetic lesson is
        # written when the caller supplies zero changes.
        meta["knowledge_checked_at"] = now
        result["knowledge_checked_at"] = now
        event["knowledge_checked"] = True
    events = meta.get("events")
    if not isinstance(events, list):
        events = []
    events.append(event)
    meta["events"] = events
    result["events"] = events
    return result, event


def _command_operations(command: Mapping[str, Any]) -> List[dict]:
    operations = command.get("operations")
    if operations is None:
        operation = command.get("operation")
        if operation is None:
            changes = command.get("changes")
            operation = {"op": "update", "changes": changes or {}}
        elif isinstance(operation, Mapping) and operation.get("operations") is not None and not any(
            operation.get(key) for key in ("op", "operation", "type")
        ):
            operations = operation.get("operations")
        else:
            operations = [operation]
    if isinstance(operations, Mapping):
        operations = [operations]
    if not isinstance(operations, list) or not operations:
        raise SJSV4Error("OPERATIONS_MISSING")
    normalized: List[dict] = []
    for operation in operations:
        if isinstance(operation, str):
            normalized.append({"op": operation})
        elif isinstance(operation, Mapping):
            normalized.append(dict(operation))
        else:
            raise SJSV4Error("OPERATION_INVALID")
    return normalized


def _operation_card_ref(operation: Mapping[str, Any], command: Mapping[str, Any]) -> Tuple[Optional[str], Any]:
    card_id = operation.get("card_id") or operation.get("job_id") or command.get("card_id") or command.get("job_id")
    shape = operation.get("shape", command.get("shape"))
    return (str(card_id) if card_id is not None else None, shape)


def _fault(point: str) -> None:
    wanted = os.environ.get("SJS_V4_FAULT_INJECT") or os.environ.get("SJS_FAULT_INJECT") or ""
    if wanted and wanted in {point, "v4:" + point}:
        raise RuntimeError("FAULT_INJECT:" + point)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    temp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temp), str(path))
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def _atomic_copy(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    temp = Path(name)
    os.close(fd)
    try:
        shutil.copy2(str(source), str(temp))
        # Windows rejects fsync on a read-only descriptor (EBADF).  Keep the
        # destination descriptor writable while flushing the copied bytes.
        with temp.open("rb+") as stream:
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temp), str(target))
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


def _json_atomic(path: Path, data: Mapping[str, Any]) -> None:
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _lease_path(root: Path, instance_data: Mapping[str, Any]) -> Path:
    return _private_root(root, instance_data) / "instance.lock"


def _acquire_lease(root: Path, instance_data: Mapping[str, Any], command_id: str, allow_same: bool = False) -> Path:
    path = _lease_path(root, instance_data)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"command_id": command_id, "pid": os.getpid(), "at": _now()}
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        if allow_same:
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
                if current.get("command_id") == command_id:
                    return path
            except (OSError, ValueError):
                pass
        raise SJSV4Error("CONCURRENT_WRITE", path=str(path))
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    return path


def _release_lease(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _transaction_dir(root: Path, instance_data: Mapping[str, Any], command_id: str) -> Path:
    return _private_root(root, instance_data) / "transactions" / re.sub(r"[^A-Za-z0-9_.-]+", "_", command_id)


def _find_transaction(root: Path, instance_data: Mapping[str, Any], command_id: str) -> Optional[Path]:
    target = _transaction_dir(root, instance_data, command_id) / "journal.json"
    return target if target.is_file() else None


def _load_journal(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SJSV4Error("JOURNAL_UNREADABLE", str(exc))
    if not isinstance(data, dict):
        raise SJSV4Error("JOURNAL_INVALID")
    return data


def _record_base(root: Path, record: Optional[Mapping[str, Any]] = None) -> Path:
    value = record.get("base_root") if isinstance(record, Mapping) else None
    return Path(str(value)).expanduser().resolve() if value else root


def _target_hash(root: Path, relative: str, base_root: Optional[Path] = None) -> Optional[str]:
    base = (base_root or root).resolve()
    relative = _safe_relative(base, relative)
    path = base / relative
    return _sha_bytes(path.read_bytes()) if path.is_file() else None


def _command_digest(command: Mapping[str, Any]) -> str:
    raw = json.dumps(command, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha_text(raw)


def _target_drift(
    root: Path,
    snapshot: Mapping[str, Any],
    targets: Sequence[str],
    expected_hashes: Mapping[str, Any],
    target_bases: Optional[Mapping[str, Path]] = None,
) -> List[str]:
    drift: List[str] = []
    files = snapshot.get("files") or {}
    for value in targets:
        base = (target_bases or {}).get(value, root)
        relative = _safe_relative(base, value)
        expected = (files.get(relative) or {}).get("sha256")
        actual = _target_hash(root, relative, base)
        # Missing derived views are intentionally rebuildable; a missing source
        # card remains a hard failure and is handled by caller.
        if expected is None and actual is None and relative in DERIVED_FILES:
            continue
        if actual != expected:
            drift.append(relative)
        if relative in expected_hashes and expected_hashes.get(relative) != actual:
            drift.append("EXPECTED_HASH:%s" % relative)
    return _unique(drift)


def _make_prospective_snapshot(snapshot: Mapping[str, Any], updated_cards: Mapping[str, dict], revision: int) -> dict:
    result = copy.deepcopy(dict(snapshot))
    cards = []
    existing_ids = set()
    for card in snapshot.get("cards") or []:
        card_id = str(card.get("id"))
        existing_ids.add(card_id)
        cards.append(copy.deepcopy(updated_cards.get(card_id, card)))
    for card_id, card in updated_cards.items():
        if str(card_id) not in existing_ids:
            cards.append(copy.deepcopy(card))
    cards.sort(key=lambda c: (_shape_sort(c.get("shape")), str(c.get("id") or "")))
    result["cards"] = cards
    result["revision"] = revision
    result["cards_by_id"] = {str(card.get("id")): card for card in cards if card.get("id")}
    return result


def _prepare_targets(root: Path, snapshot: Mapping[str, Any], updated_cards: Mapping[str, dict], revision: int) -> Dict[str, str]:
    prospective = _make_prospective_snapshot(snapshot, updated_cards, revision)
    projections = render_projections(prospective)
    targets: Dict[str, str] = dict(projections)
    for card in updated_cards.values():
        relative = _safe_relative(root, card.get("path") or "")
        if not relative:
            raise SJSV4Error("CARD_PATH_MISSING")
        targets[relative] = render_card(card)
    return dict(sorted(targets.items()))


def _backup_targets(
    root: Path,
    tx_dir: Path,
    targets: Sequence[str],
    target_bases: Optional[Mapping[str, Path]] = None,
) -> List[dict]:
    backup_dir = tx_dir / "backup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    records: List[dict] = []
    for index, relative in enumerate(targets):
        base = ((target_bases or {}).get(relative) or root).resolve()
        relative = _safe_relative(base, relative)
        source = base / relative
        record: Dict[str, Any] = {
            "path": relative,
            "exists_before": source.is_file(),
            "before_sha256": _target_hash(root, relative, base),
            "backup": None,
        }
        if base != root.resolve():
            record["base_root"] = str(base)
        if source.is_file():
            backup = backup_dir / ("%04d-%s" % (index, Path(relative).name))
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(source), str(backup))
            record["backup"] = str(backup)
        else:
            # Keep a durable sentinel for absent generated views as well.  The
            # journal must enumerate every target; ``exists_before`` tells
            # rollback whether the sentinel is content or absence.
            backup = backup_dir / ("%04d-%s.missing" % (index, Path(relative).name))
            _atomic_write(backup, "")
            record["backup"] = str(backup)
        records.append(record)
    return records


def _stage_targets(
    tx_dir: Path,
    targets: Mapping[str, str],
    target_bases: Optional[Mapping[str, Path]] = None,
    root: Optional[Path] = None,
) -> List[dict]:
    stage_dir = tx_dir / "stage"
    stage_dir.mkdir(parents=True, exist_ok=True)
    records: List[dict] = []
    for index, (relative, text) in enumerate(sorted(targets.items())):
        staged = stage_dir / ("%04d.txt" % index)
        _atomic_write(staged, text)
        record = {"path": relative, "staged": str(staged), "after_sha256": _sha_text(text), "bytes": len(text.encode("utf-8"))}
        base = ((target_bases or {}).get(relative) or root) if root is not None else None
        if base is not None and base.resolve() != root.resolve():
            record["base_root"] = str(base.resolve())
        records.append(record)
    return records


def _write_journal(path: Path, data: dict) -> None:
    _json_atomic(path, data)


def _write_staged(root: Path, tx: dict) -> None:
    for record in tx.get("targets") or []:
        staged = _safe_private_path(root, record.get("staged"))
        base = _record_base(root, record)
        relative = _safe_relative(base, record.get("path"))
        target = base / relative
        if not staged.is_file():
            raise SJSV4Error("STAGED_FILE_MISSING", path=relative)
        _atomic_copy(staged, target)


def _recover_drift(root: Path, journal: Mapping[str, Any]) -> List[str]:
    """Only resume/rollback files that still equal a journal boundary."""
    drift: List[str] = []
    for record in journal.get("targets") or []:
        base = _record_base(root, record)
        relative = _safe_relative(base, record.get("path"))
        actual = _target_hash(root, relative, base)
        before = record.get("before_sha256")
        after = record.get("after_sha256")
        if actual not in {before, after}:
            # A newly created target may still be absent before its first write.
            if not (actual is None and before is None):
                drift.append(relative)
    return drift


def _journal_result(tx: Mapping[str, Any]) -> dict:
    return {
        "ok": tx.get("phase") == "committed",
        "schema": tx.get("schema", TX_SCHEMA),
        "command_id": tx.get("command_id"),
        "phase": tx.get("phase"),
        "revision": tx.get("next_revision"),
        "targets": [item.get("path") for item in tx.get("targets") or []],
        "replayed": bool(tx.get("replayed")),
        "warnings": tx.get("warnings") or [],
    }


def _migration_path(root: Path, operation: Mapping[str, Any]) -> Tuple[Path, str]:
    """Resolve one migration target with a narrow registry exception.

    Migration archives and cards stay below the instance.  The only target
    allowed outside it is the repository registry entry that the migration
    plan explicitly marks ``external_root``; accepting arbitrary absolute
    paths here would make a plan an unrestricted file writer.
    """
    raw = str(operation.get("path") or "").strip()
    if not raw:
        raise SJSV4Error("MIGRATION_PATH_MISSING")
    if operation.get("external_root"):
        repo = _repo_root(root).resolve()
        candidate = Path(raw).expanduser()
        candidate = (repo / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()
        allowed = (repo / ".serial-job-session" / "instances.json").resolve()
        if candidate != allowed:
            raise SJSV4Error("MIGRATION_EXTERNAL_TARGET_FORBIDDEN", path=raw)
        return repo, candidate.relative_to(repo).as_posix()
    return root, _safe_relative(root, raw)


def _migration_target_key(base: Path, root: Path, relative: str) -> str:
    # The current migration schema has no same-relative path in both roots, so
    # keep journal paths human-readable.  Detect a collision explicitly rather
    # than silently letting one operation replace another.
    return relative


def apply_migration(instance: Any, command: Mapping[str, Any]) -> dict:
    """Apply migration ``file_operations`` through the normal journal.

    ``replace_card`` and ``create_card`` are converted to canonical card text;
    ``write_file`` handles archives/control files.  Generated projections are
    added to the same target set, so a crash cannot leave cards and views at
    different revisions.  This public entry point is intentionally narrow and
    is advertised by ``MIGRATION_CAPABILITIES`` for the migration adapter.
    """
    if not isinstance(command, Mapping):
        raise SJSV4Error("COMMAND_INVALID")
    command_id = str(command.get("command_id") or "").strip()
    if not command_id:
        raise SJSV4Error("COMMAND_ID_MISSING")
    root = _instance_path(instance)
    initial = load_snapshot(root, include_text=False)
    instance_data = initial.get("instance") or {}
    existing_path = _find_transaction(root, instance_data, command_id)
    if existing_path:
        existing = _load_journal(existing_path)
        if existing.get("command_digest") != _command_digest(command):
            raise SJSV4Error("COMMAND_ID_REUSE", command_id=command_id)
        if existing.get("phase") == "committed":
            existing["replayed"] = True
            return _journal_result(existing)
        return recover_transaction(root, command_id=command_id, action="resume")
    unfinished = _find_unfinished_transaction(root, instance_data)
    if unfinished:
        raise SJSV4Error("UNFINISHED_TRANSACTION", command_id=unfinished.get("command_id"))
    expected_raw = command.get("expected_revision")
    try:
        expected_revision = int(expected_raw)
    except (TypeError, ValueError):
        raise SJSV4Error("EXPECTED_REVISION_INVALID")
    operations = command.get("file_operations")
    if operations is None:
        operations = command.get("operations")
    if isinstance(operations, Mapping):
        operations = [operations]
    if not isinstance(operations, list) or not operations:
        raise SJSV4Error("MIGRATION_OPERATIONS_MISSING")
    lease: Optional[Path] = None
    tx_dir: Optional[Path] = None
    try:
        lease = _acquire_lease(root, instance_data, command_id)
        snapshot = load_snapshot(root, include_text=False)
        actual_revision = int(snapshot.get("revision") or 0)
        if actual_revision != expected_revision:
            raise SJSV4Error("REVISION_CONFLICT", expected=expected_revision, actual=actual_revision)
        next_revision = actual_revision + 1
        target_texts: Dict[str, str] = {}
        target_bases: Dict[str, Path] = {}
        before_hashes: Dict[str, Optional[str]] = {}
        updated_cards: Dict[str, dict] = {}
        card_ops: Dict[str, Mapping[str, Any]] = {}
        manifest_override: Optional[dict] = None

        def add_target(base: Path, relative: str, text: str, operation: Mapping[str, Any]) -> None:
            key = _migration_target_key(base, root, relative)
            base = base.resolve()
            if key in target_texts:
                if target_bases[key].resolve() != base or target_texts[key] != text:
                    raise SJSV4Error("MIGRATION_TARGET_CONFLICT", path=relative)
                return
            target_texts[key] = str(text)
            target_bases[key] = base
            before_hashes[key] = _target_hash(root, relative, base)

        for raw_operation in operations:
            if not isinstance(raw_operation, Mapping):
                raise SJSV4Error("MIGRATION_OPERATION_INVALID")
            operation = dict(raw_operation)
            name = str(operation.get("op") or operation.get("kind") or "").strip().lower().replace("-", "_")
            base, relative = _migration_path(root, operation)
            current_hash = _target_hash(root, relative, base)
            if "if_absent_or_same_sha256" in operation:
                expected_hash = operation.get("if_absent_or_same_sha256")
                # Archive destinations are content-addressed copies: the
                # first migration is allowed to create an absent destination
                # even though its expected hash is the source hash.  A source
                # replacement still requires the source to exist and match
                # its explicit ``source_sha256`` below.  Existing archives
                # must match exactly so a changed archive cannot be silently
                # replaced.
                archive_target = (
                    base.resolve() == root.resolve()
                    and relative.replace("\\", "/").lower().startswith("knowledge/v4-migration/archive/")
                )
                absent_archive = current_hash is None and expected_hash is not None and archive_target
                if current_hash != expected_hash and not (current_hash is None and expected_hash is None) and not absent_archive:
                    raise SJSV4Error("MIGRATION_SOURCE_DRIFT", path=relative, expected=expected_hash, actual=current_hash)
            if name in {"replace_card", "replace"}:
                source_hash = operation.get("source_sha256")
                if source_hash is not None and current_hash != source_hash:
                    raise SJSV4Error("MIGRATION_SOURCE_DRIFT", path=relative, expected=source_hash, actual=current_hash)
                if operation.get("new_text") is not None:
                    text = str(operation.get("new_text"))
                else:
                    text = render_card({
                        "metadata": dict(operation.get("metadata") or {}),
                        "sections": dict(operation.get("sections") or {}),
                        "path": relative,
                    })
                card = parse_card_text(text, relative)
                card_id = str(operation.get("card_id") or card.get("id") or "")
                if not card_id:
                    raise SJSV4Error("CARD_ID_MISSING")
                card["id"] = card_id
                card.setdefault("metadata", {})["id"] = card_id
                updated_cards[card_id] = card
                card_ops[card_id] = operation
                add_target(base, relative, text, operation)
            elif name in {"create_card", "create", "create_ready_card"}:
                if current_hash is not None:
                    raise SJSV4Error("MIGRATION_TARGET_EXISTS", path=relative)
                text = str(operation.get("new_text") or render_card({
                    "metadata": dict(operation.get("metadata") or {}),
                    "sections": dict(operation.get("sections") or {}),
                    "path": relative,
                }))
                card = parse_card_text(text, relative)
                card_id = str(operation.get("card_id") or card.get("id") or "")
                if not card_id:
                    raise SJSV4Error("CARD_ID_MISSING")
                card["id"] = card_id
                card.setdefault("metadata", {})["id"] = card_id
                if card_id in (snapshot.get("cards_by_id") or {}) or card_id in updated_cards:
                    raise SJSV4Error("DUPLICATE_CARD_ID", card_id=card_id)
                updated_cards[card_id] = card
                card_ops[card_id] = operation
                add_target(base, relative, text, operation)
            elif name in {"write_file", "write"}:
                content = operation.get("content")
                if content is None:
                    raise SJSV4Error("MIGRATION_CONTENT_MISSING", path=relative)
                if base.resolve() == root.resolve() and relative.replace("\\", "/") == "INSTANCE.json":
                    try:
                        loaded_manifest = json.loads(str(content))
                    except (TypeError, ValueError) as exc:
                        raise SJSV4Error("INSTANCE_MANIFEST_INVALID", error=str(exc))
                    if not isinstance(loaded_manifest, dict):
                        raise SJSV4Error("INSTANCE_MANIFEST_INVALID")
                    manifest_override = loaded_manifest
                add_target(base, relative, str(content), operation)
            else:
                raise SJSV4Error("UNKNOWN_MIGRATION_OPERATION", operation=name)

        for card_id, card in updated_cards.items():
            meta = card.setdefault("metadata", {})
            meta["revision"] = next_revision
            meta["last_command_id"] = command_id
            card["revision"] = next_revision
            card["last_command_id"] = command_id
            relative = _safe_relative(root, card.get("path") or "")
            # The migration operation text was rendered before the transaction
            # revision existed; render it once more so card revision and all
            # generated views advance together.
            target_texts[relative] = render_card(card)
            target_bases[relative] = root
            before_hashes.setdefault(relative, _target_hash(root, relative, root))

        prospective = _make_prospective_snapshot(snapshot, updated_cards, next_revision)
        # INSTANCE.json is part of the same migration transaction.  Render
        # projections against the manifest that will be committed, rather
        # than mixing a new protocol/instance id with the old one.
        if manifest_override is not None:
            prospective["instance"] = copy.deepcopy(manifest_override)
        for relative, text in render_projections(prospective).items():
            add_target(root, relative, text, {"op": "generated_projection"})

        targets = sorted(target_texts)
        # Registry is the only external target; all other migration paths have
        # already gone through _safe_relative.  Check every target's current
        # bytes before and after backup to catch an editor race.
        for key in targets:
            base = target_bases[key]
            rel = _safe_relative(base, key)
            current = _target_hash(root, rel, base)
            if current != before_hashes[key]:
                raise SJSV4Error("DRIFT_DETECTED", paths=[key])
        tx_dir = _transaction_dir(root, instance_data, command_id)
        tx_dir.mkdir(parents=True, exist_ok=True)
        backups = _backup_targets(root, tx_dir, targets, target_bases=target_bases)
        for key in targets:
            base = target_bases[key]
            rel = _safe_relative(base, key)
            current = _target_hash(root, rel, base)
            if current != before_hashes[key]:
                raise SJSV4Error("DRIFT_DETECTED_DURING_BACKUP", paths=[key])
        staged = _stage_targets(tx_dir, target_texts, target_bases=target_bases, root=root)
        backup_by_path = {item["path"]: item for item in backups}
        tx = {
            "schema": TX_SCHEMA,
            "command_id": command_id,
            "command_digest": _command_digest(command),
            "migration": True,
            "expected_revision": expected_revision,
            "base_revision": actual_revision,
            "next_revision": next_revision,
            "phase": "prepared",
            "created_at": _now(),
            "instance_root": str(root),
            "targets": [dict(stage, **backup_by_path[stage["path"]]) for stage in staged],
            "operations": [dict(item) for item in operations],
        }
        journal_path = tx_dir / "journal.json"
        _write_journal(journal_path, tx)
        _fault("after_prepare")
        tx["phase"] = "backed_up"
        _write_journal(journal_path, tx)
        _fault("after_backup")
        drift_now = _recover_drift(root, tx)
        if drift_now:
            raise SJSV4Error("DRIFT_DETECTED_DURING_APPLY", paths=drift_now)
        _write_staged(root, tx)
        _fault("after_targets")
        tx["phase"] = "applied"
        _write_journal(journal_path, tx)
        _fault("after_apply")
        tx["phase"] = "committed"
        tx["committed_at"] = _now()
        _write_journal(journal_path, tx)
        _fault("after_commit")
        return _journal_result(tx)
    except SJSV4Error:
        raise
    except Exception as exc:
        raise SJSV4Error("MIGRATION_APPLY_FAILED", "MIGRATION_APPLY_FAILED: %s" % exc)
    finally:
        if lease is not None:
            _release_lease(lease)


def apply_command(instance: Any, command: Mapping[str, Any]) -> dict:
    """Apply a structured command with CAS, durable recovery and idempotency."""
    if not isinstance(command, Mapping):
        raise SJSV4Error("COMMAND_INVALID")
    if command.get("file_operations") is not None or command.get("migration_schema"):
        return apply_migration(instance, command)
    command_id = str(command.get("command_id") or "").strip()
    if not command_id:
        raise SJSV4Error("COMMAND_ID_MISSING")
    root = _instance_path(instance)
    initial = load_snapshot(root, include_text=False)
    instance_data = initial.get("instance") or {}
    existing_path = _find_transaction(root, instance_data, command_id)
    if existing_path:
        existing = _load_journal(existing_path)
        if existing.get("command_digest") != _command_digest(command):
            raise SJSV4Error("COMMAND_ID_REUSE", command_id=command_id)
        if existing.get("phase") == "committed":
            existing["replayed"] = True
            return _journal_result(existing)
        # A same-command retry resumes a prepared journal; no duplicate writes.
        return recover_transaction(root, command_id=command_id, action="resume")
    unfinished = _find_unfinished_transaction(root, instance_data)
    if unfinished:
        raise SJSV4Error("UNFINISHED_TRANSACTION", command_id=unfinished.get("command_id"))
    expected_raw = command.get("expected_revision")
    if expected_raw is None:
        raise SJSV4Error("EXPECTED_REVISION_MISSING")
    try:
        expected_revision = int(expected_raw)
    except (TypeError, ValueError):
        raise SJSV4Error("EXPECTED_REVISION_INVALID")
    lease: Optional[Path] = None
    tx_dir: Optional[Path] = None
    try:
        lease = _acquire_lease(root, instance_data, command_id)
        snapshot = load_snapshot(root, include_text=False)
        if int(snapshot.get("revision") or 0) != expected_revision:
            raise SJSV4Error("REVISION_CONFLICT", expected=expected_revision, actual=snapshot.get("revision"))
        operations = _command_operations(command)
        updated_cards: Dict[str, dict] = {}
        events: List[dict] = []
        touched_order: List[str] = []
        now = str(command.get("at") or _now())
        for operation in operations:
            card_id, shape = _operation_card_ref(operation, command)
            card = updated_cards.get(card_id or "") if card_id else None
            if card is None:
                card = _card_from_snapshot(snapshot, card_id, shape)
            if card is None:
                if _operation_name(operation) in {"create", "create_card"}:
                    metadata = dict(operation.get("metadata") or {})
                    metadata.setdefault("id", card_id or operation.get("id"))
                    metadata.setdefault("status", "ready")
                    metadata.setdefault("shape", operation.get("shape"))
                    path_value = operation.get("path") or "jobs/%s.md" % metadata.get("id")
                    path_value = _safe_relative(root, path_value)
                    existing_paths = {
                        str(item.get("path") or "").replace("\\", "/")
                        for item in (snapshot.get("cards") or [])
                        if item.get("path")
                    }
                    existing_paths.update(
                        str(item.get("path") or "").replace("\\", "/")
                        for item in updated_cards.values()
                        if item.get("path")
                    )
                    if path_value in existing_paths or _target_hash(root, path_value, root) is not None:
                        raise SJSV4Error("CARD_PATH_COLLISION", path=path_value)
                    card = {"metadata": metadata, "sections": dict(operation.get("sections") or {}), "path": path_value}
                    card.update(metadata)
                else:
                    raise SJSV4Error("CARD_NOT_FOUND", card_id=card_id, shape=shape)
            _validate_operation_preconditions(
                card,
                operation,
                _make_prospective_snapshot(snapshot, updated_cards, int(snapshot.get("revision") or 0)),
            )
            updated, event = _apply_operation(card, operation, now)
            updated_cards[str(updated.get("id"))] = updated
            touched_order.append(str(updated.get("id")))
            events.append(event)
        _reconcile_global_norms(snapshot, updated_cards, now, touched_order)
        next_revision = int(snapshot.get("revision") or 0) + 1
        for card in updated_cards.values():
            card.setdefault("metadata", {})["revision"] = next_revision
            card["revision"] = next_revision
            card["metadata"]["last_command_id"] = command_id
            card["last_command_id"] = command_id
            for event in card["metadata"].get("events") or []:
                if event.get("at") == now and event.get("revision") is None:
                    event["revision"] = next_revision
            if any(event.get("status") == "accepted" and event.get("at") == now for event in card["metadata"].get("events") or []):
                # Preserve the original acceptance timestamp while retaining a
                # revisioned acceptance history for patches/retries.
                history = card["metadata"].get("acceptance_history")
                if not isinstance(history, list):
                    history = []
                if not any(item.get("revision") == next_revision for item in history if isinstance(item, Mapping)):
                    history.append({"revision": next_revision, "accepted_at": card["metadata"].get("accepted_at"), "command_id": command_id})
                card["metadata"]["acceptance_history"] = history
                card["acceptance_history"] = history
            for patch_item in card["metadata"].get("patches") or []:
                if isinstance(patch_item, Mapping) and patch_item.get("revision") != next_revision and patch_item.get("command_id") is None:
                    patch_item["revision"] = next_revision
                    patch_item["command_id"] = command_id
        expected_hashes = command.get("expected_hashes") or command.get("hashes") or {}
        if not isinstance(expected_hashes, Mapping):
            raise SJSV4Error("EXPECTED_HASHES_INVALID")
        expected_hashes = {_safe_relative(root, key): value for key, value in expected_hashes.items()}
        targets = list(_prepare_targets(root, snapshot, updated_cards, next_revision))
        drift = _target_drift(root, snapshot, targets, expected_hashes)
        # A card may be newly created; source absence is expected only in that case.
        operation_names = {_operation_name(operation) for operation in operations}
        for card in updated_cards.values():
            relative = _safe_relative(root, card.get("path") or "")
            if relative not in (snapshot.get("files") or {}) and not operation_names.intersection({"create", "create_card"}):
                drift.append(relative)
        if drift:
            raise SJSV4Error("DRIFT_DETECTED", paths=_unique(drift))
        tx_dir = _transaction_dir(root, instance_data, command_id)
        tx_dir.mkdir(parents=True, exist_ok=True)
        target_texts = _prepare_targets(root, snapshot, updated_cards, next_revision)
        backups = _backup_targets(root, tx_dir, targets)
        # Recheck immediately after taking the backups.  The first CAS check
        # protects the planning window; this second check closes the race in
        # which an external editor changes a target between planning and the
        # last backup.  Without it the backup would faithfully capture the
        # wrong bytes and recovery could overwrite that external update.
        drift_after_backup = _target_drift(root, snapshot, targets, expected_hashes)
        if drift_after_backup:
            raise SJSV4Error("DRIFT_DETECTED_DURING_BACKUP", paths=drift_after_backup)
        staged = _stage_targets(tx_dir, target_texts)
        tx = {
            "schema": TX_SCHEMA,
            "command_id": command_id,
            "command_digest": _command_digest(command),
            "expected_revision": expected_revision,
            "base_revision": int(snapshot.get("revision") or 0),
            "next_revision": next_revision,
            "phase": "prepared",
            "created_at": _now(),
            "instance_root": str(root),
            "targets": [dict(stage, **next(item for item in backups if item["path"] == stage["path"])) for stage in staged],
            "operations": operations,
            "events": events,
        }
        journal_path = tx_dir / "journal.json"
        _write_journal(journal_path, tx)
        _fault("after_prepare")
        tx["phase"] = "backed_up"
        _write_journal(journal_path, tx)
        _fault("after_backup")
        drift_now = _recover_drift(root, tx)
        if drift_now:
            raise SJSV4Error("DRIFT_DETECTED_DURING_APPLY", paths=drift_now)
        _write_staged(root, tx)
        _fault("after_targets")
        tx["phase"] = "applied"
        _write_journal(journal_path, tx)
        _fault("after_apply")
        tx["phase"] = "committed"
        tx["committed_at"] = _now()
        _write_journal(journal_path, tx)
        _fault("after_commit")
        return _journal_result(tx)
    except SJSV4Error:
        raise
    except Exception as exc:
        # Journal remains for explicit recover/rollback if it was prepared.
        raise SJSV4Error("APPLY_FAILED", "APPLY_FAILED: %s" % exc)
    finally:
        if lease is not None:
            # Faults before commit intentionally leave the journal but release the
            # process lease, allowing the explicit recovery command to proceed.
            _release_lease(lease)


def _find_unfinished_transaction(root: Path, instance_data: Mapping[str, Any]) -> Optional[dict]:
    directory = _private_root(root, instance_data) / "transactions"
    if not directory.is_dir():
        return None
    for journal_path in sorted(directory.glob("*/journal.json")):
        try:
            journal = _load_journal(journal_path)
        except SJSV4Error:
            continue
        if journal.get("phase") in {"prepared", "backed_up", "applied"}:
            return journal
    return None


def recover_transaction(instance: Any, *, command_id: Optional[str] = None, action: str = "resume") -> dict:
    root = _instance_path(instance)
    snapshot = load_snapshot(root)
    instance_data = snapshot.get("instance") or {}
    journal: Optional[dict] = None
    path: Optional[Path] = None
    if command_id:
        path = _find_transaction(root, instance_data, command_id)
        if path:
            journal = _load_journal(path)
    else:
        journal = _find_unfinished_transaction(root, instance_data)
        if journal:
            path = _transaction_dir(root, instance_data, str(journal.get("command_id"))) / "journal.json"
    if not journal or path is None:
        raise SJSV4Error("NO_TRANSACTION")
    if journal.get("phase") == "committed":
        return _journal_result(journal)
    lease = _acquire_lease(root, instance_data, str(journal.get("command_id")), allow_same=True)
    try:
        drift = _recover_drift(root, journal)
        if drift:
            raise SJSV4Error("DRIFT_DETECTED_DURING_RECOVERY", paths=drift)
        if action in {"rollback", "roll_back"}:
            for record in journal.get("targets") or []:
                base = _record_base(root, record)
                relative = _safe_relative(base, record.get("path"))
                destination = base / relative
                backup = record.get("backup")
                backup_path = _safe_private_path(root, backup) if backup else None
                if record.get("exists_before") and backup_path and backup_path.is_file():
                    _atomic_copy(backup_path, destination)
                elif not record.get("exists_before") and (destination.exists() or destination.is_symlink()):
                    destination.unlink()
            journal["phase"] = "rolled_back"
            journal["rolled_back_at"] = _now()
            _write_journal(path, journal)
            return _journal_result(journal)
        _write_staged(root, journal)
        _fault("after_targets")
        journal["phase"] = "committed"
        journal["committed_at"] = _now()
        _write_journal(path, journal)
        return _journal_result(journal)
    finally:
        _release_lease(lease)


def build_context(snapshot: Mapping[str, Any], card_id: Optional[str] = None, *, max_chars: int = 12000) -> dict:
    """Produce a compact start/context payload without reading additional files."""
    card = _card_from_snapshot(snapshot, card_id)
    if card is None and card_id:
        raise SJSV4Error("CARD_NOT_FOUND", card_id=card_id)
    if card is None:
        active = [item for item in snapshot.get("cards") or [] if _status(item) in {"doing", "awaiting_acceptance"}]
        card = active[0] if active else None
    card_summary = None
    if card:
        eligibility = dependency_eligibility(card, snapshot)
        card_summary = {
            "id": card.get("id"),
            "path": card.get("path"),
            "shape": card.get("shape"),
            "status": _status(card),
            "revision": card.get("revision", 0),
            "goal": _safe_goal(card),
            "depends_on": card.get("depends_on") or (card.get("metadata") or {}).get("depends_on") or [],
            "dependency_eligibility": eligibility,
            "questions": _section_value(card, "questions")[-2000:],
            "acceptance": _section_value(card, "acceptance")[-3000:],
            "artifacts": _section_value(card, "artifacts")[-3000:],
        }
    payload = {
        "schema": "serial-job-session-context/v4",
        "revision": snapshot.get("revision", 0),
        "instance": {
            "id": (snapshot.get("instance") or {}).get("instance_id"),
            "domain_name": (snapshot.get("instance") or {}).get("domain_name"),
            "route_key": (snapshot.get("instance") or {}).get("route_key"),
            "protocol_version": (snapshot.get("instance") or {}).get("protocol_version"),
        },
        "card": card_summary,
        "active": [
            {"id": item.get("id"), "status": _status(item), "shape": item.get("shape"), "goal": _safe_goal(item)}
            for item in snapshot.get("cards") or []
            if _status(item) in {"doing", "awaiting_acceptance"}
        ],
        "warnings": list(snapshot.get("warnings") or []),
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if len(raw) > max_chars:
        payload["warnings"].append("CONTEXT_BOUNDED")
        payload["active"] = payload["active"][:10]
        if payload.get("card"):
            payload["card"]["acceptance"] = str(payload["card"].get("acceptance") or "")[:500]
            payload["card"]["artifacts"] = str(payload["card"].get("artifacts") or "")[:500]
    return payload


def handoff_context(snapshot: Mapping[str, Any], card_id: str, *, max_chars: int = 12000) -> dict:
    payload = build_context(snapshot, card_id, max_chars=max_chars)
    card = payload.get("card") or {}
    payload["handoff"] = {
        "card_id": card.get("id"),
        "instruction": "Continue the named card from its current status. Read the card and direct evidence before writing.",
        "read_paths": [card.get("path")] if card.get("path") else [],
    }
    return payload


def _adapter_call(module_name: str, function_name: str, *args: Any, **kwargs: Any) -> Any:
    try:
        module = importlib.import_module(module_name)
    except (ImportError, ModuleNotFoundError):
        return None
    function = getattr(module, function_name, None)
    if not callable(function):
        return None
    return function(*args, **kwargs)


def context_plugin(instance: Any, snapshot: Optional[Mapping[str, Any]] = None, card_id: Optional[str] = None, **kwargs: Any) -> Optional[dict]:
    root = _instance_path(instance)
    payload = snapshot or load_snapshot(root)
    card = _card_from_snapshot(payload, card_id)
    if card is None:
        return {"schema": "serial-job-session-context/v4", "warnings": ["CARD_NOT_FOUND"], "card_id": card_id}
    # ``context`` is the adapter's instance-aware hook.  It reads the stable
    # NORTH-STAR/ENV/knowledge pointers and then delegates to its pure
    # renderer.  Calling ``build_context(card, snapshot)`` here would bypass
    # those sources and silently return a card-only context.
    adapter_kwargs = dict(kwargs)
    if "budget_chars" in adapter_kwargs and "max_chars" not in adapter_kwargs:
        adapter_kwargs["max_chars"] = adapter_kwargs.pop("budget_chars")
    # The public core keeps exactly seven parent sections.  The context
    # adapter also supports semantic child aliases, so provide a private
    # projection for that call without changing the authoritative snapshot.
    adapter_payload = copy.deepcopy(dict(payload))
    adapter_cards = []
    for source_card in payload.get("cards") or []:
        projected_card = copy.deepcopy(source_card)
        projected_sections = dict(projected_card.get("sections") or {})
        for alias in ("questions", "decisions", "acceptance", "feedback", "patches", "current_state", "next_steps"):
            value = _section_value(source_card, alias)
            if value:
                projected_sections[alias] = value
        projected_card["sections"] = projected_sections
        adapter_cards.append(projected_card)
    adapter_payload["cards"] = adapter_cards
    adapter_payload["cards_by_id"] = {str(item.get("id")): item for item in adapter_cards if item.get("id")}
    result = _adapter_call(
        "sjs_v4_context",
        "context",
        root,
        adapter_payload,
        card_id=card_id,
        **adapter_kwargs,
    )
    if result is None:
        # Keep a deterministic local fallback for environments where the
        # optional adapter is genuinely unavailable, while surfacing that
        # fact to callers rather than pretending it was instance-aware.
        result = _adapter_call("sjs_v4_context", "build_context", card, adapter_payload, **kwargs)
    if result is None:
        return {"schema": "serial-job-session-context/v4", "warnings": ["CONTEXT_ADAPTER_MISSING"], "card_id": card.get("id")}
    # Keep effective live source pointers while exposing archive provenance
    # carried by migrated cards/norm records.  Context never inlines archive
    # documents; this is a small, auditable path/line map.
    provenance: List[dict] = []
    selected = _card_from_snapshot(payload, card_id)
    if selected is not None:
        meta = selected.get("metadata") if isinstance(selected.get("metadata"), Mapping) else selected
        refs: List[Any] = []
        if meta.get("archive_ref"):
            refs.append(meta.get("archive_ref"))
        for key in ("norms", "fact_records", "knowledge"):
            values = meta.get(key)
            if isinstance(values, Mapping):
                values = [values]
            if isinstance(values, list):
                for item in values:
                    if isinstance(item, Mapping):
                        refs.extend(
                            item.get(name)
                            for name in ("source", "source_ref", "source_archive", "archive_path", "evidence", "archive_ref")
                            if item.get(name)
                        )
                        source_refs = item.get("source_refs")
                        if isinstance(source_refs, (list, tuple, set)):
                            refs.extend(source_refs)
                        elif source_refs:
                            refs.append(source_refs)
        for raw in refs:
            raw_text = str(raw)
            # Migration provenance may still point at the effective NORMS.md
            # projection.  Its line number is stable evidence for the
            # archived source as well, so expose the archive counterpart while
            # preserving the live path in the adapter's existing source_refs.
            for live_name in ("NORMS.md", "FACTS.md", "NORTH-STAR.md"):
                live_match = re.search(r"(?:^|[/\\])%s#L(\d+)" % re.escape(live_name), raw_text, flags=re.I)
                if live_match:
                    archive_item = {
                        "kind": "archive_provenance",
                        "path": "knowledge/v4-migration/archive/%s" % live_name,
                        "line": int(live_match.group(1)),
                    }
                    if archive_item not in provenance:
                        provenance.append(archive_item)
            for token in re.findall(r"(?:[A-Za-z0-9_.-]+/)*knowledge/v4-migration/archive/[^\s`),;]+|knowledge/v4-migration/archive/[^\s`),;]+", raw_text):
                clean = token.rstrip(".,;)")
                line = None
                match = re.search(r"#L(\d+)$", clean, flags=re.I)
                if match:
                    line = int(match.group(1))
                    clean = clean[: match.start()]
                item = {"kind": "archive_provenance", "path": clean}
                if line is not None:
                    item["line"] = line
                if item not in provenance:
                    provenance.append(item)
    # A migrated instance can retain stable-source records on the archive even
    # when an older card has no structured ``norms`` field yet.  Expose those
    # immutable control pointers alongside the live projection so callers can
    # audit provenance without reading a second source database.  Hash only
    # the archived bytes; context remains pointer-only.
    archive_root = root / "knowledge" / "v4-migration" / "archive"
    for archive_name in ("NORMS.md", "FACTS.md", "NORTH-STAR.md", "ENV.md"):
        archive_path = archive_root / archive_name
        if not archive_path.is_file():
            continue
        try:
            archive_hash = _sha_bytes(archive_path.read_bytes())
        except OSError:
            archive_hash = None
        item = {
            "kind": "archive_provenance",
            "path": "knowledge/v4-migration/archive/%s" % archive_name,
        }
        if archive_hash:
            item["sha256"] = archive_hash
        if item not in provenance:
            provenance.append(item)
    # Stable-source projections carry line-level evidence on each protected
    # rule.  Mirror those line pointers onto the archived counterpart when the
    # adapter supplied them, keeping the live path available as well.
    for constraint in (result.get("protected_constraints") if isinstance(result, Mapping) else []) or []:
        if not isinstance(constraint, Mapping):
            continue
        for ref in constraint.get("source_refs") or []:
            if not isinstance(ref, Mapping):
                continue
            live_path = str(ref.get("path") or "")
            name = Path(live_path).name
            if name not in {"NORMS.md", "FACTS.md", "NORTH-STAR.md", "ENV.md"}:
                continue
            item = {
                "kind": "archive_provenance",
                "path": "knowledge/v4-migration/archive/%s" % name,
            }
            if ref.get("line") is not None:
                item["line"] = ref.get("line")
            if item not in provenance:
                provenance.append(item)
    if provenance:
        existing_refs = result.get("source_refs") if isinstance(result, Mapping) else None
        result["source_refs"] = list(existing_refs or []) + provenance
        result["archive_provenance"] = provenance
    return result


def time_adapter(instance: Any, **kwargs: Any) -> Any:
    root = _instance_path(instance)
    result = _adapter_call("sjs_v4_time", "time_adapter", root, **kwargs)
    if result is not None:
        return result
    result = _adapter_call("sjs_v4_time", "build_time_record", **kwargs)
    return result if result is not None else {"schema": "serial-job-session-time/v4", "complete": False, "count_for_calibration": False, "warning": "TIME_ADAPTER_MISSING"}


def migrate_instance(instance: Any, mode: str = "dry-run", **kwargs: Any) -> dict:
    root = _instance_path(instance)
    result = _adapter_call("sjs_v4_migrate", "migrate_instance", root, mode, **kwargs)
    if result is not None:
        return result
    try:
        module = importlib.import_module("sjs_v4_migrate")
    except ImportError as exc:
        return {"ok": False, "mode": mode, "error": "MIGRATION_ADAPTER_MISSING", "details": str(exc)}
    builder = getattr(module, "build_plan", None)
    if not callable(builder):
        return {"ok": False, "mode": mode, "error": "MIGRATION_ADAPTER_INCOMPLETE"}
    plan_path = kwargs.get("plan")
    if mode in {"dry-run", "check"}:
        plan = builder(root, **{key: value for key, value in kwargs.items() if key in {"direction_config", "include_output"}})
        result = {
            "ok": True,
            "mode": mode,
            "schema": plan.get("schema"),
            "base_revision": plan.get("base_revision"),
            "summary": plan.get("summary") or {},
            "operation_count": len(plan.get("file_operations") or []),
        }
        if plan_path:
            target = Path(str(plan_path)).expanduser().resolve()
            _json_atomic(target, plan)
            result["plan"] = str(target)
        return result
    if mode in {"apply", "recover", "rollback"}:
        if mode == "apply":
            if plan_path:
                try:
                    plan = json.loads(Path(str(plan_path)).expanduser().resolve().read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    raise SJSV4Error("MIGRATION_PLAN_INVALID", error=str(exc))
                if not isinstance(plan, Mapping):
                    raise SJSV4Error("MIGRATION_PLAN_INVALID")
            else:
                plan = builder(root, **{key: value for key, value in kwargs.items() if key in {"direction_config", "include_output"}})
            command_id = str(kwargs.get("command_id") or kwargs.get("transaction") or ("v4-migrate-" + uuid.uuid4().hex))
            command = {
                "command_id": command_id,
                "expected_revision": int(plan.get("base_revision") or 0),
                "migration_schema": plan.get("schema"),
                "file_operations": plan.get("file_operations") or [],
            }
            result = apply_migration(root, command)
            result["mode"] = "apply"
            result["summary"] = plan.get("summary") or {}
            return result
        command_id = kwargs.get("command_id") or kwargs.get("transaction")
        if not command_id:
            raise SJSV4Error("TRANSACTION_ID_REQUIRED")
        return recover_transaction(root, command_id=str(command_id), action="rollback" if mode == "rollback" else "resume")
    return {"ok": False, "mode": mode, "error": "MIGRATION_MODE_UNSUPPORTED"}


def calibration_eligible(card: Mapping[str, Any]) -> bool:
    """Timing is optional; missing/incomplete timing never blocks acceptance."""
    meta = card.get("metadata") if isinstance(card.get("metadata"), Mapping) else card
    timing = meta.get("timing_last")
    if not isinstance(timing, Mapping):
        records = meta.get("timing_records")
        if isinstance(records, list) and records:
            timing = records[-1] if isinstance(records[-1], Mapping) else None
    if timing is not None:
        if not bool(timing.get("complete")) or not bool(timing.get("count_for_calibration", False)):
            return False
        return True
    if meta.get("timing_complete") is False or meta.get("timing_source") in {None, "missing", "incomplete"}:
        return False
    return meta.get("ai_net") is not None or meta.get("elapsed") is not None


__all__ = [
    "ALLOWED_STATUSES",
    "CARD_SCHEMA",
    "MIGRATION_CAPABILITIES",
    "SJSV4Error",
    "artifact_is_valid",
    "apply_command",
    "apply_migration",
    "build_context",
    "calibration_eligible",
    "check_instance",
    "check_snapshot",
    "context_plugin",
    "dependency_eligibility",
    "handoff_context",
    "load_snapshot",
    "migrate_instance",
    "parse_card",
    "parse_card_text",
    "recover_transaction",
    "render_card",
    "render_projections",
    "time_adapter",
]
