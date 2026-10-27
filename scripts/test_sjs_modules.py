"""Opt-in module tests; no live Lark service or user session data."""
from __future__ import annotations

import builtins
import copy
import json
import sqlite3
from pathlib import Path
from unittest import mock

import sjs_estimates as estimates
import sjs_feishu as feishu
import sjs_host_time as timing
import sjs_store as store
from test_sjs_v5 import WorkflowCase, assessment, create


def host_records(complete=True, all_wait=False):
    rows = [{"thread_id": "thread", "turn_id": "turn", "kind": "task_started", "started_at": "2026-01-01T10:00:00Z"},
            {"thread_id": "thread", "turn_id": "turn", "kind": "human_wait", "started_at": "2026-01-01T10:00:00Z" if all_wait else "2026-01-01T10:02:00Z", "completed_at": "2026-01-01T10:10:00Z" if all_wait else "2026-01-01T10:05:00Z"}]
    if complete:
        rows.append({"thread_id": "thread", "turn_id": "turn", "kind": "task_complete", "started_at": "2026-01-01T10:00:00Z", "completed_at": "2026-01-01T10:10:00Z", "duration_ms": 600000})
    return rows


class EstimateTests(WorkflowCase):
    def setup_module(self):
        self.init(modules={"estimates": True}, estimates={"library": ".serial-job-session/estimates.jsonl"})
        self.apply(create())

    def sample(self, complete=True):
        return {"op": "timing", "task_id": "a", "sample": {"id": "turn-time", "source": "host", "thread_id": "thread", "turn_id": "turn", "records": host_records(complete), "host_started_at": "2026-01-01T10:00:00Z"}}

    def test_untracked_is_nonblocking_and_excluded(self):
        self.setup_module()
        self.apply({"op": "timing", "task_id": "a", "sample": {"id": "missing", "reason": "host unavailable"}})
        self.assess()
        self.start()
        self.finish()
        report = estimates.stats(self.root, self.state(), append_library=True)
        self.assertEqual(report["library_appended"], 0)
        self.assertEqual(report["measurements"][0]["timing"], "untracked")

    def test_host_reconciliation_wait_and_library_are_idempotent(self):
        self.setup_module()
        self.apply({"op": "estimate", "task_id": "a", "hours": .2, "source": "initial expert estimate"}, self.sample(False))
        self.assertIsNone(estimates.task_measurement(self.state()["tasks"]["a"])["ai_net_hours"])
        self.apply(self.sample(True))
        self.assess()
        self.start()
        self.finish()
        report = estimates.stats(self.root, self.state(), append_library=True)
        row = report["measurements"][0]
        self.assertAlmostEqual(row["ai_net_hours"], 420000 / 3600000)
        self.assertAlmostEqual(row["business_wait_hours"], .05)
        self.assertEqual(report["library_appended"], 1)
        self.assertEqual(estimates.stats(self.root, self.state(), append_library=True)["library_appended"], 0)

    def test_all_wait_is_zero_active_effort(self):
        data = timing.resolve_timing(host_records(all_wait=True), thread_id="thread", turn_id="turn", host_started_at="2026-01-01T10:00:00Z")
        self.assertEqual(data["active_work_ms"], 0)
        self.assertEqual(data["business_wait_ms"], 600000)

    def test_overlapping_manual_intervals_are_unioned_across_samples(self):
        self.setup_module()
        for key, start, end in (("one", "10:00", "10:10"), ("two", "10:05", "10:15")):
            self.apply({"op": "timing", "task_id": "a", "sample": {"id": key, "source": "manual", "evidence": ["user-record"],
                       "active_intervals": [["2026-01-01T" + start + ":00Z", "2026-01-01T" + end + ":00Z"]]}})
        self.assertAlmostEqual(estimates.task_measurement(self.state()["tasks"]["a"])["ai_net_hours"], .25)

    def test_missing_start_is_not_a_calibration_sample(self):
        sample = estimates.normalize_sample({"id": "x", "source": "host", "thread_id": "thread", "turn_id": "turn",
                 "records": [{"thread_id": "thread", "turn_id": "turn", "kind": "task_complete", "duration_ms": 10}]})
        self.assertFalse(sample["count_for_calibration"])

    def test_same_host_turn_cannot_be_counted_twice(self):
        self.setup_module()
        self.apply(self.sample())
        op = self.sample()
        op["sample"]["id"] = "another-id"
        self.error("DUPLICATE_TIMING_CONTEXT", lambda: self.apply(op))

    def test_forecast_does_not_rewrite_initial_estimate(self):
        self.setup_module()
        self.apply({"op": "estimate", "task_id": "a", "hours": 1, "source": "initial"},
                   {"op": "estimate", "task_id": "a", "hours": 2, "source": "new scope forecast"})
        row = estimates.task_measurement(self.state()["tasks"]["a"])
        self.assertEqual(row["estimate_hours"], 1)
        self.assertEqual(row["forecast_hours"], 2)

    def test_host_sqlite_is_read_only_and_imported_events_are_excluded(self):
        db = self.base / "history with spaces.sqlite"
        con = sqlite3.connect(db)
        con.executescript("create table thread_turns(thread_id text,turn_id text,status text,started_at integer,completed_at integer,duration_ms integer);"
                          "insert into thread_turns values ('thread','turn','accepted',1767261600000,1767262200000,600000);")
        con.commit()
        con.close()
        before = db.read_bytes()
        rows = timing.read_thread_history(db, thread_id="thread", turn_id="turn")
        self.assertTrue(rows)
        self.assertEqual(before, db.read_bytes())
        rollout = self.base / "rollout.jsonl"
        rollout.write_text(json.dumps({"source": "external-import", "type": "event_msg", "payload": {"type": "task_complete", "thread_id": "thread", "turn_id": "turn", "duration_ms": 999}}) + "\n")
        self.assertEqual(timing.read_rollout_events(rollout, thread_id="thread", turn_id="turn"), [])


