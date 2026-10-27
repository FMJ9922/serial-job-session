"""Replay independent declarative scenarios, including deliberately red gates."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import sjs_config as config
import sjs_store as store


class ReplayTests(unittest.TestCase):
    def replay(self, name):
        fixture = json.loads((Path(__file__).parent / "fixtures" / (name + ".json")).read_text())
        with tempfile.TemporaryDirectory() as temporary, mock.patch("check_skill_integrity.check_manifest", return_value={"ok": True}):
            instance = Path(temporary) / "flow"
            policy = {"mode": fixture["mode"]}
            if policy["mode"] == "conditional":
                policy.update(risk_ceiling="medium", thresholds={"feasibility": 85, "direction": 90, "acceptance": 95}, confirmation="fixture explicit human instruction")
            store.initialize(instance, {"goal": "Fix agreed UI", "route_key": "replay", "authorization": {"paths": ["."], "tools": ["shell"]},
                "policy": policy, "modules": {"estimates": False, "feishu": False}, "rotation": {"tasks": 1}})
            for index, step in enumerate(fixture["steps"]):
                state = store.load_snapshot(instance)
                task_id, action = step["id"], step["action"]
                op = {"op": action, "task_id": task_id}
                if action == "create":
                    op["contract"] = {"goal": "Fix " + task_id, "checks": [{"id": "works", "description": "Matches actual reference"}]}
                    if step.get("parent"):
                        op["parent_id"] = step["parent"]
                elif action in {"assess", "review"}:
                    phase = "acceptance" if action == "review" else "feasibility"
                    data = {"id": "assessment-" + str(index), "score": step["score"], "risk": step.get("risk", "low"), "conclusion": "pass",
                            "evidence": ["artifacts/observed.png"], "unknowns": [], "rationale": "fixture observation", "recovery_cost": "isolated reversible write"}
                    if action == "review":
                        task = state["tasks"][task_id]
                        data.update(candidate_version=task["candidate"]["version"], contract_version=task["contract_version"], by={"id": "reviewer", "kind": "agent"},
                                    checks={"works": {"status": step.get("check", "passed"), "evidence": ["artifacts/observed.png"]}})
                    op.update(op="assess", phase=phase, assessment=data)
                elif action == "report":
                    op.update(candidate_version=step["version"], by={"id": "worker", "kind": "agent"}, evidence=["artifacts/observed.png"])
                elif action in {"reject", "nest"}:
                    op["reason"] = "fixture feedback or decomposition"
                command = {"command_id": "step-" + str(index), "expected_revision": state["revision"],
                           "actor": {"id": "human" if step.get("human") else "controller", "kind": "human" if step.get("human") else "agent"}, "operations": [op]}
                with self.subTest(fixture=name, step=index, action=action):
                    if step.get("error"):
                        with self.assertRaises(config.SJSError) as caught:
                            store.apply_command(instance, command)
                        self.assertEqual(caught.exception.code, step["error"])
                        self.assertEqual(state, store.load_snapshot(instance), "Rejected operation must not mutate state")
                    else:
                        result = store.apply_command(instance, command)
                        if "allowed" in step:
                            self.assertEqual(result["outcomes"][0]["decision"]["allowed"], step["allowed"])
            state = store.load_snapshot(instance)
            self.assertEqual({k: t["status"] for k, t in state["tasks"].items()}, fixture["expected"])
            self.assertEqual(state, store.rebuild(instance, persist=False))
            (instance / "state.json").unlink()
            self.assertEqual(state, store.load_snapshot(instance))

    def test_manual(self):
        self.replay("manual")

    def test_conditional_rework(self):
        self.replay("conditional-rework")

    def test_full_ai_nested(self):
        self.replay("full-ai-nested")


if __name__ == "__main__":
    unittest.main()
