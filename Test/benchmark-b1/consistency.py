"""Build a blinded, per-requirement B1 consistency review pack; never judge it.

All six input audit reports must already be complete and evidence-validated.
The pack is pending review material, not a statement that standards agree.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
from pathlib import Path
import re
import sys

try:
    from . import audit, spec
except ImportError:
    import audit
    import spec


INSTRUCTIONS = """# B1 六份交付的横向一致性复核

此包仅为待独立人工或 AI 复核的材料。生成它不证明验收一致，也不完成复核，不修改任何既有判分。

按 consistency.json 的001–100逐项比较六个匿名候选：先读同一冻结要求，再核对操作、预期、实测、证据、复核及裁定记录是否采用相同标准。必须检查全部100项，不能只挑分数不同的项目。分数不同本身不代表标准不一致，交付行为不同可以合理产生不同分数；相同分数也不代表标准一致。

保持 B1 原要求、分母和0/1规则不变，不新增要求、不降低或提高门槛。证据或测量存在分歧时，记录受影响编号与候选，交独立复核者用原业务和冻结测试复测；只允许新的测试适配层，不修复或替换业务实现。基础设施故障修复后重测，不归咎交付。

此包不附模型、技能条件、私有组别映射或成本等指标，仅对显式标识和绝对路径作带标记脱敏，并记录 redactions_applied。自由文本未必可自动可靠盲化，requires_judge_blinding 表示仍须评委检查，不能声称绝对匿名。匿名文本视图不替代原始事实或证据；原报告哈希是未改写记录的出处。不得主动寻找身份；如内容泄露身份，应先由评委处理，避免据组别判断。reviewer编号仅在该匿名候选内部表示不同审计身份。

证据以 opaque_id:sha256 的匿名索引给出，生成时已核验原文件存在和哈希。原始文件路径与内容不直接打包，避免日志或截图泄露身份；复核者应由评委按索引提供经过盲化检查的证据副本，并核对哈希（若脱敏改变内容，应另附脱敏来源和新哈希，保留原证据）。不能仅凭索引认定行为通过。

