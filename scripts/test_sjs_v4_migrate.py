#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Focused tests for the v3 -> v4 migration planner.

These tests exercise planning and safety boundaries with a small temporary
instance.  They never invoke a real Houdini/Unity workspace or copy output
evidence.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import sjs_v4_migrate as migrate


class MigrationPlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="sjs-v4-migrate-")
        self.root = Path(self.temp.name) / "instance"
        self.root.mkdir(parents=True)
        (self.root / "jobs").mkdir()
        (self.root / "protocol").mkdir()
        (self.root / "knowledge").mkdir()
        (self.root / "output" / "job-six").mkdir(parents=True)
        (self.root / ".serial-job-session").mkdir()
        self._write("INSTANCE.json", json.dumps({
            "instance_id": "fixture-instance",
            "route_key": "fixture",
            "domain_name": "fixture",
            "protocol_version": "3.0.1",
        }, ensure_ascii=False, indent=2) + "\n")
        self._write("ENV.md", "# ENV\n")
        self._write("CURRENT.md", "# CURRENT\n")
        self._write("PROTOCOL.md", "# protocol\n")
        self._write("protocol/PROTOCOL.md", "# old protocol\n")
        self._write("NORTH-STAR.md", "# star\n\n## 当前纠偏基线\n- 编号 90 is dynamic\n\n## P4–P8 Go / No-Go\n- 90 → 42\n\n## 最终目标\n- stable\n")
        self._write("NORMS.md", """# norms

## 通则
- stable rule

## v3.1 用户确认覆盖
- newer authored rule

## 自动沉淀
- [auto:fixture] old practice
""")
        self._write("JOB-SHAPES.md", """# menu

## 待做

| 编号 | phase_id | 短名 | 目标 | estimate_group | 依赖 | 面 | actor | human_accept | 预计 |
|---|---|---|---|---|---|---|---|---|---|
| 90 | P4 | ready shape | do the thing | hda | 6 | docs | agent | none | 1.2 |

## 条件分支（4m）

| 编号 | phase_id | 短名 | 目标 | estimate_group | 依赖 | 面 | actor | human_accept | 预计 |
|---|---|---|---|---|---|---|---|---|---|
| 96 | P7 | branch shape | rebuild after decision | map | 61 | houdini | agent | lookdev | 1.5 |

编号 **90**：正式接口必须保持 `contract_v4`，分支条件是 `published` 后才允许。
""")
        self._write("FACTS.md", """# facts

| 事实 | 来源 job-id | 日期 |
|---|---|---|
| fixture fact remains valid | job-six | 2026-09-20 |
| old fact was 已被新事实取代 | job-six | 2026-09-19 |
""")
        self._write("LOG.md", """# log

| 日期 | 编号 | job-id | 结果 |
|---|---|---|---|
| 2026-09-20 | 6 | job-six | accepted；attempt 1 |
补记：规范审阅不属于单卡事件。
""")
        self._write("PLAN.md", "# plan\n")
        self._write(".serial-job-session/instances.json", json.dumps({
            "instances": [{
                "instance_id": "fixture-instance",
                "instance_root": str(self.root),
                "protocol_version": "3.0.1",
            }],
        }, indent=2) + "\n")
        self._write("jobs/job-six.md", """# 任务卡 · job-six

```yaml
id: job-six
shape: 6
estimate_group: docs
depends_on: []
faces: [docs]
estimate: 0.5
estimate_source: menu
status: accepted
attempt: 1
closed_at: 2026-09-20T10:00:00+08:00
```

## 目标

- Freeze the old norm contract.

## 验收

- accepted with evidence.

## 拟写入 FACTS

- fixture fact remains valid
""")
        (self.root / "output" / "job-six" / "evidence.json").write_text("{\"ok\":true}\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_plan_preserves_card_history_facts_events_and_details(self) -> None:
        before = migrate._output_manifest(self.root)
        plan = migrate.build_plan(self.root, include_output=True)
        self.assertEqual(plan["summary"]["cards"], 1)
        rewrite = next(item for item in plan["operations"] if item.get("kind") == "rewrite_card")
        self.assertEqual(rewrite["metadata"]["legacy_status"], "accepted")
        self.assertEqual(rewrite["metadata"]["status"], "accepted")
        self.assertEqual(rewrite["metadata"]["fact_records"][0]["source_date"], "2026-09-20")
        self.assertTrue(rewrite["metadata"]["fact_records"][0]["evidence"].startswith("knowledge/v4-migration/archive/control/FACTS.md#L"))
        self.assertTrue(rewrite["metadata"]["fact_records"][1]["active"] is False)
        self.assertEqual(rewrite["metadata"]["events"][0]["event"], "legacy_close")
        self.assertIn("fixture fact remains valid", rewrite["sections"]["knowledge"])
        ready = next(item for item in plan["operations"] if item.get("shape") == 90)
        self.assertIn("contract_v4", ready["sections"]["goal"])
        self.assertEqual(set(rewrite["sections"]), {"goal", "dependencies", "progress", "execution", "artifacts", "closure", "knowledge"})
        self.assertEqual(before, migrate._output_manifest(self.root))

    def test_direction_or_semantics_and_registry_scope(self) -> None:
        config = self.root / "direction-review.json"
        config.write_text(json.dumps({"nodes": [{"after": "61/99", "before": "62", "label": "review"}]}, ensure_ascii=False), encoding="utf-8")
        plan = migrate.build_plan(self.root, direction_config=str(config), include_output=False)
        direction = next(item for item in plan["operations"] if item.get("direction_review"))
        self.assertEqual(direction["metadata"]["depends_on"], [61])
        self.assertEqual(direction["metadata"]["conditional_dependencies"], [99])
        self.assertEqual(direction["metadata"]["decision_paths"], [
            {"when": "61=8m", "requires": []},
            {"when": "61=4m", "requires": [99]},
        ])
        registry_ops = [item for item in plan["operations"] if item.get("kind") == "upgrade_registry"]
        self.assertEqual(len(registry_ops), 1)
        self.assertEqual(registry_ops[0]["registry_scope"], "repository-instance-entry-only")
        self.assertEqual(Path(registry_ops[0]["path"]).resolve(), (self.root / ".serial-job-session" / "instances.json").resolve())

    def test_curated_norms_have_one_structured_owner_and_split_kinds(self) -> None:
        (self.root / "norms-v4.md").write_text(
            """# v4 effective NORMS

### NORM-WORLD-001 · world contract
- rule: keep the world contract stable.
- tags: [world, stable]
- source: `NORMS.md:L14-L15`（migration）
- evidence: ENV world table

### NORM-EXP-001 · experience
- rule: record evidence for every close.
- tags: [experience, evidence]
- source: `NORMS.md:L100`
- evidence: card log
""",
            encoding="utf-8",
        )
        plan = migrate.build_plan(self.root, include_output=False)
        rewrite = next(item for item in plan["operations"] if item.get("kind") == "rewrite_card")
        records = rewrite["metadata"]["norms"]
        self.assertTrue(records)
        self.assertTrue(all(item.get("key") and item.get("active") is True for item in records))
        self.assertTrue(any(item.get("kind") == "protected_constraint" for item in records))
        self.assertTrue(any(item.get("kind") == "practice" for item in records))
        card_op = next(item for item in plan["file_operations"] if item.get("op") == "replace_card" and item.get("card_id") == rewrite["card_id"])
        self.assertEqual(len(card_op["metadata"]["norms"]), len(records))
        norms_text = next(item["content"] for item in plan["operations"] if item.get("kind") == "replace_source" and item.get("path") == "NORMS.md")
        self.assertIn("NORM-WORLD-001", norms_text)
        self.assertIn("knowledge/v4-migration/archive/NORMS.md#L14-L15", norms_text)
        self.assertTrue(all("NORMS.md:" not in line or "archive/NORMS.md" in line for line in norms_text.splitlines()))

    def test_default_norms_are_generic(self) -> None:
        plan = migrate.build_plan(self.root, include_output=False)
        norms_text = next(item["content"] for item in plan["operations"] if item.get("kind") == "replace_source" and item.get("path") == "NORMS.md")
        self.assertIn("# v4 effective NORMS", norms_text)
        self.assertNotIn("NORM-WORLD", norms_text)
        self.assertNotIn("NORM-EXP", norms_text)

    def test_projection_sources_are_archived_and_output_manifest_is_exact(self) -> None:
        plan = migrate.build_plan(self.root, include_output=True)
        archived = {item["source_path"] for item in plan["operations"] if item.get("kind") == "archive_projection_source"}
        self.assertEqual(archived, {"JOB-SHAPES.md", "CURRENT.md", "LOG.md", "FACTS.md", "PLAN.md"})
        self.assertEqual(plan["summary"]["output_files"], 1)
        output_bytes = (self.root / "output" / "job-six" / "evidence.json").read_bytes()
        self.assertEqual(plan["summary"]["output_bytes"], len(output_bytes))
        self.assertEqual(plan["output_manifest"][0]["sha256"], hashlib.sha256(output_bytes).hexdigest())

    def test_external_registry_path_is_narrow(self) -> None:
        plan = {"base_revision": 0, "file_operations": [{"op": "write_file", "path": str(self.root.parent / "outside.json"), "content": "x", "external_root": True}]}
        with self.assertRaises(migrate.MigrationError):
            migrate._apply_core_transaction(self.root, plan, "malicious-external")


if __name__ == "__main__":
    unittest.main()
