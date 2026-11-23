"""Synthetic shadow provenance tests; no formal game, Unity or model calls."""
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import operation_provenance as p


class OperationProvenanceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.u, self.r, self.e = [self.root / x for x in ("candidate", "runtime", "evidence")]
        for scope in p.SCOPES:
            (self.u / scope).mkdir(parents=True)
        (self.u / "Assets/Game.cs").write_bytes(b"original code")
        (self.u / "Assets/Game.cs.meta").write_bytes(b"fileFormatVersion: 2\nguid: " + b"a" * 32 + b"\nimporter: old\n")
        (self.u / "Assets/Cell.mat").write_bytes(b"original material bytes")
        (self.u / "Packages/manifest.json").write_text('{"dependencies":{}}')
        (self.u / "Packages/packages-lock.json").write_text('{}')
        (self.u / "ProjectSettings/ProjectSettings.asset").write_bytes(b"original configuration")
        self.m = p.initialize(self.u, self.r, self.e, "b" * 64, primary_id="actual-primary")
        self.e.mkdir(exist_ok=True)
        (self.e / "tool.json").write_text('{"actual_call":"exact original invocation"}')
        self.prov = {"actor_id": "actual-primary", "invocation": {"kind": "tool_call", "command": "Original.Build()", "refs": ["tool-call-1"]},
                     "source_refs": [{"path": "Assets/Game.cs", "sha256": self.m["reference_inventory"]["Assets/Game.cs"]}],
                     "evidence_refs": [{"path": "tool.json", "sha256": p._file_hash(self.e / "tool.json")} ]}

    def begin(self, op="op-1", kind="normal_run", prov=None):
        return p.begin_operation(self.u, self.r, self.e, self.m, kind, op, self.prov if prov is None else prov)

    def finish(self, op="op-1", prov=None):
        return p.finish_operation(self.u, self.r, self.e, self.m, op, prov)

    def validate(self, review=False):
        return p.validate_workspace(self.u, self.r, self.e, self.m, require_review=review)

    def change(self, op="op-1", payload=b"standard Unity derived material"):
        self.begin(op)
        (self.r / "Assets/Cell.mat").write_bytes(payload)
        return self.finish(op)

    def review(self, reviewer="independent-reviewer"):
        state = self.validate()
        doc = {"schema": "b1-provenance-review-v1", "manifest_sha256": self.m["manifest_sha256"],
               "candidate_hash": self.m["candidate_hash"], "journal_head": state["journal_head"],
               "primary_id": self.m["primary_id"], "reviewer_id": reviewer, "independent": True,
               "decisions": [{"operation_id": c["operation_id"], "path": c["path"], "decision": "approved",
                              "basis": "Actual recorded original invocation and complete before/after bytes reviewed; no semantic equivalence asserted.",
                              "evidence_refs": self.prov["evidence_refs"]} for c in state["changes"]],
               "final": {"approved": True, "runtime_digest": state["current_digest"], "reference_digest": state["reference_digest"],
                         "basis": "Independently verified complete chain and final hashes.", "evidence_refs": self.prov["evidence_refs"]}}
        p._write(self.e / "provenance-review.json", doc)
        return doc

    def test_initial_copy_and_no_change_final_review(self):
        self.assertEqual(p.inventory(self.u), p.inventory(self.r))
        self.assertTrue(self.validate()["review_required"])
        with self.assertRaises(p.ProvenanceError):
            self.validate(True)
        self.review()
        self.assertTrue(self.validate(True)["valid"])

    def test_dirty_runtime_never_accepted_or_repaired(self):
        root = self.root / "second"
        root.mkdir()
        r, e = root / "runtime", root / "evidence"
        import shutil
        shutil.copytree(self.u, r)
        (r / "Assets/Cell.mat").write_bytes(b"polluted")
        with self.assertRaisesRegex(p.ProvenanceError, "differs"):
            p.initialize(self.u, r, e, "b" * 64)
        self.assertEqual((r / "Assets/Cell.mat").read_bytes(), b"polluted")
        self.assertFalse(e.exists())

    def test_data_change_requires_independent_review_all_bytes(self):
        op = self.change()
        self.assertEqual(len(op["changes"]), 1)
        self.assertTrue(self.validate()["pending_review"])
        for key in ("before_sha256", "after_sha256"):
            h = op["changes"][0][key]
            self.assertEqual(p._file_hash(self.e / "provenance/blobs" / h), h)
        self.review()
        self.assertTrue(self.validate(True)["valid"])

    def test_second_change_chain_and_deletion_capture_old_bytes(self):
        first = self.change()
        self.begin("delete")
        (self.r / "Assets/Cell.mat").unlink()
        second = self.finish("delete")
        self.assertEqual(second["previous_operation_sha256"], first["operation_sha256"])
        self.assertIsNone(second["changes"][0]["after_sha256"])
        self.review()
        self.assertTrue(self.validate(True)["valid"])

    def test_unjournaled_data_and_reference_tamper_blocked(self):
        (self.r / "Assets/Cell.mat").write_bytes(b"unrecorded")
        with self.assertRaisesRegex(p.ProvenanceError, "unjournaled"):
            self.validate()
        with self.assertRaisesRegex(p.ProvenanceError, "Unjournaled"):
            self.begin()
        (self.u / "Assets/Game.cs").write_bytes(b"reference edited")
        with self.assertRaisesRegex(p.ProvenanceError, "reference input changed"):
            self.validate()

    def test_active_missing_finish_or_missing_receipt_blocked(self):
        self.begin()
        with self.assertRaisesRegex(p.ProvenanceError, "Unfinished"):
            self.validate()
        (self.e / "provenance/active-operation.json").unlink()
        with self.assertRaisesRegex(p.ProvenanceError, "active operation"):
            self.validate()

    def test_delete_last_noop_operation_is_detected(self):
        self.begin()
        self.finish()
        (self.e / "provenance/operations/op-1.json").unlink()
        with self.assertRaisesRegex(p.ProvenanceError, "head index"):
            self.validate()

    def test_missing_middle_chain_blocked(self):
        self.change()
        self.change("op-2", b"second derived bytes")
        (self.e / "provenance/operations/op-1.json").unlink()
        with self.assertRaisesRegex(p.ProvenanceError, "chain"):
            self.validate()

    def test_blob_and_operation_tampering_blocked(self):
        op = self.change()
        target = self.e / "provenance/blobs" / op["changes"][0]["after_sha256"]
        target.write_bytes(b"tamper")
        with self.assertRaisesRegex(p.ProvenanceError, "full bytes"):
            self.validate()

    def test_journal_hash_tampering_blocked(self):
        self.change()
        f = self.e / "provenance/operations/op-1.json"
        op = p._read(f)
        op["kind"] = "editor_exit"
        p._write(f, op)
        with self.assertRaisesRegex(p.ProvenanceError, "hash mismatch"):
            self.validate()

    def test_original_code_dependency_and_guid_changes_recorded_but_blocked(self):
        for path, payload in [("Assets/Game.cs", b"repaired game"), ("Packages/manifest.json", b"changed dependencies"),
                              ("Assets/Game.cs.meta", b"guid: " + b"b" * 32 + b"\n")]:
            with self.subTest(path=path):
                # Independent synthetic setups keep each rejected journal intact.
                self.setUp()
                self.begin()
                (self.r / path).write_bytes(payload)
                with self.assertRaisesRegex(p.ProvenanceError, "Blocked operation"):
                    self.finish()
                self.assertTrue((self.e / "provenance/operations/op-1.json").exists())
                with self.assertRaises(p.ProvenanceError):
                    self.validate()

    def test_original_meta_importer_data_is_recorded_and_reviewed_not_autoapproved(self):
        self.begin()
        f = self.r / "Assets/Game.cs.meta"
        f.write_bytes(f.read_bytes().replace(b"importer: old", b"importer: natural-derived"))
        self.finish()
        self.assertEqual(len(self.validate()["changes"]), 1)
        self.review()
        self.assertTrue(self.validate(True)["valid"])

    def test_duplicate_malformed_meta_guid_cannot_preserve_one_visible_original(self):
        self.begin()
        f = self.r / "Assets/Game.cs.meta"
        f.write_bytes(b"guid: malformed\n" + f.read_bytes())
        with self.assertRaisesRegex(p.ProvenanceError, "GUID changed"):
            self.finish()

    def test_new_code_only_in_adapter(self):
        self.begin()
        (self.r / "Assets/Repair.cs").write_bytes(b"new business code")
        with self.assertRaisesRegex(p.ProvenanceError, "outside audit adapter"):
            self.finish()

    def test_adapter_code_still_requires_write_set_review(self):
        self.begin()
        target = self.r / "Assets/__B1Audit/Adapter.cs"
        target.parent.mkdir()
        target.write_bytes(b"original interface test adapter")
        self.finish()
        self.review()
        self.assertTrue(self.validate(True)["valid"])

    def test_missing_source_wrong_source_and_missing_call_refs_rejected(self):
        variants = [{"source_refs": []}, {"source_refs": [{"path": "Assets/Game.cs", "sha256": "0" * 64}]},
                    {"source_refs": [{"path": "../outside.cs", "sha256": "0" * 64}]},
                    {"invocation": {"kind": "tool_call", "command": "exact", "refs": []}}]
        for updates in variants:
            with self.subTest(updates=updates):
                bad = copy.deepcopy(self.prov)
                bad.update(updates)
                with self.assertRaises(p.ProvenanceError):
                    self.begin(prov=bad)

    def test_actual_log_refs_can_be_added_at_finish_but_not_replace_actor(self):
        partial = copy.deepcopy(self.prov)
        del partial["evidence_refs"]
        self.begin(prov=partial)
        self.finish(prov=self.prov)
        self.assertTrue(self.validate()["valid"])

    def test_missing_actual_log_finish_is_recorded_and_blocks(self):
        partial = copy.deepcopy(self.prov)
        del partial["evidence_refs"]
        self.begin(prov=partial)
        with self.assertRaisesRegex(p.ProvenanceError, "evidence_refs"):
            self.finish()
        with self.assertRaises(p.ProvenanceError):
            self.validate()

    def test_evidence_path_escape_and_hash_tamper_rejected(self):
        bad = copy.deepcopy(self.prov)
        bad["evidence_refs"][0]["path"] = "../outside.log"
        with self.assertRaisesRegex(p.ProvenanceError, "relative path"):
            self.begin(prov=bad)
        self.change()
        (self.e / "tool.json").write_text("tampered original invocation")
        with self.assertRaisesRegex(p.ProvenanceError, "Evidence reference hash"):
            self.validate()

    def test_self_review_and_writer_review_rejected(self):
        self.change()
        self.review("actual-primary")
        with self.assertRaisesRegex(p.ProvenanceError, "self-review"):
            self.validate(True)
        doc = self.review()
        doc["reviewer_id"] = "actual-primary"
        # Even if manifest primary were a logical alias, the per-op writer must differ.
        doc["primary_id"] = "other"
        p._write(self.e / "provenance-review.json", doc)
        with self.assertRaises(p.ProvenanceError):
            self.validate(True)

    def test_nonprimary_operation_writer_cannot_approve_own_write_set(self):
        prov = copy.deepcopy(self.prov)
        prov["actor_id"] = "second-auditor"
        self.begin(prov=prov)
        (self.r / "Assets/Cell.mat").write_bytes(b"second auditor natural operation")
        self.finish()
        self.review("second-auditor")
        with self.assertRaisesRegex(p.ProvenanceError, "actor cannot self-review"):
            self.validate(True)

    def test_missing_full_byte_blob_and_head_index_blocked(self):
        op = self.change()
        (self.e / "provenance/blobs" / op["changes"][0]["before_sha256"]).unlink()
        with self.assertRaisesRegex(p.ProvenanceError, "full bytes"):
            self.validate()

    def test_stale_review_after_new_operation_is_rejected(self):
        self.change()
        self.review()
        self.begin("exit", "editor_exit")
        self.finish("exit")
        with self.assertRaisesRegex(p.ProvenanceError, "final provenance chain"):
            self.validate(True)

    def test_review_missing_path_duplicate_or_stale_final_hash_rejected(self):
        self.change()
        original = self.review()
        for changes in [{"decisions": []}, {"decisions": original["decisions"] * 2}, {"journal_head": "0" * 64},
                        {"final": {**original["final"], "runtime_digest": "0" * 64}}]:
            with self.subTest(changes=changes):
                doc = copy.deepcopy(original)
                doc.update(changes)
                p._write(self.e / "provenance-review.json", doc)
                with self.assertRaises(p.ProvenanceError):
                    self.validate(True)

    def test_reparse_rejected_before_traversal(self):
        original = Path.lstat
        target = (self.r / "Assets/Cell.mat").resolve()
        class Reparse:
            st_mode = 0
            st_file_attributes = 0x400
        def fake(path, *args, **kwargs):
            if path == target:
                return Reparse()
            return original(path, *args, **kwargs)
        with patch.object(Path, "lstat", fake):
            with self.assertRaisesRegex(p.ProvenanceError, "reparse"):
                self.validate()

    def test_nested_roots_and_unsafe_operation_id_rejected(self):
        with self.assertRaisesRegex(p.ProvenanceError, "disjoint"):
            p.initialize(self.u, self.u / "shadow", self.e, "a" * 64)
        with self.assertRaisesRegex(p.ProvenanceError, "operation kind/id"):
            self.begin("../write-outside")

    def test_manifest_cannot_be_replaced_or_reset(self):
        other = copy.deepcopy(self.m)
        other["candidate_hash"] = "c" * 64
        with self.assertRaisesRegex(p.ProvenanceError, "differs"):
            p.validate_workspace(self.u, self.r, self.e, other)
        with self.assertRaisesRegex(p.ProvenanceError, "never reset"):
            p.initialize(self.u, self.r, self.e, "b" * 64)

    def test_cli_failure_has_no_business_zero(self):
        from contextlib import redirect_stdout
        import io
        output = io.StringIO()
        args = ["validate", "--reference", str(self.u), "--runtime", str(self.r), "--evidence-root", str(self.e),
                "--manifest", str(self.e / "provenance/manifest.json"), "--require-review"]
        with redirect_stdout(output):
            self.assertEqual(p.main(args), 2)
        self.assertIsNone(json.loads(output.getvalue())["business_score"])


if __name__ == "__main__":
    unittest.main()
