"""Installed CLI integration, without monkeypatching release integrity."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from sjs_config import ROOT
import sjs_store as store
from test_sjs_v5 import configuration, create, assessment


class CliTests(unittest.TestCase):
    def test_native_init_apply_handoff_stats_guard_and_check(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            instance = base / "instance"
            config_path = base / "config.json"
            config_path.write_text(json.dumps(configuration()), encoding="utf-8")
            def cli(command, *, payload=None, expected=0, extra=()):
                args = [sys.executable, "-X", "utf8", str(ROOT / "scripts/sjs.py"), command, "--instance", str(instance), *extra]
                if payload is not None:
                    args += ["--input", "-"]
                response = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                                          capture_output=True, text=True, encoding="utf-8", cwd=base)
                self.assertEqual(response.returncode, expected, response.stdout + response.stderr)
                return json.loads(response.stdout)
            preview = cli("init", payload=configuration(), extra=("--dry-run",))
            self.assertTrue(preview["dry_run"])
            self.assertFalse(instance.exists())
            cli("init", payload=configuration())
            self.assertEqual(len([p for p in instance.rglob("*") if p.is_file()]), 4)
            def apply(*ops):
                state = store.load_snapshot(instance)
                return cli("apply", payload={"command_id": str(state["revision"]), "expected_revision": state["revision"],
                           "actor": {"id": "controller", "kind": "agent"}, "operations": list(ops)})
            apply(create(), {"op": "assess", "task_id": "a", "phase": "feasibility", "assessment": assessment()},
                  {"op": "progress", "task_id": "a", "summary": "Baseline ready", "next_steps": ["Implement candidate"]})
            cli("start", extra=("--task-id", "a", "--command-id", "start-a"))
            handoff = cli("handoff", extra=("--task-id", "a", "--role", "worker"))
            self.assertEqual(handoff["task"]["progress"]["summary"], "Baseline ready")
            apply({"op": "report", "task_id": "a", "candidate_version": "v1", "by": {"id": "worker", "kind": "agent"}, "evidence": ["artifacts/proof.json"]})
            data = assessment("acceptance", candidate_version="v1", contract_version=1, by={"id": "reviewer", "kind": "agent"},
                              checks={"works": {"status": "passed", "evidence": ["artifacts/proof.json"]}})
            apply({"op": "assess", "task_id": "a", "phase": "acceptance", "assessment": data}, {"op": "accept", "task_id": "a"})
            self.assertTrue(cli("check")["ok"])
            self.assertEqual(cli("stats", expected=1)["error"], "ESTIMATES_DISABLED")
            self.assertEqual(cli("sync", expected=1)["error"], "FEISHU_DISABLED")
            (instance / "state.json").unlink()
            self.assertEqual(cli("context", extra=("--task-id", "a"))["task"]["status"], "accepted")
            self.assertTrue(cli("rebuild")["ok"])
            self.assertTrue(cli("check")["ok"])

    def test_shared_document_links_exist(self):
        sources = list(ROOT.glob("*.md")) + list((ROOT / "references").glob("*.md"))
        for path in sources:
            text = path.read_text(encoding="utf-8")
            for target in re.findall(r"\]\(([^)]+)\)", text):
                if "://" in target or target.startswith("#"):
                    continue
                with self.subTest(document=path.name, target=target):
                    self.assertTrue((path.parent / target.split("#")[0]).exists())


if __name__ == "__main__":
    unittest.main()
