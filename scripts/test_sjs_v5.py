"""Observable v5 contracts: persistence, governance, nesting and context."""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from pathlib import Path
from unittest import mock

import sjs_config as config
import sjs_context as context
import sjs_store as store


def configuration(mode="full_ai", **extra):
    value = {"goal": "Deliver the agreed feature", "route_key": "demo", "authorization": {"paths": ["."], "tools": ["shell", "unity"]},
             "policy": {"mode": mode}, "modules": {"estimates": False, "feishu": False}}
    if mode == "conditional":
        value["policy"].update(risk_ceiling="medium", thresholds={"feasibility": 85, "direction": 90, "acceptance": 95}, confirmation="user-message:1")
    return config.merge(value, extra)


def create(task_id="a", **extra):
    return {"op": "create", "task_id": task_id, "contract": {"goal": "Fix " + task_id, "checks": [{"id": "works", "description": "Observed behavior matches requirement"}]}, **extra}


def assessment(phase="feasibility", score=99, risk="low", **extra):
    return {"id": uuid.uuid4().hex, "score": score, "risk": risk, "conclusion": "pass", "evidence": ["artifacts/proof.json"],
            "unknowns": [], "rationale": "Observed relevant evidence", "recovery_cost": "revert own isolated change", **extra}


class WorkflowCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / "flow"
        # Source edits legitimately change the maintenance manifest while tests
        # are being developed. Release integrity is separately checked by CI.
        self.integrity = mock.patch("check_skill_integrity.check_manifest", return_value={"ok": True})
        self.integrity.start()
        self.addCleanup(self.integrity.stop)

    def init(self, mode="full_ai", **extra):
        return store.initialize(self.root, configuration(mode, **extra))

    def state(self):
        return store.load_snapshot(self.root)

    def command(self, *ops, human=False, command_id=None, revision=None):
        return {"command_id": command_id or uuid.uuid4().hex, "expected_revision": self.state()["revision"] if revision is None else revision,
                "actor": {"id": "user" if human else "controller", "kind": "human" if human else "agent"}, "operations": list(ops)}

    def apply(self, *ops, human=False, **kwargs):
        return store.apply_command(self.root, self.command(*ops, human=human, **kwargs))

    def assess(self, task_id="a", phase="feasibility", **extra):
        data = assessment(phase, **extra)
        if phase == "acceptance":
            task = self.state()["tasks"][task_id]
            data.update(candidate_version=task["candidate"]["version"], contract_version=task["contract_version"])
            data.setdefault("by", {"id": "reviewer", "kind": "agent"})
            data.setdefault("checks", {"works": {"status": "passed", "evidence": ["artifacts/proof.json"]}})
        return self.apply({"op": "assess", "task_id": task_id, "phase": phase, "assessment": data})

    def start(self, task_id="a", human=False):
        return self.apply({"op": "start", "task_id": task_id}, human=human)

    def report(self, task_id="a", version="v1"):
        return self.apply({"op": "report", "task_id": task_id, "candidate_version": version,
                           "by": {"id": "worker", "kind": "agent"}, "evidence": ["artifacts/proof.json"]})

    def finish(self, task_id="a", human=False):
        self.report(task_id)
        self.assess(task_id, "acceptance")
        return self.apply({"op": "accept", "task_id": task_id}, human=human)

    def error(self, code, callback):
        with self.assertRaises(config.SJSError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)


