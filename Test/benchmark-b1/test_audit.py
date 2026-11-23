"""Synthetic audit evidence only: never grade or modify formal games."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import audit


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "sealed"
        for directory in audit.BUSINESS_DIRS:
            (self.source / directory).mkdir(parents=True)
        (self.source / "Assets/Game.cs").write_text("original business implementation", encoding="utf-8")
        (self.source / "ProjectSettings/ProjectSettings.asset").write_bytes(b"PlayerSettings:\r\n  companyName: Test\r\n  productName: Original\r\n  other: unchanged\r\n")
        (self.source / "README.md").write_text("Open Main scene. gpt-6.1-sol E:/Test/Demo/AITest", encoding="utf-8")
        (self.source / ".serial-job-session").mkdir()
        (self.source / ".serial-job-session/private.json").write_text("do not copy", encoding="utf-8")
        self.out = self.root / "audit"
        audit.init_audit(self.source, self.out, "opaque")
        self.blind = self.out / "blind"
        self.report = self.blind / "audit.json"

    def fill(self):
        evidence = self.blind / "evidence/runtime.txt"
        evidence.write_text("Synthetic test evidence, not a real benchmark judgment", encoding="utf-8")
        document = audit.read_json(self.report)
        document["auditor"]["id"] = "independent-a"
        for check in document["checks"]:
            check.update(status="verified", score=1, operation="synthetic operation", actual="synthetic success", evidence=[{"path": "runtime.txt", "sha256": audit.sha256(evidence)}])
        audit.write_json(self.report, document)
        return document

    def operational_workspace(self, primary_id="primary"):
        self.out = self.root / "operational-audit"
        audit.init_audit(self.source, self.out, "operational-opaque", operational=True, primary_id=primary_id)
        self.blind = self.out / "blind"
        self.report = self.blind / "audit.json"
        return audit.read_json(self.blind / "manifest.json")

    def test_operational_init_copies_immutable_reference_without_reanchoring(self):
        manifest = self.operational_workspace()
        self.assertEqual(manifest["workspace_mode"], "derived_operations_v1")
        self.assertEqual(manifest["runtime"], "runtime")
        self.assertEqual(audit.inventory(self.blind / "candidate"), audit.inventory(self.blind / "runtime"))
        self.assertEqual(manifest["runtime_initial_inventory"], manifest["audit_baseline"])
        self.assertEqual(manifest["runtime_to_candidate"], {name: name for name in manifest["audit_baseline"]})
        self.assertEqual(manifest["candidate_hash"], audit.fingerprint(audit.inventory(self.source)))
        self.assertIn("operational_provenance", manifest)
        prompt = (self.blind / "prompt.md").read_text(encoding="utf-8")
        self.assertIn(str((self.blind / "runtime").resolve()), prompt)
        self.assertIn("immutable reference U", prompt)
        self.assertIn("runtime/Assets/__B1Audit", prompt)
        self.assertNotIn("Run audit.py validate/score", prompt)
        self.assertIn("controller performs final business validation", prompt)
        self.assertIn("original code/meta GUID/dependency changes", prompt)
        self.assertIn("Original non-GUID meta importer data", prompt)
        self.assertIn("An unchanged GUID never proves semantic equivalence", prompt)
        self.assertTrue(all(check["score"] is None for check in audit.read_json(self.report)["checks"]))

    def test_operational_reference_rejects_even_adapter_writes(self):
        self.operational_workspace()
        self.fill()
        forbidden = self.blind / "candidate/Assets/__B1Audit"
        forbidden.mkdir()
        (forbidden / "Wrong.cs").write_text("wrong workspace", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Candidate business files changed"):
            audit.validate_report(self.report)

    def test_operational_missing_provenance_review_blocks(self):
        self.operational_workspace()
        self.fill()
        with self.assertRaises(ValueError):
            audit.validate_report(self.report)

    def test_operational_controller_validator_must_match_frozen_tool_version(self):
        self.operational_workspace()
        self.fill()
        different_module = self.root / "different_provenance_version.py"
        different_module.write_text("# different validator version, even with a compatible API", encoding="utf-8")
        module = SimpleNamespace(__file__=str(different_module))
        with patch.object(audit, "provenance_module", return_value=module):
            with self.assertRaisesRegex(ValueError, "validator version differs from frozen"):
                audit.validate_report(self.report)

    def test_initial_reference_copy_cannot_reanchor_polluted_code_or_settings(self):
        original_copytree = audit.shutil.copytree
        source_hashes = audit.inventory(self.source)
        for directory, relative in (("Assets", "Game.cs"), ("ProjectSettings", "ProjectSettings.asset")):
            with self.subTest(directory=directory):
                def corrupt_copy(source, destination, *args, **kwargs):
                    result = original_copytree(source, destination, *args, **kwargs)
                    if Path(destination).name == directory:
                        (Path(destination) / relative).write_text("copy pollution", encoding="utf-8")
                    return result

                with patch.object(audit.shutil, "copytree", side_effect=corrupt_copy):
                    with self.assertRaisesRegex(ValueError, "reference copy differs from sealed input"):
                        audit.init_audit(self.source, self.root / f"polluted-copy-{directory}", "polluted", operational=True)
                self.assertEqual(audit.inventory(self.source), source_hashes)

    def test_operational_journal_review_accepts_traceable_synthetic_data_derivation(self):
        # This is a synthetic protocol test, never a claim about real Unity
        # normalization or a real game's correctness.
        (self.source / "Assets/Synthetic.mat").write_text("before-bytes", encoding="utf-8")
        manifest = self.operational_workspace(primary_id="independent-a")
        self.fill()
        module = audit.provenance_module()
        reference, runtime, evidence = [(self.blind / name).resolve() for name in ("candidate", "runtime", "evidence")]
        invocation = evidence / "synthetic-invocation.txt"
        invocation.write_text("synthetic operation record for isolated protocol unit test only", encoding="utf-8")
        refs = [{"path": invocation.name, "sha256": audit.sha256(invocation)}]
        p = manifest["operational_provenance"]
        provenance = {"actor_id": "independent-a", "invocation": {"kind": "adapter", "command": "synthetic unit-test transform", "refs": [invocation.name]}, "source_refs": [{"path": "Assets/Game.cs", "sha256": manifest["audit_baseline"]["Assets/Game.cs"]}], "evidence_refs": refs}
        module.begin_operation(reference, runtime, evidence, p, "normal_run", "synthetic-run", provenance)
        (runtime / "Assets/Synthetic.mat").write_text("after-bytes", encoding="utf-8")
        module.finish_operation(reference, runtime, evidence, p, "synthetic-run", provenance)
        observed = module.validate_workspace(reference, runtime, evidence, p, require_review=False)
        review = {"schema": "b1-provenance-review-v1", "manifest_sha256": p["manifest_sha256"], "candidate_hash": p["candidate_hash"], "journal_head": observed["journal_head"], "primary_id": p["primary_id"], "reviewer_id": "independent-b", "independent": True, "decisions": [{"operation_id": change["operation_id"], "path": change["path"], "decision": "approved", "basis": "synthetic protocol fixture only; no semantic equivalence claim", "evidence_refs": refs} for change in observed["changes"]], "final": {"approved": True, "runtime_digest": observed["current_digest"], "reference_digest": observed["reference_digest"], "basis": "synthetic protocol final evidence", "evidence_refs": refs}}
        audit.write_json(evidence / "provenance-review.json", review)
        result = audit.validate_report(self.report)
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["workspace_mode"], "derived_operations_v1")
        self.assertEqual(result["measurement_basis"], "sealed_original_input_through_traceable_standard_unity_lifecycle")
        self.assertEqual(audit.inventory(reference), manifest["audit_baseline"])
        self.assertNotEqual(audit.inventory(runtime), manifest["audit_baseline"])
        review["reviewer_id"] = "independent-a"
        audit.write_json(evidence / "provenance-review.json", review)
        with self.assertRaises(ValueError):
            audit.validate_report(self.report)
        (self.blind / "runtime/Assets/Game.cs").write_text("prohibited repair", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "unjournaled|protected|code"):
            audit.validate_report(self.report)

    def test_init_is_blind_ungraded_and_namespace_only(self):
        report = audit.read_json(self.report)
        self.assertTrue(all(c["score"] is None and c["status"] == "not_run" for c in report["checks"]))
        self.assertFalse((self.blind / "candidate/.serial-job-session").exists())
        original = (self.source / "ProjectSettings/ProjectSettings.asset").read_bytes()
        candidate = (self.blind / "candidate/ProjectSettings/ProjectSettings.asset").read_bytes()
        self.assertEqual(candidate, original.replace(b"productName: Original", b"productName: B1Audit-opaque"))
        self.assertNotIn("gpt-6.1-sol", (self.blind / "candidate/README.md").read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            audit.validate_report(self.report)

    def test_actual_evidence_required_and_complete_score(self):
        document = self.fill()
        self.assertEqual(audit.save_score(self.report)["score"], 100)
        document["checks"][0]["evidence"] = []
        audit.write_json(self.report, document)
        with self.assertRaisesRegex(ValueError, "actual evidence"):
            audit.validate_report(self.report)

    def test_duplicate_ids_rejected(self):
        document = self.fill()
        document["checks"][-1]["id"] = "001"
        audit.write_json(self.report, document)
        with self.assertRaisesRegex(ValueError, "unique"):
            audit.validate_report(self.report)

    def test_missing_tampered_or_outside_evidence_rejected(self):
        document = self.fill()
        document["checks"][0]["evidence"][0]["path"] = "missing.txt"
        audit.write_json(self.report, document)
        with self.assertRaises(ValueError):
            audit.validate_report(self.report)
        document = self.fill()
        document["checks"][0]["evidence"][0]["sha256"] = "0" * 64
        audit.write_json(self.report, document)
        with self.assertRaisesRegex(ValueError, "sha256"):
            audit.validate_report(self.report)
        document = self.fill()
        document["checks"][0]["evidence"][0]["path"] = "../prompt.md"
        audit.write_json(self.report, document)
        with self.assertRaises(ValueError):
            audit.validate_report(self.report)

    def test_business_changes_rejected_new_adapters_allowed(self):
        self.fill()
        adapters = self.blind / "candidate/Assets/__B1Audit"
        adapters.mkdir()
        (adapters / "Tests.cs").write_text("test adapter", encoding="utf-8")
        self.assertEqual(audit.validate_report(self.report)["score"], 100)
        (self.blind / "candidate/Assets/Game.cs").write_text("repaired business", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "business files changed"):
            audit.validate_report(self.report)

    def test_original_snapshot_change_rejected(self):
        self.fill()
        (self.source / "Assets/Game.cs").write_text("mutated sealed version", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "sealed snapshot"):
            audit.validate_report(self.report)

    def test_windows_reparse_attribute_rejected_on_python311(self):
        original_lstat = Path.lstat
        junction = self.source / "Assets"

        def fake_lstat(path):
            details = original_lstat(path)
            if path == junction:
                return SimpleNamespace(st_mode=details.st_mode, st_file_attributes=0x400)
            return details

        with patch.object(Path, "lstat", fake_lstat):
            with self.assertRaisesRegex(ValueError, "reparse"):
                audit.check_links(junction / "Game.cs")
            with self.assertRaisesRegex(ValueError, "reparse"):
                audit.inventory(self.source)
            with self.assertRaisesRegex(ValueError, "reparse"):
                audit.sha256(junction / "Game.cs")

    def test_independent_review_conflict_scores_zero_until_adjudicated(self):
        document = self.fill()
        check = document["checks"][0]
        check.update(review_required=True, review_reason="conflicting evidence", review={"auditor_id": "independent-b", "score": 0, "operation": "retest", "expected": "pass", "actual": "fail", "evidence": check["evidence"]})
        audit.write_json(self.report, document)
        result = audit.validate_report(self.report)
        self.assertEqual(result["score"], 99)
        self.assertEqual(result["unresolved_conflicts"], ["001"])
        check["adjudication"] = {**check["review"], "auditor_id": "independent-c", "score": 1}
        audit.write_json(self.report, document)
        self.assertEqual(audit.validate_report(self.report)["score"], 100)
        check["review"]["auditor_id"] = "independent-a"
        audit.write_json(self.report, document)
        with self.assertRaisesRegex(ValueError, "independent"):
            audit.validate_report(self.report)

    def test_summarize_uses_only_explicit_judge_pairing_and_null_metrics(self):
        reports = []
        groups = []
        for index, group in enumerate(audit.spec.GROUPS):
            output = self.root / f"audit-{index}"
            opaque = f"opaque-{index}"
            audit.init_audit(self.source, output, opaque)
            path = output / "blind/audit.json"
            document = audit.read_json(path)
            evidence = output / "blind/evidence/runtime.txt"
            evidence.write_text("synthetic summary evidence", encoding="utf-8")
            document["auditor"]["id"] = "independent-a"
            for check in document["checks"]:
                check.update(status="verified", score=int(group["use_skill"]), operation="synthetic", actual="synthetic", evidence=[{"path": "runtime.txt", "sha256": audit.sha256(evidence)}])
            audit.write_json(path, document)
            reports.append(path)
            groups.append({**group, "opaque_id": opaque, "termination": "submitted", "validity": "valid"})
        judge = self.root / "pairing.json"
        audit.write_json(judge, {"groups": groups})
        result = audit.summarize(judge, list(reversed(reports)), self.root / "summary")
        self.assertTrue(all(pair["skill_minus_control"] == 100 and pair["comparable"] for pair in result["pairs"]))
        self.assertTrue(all(row["tokens"] is None and row["cost"] is None for row in result["groups"]))
        self.assertTrue((self.root / "summary/results.csv").is_file())
        groups[0]["model"] = "wrong-model"
        audit.write_json(judge, {"groups": groups})
        with self.assertRaisesRegex(ValueError, "allocation"):
            audit.summarize(judge, reports, self.root / "bad-summary")


if __name__ == "__main__":
    unittest.main()
