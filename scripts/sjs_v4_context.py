#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure context and handoff adapters for serial-job-session v4.

The v4 engine owns cards and snapshots.  This module deliberately does not
persist anything and does not import the engine: a card/snapshot may be a
mapping, a dataclass, or another object exposing attributes.  Keeping this
boundary small makes the adapter useful while the engine is being migrated
and makes it safe to exercise with fixtures.

The adapter is intentionally conservative.  It only projects information
that is relevant to the requested card, never scans historical output by
default, and will exceed the soft context budget rather than cutting a
protected rule.  All returned values are JSON serialisable.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple


CONTEXT_SCHEMA = "serial-job-session-context/v4"
SNAPSHOT_SCHEMA = "serial-job-session-snapshot/v4"
HANDOFF_SCHEMA = "serial-job-session-handoff/v4"
MENU_SCHEMA = "serial-job-session-menu/v4"
CONTEXT_BUDGET_CHARS = 12_000
DEFAULT_HANDOFF_BUDGET_CHARS = 4_000

_MISSING = object()
_CONTROL_SOURCE_NAMES = (
    "ENV.md",
    "NORTH-STAR.md",
    "CURRENT.md",
    "NORMS.md",
    "FACTS.md",
)
_SOURCE_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?P<key>stable[_ -]?id|id|tags?|kind|faces?|artifacts?|links?|source|evidence|protected|safety[_ -]?relevant|invariant|summary|rule|text)\s*:\s*(?P<value>.*?)\s*$",
    re.I,
)
_STABLE_ID_RE = re.compile(r"\b(?P<id>(?:NORM|FACT|RULE|CONSTRAINT)[-_A-Z0-9.]+)\b", re.I)


def _value(obj: Any, *names: str, default: Any = None) -> Any:
    """Read the first non-null field from a mapping or object."""

    if obj is None:
        return default
    for name in names:
        if isinstance(obj, Mapping):
            value = obj.get(name, _MISSING)
        else:
            value = getattr(obj, name, _MISSING)
        if value is not _MISSING and value is not None:
            return value
    return default


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return [value]
    if isinstance(value, Mapping):
        return [value]
    try:
        return list(value)
    except TypeError:
        return [value]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _state_summary(value: Any) -> Any:
    """Keep a current-state summary without replaying an old progress log.

    Cards may still expose a long legacy ``progress`` section.  The context
    contract needs the current state; historical detail is available from the
    card/source pointer when explicitly requested.  Safety-marked text is
    retained whole so this convenience compaction cannot discard a rule.
    """

    if not isinstance(value, str):
        return value
    text = value.strip()
    if len(text) <= 2400:
        return text
    lowered = text.casefold()
    safety_tokens = ("禁止", "不变量", "真相", "never", "must", "safety", "invariant", "protected")
    if any(token in lowered for token in safety_tokens):
        return text
    head = text[:1600].rstrip()
    tail = text[-600:].lstrip()
    return head + "\n[…historical progress omitted; read the card evidence/source explicitly…]\n" + tail


