#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focused fixtures for the v4 context, timing, and stats adapters."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import sjs_v4_context as context  # noqa: E402
import sjs_v4_stats as stats  # noqa: E402
import sjs_v4_time as timing  # noqa: E402
import instantiate_domain  # noqa: E402


class ContextAdapterTests(unittest.TestCase):
    def card(self):
        return {
            "id": "90",
            "shape": 90,
            "revision": 3,
            "status": "ready",
            "goal": "publish the next immutable layout snapshot",
            "current_state": "ready; 42 remains blocked until its production checkpoint is verified",
            "questions": ["confirm v5 source before apply"],
            "acceptance": ["manifest is immutable", "all four outputs have matching hashes"],
            "dependencies": [
                {
                    "id": "89",
                    "status": "accepted",
                    "version": 4,
                    "validity": "accepted output",
                    "outputs": [{"name": "PRODUCTION.json", "version": 4, "evidence": "SECRET"}],
                }
            ],
            "downstream_impact": "42 may not start until 90 and production checkpoint are accepted",
            "northstar": "published snapshots are immutable; production consumes the accepted version",
            "protected_constraints": ["never overwrite published v4", "do not treat lookdev as production truth"],
            "knowledge_tags": ["layout", "publish"],
            "source_refs": ["jobs/90.md", {"kind": "evidence", "path": "output/90/gate.json"}],
        }

    def test_required_contract_survives_soft_budget_and_output_is_pointer_only(self):
        card = self.card()
        result = context.build_context(
            card,
            knowledge=[{"tags": ["layout"], "summary": "use accepted layout", "evidence": "SECRET-KNOWLEDGE"}],
            budget_chars=160,
        )
        self.assertIn("publish the next immutable layout snapshot", result["text"])
        self.assertIn("manifest is immutable", result["text"])
        self.assertIn("never overwrite published v4", result["text"])
        self.assertIn("output/90/gate.json", result["text"])
        self.assertNotIn("SECRET-KNOWLEDGE", result["text"])
        self.assertNotIn("SECRET", json.dumps(result["dependencies"], ensure_ascii=False))
        self.assertFalse(result["output_evidence_read"])
        self.assertTrue(result["measurements"]["over_budget"])
        self.assertTrue(result["warnings"])

    def test_missing_selectors_warn_and_menu_has_no_history(self):
        result = context.build_context({"id": "x", "goal": "do x"}, knowledge=[])
        self.assertTrue(any("SUPPLEMENTARY_READ_REQUIRED" in warning for warning in result["warnings"]))
        menu = context.compact_menu([self.card()])
        self.assertFalse(menu["historical_scan"])
        self.assertFalse(menu["output_evidence_read"])
        self.assertEqual(menu["cards"][0]["card_id"], "90")

    def test_unclassified_card_knowledge_is_visible_with_supplementary_warning(self):
        result = context.build_context(
            {"id": "x", "goal": "do x", "knowledge": [{"text": "review this domain rule", "source_refs": ["NORMS.md#L1"]}]}
        )
        self.assertEqual(result["knowledge"][0]["match_status"], "unclassified")
        self.assertTrue(any("SUPPLEMENTARY_READ_REQUIRED" in warning for warning in result["warnings"]))

    def test_revision_summary_and_same_card_handoff(self):
        old = context.build_context(self.card())
        changed = self.card()
        changed["revision"] = 4
        changed["protected_constraints"].append("v4 files remain byte stable")
        current = context.build_context(changed, previous=old, since_revision=3)
        self.assertIn("v4 files remain byte stable", current["change_summary"]["newly_relevant_constraints"])
        handoff = context.build_handoff(changed, current, target_session="new-session")
        self.assertEqual(handoff["card_id"], "90")
        self.assertTrue(handoff["same_card"])
        self.assertFalse(handoff["new_card"])
        self.assertEqual(handoff["target_session"], "new-session")

    def test_engine_contract_wrapper_uses_flat_core_card(self):
        snapshot = {
            "revision": 5,
            "instance": {
                "instance_id": "instance-1",
                "domain_name": "demo",
                "route_key": "demo",
                "protocol_version": "4",
                "required_reads": ["ENV.md", "NORTH-STAR.md"],
            },
            "cards": [
                {
                    "id": "90",
                    "shape": 90,
                    "status": "ready",
                    "revision": 5,
                    "metadata": {"id": "90", "depends_on": [89], "faces": ["publish"]},
                    "sections": {"goal": "publish", "acceptance": "manifest", "progress": "ready"},
                    "path": "jobs/90.md",
                },
                {
                    "id": "89",
                    "shape": 89,
                    "status": "accepted",
                    "revision": 4,
                    "sections": {"artifacts": "PRODUCTION.json v4"},
                    "path": "jobs/89.md",
                },
            ],
            "cards_by_id": {},
        }
        snapshot["cards_by_id"] = {card["id"]: card for card in snapshot["cards"]}
        result = context.context("instance", snapshot, card_id="90", include_text=False)
        self.assertEqual(result["card_id"], "90")
        self.assertNotIn("text", result)
        self.assertEqual(result["dependencies"][0]["id"], "89")
        self.assertTrue(any("SUPPLEMENTARY_READ_REQUIRED" in warning for warning in result["warnings"]))

    def test_engine_sections_supply_knowledge_constraints_and_handoff_fields(self):
        card = {
            "id": "90",
            "shape": 90,
            "status": "doing",
            "metadata": {"id": "90", "knowledge_tags": ["publish"]},
            "sections": {
                "goal": "publish",
                "acceptance": "manifest",
                "progress": "in progress",
                "northstar": "accepted artifacts are immutable",
                "protected_constraints": ["never overwrite accepted output"],
                "knowledge": [
                    {
                        "stable_id": "norm-publish",
                        "tags": ["publish"],
                        "summary": "production artifact must carry its version",
                        "protected": True,
                        "source_refs": ["rules/publish.md#version"],
                    }
                ],
                "decisions": ["use the v4 manifest"],
                "next_steps": ["verify the manifest hash"],
                "evidence": ["evidence/90/manifest.json"],
            },
            "path": "jobs/90.md",
        }
        result = context.build_context(card, snapshot={"instance": {}})
        self.assertEqual(result["knowledge"][0]["summary"], "production artifact must carry its version")
        self.assertIn("never overwrite accepted output", result["protected_constraints"])
        self.assertEqual(result["northstar"], "accepted artifacts are immutable")
        handoff = context.build_handoff(card, result)
        self.assertEqual(handoff["decisions"], ["use the v4 manifest"])
        self.assertEqual(handoff["next_steps"], ["verify the manifest hash"])

    def test_real_entrypoint_reads_allowlisted_sources_and_shape_dependencies(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "output" / "90").mkdir(parents=True)
            (root / "output" / "90" / "secret.json").write_text("DO NOT READ", encoding="utf-8")
            (root / "ENV.md").write_text(
                "## Protected constraints\n- environment boundary is fixed\n", encoding="utf-8"
            )
            (root / "NORTH-STAR.md").write_text(
                "# Domain north star\n## Protected constraints\n- accepted output is immutable\n",
                encoding="utf-8",
            )
            (root / "NORMS.md").write_text(
                "## NORM-PUBLISH\n"
                "stable_id: NORM-PUBLISH\n"
                "tags: [publish]\n"
                "kind: norm\n"
                "protected: true\n"
                "summary: published artifacts must be immutable\n",
                encoding="utf-8",
            )
            (root / "FACTS.md").write_text(
                "## FACT-PUBLISH\n"
                "stable_id: FACT-PUBLISH\n"
                "tags: [publish]\n"
                "kind: fact\n"
                "summary: accepted version is v4\n",
                encoding="utf-8",
            )
            (root / "CURRENT.md").write_text("status: idle\n", encoding="utf-8")
            accepted = {
                "id": "89",
                "shape": 89,
                "status": "accepted",
                "metadata": {
                    "id": "89",
                    "artifact_version": "v4",
                    "artifact_valid": True,
                },
                "sections": {"artifacts": "accepted manifest v4"},
                "path": "jobs/89.md",
            }
            card = {
                "id": "90",
                "shape": 90,
                "status": "ready",
                "metadata": {
                    "id": "90",
                    "depends_on": [89],
                    "knowledge_tags": ["publish"],
                },
                "sections": {
                    "goal": "publish the immutable manifest",
                    "acceptance": "manifest hash matches",
                    "decisions": ["use accepted v4 output"],
                },
                "path": "jobs/90.md",
            }
            snapshot = {
                "revision": 7,
                "instance": {
                    "instance_id": "fixture",
                    "required_reads": ["ENV.md", "NORTH-STAR.md"],
                },
                "cards": [card, accepted],
                "cards_by_id": {"90": card, "89": accepted},
            }
            result = context.context(root, snapshot, card_id="90", max_chars=1600)
            self.assertEqual(result["dependencies"][0]["id"], "89")
            self.assertTrue(result["dependencies"][0]["accepted_outputs"][0]["validity"])
            self.assertIn("accepted output is immutable", result["northstar"])
            self.assertIn("NORM-PUBLISH", {item.get("stable_id") for item in result["knowledge"]})
            self.assertTrue(any(item["path"] == "NORMS.md" and item.get("sha256") for item in result["stable_sources"]))
            self.assertNotIn("DO NOT READ", json.dumps(result, ensure_ascii=False))
            self.assertIn("use accepted v4 output", result["decisions"])

    def test_menu_is_current_plus_ready_and_empty_context_is_menu_only(self):
        cards = [
            {"id": "accepted", "shape": 1, "status": "accepted"},
            {"id": "ready-blocked", "shape": 10, "status": "ready", "depends_on": [20]},
            {"id": "ready-open", "shape": 2, "status": "ready", "depends_on": []},
            {"id": "doing-history", "shape": 3, "status": "doing"},
            {"id": "current", "shape": 99, "status": "doing"},
        ]
        menu = context.compact_menu(cards, current_card_id="current")
        self.assertEqual([item["card_id"] for item in menu["cards"]], ["ready-open", "ready-blocked", "current"])
        blocked = next(item for item in menu["cards"] if item["card_id"] == "ready-blocked")
        self.assertFalse(blocked["eligible"])
        self.assertIn("missing:20", blocked["blocked_reason"])
        self.assertFalse(menu["historical_scan"])
        full = context.compact_menu(cards, include_history=True)
        self.assertTrue(full["historical_scan"])
        self.assertEqual(len(full["cards"]), len(cards))

        snapshot = {"revision": 0, "instance": {}, "cards": [], "cards_by_id": {}}
        idle = context.context("instance-with-no-cards", snapshot)
        self.assertIsNone(idle["card_id"])
        self.assertEqual(idle["menu"]["cards"], [])
        with self.assertRaises(ValueError):
            context.context("instance-with-no-cards", snapshot, card_id="missing")


