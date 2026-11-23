from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import sjs_context_budget as budget
import sjs_context_budget_core as core


class ContextBudgetTests(unittest.TestCase):
    def test_pure_allocation_interface_applies_target_and_band(self):
        result = core.estimate_task_allocation(
            observed_history_tokens=100,
            context_limit_tokens=1000,
            effective_percent=100,
            reserve_tokens=50,
            task_prompt_tokens=200,
            task_output_tokens=250,
        )
        self.assertEqual(result["available_tokens"], 850)
        self.assertEqual(result["target_tokens"], 637)
        self.assertEqual(result["task_utilization_percent"], 52.94)
        self.assertEqual(result["allocation_band"], "acceptable")
        self.assertEqual(result["allocation_recommendation"], "dispatch")

    def test_recent_three_accepted_comparable_tasks_trigger_review(self):
        result = core.evaluate_recent_utilization([
            {"task_id": "a", "status": "accepted", "group": "ui", "utilization_percent": 40},
            {"task_id": "b", "status": "accepted", "group": "ui", "utilization_percent": 48},
            {"task_id": "c", "status": "accepted", "group": "ui", "utilization_percent": 42},
            {"task_id": "small", "status": "accepted", "group": "ui", "utilization_percent": 10, "intentional_small": True},
            {"task_id": "other", "status": "accepted", "group": "backend", "utilization_percent": 10},
        ], group="ui")
        self.assertEqual(result["sample_count"], 3)
        self.assertEqual(result["median_utilization_percent"], 42.0)
        self.assertTrue(result["adjustment_triggered"])
        self.assertEqual(result["recommendation"], "review_next_cards")

    def test_task_samples_can_be_read_from_accepted_cards(self):
        samples = budget.task_samples_from_state({
            "tasks": {
                "a": {"id": "a", "status": "accepted", "domain": "ui", "accepted_at": "2026-01-01",
                      "parameters": {"context_budget": {"actual_utilization_percent": 45, "group": "ui"}}},
                "b": {"id": "b", "status": "doing", "domain": "ui", "parameters": {
                    "context_budget": {"actual_utilization_percent": 20}}},
                "c": {"id": "c", "status": "accepted", "domain": "ui", "accepted_at": "2026-01-02",
                      "parameters": {"context_budget": {"actual_utilization_percent": 40,
                                                          "intentional_small": True}}},
            }
        })
        self.assertEqual([row["task_id"] for row in samples], ["a", "c"])
        self.assertTrue(samples[1]["intentional_small"])

    def make_databases(self, root: Path) -> tuple[Path, Path]:
        state = root / "state.sqlite"
        history = root / "history.sqlite"
        connection = sqlite3.connect(state)
        try:
            connection.execute(
                "create table threads (id text primary key, model text, reasoning_effort text, "
                "tokens_used integer, rollout_path text, title text, updated_at_ms integer, history_mode text)"
            )
            connection.execute(
                "insert into threads values (?, ?, ?, ?, ?, ?, ?, ?)",
                ("thread-1", "test-model", "high", 999999, "", "Test", 1, "paginated"),
            )
            connection.commit()
        finally:
            connection.close()
        connection = sqlite3.connect(history)
        try:
            connection.execute(
                "create table thread_items (thread_id text, turn_id text, item_id text, "
                "rollout_ordinal integer, created_at_ms integer, item_json text, item_type text, "
                "updated_at_ordinal integer, started_at_ms integer, completed_at_ms integer)"
            )
            rows = [
                ("thread-1", "turn-1", "item-1", 1, 1, json.dumps({"type": "userMessage", "text": "hello"}), "userMessage", 0, None, None),
                ("thread-1", "turn-1", "item-2", 2, 2, json.dumps({"type": "agentMessage", "text": "world"}), "agentMessage", 0, None, None),
            ]
            connection.executemany("insert into thread_items values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
            connection.commit()
        finally:
            connection.close()
        return state, history

    def test_estimate_reads_thread_history_and_model_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state, history = self.make_databases(root)
            (root / "models_cache.json").write_text(json.dumps({"models": [{
                "slug": "test-model",
                "context_window": 1000,
                "effective_context_window_percent": 80,
            }]}), encoding="utf-8")
            result = budget.estimate_context_budget(
                thread_id="thread-1",
                state_db=state,
                history_db=history,
                codex_home=root,
                task_text="a small task",
                task_output_tokens=10,
            task_tool_tokens=20,
                reserve_tokens=10,
                recent_tasks=[
                    {"task_id": "a", "status": "accepted", "group": "ui", "utilization_percent": 40},
                    {"task_id": "b", "status": "accepted", "group": "ui", "utilization_percent": 45},
                    {"task_id": "c", "status": "accepted", "group": "ui", "utilization_percent": 48},
                ],
                task_group="ui",
            )
            self.assertEqual(result["schema"], "sjs-context-budget/v1")
            self.assertEqual(result["model"], "test-model")
            self.assertEqual(result["context"]["configured_limit_tokens"], 1000)
            self.assertEqual(result["context"]["usable_limit_tokens"], 800)
            self.assertEqual(result["history"]["item_count"], 2)
            self.assertGreater(result["context"]["observed_history_tokens"], 0)
            self.assertEqual(result["task"]["fit"], "light")
            self.assertEqual(result["task"]["recommendation"], "review_scope")
            self.assertEqual(result["allocation"]["task_tool_tokens"], 20)
            self.assertTrue(result["recent_utilization"]["adjustment_triggered"])
            self.assertEqual(result["confidence"], "estimated")

    def test_cumulative_tokens_used_is_not_used_as_context_usage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state, history = self.make_databases(root)
            (root / "models_cache.json").write_text(json.dumps({"models": [{
                "slug": "test-model", "context_window": 1000,
            }]}), encoding="utf-8")
            result = budget.estimate_context_budget(
                thread_id="thread-1", state_db=state, history_db=history, codex_home=root,
                task_output_tokens=1, reserve_tokens=1,
            )
            self.assertLess(result["context"]["observed_history_tokens"], 100)
            self.assertEqual(result["source"]["model_metadata"], "models_cache")

    def test_cli_context_budget_does_not_require_an_instance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state, history = self.make_databases(root)
            (root / "models_cache.json").write_text(json.dumps({"models": [{
                "slug": "test-model", "context_window": 1000,
            }]}), encoding="utf-8")
            command = [
                sys.executable, str(Path(__file__).with_name("sjs.py")), "context-budget",
                "--thread-id", "thread-1", "--state-db", str(state),
                "--history-db", str(history), "--codex-home", str(root), "--input", "-",
            ]
            completed = subprocess.run(
                command,
                input=json.dumps({"task_output_tokens": 20, "task_tool_tokens": 30}),
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result["ok"], True)
            self.assertEqual(result["allocation"]["task_output_tokens"], 20)
            self.assertEqual(result["allocation"]["task_tool_tokens"], 30)


if __name__ == "__main__":
    unittest.main()

