"""Fail-closed provenance for disposable Unity operation shadows (stdlib only).

The immutable reference is never opened by this module as a Unity project.
Permitted data changes are *recorded*, not declared semantically equivalent.
Only an independent review can approve the actual invocation and complete write
set. This module checks structure/integrity; the runner verifies model identity.
Snapshots cover Assets/Packages/ProjectSettings, not arbitrary OS writes or
transient edits reverted between snapshots. Invocation/source review is required
in addition to this structural validator.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile

SCOPES = ("Assets", "Packages", "ProjectSettings")
ADAPTER = "Assets/__B1Audit"
KINDS = {"startup", "editor_startup", "normal_run", "addressables_build", "normal_stop_play", "editor_exit"}
CODE_EXTENSIONS = {".cs", ".csx", ".shader", ".hlsl", ".cginc", ".compute", ".asmdef", ".asmref", ".dll", ".exe", ".py", ".pyc", ".pyd", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".ps1", ".psm1", ".bat", ".cmd", ".sh", ".c", ".cpp", ".h", ".hpp", ".vb", ".boo", ".rsp", ".so", ".dylib", ".wasm", ".jar"}
DEPENDENCIES = {"Packages/manifest.json", "Packages/packages-lock.json"}
HEX = re.compile(r"^[0-9a-f]{64}$")
OP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")


class ProvenanceError(ValueError):
    """Measurement is blocked; never interpret this as a business failure/zero."""


def _fail(message):
    raise ProvenanceError(message)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf8")


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _utc():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _safe(path):
    path = Path(os.path.abspath(path))
    for current in [path, *path.parents]:
        try:
            s = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(s.st_mode) or getattr(s, "st_file_attributes", 0) & 0x400:
            _fail("Linked, junction or reparse path prohibited: " + str(current))
    # Check before resolving, so normalization cannot conceal a reparse; then
    # normalize Windows short/long names for stable identity comparisons.
    return path.resolve(strict=False)


def _relative(value):
    if not isinstance(value, str) or "\\" in value or ":" in value or "\x00" in value:
        _fail("Unsafe relative path")
    p = PurePosixPath(value)
    if not value or p.is_absolute() or any(x in ("", ".", "..") for x in value.split("/")):
        _fail("Unsafe relative path: " + value)
    return p.as_posix()


def _under(root, relative):
    relative = _relative(relative)
    root = _safe(root)
    p = _safe(root / relative)
    if not p.is_relative_to(root):
        _fail("Path outside expected root")
    return p


def _file_hash(path):
    p = _safe(path)
    if not p.is_file():
        _fail("Missing regular file: " + str(p))
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _read(path):
    try:
        result = json.loads(_safe(path).read_text(encoding="utf-8-sig"))
        if not isinstance(result, dict):
            _fail("JSON object required: " + str(path))
        return result
    except (OSError, ValueError) as exc:
        _fail("Missing/invalid JSON: " + str(path) + ": " + str(exc))


def _write(path, value):
    path = _safe(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _safe(path.parent)
    # Atomic replacement leaves a prior chain valid if the process is interrupted.
    fd, name = tempfile.mkstemp(prefix=".provenance-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(json.dumps(value, ensure_ascii=False, indent=2).encode("utf8") + b"\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def inventory(root):
    root = _safe(root)
    result = {}
    aliases = set()
    for scope in SCOPES:
        target = _safe(root / scope)
        if not target.is_dir():
            _fail("Missing Unity scope: " + scope)
        for base, dirs, files in os.walk(target, followlinks=False):
            for name in dirs + files:
                _safe(Path(base) / name)
            for name in files:
                p = Path(base) / name
                if not p.is_file():
                    _fail("Nonregular input file")
                rel = p.relative_to(root).as_posix()
                # Windows case-insensitivity makes ambiguous path aliases unsafe.
                if rel.casefold() in aliases:
                    _fail("Case-aliased scope path")
                aliases.add(rel.casefold())
                result[rel] = _file_hash(p)
    return dict(sorted(result.items()))


def fingerprint(entries):
    return _hash(entries)


def _guid(path):
    text = _safe(path).read_bytes().decode("utf-8-sig", errors="replace")
    matches = re.findall(r"^guid:\s*([0-9a-fA-F]{32})\s*$", text, re.MULTILINE)
    if len(re.findall(r"^guid:", text, re.MULTILINE)) > 1:
        _fail("Ambiguous original meta GUID")
    return matches[0].lower() if matches else None


def _adapter(path):
    return path.startswith(ADAPTER + "/") or path == ADAPTER + ".meta"


def _roots(reference, runtime, evidence_root):
    roots = [_safe(x) for x in (reference, runtime, evidence_root)]
    for i, a in enumerate(roots):
        for b in roots[i + 1:]:
            if a == b or a.is_relative_to(b) or b.is_relative_to(a):
                _fail("Reference, runtime and evidence roots must be disjoint")
    return roots


def initialize(reference, runtime, evidence_root, candidate_hash, *, primary_id="primary"):
    """Freeze U/R identity; create R only if absent, never repair a dirty R."""
    reference, runtime, evidence_root = _roots(reference, runtime, evidence_root)
    if not HEX.fullmatch(str(candidate_hash)) or not isinstance(primary_id, str) or not primary_id.strip():
        _fail("candidate_hash must be SHA256 and primary_id must be nonempty")
    initial = inventory(reference)
    if any(_adapter(p) for p in initial):
        _fail("Immutable reference must not contain audit adapters")
    store = evidence_root / "provenance"
    if store.exists():
        _fail("Provenance already initialized; never reset its chain")
    if not runtime.exists():
        # Scan the whole reference before copying to reject out-of-scope links too.
        for base, dirs, files in os.walk(reference, followlinks=False):
            for name in dirs + files:
                _safe(Path(base) / name)
        shutil.copytree(reference, runtime)
    current = inventory(runtime)
    if current != initial:
        _fail("Runtime initial input differs from immutable reference; no repair allowed")
    manifest = {"schema": "b1-operation-provenance-v1", "created_utc": _utc(),
                "reference": str(reference), "runtime": str(runtime), "evidence_root": str(evidence_root),
                "candidate_hash": candidate_hash, "primary_id": primary_id,
                "reference_inventory": initial, "runtime_initial_inventory": current,
                "identity_mapping": {p: p for p in initial}, "reference_digest": fingerprint(initial),
                "protected_code": {p: h for p, h in initial.items() if Path(p).suffix.lower() in CODE_EXTENSIONS or p in DEPENDENCIES},
                "original_meta": {p: {"sha256": h, "guid": _guid(reference / p)} for p, h in initial.items() if p.lower().endswith(".meta")}}
    manifest["manifest_sha256"] = _hash(manifest)
    _write(store / "manifest.json", manifest)
    (store / "operations").mkdir()
    (store / "blobs").mkdir()
    _write(store / "journal-head.json", {"operation_ids": [], "head": manifest["manifest_sha256"], "active": None})
    return manifest


def _manifest(reference, runtime, evidence_root, manifest):
    reference, runtime, evidence_root = _roots(reference, runtime, evidence_root)
    provided = _read(manifest) if isinstance(manifest, (str, Path)) else manifest
    stored = _read(evidence_root / "provenance/manifest.json")
    if provided != stored:
        _fail("Provided provenance manifest differs from frozen manifest")
    expected_hash = stored.get("manifest_sha256")
    body = {k: v for k, v in stored.items() if k != "manifest_sha256"}
    if stored.get("schema") != "b1-operation-provenance-v1" or _hash(body) != expected_hash:
        _fail("Manifest hash/schema mismatch")
    for key, path in (("reference", reference), ("runtime", runtime), ("evidence_root", evidence_root)):
        if stored.get(key) != str(path):
            _fail("Manifest root mismatch: " + key)
    initial = inventory(reference)
    if initial != stored.get("reference_inventory") or initial != stored.get("runtime_initial_inventory") or fingerprint(initial) != stored.get("reference_digest"):
        _fail("Immutable reference input changed")
    if stored.get("identity_mapping") != {p: p for p in initial}:
        _fail("Initial identity mapping mismatch")
    return reference, runtime, evidence_root, stored


def _refs(evidence_root, refs):
    if not isinstance(refs, list) or not refs:
        _fail("Actual invocation evidence_refs required")
    for item in refs:
        if not isinstance(item, dict):
            _fail("Invalid evidence reference")
        rel = _relative(item.get("path"))
        if rel.startswith("provenance/") or rel == "provenance-review.json":
            _fail("Invocation/review evidence cannot cite its own provenance assertions")
        if not HEX.fullmatch(str(item.get("sha256"))) or _file_hash(_under(evidence_root, rel)) != item["sha256"]:
            _fail("Evidence reference hash mismatch: " + rel)


def _provenance(value, manifest, evidence_root, *, complete):
    if not isinstance(value, dict) or not isinstance(value.get("actor_id"), str) or not value["actor_id"].strip():
        _fail("Provenance actor_id required")
    call = value.get("invocation", {})
    if not isinstance(call, dict) or call.get("kind") not in {"tool_call", "adapter", "controller"} or not isinstance(call.get("command"), str) or not call["command"].strip():
        _fail("Exact original invocation kind/command required")
    if not isinstance(call.get("refs"), list) or not call["refs"] or any(not isinstance(x, str) or not x.strip() for x in call["refs"]):
        _fail("Original tool-call/adapter refs required")
    sources = value.get("source_refs")
    if not isinstance(sources, list) or not sources:
        _fail("Original code/package source_refs required")
    for src in sources:
        if not isinstance(src, dict):
            _fail("Invalid source reference")
        path = _relative(src.get("path"))
        source_kind = Path(path).suffix.lower() in CODE_EXTENSIONS or (path.startswith("Packages/") and Path(path).name in {"package.json", "manifest.json", "packages-lock.json"})
        if manifest["reference_inventory"].get(path) != src.get("sha256") or not source_kind:
            _fail("Original code/package source hash mismatch: " + path)
    if complete:
        _refs(evidence_root, value.get("evidence_refs"))
    elif value.get("evidence_refs"):
        _refs(evidence_root, value["evidence_refs"])
    return value


def _blob(evidence_root, payload):
    h = hashlib.sha256(payload).hexdigest()
    p = _under(evidence_root, "provenance/blobs/" + h)
    if p.exists():
        if _file_hash(p) != h:
            _fail("Content-addressed blob was tampered")
    else:
        p.write_bytes(payload)
    return h


def _changes(before, after):
    return [{"path": p, "before_sha256": before.get(p), "after_sha256": after.get(p)}
            for p in sorted(set(before) | set(after)) if before.get(p) != after.get(p)]


def _protections(manifest, before, after, content):
    errors = []
    for p in DEPENDENCIES:
        if after.get(p) != manifest["reference_inventory"].get(p):
            errors.append("Package dependency manifest/lock changed: " + p)
    for p, h in manifest["protected_code"].items():
        if after.get(p) != h:
            errors.append("Original code/dependency changed: " + p)
    for p in after:
        if p not in manifest["reference_inventory"] and Path(p).suffix.lower() in CODE_EXTENSIONS and not _adapter(p):
            errors.append("New code outside audit adapter: " + p)
    for p, meta in manifest["original_meta"].items():
        if p not in after:
            errors.append("Original meta deleted: " + p)
        elif after[p] != meta["sha256"]:
            payload = content(p, after[p])
            guids = re.findall(rb"^guid:\s*([0-9a-fA-F]{32})\s*$", payload, re.MULTILINE)
            if meta["guid"] is None or len(re.findall(rb"^guid:", payload, re.MULTILINE)) != 1 or len(guids) != 1 or guids[0].decode().lower() != meta["guid"]:
                errors.append("Original meta GUID changed: " + p)
    return errors


def _chain(reference, runtime, evidence_root, manifest):
    directory = _safe(evidence_root / "provenance/operations")
    if not directory.is_dir():
        _fail("Missing operation journal directory")
    records = []
    for p in directory.iterdir():
        _safe(p)
        if not p.is_file() or p.suffix != ".json":
            _fail("Unknown journal entry")
        op = _read(p)
        if p.name != str(op.get("operation_id")) + ".json":
            _fail("Journal filename mismatch")
        records.append(op)
    records.sort(key=lambda x: x.get("sequence", -1))
    before = manifest["runtime_initial_inventory"]
    head = manifest["manifest_sha256"]
    ids = set()
    def content(path, h):
        blob = _under(evidence_root, "provenance/blobs/" + h)
        if not blob.exists() or _file_hash(blob) != h:
            _fail("Missing/tampered changed-file full bytes: " + path)
        return blob.read_bytes()
    for seq, op in enumerate(records, 1):
        if op.get("operation_id") in ids or op.get("sequence") != seq or op.get("previous_operation_sha256") != head:
            _fail("Missing/discontinuous operation hash chain")
        ids.add(op["operation_id"])
        if not OP_ID.fullmatch(op["operation_id"]) or op.get("kind") not in KINDS:
            _fail("Invalid journal operation identity/kind")
        body = {k: v for k, v in op.items() if k != "operation_sha256"}
        if _hash(body) != op.get("operation_sha256"):
            _fail("Operation hash mismatch")
        if op.get("input_inventory") != before or op.get("input_digest") != fingerprint(before):
            _fail("Operation input continuity mismatch")
        after = op.get("output_inventory")
        if not isinstance(after, dict) or op.get("output_digest") != fingerprint(after):
            _fail("Operation output hash mismatch")
        for path, h in after.items():
            if not isinstance(path, str) or not _relative(path).split("/")[0] in SCOPES or not HEX.fullmatch(str(h)):
                _fail("Invalid journal output inventory")
        changes = _changes(before, after)
        if op.get("changes") != changes:
            _fail("Actual operation write set mismatch")
        for item in changes:
            for key in ("before_sha256", "after_sha256"):
                if item[key] is not None:
                    content(item["path"], item[key])
        errors = _protections(manifest, before, after, content)
        _provenance(op.get("provenance"), manifest, evidence_root, complete=True)
        if op.get("violations") or errors:
            _fail("Blocked operation: " + "; ".join(op.get("violations", []) + errors))
        before, head = after, op["operation_sha256"]
    index = _read(evidence_root / "provenance/journal-head.json")
    if index.get("operation_ids") != [op["operation_id"] for op in records] or index.get("head") != head:
        _fail("Journal head index mismatch or missing operation")
    active_path = _safe(evidence_root / "provenance/active-operation.json")
    if index.get("active") is not None:
        if not active_path.exists() or _read(active_path).get("receipt_sha256") != index["active"]:
            _fail("Missing/tampered active operation receipt")
    elif active_path.exists():
        _fail("Unindexed active operation receipt")
    return records, before, head


def begin_operation(reference, runtime, evidence_root, manifest, kind, operation_id, provenance):
    reference, runtime, evidence_root, manifest = _manifest(reference, runtime, evidence_root, manifest)
    if kind not in KINDS or not isinstance(operation_id, str) or not OP_ID.fullmatch(operation_id) or ".." in operation_id:
        _fail("Invalid operation kind/id")
    active = evidence_root / "provenance/active-operation.json"
    if active.exists():
        _fail("Unfinished operation already exists")
    records, expected, head = _chain(reference, runtime, evidence_root, manifest)
    if any(op["operation_id"] == operation_id for op in records):
        _fail("Operation ID already used")
    actual = inventory(runtime)
    if actual != expected:
        _fail("Unjournaled runtime changes before operation")
    provenance = _provenance(provenance, manifest, evidence_root, complete=False)
    receipt = {"schema": "b1-operation-begin-v1", "operation_id": operation_id, "kind": kind,
               "sequence": len(records) + 1, "started_utc": _utc(), "previous_operation_sha256": head,
               "input_inventory": actual, "input_digest": fingerprint(actual), "provenance": provenance}
    receipt["receipt_sha256"] = _hash(receipt)
    _write(active, receipt)
    _write(evidence_root / "provenance/journal-head.json", {"operation_ids": [op["operation_id"] for op in records], "head": head, "active": receipt["receipt_sha256"]})
    return receipt


def finish_operation(reference, runtime, evidence_root, manifest, operation_id, provenance=None):
    reference, runtime, evidence_root, manifest = _manifest(reference, runtime, evidence_root, manifest)
    active = _under(evidence_root, "provenance/active-operation.json")
    receipt = _read(active)
    if receipt.get("operation_id") != operation_id:
        _fail("Wrong/missing active operation")
    if _hash({k: v for k, v in receipt.items() if k != "receipt_sha256"}) != receipt.get("receipt_sha256"):
        _fail("Active operation receipt tampered")
    records, before, head = _chain(reference, runtime, evidence_root, manifest)
    if receipt.get("previous_operation_sha256") != head or receipt.get("input_inventory") != before or receipt.get("sequence") != len(records) + 1:
        _fail("Active receipt chain mismatch")
    after = inventory(runtime)
    changes = _changes(before, after)
    for item in changes:
        p, old, new = item["path"], item["before_sha256"], item["after_sha256"]
        if old is not None:
            if manifest["reference_inventory"].get(p) == old:
                _blob(evidence_root, _under(reference, p).read_bytes())
            else:
                # Every non-initial byte version must already have been stored.
                blob = _under(evidence_root, "provenance/blobs/" + old)
                if not blob.exists() or _file_hash(blob) != old:
                    _fail("Missing prior complete bytes: " + p)
        if new is not None:
            payload = _under(runtime, p).read_bytes()
            if _blob(evidence_root, payload) != new:
                _fail("Runtime changed concurrently while finishing operation")
    errors = _protections(manifest, before, after, lambda p, h: _under(runtime, p).read_bytes())
    final_provenance = provenance if provenance is not None else receipt["provenance"]
    if not isinstance(final_provenance, dict):
        errors.append("Invalid finish provenance object")
        final_provenance = {}
    # The identity, source and intended invocation cannot silently be replaced
    # at finish; append actual execution refs only.
    for key in ("actor_id", "invocation", "source_refs"):
        if final_provenance.get(key) != receipt["provenance"].get(key):
            errors.append("Invocation/source/actor changed between begin and finish")
    try:
        _provenance(final_provenance, manifest, evidence_root, complete=True)
    except ProvenanceError as exc:
        errors.append(str(exc))
    op = {k: v for k, v in receipt.items() if k not in ("schema", "receipt_sha256", "provenance")}
    op.update(schema="b1-operation-record-v1", finished_utc=_utc(), provenance=final_provenance,
              output_inventory=after, output_digest=fingerprint(after), changes=changes, violations=errors,
              begin_receipt_sha256=receipt["receipt_sha256"])
    op["operation_sha256"] = _hash(op)
    target = _under(evidence_root, "provenance/operations/" + operation_id + ".json")
    if target.exists():
        _fail("Operation overwrite prohibited")
    _write(target, op)
    active.unlink()
    _write(evidence_root / "provenance/journal-head.json", {"operation_ids": [x["operation_id"] for x in records] + [operation_id], "head": op["operation_sha256"], "active": None})
    if errors:
        _fail("Blocked operation recorded: " + "; ".join(errors))
    return op


def _review(evidence_root, manifest, records, head, final_inventory):
    review = _read(evidence_root / "provenance-review.json")
    if review.get("schema") != "b1-provenance-review-v1" or review.get("manifest_sha256") != manifest["manifest_sha256"] or review.get("candidate_hash") != manifest["candidate_hash"] or review.get("journal_head") != head:
        _fail("Review does not bind the final provenance chain")
    reviewer = review.get("reviewer_id")
    if not isinstance(reviewer, str) or not reviewer.strip() or reviewer == manifest["primary_id"] or review.get("primary_id") != manifest["primary_id"] or review.get("independent") is not True:
        _fail("Independent reviewer required; self-review prohibited")
    expected = {(op["operation_id"], c["path"]): op for op in records for c in op["changes"]}
    decisions = review.get("decisions")
    if not isinstance(decisions, list):
        _fail("Review decisions list required")
    seen = set()
    for d in decisions:
        if not isinstance(d, dict):
            _fail("Invalid review decision")
        key = (d.get("operation_id"), d.get("path"))
        if key not in expected or key in seen:
            _fail("Unknown/duplicate review operation/path")
        seen.add(key)
        if d.get("decision") != "approved" or not isinstance(d.get("basis"), str) or not d["basis"].strip():
            _fail("Write-set review approval/basis missing")
        if reviewer == expected[key]["provenance"]["actor_id"]:
            _fail("Write-set actor cannot self-review")
        _refs(evidence_root, d.get("evidence_refs"))
    if seen != set(expected):
        _fail("Review missing actual operation/path write-set coverage")
    final = review.get("final", {})
    if not isinstance(final, dict) or final.get("approved") is not True or final.get("runtime_digest") != fingerprint(final_inventory) or final.get("reference_digest") != manifest["reference_digest"] or not isinstance(final.get("basis"), str) or not final["basis"].strip():
        _fail("Final independent reference/runtime hash approval required")
    _refs(evidence_root, final.get("evidence_refs"))
    return reviewer


def validate_workspace(reference, runtime, evidence_root, manifest, require_review=False):
    reference, runtime, evidence_root, manifest = _manifest(reference, runtime, evidence_root, manifest)
    if (evidence_root / "provenance/active-operation.json").exists():
        _fail("Unfinished operation; measurement blocked")
    records, expected, head = _chain(reference, runtime, evidence_root, manifest)
    current = inventory(runtime)
    if current != expected:
        _fail("Current runtime differs from final journal output: unjournaled write")
    reviewer = _review(evidence_root, manifest, records, head, current) if require_review else None
    changes = [{"operation_id": op["operation_id"], **c} for op in records for c in op["changes"]]
    return {"valid": True, "blocked": False, "candidate_hash": manifest["candidate_hash"],
            "manifest_sha256": manifest["manifest_sha256"], "journal_head": head,
            "reference_digest": manifest["reference_digest"], "current_digest": fingerprint(current),
            "operations": [op["operation_id"] for op in records], "changes": changes,
            "review_required": True, "review_approved": bool(require_review), "pending_final_review": not require_review,
            "pending_review": [] if require_review else [op["operation_id"] for op in records if op["changes"]],
            "reviewer_id": reviewer, "primary_id": manifest["primary_id"],
            "operation_actor_ids": sorted({op["provenance"]["actor_id"] for op in records}),
            "semantics": "Recorded original-process derivation; no automatic semantic equivalence claim."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("init", "begin", "finish", "validate"):
        cmd = sub.add_parser(name)
        for field in ("reference", "runtime", "evidence-root"):
            cmd.add_argument("--" + field, required=True)
        if name == "init":
            cmd.add_argument("--candidate-hash", required=True)
            cmd.add_argument("--primary-id", default="primary")
        else:
            cmd.add_argument("--manifest", required=True)
        if name in ("begin", "finish"):
            cmd.add_argument("--operation-id", required=True)
            cmd.add_argument("--provenance-json", required=name == "begin")
        if name == "begin":
            cmd.add_argument("--kind", choices=sorted(KINDS), required=True)
        if name == "validate":
            cmd.add_argument("--require-review", action="store_true")
    args = parser.parse_args(argv)
    roots = (args.reference, args.runtime, args.evidence_root)
    try:
        if args.action == "init":
            result = initialize(*roots, args.candidate_hash, primary_id=args.primary_id)
        elif args.action == "begin":
            result = begin_operation(*roots, args.manifest, args.kind, args.operation_id, _read(args.provenance_json))
        elif args.action == "finish":
            result = finish_operation(*roots, args.manifest, args.operation_id, _read(args.provenance_json) if args.provenance_json else None)
        else:
            result = validate_workspace(*roots, args.manifest, require_review=args.require_review)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ProvenanceError, OSError) as exc:
        print(json.dumps({"valid": False, "blocked": True, "reason": str(exc), "business_score": None}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
