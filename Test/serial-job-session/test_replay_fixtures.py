#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import diagnose_replay  # noqa: E402

LIVE_ALLOWED_BLOCKING = set()
BLOCKING = {
    "STALE_DEPENDENCY",
    "REJECTED_IN_SUCCESS_SAMPLE",
    "LIBRARY_STALE_REVISION",
    "PLAN_ROLLUP_STALE",
    "POST_CLOSE_INCOMPLETE",
    "MENU_TABLE_UNPARSED",
}


class ReplayFixtureTests(unittest.TestCase):
    def test_catalog_known_defects(self):
        catalog = ROOT / "fixtures" / "baseline-2.2.json"
        data = json.loads(catalog.read_text(encoding="utf-8"))
        ids = {item["id"] for item in data["known_defects"]}
        self.assertIn("stale_dependency_70", ids)
        self.assertIn("missing_library_119_126", ids)
        self.assertIn("menu_table_unparsed", ids)

    def test_negative_snapshots_match_goldens(self):
        snaps = ROOT / "fixtures" / "snapshots"
        goldens = ROOT / "fixtures" / "goldens"
        reports = []
        for snapshot in sorted(p for p in snaps.iterdir() if p.is_dir()):
            report = diagnose_replay.replay_one(snapshot)
            reports.append(report)
            golden_path = goldens / (report["id"] + ".json")
            self.assertTrue(golden_path.is_file(), golden_path)
            golden = json.loads(golden_path.read_text(encoding="utf-8"))
            self.assertEqual(golden["expected_codes"], report["expected_codes"], report)
            self.assertEqual(golden["observed_codes"], report["observed_codes"], report)
            self.assertTrue(report["ok"], report)
        blocking_negs = [
            item for item in reports if item["id"].startswith("neg-")
        ]
        self.assertEqual(len(blocking_negs), 6)
        for item in blocking_negs:
            self.assertTrue(set(item["expected_codes"]) & BLOCKING, item)

    def test_live_min_has_no_unexpected_blocking(self):
        snapshot = ROOT / "fixtures" / "snapshots" / "live-min"
        report = diagnose_replay.replay_one(snapshot)
        blocking = set(report["observed_codes"]) & BLOCKING
        self.assertEqual(blocking, LIVE_ALLOWED_BLOCKING, report)


if __name__ == "__main__":
    unittest.main()