class InitializationTests(WorkflowCase):
    def test_choices_are_required_and_no_files_on_incomplete_or_dry_run(self):
        raw = configuration()
        raw.pop("policy")
        raw["modules"] = {}
        result = store.initialize(self.root, raw)
        self.assertEqual(result["missing"], ["policy.mode", "modules.estimates", "modules.feishu"])
        self.assertFalse(self.root.exists())
        self.assertNotIn("policy.mode", result["suggestions"])
        self.assertTrue(store.initialize(self.root, configuration(), dry_run=True)["ok"])
        self.assertFalse(self.root.exists())

    def test_only_four_files_and_route_index(self):
        self.init()
        self.assertEqual(sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*") if p.is_file()),
                         ["records/direction-000001.jsonl", "records/tasks-000001.jsonl", "run.json", "state.json"])
        self.assertEqual(store.resolve_route(" D E M O ", self.base), self.root)

    def test_conditional_parameters_must_be_explicit(self):
        raw = configuration("manual")
        raw["policy"]["mode"] = "conditional"
        result = store.initialize(self.root, raw)
        self.assertIn("policy.thresholds.acceptance", result["missing"])
        self.assertIn("policy.confirmation", result["missing"])

    def test_zero_false_and_empty_arrays_survive(self):
        self.init(policy={"max_retries": 0, "independent_review": False}, authorization={"tools": []}, note="  ")
        cfg = self.state()["config"]
        self.assertEqual(cfg["policy"]["max_retries"], 0)
        self.assertIs(cfg["policy"]["independent_review"], False)
        self.assertEqual(cfg["authorization"]["tools"], [])
        self.assertNotIn("note", json.loads((self.root / "run.json").read_text()))

    def test_legacy_instance_is_explicitly_rejected(self):
        self.root.mkdir()
        (self.root / "INSTANCE.json").write_text('{"protocol_version":"4.0.0"}')
        self.error("UNSUPPORTED_PROTOCOL", self.state)

    def test_duplicate_route_does_not_create_an_instance(self):
        self.init()
        other = self.base / "other"
        self.error("ROUTE_KEY_CONFLICT", lambda: store.initialize(other, configuration()))
        self.assertFalse(other.exists())

    def test_creation_config_is_immutable(self):
        self.init()
        run = store.read_json(self.root / "run.json")
        run["goal"] = "changed"
        store.atomic_json(self.root / "run.json", run)
        self.error("RUN_CONFIG_CHANGED", self.state)


class TemplateIdentityTests(unittest.TestCase):
    def test_method_identity_ignores_version_but_integrity_covers_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "VERSION").write_text("5.2.0", encoding="utf-8")
            manifest = {"schema": "serial-job-session-integrity/v1", "protocol_version": "5.2.0",
                        "files": {"SKILL.md": "skill-hash", "VERSION": "version-hash-1"}}
            (root / "skill-integrity.json").write_text(json.dumps(manifest), encoding="utf-8")
            with mock.patch.object(config, "ROOT", root):
                first = config.template_identity()
                (root / "VERSION").write_text("5.2.1", encoding="utf-8")
                manifest["protocol_version"] = "5.2.1"
                manifest["files"]["VERSION"] = "version-hash-2"
                (root / "skill-integrity.json").write_text(json.dumps(manifest), encoding="utf-8")
                second = config.template_identity()
        self.assertEqual(first["version"], "5.2.0")
        self.assertEqual(second["version"], "5.2.1")
        self.assertEqual(first["method"], second["method"])
        self.assertNotEqual(first["integrity"], second["integrity"])
        self.assertTrue(config.same_template(first, second))

    def test_version_only_rebuild_keeps_method_identity(self):
        import check_skill_integrity
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            version = root / "VERSION"
            skill = root / "SKILL.md"
            version.write_text("5.2.0", encoding="utf-8")
            skill.write_text("# skill\n", encoding="utf-8")
            manifest_path = root / "skill-integrity.json"
            with mock.patch.multiple(check_skill_integrity, SKILL_ROOT=root,
                                     MANIFEST_PATH=manifest_path, VERSION_PATH=version):
                check_skill_integrity.update_manifest()
                with mock.patch.object(config, "ROOT", root):
                    first = config.template_identity()
                version.write_text("5.2.1", encoding="utf-8")
                check_skill_integrity.update_manifest()
                with mock.patch.object(config, "ROOT", root):
                    second = config.template_identity()
        self.assertEqual(first["method"], second["method"])
        self.assertNotEqual(first["integrity"], second["integrity"])
        self.assertTrue(config.same_template(first, second))

    def test_installation_integrity_still_covers_version(self):
        import check_skill_integrity
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            version = root / "VERSION"
            skill = root / "SKILL.md"
            version.write_text("5.2.0", encoding="utf-8")
            skill.write_text("# skill\n", encoding="utf-8")
            manifest_path = root / "skill-integrity.json"
            manifest = {"schema": "serial-job-session-integrity/v1", "protocol_version": "5.2.0",
                        "files": {"SKILL.md": check_skill_integrity.sha256(skill),
                                  "VERSION": check_skill_integrity.sha256(version)}}
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with mock.patch.multiple(check_skill_integrity, SKILL_ROOT=root,
                                     MANIFEST_PATH=manifest_path, VERSION_PATH=version):
                self.assertTrue(check_skill_integrity.check_manifest()["ok"])
                version.write_text("5.2.1", encoding="utf-8")
                result = check_skill_integrity.check_manifest()
        self.assertFalse(result["ok"])
        self.assertIn("VERSION", result["modified"])

    def test_pre_5_2_identity_falls_back_to_full_integrity(self):
        old = {"version": "5.0.0", "integrity": "full-old"}
        new = {"version": "5.2.0", "integrity": "full-new", "method": "method-new"}
        self.assertFalse(config.same_template(old, new))
        self.assertTrue(config.same_template(old, dict(old)))