class InstantiationAndCliTests(unittest.TestCase):
    def _instantiate(self, target: Path, *, version: str = "v4") -> Path:
        # The working tree is intentionally between skill baselines while the
        # v4 migration is being assembled.  The creator's production guard is
        # still exercised by the normal path; this fixture isolates generation
        # semantics from that repository-wide baseline gate.
        with mock.patch.object(instantiate_domain.check_skill_integrity, "check_manifest", return_value={"ok": True}):
            return instantiate_domain.instantiate(target, "fixture", instance_version=version)

    def test_default_v4_and_explicit_v3_creation_are_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            v4 = self._instantiate(root / "v4")
            manifest4 = json.loads((v4 / "INSTANCE.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest4["protocol_version"].split(".", 1)[0], "4")
            self.assertEqual(manifest4["instance_generation"], "v4")
            self.assertIn("NORTH-STAR.md", manifest4["required_reads"])
            self.assertTrue((v4 / "NORTH-STAR.md").is_file())
            self.assertIn("serial-job-session-card/v4", (v4 / "jobs" / "_template.md").read_text(encoding="utf-8"))

            v3 = self._instantiate(root / "v3", version="legacy")
            manifest3 = json.loads((v3 / "INSTANCE.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest3["protocol_version"], instantiate_domain.load_version())
            self.assertEqual(manifest3["instance_generation"], "v3")
            self.assertEqual(manifest3["required_reads"], list(instantiate_domain.V3_REQUIRED_READS))
            self.assertIn("id: {date}_{slug}", (v3 / "jobs" / "_template.md").read_text(encoding="utf-8"))

    def test_new_v4_instance_context_start_apply_check_and_empty_menu(self):
        import sjs_v4

        with tempfile.TemporaryDirectory() as temp:
            root = self._instantiate(Path(temp) / "v4")
            snapshot = sjs_v4.load_snapshot(root)
            self.assertEqual(snapshot["cards"], [])
            empty = context.context(root, snapshot)
            self.assertIsNone(empty["card_id"])
            self.assertEqual(empty["menu"]["cards"], [])

            jobs = root / "jobs"
            jobs.mkdir(exist_ok=True)
            card = {
                "metadata": {
                    "schema": "serial-job-session-card/v4",
                    "id": "90",
                    "shape": 90,
                    "status": "ready",
                    "depends_on": [],
                },
                "sections": {
                    "goal": "publish fixture",
                    "acceptance": "manifest is valid",
                    "progress": "ready",
                },
                "path": "jobs/90.md",
            }
            (jobs / "90.md").write_text(sjs_v4.render_card(card), encoding="utf-8")
            env = dict(os.environ)
            env["CODEX_HOME"] = str(root / "codex-home")
            context_run = subprocess.run(
                [sys.executable, str(HERE / "sjs.py"), "context", "--instance", str(root), "--card-id", "90"],
                cwd=str(HERE), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
            )
            self.assertEqual(context_run.returncode, 0, context_run.stdout + context_run.stderr)
            self.assertEqual(json.loads(context_run.stdout)["card_id"], "90")

            start = subprocess.run(
                [sys.executable, str(HERE / "sjs.py"), "start", "--instance", str(root), "--card-id", "90", "--thread-id", "fixture-thread", "--turn-id", "fixture-turn"],
                cwd=str(HERE), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
            )
            self.assertEqual(start.returncode, 0, start.stdout + start.stderr)
            start_result = json.loads(start.stdout)
            self.assertEqual(start_result.get("ok"), True)

            after_start = sjs_v4.load_snapshot(root)
            command = root / "apply.json"
            command.write_text(
                json.dumps({
                    "command_id": "fixture-apply",
                    "expected_revision": after_start["revision"],
                    "operation": {"op": "update", "card_id": "90", "changes": {"fixture_checked": True}},
                }),
                encoding="utf-8",
            )
            apply = subprocess.run(
                [sys.executable, str(HERE / "sjs.py"), "apply", "--instance", str(root), "--input", str(command), "--thread-id", "fixture-thread", "--turn-id", "fixture-turn"],
                cwd=str(HERE), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
            )
            self.assertEqual(apply.returncode, 0, apply.stdout + apply.stderr)
            check = subprocess.run(
                [sys.executable, str(HERE / "sjs.py"), "check", "--instance", str(root)],
                cwd=str(HERE), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
            )
            self.assertEqual(check.returncode, 0, check.stdout + check.stderr)
            self.assertTrue(json.loads(check.stdout)["ok"])


class TimingAdapterTests(unittest.TestCase):
    def records(self, complete=True):
        records = [
            {"thread_id": "thread-a", "turn_id": "turn-1", "event_type": "task_started", "started_at": "2026-01-01T10:00:00Z"},
            {"thread_id": "thread-a", "turn_id": "turn-1", "kind": "human_wait", "started_at": "2026-01-01T10:02:00Z", "completed_at": "2026-01-01T10:05:00Z"},
        ]
        if complete:
            records.append(
                {
                    "thread_id": "thread-a",
                    "turn_id": "turn-1",
                    "event_type": "task_complete",
                    "started_at": "2026-01-01T10:00:00Z",
                    "completed_at": "2026-01-01T10:10:00Z",
                    "duration_ms": 600_000,
                    "status": "accepted",
                }
            )
        return records

    def test_wait_is_subtracted_from_active_work(self):
        result = timing.resolve_timing(self.records(), thread_id="thread-a", turn_id="turn-1", host_started_at="2026-01-01T10:00:00Z")
        self.assertEqual(result["duration_ms"], 600_000)
        self.assertEqual(result["business_wait_ms"], 180_000)
        self.assertEqual(result["active_work_ms"], 420_000)
        self.assertTrue(result["count_for_calibration"])
        patch = timing.timing_patch(result)
        self.assertEqual(patch["ai_net"], 0.116667)
        self.assertEqual(patch["business_wait"], 0.05)
        self.assertNotIn("reasoning_hours", patch)

    def test_wait_overlap_is_clipped_and_outside_wait_is_ignored(self):
        records = [
            {"thread_id": "thread-a", "turn_id": "turn-1", "event_type": "task_started", "started_at": "2026-01-01T10:00:00Z"},
            {"thread_id": "thread-a", "turn_id": "turn-1", "event_type": "task_complete", "started_at": "2026-01-01T10:00:00Z", "completed_at": "2026-01-01T10:10:00Z", "duration_ms": 600_000},
            {"thread_id": "thread-a", "turn_id": "turn-1", "kind": "human_wait", "started_at": "2026-01-01T09:59:00Z", "completed_at": "2026-01-01T10:02:00Z"},
            {"thread_id": "thread-a", "turn_id": "turn-1", "kind": "human_wait", "started_at": "2026-01-01T10:09:00Z", "completed_at": "2026-01-01T10:11:00Z"},
            {"thread_id": "thread-a", "turn_id": "turn-1", "kind": "human_wait", "started_at": "2026-01-01T08:00:00Z", "completed_at": "2026-01-01T09:00:00Z"},
        ]
        result = timing.resolve_timing(records, thread_id="thread-a", turn_id="turn-1", host_started_at="2026-01-01T10:00:00Z")
        self.assertEqual(result["business_wait_ms"], 180_000)
        self.assertEqual(result["active_work_ms"], 420_000)

    def test_missing_completion_reconciles_next_invocation_without_synthesising_end(self):
        pending = timing.resolve_timing(self.records(False), thread_id="thread-a", turn_id="turn-1", host_started_at="2026-01-01T10:00:00Z")
        self.assertFalse(pending["complete"])
        self.assertIsNone(pending["completed_at"])
        self.assertFalse(pending["count_for_calibration"])
        reconciled = timing.reconcile_completed_turn(self.records(), thread_id="thread-a", turn_id="turn-1", host_started_at="2026-01-01T10:00:00Z")
        self.assertTrue(reconciled["complete"])

    def test_explicit_completion_without_reliable_start_is_not_calibration_sample(self):
        result = timing.resolve_timing(
            [{"thread_id": "thread-a", "turn_id": "turn-1", "event_type": "task_complete", "duration_ms": 10}],
            thread_id="thread-a",
            turn_id="turn-1",
        )
        self.assertTrue(result["complete"])
        self.assertFalse(result["count_for_calibration"])

    def test_completion_kind_wins_over_nested_item_duration_and_patch_keeps_raw_intervals(self):
        result = timing.resolve_timing(
            [
                {"thread_id": "thread-a", "turn_id": "turn-1", "kind": "task_complete", "started_at": "2026-01-01T10:00:00Z", "completed_at": "2026-01-01T10:10:00Z"},
                {"thread_id": "thread-a", "turn_id": "turn-1", "kind": "item_completed", "started_at": "2026-01-01T10:01:00Z", "completed_at": "2026-01-01T10:02:00Z", "duration_ms": 60_000},
            ],
            thread_id="thread-a",
            turn_id="turn-1",
            host_started_at="2026-01-01T10:00:00Z",
        )
        self.assertEqual(result["duration_ms"], 600_000)
        self.assertEqual(result["active_work_ms"], 600_000)
        patch = timing.timing_patch(result)
        self.assertEqual(patch["timing_source"], "v4_host_timing")
        self.assertEqual(patch["active_work_ms"], 600_000)
        self.assertEqual(patch["thread_id"], "thread-a")

    def test_user_event_mapping_is_a_start_bound_and_wait_aggregation_unions_overlap(self):
        result = timing.resolve_timing(
            [
                {"thread_id": "thread-a", "turn_id": "turn-1", "kind": "task_complete", "started_at": "2026-01-01T10:00:00Z", "completed_at": "2026-01-01T10:10:00Z"},
                {"thread_id": "thread-a", "turn_id": "turn-1", "kind": "human_wait", "started_at": "2026-01-01T10:02:00Z", "completed_at": "2026-01-01T10:05:00Z"},
            ],
            thread_id="thread-a",
            turn_id="turn-1",
            current_thread_user_event={"received_at": "2026-01-01T09:59:00Z"},
        )
        self.assertEqual(result["start_source"], "current_thread_user_event")
        aggregate = timing.aggregate_timing(
            [result],
            child_samples=[
                {"thread_id": "child", "turn_id": "child-turn", "active_work_ms": 30, "complete": True},
                {"thread_id": "thread-a", "turn_id": "turn-1", "active_work_ms": 999, "complete": True},
            ],
            parent_thread_id="thread-a",
        )
        self.assertEqual(aggregate["business_wait_ms"], 180_000)
        self.assertEqual(aggregate["child_effort_ms"], 30)

    def test_timing_requires_explicit_thread_and_turn_for_start(self):
        with self.assertRaises(ValueError):
            timing.time_adapter("instance", operation="start", turn_id="turn-1", host_records=[])
        with self.assertRaises(ValueError):
            timing.time_adapter("instance", operation="start", thread_id="thread-a", host_records=[])

    def test_jsonl_and_readonly_sqlite_fixtures(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rollout = root / "rollout-2026-01-01-thread-a.jsonl"
            lines = [
                {"type": "event_msg", "payload": {"type": "task_started", "thread_id": "thread-a", "turn_id": "turn-1", "started_at": 1767261600}},
                {"type": "event_msg", "payload": {"type": "task_complete", "thread_id": "thread-a", "turn_id": "turn-1", "started_at": 1767261600, "completed_at": 1767262200, "duration_ms": 600000}},
                {"source": "external-import", "type": "event_msg", "payload": {"type": "task_complete", "thread_id": "thread-a", "turn_id": "turn-1", "duration_ms": 999}},
            ]
            rollout.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
            parsed = timing.read_rollout_events(rollout, thread_id="thread-a", turn_id="turn-1")
            self.assertEqual(len(parsed), 2)

            db = root / "history with spaces.sqlite"
            con = sqlite3.connect(db)
            con.executescript(
                """
                create table thread_turns(thread_id text, turn_id text, status text, started_at integer, completed_at integer, duration_ms integer);
                create table thread_items(thread_id text, turn_id text, item_id text, created_at_ms integer, item_json text, item_type text);
                insert into thread_turns values ('thread-a','turn-1','accepted',1767261600000,1767262200000,600000);
                insert into thread_items values ('thread-a','turn-1','u1',1767261600000,'{"type":"UserMessage"}','UserMessage');
                """
            )
            con.commit()
            con.close()
            from_db = timing.read_thread_history(db, thread_id="thread-a", turn_id="turn-1")
            self.assertTrue(any(record["kind"] == "human_event" for record in from_db))
            self.assertTrue(any(record["kind"] == "thread_turn" for record in from_db))


class StatsAdapterTests(unittest.TestCase):
    def test_legacy_and_v4_are_grouped_separately(self):
        samples = [
            {"schema": "serial-job-session-time/v4", "job_id": "90", "active_work_ms": 3_600_000, "complete": True, "count_for_calibration": True},
            {"job_id": "90", "elapsed": 2.0},
            {"schema": "serial-job-session-time/v4", "job_id": "90", "active_work_ms": 10, "complete": False},
        ]
        report = stats.grouped_statistics(samples)
        self.assertIn("90|v4_host_timing", report["groups"])
        self.assertIn("90|legacy_card_elapsed", report["groups"])
        self.assertEqual(report["groups"]["90|v4_host_timing"]["eligible_count"], 1)

    def test_initial_estimate_stays_frozen_and_event_ids_are_idempotent(self):
        initial = stats.initial_estimate(2.0, estimate_source="human", card_id="90")
        forecast = stats.forecast_view(initial, forecast_hours=3.0, forecast_revision=2)
        self.assertEqual(forecast["initial_estimate_hours"], 2.0)
        self.assertEqual(forecast["forecast_hours"], 3.0)
        event = stats.library_event(instance_id="i", job_id="90", event_type="closed", closure_revision=7, patch_revision=0, timing={"schema": "serial-job-session-time/v4"})
        duplicate = dict(event)
        self.assertEqual(stats.project_library([event, duplicate])["count"], 1)
        self.assertNotEqual(event["closure_revision"], 1)
        correction = stats.library_event(instance_id="i", job_id="90", event_type="correction", closure_revision=7, patch_revision=1)
        projected = stats.project_library([event, correction])
        self.assertEqual(projected["latest"][0]["event_type"], "correction")

    def test_aggregate_subtracts_wait_and_same_revision_uses_append_order(self):
        sample = {
            "thread_id": "thread-a",
            "turn_id": "turn-1",
            "complete": True,
            "active_work_ms": 420_000,
            "duration_ms": 600_000,
            "business_wait_ms": 180_000,
            "evidence_records": [
                {"kind": "turn_complete", "started_at": "2026-01-01T00:00:00Z", "completed_at": "2026-01-01T00:10:00Z"},
                {"kind": "human_wait", "started_at": "2026-01-01T00:02:00Z", "completed_at": "2026-01-01T00:05:00Z"},
            ],
        }
        aggregate = timing.aggregate_timing([sample])
        self.assertEqual(aggregate["active_work_ms"], 420_000)
        self.assertEqual(aggregate["business_wait_ms"], 180_000)
        closed = stats.library_event(instance_id="i", job_id="90", event_type="closed", closure_revision=1, patch_revision=0)
        revoked = stats.library_event(instance_id="i", job_id="90", event_type="revoked", closure_revision=1, patch_revision=0)
        self.assertEqual(stats.project_library([closed, revoked])["latest"][0]["event_type"], "revoked")


class CoreCliTimingIntegrationTests(unittest.TestCase):
    def test_cli_start_host_completion_next_apply_and_stats_projection(self):
        # This is a disposable v4 instance.  The CLI performs the card
        # transaction and persists an incomplete timing sample on start.  The
        # next apply reads the same host thread/turn from a temporary
        # CODEX_HOME and appends the reconciled sample to that transaction.
        import sjs_v4

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "jobs").mkdir()
            (root / "INSTANCE.json").write_text(
                json.dumps({"schema": "serial-job-session-instance/v2", "instance_id": "integration", "protocol_version": "4", "domain_name": "fixture"}),
                encoding="utf-8",
            )
            card = {
                "metadata": {"schema": "serial-job-session-card/v4", "id": "90", "shape": 90, "status": "ready", "depends_on": []},
                "sections": {"goal": "publish", "acceptance": "manifest", "progress": "ready"},
                "path": "jobs/90.md",
            }
            (root / "jobs" / "90.md").write_text(sjs_v4.render_card(card), encoding="utf-8")
            codex_home = root / "codex-home"
            rollout_dir = codex_home / "sessions" / "2026" / "01" / "01"
            rollout_dir.mkdir(parents=True)
            env = dict(os.environ)
            env["CODEX_HOME"] = str(codex_home)
            start = subprocess.run(
                [sys.executable, str(HERE / "sjs.py"), "start", "--instance", str(root), "--card-id", "90", "--thread-id", "thread-a", "--turn-id", "turn-1"],
                cwd=str(HERE), env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(start.returncode, 0, start.stdout + start.stderr)
            self.assertEqual(sjs_v4.load_snapshot(root)["cards_by_id"]["90"]["status"], "doing")
            start_result = json.loads(start.stdout)
            self.assertFalse(start_result["timing"]["sample"]["complete"])
            self.assertTrue(sjs_v4.load_snapshot(root)["cards_by_id"]["90"]["metadata"]["timing_reconcile_next_invocation"])

            rollout = rollout_dir / "rollout-2026-01-01-thread-a.jsonl"
            rollout.write_text(
                "\n".join(
                    json.dumps(item)
                    for item in (
                        {"type": "event_msg", "payload": {"type": "task_started", "thread_id": "thread-a", "turn_id": "turn-1", "started_at": "2026-01-01T10:00:00Z"}},
                        {"type": "event_msg", "payload": {"type": "task_complete", "thread_id": "thread-a", "turn_id": "turn-1", "started_at": "2026-01-01T10:00:00Z", "completed_at": "2026-01-01T10:10:00Z", "duration_ms": 600000, "status": "accepted"}},
                    )
                ) + "\n",
                encoding="utf-8",
            )
            snapshot = sjs_v4.load_snapshot(root)
            command_path = root / "timing-apply.json"
            command_path.write_text(
                json.dumps({"command_id": "timing-1", "expected_revision": snapshot["revision"], "operation": {"op": "update", "card_id": "90", "changes": {"host_reconciled": True}}}),
                encoding="utf-8",
            )
            apply = subprocess.run(
                [sys.executable, str(HERE / "sjs.py"), "apply", "--instance", str(root), "--input", str(command_path), "--thread-id", "thread-a", "--turn-id", "turn-1"],
                cwd=str(HERE), env=env, capture_output=True, text=True, check=False,
            )
            self.assertEqual(apply.returncode, 0, apply.stdout + apply.stderr)
            apply_result = json.loads(apply.stdout)
            self.assertTrue(apply_result["timing"]["sample"]["complete"])
            updated = sjs_v4.load_snapshot(root)["cards_by_id"]["90"]
            self.assertTrue(updated["metadata"]["timing_complete"])
            self.assertEqual(updated["metadata"]["timing_last"]["active_work_ms"], 600_000)
            self.assertTrue(stats.eligible_sample(updated))
            self.assertEqual(stats.grouped_statistics([updated])["groups"]["90|v4_host_timing"]["eligible_count"], 1)


if __name__ == "__main__":
    unittest.main()