def _json_safe(value: Any) -> Any:
    """Make an engine value safe for JSON and deterministic hashing."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        return {
            str(k): _json_safe(v)
            for k, v in vars(value).items()
            if not str(k).startswith("_")
        }
    return str(value)


def _parse_source_value(value: str) -> Any:
    """Parse the small scalar/list values used by NORMS/FACTS annotations."""

    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("[") and text.endswith("]"):
        try:
            parsed = json.loads(text)
            return parsed
        except (TypeError, ValueError):
            return [part.strip().strip("'\"") for part in text[1:-1].split(",") if part.strip()]
    if text.casefold() in {"true", "yes", "y"}:
        return True
    if text.casefold() in {"false", "no", "n"}:
        return False
    return text.strip("`\"")


def _source_root(instance: Any, snapshot: Optional[Mapping[str, Any]]) -> Optional[Path]:
    if isinstance(instance, (str, Path)):
        return Path(instance).expanduser().resolve()
    if isinstance(instance, Mapping):
        value = _value(instance, "instance_root", "root", "path", default=None)
        if value:
            return Path(str(value)).expanduser().resolve()
    if isinstance(snapshot, Mapping):
        value = snapshot.get("instance_root")
        if value:
            return Path(str(value)).expanduser().resolve()
    return None


def _parse_knowledge_document(relative: str, text: str, sha256: str) -> List[dict]:
    """Parse annotated norm/fact blocks without scanning any other files.

    The parser deliberately keeps prose as a source-backed summary.  It only
    promotes fields explicitly written in the document (stable id, tags,
    kind/faces/artifact/link, evidence and protected markers); unannotated
    blocks remain visible as ``unclassified`` context and trigger a warning.
    """

    lines = text.splitlines()
    headings: List[Tuple[int, int, str]] = []
    for number, line in enumerate(lines, 1):
        match = re.match(r"^\s*(#{2,6})\s+(.+?)\s*$", line)
        if match:
            headings.append((number, len(match.group(1)), match.group(2).strip()))
    blocks: List[Tuple[int, int, str, str]] = []
    for index, (start, level, title) in enumerate(headings):
        end = len(lines)
        for next_start, next_level, _ in headings[index + 1 :]:
            if next_level <= level:
                end = next_start - 1
                break
        if end >= start:
            blocks.append((start, end, title, "\n".join(lines[start:end])))
    records: List[dict] = []
    source_kind = "norm" if relative.casefold() == "norms.md" else "fact"
    for start, end, title, body in blocks:
        fields: Dict[str, Any] = {}
        for line in body.splitlines():
            match = _SOURCE_LINE_RE.match(line)
            if not match:
                continue
            key = re.sub(r"[^a-z]", "", match.group("key").casefold())
            fields[key] = _parse_source_value(match.group("value"))
        stable = fields.get("stableid") or fields.get("id")
        if not stable:
            found = _STABLE_ID_RE.search(title)
            stable = found.group("id") if found else "%s:%s" % (relative, start)
            fields["stable_id_generated"] = True
        tags = fields.get("tags") or fields.get("tag") or []
        if isinstance(tags, str):
            tags = [tags]
        kind = fields.get("kind") or source_kind
        faces = fields.get("faces") or fields.get("face") or []
        if isinstance(faces, str):
            faces = [faces]
        artifacts = fields.get("artifacts") or fields.get("artifact") or []
        if isinstance(artifacts, str):
            artifacts = [artifacts]
        links = fields.get("links") or []
        if isinstance(links, str):
            links = [links]
        evidence = fields.get("evidence") or fields.get("source") or []
        if isinstance(evidence, str):
            evidence = [evidence]
        explicit_protected = fields.get("protected")
        if explicit_protected is None:
            explicit_protected = fields.get("safetyrelevant")
        if explicit_protected is None:
            explicit_protected = fields.get("invariant")
        safety_blob = (title + "\n" + body).casefold()
        protected = bool(explicit_protected) if explicit_protected is not None else bool(
            source_kind == "norm"
            and any(
                token in safety_blob
                for token in (
                    "禁止",
                    "不得",
                    "不允许",
                    "必须",
                    "硬停",
                    "永不",
                    "不可",
                    "never",
                    "prohibit",
                    "invariant",
                    "protected",
                    "immutable",
                    "production",
                    "published",
                    "atomic",
                )
            )
        )
        summary = str(fields.get("summary") or fields.get("rule") or "").strip()
        if not summary:
            summary = body.strip()
        if len(summary) > 4000:
            summary = summary[:3900].rstrip() + "\n[…source block compacted; read the source pointer for full text…]"
        record = {
            "stable_id": str(stable),
            "title": title,
            "kind": str(kind),
            "tags": [_text(item) for item in tags if _text(item)],
            "faces": [_text(item) for item in faces if _text(item)],
            "artifacts": [_text(item) for item in artifacts if _text(item)],
            "links": [_text(item) for item in links if _text(item)],
            "summary": summary,
            "protected": protected,
            "source_refs": [{"kind": source_kind, "path": relative, "line": start, "sha256": sha256}],
        }
        record.update({key: value for key, value in fields.items() if key in {"stable_id_generated"}})
        record["evidence_pointers"] = _pointer_refs(evidence, default_kind=source_kind)
        records.append(record)
    return records


def _northstar_constraints(text: str) -> List[str]:
    """Extract only explicitly safety-like north-star bullets for retention."""

    result: List[str] = []
    active = False
    for line in text.splitlines():
        heading = re.match(r"^\s*#{2,6}\s+(.+?)\s*$", line)
        if heading:
            title = heading.group(1).casefold()
            active = any(
                token in title
                for token in (
                    "禁止",
                    "不变量",
                    "真相",
                    "prohibit",
                    "safety",
                    "constraint",
                    "protected",
                    "invariant",
                    "go / no-go",
                    "go/no-go",
                )
            )
            continue
        if active and re.match(r"^\s*(?:[-*]|\d+\.)\s+", line):
            value = re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", line).strip()
            if value:
                if len(value) > 280:
                    value = value[:240].rstrip() + "\n[…constraint summary compacted; source pointer retained…]"
                result.append(value)
    return result


def _northstar_summary(text: str, *, max_chars: int = 3600) -> str:
    """Project the stable objective without embedding the whole NORTH-STAR."""

    lines = text.splitlines()
    headings: List[Tuple[int, str]] = []
    for number, line in enumerate(lines):
        match = re.match(r"^\s*(#{1,6})\s+(.+?)\s*$", line)
        if match:
            headings.append((number, match.group(2).strip()))
    wanted_tokens = (
        "目标",
        "goal",
        "mission",
        "north-star",
        "north star",
        "数据流",
        "真相",
        "纠偏",
        "baseline",
        "go / no-go",
        "go/no-go",
    )
    blocks: List[str] = []
    for index, (start, title) in enumerate(headings):
        if not any(token in title.casefold() for token in wanted_tokens):
            continue
        end = headings[index + 1][0] if index + 1 < len(headings) else len(lines)
        body = "\n".join(lines[start + 1 : end]).strip()
        if body:
            blocks.append("## %s\n%s" % (title, body))
    if not blocks:
        body = "\n".join(line for line in lines if line.strip()).strip()
        blocks = [body] if body else []
    summary = "\n\n".join(blocks)
    # Safety bullets are separately retained as protected constraints, so this
    # cap only applies to explanatory north-star prose.
    if len(summary) > max_chars:
        summary = summary[: max_chars - 64].rstrip() + "\n[…north-star summary compacted; source hash retained…]"
    return summary


def _protected_source_projection(item: Mapping[str, Any], *, max_chars: int = 180) -> dict:
    """Keep one compact, source-backed rule excerpt for the hard section."""

    summary = _text(item.get("summary"))
    if len(summary) > max_chars:
        lowered_tokens = ("禁止", "不得", "不允许", "必须", "never", "must", "prohibit", "immutable")
        lines = [
            line.strip()
            for line in summary.splitlines()
            if line.strip() and any(token in line.casefold() for token in lowered_tokens)
        ]
        if lines:
            summary = " ".join(lines)
        summary = summary[:max_chars].rstrip() + ("…" if len(summary) > max_chars else "")
    result = {
        "stable_id": item.get("stable_id"),
        "kind": item.get("kind", "norm"),
        "summary": summary,
        "protected": True,
        "source_refs": item.get("source_refs") or [],
    }
    return {key: value for key, value in result.items() if value not in (None, "", [])}


def _read_stable_sources(instance: Any, snapshot: Optional[Mapping[str, Any]]) -> dict:
    """Read the allowlisted control documents for the real context entrypoint."""

    root = _source_root(instance, snapshot)
    result = {
        "knowledge": [],
        "northstar": "",
        "protected_constraints": [],
        "source_refs": [],
        "sources": [],
        "supplementary_reads": [],
        "warnings": [],
    }
    protected_budget = 6000
    protected_chars = 0
    omitted_protected = 0
    if root is None:
        result["warnings"].append("SUPPLEMENTARY_READ_REQUIRED: instance root was not available for stable sources")
        return result
    instance_data = snapshot.get("instance") if isinstance(snapshot, Mapping) and isinstance(snapshot.get("instance"), Mapping) else {}
    required = [str(item).replace("\\", "/") for item in _as_list(instance_data.get("required_reads"))]
    for relative in _CONTROL_SOURCE_NAMES:
        path = root / relative
        if not path.is_file():
            if relative in required or relative in {"NORTH-STAR.md", "NORMS.md", "FACTS.md"}:
                result["supplementary_reads"].append(relative)
                result["warnings"].append("SUPPLEMENTARY_READ_REQUIRED: missing stable source %s" % relative)
            continue
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8", errors="replace")
        except OSError as exc:
            result["supplementary_reads"].append(relative)
            result["warnings"].append("SUPPLEMENTARY_READ_REQUIRED: cannot read %s (%s)" % (relative, exc))
            continue
        sha256 = hashlib.sha256(raw).hexdigest()
        pointer = {"kind": "stable_source", "path": relative, "bytes": len(raw), "sha256": sha256}
        result["source_refs"].append(pointer)
        result["sources"].append(pointer)
        if relative in {"NORMS.md", "FACTS.md"}:
            parsed = _parse_knowledge_document(relative, text, sha256)
            result["knowledge"].extend(parsed)
            # A card's selectors decide which knowledge prose is relevant,
            # but they cannot hide global safety rules.  Carry a compact
            # source-backed summary for every explicitly/semantically
            # protected norm.  The full document remains behind the pointer.
            for item in parsed:
                if not item.get("protected"):
                    continue
                protected = _protected_source_projection(item)
                rendered_size = len(json.dumps(protected, ensure_ascii=False, sort_keys=True))
                if protected_chars + rendered_size > protected_budget:
                    omitted_protected += 1
                    continue
                protected["projection"] = "protected_summary"
                result["protected_constraints"].append(protected)
        elif relative == "NORTH-STAR.md":
            result["northstar"] = _northstar_summary(text, max_chars=1400)
            for number, constraint in enumerate(_northstar_constraints(text), 1):
                result["protected_constraints"].append(
                    {
                        "stable_id": "NORTH-STAR:%s" % number,
                        "kind": "northstar",
                        "summary": constraint,
                        "protected": True,
                        "source_refs": [pointer],
                    }
                )
        elif relative == "ENV.md":
            # Keep environment boundaries as source pointers and retain only
            # explicitly marked prohibitions/invariants in the hard section.
            result["protected_constraints"].extend(
                _northstar_constraints(text)
            )
    if result["knowledge"] and not any(item.get("tags") or item.get("kind") or item.get("faces") or item.get("artifacts") or item.get("links") for item in result["knowledge"]):
        result["warnings"].append("SUPPLEMENTARY_READ_REQUIRED: stable knowledge has no explicit selectors")
    if omitted_protected:
        result["protected_constraints"].append(
            {
                "stable_id": "NORMS:protected-summary-omitted",
                "kind": "norm",
                "summary": "%s additional protected rule excerpts remain in NORMS.md; read the source before changing a protected domain invariant" % omitted_protected,
                "protected": True,
                "source_refs": [
                    item for item in result["source_refs"] if item.get("path") == "NORMS.md"
                ],
                "omitted_count": omitted_protected,
            }
        )
        result["warnings"].append(
            "SUPPLEMENTARY_READ_REQUIRED: protected norm summaries were compacted; read NORMS.md before changing a global invariant"
        )
    return result


def _string_set(values: Any) -> set[str]:
    """Normalise explicit tags/identifiers without inventing tags."""

    result: set[str] = set()
    for item in _as_list(values):
        if isinstance(item, Mapping):
            item = _value(item, "name", "id", "tag", "key", default="")
        text = _text(item)
        if text:
            result.add(text.casefold())
    return result


def _source_ref(value: Any, *, default_kind: str = "source") -> Optional[dict]:
    """Return a compact source pointer, never dereferencing a path."""

    if value is None:
        return None
    if isinstance(value, Mapping):
        pointer = {}
        for key in (
            "id",
            "kind",
            "type",
            "path",
            "uri",
            "url",
            "link",
            "line",
            "lines",
            "revision",
            "version",
            "sha256",
            "hash",
        ):
            if key in value and value[key] not in (None, ""):
                pointer[key] = _json_safe(value[key])
        if pointer:
            pointer.setdefault("kind", default_kind)
            return pointer
        return None
    text = _text(value)
    if not text:
        return None
    return {"kind": default_kind, "ref": text}


def _source_refs(value: Any, *, default_kind: str = "source") -> List[dict]:
    found: List[dict] = []
    seen: set[str] = set()
    for item in _as_list(value):
        pointer = _source_ref(item, default_kind=default_kind)
        if pointer is None:
            continue
        marker = json.dumps(pointer, sort_keys=True, ensure_ascii=False)
        if marker not in seen:
            seen.add(marker)
            found.append(pointer)
    return found


def _pointer_refs(value: Any, *, default_kind: str = "source") -> List[dict]:
    """Keep evidence pointers while dropping arbitrary evidence payload text."""

    found: List[dict] = []
    for item in _as_list(value):
        if isinstance(item, Mapping):
            pointer = _source_ref(item, default_kind=default_kind)
            if pointer:
                found.append(pointer)
            continue
        text = _text(item)
        if not text:
            continue
        lower = text.casefold()
        if (
            "/" in text
            or "\\" in text
            or "://" in text
            or lower.startswith(("artifact:", "evidence:", "ref:"))
            or lower.endswith((".md", ".json", ".txt", ".log", ".png", ".jpg", ".bgeo.sc"))
        ):
            found.append({"kind": default_kind, "ref": text})
    unique: List[dict] = []
    seen: set[str] = set()
    for pointer in found:
        marker = _canonical(pointer)
        if marker not in seen:
            seen.add(marker)
            unique.append(pointer)
    return unique


def _identifier(card: Any) -> str:
    raw = _value(card, "card_id", "id", "shape_id", "shape", "job_id", default="")
    return _text(raw)


def _revision(value: Any) -> Any:
    return _value(value, "revision", "card_revision", "version", "updated_revision", default=None)


def _canonical(value: Any) -> str:
    return json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def context_fingerprint(card: Any, knowledge: Any = None, *, query: Any = None) -> str:
    """Fingerprint all source inputs that affect a projected context.

    This function is pure.  Callers that cache a context must compare this
    fingerprint before using the cached value; no on-disk cache is created by
    this adapter.
    """

    payload = {
        "card": _json_safe(card),
        "knowledge": _json_safe(knowledge),
        "query": _json_safe(query),
        "adapter": CONTEXT_SCHEMA,
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


@dataclass
class ContextCache:
    """Small opt-in in-memory cache with source fingerprint invalidation."""

    entries: Dict[str, Tuple[str, dict]] = field(default_factory=dict)

    def get(self, key: str, fingerprint: str) -> Optional[dict]:
        item = self.entries.get(str(key))
        if item is None or item[0] != fingerprint:
            if item is not None:
                self.entries.pop(str(key), None)
            return None
        return copy.deepcopy(item[1])

    def put(self, key: str, fingerprint: str, value: Mapping[str, Any]) -> dict:
        self.entries[str(key)] = (fingerprint, copy.deepcopy(dict(value)))
        return copy.deepcopy(dict(value))

    def invalidate(self, key: Optional[str] = None) -> None:
        if key is None:
            self.entries.clear()
        else:
            self.entries.pop(str(key), None)


def _normalise_dependency(dep: Any, *, include_output_evidence: bool = False) -> dict:
    """Project a direct dependency to accepted outputs and validity/version."""

    if isinstance(dep, Mapping):
        result: dict = {}
        dep_id = _value(dep, "card_id", "id", "shape_id", "shape", "job_id", default=None)
        if dep_id is not None:
            result["id"] = _json_safe(dep_id)
        for key in ("title", "name", "kind", "status", "outcome", "accepted", "accepted_at"):
            value = _value(dep, key, default=None)
            if value is not None and value != "":
                result[key] = _json_safe(value)
        outputs = _value(dep, "accepted_outputs", "outputs", "artifacts", "accepted_artifacts", default=None)
        if outputs is not None:
            result["accepted_outputs"] = _project_outputs(
                outputs, include_output_evidence=include_output_evidence
            )
        validity = _value(dep, "validity", "valid_until", "validity_window", "valid", default=None)
        if validity is not None:
            result["validity"] = _json_safe(validity)
        version = _value(dep, "version", "snapshot_version", "input_version", "revision", default=None)
        if version is not None:
            result["version"] = _json_safe(version)
        refs = _pointer_refs(
            _value(dep, "source_refs", "sources", "links", "evidence_pointers", default=None),
            default_kind="dependency",
        )
        if refs:
            result["source_refs"] = refs
        return result or {"value": _json_safe(dep)}
    return {"id": _json_safe(dep)}


def _project_outputs(outputs: Any, *, include_output_evidence: bool = False) -> List[Any]:
    projected: List[Any] = []
    for output in _as_list(outputs):
        if not isinstance(output, Mapping):
            projected.append(_json_safe(output))
            continue
        item: dict = {}
        for key in (
            "id",
            "name",
            "kind",
            "artifact",
            "artifact_id",
            "version",
            "snapshot_version",
            "validity",
            "valid_until",
            "status",
            "accepted",
            "sha256",
            "hash",
        ):
            if key in output and output[key] not in (None, ""):
                item[key] = _json_safe(output[key])
        refs = _pointer_refs(
            _value(output, "source_refs", "sources", "links", "evidence_pointers", default=None),
            default_kind="output",
        )
        if refs:
            item["source_refs"] = refs
        # Evidence payloads may be large and can be files.  We preserve a
        # pointer but never read or embed it unless explicitly requested.
        if include_output_evidence:
            evidence = _value(output, "evidence", "evidence_text", "details", default=None)
            if evidence is not None:
                item["evidence"] = _json_safe(evidence)
        projected.append(item or {"value": _json_safe(output)})
    return projected


def _normalise_knowledge_item(item: Any, *, include_evidence: bool = False) -> dict:
    if not isinstance(item, Mapping):
        return {"value": _json_safe(item)}
    result: dict = {}
    for key in (
        "stable_id",
        "stableId",
        "id",
        "title",
        "name",
        "kind",
        "artifact",
        "face",
        "faces",
        "tag",
        "tags",
        "summary",
        "text",
        "protected",
        "safety_relevant",
        "invariant",
        "priority",
        "version",
    ):
        if key in item and item[key] not in (None, ""):
            result[key] = _json_safe(item[key])
    refs = _pointer_refs(
        _value(item, "source_refs", "sources", "links", "evidence_pointers", "uri", "url", default=None),
        default_kind="knowledge",
    )
    if refs:
        result["source_refs"] = refs
    if include_evidence:
        evidence = _value(item, "evidence", "evidence_text", "details", default=None)
        if evidence is not None:
            result["evidence"] = _json_safe(evidence)
    return result or {"value": _json_safe(item)}


def _knowledge_match(item: Any, query: dict) -> Tuple[bool, int]:
    """Match explicit card tags/kind/faces/artifact/links; score is deterministic."""

    if not isinstance(item, Mapping):
        return False, 0
    item_tags = _string_set(_value(item, "tags", "tag", default=[]))
    item_kinds = _string_set(_value(item, "kind", "kinds", default=[]))
    item_faces = _string_set(_value(item, "faces", "face", default=[]))
    item_artifacts = _string_set(_value(item, "artifacts", "artifact", "artifact_id", default=[]))
    item_links = _string_set(_value(item, "links", "link", "uri", "url", default=[]))
    checks = (
        (query["tags"], item_tags),
        (query["kinds"], item_kinds),
        (query["faces"], item_faces),
        (query["artifacts"], item_artifacts),
        (query["links"], item_links),
    )
    explicit = [source for source, _ in checks if source]
    if not explicit:
        return False, 0
    score = 0
    matched_group = False
    for wanted, available in checks:
        if wanted and available & wanted:
            score += len(available & wanted)
            matched_group = True
    return matched_group, score


def _knowledge_projection(card: Any, knowledge: Any, *, include_evidence: bool = False) -> Tuple[List[dict], List[str], dict]:
    tags = _string_set(_value(card, "knowledge_tags", "tags", "required_tags", default=[]))
    kinds = _string_set(_value(card, "knowledge_kinds", "kinds", "required_kinds", "kind", default=[]))
    faces = _string_set(_value(card, "knowledge_faces", "faces", "required_faces", "face", default=[]))
    artifacts = _string_set(
        _value(card, "knowledge_artifacts", "artifacts", "artifact", "artifact_id", default=[])
    )
    links = _string_set(_value(card, "knowledge_links", "links", "link", "uri", "url", default=[]))
    query = {"tags": tags, "kinds": kinds, "faces": faces, "artifacts": artifacts, "links": links}
    warnings: List[str] = []
    if not any(query.values()):
        warnings.append(
            "SUPPLEMENTARY_READ_REQUIRED: card has no explicit knowledge tags/kind/faces/artifact/links; "
            "critical knowledge was not silently inferred"
        )
    # The v4 card contract may carry its parsed knowledge/norm entries in the
    # stable card sections.  Treat an explicitly supplied collection as the
    # authoritative source, while using the card's own collection when the
    # caller did not provide a second source.  This keeps one document source
    # of truth and avoids silently rendering an empty knowledge section.
    source = knowledge
    if source is None:
        source = _value(card, "knowledge", "norms", default=[])
    wanted = [item for item in _as_list(source)]
    scored: List[Tuple[int, int, dict]] = []
    for index, item in enumerate(wanted):
        matched, score = _knowledge_match(item, query)
        if matched:
            scored.append((-score, index, _normalise_knowledge_item(item, include_evidence=include_evidence)))
    scored.sort(key=lambda row: (row[0], row[1]))
    projected = [row[2] for row in scored]
    if any(query.values()) and not projected:
        warnings.append(
            "SUPPLEMENTARY_READ_REQUIRED: no knowledge item matched the card's explicit selectors; "
            "review domain knowledge before acting"
        )
    if not any(query.values()) and wanted:
        # Preserve an unclassified card knowledge entry as an explicitly
        # review-required item.  It is not silently promoted to a rule, and
        # the warning above tells the caller to read the supplementary source
        # before treating it as complete coverage.
        for item in wanted:
            if isinstance(item, Mapping) and item.get("active") is False:
                continue
            # A loaded NORMS/FACTS catalogue is intentionally not a history
            # dump.  Keep only entries explicitly marked protected here; the
            # rest remain available through the supplementary source pointer.
            # Small card-local unclassified entries stay visible so they are
            # never silently dropped.
            if isinstance(item, Mapping) and item.get("kind") in {"norm", "fact"} and not item.get("protected"):
                continue
            unclassified = _normalise_knowledge_item(item, include_evidence=include_evidence)
            unclassified["match_status"] = "unclassified"
            projected.append(unclassified)
    # A missing individual selector is useful to the caller and prevents a
    # partial match from being mistaken for complete safety coverage.
    for label, values in (
        ("tag", tags),
        ("kind", kinds),
        ("face", faces),
        ("artifact", artifacts),
        ("link", links),
    ):
        if values and not any(
            values & _string_set(_value(item, label + "s", label, default=[]))
            for item in wanted
            if isinstance(item, Mapping)
        ):
            warnings.append("SUPPLEMENTARY_READ_REQUIRED: no knowledge match for explicit %s selector" % label)
    return projected, warnings, query


def _protected_constraints(card: Any, knowledge: Any) -> List[Any]:
    """Collect global invariants and safety rules in stable source order."""

    values: List[Any] = []
    def compact(value: Any) -> Any:
        if isinstance(value, Mapping):
            # Card-local and migrated norm records may retain full evidence or
            # replacement prose.  The context keeps the rule excerpt and its
            # source refs; the authoritative record remains on the card.
            if _value(value, "summary", "text", "value", default=None) is not None:
                return _protected_source_projection(value, max_chars=180)
            return value
        if isinstance(value, str) and len(value) > 240:
            return value[:236].rstrip() + "…"
        return value
    for source in (
        _value(
            card,
            "protected_constraints",
            "protected_invariants",
            "global_invariants",
            "safety_constraints",
            "northstar_constraints",
            "northstar",
            default=[],
        ),
        _value(card, "constraints", "invariants", "rules", default=[]),
    ):
        values.extend(compact(value) for value in _as_list(source))
    source = knowledge
    if source is None:
        source = _value(card, "knowledge", "norms", default=[])
    for item in _as_list(source):
        if not isinstance(item, Mapping):
            continue
        if _value(item, "protected", "safety_relevant", "invariant", default=False):
            # Stable NORMS/FACTS entries can carry long historical evidence
            # and full replacement payloads.  The hard context section only
            # needs a compact, source-backed rule; the complete record stays
            # reachable through source_refs/supplementary reads.  Keeping the
            # raw record here duplicated the same text after _read_stable_sources
            # had already projected it, making a small card exceed its budget.
            values.append(_protected_source_projection(item))
    result: List[Any] = []
    seen: set[str] = set()
    for value in values:
        safe = _json_safe(value)
        stable_id = _value(safe, "stable_id", "stableId", default=None)
        marker = "stable_id:" + str(stable_id) if stable_id else _canonical(safe)
        if marker not in seen:
            seen.add(marker)
            result.append(safe)
    return result


def _section_text(name: str, value: Any) -> str:
    if value in (None, "", [], {}, ()):
        return ""
    if name == "protected_constraints" and isinstance(value, Mapping):
        summary = _text(value.get("summary") or value.get("text") or value.get("value"))
        if len(summary) > 180:
            summary = summary[:176].rstrip() + "…"
        refs = value.get("source_refs") or value.get("source_ref") or value.get("source_archive")
        pointers: List[str] = []
        for ref in _as_list(refs):
            if isinstance(ref, Mapping):
                path = ref.get("path") or ref.get("source")
                line = ref.get("line")
                if path:
                    pointers.append("%s%s" % (path, ("#L%s" % line) if line else ""))
            elif ref:
                pointers.append(str(ref))
        suffix = " [source: %s]" % ", ".join(pointers[:2]) if pointers else ""
        return summary + suffix
    if isinstance(value, str):
        return value.strip()
    return _canonical(value)


def _clip_non_safety(text: str, budget: int) -> Tuple[str, bool]:
    if len(text) <= budget:
        return text, False
    if budget <= 0:
        return "", True
    marker = "\n[…truncated for soft context budget; source refs retained…]"
    if budget <= len(marker):
        return text[:budget], True
    return text[: budget - len(marker)] + marker, True


def _render_context(sections: Mapping[str, Any], protected: List[Any]) -> Tuple[str, dict, List[str]]:
    """Render ordered sections and preserve contract-bearing content.

    The soft budget may compact optional knowledge prose.  It must never cut
    the card goal, state, questions, acceptance, dependency/blocker summary,
    downstream impact, north-star item, protected rules, or evidence entry
    points.  This distinction matters for a 90/38/89 start context: a small
    card with a long evidence note still retains the actionable contract.
    """

    order = (
        "goal",
        "current_state",
        "questions",
        "decisions",
        "next_steps",
        "acceptance",
        "dependencies",
        "downstream_impact",
        "northstar",
        "protected_constraints",
        "knowledge",
        "evidence",
    )
    lines: List[str] = []
    measurements: dict = {"limit_chars": CONTEXT_BUDGET_CHARS}
    warnings: List[str] = []
    safety_text = "\n".join(_section_text("protected_constraints", item) for item in protected)
    # These fields are the executable contract.  They are retained in full;
    # only optional knowledge prose is eligible for soft compaction.
    required_names = (
        "goal",
        "current_state",
        "questions",
        "decisions",
        "next_steps",
        "acceptance",
        "dependencies",
        "downstream_impact",
        "northstar",
        "evidence",
    )
    required_parts: List[str] = []
    for name in required_names:
        text = _section_text(name, sections.get(name))
        if text:
            required_parts.append("## %s\n%s" % (name, text))
    required_parts.append("## protected_constraints\n" + safety_text if safety_text else "")
    required = "\n\n".join(part for part in required_parts if part)
    optional_parts: List[str] = []
    for name in ("knowledge",):
        text = _section_text(name, sections.get(name))
        if text:
            optional_parts.append("## %s\n%s" % (name, text))
    optional_budget = max(0, CONTEXT_BUDGET_CHARS - len(required))
    optional, optional_clipped = _clip_non_safety("\n\n".join(optional_parts), optional_budget)
    if optional_clipped:
        warnings.append("CONTEXT_SOFT_BUDGET: optional knowledge prose was compacted")
    if required:
        lines.append(required)
    if optional:
        lines.append(optional)
    rendered = "\n\n".join(lines)
    measurements["chars"] = len(rendered)
    measurements["bytes"] = len(rendered.encode("utf-8"))
    measurements["over_budget"] = len(rendered) > CONTEXT_BUDGET_CHARS
    if measurements["over_budget"]:
        reason = "protected safety content is never truncated"
        warnings.append("CONTEXT_OVER_BUDGET: %s" % reason)
        measurements["over_budget_reason"] = reason
    measurements["source_chars"] = sum(len(_section_text(k, sections.get(k))) for k in order)
    measurements["source_bytes"] = sum(
        len(_section_text(k, sections.get(k)).encode("utf-8")) for k in order
    )
    return rendered, measurements, warnings


def _normalise_card_sections(
    card: Any,
    knowledge: Any,
    *,
    include_output_evidence: bool = False,
    include_evidence: bool = False,
) -> Tuple[dict, List[dict], List[str], List[Any]]:
    relevant_knowledge, knowledge_warnings, query = _knowledge_projection(
        card,
        knowledge,
        include_evidence=include_evidence,
    )
    protected = _protected_constraints(card, knowledge)
    dependencies = [
        _normalise_dependency(dep, include_output_evidence=include_output_evidence)
        for dep in _as_list(
            _value(card, "direct_dependencies", "dependencies", "depends_on", "accepted_dependencies", default=[])
        )
    ]
    source_refs = _pointer_refs(
        _value(card, "source_refs", "sources", "links", "evidence_pointers", default=[]),
        default_kind="card",
    )
    # Keep the text fields intentionally explicit.  A legacy `details` blob
    # is not allowed to smuggle a full history scan into a compact context.
    sections = {
        "goal": _value(card, "goal", "task_goal", "objective", "title", default=""),
        "current_state": _value(card, "current_state", "state", "status_summary", default=""),
        "questions": _value(card, "questions", "open_questions", "blocking_questions", default=[]),
        "decisions": _value(card, "decisions", "current_decisions", "decision", default=[]),
        "next_steps": _value(card, "next_steps", "next", default=[]),
        "acceptance": _value(card, "acceptance", "acceptance_criteria", "criteria", "done_when", default=[]),
        "dependencies": dependencies,
        "downstream_impact": _value(card, "downstream_impact", "downstream", "impact", default=[]),
        "northstar": _value(card, "northstar", "protected_northstar", "north_star", default=""),
        "protected_constraints": protected,
        "knowledge": relevant_knowledge,
        # Evidence is pointer-only.  There is no file read in this function.
        "evidence": source_refs,
        "knowledge_query": query,
    }
    return sections, source_refs, knowledge_warnings, protected


def compact_menu(
    cards: Iterable[Any],
    *,
    current_card_id: Any = None,
    include_history: bool = False,
) -> dict:
    """Generate a compact menu from current/ready metadata only by default."""

    entries: List[dict] = []
    all_cards = list(cards or [])
    by_id = {
        str(_identifier(card)): card
        for card in all_cards
        if _identifier(card)
    }
    by_shape = {
        str(_value(card, "shape", "shape_id", default="")): card
        for card in all_cards
        if _value(card, "shape", "shape_id", default=None) is not None
    }
    for card in all_cards:
        card_id = _identifier(card)
        if not card_id:
            continue
        status = _text(_value(card, "status", "state", default=""))
        is_current = current_card_id is not None and card_id == _text(current_card_id)
        if not include_history and not is_current and status not in {"ready", "running", "doing", "awaiting_acceptance"}:
            continue
        if not include_history and not is_current and status in {"running", "doing", "awaiting_acceptance"}:
            # Only the explicitly bound current card is shown from in-flight
            # history; an idle cold start remains a ready-card menu.
            continue
        dependencies = _as_list(_value(card, "depends_on", "dependencies", default=[]))
        blocked: List[str] = []
        if status in {"ready", "doing", "awaiting_acceptance", "running"}:
            for dependency in dependencies:
                dependency_key = str(_value(dependency, "id", "card_id", "shape", default=dependency))
                target = by_id.get(dependency_key) or by_shape.get(dependency_key)
                if target is None:
                    blocked.append("missing:%s" % dependency_key)
                    continue
                target_status = _text(_value(target, "status", "state", default=""))
                if target_status != "accepted":
                    blocked.append("%s:%s" % (dependency_key, target_status or "unknown"))
        entry = {
            "card_id": card_id,
            "shape": _json_safe(_value(card, "shape", "shape_id", default=None)),
            "title": _text(_value(card, "short_name", "title", "name", "goal", default="")),
            "phase": _json_safe(_value(card, "phase", "phase_id", default=None)),
            "status": _json_safe(_value(card, "status", "state", default=None)),
            "depends_on": _json_safe(_value(card, "depends_on", "dependencies", default=[])),
            "estimate_group": _json_safe(_value(card, "estimate_group", "estimate_class", default=None)),
            "current": is_current,
            "eligible": not blocked,
        }
        blocked_reason = _value(card, "blocked_reason", "block_reason", "gate_reason", default=None)
        if blocked and not blocked_reason:
            blocked_reason = "dependencies not accepted: " + ", ".join(blocked)
        if blocked_reason:
            entry["blocked_reason"] = _json_safe(blocked_reason)
        entries.append({key: value for key, value in entry.items() if value not in (None, "", [])})
    def shape_key(row: Mapping[str, Any]) -> Tuple[int, str]:
        raw = row.get("shape")
        try:
            return (int(raw), "")
        except (TypeError, ValueError):
            return (10**9, str(raw or row.get("card_id") or ""))
    entries.sort(key=lambda row: (str(row.get("phase", "")), shape_key(row), row["card_id"]))
    return {
        "schema": MENU_SCHEMA,
        "cards": entries,
        "historical_scan": bool(include_history),
        "output_evidence_read": False,
        "include_history": bool(include_history),
        "total_cards": len(all_cards),
        "omitted_cards": max(0, len(all_cards) - len(entries)),
    }


# Friendly aliases used by early engine prototypes.
build_menu = compact_menu
menu_from_cards = compact_menu


def build_context(
    card: Any,
    snapshot: Any = None,
    knowledge: Any = None,
    *,
    budget_chars: int = CONTEXT_BUDGET_CHARS,
    max_chars: Optional[int] = None,
    include_output_evidence: bool = False,
    include_evidence: bool = False,
    since_revision: Any = None,
    previous: Any = None,
    cache: Optional[ContextCache] = None,
    cache_key: Optional[str] = None,
) -> dict:
    """Build a bounded context projection for one card.

    ``snapshot`` is accepted for the engine boundary and is used as a source
    of card metadata only when the card does not provide it.  It is never
    interpreted as permission to read historical outputs.
    """

    if card is None:
        raise ValueError("card is required")
    if max_chars is not None:
        budget_chars = int(max_chars)
    source_card = card
    if isinstance(card, Mapping) and isinstance(card.get("sections"), Mapping):
        # This is the published sjs_v4 Card contract.  Adapt once at the
        # boundary so the rest of the projection operates on stable fields.
        source_card = _engine_card_projection(card, snapshot if isinstance(snapshot, Mapping) else {})
    elif snapshot is not None:
        # Overlay only absent scalar fields.  Card remains authoritative.
        if isinstance(card, Mapping) and isinstance(snapshot, Mapping):
            merged = dict(snapshot)
            merged.update(card)
            source_card = merged
    fp = context_fingerprint(source_card, knowledge, query={"since_revision": since_revision})
    key = cache_key or _identifier(source_card) or "context"
    if cache is not None:
        hit = cache.get(key, fp)
        if hit is not None:
            return hit
    sections, refs, warnings, protected = _normalise_card_sections(
        source_card,
        knowledge,
        include_output_evidence=include_output_evidence,
        include_evidence=include_evidence,
    )
    if include_evidence:
        # Explicit evidence is still limited to values carried by the card;
        # this adapter intentionally has no filesystem/network side effects.
        sections["evidence"] = _json_safe(
            _value(source_card, "evidence", "evidence_text", "details", default=sections["evidence"])
        )
    # Honour caller budgets while retaining the safety guarantee.  The
    # renderer uses the v4 default for its allocation; render again with a
    # caller-specific budget by temporarily applying a local equivalent.
    rendered, measurements, render_warnings = _render_context(sections, protected)
    if budget_chars != CONTEXT_BUDGET_CHARS:
        # Re-run the same required/optional allocation with the requested
        # limit.  The contract fields remain complete even when the result is
        # necessarily over the caller's soft budget.
        safety_text = "\n".join(_section_text("protected_constraints", item) for item in protected)
        required_parts = []
        for name in (
            "goal", "current_state", "questions", "decisions", "next_steps", "acceptance", "dependencies",
            "downstream_impact", "northstar", "evidence",
        ):
            text = _section_text(name, sections.get(name))
            if text:
                required_parts.append("## %s\n%s" % (name, text))
        if safety_text:
            required_parts.append("## protected_constraints\n" + safety_text)
        required = "\n\n".join(required_parts)
        knowledge_text = _section_text("knowledge", sections.get("knowledge"))
        optional, clipped = _clip_non_safety(
            ("## knowledge\n" + knowledge_text) if knowledge_text else "",
            max(0, int(budget_chars) - len(required)),
        )
        rendered = "\n\n".join(part for part in (required, optional) if part)
        measurements = {
            "limit_chars": int(budget_chars),
            "chars": len(rendered),
            "bytes": len(rendered.encode("utf-8")),
            "over_budget": len(rendered) > int(budget_chars),
            "source_chars": sum(len(_section_text(k, sections.get(k))) for k in sections),
            "source_bytes": sum(len(_section_text(k, sections.get(k)).encode("utf-8")) for k in sections),
        }
        render_warnings = ["CONTEXT_SOFT_BUDGET: non-safety sections were compacted"] if clipped else []
        if measurements["over_budget"]:
            measurements["over_budget_reason"] = "protected safety content is never truncated"
            render_warnings.append("CONTEXT_OVER_BUDGET: protected safety content is never truncated")
    all_warnings = list(dict.fromkeys(warnings + render_warnings))
    current_revision = _revision(source_card)
    result = {
        "schema": CONTEXT_SCHEMA,
        "snapshot_schema": SNAPSHOT_SCHEMA,
        "card_id": _identifier(source_card),
        "revision": current_revision,
        "goal": _json_safe(sections["goal"]),
        "current_state": _json_safe(sections["current_state"]),
        "questions": _json_safe(sections["questions"]),
        "decisions": _json_safe(sections["decisions"]),
        "next_steps": _json_safe(sections["next_steps"]),
        "acceptance": _json_safe(sections["acceptance"]),
        "dependencies": _json_safe(sections["dependencies"]),
        "downstream_impact": _json_safe(sections["downstream_impact"]),
        "northstar": _json_safe(sections["northstar"]),
        "protected_constraints": _json_safe(protected),
        "knowledge": _json_safe(sections["knowledge"]),
        "knowledge_query": _json_safe(sections["knowledge_query"]),
        "source_refs": _json_safe(refs),
        "warnings": all_warnings,
        "measurements": measurements,
        "text": rendered,
        "output_evidence_read": bool(include_output_evidence),
        "historical_scan": False,
        "fingerprint": fp,
    }
    if since_revision is not None or previous is not None:
        result["change_summary"] = context_change_summary(previous, result, since_revision=since_revision)
    if cache is not None:
        cache.put(key, fp, result)
    return result


def build_context_snapshot(*args: Any, **kwargs: Any) -> dict:
    return build_context(*args, **kwargs)


def context_change_summary(previous: Any, current: Any, *, since_revision: Any = None) -> dict:
    """Summarise changes since a revision, including newly relevant rules."""

    previous = previous or {}
    current = current or {}
    if hasattr(previous, "to_dict"):
        previous = previous.to_dict()
    if hasattr(current, "to_dict"):
        current = current.to_dict()
    if not isinstance(previous, Mapping):
        previous = _json_safe(previous)
    if not isinstance(current, Mapping):
        current = _json_safe(current)
    old_rules = {_canonical(x) for x in _as_list(previous.get("protected_constraints", []))}
    current_rules = _as_list(current.get("protected_constraints", []))
    newly_relevant = [x for x in current_rules if _canonical(x) not in old_rules]
    changed: List[str] = []
    for key in (
        "goal", "current_state", "questions", "acceptance", "dependencies",
        "downstream_impact", "northstar", "protected_constraints", "knowledge",
    ):
        if _canonical(previous.get(key)) != _canonical(current.get(key)):
            changed.append(key)
    return {
        "from_revision": since_revision if since_revision is not None else previous.get("revision"),
        "to_revision": current.get("revision"),
        "changed_sections": changed,
        "newly_relevant_constraints": _json_safe(newly_relevant),
        "has_new_constraints": bool(newly_relevant),
    }


incremental_context = context_change_summary
same_session_changes = context_change_summary


def build_handoff(
    card: Any,
    context: Any = None,
    *,
    target_session: Optional[str] = None,
    budget_chars: int = DEFAULT_HANDOFF_BUDGET_CHARS,
    include_output_evidence: bool = False,
) -> dict:
    """Build a concise same-card handoff; it never creates a replacement card."""

    # Engine cards keep semantic fields inside stable sections.  Project that
    # shape before falling back to card fields so decisions and next steps do
    # not disappear from a handoff when the caller supplied only a compact
    # context payload.
    handoff_card = card
    if isinstance(card, Mapping) and isinstance(card.get("sections"), Mapping):
        handoff_card = _engine_card_projection(card, {})
    raw = context if context is not None else build_context(card, include_output_evidence=include_output_evidence)
    if hasattr(raw, "to_dict"):
        raw = raw.to_dict()
    if not isinstance(raw, Mapping):
        raw = build_context(card, include_output_evidence=include_output_evidence)
    state = _value(raw, "current_state", "state", default=_value(handoff_card, "current_state", "state", default=""))
    evidence = _value(raw, "evidence", "source_refs", default=_value(handoff_card, "evidence", "source_refs", "links", default=[]))
    decisions = _value(raw, "decisions", default=_value(handoff_card, "decisions", "decision_log", default=[]))
    questions = _value(raw, "questions", default=_value(handoff_card, "questions", "open_questions", default=[]))
    next_steps = _value(raw, "next_steps", "next", default=_value(handoff_card, "next_steps", "next", default=[]))
    constraints = _value(raw, "protected_constraints", default=_protected_constraints(handoff_card, None))
    result = {
        "schema": HANDOFF_SCHEMA,
        "card_id": _identifier(card) or _value(raw, "card_id", default=""),
        "source_revision": _value(raw, "revision", default=_revision(card)),
        "target_session": target_session,
        "same_card": True,
        "new_card": False,
        "state": _json_safe(state),
        "evidence": _json_safe(evidence),
        "decisions": _json_safe(decisions),
        "questions": _json_safe(questions),
        "next_steps": _json_safe(next_steps),
        "protected_constraints": _json_safe(constraints),
        "source_refs": _json_safe(_value(raw, "source_refs", default=[])),
        "output_evidence_read": bool(include_output_evidence),
    }
    text_sections = []
    for name in ("state", "evidence", "decisions", "questions", "next_steps", "protected_constraints"):
        text = _section_text(name, result.get(name))
        if text:
            text_sections.append("## %s\n%s" % (name, text))
    text = "\n\n".join(text_sections)
    clipped, was_clipped = _clip_non_safety(text, int(budget_chars))
    # Preserve protected constraints if ordinary text clipping would remove
    # them.  The handoff is concise, but safety has the same guarantee as the
    # context projection.
    protected_text = _section_text("protected_constraints", result["protected_constraints"])
    if was_clipped and protected_text and protected_text not in clipped:
        clipped = clipped + "\n\n## protected_constraints\n" + protected_text
        result["budget_warning"] = "HANDOFF_OVER_BUDGET: protected safety content is never truncated"
    result["text"] = clipped
    result["measurements"] = {
        "limit_chars": int(budget_chars),
        "chars": len(clipped),
        "bytes": len(clipped.encode("utf-8")),
        "over_budget": len(clipped) > int(budget_chars),
    }
    return result


make_handoff = build_handoff
handoff_summary = build_handoff


def benchmark_context(card: Any, knowledge: Any = None, *, legacy_chars: Optional[int] = None) -> dict:
    """Return a reproducible reduction report for representative cards."""

    context = build_context(card, knowledge=knowledge)
    current_chars = context["measurements"]["chars"]
    if legacy_chars is None:
        # The benchmark fixture represents the mandatory legacy reading list.
        legacy_chars = sum(
            len(_text(_value(card, key, default="")))
            for key in (
                "mandatory_skill",
                "protocol",
                "environment",
                "northstar",
                "current",
                "job_shapes",
            )
        )
        if not legacy_chars:
            legacy_chars = 1
    reduction = 1.0 - (current_chars / float(legacy_chars))
    retained = {
        "goal": bool(context.get("goal")),
        "current_state": bool(context.get("current_state")),
        "acceptance": bool(context.get("acceptance")),
        "protected_constraints": bool(context.get("protected_constraints")),
        "dependencies": bool(context.get("dependencies")),
    }
    return {
        "card_id": context.get("card_id"),
        "legacy_chars": legacy_chars,
        "context_chars": current_chars,
        "reduction": reduction,
        "reduction_pct": round(reduction * 100.0, 2),
        "target_met": reduction >= 0.60 and all(retained.values()),
        "retained": retained,
        "required_constraints_retained": all(retained.values()),
    }


def _engine_card_projection(card: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict:
    """Map the core's flat card + stable sections to adapter sections."""

    metadata = card.get("metadata") if isinstance(card.get("metadata"), Mapping) else {}
    sections = card.get("sections") if isinstance(card.get("sections"), Mapping) else {}
    result = dict(metadata)
    # Core cards intentionally expose common metadata at the top level.  Use
    # that shape first, then stable section names, so migration cards remain
    # lossless without scanning their archived body.
    result.update({key: value for key, value in card.items() if key not in {"metadata", "sections", "body", "text"}})
    result["card_id"] = card.get("id") or result.get("id")
    result["revision"] = card.get("revision", result.get("revision"))
    result["goal"] = sections.get("goal", result.get("goal", ""))
    state_value = sections.get(
        "current_state",
        sections.get("progress", result.get("current_state", result.get("status", ""))),
    )
    result["current_state"] = _state_summary(state_value)
    # ``progress`` is a state source, not an open-question list.  Falling back
    # to it here duplicated long historical narratives in every cold-start
    # context.  Questions remain explicit and therefore actionable.
    result["questions"] = sections.get(
        "questions",
        result.get("questions", result.get("open_questions", result.get("blocking_questions", []))),
    )
    # Migration folds the legacy "目标范围验收" section into `goal` and the
    # legacy acceptance/output section into `artifacts`.  Keep a complete
    # acceptance projection even when a card has no separate v4 heading.
    result["acceptance"] = sections.get(
        "acceptance",
        result.get("acceptance", sections.get("artifacts", sections.get("goal", ""))),
    )
    result["dependencies"] = sections.get("dependencies", result.get("dependencies", ""))
    result["downstream_impact"] = sections.get("downstream", sections.get("impact", result.get("downstream_impact", "")))
    result["artifacts"] = sections.get("artifacts", result.get("artifacts", ""))
    result["decisions"] = sections.get("decisions", result.get("decisions", ""))
    result["next_steps"] = sections.get("next_steps", sections.get("closure", result.get("next_steps", "")))
    result["evidence"] = sections.get("evidence", result.get("evidence", []))
    result["knowledge"] = sections.get("knowledge", result.get("knowledge", []))
    result["protected_constraints"] = sections.get(
        "protected_constraints",
        sections.get("protected_invariants", sections.get("invariants", result.get("protected_constraints", []))),
    )
    if not result["protected_constraints"]:
        instance = snapshot.get("instance") if isinstance(snapshot.get("instance"), Mapping) else {}
        result["protected_constraints"] = instance.get("protected_constraints", instance.get("global_invariants", []))
    result["northstar"] = sections.get("northstar", sections.get("north_star", result.get("northstar", "")))
    if not result["northstar"]:
        instance = snapshot.get("instance") if isinstance(snapshot.get("instance"), Mapping) else {}
        result["northstar"] = instance.get("northstar", instance.get("north_star", ""))
    depends_on = result.get("depends_on") or result.get("dependencies_ids") or []
    if isinstance(depends_on, str):
        depends_on = [part.strip() for part in re.split(r"[,\s]+", depends_on) if part.strip()]
    result["depends_on"] = depends_on
    # Direct dependency cards are reduced to accepted output summaries,
    # validity, and versions.  No output file is opened here.
    cards_by_id = snapshot.get("cards_by_id") if isinstance(snapshot.get("cards_by_id"), Mapping) else {}
    cards_by_shape = {
        str(item.get("shape")): item
        for item in (snapshot.get("cards") or [])
        if isinstance(item, Mapping) and item.get("shape") is not None
    }
    direct = []
    for dep_id in depends_on:
        dep = cards_by_id.get(str(dep_id)) or cards_by_shape.get(str(dep_id))
        if not isinstance(dep, Mapping):
            direct.append({"id": dep_id, "status": "missing", "validity": "unknown", "version": None})
            continue
        dep_meta = dep.get("metadata") if isinstance(dep.get("metadata"), Mapping) else {}
        dep_sections = dep.get("sections") if isinstance(dep.get("sections"), Mapping) else {}
        artifact_value = dep.get(
            "accepted_outputs",
            dep.get("outputs", dep.get("artifacts", dep_sections.get("artifacts", ""))),
        )
        if isinstance(artifact_value, (Mapping, list, tuple)):
            accepted_outputs = _project_outputs(
                artifact_value,
                include_output_evidence=False,
            )
        else:
            artifact_text = _text(artifact_value)
            accepted_outputs = []
            if artifact_text:
                # A long section is historical prose, not an accepted-output
                # payload.  Keep a short artifact label or a pointer only.
                if len(artifact_text) <= 240:
                    accepted_outputs.append({"artifact": artifact_text})
                else:
                    # The dependency card path/hash below is the evidence
                    # locator.  Do not turn an entire prose section into one
                    # giant pseudo-path just because it contains a slash.
                    accepted_outputs.append({})
            if not accepted_outputs:
                accepted_outputs.append({})
        dep_source_refs = [{"kind": "card", "path": dep.get("path")}] if dep.get("path") else []
        output_version = dep.get(
            "validated_artifact_version",
            dep_meta.get("validated_artifact_version", dep.get("artifact_version", dep_meta.get("artifact_version"))),
        )
        output_validity = dep.get(
            "artifact_valid",
            dep_meta.get("artifact_valid", dep.get("pending_verification", dep_meta.get("pending_verification"))),
        )
        for output in accepted_outputs:
            if output_version is not None:
                output.setdefault("version", _json_safe(output_version))
            if output_validity is not None:
                output.setdefault("validity", _json_safe(output_validity))
            if dep_source_refs:
                output.setdefault("source_refs", dep_source_refs)
        direct.append(
            {
                "id": dep.get("id", dep_id),
                "status": dep.get("status", dep_meta.get("status")),
                "accepted": dep.get("status", dep_meta.get("status")) == "accepted",
                "accepted_outputs": accepted_outputs,
                "version": output_version,
                "validity": output_validity,
                "source_refs": dep_source_refs,
            }
        )
    result["direct_dependencies"] = direct
    result["source_refs"] = [{"kind": "card", "path": card.get("path")}] if card.get("path") else []
    # Card evidence is an entry point, not output contents.  We only expose
    # path/hash values already present in the snapshot/card record.
    for key in ("evidence_refs", "source_refs", "links"):
        raw = card.get(key) if key in card else metadata.get(key)
        if raw:
            result["source_refs"].extend(_pointer_refs(raw, default_kind="evidence"))
    result["source_refs"].extend(_pointer_refs(sections.get("evidence"), default_kind="evidence"))
    return result


