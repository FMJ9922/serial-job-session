"""B1 blind audit scaffolding and evidence-checked scoring, standard library only.

This tool never infers behavior from source or judges unfinished games. An actual
independent auditor fills audit.json using recorded runtime/test evidence.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import uuid

try:
    from . import spec
except ImportError:
    import spec

SEED = 20261116
BUSINESS_DIRS = ("Assets", "Packages", "ProjectSettings")
MANUAL_NAMES = {"README.md", "OPERATION.md", "DELIVERY.md", "INSTRUCTIONS.md", "操作说明.md"}
ADAPTER = "Assets/__B1Audit"


def sha256(path):
    return hashlib.sha256(check_links(path).read_bytes()).hexdigest()


def write_json(path, value):
    check_links(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path):
    return json.loads(check_links(path).read_text(encoding="utf-8"))


def check_links(path):
    path = Path(path).absolute()
    for current in [path, *path.parents]:
        try:
            details = current.lstat()
        except FileNotFoundError:
            continue
        # Python3.11 lacks Path.is_junction(). Windows junctions and other
        # reparses are rejected by the native stat bit before any resolve/read.
        if stat.S_ISLNK(details.st_mode) or getattr(details, "st_file_attributes", 0) & 0x400:
            raise ValueError("Linked, junction or reparse paths are prohibited")
    return path


def inventory(root, allow_adapters=False):
    root = check_links(root)
    entries = {}
    for directory in BUSINESS_DIRS:
        target = root / directory
        if not target.is_dir():
            raise ValueError(f"Missing Unity directory: {directory}")
        for base, dirs, files in os.walk(target, followlinks=False):
            for name in dirs + files:
                check_links(Path(base) / name)
            for name in files:
                path = Path(base) / name
                relative = path.relative_to(root).as_posix()
                if allow_adapters and (relative.startswith(ADAPTER + "/") or relative == ADAPTER + ".meta"):
                    continue
                entries[relative] = sha256(path)
    return dict(sorted(entries.items()))


def fingerprint(entries):
    return hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def provenance_module():
    try:
        from . import operation_provenance
    except ImportError:
        import operation_provenance
    return operation_provenance


def measurement_dispute_module():
    try:
        from . import measurement_dispute
    except ImportError:
        import measurement_dispute
    if check_links(measurement_dispute.__file__).resolve().parent != check_links(__file__).resolve().parent:
        raise ValueError('Measurement dispute validator must be colocated')
    return measurement_dispute


def fixtures():
    """Deterministic abstract inputs; no candidate-specific types are prescribed."""
    return {
        "seed": SEED,
        "profiles": [
            {"fixture_id": "profile-a", "name": "玩家甲"},
            {"fixture_id": "profile-b", "name": "Alice"},
            {"fixture_id": "profile-c", "name": "测试 Player"},
        ],
        "profile_invalid_names": ["", "   ", "Alice", "../save:<name>?*"],
        "scores": [120, 400, 400, 0, 90, 300, 150, 600, 250, 50, 200, 500],
        "languages": ["zh", "en"],
        "volume_values": [1.0, 0.5, 0.0],
        "restart_count": 1,
        "menu_roundtrips": 5,
        "sequence_compare_length": 3,
        "game_view": [1920, 1080],
        "board_cases": ["empty_spawn", "both_horizontal_boundaries", "floor", "occupied_cell", "rotation_collision", "single_full_plane", "incomplete_plane", "single_full_row_only", "two_full_planes_with_marker_above", "blocked_spawn"],
        "board_instructions": "Read actual candidate dimensions and original interfaces. Regenerate full planes/rows and legal markers for those dimensions, not a forced 4x4x8 board. Use original algorithms, not replacement implementations. Confirm all shapes and three-piece continuity. Invalid-name fixtures demand safe explicit handling, not a forced accept/reject policy.",
        "scope": "Fixtures implement published requirements only. Do not add new requirements or demand fixture IDs match candidate storage IDs.",
    }


def init_audit(snapshot, out, opaque_id=None, operational=False, primary_id="primary"):
    entry = check_links(snapshot).resolve()
    source = entry / "project" if (entry / "project/Assets").is_dir() else entry
    output = check_links(out).resolve()
    if output == entry or entry in output.parents or output in entry.parents:
        raise ValueError("Audit output and source snapshot must be disjoint")
    opaque = opaque_id or uuid.uuid4().hex
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", opaque):
        raise ValueError("Opaque ID must be anonymous letters/digits/dash/underscore")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Audit output must be empty")
    before = inventory(source)
    seal_path = source.parent / "seal.json"
    if seal_path.is_file():
        seal = read_json(seal_path)
        expected = {name: digest for name, digest in seal.get("source_files", {}).items() if name.split("/", 1)[0] in BUSINESS_DIRS}
        if expected != before:
            raise ValueError("Snapshot business files do not match runner seal")
    if any(name.startswith(ADAPTER + "/") or name == ADAPTER + ".meta" for name in before):
        raise ValueError("Candidate already uses reserved audit adapter directory")
    candidate_hash = fingerprint(before)
    blind = output / "blind"
    candidate = blind / "candidate"
    candidate.mkdir(parents=True)
    (blind / "evidence").mkdir()
    for directory in BUSINESS_DIRS:
        shutil.copytree(source / directory, candidate / directory)
    if inventory(candidate) != before:
        raise ValueError("Initial reference copy differs from sealed input; refusing a new baseline")
    manuals = []
    for name in sorted(MANUAL_NAMES):
        manual = source / name
        if not manual.is_file():
            continue
        check_links(manual)
        content = manual.read_text(encoding="utf-8-sig", errors="replace")
        content = re.sub(r"(?i)(?:gpt-)?(?:6\.1[ -]?(?:sol|terra)|5\.6[ -]?luna)|serial-job-session|[A-Z]:[\\/][^\s`\"<>]+", "[provenance redacted]", content)
        target = candidate / name
        target.write_text(content, encoding="utf-8")
        manuals.append(name)
    settings = candidate / "ProjectSettings/ProjectSettings.asset"
    original_settings = settings.read_bytes()
    if hashlib.sha256(original_settings).hexdigest() != before.get("ProjectSettings/ProjectSettings.asset"):
        raise ValueError("Copied ProjectSettings bytes differ from sealed input")
    replacement = f"  productName: B1Audit-{opaque}".encode("utf-8")
    modified, count = re.subn(rb"(?m)^  productName:[^\r\n]*", replacement, original_settings)
    if count != 1:
        raise ValueError("Cannot establish isolated Unity productName namespace")
    settings.write_bytes(modified)
    baseline = inventory(candidate)
    expected_baseline = {**before, "ProjectSettings/ProjectSettings.asset": hashlib.sha256(modified).hexdigest()}
    if baseline != expected_baseline:
        raise ValueError("Initialized reference differs beyond the authorized productName namespace change")
    runtime = blind / "runtime"
    operational_provenance = None
    if operational:
        shutil.copytree(candidate, runtime)
        if inventory(runtime) != baseline:
            raise ValueError("Operational runtime must be a byte-identical copy of immutable candidate")
        operational_provenance = provenance_module().initialize(candidate, runtime, blind / "evidence", candidate_hash, primary_id=primary_id)
        shutil.copy2(provenance_module().__file__, blind / "provenance_tool.py")
    write_json(blind / "fixtures.json", fixtures())
    prompt = spec.audit_prompt(str(runtime if operational else candidate), str(blind / "evidence"))
    if operational:
        prompt += (
            "\nWorkspace mode derived_operations_v1: candidate/ is an immutable reference U; never open it in Unity, write adapters there, or copy any outputs back. "
            "Only runtime/ R (the initially byte-identical disposable operational copy) may be opened in Unity. "
            "Use the provenance journal for every ordinary game run, original-config/original-builder standard build, stop Play, and normal Editor exit. "
            "Capture each operation's complete before/after write set, original/derived bytes and native-call/source evidence; obtain distinct independent approval of every data change. "
            "Missing journal, unexplained writes, original code/meta GUID/dependency changes, self-approval or insufficient provenance are measurement blockers, not game failures. "
            "Original non-GUID meta importer data may be naturally serialized only by original standard Unity operations; every such path still requires complete before/after bytes, actual invocation/source evidence and independent approval. An unchanged GUID never proves semantic equivalence. "
            "Do not restore changed files, update the initial expected hashes, presume material semantic equivalence, repair business assets/code, or force-kill a running game to preserve hashes. "
            "Normal Unity lifecycle/build side effects must be explicitly traced and reviewed; they are not automatically ignored. "
            "The measured subject is the sealed original input naturally derived through traceable standard Unity lifecycle operations. "
            "Journal acceptance supplies no functional points: every one of the100 requirements still needs its own actual behavioral evidence.\n"
        )
    prompt += "\nUse fixtures.json seed 20261116 for all candidates. Fill audit.json, keeping all100 IDs. The only initialization change is productName for save namespace isolation. Never count not_run/null as a fail or a pass; those mean incomplete audit.\n"
    if operational:
        prompt += "New adapters are permitted only in runtime/Assets/__B1Audit; never in candidate/. Use only the frozen local provenance_tool.py for measurement journal validation. Do not access parent/admin directories or external audit.py. The controller performs final business validation and scoring; you must not run the controller scoring commands.\n"
    else:
        prompt += "Dedicated new adapters only in Assets/__B1Audit. Legacy strict workspace: no other business file changes after initialization. Run audit.py validate/score after evidence capture.\n"
    (blind / "prompt.md").write_text(prompt, encoding="utf-8")
    manifest = {"spec_version": spec.SPEC_VERSION, "opaque_id": opaque, "candidate_hash": candidate_hash, "audit_baseline": baseline, "fixture_seed": SEED, "allowed_new_adapter_directory": ADAPTER, "namespace_change": {"file": "ProjectSettings/ProjectSettings.asset", "field": "productName", "value": f"B1Audit-{opaque}"}, "manuals": {name: sha256(candidate / name) for name in manuals}, "artifacts": {"fixtures.json": sha256(blind / "fixtures.json"), "prompt.md": sha256(blind / "prompt.md")}}
    if operational:
        manifest.update(workspace_mode="derived_operations_v1", runtime="runtime", runtime_initial_inventory=baseline.copy(), runtime_to_candidate={name: name for name in baseline}, operational_provenance=operational_provenance)
        manifest["artifacts"]["provenance_tool.py"] = sha256(blind / "provenance_tool.py")
    write_json(blind / "manifest.json", manifest)
    report = {"spec_version": spec.SPEC_VERSION, "opaque_id": opaque, "candidate_hash": candidate_hash, "auditor": {"id": None, "model": "gpt-6.1-sol", "effort": "high"}, "checks": [{"id": check["id"], "candidate_hash": candidate_hash, "status": "not_run", "score": None, "operation": "", "expected": check["title"], "actual": "", "evidence": [], "review_required": False, "review_reason": "", "review": None, "adjudication": None} for check in spec.CHECKS]}
    if operational and primary_id != "primary":
        report["auditor"]["id"] = primary_id
    write_json(blind / "audit.json", report)
    judge = {"spec_version": spec.SPEC_VERSION, "opaque_id": opaque, "snapshot": str(source), "candidate_hash": candidate_hash, "source_inventory": before, "runner_seal": {"path": str(seal_path), "sha256": sha256(seal_path)} if seal_path.is_file() else None, "blind_manifest_sha256": sha256(blind / "manifest.json"), "audit_report": str(blind / "audit.json"), "metrics": {"time_seconds": None, "tokens": None, "cost": None, "subagents": None, "compactions": None}}
    write_json(output / "judge_manifest.json", judge)
    return judge


def evidence_records(records, evidence_root):
    if not isinstance(records, list) or not records:
        raise ValueError("An evaluated decision requires actual evidence files")
    root = check_links(evidence_root).resolve()
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("path"), str) or not isinstance(record.get("sha256"), str):
            raise ValueError("Evidence requires path and sha256")
        path = Path(record["path"])
        path = check_links(path if path.is_absolute() else root / path).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Evidence must be an existing file contained in evidence directory")
        if sha256(path) != record["sha256"]:
            raise ValueError("Evidence sha256 mismatch")


def decision(value, auditor_id, evidence_root):
    if not isinstance(value, dict) or value.get("auditor_id") == auditor_id or not value.get("auditor_id"):
        raise ValueError("Review requires a distinct independent auditor")
    if type(value.get("score")) is not int or value["score"] not in (0, 1):
        raise ValueError("Review score must be integer 0 or 1")
    if not value.get("operation") or not value.get("expected") or not value.get("actual"):
        raise ValueError("Review requires operation/expected/actual")
    evidence_records(value.get("evidence"), evidence_root)
    return value["score"]


def technical_adjudication_module():
    try:
        from . import technical_adjudication
    except ImportError:
        import technical_adjudication
    if check_links(technical_adjudication.__file__).resolve() != Path(__file__).resolve().with_name('technical_adjudication.py'):
        raise ValueError('Technical adjudication requires its colocated resolver')
    return technical_adjudication


def validate_report(report_path):
    report_path = check_links(report_path).resolve()
    blind = report_path.parent
    report = read_json(report_path)
    manifest = read_json(blind / "manifest.json")
    judge = read_json(blind.parent / "judge_manifest.json")
    if sha256(blind / "manifest.json") != judge["blind_manifest_sha256"]:
        raise ValueError("Blind manifest changed after initialization")
    if inventory(judge["snapshot"]) != judge["source_inventory"]:
        raise ValueError("Original sealed snapshot business files changed")
    if judge.get("runner_seal") and sha256(judge["runner_seal"]["path"]) != judge["runner_seal"]["sha256"]:
        raise ValueError("Original runner seal changed")
    operational = manifest.get("workspace_mode") == "derived_operations_v1"
    if manifest.get("workspace_mode") not in (None, "derived_operations_v1"):
        raise ValueError("Unknown audit workspace mode")
    if inventory(blind / "candidate", allow_adapters=not operational) != manifest["audit_baseline"]:
        raise ValueError("Candidate business files changed during audit")
    for name, digest in manifest["manuals"].items():
        if sha256(blind / "candidate" / name) != digest:
            raise ValueError("Operation manual changed")
    for name, digest in manifest["artifacts"].items():
        if sha256(blind / name) != digest:
            raise ValueError("Frozen audit artifact changed")
    if report.get("candidate_hash") != manifest["candidate_hash"] or report.get("opaque_id") != manifest["opaque_id"] or report.get("spec_version") != "B1":
        raise ValueError("Report identifies a different candidate/spec")
    auditor = report.get("auditor", {})
    if not auditor.get("id") or auditor.get("model") != "gpt-6.1-sol" or auditor.get("effort") != "high":
        raise ValueError("Independent auditor must be identified and use gpt-6.1-sol/high")
    if operational:
        if manifest.get("runtime") != "runtime" or manifest.get("runtime_initial_inventory") != manifest["audit_baseline"] or manifest.get("runtime_to_candidate") != {name: name for name in manifest["audit_baseline"]}:
            raise ValueError("Runtime derivation does not match immutable reference")
        for name, digest in manifest["manuals"].items():
            if sha256(blind / "runtime" / name) != digest:
                raise ValueError("Runtime operation manual changed")
        module = provenance_module()
        if sha256(module.__file__) != manifest["artifacts"].get("provenance_tool.py"):
            raise ValueError("Controller provenance validator version differs from frozen blind tool")
        if judge.get('technical_adjudication_acceptance'):
            provenance = technical_adjudication_module().resolve(report_path, {c['id'] for c in spec.CHECKS}, action='full_report')
        else:
            provenance = module.validate_workspace(blind / "candidate", blind / "runtime", blind / "evidence", manifest["operational_provenance"], require_review=True)
        if provenance.get("primary_id") not in ("primary", auditor["id"]):
            raise ValueError("Operational primary identity does not match report auditor")
        if not provenance.get("reviewer_id") or provenance["reviewer_id"] == auditor["id"] or provenance["reviewer_id"] in provenance.get("operation_actor_ids", []):
            raise ValueError("Operational provenance requires an independent reviewer distinct from the report auditor")
    checks = report.get("checks")
    expected_ids = {check["id"] for check in spec.CHECKS}
    if not isinstance(checks, list) or len(checks) != 100 or {check.get("id") for check in checks} != expected_ids:
        raise ValueError("Exactly100 unique canonical IDs are required")
    decisions = []
    for check in checks:
        if check.get("candidate_hash") != manifest["candidate_hash"]:
            raise ValueError("Check candidate hash mismatch")
        overlay = None
        if check.get('id') == '057' and judge.get('measurement_dispute_acceptance'):
            overlay = measurement_dispute_module().resolve(report_path, report, check)
        elif check.get("status") != "verified" or type(check.get("score")) is not int or check["score"] not in (0, 1):
            if operational and check.get('id') == '057' and check.get('status') == 'review_required' and check.get('score') is None:
                overlay = measurement_dispute_module().resolve(report_path, report, check)
            else:
                raise ValueError("All100 items require verified integer0/1 decisions; not_run is incomplete")
        if not all(isinstance(check.get(field), str) and check[field].strip() for field in ("operation", "expected", "actual")):
            raise ValueError("Every decision requires operation/expected/actual")
        evidence_records(check.get("evidence"), blind / "evidence")
        if type(check.get("review_required")) is not bool:
            raise ValueError("review_required must be boolean")
        score = overlay['score'] if overlay else check["score"]
        unresolved = False
        if check["review_required"] or check.get("review"):
            if check["review_required"] and not check.get("review_reason"):
                raise ValueError("Required review needs a reason")
            reviewed = decision(check.get("review"), auditor["id"], blind / "evidence")
            if reviewed != score:
                if check.get("adjudication"):
                    if check["adjudication"].get("auditor_id") == check["review"]["auditor_id"]:
                        raise ValueError("Adjudication requires another independent auditor")
                    score = decision(check["adjudication"], auditor["id"], blind / "evidence")
                else:
                    score, unresolved = 0, True
        elif check.get("adjudication"):
            raise ValueError("Adjudication requires an initial independent review")
        effective = {"id": check["id"], "score": score, "unresolved_conflict": unresolved, "evidence": check["evidence"]}
        if overlay:
            effective['measurement_dispute'] = overlay
        decisions.append(effective)
    result = {"spec_version": "B1", "opaque_id": report["opaque_id"], "candidate_hash": report["candidate_hash"], "workspace_mode": manifest.get("workspace_mode", "legacy_strict"), "measurement_basis": "sealed_original_input_through_traceable_standard_unity_lifecycle" if operational else "strict_business_bytes", "score": sum(check["score"] for check in decisions), "denominator": 100, "unresolved_conflicts": [check["id"] for check in decisions if check["unresolved_conflict"]], "checks": sorted(decisions, key=lambda check: check["id"]), "report_sha256": sha256(report_path), "metrics": judge.get("metrics", {})}
    if operational and provenance.get('technical_mode'):
        result['technical_adjudication'] = {k: provenance[k] for k in (
            'canonical_review_approved', 'canonical_reviewer_id', 'canonical_review_sha256',
            'technical_mode', 'technical_adjudicator_id', 'eligible_check_ids', 'blocked_check_ids',
            'receipt_ref', 'verdict_ref', 'input_capsule_ref', 'evidence_refs')}
    return result


def save_score(report):
    result = validate_report(report)
    output = Path(report).parent
    write_json(output / "score.json", result)
    with (output / "score.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "score", "unresolved_conflict"])
        writer.writeheader()
        for check in result["checks"]:
            writer.writerow({name: check[name] for name in writer.fieldnames})
    return result


def summarize(manifest_path, report_paths, output):
    """Pair only from judge-provided groups, never infer identities from paths."""
    manifest = read_json(manifest_path)
    groups = manifest.get("groups")
    if isinstance(groups, dict):
        groups = [{"id": id_, **group} for id_, group in groups.items()]
    if not isinstance(groups, list) or len(groups) != 6:
        raise ValueError("Judge manifest requires six groups with opaque_id/model/use_skill/id")
    canonical = {group["id"]: group for group in spec.GROUPS}
    if {group.get("id") for group in groups} != set(canonical):
        raise ValueError("Judge groups must identify exactly the six canonical groups")
    for group in groups:
        approved = canonical[group["id"]]
        if group.get("model") != approved["model"] or type(group.get("use_skill")) is not bool or group["use_skill"] != approved["use_skill"]:
            raise ValueError("Judge model/treatment allocation differs from frozen B1")
    scores = [validate_report(path) for path in report_paths]
    by_opaque = {result["opaque_id"]: result for result in scores}
    if len(scores) != 6 or len(by_opaque) != 6 or set(by_opaque) != {group["opaque_id"] for group in groups}:
        raise ValueError("Exactly six distinct reports must match judge manifest")
    rows = []
    for group in groups:
        result = by_opaque[group["opaque_id"]]
        rows.append({"id": group["id"], "model": group["model"], "use_skill": group["use_skill"], "score": result["score"], "denominator": 100, "termination": group.get("termination"), "validity": group.get("validity", "unspecified"), **{key: result.get("metrics", {}).get(key) for key in ("time_seconds", "tokens", "cost", "subagents", "compactions")}})
    paired = []
    for model in sorted({row["model"] for row in rows}):
        pair = [row for row in rows if row["model"] == model]
        if len(pair) != 2 or {row["use_skill"] for row in pair} != {True, False}:
            raise ValueError("Each model must have exactly one treatment and one control")
        treatment = next(row for row in pair if row["use_skill"])
        control = next(row for row in pair if not row["use_skill"])
        pair_checks = {group["use_skill"]: {c["id"]: c["score"] for c in by_opaque[group["opaque_id"]]["checks"]} for group in groups if group["model"] == model}
        categories = {}
        for category in dict.fromkeys(c["category"] for c in spec.CHECKS):
            ids = [c["id"] for c in spec.CHECKS if c["category"] == category]
            categories[category] = sum(pair_checks[True][id_] - pair_checks[False][id_] for id_ in ids)
        comparable = all(row["validity"] == "valid" and row["termination"] in ("submitted", "abandoned", "timeout") for row in pair)
        paired.append({"model": model, "skill_minus_control": treatment["score"] - control["score"], "category_deltas": categories, "comparable": comparable, "comparison_note": None if comparable else "Contains invalid, interrupted, or unclassified run; numerical delta is descriptive only."})
    output = check_links(output)
    output.mkdir(parents=True, exist_ok=True)
    result = {"spec_version": "B1", "groups": rows, "pairs": paired, "limitation": "One run per condition on one task; no statistical significance or generalized benefit claim."}
    write_json(output / "results.json", result)
    with (output / "results.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--snapshot", required=True)
    init.add_argument("--out", required=True)
    init.add_argument("--opaque-id")
    init.add_argument("--operational", action="store_true", help="Create immutable candidate reference plus journaled runtime shadow")
    init.add_argument("--primary-id", default="primary", help="Immutable operational primary auditor identity")
    validate = commands.add_parser("validate")
    validate.add_argument("--evidence", required=True, help="Blind directory or audit.json path")
    score = commands.add_parser("score")
    score.add_argument("--report", required=True)
    summary = commands.add_parser("summarize")
    summary.add_argument("--manifest", required=True)
    summary.add_argument("--reports", required=True, nargs="+")
    summary.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    if args.command == "init":
        result = init_audit(args.snapshot, args.out, args.opaque_id, operational=args.operational, primary_id=args.primary_id)
    elif args.command == "validate":
        path = Path(args.evidence)
        result = validate_report(path / "audit.json" if path.is_dir() else path)
    elif args.command == "score":
        result = save_score(args.report)
    else:
        result = summarize(args.manifest, args.reports, args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"Audit failed: {error}", file=sys.stderr)
        raise SystemExit(2)