任何修订判定均须在原审计流程中形成独立证据、复核/裁定和可追溯报告，再重新验证并重新生成本包。本工具不写回输入报告，不自动裁决分歧，不填入复核完成状态。
"""


def redact_details(value):
    """Mark only explicit provenance/absolute paths; retain all other wording."""
    value = str(value)
    applied = []
    patterns = [
        ("absolute_path", r"(?<![\w])(?:[A-Za-z]:[\\/]|\\\\|/)[^\s<>\"`]+", "[absolute path redacted]"),
        ("explicit_model_or_skill", r"(?i)(?:gpt-)?(?:6\.1[ -]?(?:sol|terra)|5\.6[ -]?luna)|serial-job-session", "[provenance redacted]"),
        ("explicit_group_identifier", r"(?i)(?<!\w)Demo[1-5]?(?!\w)", "[candidate redacted]"),
    ]
    for category, pattern, replacement in patterns:
        value, count = re.subn(pattern, replacement, value)
        if count:
            applied.append({"kind": category, "count": count})
    return value, applied


def sanitize_text(value):
    return redact_details(value)[0]


def narrative_view(record, fields):
    values, changes = {}, []
    for field in fields:
        values[field], redactions = redact_details(record.get(field, ""))
        changes.extend({"field": field, **redaction} for redaction in redactions)
    return values, changes


def validator_descriptor():
    """Require the colocated current validator, never a cached archived audit.

    A frozen consistency bundle must include its own matching audit.py,
    operation_provenance.py and spec.py. No fallback to an older admin archive
    is permitted. The descriptor records content hashes, not private paths.
    """
    here = audit.check_links(__file__).resolve().parent
    if audit.check_links(audit.__file__).resolve() != here / "audit.py":
        raise ValueError("Consistency requires its colocated current audit.py validator")
    if not callable(getattr(audit, "provenance_module", None)) or "operational" not in inspect.signature(audit.init_audit).parameters:
        raise ValueError("Current derived_operations_v1 audit validator is required")
    provenance = audit.provenance_module()
    if audit.check_links(provenance.__file__).resolve() != here / "operation_provenance.py" or not callable(getattr(provenance, "validate_workspace", None)):
        raise ValueError("Consistency requires its colocated operation provenance validator")
    if audit.check_links(spec.__file__).resolve() != here / "spec.py":
        raise ValueError("Consistency requires its colocated frozen B1 spec")
    dispute = audit.measurement_dispute_module()
    if audit.check_links(dispute.__file__).resolve() != here / 'measurement_dispute.py':
        raise ValueError('Consistency requires its colocated measurement dispute validator')
    execution = dispute.execution_helper()
    if audit.check_links(execution.runner.__file__).resolve() != here / 'runner.py':
        raise ValueError('Consistency requires its colocated execution runner dependency')
    if audit.check_links(dispute.generation_module().__file__).resolve() != here / 'process_generation.py':
        raise ValueError('Consistency requires its colocated process generation dependency')
    technical = audit.technical_adjudication_module()
    if audit.check_links(technical.__file__).resolve() != here / 'technical_adjudication.py':
        raise ValueError('Consistency requires its colocated technical adjudication dependency')
    return {"audit_sha256": audit.sha256(here / "audit.py"),
            "operation_provenance_sha256": audit.sha256(here / "operation_provenance.py"),
            "measurement_dispute_sha256": audit.sha256(here / 'measurement_dispute.py'),
            "measurement_execution_helper_sha256": audit.sha256(here / 'closed_seal.py'),
            "process_generation_sha256": audit.sha256(here / 'process_generation.py'),
            "runner_sha256": audit.sha256(here / 'runner.py'),
            "technical_adjudication_sha256": audit.sha256(here / 'technical_adjudication.py'),
            "spec_sha256": audit.sha256(here / "spec.py"),
            "supported_workspace_modes": ["legacy_strict", "derived_operations_v1"]}


def build_pack(report_paths, output):
    """Validate all inputs before creating output; never change any scores."""
    if len(report_paths) != 6:
        raise ValueError("Exactly six explicit existing audit.json reports are required")
    validator = validator_descriptor()
    output = audit.check_links(output).resolve()
    if output.exists():
        raise ValueError("Output already exists; overwriting review material is prohibited")
    validated = []
    for supplied in report_paths:
        path = audit.check_links(supplied).resolve()
        if path.name != "audit.json" or not path.is_file():
            raise ValueError("Each input must be an existing audit.json")
        if output == path.parent or path.parent in output.parents or output in path.parents:
            raise ValueError("Review output must be disjoint from input audit directories")
        manifest = audit.read_json(path.parent / "manifest.json")
        mode = manifest.get("workspace_mode", "legacy_strict")
        if mode not in validator["supported_workspace_modes"]:
            raise ValueError("Unsupported consistency input workspace mode")
        result = audit.validate_report(path)
        if result.get("workspace_mode") != mode:
            raise ValueError("Validator did not validate the input workspace mode; archived validators are prohibited")
        if mode == "derived_operations_v1" and result.get("measurement_basis") != "sealed_original_input_through_traceable_standard_unity_lifecycle":
            raise ValueError("Derived input requires current operational provenance validation")
        report = audit.read_json(path)
        if report.get("spec_version") != "B1" or result.get("spec_version") != "B1":
            raise ValueError("All six reports must use B1")
        opaque = report["opaque_id"]
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", opaque) or sanitize_text(opaque) != opaque:
            raise ValueError("Candidate IDs must be anonymous opaque identifiers")
        if audit.sha256(path) != result["report_sha256"]:
            raise ValueError("Audit report changed during validation")
        validated.append((opaque, path, report, result))
    if len({entry[0] for entry in validated}) != 6:
        raise ValueError("Six distinct opaque candidate IDs are required")
    if len({entry[3]["workspace_mode"] for entry in validated}) != 1:
        raise ValueError("All six reports must use the same measurement workspace mode")
    validated.sort(key=lambda entry: entry[0])
    evidence_index = {}

    def evidence_refs(records, opaque, source_root):
        refs = []
        for record in records:
            source = Path(record["path"])
            source = source if source.is_absolute() else source_root / source
            source = audit.check_links(source).resolve()
            if not source.is_relative_to(source_root.resolve()) or audit.sha256(source) != record["sha256"]:
                raise ValueError("Evidence changed or escaped between validation and packaging")
            digest = record["sha256"]
            key = f"{opaque}:{digest}"
            if key not in evidence_index:
                evidence_index[key] = {"id": key, "sha256": digest, "bytes": source.stat().st_size}
            refs.append(key)
        return refs

    sources = {}
    candidate_metadata = []
    for opaque, path, report, result in validated:
        originals = {check["id"]: check for check in report["checks"]}
        effective = {check["id"]: check for check in result["checks"]}
        sources[opaque] = (path, originals, effective)
        candidate_metadata.append({"opaque_id": opaque, "candidate_hash": report["candidate_hash"], "report_sha256": result["report_sha256"], "workspace_mode": result["workspace_mode"], "measurement_basis": result["measurement_basis"]})
        if result.get('technical_adjudication'):
            technical = result['technical_adjudication']
            candidate_metadata[-1]['technical_adjudication'] = {
                'technical_mode': technical['technical_mode'], 'canonical_review_approved': False,
                'eligible_check_ids': technical['eligible_check_ids'], 'blocked_check_ids': technical['blocked_check_ids'],
                'evidence_refs': evidence_refs([technical['receipt_ref'], *technical['evidence_refs']], opaque, path.parent.parent)}

    rows = []
    for canonical in spec.CHECKS:
        entries = []
        for opaque, _, report, result in validated:
            path, originals, effective = sources[opaque]
            original = originals[canonical["id"]]
            final = effective[canonical["id"]]
            source_root = path.parent / "evidence"
            identities = {report["auditor"]["id"]: "reviewer-1"}

            def review_details(value):
                if value is None:
                    return None
                identity = value["auditor_id"]
                if identity not in identities:
                    identities[identity] = f"reviewer-{len(identities) + 1}"
                narrative, redactions = narrative_view(value, ("operation", "expected", "actual"))
                return {"reviewer": identities[identity], "score": value["score"], **narrative, "redactions_applied": redactions, "requires_judge_blinding": True, "evidence_refs": evidence_refs(value["evidence"], opaque, source_root)}

            narrative, redactions = narrative_view(original, ("operation", "expected", "actual", "review_reason"))
            entries.append({"opaque_id": opaque, "candidate_hash": report["candidate_hash"], "report_sha256": result["report_sha256"], **narrative, "redactions_applied": redactions, "requires_judge_blinding": True, "original_score": original["score"], "effective_score": final["score"], "unresolved_conflict": final["unresolved_conflict"], "evidence_refs": evidence_refs(original["evidence"], opaque, source_root), "review_required": original["review_required"], "review": review_details(original.get("review")), "adjudication": review_details(original.get("adjudication"))})
            if final.get('measurement_dispute'):
                overlay = final['measurement_dispute']
                extra = evidence_refs(overlay['decision']['evidence'] + [
                    {'path': 'measurement-dispute.json', 'sha256': overlay['overlay_sha256']}], opaque, source_root)
                entries[-1]['measurement_dispute'] = {
                    'primary_status': original['status'], 'primary_score': original['score'],
                    'decision': review_details(overlay['decision']), 'effective_score': final['score'],
                    'overlay_sha256': overlay['overlay_sha256'], 'acceptance_sha256': overlay['acceptance_sha256'],
                    'original_primary_report_sha256': overlay['original_primary_report_sha256'],
                    'evidence_refs': extra, 'basis': overlay['basis']}
                entries[-1]['evidence_refs'] = list(dict.fromkeys(entries[-1]['evidence_refs'] + extra))
        rows.append({"id": canonical["id"], "requirement": canonical["title"], "category": canonical["category"], "candidates": entries})
    # Confirm input audit documents were not rewritten while collecting evidence.
    for _, path, _, result in validated:
        if audit.sha256(path) != result["report_sha256"]:
            raise ValueError("Audit report changed during packaging")
        # Revalidate complete prerequisites too: evidence collection must not
        # allow a runtime/journal/review change after initial validation.
        current = audit.validate_report(path)
        if current != result:
            raise ValueError("Audit validation changed during packaging")
    if validator_descriptor() != validator:
        raise ValueError("Validator sources changed during packaging")
    payload = {"spec_version": "B1", "validator": validator, "purpose": "Pending independent cross-candidate consistency review material only; no consistency conclusion or score changes.", "requires_judge_blinding": True, "narrative_notice": "Explicit provenance and paths are marked, not silently rewritten. Free text still requires judge blinding checks. Views never replace original report/evidence facts; consult report_sha256.", "canonical_requirements_sha256": hashlib.sha256(json.dumps(spec.CHECKS, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(), "candidates": candidate_metadata, "rows": rows, "evidence_index": [evidence_index[key] for key in sorted(evidence_index)]}
    # All six reports, provenance prerequisites and source hashes have passed;
    # only now may output exist.
    output.mkdir(parents=True, exist_ok=False)
    audit.write_json(output / "consistency.json", payload)
    (output / "REVIEW.md").write_text(INSTRUCTIONS, encoding="utf-8")
    audit.write_json(output / "artifacts.json", {"consistency.json": audit.sha256(output / "consistency.json"), "REVIEW.md": audit.sha256(output / "REVIEW.md")})
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports", nargs="+", required=True, help="Exactly six complete blind/audit.json reports")
    parser.add_argument("--out", required=True, help="New, disjoint output directory; existing output is rejected")
    args = parser.parse_args(argv)
    result = build_pack(args.reports, args.out)
    print(json.dumps({"spec_version": "B1", "candidates": len(result["candidates"]), "requirements": len(result["rows"]), "output": str(Path(args.out).resolve()), "note": "Pending review material only; no judgments changed."}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(f"Consistency packaging failed: {error}", file=sys.stderr)
        raise SystemExit(2)
