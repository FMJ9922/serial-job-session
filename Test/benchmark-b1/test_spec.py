"""Verify frozen public acceptance numbering and experimental allocations."""
import collections
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

_path = Path(__file__).with_name("spec.py")
_loader = importlib.util.spec_from_file_location("b1_canonical_spec", _path)
spec = importlib.util.module_from_spec(_loader)
_loader.loader.exec_module(spec)


class SpecTests(unittest.TestCase):
    def test_frozen_requirement_wording(self):
        encoded = json.dumps(spec.CHECKS, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.assertEqual(hashlib.sha256(encoded).hexdigest(), "521669a8f2dc43e8fbfab5f7b24bd82533c25e05b0d46c4a3dd8639428369fad")

    def test_checks_are_exactly_100_unique_ordered_ids(self):
        self.assertEqual([c["id"] for c in spec.CHECKS], [f"{i:03d}" for i in range(1, 101)])
        self.assertTrue(all(set(c) == {"id", "title", "category"} for c in spec.CHECKS))
        self.assertTrue(all(c["title"].endswith("。") for c in spec.CHECKS))

    def test_category_boundaries(self):
        expected = [
            ("菜单与完整流程", 8), ("三维棋盘与消除", 16), ("操作与三维可读性", 8),
            ("HUD、计分与局内状态", 8), ("玩家档案", 8), ("JSON 存档与续局", 12),
            ("音乐与音效设置", 6), ("中英文本地化", 8), ("键位映射", 8),
            ("本地排行榜", 8), ("Addressables", 6), ("资源完整性", 4),
        ]
        self.assertEqual(list(collections.Counter(c["category"] for c in spec.CHECKS).items()), expected)

    def test_group_assignments_and_order(self):
        self.assertEqual(spec.ORDER, ["Demo1", "Demo2", "Demo5", "Demo", "Demo3", "Demo4"])
        self.assertEqual([g["id"] for g in spec.GROUPS], ["Demo", "Demo1", "Demo2", "Demo3", "Demo4", "Demo5"])
        self.assertEqual([g["model"] for g in spec.GROUPS], [
            "gpt-6.1-sol", "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6.1-terra", "gpt-5.6-luna", "gpt-5.6-luna",
        ])
        self.assertEqual([g["use_skill"] for g in spec.GROUPS], [True, False, True, False, True, False])
        for g in spec.GROUPS:
            self.assertEqual(g["project"], f"E:/Test/{g['id']}/AITest")
        self.assertEqual(spec.MAX_ACTIVE_SECONDS, 86400)
        self.assertEqual(spec.MAX_TEAM_AGENTS, 3)
        self.assertEqual(spec.REASONING_EFFORT, "high")
        self.assertEqual(spec.SKILL_VERSION, "5.2.2")
        self.assertEqual(spec.UNITY_VERSION, "2022.3.62f2")

    def test_frozen_document_and_prompts_have_exact_public_requirements(self):
        document = _path.with_name("BENCHMARK_B1.md").read_text(encoding="utf-8")
        common = spec.common_prompt()
        audit = spec.audit_prompt("ANONYMOUS_SNAPSHOT", "EVIDENCE")
        for c in spec.CHECKS:
            line = f"- **{c['id']}** {c['title']}"
            for text in (document, common, audit):
                self.assertEqual(text.count(line), 1)
        for group in spec.GROUPS:
            self.assertNotIn(group["project"], common)
        self.assertIn("max_retries=3", spec.treatment_prompt(True, "SKILL_PATH"))
        self.assertIn("SKILL_PATH", spec.treatment_prompt(True, "SKILL_PATH"))
        self.assertNotIn("SKILL_PATH", spec.treatment_prompt(False, "SKILL_PATH"))


if __name__ == "__main__":
    unittest.main()