class TemplateRebindTests(WorkflowCase):
    def test_version_only_change_does_not_require_rebind(self):
        self.init()
        stored = self.state()["config"]["template"]
        version_only = {**stored, "version": "9.9.9", "integrity": "full-new"}
        with mock.patch.object(store, "template_identity", return_value=version_only):
            self.assertEqual(self.state()["revision"], 1)
            self.apply(create("b"))
            checked = store.check_instance(self.root)
        self.assertNotIn("TEMPLATE_CHANGED", checked["errors"])

    def test_method_change_requires_rebind(self):
        self.init()
        stored = self.state()["config"]["template"]
        changed = {**stored, "version": "9.9.9", "integrity": "full-new", "method": "method-changed"}
        with mock.patch.object(store, "template_identity", return_value=changed):
            self.error("TEMPLATE_CHANGED", self.state)
            state = store.load_snapshot(self.root, verify_template=False)
            command = {"command_id": "rebind", "expected_revision": state["revision"],
                       "actor": {"id": "user", "kind": "human"},
                       "operations": [{"op": "rebind_template", "template": changed,
                                       "reason": "reviewed new shared methods"}]}
            store.apply_command(self.root, command)
            self.assertEqual(self.state()["config"]["template"], changed)
            self.assertEqual(store.rebuild(self.root, persist=False), self.state())