def context(
    instance: Any,
    snapshot: Optional[Mapping[str, Any]] = None,
    *,
    card_id: Optional[str] = None,
    knowledge: Any = None,
    max_chars: int = CONTEXT_BUDGET_CHARS,
    include_output_evidence: bool = False,
    include_evidence: bool = False,
    since_revision: Any = None,
    previous: Any = None,
    include_text: bool = True,
    include_history: bool = False,
    **_: Any,
) -> dict:
    """Engine hook: ``context(instance, snapshot)``.

    ``snapshot`` is expected to be the result of ``sjs_v4.load_snapshot``. If
    omitted, the core is imported lazily and read once.  The adapter never
    writes a timing field; callers persist returned records through the core.
    """

    if snapshot is None:
        try:
            import sjs_v4

            snapshot = sjs_v4.load_snapshot(instance, max_chars=max_chars, include_text=False)
        except (ImportError, AttributeError):
            snapshot = {"cards": [], "cards_by_id": {}, "instance": {}}
    cards = list(snapshot.get("cards") or [])
    def matches(card: Mapping[str, Any], wanted: Any) -> bool:
        token = str(wanted)
        return any(
            str(card.get(key)) == token
            for key in ("id", "shape", "shape_id", "job_id")
            if card.get(key) is not None
        )
    selected = None
    if card_id is not None:
        selected = next((card for card in cards if matches(card, card_id)), None)
        if selected is None:
            raise ValueError("card not found: %s" % card_id)
    if selected is None:
        selected = next((card for card in cards if str(card.get("status")) in {"doing", "awaiting_acceptance"}), None)
    if selected is None:
        # A cold idle context, including a newly created empty instance, is a
        # menu request, not permission to select the first historical card.
        # The caller can name a card explicitly once one exists.
        menu = compact_menu(cards, include_history=include_history)
        return {
            "schema": CONTEXT_SCHEMA,
            "snapshot_schema": SNAPSHOT_SCHEMA,
            "card_id": None,
            "revision": snapshot.get("revision"),
            "goal": "",
            "current_state": "idle",
            "questions": [],
            "acceptance": [],
            "dependencies": [],
            "downstream_impact": "",
            "northstar": "",
            "protected_constraints": [],
            "knowledge": [],
            "knowledge_query": {},
            "source_refs": [],
            "warnings": ["MENU_ONLY: no active card; name a card before execution"],
            "measurements": {"chars": 0, "bytes": 0, "source_chars": 0, "source_bytes": 0, "limit_chars": max_chars, "over_budget": False},
            "text": "",
            "menu": menu,
            "snapshot_revision": snapshot.get("revision"),
            "historical_scan": bool(include_history),
            "output_evidence_read": False,
        }
    projected = _engine_card_projection(selected, snapshot)
    stable = _read_stable_sources(instance, snapshot)
    if stable.get("northstar"):
        existing_northstar = _text(projected.get("northstar"))
        if len(existing_northstar) > 1200:
            existing_northstar = _northstar_summary(existing_northstar, max_chars=1200)
        stable_northstar = _text(stable["northstar"])
        projected["northstar"] = stable_northstar if not existing_northstar else stable_northstar + "\n\n" + existing_northstar
    protected = list(_as_list(projected.get("protected_constraints")))
    protected.extend(stable.get("protected_constraints") or [])
    # Deduplicate by a canonical marker while preserving the original value.
    # The renderer and downstream callers need the structured constraint (for
    # example a mapping with an evidence pointer), not its JSON key.
    unique_protected = []
    seen_protected = set()
    for item in protected:
        stable_id = _value(item, "stable_id", "stableId", default=None)
        marker = "stable_id:" + str(stable_id) if stable_id else _canonical(item)
        if marker in seen_protected:
            continue
        seen_protected.add(marker)
        unique_protected.append(item)
    projected["protected_constraints"] = unique_protected
    projected["source_refs"] = list(projected.get("source_refs") or []) + list(stable.get("source_refs") or [])
    effective_knowledge = knowledge if knowledge is not None else stable.get("knowledge")
    payload = build_context(
        projected,
        knowledge=effective_knowledge,
        budget_chars=max_chars,
        include_output_evidence=include_output_evidence,
        include_evidence=include_evidence,
        since_revision=since_revision,
        previous=previous,
    )
    payload["instance"] = {
        key: snapshot.get("instance", {}).get(key)
        for key in ("instance_id", "domain_name", "route_key", "protocol_version")
        if isinstance(snapshot.get("instance"), Mapping) and snapshot.get("instance", {}).get(key) is not None
    }
    payload["card"] = {
        "id": selected.get("id"),
        "path": selected.get("path"),
        "shape": selected.get("shape"),
        "status": selected.get("status"),
        "revision": selected.get("revision", 0),
    }
    payload["menu"] = compact_menu(cards, current_card_id=selected.get("id"), include_history=include_history)
    payload["snapshot_revision"] = snapshot.get("revision")
    payload["stable_sources"] = stable.get("sources") or []
    payload["supplementary_reads"] = list(dict.fromkeys(
        (list(snapshot.get("instance", {}).get("required_reads") or []) if isinstance(snapshot.get("instance"), Mapping) else [])
        + list(stable.get("supplementary_reads") or [])
    ))
    payload["warnings"] = list(dict.fromkeys(payload.get("warnings", []) + list(stable.get("warnings") or [])))
    if payload["supplementary_reads"]:
        payload["warnings"] = list(dict.fromkeys(payload.get("warnings", []) + [
            "SUPPLEMENTARY_READ_REQUIRED: stable ENV/NORTH-STAR/protocol sources remain pointers; read only when needed"
        ]))
    if not include_text:
        payload.pop("text", None)
    return payload


