"""Only synthetic audit fixtures; never invoke models or formal game projects."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import audit
import consistency


class ConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        source = self.root / "synthetic-source"
        for directory in audit.BUSINESS_DIRS:
            (source / directory).mkdir(parents=True)
        (source / "Assets/Synthetic.cs").write_text("// synthetic fixture only", encoding="utf-8")
        (source / "ProjectSettings/ProjectSettings.asset").write_text("PlayerSettings:\n  productName: Synthetic\n", encoding="utf-8")
        self.reports = []
        for index in range(6):
            out = self.root / f"audit-{index}"
            audit.init_audit(source, out, f"opaque-{5-index}")
            path = out / "blind/audit.json"
            evidence = out / "blind/evidence/runtime.txt"
            evidence.write_text("Private provenance may exist here: gpt-6.1-sol Demo1. Do not copy raw logs.", encoding="utf-8")
            document = audit.read_json(path)
            document["auditor"]["id"] = f"private-auditor-{index}"
            document["private_mapping"] = {"model": "gpt-6.1-sol", "use_skill": True, "group": "Demo1"}
            document["metrics"] = {"cost": 123456789}
            for check in document["checks"]:
                check.update(status="verified", score=index % 2, operation="Run E:/Test/Demo1/AITest using gpt-6.1-sol serial-job-session", actual="Synthetic behavior: Demo1", evidence=[{"path": "runtime.txt", "sha256": audit.sha256(evidence)}])
            audit.write_json(path, document)
            self.reports.append(path)

    def test_six_complete_reports_create_100_blind_rows_without_judgment(self):
        output = self.root / "review"
        result = consistency.build_pack(self.reports, output)
        self.assertEqual([candidate["opaque_id"] for candidate in result["candidates"]], [f"opaque-{i}" for i in range(6)])
        self.assertEqual([row["id"] for row in result["rows"]], [f"{i:03d}" for i in range(1, 101)])
        self.assertTrue(all(len(row["candidates"]) == 6 for row in result["rows"]))
        text = (output / "consistency.json").read_text(encoding="utf-8")
        for forbidden in ("gpt-6.1-sol", "serial-job-session", "Demo1", "E:/Test", "private_mapping", "metrics", "private-auditor", "123456789"):
            self.assertNotIn(forbidden, text)
        self.assertNotIn("complete", result)
        self.assertIn("Pending", result["purpose"])
        self.assertTrue(result["requires_judge_blinding"])
        candidate = result["rows"][0]["candidates"][0]
        self.assertTrue(candidate["redactions_applied"])
        self.assertIn("[absolute path redacted]", candidate["operation"])
        self.assertIn("[provenance redacted]", candidate["operation"])
        self.assertEqual(len(result["evidence_index"]), 6)
        self.assertFalse((output / "evidence").exists())
        self.assertTrue((output / "REVIEW.md").is_file())
        with self.assertRaisesRegex(ValueError, "already exists"):
            consistency.build_pack(self.reports, output)

    def test_missing_six_reports_is_blocked_without_output(self):
        output = self.root / "missing"
        with self.assertRaisesRegex(ValueError, "six"):
            consistency.build_pack(self.reports[:5], output)
        self.assertFalse(output.exists())

    def test_duplicate_opaque_is_blocked_without_output(self):
        output = self.root / "duplicate"
        with self.assertRaisesRegex(ValueError, "distinct"):
            consistency.build_pack([self.reports[0], *self.reports[:5]], output)
        self.assertFalse(output.exists())

    def test_tampered_evidence_hash_is_blocked_without_output(self):
        evidence = self.reports[-1].parent / "evidence/runtime.txt"
        evidence.write_text("tampered", encoding="utf-8")
        output = self.root / "tampered"
        with self.assertRaisesRegex(ValueError, "sha256"):
            consistency.build_pack(self.reports, output)
        self.assertFalse(output.exists())

    def test_original_and_effective_scores_and_review_preserved(self):
        document = audit.read_json(self.reports[0])
        check = document["checks"][0]
        check["score"] = 1
        check.update(review_required=True, review_reason="Runtime disagreement", review={"auditor_id": "another-private-auditor", "score": 0, "operation": "Retest", "expected": "same requirement", "actual": "Different recorded behavior", "evidence": check["evidence"]})
        audit.write_json(self.reports[0], document)
        result = consistency.build_pack(self.reports, self.root / "conflict")
        candidate = next(c for c in result["rows"][0]["candidates"] if c["opaque_id"] == "opaque-5")
        self.assertEqual(candidate["original_score"], 1)
        self.assertEqual(candidate["effective_score"], 0)
        self.assertTrue(candidate["unresolved_conflict"])
        self.assertEqual(candidate["review"]["reviewer"], "reviewer-2")
        self.assertIsNone(candidate["adjudication"])
        self.assertEqual(audit.read_json(self.reports[0])["checks"][0]["score"], 1)

    def test_descriptive_facts_retained_except_explicit_provenance(self):
        original = "At score 400 two full planes cleared and marker moved from y=7 to y=5; profile 玩家甲 persisted."
        viewed, changes = consistency.redact_details(original)
        self.assertEqual(viewed, original)
        self.assertEqual(changes, [])


class DerivedConsistencyTests(unittest.TestCase):
    """Real current validators over tiny synthetic fixtures, never real games."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        source = self.root / "tiny-synthetic-reference"
        for directory in audit.BUSINESS_DIRS:
            (source / directory).mkdir(parents=True)
        (source / "Assets/Synthetic.cs").write_text("// isolated test fixture, not game logic", encoding="utf8")
        (source / "Assets/Fixture.mat").write_bytes(b"synthetic initial data")
        (source / "Packages/manifest.json").write_text('{"dependencies":{}}', encoding="utf8")
        (source / "Packages/packages-lock.json").write_text('{}', encoding="utf8")
        (source / "ProjectSettings/ProjectSettings.asset").write_text("PlayerSettings:\n  productName: Synthetic\n", encoding="utf8")
        module = audit.provenance_module()
        self.reports = []
        for index in range(6):
            opaque, primary = f"opaque-derived-{index}", f"synthetic-primary-{index}"
            out = self.root / f"case-{index}"
            audit.init_audit(source, out, opaque, operational=True, primary_id=primary)
            blind = out / "blind"
            manifest = audit.read_json(blind / "manifest.json")
            evidence = blind / "evidence"
            log = evidence / "synthetic-operation.txt"
            log.write_text("Synthetic unit test invocation only; no Unity or model was run. Complete fixture byte transform recorded.", encoding="utf8")
            refs = [{"path": log.name, "sha256": audit.sha256(log)}]
            provenance = {"actor_id": primary, "invocation": {"kind": "adapter", "command": "synthetic fixture byte derivation", "refs": ["synthetic-call-1"]},
                          "source_refs": [{"path": "Assets/Synthetic.cs", "sha256": manifest["audit_baseline"]["Assets/Synthetic.cs"]}], "evidence_refs": refs}
            p = manifest["operational_provenance"]
            args = ((blind / "candidate").resolve(), (blind / "runtime").resolve(), evidence.resolve(), p)
            module.begin_operation(*args, "normal_run", "synthetic-operation", provenance)
            (blind / "runtime/Assets/Fixture.mat").write_bytes(b"synthetic derived data; no semantic equivalence assertion")
            module.finish_operation(*args, "synthetic-operation")
            state = module.validate_workspace(*args, require_review=False)
            review = {"schema": "b1-provenance-review-v1", "manifest_sha256": p["manifest_sha256"], "candidate_hash": p["candidate_hash"],
                      "journal_head": state["journal_head"], "primary_id": primary, "reviewer_id": f"synthetic-provenance-reviewer-{index}", "independent": True,
                      "decisions": [{"operation_id": change["operation_id"], "path": change["path"], "decision": "approved",
                                     "basis": "Synthetic protocol fixture only: invocation and complete bytes independently checked; no real behavior/equivalence claim.", "evidence_refs": refs} for change in state["changes"]],
                      "final": {"approved": True, "runtime_digest": state["current_digest"], "reference_digest": state["reference_digest"],
                                "basis": "Synthetic final hash verification only.", "evidence_refs": refs}}
            audit.write_json(evidence / "provenance-review.json", review)
            report = blind / "audit.json"
            document = audit.read_json(report)
            for check in document["checks"]:
                check.update(status="verified", score=index % 2, operation="Synthetic protocol input; no real game measured", actual="Synthetic evidence fixture supplied for consistency packaging only", evidence=refs)
            audit.write_json(report, document)
            self.reports.append(report)

    def test_real_derived_validators_package_six_synthetic_full100_reports(self):
        before = {str(p): audit.sha256(p) for p in self.reports}
        result = consistency.build_pack(self.reports, self.root / "derived-pack")
        self.assertEqual(len(result["candidates"]), 6)
        self.assertEqual(len({x["opaque_id"] for x in result["candidates"]}), 6)
        self.assertEqual(len(result["rows"]), 100)
        self.assertTrue(all(len(row["candidates"]) == 6 for row in result["rows"]))
        self.assertTrue(all(x["workspace_mode"] == "derived_operations_v1" for x in result["candidates"]))
        self.assertTrue(all(x["measurement_basis"] == "sealed_original_input_through_traceable_standard_unity_lifecycle" for x in result["candidates"]))
        self.assertEqual(result["validator"]["audit_sha256"], audit.sha256(Path(audit.__file__)))
        self.assertEqual(result["validator"]["operation_provenance_sha256"], audit.sha256(Path(audit.provenance_module().__file__)))
        self.assertEqual({str(p): audit.sha256(p) for p in self.reports}, before)
        self.assertFalse(any(self.root.rglob("score.json")))

    def test_old_validator_without_derived_mode_result_is_rejected(self):
        current = audit.validate_report
        def obsolete_result(path):
            result = current(path)
            result.pop("workspace_mode")
            return result
        output = self.root / "old-validator"
        with patch.object(audit, "validate_report", side_effect=obsolete_result):
            with self.assertRaisesRegex(ValueError, "archived validators"):
                consistency.build_pack(self.reports, output)
        self.assertFalse(output.exists())

    def test_cached_validator_from_another_archive_is_rejected(self):
        output = self.root / "cached-validator"
        with patch.object(audit, "__file__", str(self.root / "judge-consistency-001/audit.py")):
            with self.assertRaisesRegex(ValueError, "colocated current audit"):
                consistency.build_pack(self.reports, output)
        self.assertFalse(output.exists())

    def test_current_validator_rejects_missing_independent_provenance_review(self):
        (self.reports[2].parent / "evidence/provenance-review.json").unlink()
        output = self.root / "missing-review"
        with self.assertRaises(ValueError):
            consistency.build_pack(self.reports, output)
        self.assertFalse(output.exists())

    def test_current_validator_rejects_unjournaled_runtime_write(self):
        (self.reports[5].parent / "runtime/Assets/Fixture.mat").write_bytes(b"unjournaled")
        output = self.root / "unjournaled"
        with self.assertRaisesRegex(ValueError, "unjournaled"):
            consistency.build_pack(self.reports, output)
        self.assertFalse(output.exists())

    def test_runtime_changes_during_packaging_are_revalidated(self):
        current = audit.validate_report
        calls = 0
        def change_after_initial_validation(path):
            nonlocal calls
            result = current(path)
            calls += 1
            if calls == 6:
                (self.reports[0].parent / "runtime/Assets/Fixture.mat").write_bytes(b"changed after first validation")
            return result
        output = self.root / "race"
        with patch.object(audit, "validate_report", side_effect=change_after_initial_validation):
            with self.assertRaisesRegex(ValueError, "unjournaled"):
                consistency.build_pack(self.reports, output)
        self.assertFalse(output.exists())

    def test_six_reports_cannot_mix_legacy_and_derived_measurement_methods(self):
        out = self.root / "legacy-case"
        audit.init_audit(self.root / "tiny-synthetic-reference", out, "opaque-legacy")
        report = out / "blind/audit.json"
        log = out / "blind/evidence/synthetic.txt"
        log.write_text("Synthetic legacy protocol fixture only", encoding="utf8")
        document = audit.read_json(report)
        document["auditor"]["id"] = "synthetic-legacy-auditor"
        for check in document["checks"]:
            check.update(status="verified", score=0, operation="Synthetic legacy input", actual="Synthetic evidence only", evidence=[{"path": log.name, "sha256": audit.sha256(log)}])
        audit.write_json(report, document)
        output = self.root / "mixed-methods"
        with self.assertRaisesRegex(ValueError, "same measurement workspace mode"):
            consistency.build_pack([*self.reports[:5], report], output)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