class FakeLark:
    def __init__(self):
        self.calls, self.rows = [], []
        self.create_count, self.upserts = 0, 0
        self.offline, self.fail_create, self.fail_update = False, False, False
        self.paged = False

    def __call__(self, args, *, cwd):
        self.calls.append(args)
        if self.offline:
            raise RuntimeError("offline")
        action = args[1]
        if action == "status":
            return {"ok": True}
        if action == "+node-create":
            self.create_count += 1
            if self.fail_create:
                raise TimeoutError("outcome unknown")
            return {"data": {"node": {"obj_token": "base", "node_token": "wiki", "obj_type": "bitable"}}}
        if action == "+node-get":
            return {"data": {"node": {"obj_token": "base", "obj_type": "bitable"}}}
        if action == "+table-list":
            return {"data": {"items": [{"table_id": "table"}]}}
        if action == "+field-list":
            return {"data": {"items": [{"field_name": name} for name in feishu.FIELDS.values()]}}
        if action == "+record-list":
            if self.paged and len(self.rows) > 1:
                page = int(args[args.index("--page-token") + 1]) if "--page-token" in args else 0
                return {"data": {"items": copy.deepcopy(self.rows[page:page+1]), "has_more": page + 1 < len(self.rows), "page_token": str(page+1)}}
            return {"data": {"items": copy.deepcopy(self.rows)}}
        if action == "+record-upsert":
            if self.fail_update:
                raise RuntimeError("temporary update failure")
            self.upserts += 1
            fields = json.loads((Path(cwd) / "payload.json").read_text(encoding="utf-8"))["fields"]
            rid = args[args.index("--record-id") + 1] if "--record-id" in args else "rec" + str(len(self.rows))
            row = next((r for r in self.rows if r["record_id"] == rid), None)
            if row:
                row["fields"].update(fields)
            else:
                self.rows.append({"record_id": rid, "fields": fields})
            return {"data": {"record_id": rid}}
        if action == "+field-create":
            return {"ok": True}
        raise AssertionError(args)