def handoff_context(
    instance: Any,
    snapshot: Optional[Mapping[str, Any]],
    card_id: str,
    *,
    target_session: Optional[str] = None,
    max_chars: int = DEFAULT_HANDOFF_BUDGET_CHARS,
    include_output_evidence: bool = False,
    **_: Any,
) -> dict:
    """Engine hook for continuing the same card in a new session."""

    payload = context(
        instance,
        snapshot,
        card_id=card_id,
        max_chars=max_chars,
        include_output_evidence=include_output_evidence,
        include_text=False,
    )
    card = next(
        (
            item
            for item in (snapshot or {}).get("cards", [])
            if any(str(item.get(key)) == str(card_id) for key in ("id", "shape", "shape_id", "job_id") if item.get(key) is not None)
        ),
        None,
    )
    if card is None:
        raise ValueError("card not found: %s" % card_id)
    handoff = build_handoff(card, payload, target_session=target_session, budget_chars=max_chars, include_output_evidence=include_output_evidence)
    # Keep the handoff contract concise and explicit; do not emit a new card.
    return handoff


__all__ = [
    "CONTEXT_SCHEMA",
    "SNAPSHOT_SCHEMA",
    "HANDOFF_SCHEMA",
    "MENU_SCHEMA",
    "CONTEXT_BUDGET_CHARS",
    "DEFAULT_HANDOFF_BUDGET_CHARS",
    "ContextCache",
    "context_fingerprint",
    "compact_menu",
    "build_menu",
    "menu_from_cards",
    "build_context",
    "build_context_snapshot",
    "context_change_summary",
    "incremental_context",
    "same_session_changes",
    "build_handoff",
    "make_handoff",
    "handoff_summary",
    "benchmark_context",
    "context",
    "handoff_context",
]