class PersistenceTests(WorkflowCase):
    def test_unrecorded_method_changes_block_writes(self):
        self.init()
        before = self.state()["revision"]
        with mock.patch("check_skill_integrity.check_manifest", return_value={"ok": False, "modified": ["SKILL.md"]}):
            self.error("TEMPLATE_INTEGRITY_FAILED", lambda: self.apply(create()))
        self.assertEqual(before, self.state()["revision"])

    def test_commands_are_idempotent_and_payload_collision_is_rejected(self):
        self.init()
        command = self.command(create(), command_id="same")
        first = store.apply_command(self.root, command)
        raw = (self.root / "records/tasks-000001.jsonl").read_bytes()
        again = store.apply_command(self.root, command)
        self.assertTrue(again["duplicate"])
        self.assertEqual(first["revision"], again["revision"])
        self.assertEqual(raw, (self.root / "records/tasks-000001.jsonl").read_bytes())
        command["operations"][0]["title"] = "different"
        self.error("COMMAND_ID_COLLISION", lambda: store.apply_command(self.root, command))

    def test_compare_and_swap_and_concurrent_writers(self):
        self.init()
        self.apply(create())
        self.error("REVISION_CONFLICT", lambda: self.apply(create("b"), revision=1))
        outcomes = []
        barrier = threading.Barrier(2)
        def writer(key):
            command = self.command(create(key))
            barrier.wait()
            try:
                store.apply_command(self.root, command)
                outcomes.append("ok")
            except config.SJSError as exc:
                outcomes.append(exc.code)
        threads = [threading.Thread(target=writer, args=(k,)) for k in ("b", "c")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(outcomes.count("ok"), 1)
        self.assertTrue(set(outcomes) <= {"ok", "WRITER_BUSY", "REVISION_CONFLICT"})

    def test_durable_event_survives_snapshot_write_failure(self):
        self.init()
        command = self.command(create())
        with mock.patch.object(store, "_save_state", side_effect=OSError("simulated crash")):
            with self.assertRaises(OSError):
                store.apply_command(self.root, command)
        self.assertIn("a", self.state()["tasks"])
        self.assertTrue(store.apply_command(self.root, command)["duplicate"])
        store.rebuild(self.root)
        self.assertTrue(store.check_instance(self.root)["ok"])

    def test_corrupt_and_deleted_snapshots_rebuild_from_both_streams(self):
        self.init()
        self.apply(create())
        data = assessment("direction", route="next", candidates=["next", "defer"])
        self.apply({"op": "assess_direction", "assessment": data})
        self.apply({"op": "apply_direction", "assessment_id": data["id"]})
        expected = self.state()
        (self.root / "state.json").write_text("bad json")
        self.assertEqual(expected, self.state())
        (self.root / "state.json").unlink()
        self.assertEqual(expected, self.state())

    def test_torn_tail_requires_explicit_recovery_and_preserves_committed_bytes(self):
        self.init()
        self.apply(create())
        path = self.root / "records/tasks-000001.jsonl"
        before = path.read_bytes()
        with path.open("ab") as f:
            f.write(b'{"incomplete":')
        self.error("TORN_TAIL", self.state)
        preview = store.recover(self.root, dry_run=True)
        self.assertEqual(len(preview["repairs"]), 1)
        self.assertGreater(path.stat().st_size, len(before))
        result = store.recover(self.root, dry_run=False)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(Path(result["fragment_backups"][0]).read_bytes(), b'{"incomplete":')
        self.assertIn("a", self.state()["tasks"])

    def test_active_lock_cannot_be_broken(self):
        self.init()
        with store.file_lock(store.private_dir(self.root) / "writer.lock"):
            self.error("WRITER_BUSY", lambda: self.apply(create()))
            self.error("LOCK_OWNER_ALIVE", lambda: store.recover(self.root, dry_run=False, break_stale=True))

    def test_validation_failure_does_not_append_partial_batch(self):
        self.init()
        before = (self.root / "records/tasks-000001.jsonl").read_bytes()
        self.error("DEPENDENCY_NOT_FOUND", lambda: self.apply(create(), create("b", depends_on=["missing"])))
        self.assertEqual(before, (self.root / "records/tasks-000001.jsonl").read_bytes())

    def test_missing_middle_segment_is_not_silently_ignored(self):
        self.init(rotation={"tasks": 1})
        for key in ("one", "two"):
            self.apply(create(key), {"op": "cancel", "task_id": key, "reason": "closed"})
        (self.root / "records/tasks-000002.jsonl").unlink()
        self.error("SEGMENT_SEQUENCE_GAP", lambda: store.check_instance(self.root))

    def test_rotation_and_old_task_patch_are_append_only(self):
        self.init(rotation={"tasks": 1})
        self.apply(create())
        self.assess()
        self.start()
        self.finish()
        old = self.root / "records/tasks-000001.jsonl"
        before = old.read_bytes()
        self.apply({"op": "patch", "task_id": "a", "summary": "Additional evidence", "evidence": ["artifacts/extra.json"]})
        self.assertEqual(before, old.read_bytes())
        self.assertTrue((self.root / "records/tasks-000002.jsonl").stat().st_size)
        self.assertEqual(store.rebuild(self.root, persist=False), self.state())


class GovernanceTests(WorkflowCase):
    def test_manual_start_and_accept_require_real_human_decisions(self):
        self.init("manual")
        self.apply(create())
        self.assertFalse(self.start()["outcomes"][0]["decision"]["allowed"])
        self.assertEqual(self.state()["tasks"]["a"]["status"], "ready")
        self.start(human=True)
        self.assertFalse(self.finish()["outcomes"][0]["decision"]["allowed"])
        self.apply({"op": "accept", "task_id": "a"}, human=True)
        self.assertEqual(self.state()["tasks"]["a"]["status"], "accepted")

    def test_conditional_threshold_boundary_and_risk_are_separate(self):
        self.init("conditional")
        self.apply(create())
        self.assess(score=84.9)
        self.assertEqual(self.start()["outcomes"][0]["decision"]["reason"], "below_threshold")
        self.assess(score=100, risk="high")
        self.assertEqual(self.start()["outcomes"][0]["decision"]["reason"], "risk_exceeded")
        self.assess(score=85, risk="medium")
        self.assertTrue(self.start()["outcomes"][0]["decision"]["allowed"])
        self.report()
        self.assess(phase="acceptance", score=94.9)
        self.assertFalse(self.apply({"op": "accept", "task_id": "a"})["outcomes"][0]["decision"]["allowed"])
        self.assess(phase="acceptance", score=95)
        self.apply({"op": "accept", "task_id": "a"})
        self.assertEqual(self.state()["tasks"]["a"]["status"], "accepted")

    def test_full_ai_records_low_score_high_risk_without_human_pause(self):
        self.init()
        self.apply(create())
        self.assess(score=1, risk="high", conclusion="uncertain", unknowns=["risky experiment"])
        self.assertTrue(self.start()["outcomes"][0]["decision"]["allowed"])
        self.report()
        self.assess(phase="acceptance", score=1, risk="high")
        self.apply({"op": "accept", "task_id": "a"})
        self.assertEqual(self.state()["tasks"]["a"]["status"], "accepted")

    def test_full_ai_cannot_hide_failed_or_unknown_mandatory_checks(self):
        self.init()
        self.apply(create())
        self.assess()
        self.start()
        self.report()
        self.assess(phase="acceptance", checks={"works": {"status": "unknown", "evidence": ["not observed"]}})
        self.error("REQUIRED_CHECK_NOT_PASSED", lambda: self.apply({"op": "accept", "task_id": "a"}))

    def test_candidate_change_invalidates_previous_acceptance(self):
        self.init()
        self.apply(create())
        self.assess()
        self.start()
        self.report()
        self.assess(phase="acceptance")
        self.report(version="v2")
        self.error("ACCEPTANCE_ASSESSMENT_REQUIRED", lambda: self.apply({"op": "accept", "task_id": "a"}))
        stale = assessment("acceptance", candidate_version="v1", contract_version=1)
        self.error("STALE_CANDIDATE", lambda: self.apply({"op": "assess", "task_id": "a", "phase": "acceptance", "assessment": stale}))

    def test_independent_review_and_failed_check_cannot_be_removed_by_agent(self):
        self.init()
        self.apply(create())
        self.assess()
        self.start()
        self.report()
        self.error("REVIEWER_NOT_INDEPENDENT", lambda: self.assess(phase="acceptance", by={"id": "worker", "kind": "agent"}))
        contract = {"goal": "new", "checks": [{"id": "easier", "description": "easier"}]}
        self.error("REQUIRED_CHECK_CHANGE_REQUIRES_HUMAN", lambda: self.apply({"op": "revise_contract", "task_id": "a", "contract": contract, "reason": "avoid failure"}))

    def test_retries_are_bounded_and_failed_dependencies_do_not_run(self):
        self.init(policy={"max_retries": 1})
        self.apply(create(), create("b", depends_on=["a"]), create("c"))
        self.assess()
        self.start()
        self.report()
        self.apply({"op": "reject", "task_id": "a", "reason": "first failure"})
        self.report(version="v2")
        self.apply({"op": "reject", "task_id": "a", "reason": "second failure"})
        self.assertEqual(self.state()["tasks"]["a"]["status"], "failed")
        self.error("DEPENDENCY_BLOCKED", lambda: self.start("b"))
        self.assess("c")
        self.start("c")
        self.assertEqual(self.state()["tasks"]["c"]["status"], "doing")

    def test_stop_failure_policy_and_human_resume(self):
        self.init(policy={"on_failure": "stop"})
        self.apply(create(), create("b"), {"op": "fail", "task_id": "a", "reason": "blocked"})
        self.error("WORKFLOW_HALTED", lambda: self.start("b"))
        self.apply({"op": "resume"}, human=True)
        self.assess("b")
        self.start("b")

    def test_task_policy_override_is_scoped_and_relaxation_needs_human(self):
        self.init("manual")
        self.error("POLICY_RELAXATION_REQUIRES_HUMAN", lambda: self.apply(create(policy={"mode": "full_ai"})))
        self.apply(create(policy={"mode": "full_ai"}), create("b"), human=True)
        self.assess()
        self.assertTrue(self.start()["outcomes"][0]["decision"]["allowed"])
        self.assertFalse(self.start("b")["outcomes"][0]["decision"]["allowed"])
        self.assertEqual(self.state()["config"]["policy"]["mode"], "manual")

    def test_direction_is_assessed_separately_then_applied(self):
        self.init("conditional")
        data = assessment("direction", score=89, route="route-b", candidates=["route-a", "route-b"])
        self.apply({"op": "assess_direction", "assessment": data})
        self.assertIsNone(self.state()["route"])
        self.assertFalse(self.apply({"op": "apply_direction", "assessment_id": data["id"]})["outcomes"][0]["decision"]["allowed"])
        self.apply({"op": "apply_direction", "assessment_id": data["id"]}, human=True)
        self.assertEqual(self.state()["route"]["route"], "route-b")
        self.error("CROSS_STREAM_TRANSACTION", lambda: self.apply({"op": "assess_direction", "assessment": assessment()}, create()))

    def test_paths_tools_and_controller_role_are_enforced(self):
        self.init(authorization={"paths": ["safe"], "tools": []})
        self.error("PATH_NOT_AUTHORIZED", lambda: self.apply(create(paths=["outside"])))
        self.error("TOOL_NOT_AUTHORIZED", lambda: self.apply(create(tools=["shell"])))
        cmd = self.command(create())
        cmd["actor"]["role"] = "worker"
        self.error("CONTROL_WRITER_REQUIRED", lambda: store.apply_command(self.root, cmd))


class NestingAndContextTests(WorkflowCase):
    def test_method_parameters_inherit_without_copying_and_task_changes_are_scoped(self):
        self.init(parameters={"quality": "high", "render": {"width": 800, "enabled": True}})
        self.apply(create(parameters={"render": {"width": 400, "enabled": False}}), create("other"))
        first = context.build_context(self.state(), task_id="a")["parameters"]
        self.assertEqual(first, {"quality": "high", "render": {"width": 400, "enabled": False}})
        self.assertEqual(context.build_context(self.state(), task_id="other")["parameters"]["render"]["width"], 800)
        self.apply({"op": "set_parameters", "task_id": "a", "parameters": {"quality": "  ", "items": [], "scale": 0}, "reason": "task settings"})
        result = context.build_context(self.state(), task_id="a")["parameters"]
        self.assertEqual(result["quality"], "high")
        self.assertEqual(result["items"], [])
        self.assertEqual(result["scale"], 0)

    def test_override_is_scoped_and_drift_is_detected_before_start(self):
        self.init()
        (self.root / "overrides").mkdir()
        path = self.root / "overrides" / "verify.md"
        path.write_text("Use the project runtime screenshot method", encoding="utf-8")
        self.apply(create(overrides=[{"step": "verify", "path": "overrides/verify.md", "reason": "custom runtime"}]))
        self.assess()
        self.assertTrue(context.build_context(self.state(), task_id="a")["task"]["overrides"][0]["sha256"])
        path.write_text("changed local method", encoding="utf-8")
        self.error("OVERRIDE_CHANGED", self.start)

    def test_failed_required_child_closes_branch_and_releases_independent_work(self):
        self.init()
        self.apply(create(), create("independent"))
        self.assess()
        self.start()
        self.apply({"op": "nest", "task_id": "a", "reason": "complex"}, create("child", parent_id="a"))
        self.apply({"op": "fail", "task_id": "child", "reason": "cannot meet required contract"})
        self.assertEqual(self.state()["tasks"]["a"]["status"], "failed")
        self.assess("independent")
        self.start("independent")
        self.assertEqual(self.state()["tasks"]["independent"]["status"], "doing")

    def test_optional_child_failure_does_not_fail_parent(self):
        self.init()
        self.apply(create())
        self.assess()
        self.start()
        self.apply({"op": "nest", "task_id": "a", "reason": "optional investigation"}, create("optional", parent_id="a", required=False))
        self.apply({"op": "fail", "task_id": "optional", "reason": "optional result unavailable"})
        self.assertEqual(self.state()["tasks"]["a"]["status"], "doing")
        self.finish()

    def test_nested_children_inherit_scope_and_gate_parent_acceptance(self):
        self.init()
        self.apply(create(paths=["safe"], tools=[]))
        self.assess()
        self.start()
        self.apply({"op": "nest", "task_id": "a", "reason": "separate bounded contracts"})
        self.apply(create("a/1", parent_id="a"), create("a/2", parent_id="a", depends_on=["a/1"]))
        self.assertEqual(self.state()["tasks"]["a/1"]["paths"], ["safe"])
        self.error("PATH_NOT_AUTHORIZED", lambda: self.apply(create("escape", parent_id="a", paths=["."])))
        self.assess("a/1")
        self.start("a/1")
        self.finish("a/1")
        self.assess("a/2")
        self.start("a/2")
        self.error("CHILDREN_NOT_ACCEPTED", lambda: self.report("a"))
        self.assertEqual(self.state()["tasks"]["a"]["status"], "doing")
        self.finish("a/2")
        self.finish("a")
        self.assertEqual(self.state()["tasks"]["a"]["status"], "accepted")

    def test_manual_nesting_requires_confirmation_and_limits_apply(self):
        self.init("manual", limits={"max_children": 1, "max_depth": 1})
        self.apply(create())
        self.start(human=True)
        self.assertFalse(self.apply({"op": "nest", "task_id": "a", "reason": "complex"})["outcomes"][0]["decision"]["allowed"])
        self.error("NESTING_NOT_AUTHORIZED", lambda: self.apply(create("child", parent_id="a")))
        self.apply({"op": "nest", "task_id": "a", "reason": "complex"}, human=True)
        self.apply(create("child", parent_id="a"))
        self.error("CHILD_LIMIT", lambda: self.apply(create("child2", parent_id="a")))
        self.start("child", human=True)
        self.apply({"op": "nest", "task_id": "child", "reason": "complex"}, human=True)
        self.error("DEPTH_LIMIT", lambda: self.apply(create("grandchild", parent_id="child")))

    def test_shared_resource_and_overlapping_writes_are_serial(self):
        self.init(limits={"max_parallel": 2})
        self.apply(create("a", paths=["one"], resources=["unity"]), create("b", paths=["two"], resources=["unity"]))
        self.assess("a")
        self.assess("b")
        self.start("a")
        self.error("RESOURCE_BUSY", lambda: self.start("b"))

    def test_role_reuse_matches_domain_host_and_role_then_invalidates(self):
        self.init()
        self.apply(create(), {"op": "assign_role", "slot": "worker-ui", "role": "worker", "domain": "ui", "handle": "agent-1", "host_session": "host-1"})
        payload = context.build_context(self.state(), task_id="a", role="worker", domain="ui", host_session="host-1", handoff=True)
        self.assertEqual(payload["role"]["session"]["action"], "reuse")
        self.assertTrue(payload["same_task"])
        self.assertEqual(context.build_context(self.state(), role="worker", domain="ui", host_session="new")["role"]["session"]["action"], "spawn")
        self.apply({"op": "invalidate_role", "slot": "worker-ui", "reason": "host handle expired"})
        self.assertEqual(context.build_context(self.state(), role="worker", domain="ui", host_session="host-1")["role"]["session"]["action"], "spawn")

    def test_protected_constraints_survive_small_context_budget(self):
        self.init(constraints=["must retain this constraint"])
        self.apply(create())
        payload = context.build_context(self.state(), task_id="a", max_chars=100)
        self.assertEqual(payload["constraints"], ["must retain this constraint"])
        self.assertIn("Observed behavior matches requirement", config.canonical(payload))
        self.assertTrue(any("OVER_BUDGET" in s for s in payload["warnings"]))

    def test_progress_surfaces_in_context_and_terminal_tasks_reject_it(self):
        self.init()
        self.apply(create())
        self.assess()
        self.start()
        self.apply({"op": "progress", "task_id": "a", "summary": "Candidate under review",
                    "questions": ["Approve the visual direction?"], "decisions": ["Use the reference layout"],
                    "next_steps": ["Send to reviewer"]})
        payload = context.build_context(self.state(), task_id="a")
        self.assertEqual(payload["task"]["progress"]["summary"], "Candidate under review")
        self.assertEqual(payload["task"]["progress"]["questions"], ["Approve the visual direction?"])
        self.finish()
        self.error("TASK_NOT_ACTIVE", lambda: self.apply({"op": "progress", "task_id": "a", "summary": "late"}))

    def test_default_context_without_active_task_is_menu_only(self):
        self.init()
        self.apply(create())
        self.apply({"op": "progress", "task_id": "a", "summary": "ready and waiting"})
        payload = context.build_context(self.state())
        self.assertIsNone(payload["task_id"])
        self.assertIsNone(payload["task"])
        self.assertTrue(any(item["id"] == "a" and item["status"] == "ready" for item in payload["menu"]))
        self.assertNotIn("ready and waiting", config.canonical(payload))
        explicit = context.build_context(self.state(), task_id="a")
        self.assertEqual(explicit["task"]["progress"]["summary"], "ready and waiting")

    def test_default_context_after_failure_is_menu_only(self):
        self.init()
        self.apply(create())
        self.assess()
        self.start()
        self.apply({"op": "fail", "task_id": "a", "reason": "required environment missing"})
        payload = context.build_context(self.state())
        self.assertIsNone(payload["task_id"])
        self.assertTrue(any(item["id"] == "a" and item["status"] == "failed" for item in payload["menu"]))

    def test_template_change_requires_explicit_rebind(self):
        self.init()
        identity = {"version": "5.0.1", "integrity": "changed", "method": "changed-method"}
        with mock.patch.object(store, "template_identity", return_value=identity):
            self.error("TEMPLATE_CHANGED", self.state)
            state = store.load_snapshot(self.root, verify_template=False)
            command = {"command_id": "rebind", "expected_revision": state["revision"], "actor": {"id": "user", "kind": "human"},
                       "operations": [{"op": "rebind_template", "template": identity, "reason": "reviewed new shared methods"}]}
            store.apply_command(self.root, command)
            self.assertEqual(self.state()["config"]["template"], identity)
            self.assertEqual(store.rebuild(self.root, persist=False), self.state())

    def test_1000_history_tasks_warm_context_never_opens_sealed_logs_or_evidence(self):
        self.init(rotation={"tasks": 20})
        for batch in range(50):
            ops = []
            for index in range(batch * 20, batch * 20 + 20):
                key = str(index)
                ops += [create(key), {"op": "cancel", "task_id": key, "reason": "historical completed scope"}]
            self.apply(*ops)
        self.apply(create("current"))
        sealed = {p.resolve() for p in (self.root / "records").glob("*.jsonl") if p.name != "tasks-000051.jsonl" and p.name != "direction-000001.jsonl"}
        opened = []
        original = Path.open
        def guarded(path, *args, **kwargs):
            self.assertNotIn(path.resolve(), sealed)
            self.assertNotIn("artifacts", path.parts)
            opened.append(str(path))
            return original(path, *args, **kwargs)
        with mock.patch.object(Path, "open", guarded):
            result = context.build_context(self.state(), task_id="current", since=1)
        self.assertEqual(result["task_id"], "current")
        self.assertLess(len(config.canonical(result)), 12000)
        self.assertLess(len(opened), 10)
        self.assertFalse(result["historical_scan"])


if __name__ == "__main__":
    unittest.main()