class FeishuTests(WorkflowCase):
    def setup_module(self, mode="bind"):
        cfg = {"mode": "bind", "base_token": "base", "table_id": "table"} if mode == "bind" else {"mode": "create", "title": "Tasks"}
        self.init(modules={"feishu": True}, feishu=cfg)
        self.remote = FakeLark()
        self.apply(create())

    def test_disabled_module_has_no_transport_calls(self):
        self.init()
        remote = FakeLark()
        self.error("FEISHU_DISABLED", lambda: feishu.sync(self.root, invoke=remote))
        self.assertEqual(remote.calls, [])

    def test_dry_run_has_no_network_or_local_mutations(self):
        self.setup_module()
        before = self.state()["revision"]
        self.assertTrue(feishu.sync(self.root, dry_run=True, invoke=self.remote)["ok"])
        self.assertEqual(self.remote.calls, [])
        self.assertEqual(before, self.state()["revision"])

    def test_binding_and_task_upsert_are_idempotent_and_read_back(self):
        self.setup_module()
        self.assertTrue(feishu.sync(self.root, invoke=self.remote)["ok"])
        self.assertEqual(len(self.remote.rows), 1)
        self.assertEqual(self.remote.rows[0]["fields"]["状态"], "todo")
        self.assertTrue(feishu.sync(self.root, invoke=self.remote)["ok"])
        self.assertEqual(self.remote.upserts, 1)
        self.assess()
        self.start()
        self.finish()
        self.assertTrue(feishu.sync(self.root, invoke=self.remote)["ok"])
        self.assertEqual(len(self.remote.rows), 1)
        self.assertEqual(self.remote.rows[0]["fields"]["状态"], "done")

    def test_sync_failure_does_not_undo_local_acceptance(self):
        self.setup_module()
        self.assess()
        self.start()
        self.finish()
        self.remote.offline = True
        self.assertFalse(feishu.sync(self.root, invoke=self.remote)["ok"])
        self.assertEqual(self.state()["tasks"]["a"]["status"], "accepted")
        self.remote.offline = False
        self.assertTrue(feishu.sync(self.root, invoke=self.remote)["ok"])

    def test_create_requires_explicit_provision_and_uncertain_outcome_is_not_repeated(self):
        self.setup_module("create")
        self.assertFalse(feishu.sync(self.root, invoke=self.remote)["ok"])
        self.assertEqual(self.remote.create_count, 0)
        self.remote.fail_create = True
        self.assertFalse(feishu.sync(self.root, event="provision", invoke=self.remote)["ok"])
        self.assertEqual(self.state()["sync"]["provision_status"], "unknown")
        self.remote.fail_create = False
        self.assertFalse(feishu.sync(self.root, event="provision", invoke=self.remote)["ok"])
        self.assertEqual(self.remote.create_count, 1)

    def test_create_saves_binding_then_flushes_tasks(self):
        self.setup_module("create")
        self.assertTrue(feishu.sync(self.root, event="provision", invoke=self.remote)["ok"])
        self.assertEqual(self.remote.create_count, 1)
        self.assertTrue(feishu.sync(self.root, invoke=self.remote)["ok"])
        self.assertEqual(len(self.remote.rows), 1)

    def test_existing_record_update_failure_can_be_retried(self):
        self.setup_module()
        feishu.sync(self.root, invoke=self.remote)
        self.assess()
        self.start()
        self.remote.fail_update = True
        self.assertFalse(feishu.sync(self.root, invoke=self.remote)["ok"])
        self.remote.fail_update = False
        self.assertTrue(feishu.sync(self.root, invoke=self.remote)["ok"])
        self.assertEqual(len(self.remote.rows), 1)

    def test_pagination_does_not_duplicate_existing_rows(self):
        self.setup_module()
        self.apply(create("b"))
        feishu.sync(self.root, invoke=self.remote)
        self.remote.paged = True
        self.assess("b")
        self.start("b")
        self.assertTrue(feishu.sync(self.root, invoke=self.remote)["ok"])
        self.assertEqual(len(self.remote.rows), 2)
        self.assertTrue(any("--page-token" in args for args in self.remote.calls))


class OptionalLoadingTests(WorkflowCase):
    def test_core_does_not_import_optional_modules(self):
        original = builtins.__import__
        def guarded(name, *args, **kwargs):
            if name in {"sjs_estimates", "sjs_host_time", "sjs_feishu"}:
                raise AssertionError("optional module imported by core")
            return original(name, *args, **kwargs)
        with mock.patch("builtins.__import__", guarded):
            self.init()
            self.apply(create())
            self.assess()
            self.start()
            self.finish()
            self.error("ESTIMATES_DISABLED", lambda: self.apply({"op": "timing", "task_id": "a", "sample": {"id": "none"}}))
