#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focused semantic and transaction tests for the serial-job-session v4 core."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import sjs_v4


class CoreFixture(unittest.TestCase):
    def make_instance(self, cards):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "jobs").mkdir()
        (root / "INSTANCE.json").write_text(
            json.dumps({
                "schema": "serial-job-session-instance/v2",
                "instance_id": "fixture",
                "protocol_version": "4.0.0",
                "domain_name": "fixture",
            }),
            encoding="utf-8",
        )
        for card in cards:
            path = root / str(card.get("path") or "jobs/%s.md" % card["metadata"]["id"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(sjs_v4.render_card(card), encoding="utf-8")
        self.addCleanup(temp.cleanup)
        return root

    @staticmethod
    def card(card_id, shape, status="ready", **metadata):
        values = {"id": card_id, "shape": shape, "status": status, "depends_on": []}
        values.update(metadata)
        return {
            "metadata": values,
            "sections": {"goal": "goal-%s" % card_id, "acceptance": "evidence", "progress": status},
            "path": "jobs/%s.md" % card_id,
        }

    def apply(self, root, command_id, operation, revision=None, **extra):
        if revision is None:
            revision = sjs_v4.load_snapshot(root)["revision"]
        command = {"command_id": command_id, "expected_revision": revision, "operation": operation}
        command.update(extra)
        return sjs_v4.apply_command(root, command)


class CardSemanticsTests(CoreFixture):
    def test_flat_card_roundtrip_and_ready_has_no_started_timestamp(self):
        card = self.card("r", 7)
        text = sjs_v4.render_card(card)
        parsed = sjs_v4.parse_card_text(text, "jobs/r.md")
        self.assertEqual(parsed["metadata"]["schema"], sjs_v4.CARD_SCHEMA)
        self.assertEqual(parsed["shape"], 7)
        self.assertNotIn("started_at", parsed["metadata"])
        self.assertEqual(sjs_v4.render_card(parsed), text)

    def test_dependency_requires_actual_valid_artifact(self):
        dep = self.card("dep", 1, "accepted", artifact_valid=False, artifact_version="1", validated_artifact_version="1")
        target = self.card("target", 2, depends_on=["dep"])
        root = self.make_instance([dep, target])
        with self.assertRaisesRegex(sjs_v4.SJSV4Error, "DEPENDENCY_ARTIFACT_INVALID"):
            self.apply(root, "start-invalid", {"op": "start", "card_id": "target"})

        dep_path = root / "jobs/dep.md"
        dep_card = sjs_v4.parse_card(dep_path)
        dep_card["metadata"]["artifact_valid"] = True
        dep_card["metadata"]["pending_verification"] = False
        dep_card["artifact_valid"] = True
        dep_card["pending_verification"] = False
        dep_path.write_text(sjs_v4.render_card(dep_card), encoding="utf-8")
        result = self.apply(root, "start-valid", {"op": "start", "card_id": "target"}, revision=sjs_v4.load_snapshot(root)["revision"])
        self.assertTrue(result["ok"])
        self.assertEqual(sjs_v4.load_snapshot(root)["cards_by_id"]["target"]["status"], "doing")

    def test_direction_review_requires_advanced_human_decision_and_fingerprint(self):
        review = self.card("review", 90, direction_review=True, review_input_fingerprint="fp-1")
        root = self.make_instance([review])
        # The review card itself can be started while its analysis is being
        # prepared.  The advanced-model + human + matching-input gate applies
        # when the review is accepted and to its downstream work.
        self.assertTrue(self.apply(root, "review-start", {"op": "start", "card_id": "review"})["ok"])
        self.apply(
            root,
            "review-decision",
            {
                "op": "record_decision",
                "card_id": "review",
                "human_decision": {"source": "human discussion", "approved": True, "model": "advanced", "input_fingerprint": "wrong"},
            },
        )
        with self.assertRaisesRegex(sjs_v4.SJSV4Error, "DECISION_REVIEW_STALE"):
            self.apply(root, "review-await", {"op": "await", "card_id": "review"})
            self.apply(root, "review-stale", {"op": "accept", "card_id": "review"})

        root2 = self.make_instance([self.card("review", 90, direction_review=True, review_input_fingerprint="fp-1")])
        self.apply(
            root2,
            "review-decision-ok",
            {
                "op": "record_decision",
                "card_id": "review",
                "human_decision": {"source": "human discussion", "approved": True, "model": "advanced", "input_fingerprint": "fp-1"},
            },
        )
        self.assertTrue(self.apply(root2, "review-start-ok", {"op": "start", "card_id": "review"})["ok"])

    def test_questions_feedback_and_patch_preserve_acceptance_history(self):
        root = self.make_instance([self.card("a", 1)])
        self.apply(root, "start", {"op": "start", "card_id": "a"}, at="2026-09-20T01:00:00+08:00")
        self.apply(root, "question", {"op": "question", "card_id": "a", "text": "need input"}, at="2026-09-20T01:01:00+08:00")
        questioned = sjs_v4.load_snapshot(root)["cards_by_id"]["a"]
        self.assertEqual(questioned["status"], "doing")
        self.assertTrue(set(questioned["sections"]).issubset(set(sjs_v4.SECTION_ORDER)))
        self.assertIn("### Questions", questioned["sections"]["progress"])
        self.apply(root, "await", {"op": "await", "card_id": "a"}, at="2026-09-20T01:02:00+08:00")
        self.apply(root, "feedback", {"op": "feedback", "card_id": "a", "text": "small fix"}, at="2026-09-20T01:03:00+08:00")
        feedback = sjs_v4.load_snapshot(root)["cards_by_id"]["a"]
        self.assertEqual(feedback["status"], "awaiting_acceptance")
        self.assertTrue(set(feedback["sections"]).issubset(set(sjs_v4.SECTION_ORDER)))
        self.assertIn("### Feedback", feedback["sections"]["artifacts"])
        self.apply(root, "accept", {"op": "accept", "card_id": "a"}, at="2026-09-20T01:04:00+08:00")
        accepted = sjs_v4.load_snapshot(root)["cards_by_id"]["a"]
        accepted_at, closed_at = accepted["accepted_at"], accepted["closed_at"]
        self.apply(root, "patch", {"op": "patch", "card_id": "a", "summary": "evidence correction", "affected_artifacts": ["out/a"], "knowledge_changes": []}, at="2026-09-20T01:05:00+08:00")
        patched = sjs_v4.load_snapshot(root)["cards_by_id"]["a"]
        self.assertEqual(patched["status"], "accepted")
        self.assertEqual(patched["accepted_at"], accepted_at)
        self.assertEqual(patched["closed_at"], closed_at)
        self.assertTrue(patched["pending_verification"])
        self.assertFalse(patched["artifact_valid"])
        self.assertTrue(set(patched["sections"]).issubset(set(sjs_v4.SECTION_ORDER)))
        self.assertIn("### Patches", patched["sections"]["closure"])

    def test_estimate_is_immutable_and_major_update_requires_decision(self):
        root = self.make_instance([self.card("a", 1)])
        self.apply(root, "estimate-set", {"op": "update", "card_id": "a", "changes": {"estimate_total": 2.0}})
        with self.assertRaisesRegex(sjs_v4.SJSV4Error, "ESTIMATE_IMMUTABLE"):
            self.apply(root, "estimate-change", {"op": "update", "card_id": "a", "changes": {"estimate_total": 3.0}})

        root2 = self.make_instance([self.card("a", 1)])
        with self.assertRaisesRegex(sjs_v4.SJSV4Error, "HUMAN_DECISION_REQUIRED"):
            self.apply(root2, "goal-direct", {"op": "update", "card_id": "a", "changes": {"goal": "new goal"}})
        self.apply(root2, "goal-decision", {"op": "record_decision", "card_id": "a", "human_decision": {"source": "human discussion", "approved": True}, "changes": {"goal": "new goal"}})
        self.assertIn("new goal", sjs_v4.load_snapshot(root2)["cards_by_id"]["a"]["sections"]["goal"])

    def test_create_and_path_traversal_guard(self):
        root = self.make_instance([])
        self.assertTrue(self.apply(root, "create", {"op": "create", "card_id": "new", "shape": 4, "path": "jobs/new.md", "metadata": {"id": "new", "shape": 4, "status": "ready", "depends_on": []}, "sections": {"goal": "new"}})["ok"])
        self.assertIn("new", sjs_v4.load_snapshot(root)["cards_by_id"])
        with self.assertRaisesRegex(sjs_v4.SJSV4Error, "PATH_TRAVERSAL"):
            self.apply(root, "escape", {"op": "create", "card_id": "escape", "shape": 5, "path": "../escape.md", "metadata": {"id": "escape", "shape": 5, "status": "ready"}, "sections": {"goal": "escape"}})

    def test_norm_replace_retire_is_unique_across_cards_and_operation_order(self):
        root = self.make_instance([self.card("a", 1), self.card("b", 2)])
        change_a = {"op": "update", "card_id": "a", "knowledge_changes": [{"id": "N1", "kind": "norm", "action": "add", "evidence": "e-a", "text": "first"}]}
        self.apply(root, "norm-a", change_a)
        change_b = {"op": "update", "card_id": "b", "knowledge_changes": [{"id": "N1", "kind": "norm", "action": "replace", "evidence": "e-b", "text": "replacement"}]}
        self.apply(root, "norm-b", change_b)
        cards = sjs_v4.load_snapshot(root)["cards_by_id"]
        self.assertFalse(cards["a"]["metadata"]["norms"][0]["active"])
        self.assertTrue(cards["b"]["metadata"]["norms"][0]["active"])
        self.apply(root, "norm-retire", {"op": "update", "card_id": "b", "knowledge_changes": [{"id": "N1", "kind": "norm", "action": "deprecate", "evidence": "e-retire", "text": "retired"}]})
        cards = sjs_v4.load_snapshot(root)["cards_by_id"]
        self.assertFalse(any(item.get("active") is not False for item in cards["a"]["metadata"].get("norms", [])))
        self.assertFalse(any(item.get("active") is not False for item in cards["b"]["metadata"].get("norms", [])))

        root2 = self.make_instance([self.card("a", 1), self.card("b", 2)])
        result = self.apply(root2, "norm-batch", {"operations": [
            {"op": "update", "card_id": "a", "knowledge_changes": [{"id": "N1", "kind": "norm", "action": "add", "evidence": "e-a", "text": "first"}]},
            {"op": "update", "card_id": "b", "knowledge_changes": [{"id": "N1", "kind": "norm", "action": "add", "evidence": "e-b", "text": "last"}]},
        ]})
        self.assertTrue(result["ok"])
        cards = sjs_v4.load_snapshot(root2)["cards_by_id"]
        self.assertFalse(cards["a"]["metadata"]["norms"][0]["active"])
        self.assertTrue(cards["b"]["metadata"]["norms"][0]["active"])


class TransactionTests(CoreFixture):
    def test_fault_recovery_backup_coverage_and_idempotency(self):
        root = self.make_instance([self.card("a", 1)])
        command = {"command_id": "fault", "expected_revision": 0, "operation": {"op": "start", "card_id": "a"}}
        old_fault = os.environ.get("SJS_V4_FAULT_INJECT")
        os.environ["SJS_V4_FAULT_INJECT"] = "after_backup"
        try:
            with self.assertRaisesRegex(sjs_v4.SJSV4Error, "APPLY_FAILED"):
                sjs_v4.apply_command(root, command)
        finally:
            if old_fault is None:
                os.environ.pop("SJS_V4_FAULT_INJECT", None)
            else:
                os.environ["SJS_V4_FAULT_INJECT"] = old_fault
        journals = list((root / ".serial-job-session" / "private" / "sjs-v4" / "fixture" / "transactions").glob("fault/journal.json"))
        self.assertEqual(len(journals), 1)
        journal = json.loads(journals[0].read_text(encoding="utf-8"))
        self.assertEqual({item["path"] for item in journal["targets"]}, {"jobs/a.md", "JOB-SHAPES.md", "CURRENT.md", "LOG.md", "FACTS.md"})
        self.assertTrue(all(item.get("backup") for item in journal["targets"]))
        self.assertTrue(sjs_v4.recover_transaction(root, command_id="fault", action="resume")["ok"])
        replay = sjs_v4.apply_command(root, command)
        self.assertTrue(replay["replayed"])
        altered = dict(command)
        altered["operation"] = {"op": "question", "card_id": "a", "text": "different"}
        with self.assertRaisesRegex(sjs_v4.SJSV4Error, "COMMAND_ID_REUSE"):
            sjs_v4.apply_command(root, altered)

    def test_recovery_refuses_external_target_change_and_rollback_restores(self):
        root = self.make_instance([self.card("a", 1)])
        command = {"command_id": "drift", "expected_revision": 0, "operation": {"op": "start", "card_id": "a"}}
        old_fault = os.environ.get("SJS_V4_FAULT_INJECT")
        os.environ["SJS_V4_FAULT_INJECT"] = "after_backup"
        try:
            with self.assertRaises(sjs_v4.SJSV4Error):
                sjs_v4.apply_command(root, command)
        finally:
            if old_fault is None:
                os.environ.pop("SJS_V4_FAULT_INJECT", None)
            else:
                os.environ["SJS_V4_FAULT_INJECT"] = old_fault
        (root / "jobs/a.md").write_text("external edit\n", encoding="utf-8")
        with self.assertRaisesRegex(sjs_v4.SJSV4Error, "DRIFT_DETECTED_DURING_RECOVERY"):
            sjs_v4.recover_transaction(root, command_id="drift", action="resume")

        root2 = self.make_instance([self.card("a", 1)])
        old_fault = os.environ.get("SJS_V4_FAULT_INJECT")
        os.environ["SJS_V4_FAULT_INJECT"] = "after_backup"
        try:
            with self.assertRaises(sjs_v4.SJSV4Error):
                sjs_v4.apply_command(root2, {"command_id": "rollback", "expected_revision": 0, "operation": {"op": "start", "card_id": "a"}})
        finally:
            if old_fault is None:
                os.environ.pop("SJS_V4_FAULT_INJECT", None)
            else:
                os.environ["SJS_V4_FAULT_INJECT"] = old_fault
        self.assertTrue(sjs_v4.recover_transaction(root2, command_id="rollback", action="rollback")["phase"] == "rolled_back")
        self.assertEqual(sjs_v4.load_snapshot(root2)["cards_by_id"]["a"]["status"], "ready")

    def test_migration_file_operations_share_transaction_and_generated_views(self):
        root = self.make_instance([self.card("a", 1)])
        source_hash = sjs_v4.load_snapshot(root)["files"]["jobs/a.md"]["sha256"]
        command = {
            "command_id": "migration",
            "expected_revision": 0,
            "migration_schema": "serial-job-session-v4-migration/v1",
            "file_operations": [
                {"op": "replace_card", "path": "jobs/a.md", "card_id": "a", "source_sha256": source_hash, "metadata": {"id": "a", "shape": 1, "status": "ready", "depends_on": []}, "sections": {"goal": "migrated"}},
                {"op": "write_file", "path": "knowledge/archive/a.md", "content": "legacy"},
            ],
        }
        result = sjs_v4.apply_migration(root, command)
        self.assertTrue(result["ok"])
        self.assertEqual(sjs_v4.load_snapshot(root)["cards_by_id"]["a"]["sections"]["goal"], "migrated")
        self.assertEqual(sjs_v4.load_snapshot(root)["cards_by_id"]["a"]["revision"], 1)
        self.assertTrue((root / "knowledge/archive/a.md").is_file())
        self.assertEqual(sjs_v4.apply_migration(root, command)["replayed"], True)


if __name__ == "__main__":
    unittest.main()
