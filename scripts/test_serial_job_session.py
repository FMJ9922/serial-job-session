#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path

import estimate_stats
import check_skill_integrity
import instantiate_domain
import runtime_guard
import validate_domain_pack


class EstimateStatsTests(unittest.TestCase):
    def test_p90_hidden_before_five_samples(self):
        rows = [
            estimate_stats.Row(
                job_id=str(i),
                shape="1",
                faces=["code"],
                estimate=1.0,
                ai_net=float(i),
                variance=float(i),
                status="accepted",
            )
            for i in range(1, 5)
        ]
        summary = estimate_stats.summarize(rows)
        self.assertFalse(summary["distribution_ready"])
        self.assertIsNone(summary["ai_net_p90"])

    def test_p90_available_at_five_samples(self):
        rows = [
            estimate_stats.Row(
                job_id=str(i),
                shape="1",
                faces=["code"],
                estimate=1.0,
                ai_net=float(i),
                variance=float(i),
                status="accepted",
            )
            for i in range(1, 6)
        ]
        summary = estimate_stats.summarize(rows)
        self.assertTrue(summary["distribution_ready"])
        self.assertIsNotNone(summary["ai_net_p90"])

    def test_backfill_requires_explicit_valid_map(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "mapping.json"
            path.write_text(json.dumps({"12": 0.5}), encoding="utf-8")
            self.assertEqual(estimate_stats.load_backfill_map(path), {"12": 0.5})

    def test_backfilled_ai_net_is_never_counted_as_main_sample(self):
        row = estimate_stats.Row(
            job_id="legacy",
            shape="12",
            faces=["code"],
            estimate=0.5,
            estimate_source="backfill",
            ai_net=1.0,
            variance=2.0,
            status="accepted",
        )
        stats = estimate_stats.build_stats([row])
        self.assertEqual(stats["overall"]["n"], 0)
        self.assertEqual(stats["backfilled"]["n"], 1)


def _light(**overrides):
    data = {
        "schema": "serial-job-session-light-workflow/v1",
        "parent_job_id": "parent",
        "revision": 1,
        "max_depth": 1,
        "max_nodes": 4,
        "execution_root": "output/parent/runtime",
        "nodes": [
            {
                "id": "n1",
                "role": "research",
                "writes": [],
                "readonly": True,
                "required": True,
                "status": "completed",
            }
        ],
    }
    data.update(overrides)
    return data


_MANAGED_GRANT = {
    "schema": "serial-job-session-managed-run/v1",
    "run_id": "run-test",
    "route_key": "协议进化",
    "preset": "managed",
    "max_jobs": 3,
    "deadline": None,
    "max_retries": 2,
    "max_parallel": 1,
    "agent_budget": 0,
    "isolate_writes": True,
    "count_human_wait_offhours": False,
    "high_risk_confirmed": False,
    "drain": False,
    "risk_ceiling": "high",
    "human_only_policy": "stop",
    "human_accept_policy": "stop",
    "irreversible_policy": "require_confirm",
    "reviewer_policy": {"low": "skip", "medium": "required", "high": "required"},
    "allowlist": {
        "shapes": [],
        "phases": [],
        "faces": [],
        "paths": [],
        "tools": [],
        "filters": [],
    },
    "stop_on": [
        "dependency_ambiguous",
        "split_required",
        "human_only",
        "probe_failed",
        "over_grant",
        "lease_conflict",
        "budget_exhausted",
        "consecutive_failures",
    ],
}


PLAN_TEMPLATE = """# demo · PLAN

```yaml
plan_status: %(plan_status)s
plan_version: 1
estimated_at: 2026-09-02T09:00:00+08:00
confirmed_at: 2026-09-02T09:05:00+08:00
library_ref: []
```

## 口径声明

AI 净耗时 ≠ 人力工时。

## 阶段

| 阶段 | 收口判据（二值） | 预计区间 h | unc | 依据 | 已拆卡编号 | 状态 |
|---|---|---|---|---|---|---|
| P1 | 导入器对 3 个样本全绿 | %(span)s | %(unc)s | fresh | %(shapes)s | in_progress |

## 粗估依据

- P1：按三个样本各一遍算。

<!-- AUTO:PLAN-ROLLUP -->
_尚未生成。_
<!-- /AUTO:PLAN-ROLLUP -->
"""


def write_plan(
    root: Path,
    plan_status: str = "confirmed",
    span: str = "1–3",
    unc: str = "med",
    shapes: str = "1",
) -> Path:
    path = root / "PLAN.md"
    path.write_text(
        PLAN_TEMPLATE
        % {"plan_status": plan_status, "span": span, "unc": unc, "shapes": shapes},
        encoding="utf-8",
    )
    return path


class PlanRollupTests(unittest.TestCase):
    def rows(self):
        return [
            estimate_stats.Row(
                job_id="2026-09-02_demo",
                shape="1",
                estimate_group="schema-check",
                faces=["code"],
                estimate=1.0,
                estimate_source="menu",
                ai_net=0.5,
                elapsed=1.0,
                variance=0.5,
                status="accepted",
                ended_at="2026-09-02T10:31:00+08:00",
            )
        ]

    def test_range_parser_accepts_dash_tilde_and_single_value(self):
        self.assertEqual(estimate_stats.parse_range_hours("2–5"), (2.0, 5.0))
        self.assertEqual(estimate_stats.parse_range_hours("2-5"), (2.0, 5.0))
        self.assertEqual(estimate_stats.parse_range_hours("2 ~ 5"), (2.0, 5.0))
        self.assertEqual(estimate_stats.parse_range_hours("1.5"), (1.5, 1.5))
        self.assertEqual(estimate_stats.parse_range_hours("30m–2h"), (0.5, 2.0))
        self.assertEqual(estimate_stats.parse_range_hours("—"), (None, None))

    def test_rollup_maps_actuals_to_phase_and_subtracts_remaining(self):
        with tempfile.TemporaryDirectory() as temp:
            plan = write_plan(Path(temp))
            meta, phases, errors = estimate_stats.parse_plan(plan)
            self.assertEqual(errors, [])
            self.assertEqual(len(phases), 1)
            self.assertEqual(phases[0].shapes, ["1"])
            rollup = estimate_stats.build_plan_rollup(meta, phases, self.rows())
            phase = rollup["phases"][0]
            self.assertEqual(phase["cards"], 1)
            self.assertEqual(phase["net_actual"], 0.5)
            self.assertEqual(phase["wall_actual"], 1.0)
            self.assertEqual((phase["remaining_low"], phase["remaining_high"]), (0.5, 2.5))
            self.assertEqual((rollup["total_net_low"], rollup["total_net_high"]), (1.0, 3.0))
            self.assertEqual(rollup["orphan_cards"], [])

    def test_rollup_reports_cards_outside_every_phase(self):
        with tempfile.TemporaryDirectory() as temp:
            plan = write_plan(Path(temp), shapes="9")
            meta, phases, _ = estimate_stats.parse_plan(plan)
            rollup = estimate_stats.build_plan_rollup(meta, phases, self.rows())
            self.assertEqual(rollup["orphan_cards"], ["2026-09-02_demo"])
            self.assertEqual(rollup["phases"][0]["cards"], 0)

    def test_inflation_needs_three_samples(self):
        with tempfile.TemporaryDirectory() as temp:
            plan = write_plan(Path(temp))
            meta, phases, _ = estimate_stats.parse_plan(plan)
            self.assertIsNone(estimate_stats.build_plan_rollup(meta, phases, self.rows())["inflation"])
            rows = self.rows() * 3
            self.assertEqual(
                estimate_stats.build_plan_rollup(meta, phases, rows)["inflation"], 2.0
            )

    def test_rollup_block_is_written_between_plan_markers(self):
        with tempfile.TemporaryDirectory() as temp:
            plan = write_plan(Path(temp))
            meta, phases, _ = estimate_stats.parse_plan(plan)
            block = estimate_stats.render_plan_block(
                estimate_stats.build_plan_rollup(meta, phases, self.rows())
            )
            wrote = estimate_stats.write_block(
                plan,
                block,
                dry_run=False,
                begin=estimate_stats.PLAN_AUTO_BEGIN,
                end=estimate_stats.PLAN_AUTO_END,
            )
            text = plan.read_text(encoding="utf-8")
            self.assertTrue(wrote)
            self.assertIn("AI 净耗时 ≠ 人力工时", text)
            self.assertNotIn("_尚未生成。_", text)

    def test_unconfirmed_plan_block_says_it_is_not_a_commitment(self):
        with tempfile.TemporaryDirectory() as temp:
            plan = write_plan(Path(temp), plan_status="draft")
            meta, phases, _ = estimate_stats.parse_plan(plan)
            block = estimate_stats.render_plan_block(
                estimate_stats.build_plan_rollup(meta, phases, self.rows())
            )
            self.assertIn("尚未经人确认", block)


class EstimateLibraryTests(unittest.TestCase):
    instance = {"instance_id": "inst-1", "domain_name": "demo"}

    def rows(self):
        return [
            estimate_stats.Row(
                job_id="job-a",
                shape="1",
                estimate_group="ui-assembly",
                faces=["code"],
                estimate=1.0,
                estimate_source="menu",
                ai_net=1.5,
                elapsed=2.0,
                variance=1.5,
                status="accepted",
                ended_at="2026-09-02T10:31:00+08:00",
            )
        ]

    def test_append_is_idempotent_per_instance_and_job(self):
        with tempfile.TemporaryDirectory() as temp:
            library = Path(temp) / ".serial-job-session" / "estimate-library.jsonl"
            records = estimate_stats.build_library_records(self.rows(), self.instance)
            first_new, first_skipped = estimate_stats.append_library(library, records, False)
            second_new, second_skipped = estimate_stats.append_library(library, records, False)
            self.assertEqual((first_new, first_skipped), (["job-a"], []))
            self.assertEqual((second_new, second_skipped), ([], ["job-a"]))
            self.assertEqual(len(library.read_text(encoding="utf-8").strip().splitlines()), 1)

    def test_library_record_carries_no_business_content(self):
        record = estimate_stats.build_library_records(self.rows(), self.instance)[0]
        self.assertEqual(record["schema"], estimate_stats.LIBRARY_SCHEMA)
        for key in estimate_stats.LIBRARY_FIELDS:
            self.assertIn(key, record)
        allowed = set(estimate_stats.LIBRARY_FIELDS) | {
            "schema",
            "instance_id",
            "domain",
            "job_id",
            "appended_at",
            "event",
            "closure_revision",
            "closed_at",
        }
        self.assertEqual(set(record) - allowed, set())

    def test_untracked_net_never_enters_library(self):
        rows = self.rows()
        rows[0].ai_net = None
        self.assertEqual(estimate_stats.build_library_records(rows, self.instance), [])

    def test_library_stats_reuse_main_distribution_rules(self):
        with tempfile.TemporaryDirectory() as temp:
            library = Path(temp) / "estimate-library.jsonl"
            rows = []
            for index in range(1, 4):
                row = self.rows()[0]
                row.job_id = "job-%d" % index
                rows.append(row)
            estimate_stats.append_library(
                library, estimate_stats.build_library_records(rows, self.instance), False
            )
            records, errors = estimate_stats.read_library(library)
            self.assertEqual(errors, [])
            stats = estimate_stats.build_stats(estimate_stats.library_rows(records, "ui-assembly"))
            self.assertEqual(stats["overall"]["n"], 3)
            self.assertFalse(stats["overall"]["distribution_ready"])
            self.assertEqual(
                estimate_stats.library_rows(records, "no-such-group"), []
            )

    def test_broken_line_does_not_discard_other_records(self):
        with tempfile.TemporaryDirectory() as temp:
            library = Path(temp) / "estimate-library.jsonl"
            library.write_text(
                '{"instance_id": "a", "job_id": "1"}\nnot-json\n', encoding="utf-8"
            )
            records, errors = estimate_stats.read_library(library)
            self.assertEqual(len(records), 1)
            self.assertEqual(len(errors), 1)


class ValidatorTests(unittest.TestCase):
    def make_job(self, directory: Path, nested_faces: bool = False) -> Path:
        faces = "\n  - code" if nested_faces else "[code]"
        text = """# 任务卡

```yaml
id: 2026-09-02_demo
shape: 1
estimate_group: schema-check
depends_on: []
faces: %s
estimate: 1
estimate_source: menu
actor: agent
human_accept: none
gate: true
negative_fixtures: [missing-field]
expected_live_failures: []
ruler_ok: true
status: accepted
attempt: 1
started_at: 2026-09-02T10:00:00+08:00
active_started_at: 2026-09-02T10:00:00+08:00
active_ended_at: 2026-09-02T10:30:00+08:00
ended_at: 2026-09-02T10:31:00+08:00
elapsed: 31m
ai_net: 0.5h
variance: 0.5
```

## 目标
## 依赖门禁
## 下游接口
## 估分依据
## 验收方式
## 产物路径
## live
## 执行区间
## 拟写入 FACTS
""" % faces
        path = directory / "2026-09-02_demo.md"
        path.write_text(text, encoding="utf-8")
        return path

    def make_domain(
        self,
        root: Path,
        environment_status: str = "locked",
        with_job: bool = True,
        library: str = "auto",
    ) -> None:
        jobs = root / "jobs"
        jobs.mkdir()
        if with_job:
            self.make_job(jobs)
        write_plan(root)
        confirmed_at = "2026-09-02T09:05:00+08:00" if environment_status == "locked" else "null"
        pending = "无待确认项。" if environment_status == "locked" else "| 人眼验收面 | 仅观感 | 策略项 | 待确认 |"
        pending_fields = "[]" if environment_status == "locked" else "[human_accept]"
        (root / "ENV.md").write_text(
            """```yaml
environment_status: %s
environment_version: 1
domain_name: demo
authorized_roots: [C:/demo]
authorized_tools: []
pending_confirmations: %s
discovered_at: 2026-09-02T09:00:00+08:00
confirmed_at: %s
```
## 探查口径
verified / inferred / user_policy / unresolved
## 路径
## 探活清单
## 单实例资源
## 控制面写者
## 面（faces）枚举
| `max_faces` | `2` |
| `estimate` 上限 | `8` |
## 待确认
%s
## 初始化记录
已探查 C:/demo
"""
            % (environment_status, pending_fields, confirmed_at, pending),
            encoding="utf-8",
        )
        (root / "CURRENT.md").write_text(
            """```yaml
status: idle
active_job: null
```
## 现场
空闲
""",
            encoding="utf-8",
        )
        (root / "JOB-SHAPES.md").write_text(
            "## 待做\n## 已结档\n## 已作废\n", encoding="utf-8"
        )
        (root / "NORMS.md").write_text(
            "<!-- AUTO-NORMS:BEGIN -->\n<!-- AUTO-NORMS:END -->\n",
            encoding="utf-8",
        )
        (root / "FACTS.md").write_text("# FACTS\n", encoding="utf-8")
        (root / "LOG.md").write_text("# LOG\n", encoding="utf-8")
        manifest = {
            "schema": "serial-job-session-instance/v1",
            "instance_id": "test-instance",
            "domain_name": "demo",
            "protocol_version": instantiate_domain.load_version(),
            "source_skill": "serial-job-session",
            "instance_root": str(root.resolve()),
            "writable_scope": str(root.resolve()),
        }
        if library == "auto":
            manifest["estimate_library"] = str(
                root.resolve() / ".serial-job-session" / "estimate-library.jsonl"
            )
        elif library:
            manifest["estimate_library"] = library
        (root / "INSTANCE.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    def test_healthy_live_gate_can_accept_with_negative_fixture(self):
        with tempfile.TemporaryDirectory() as temp:
            errors, _ = validate_domain_pack.validate_job(self.make_job(Path(temp)))
            self.assertEqual(errors, [])

    def test_nested_task_yaml_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            errors, _ = validate_domain_pack.validate_job(
                self.make_job(Path(temp), nested_faces=True)
            )
            self.assertTrue(any("不允许嵌套" in item for item in errors))

    def test_minimal_complete_domain_pack_is_valid(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_domain(root)
            result = validate_domain_pack.validate_domain(root)
            self.assertTrue(result["ok"], result["errors"])

    def test_draft_environment_requires_explicit_allow_flag(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_domain(root, environment_status="draft", with_job=False)
            blocked = validate_domain_pack.validate_domain(root)
            allowed = validate_domain_pack.validate_domain(root, allow_draft=True)
            self.assertFalse(blocked["ok"])
            self.assertTrue(allowed["ok"], allowed["errors"])
            self.assertTrue(allowed["warnings"])

    def test_instance_creator_copies_template_outside_skill(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "demo-instance"
            created = instantiate_domain.instantiate(target, "demo")
            self.assertEqual(created, target.resolve())
            self.assertTrue((created / "ENV.md").is_file())
            manifest = json.loads((created / "INSTANCE.json").read_text(encoding="utf-8"))
            self.assertEqual(
                instantiate_domain.resolve_declared(manifest["instance_root"], created),
                created,
            )
            self.assertEqual(manifest["domain_name"], "demo")

    def test_instance_creator_rejects_feature_docs_tree(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "feature-docs" / "should-not-instantiate"
            with self.assertRaises(ValueError) as ctx:
                instantiate_domain.instantiate(target, "docs-leak")
            self.assertIn("DOCS_TREE_FORBIDDEN", str(ctx.exception))
            self.assertFalse(target.exists())

    def test_instance_creator_rejects_target_inside_skill(self):
        target = instantiate_domain.SKILL_ROOT / "forbidden-test-instance"
        with self.assertRaises(ValueError):
            instantiate_domain.instantiate(target, "forbidden")
        self.assertFalse(target.exists())

    def test_runtime_guard_accepts_locked_external_instance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_domain(root)
            result = runtime_guard.preflight(root)
            self.assertTrue(result["ok"], result)

    def test_plan_estimate_may_not_become_card_estimate(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_job(Path(temp))
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "estimate_source: menu", "estimate_source: plan"
                ),
                encoding="utf-8",
            )
            errors, _ = validate_domain_pack.validate_job(path)
            self.assertTrue(any("不得为 plan" in item for item in errors), errors)

    def test_unknown_estimate_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_job(Path(temp))
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "estimate_source: menu", "estimate_source: 拍脑袋"
                ),
                encoding="utf-8",
            )
            errors, _ = validate_domain_pack.validate_job(path)
            self.assertTrue(any("非法 estimate_source" in item for item in errors), errors)

    def test_unsplit_phase_written_as_single_point_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            plan = write_plan(Path(temp), span="3", unc="high", shapes="—")
            errors, _ = validate_domain_pack.validate_plan(plan)
            self.assertTrue(any("单点工时" in item for item in errors), errors)

    def test_split_phase_with_low_uncertainty_may_use_single_point(self):
        with tempfile.TemporaryDirectory() as temp:
            plan = write_plan(Path(temp), span="3", unc="low", shapes="1")
            errors, _ = validate_domain_pack.validate_plan(plan)
            self.assertEqual(errors, [])

    def test_confirmed_plan_requires_at_least_one_phase(self):
        with tempfile.TemporaryDirectory() as temp:
            plan = write_plan(Path(temp))
            plan.write_text(
                re.sub(
                    r"^\| P1 .*$",
                    "",
                    plan.read_text(encoding="utf-8"),
                    flags=re.M,
                ),
                encoding="utf-8",
            )
            errors, _ = validate_domain_pack.validate_plan(plan)
            self.assertTrue(any("阶段」表不得为空" in item for item in errors), errors)

    def test_missing_plan_file_blocks_domain(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_domain(root)
            (root / "PLAN.md").unlink()
            result = validate_domain_pack.validate_domain(root)
            self.assertFalse(result["ok"])
            self.assertTrue(any("缺文件：PLAN.md" in item for item in result["errors"]))

    def test_library_path_inside_skill_master_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_domain(
                root,
                library=str(validate_domain_pack.SKILL_ROOT / "estimate-library.jsonl"),
            )
            result = validate_domain_pack.validate_domain(root)
            self.assertFalse(result["ok"])
            self.assertTrue(
                any("estimate_library 位于 skill 母版内" in item for item in result["errors"]),
                result["errors"],
            )

    def test_missing_library_config_only_warns(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_domain(root, library="")
            result = validate_domain_pack.validate_domain(root)
            self.assertTrue(result["ok"], result["errors"])
            self.assertIsNone(result["estimate_library"])
            self.assertTrue(any("estimate_library" in item for item in result["warnings"]))

    def test_relative_library_path_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pack"
            root.mkdir()
            self.make_domain(
                root, library=".serial-job-session/estimate-library.jsonl"
            )
            result = validate_domain_pack.validate_domain(root)
            self.assertTrue(result["ok"], result["errors"])
            self.assertIsNotNone(result["estimate_library"])

    def test_relative_instance_root_matches_current_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "pack"
            root.mkdir()
            self.make_domain(root)
            manifest = json.loads((root / "INSTANCE.json").read_text(encoding="utf-8"))
            manifest["instance_root"] = "pack"
            manifest["writable_scope"] = "pack"
            (root / "INSTANCE.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )
            result = validate_domain_pack.validate_domain(root)
            self.assertTrue(result["ok"], result["errors"])

    def test_instance_creator_records_library_outside_skill(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "demo-instance"
            created = instantiate_domain.instantiate(target, "demo")
            manifest = json.loads((created / "INSTANCE.json").read_text(encoding="utf-8"))
            library = instantiate_domain.resolve_declared(
                manifest["estimate_library"], created
            )
            self.assertTrue((created / "PLAN.md").is_file())
            self.assertEqual(library.name, instantiate_domain.LIBRARY_FILENAME)
            self.assertFalse(
                instantiate_domain.is_within(library, instantiate_domain.SKILL_ROOT.resolve())
            )

    def test_stats_path_guard_recognizes_skill_root(self):
        self.assertTrue(
            estimate_stats.is_within(
                estimate_stats.SKILL_ROOT / "domain-pack-template",
                estimate_stats.SKILL_ROOT,
            )
        )


class ProtocolV3Tests(unittest.TestCase):
    def test_parse_iso_rejects_bare_number(self):
        import calendar_hours

        self.assertIsNone(calendar_hours.parse_iso("52"))

    def test_business_wait_skips_lunch_and_night(self):
        import calendar_hours

        hours = calendar_hours.business_wait_hours(
            "2026-09-03T11:00:00+08:00",
            "2026-09-03T15:00:00+08:00",
        )
        self.assertEqual(hours, 2.5)

    def test_route_standard_command(self):
        import route_parse
        import registry

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".serial-job-session").mkdir()
            registry.atomic_write_json(
                root / ".serial-job-session" / "instances.json",
                {
                    "schema": registry.SCHEMA,
                    "instances": [
                        {
                            "route_key": "demo-domain",
                            "aliases": [],
                            "instance_id": "x",
                            "domain_name": "demo",
                            "instance_root": str(root),
                            "enabled": True,
                        }
                    ],
                },
            )
            parsed = route_parse.parse_command("demo-domain：推进", repo_root=root)
            self.assertTrue(parsed["ok"])
            self.assertEqual(parsed["intent"], "menu")

    def test_bare_push_without_binding_is_no_route_when_empty_registry(self):
        import route_parse
        import registry

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".serial-job-session").mkdir()
            registry.atomic_write_json(
                root / ".serial-job-session" / "instances.json",
                {"schema": registry.SCHEMA, "instances": []},
            )
            parsed = route_parse.parse_command("推进一下", repo_root=root)
            self.assertFalse(parsed["ok"])
            self.assertEqual(parsed["diagnosis"], "NO_ROUTE")

    def test_rejected_excluded_from_main_stats(self):
        rows = [
            estimate_stats.Row(
                job_id="ok",
                shape="1",
                estimate=1.0,
                ai_net=0.2,
                variance=0.2,
                status="accepted",
            ),
            estimate_stats.Row(
                job_id="bad",
                shape="2",
                estimate=1.0,
                ai_net=0.18,
                variance=0.18,
                status="rejected",
            ),
        ]
        stats = estimate_stats.build_stats(rows)
        self.assertEqual(stats["overall"]["n"], 1)
        self.assertEqual(stats["rejected"]["n"], 1)

    def test_library_v2_idempotent_by_revision(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "lib.jsonl"
            rec1 = {
                "schema": estimate_stats.LIBRARY_SCHEMA,
                "instance_id": "i",
                "job_id": "j",
                "closure_revision": 1,
                "status": "rejected",
                "event": "closed",
            }
            rec2 = dict(rec1, closure_revision=2, status="accepted")
            appended, skipped = estimate_stats.append_library(path, [rec1], False)
            self.assertEqual(appended, ["j"])
            appended, skipped = estimate_stats.append_library(path, [rec1], False)
            self.assertEqual(skipped, ["j"])
            appended, _ = estimate_stats.append_library(path, [rec2], False)
            self.assertEqual(appended, ["j"])
            records, _ = estimate_stats.read_library(path)
            latest = estimate_stats.latest_library_records(records)
            self.assertEqual(latest[0]["status"], "accepted")

    def test_reopen_blocked_by_downstream(self):
        import reopen_job
        import close_transaction

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = root / "jobs"
            jobs.mkdir()
            (jobs / "a.md").write_text(
                "```yaml\nid: a\nshape: 1\nstatus: accepted\nattempt: 1\nclosure_revision: 1\ndepends_on: []\n```\n",
                encoding="utf-8",
            )
            (jobs / "b.md").write_text(
                "```yaml\nid: b\nshape: 2\nstatus: running\nattempt: 1\ndepends_on: [1]\n```\n",
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit):
                reopen_job.reopen(root, "a", "漏了负样本", "no", "yes")

    def test_close_reviewer_forbidden(self):
        import close_transaction

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = root / "jobs"
            jobs.mkdir()
            (jobs / "c.md").write_text(
                "```yaml\nid: c\nshape: 1\nstatus: done_pending\nattempt: 1\nclosure_revision: 0\nai_first_pass: 0.1\nai_rework: 0\n```\n",
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit) as ctx:
                close_transaction.prepare(root, "c", "accepted", "cmd", "reviewer")
            self.assertIn("REVIEWER_MUST_NOT_CLOSE", str(ctx.exception))

    def test_close_worker_forbidden(self):
        import close_transaction

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = root / "jobs"
            jobs.mkdir()
            (jobs / "c.md").write_text(
                "```yaml\nid: c\nshape: 1\nstatus: running\nattempt: 1\nclosure_revision: 0\nai_first_pass: 0.1\nai_rework: 0\n```\n",
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit) as ctx:
                close_transaction.prepare(root, "c", "accepted", "cmd", "worker")
            self.assertIn("WORKER_MUST_NOT_CLOSE", str(ctx.exception))

    def test_unfinished_tx_blocks_other_prepare(self):
        import close_transaction

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = root / "jobs"
            jobs.mkdir()
            yaml = "```yaml\nid: %s\nshape: 1\nstatus: running\nattempt: 1\nclosure_revision: 0\nai_first_pass: 0.1\nai_rework: 0\n```\n"
            (jobs / "a.md").write_text(yaml % "a", encoding="utf-8")
            (jobs / "b.md").write_text(yaml.replace("shape: 1", "shape: 2") % "b", encoding="utf-8")
            close_transaction.prepare(root, "a", "accepted", "cmd-a", "control_owner")
            with self.assertRaises(SystemExit) as ctx:
                close_transaction.prepare(root, "b", "accepted", "cmd-b", "control_owner")
            self.assertIn("UNFINISHED_TRANSACTION", str(ctx.exception))

    def test_commit_does_not_mangle_awaiting_heading(self):
        import close_transaction

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = root / "jobs"
            jobs.mkdir()
            (jobs / "c.md").write_text(
                "```yaml\nid: c\nshape: 1\nstatus: running\nattempt: 1\nclosure_revision: 0\nestimate: 1\nai_first_pass: 0.1\nai_rework: 0\nwall_elapsed: 0.1\n```\n",
                encoding="utf-8",
            )
            (root / "CURRENT.md").write_text(
                "```yaml\nstatus: running\nactive_job: c\njob_id: c\n```\n\n## 现场\nrunning 中\n\n## 待验收稿（仅 awaiting_acceptance 时有）\n无。\n",
                encoding="utf-8",
            )
            (root / "LOG.md").write_text("# LOG\n", encoding="utf-8")
            close_transaction.prepare(root, "c", "accepted", "cmd", "control_owner")
            close_transaction.commit(root, "cmd")
            again = close_transaction.commit(root, "cmd")
            self.assertEqual(again["phase"], "committed")
            text = (root / "CURRENT.md").read_text(encoding="utf-8")
            self.assertIn("awaiting_acceptance", text)
            self.assertIn("status: idle", text)
            self.assertNotIn("`running`", text.replace("awaiting_acceptance", ""))

    def test_post_close_skips_closed_detail_lines(self):
        import post_close

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = root / "jobs"
            jobs.mkdir()
            (jobs / "old.md").write_text(
                "```yaml\nid: old\nshape: 1\nstatus: accepted\n```\n",
                encoding="utf-8",
            )
            (root / "JOB-SHAPES.md").write_text(
                "## 待做\n\n"
                "| 编号 | 目标 | 依赖 |\n|---|---|---|\n"
                "| 4 | Close | 1 |\n\n"
                "## 已结档\n\n"
                "1. **冻结基线、建立真实回放夹具**（依赖：无）\n"
                "4. **Close 事务**（依赖：3）\n",
                encoding="utf-8",
            )
            rec = post_close.recommend(root)
            self.assertEqual(rec["preferred"], 4)
            self.assertEqual(rec["pending_count"], 1)
            self.assertTrue(all(c["shape"] != 1 for c in rec["candidates"]))

    def test_post_close_plan_interval_totals(self):
        import post_close

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "PLAN.md").write_text(
                "```yaml\nbaseline_total: 39-64\nforecast_total: 49-81\n```\n",
                encoding="utf-8",
            )
            rec = post_close.recommend(root)
            self.assertEqual(rec["forecast_total"], "49-81")
            self.assertEqual(rec["baseline_total"], "39-64")
            self.assertEqual(rec["remaining_delivery_workhours"], "49-81")
            self.assertEqual(rec["scope_variance"], 13.5)

    def test_post_close_plan_numeric_totals(self):
        import post_close

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "PLAN.md").write_text(
                "```yaml\nbaseline_total: 10\nforecast_total: 12.5\n```\n",
                encoding="utf-8",
            )
            rec = post_close.recommend(root)
            self.assertEqual(rec["scope_variance"], 2.5)

    def test_post_close_plan_unparsed_totals_are_null(self):
        import post_close

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "PLAN.md").write_text(
                "```yaml\nbaseline_total: TBD\nforecast_total: 49-81\n```\n",
                encoding="utf-8",
            )
            rec = post_close.recommend(root)
            self.assertIsNone(rec["scope_variance"])
            self.assertEqual(rec["forecast_total"], "49-81")

    def test_post_close_main_survives_interval_and_drains_outbox(self):
        import io
        from contextlib import redirect_stdout
        import close_transaction
        import post_close

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "PLAN.md").write_text(
                "```yaml\nbaseline_total: 39-64\nforecast_total: 49-81\n```\n",
                encoding="utf-8",
            )
            (root / "JOB-SHAPES.md").write_text("## 待做\n\n", encoding="utf-8")
            box = {
                "schema": "serial-job-session-outbox/v1",
                "pending": ["progress_snapshot"],
                "done": [],
            }
            close_transaction.tx_dir(root).mkdir(parents=True)
            (close_transaction.tx_dir(root) / "outbox.json").write_text(
                json.dumps(box), encoding="utf-8"
            )
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = post_close.main(["--instance", str(root)])
            self.assertEqual(rc, 0)
            payload = json.loads(buf.getvalue())
            self.assertEqual(payload["scope_variance"], 13.5)
            self.assertEqual(payload["outbox"]["pending"], [])
            self.assertIn("progress_snapshot", payload["outbox"]["done"])

    def test_reopen_contract_unknown(self):
        import reopen_job

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = root / "jobs"
            jobs.mkdir()
            (jobs / "a.md").write_text(
                "```yaml\nid: a\nshape: 1\nstatus: accepted\nattempt: 1\nclosure_revision: 1\ndepends_on: []\n```\n",
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit) as ctx:
                reopen_job.reopen(root, "a", "原因", "no", "unknown")
            self.assertIn("CONTRACT_NOT_CONFIRMED", str(ctx.exception))


    def test_light_workflow_rejects_control_write(self):
        import validate_light_workflow

        errs = validate_light_workflow.validate_light_workflow_data(
            _light(
                nodes=[
                    {"id": "n1", "role": "execute", "writes": ["CURRENT.md"], "readonly": False}
                ]
            )
        )
        self.assertIn("WORKFLOW_CONTROL_PLANE_WRITE", errs)

    def test_light_workflow_depth_gt_1(self):
        import validate_light_workflow

        errs = validate_light_workflow.validate_light_workflow_data(_light(max_depth=2))
        self.assertIn("WORKFLOW_DEPTH", errs)

    def test_light_workflow_too_many_nodes(self):
        import validate_light_workflow

        nodes = [{"id": "n%d" % i, "role": "research", "writes": [], "readonly": True} for i in range(5)]
        errs = validate_light_workflow.validate_light_workflow_data(_light(nodes=nodes, max_nodes=4))
        self.assertIn("WORKFLOW_TOO_MANY_NODES", errs)

    def test_light_workflow_child_recurse(self):
        import validate_light_workflow

        errs = validate_light_workflow.validate_light_workflow_data(
            _light(
                nodes=[
                    {
                        "id": "n1",
                        "role": "research",
                        "writes": [],
                        "readonly": True,
                        "nested_agents": ["child"],
                    }
                ]
            )
        )
        self.assertIn("WORKFLOW_CHILD_RECURSE", errs)

    def test_light_workflow_split_required(self):
        import validate_light_workflow

        errs = validate_light_workflow.validate_light_workflow_data(
            _light(
                nodes=[
                    {
                        "id": "n1",
                        "role": "execute",
                        "writes": ["output/a/x.json"],
                        "human_decision": True,
                    }
                ]
            )
        )
        self.assertIn("WORKFLOW_SPLIT_REQUIRED", errs)

    def test_light_workflow_write_overlap(self):
        import validate_light_workflow

        errs = validate_light_workflow.validate_light_workflow_data(
            _light(
                nodes=[
                    {"id": "a", "role": "execute", "writes": ["output/job/runtime/a"]},
                    {"id": "b", "role": "execute", "writes": ["output/job/runtime/a/sub"]},
                ]
            )
        )
        self.assertIn("WORKFLOW_WRITE_OVERLAP", errs)

    def test_light_workflow_shared_editor(self):
        import validate_light_workflow

        errs = validate_light_workflow.validate_light_workflow_data(
            _light(
                nodes=[
                    {"id": "a", "role": "execute", "writes": ["tmp/a"], "resources": ["unity"]},
                    {"id": "b", "role": "execute", "writes": ["tmp/b"], "resources": ["unity"]},
                ]
            )
        )
        self.assertIn("WORKFLOW_SHARED_EDITOR", errs)

    def test_light_workflow_child_close(self):
        import validate_light_workflow

        errs = validate_light_workflow.validate_light_workflow_data(
            _light(
                nodes=[
                    {
                        "id": "n1",
                        "role": "execute",
                        "writes": ["output/x"],
                        "close_requested": True,
                    }
                ]
            )
        )
        self.assertIn("WORKFLOW_CHILD_CLOSE", errs)

    def test_light_workflow_required_incomplete_blocks_close(self):
        import validate_light_workflow

        data = _light(
            nodes=[
                {
                    "id": "n1",
                    "role": "research",
                    "writes": [],
                    "readonly": True,
                    "required": True,
                    "status": "pending",
                }
            ]
        )
        self.assertIn("WORKFLOW_REQUIRED_INCOMPLETE", validate_light_workflow.required_close_blockers(data))
        self.assertFalse(validate_light_workflow.parent_may_close(data, "parent"))

    def test_light_workflow_required_failed_cancelled(self):
        import validate_light_workflow

        failed = _light(
            nodes=[
                {
                    "id": "n1",
                    "role": "research",
                    "writes": [],
                    "readonly": True,
                    "required": True,
                    "status": "failed",
                }
            ]
        )
        cancelled = _light(
            nodes=[
                {
                    "id": "n1",
                    "role": "research",
                    "writes": [],
                    "readonly": True,
                    "required": True,
                    "status": "cancelled",
                }
            ]
        )
        self.assertIn("WORKFLOW_REQUIRED_FAILED", validate_light_workflow.required_close_blockers(failed))
        self.assertIn("WORKFLOW_REQUIRED_CANCELLED", validate_light_workflow.required_close_blockers(cancelled))

    def test_light_workflow_revision_mismatch(self):
        import validate_light_workflow

        errs = validate_light_workflow.validate_light_workflow_data(_light(revision=1), expected_revision=2)
        self.assertIn("WORKFLOW_REVISION_MISMATCH", errs)

    def test_light_workflow_global_reports(self):
        import validate_light_workflow

        errs = validate_light_workflow.validate_light_workflow_data(
            _light(execution_root=".cursor/reports/workflows/demo")
        )
        self.assertIn("WORKFLOW_GLOBAL_REPORTS", errs)

    def test_light_workflow_healthy_parent_may_close(self):
        import validate_light_workflow

        data = _light(
            nodes=[
                {
                    "id": "r",
                    "role": "research",
                    "writes": [],
                    "readonly": True,
                    "required": True,
                    "status": "completed",
                },
                {
                    "id": "e",
                    "role": "execute",
                    "writes": ["output/parent/runtime/shard"],
                    "required": True,
                    "status": "completed",
                },
            ]
        )
        self.assertTrue(validate_light_workflow.parent_may_close(data, "parent", 1))

    def test_close_embedded_missing_manifest(self):
        import close_transaction

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = root / "jobs"
            jobs.mkdir()
            (jobs / "wf.md").write_text(
                "```yaml\nid: wf\nshape: 6\nstatus: done_pending\nattempt: 1\n"
                "closure_revision: 0\nexecution_mode: embedded_workflow\n"
                "gate: false\nai_first_pass: 0.1\nai_rework: 0\n```\n",
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit) as ctx:
                close_transaction.prepare(root, "wf", "accepted", "cmd-wf", "owner")
            self.assertIn("WORKFLOW_MANIFEST_MISSING", str(ctx.exception))

    def test_close_embedded_required_incomplete(self):
        import close_transaction

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = root / "jobs"
            runtime = root / "output" / "wf" / "runtime"
            jobs.mkdir(parents=True)
            runtime.mkdir(parents=True)
            (jobs / "wf.md").write_text(
                "```yaml\nid: wf\nshape: 6\nstatus: done_pending\nattempt: 1\n"
                "closure_revision: 0\nexecution_mode: embedded_workflow\n"
                "workflow_ref: output/wf/runtime/light-workflow.json\n"
                "workflow_revision: 1\ngate: false\nai_first_pass: 0.1\nai_rework: 0\n```\n",
                encoding="utf-8",
            )
            (runtime / "light-workflow.json").write_text(
                json.dumps(
                    _light(
                        parent_job_id="wf",
                        nodes=[
                            {
                                "id": "r",
                                "role": "research",
                                "writes": [],
                                "readonly": True,
                                "required": True,
                                "status": "pending",
                            }
                        ]
                    )
                ),
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit) as ctx:
                close_transaction.prepare(root, "wf", "accepted", "cmd-wf2", "owner")
            self.assertIn("WORKFLOW_REQUIRED_INCOMPLETE", str(ctx.exception))

    def test_managed_high_risk_needs_confirm(self):
        import managed_runner

        result = managed_runner.dispatch_allowed(
            {"sample_n": 0, "allowlist": {}},
            {"faces": ["houdini"], "irreversible": True, "shape": 1},
            [],
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["stop"], "high_risk_unconfirmed")

    def test_managed_cold_start_never_low(self):
        import managed_runner

        risk = managed_runner.classify_risk(
            {"faces": ["measure-only"], "human_accept": "none", "gate": True, "estimate": 0.5},
            sample_n=0,
        )
        self.assertNotEqual(risk, "low")

    def test_managed_lease_conflict_unity(self):
        import managed_runner

        result = managed_runner.dispatch_allowed(
            _MANAGED_GRANT,
            {
                "id": "j1",
                "shape": 5,
                "faces": ["unity"],
                "human_accept": "none",
                "gate": True,
                "estimate": 0.5,
            },
            ["unity"],
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["stop"], "lease_conflict")

    def test_managed_over_grant_shape(self):
        import managed_runner

        grant = dict(_MANAGED_GRANT)
        grant["allowlist"] = dict(grant["allowlist"])
        grant["allowlist"]["shapes"] = [5]
        result = managed_runner.dispatch_allowed(
            grant,
            {"shape": 9, "faces": ["measure-only"], "human_accept": "none", "gate": True, "estimate": 0.5},
            [],
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["stop"], "over_grant")

    def test_managed_grant_missing_route_key_names_field(self):
        import managed_runner

        broken = dict(_MANAGED_GRANT)
        del broken["route_key"]
        errors = managed_runner.validate_grant(broken)
        self.assertIn("MISSING_FIELD:$.route_key", errors)
        result = managed_runner.dispatch_allowed(broken, {"shape": 5, "gate": True}, [])
        self.assertFalse(result["ok"])
        self.assertEqual(result["stop"], "over_grant")
        self.assertTrue(any(err.endswith(".route_key") for err in result["errors"]))

    def test_managed_human_only_and_split_required(self):
        import managed_runner

        human = managed_runner.dispatch_allowed(
            _MANAGED_GRANT,
            {"actor": "human", "shape": 5, "gate": True, "estimate": 0.5},
            [],
        )
        self.assertEqual(human["stop"], "human_only")
        split = managed_runner.dispatch_allowed(
            _MANAGED_GRANT,
            {"split_required": True, "shape": 5, "gate": True, "estimate": 0.5},
            [],
        )
        self.assertEqual(split["stop"], "split_required")

    def test_managed_worker_result_control_write(self):
        import managed_runner

        result = managed_runner.ingest_result(
            {
                "schema": "serial-job-session-worker-result/v1",
                "job_id": "j1",
                "status": "done_pending",
                "close_requested": False,
                "control_writes": [],
                "writes": ["CURRENT.md"],
            }
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["stop"], "over_grant")
        close = managed_runner.ingest_result(
            {
                "schema": "serial-job-session-worker-result/v1",
                "job_id": "j1",
                "status": "done_pending",
                "close_requested": True,
                "control_writes": [],
                "writes": ["jobs/j1.md"],
            }
        )
        self.assertEqual(close["reason"], "WORKER_MUST_NOT_CLOSE")

    def test_managed_drain_and_budget(self):
        import managed_runner

        drained = managed_runner.begin_drain(dict(_MANAGED_GRANT))
        stop = managed_runner.dispatch_allowed(
            drained,
            {"shape": 5, "faces": ["measure-only"], "gate": True, "estimate": 0.5},
            [],
        )
        self.assertEqual(stop["stop"], "drain")
        budget = dict(_MANAGED_GRANT)
        budget["completed_count"] = 3
        spent = managed_runner.dispatch_allowed(
            budget,
            {"shape": 5, "faces": ["measure-only"], "gate": True, "estimate": 0.5},
            [],
        )
        self.assertEqual(spent["stop"], "budget_exhausted")

    def test_managed_reserve_writes_current_lease(self):
        import managed_runner

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "CURRENT.md").write_text(
                "# demo\n\n```yaml\nstatus: idle\ncontrol_mode: manual\nmanaged_run_id: null\nactive_transaction: null\n```\n",
                encoding="utf-8",
            )
            job = {
                "id": "job-a",
                "shape": 5,
                "faces": ["unity"],
                "human_accept": "none",
                "gate": True,
                "estimate": 0.5,
            }
            grant = dict(_MANAGED_GRANT)
            grant["high_risk_confirmed"] = True
            out = managed_runner.reserve(root, grant, job, now="2026-09-03T22:00:00+08:00")
            self.assertTrue(out["ok"], out)
            text = (root / "CURRENT.md").read_text(encoding="utf-8")
            self.assertIn("control_mode: managed", text)
            self.assertIn("managed_run_id: run-test", text)
            self.assertIn("lease_holder: job-a", text)
            self.assertIn("unity", text)

    def test_overnight_human_wait_skips_night(self):
        import managed_runner

        grant = managed_runner.overnight_preset(dict(_MANAGED_GRANT))
        hours = managed_runner.human_wait_hours(
            grant,
            "2026-09-03T19:00:00+08:00",
            "2026-09-04T09:30:00+08:00",
        )
        self.assertEqual(hours, 0.0)


    def test_calibrate_shrinks_with_few_samples(self):
        import calibrate

        out = calibrate.calibrate_estimate(
            1.0,
            [0.2],
            [1.0],
            [0.5, 0.4, 0.6],
            [1.0, 1.0, 1.0],
        )
        self.assertGreater(out["calibrated_hours"], 0.2)
        self.assertLess(out["calibrated_hours"], 1.0)
        self.assertLess(out["shrink_factor"], 1.0)

    def test_interval_reversed_is_red(self):
        import time_ledger

        errors = time_ledger.diagnose_intervals(
            [
                {
                    "label": "attempt 1",
                    "start": "2026-09-03T16:10:00+08:00",
                    "end": "2026-09-03T16:00:00+08:00",
                }
            ]
        )
        self.assertIn("INTERVAL_REVERSED", errors)

    def test_ledger_sum_mismatch_is_red(self):
        import time_ledger

        md = "- attempt 1: 2026-09-03T16:00:00+08:00 ~ 2026-09-03T16:06:00+08:00\n"
        ledger = time_ledger.compute_ledger({"ai_net": 9.9, "estimate": 1}, md)
        self.assertIn("LEDGER_SUM_MISMATCH", ledger["interval_errors"])

    def test_menu_table_parses_crlf(self):
        import menu_parse

        text = (
            "## 待做（推进时只发这一节）\r\n"
            "| 编号 | 目标 | estimate_group | 依赖 | 面 | 预计 |\r\n"
            "|---|---|---|---|---|---|\r\n"
            "| 3 | 校准 | skill-estimate | 2 | python-scripts | 12 |\r\n"
            "\r\n"
            "## 已结档\r\n"
        )
        rows, errors = menu_parse.parse_pending_table(text)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["shape"], 3)
        self.assertEqual(rows[0]["depends_on"], [2])

    def test_menu_cycle_and_dup_shape(self):
        import menu_parse

        rows = [
            {"shape": 3, "depends_on": [4]},
            {"shape": 4, "depends_on": [3]},
            {"shape": 3, "depends_on": [4]},
        ]
        self.assertIn(3, menu_parse.duplicate_shapes(rows))
        self.assertTrue(set(menu_parse.cycle_shapes(rows)) & {3, 4})

    def test_rejected_does_not_cut_remaining(self):
        with tempfile.TemporaryDirectory() as temp:
            plan = write_plan(Path(temp), span="2–4", shapes="1")
            meta, phases, _ = estimate_stats.parse_plan(plan)
            rows = [
                estimate_stats.Row(
                    job_id="ok",
                    shape="1",
                    estimate=1.0,
                    ai_net=1.0,
                    elapsed=1.0,
                    status="accepted",
                ),
                estimate_stats.Row(
                    job_id="bad",
                    shape="1",
                    estimate=1.0,
                    ai_net=9.0,
                    elapsed=9.0,
                    status="rejected",
                ),
            ]
            rollup = estimate_stats.build_plan_rollup(meta, phases, rows)
            phase = rollup["phases"][0]
            self.assertEqual(phase["net_actual"], 1.0)
            self.assertEqual((phase["remaining_low"], phase["remaining_high"]), (1.0, 3.0))

    def test_phase_over_24h_is_warning_not_error(self):
        with tempfile.TemporaryDirectory() as temp:
            plan = write_plan(Path(temp), span="1–30", unc="high", shapes="—")
            errors, warnings = validate_domain_pack.validate_plan(plan)
            self.assertTrue(any("PHASE_OVER_24H" in item for item in warnings), warnings)
            self.assertFalse(any("PHASE_OVER_24H" in item for item in errors), errors)

    def test_shape_multi_phase_is_red(self):
        with tempfile.TemporaryDirectory() as temp:
            plan = write_plan(Path(temp), shapes="1")
            text = plan.read_text(encoding="utf-8")
            extra = "| P2 | 另一阶段 | 2–4 | med | fresh | 1 | pending |\n"
            plan.write_text(text.replace("## 粗估依据", extra + "\n## 粗估依据"), encoding="utf-8")
            errors, _ = validate_domain_pack.validate_plan(plan)
            self.assertTrue(any("SHAPE_MULTI_PHASE" in item for item in errors), errors)

    def test_library_rejected_uses_closed_at_not_accepted_at(self):
        rows = [
            estimate_stats.Row(
                job_id="bad",
                shape="1",
                estimate=1.0,
                ai_net=0.2,
                status="rejected",
                ended_at="2026-09-03T16:00:00+08:00",
            )
        ]
        rec = estimate_stats.build_library_records(rows, {"instance_id": "i", "domain_name": "d"})[0]
        self.assertEqual(rec["closed_at"], "2026-09-03T16:00:00+08:00")
        self.assertIsNone(rec["accepted_at"])

    def test_orchestration_wait_uses_work_windows(self):
        import calendar_hours

        hours = calendar_hours.orchestration_wait_hours(
            "2026-09-03T18:30:00+08:00",
            "2026-09-03T20:00:00+08:00",
        )
        self.assertEqual(hours, 0.5)

    def test_schema_missing_field_names_the_field(self):
        import schema_validate

        errors = schema_validate.validate_named({"shape": 1}, "job.schema.json")
        self.assertTrue(any(err.endswith(".id") for err in errors), errors)
        self.assertIn("MISSING_FIELD:$.id", errors)

    def test_duplicate_route_key_and_alias_conflict(self):
        import registry

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            inst = root / "a"
            inst.mkdir()
            (root / ".serial-job-session").mkdir()
            registry.atomic_write_json(
                root / ".serial-job-session" / "instances.json",
                {
                    "schema": registry.SCHEMA,
                    "instances": [
                        {
                            "route_key": "甲",
                            "aliases": ["别名"],
                            "instance_id": "1",
                            "domain_name": "d",
                            "instance_root": "a",
                            "enabled": True,
                        }
                    ],
                },
            )
            with self.assertRaises(SystemExit) as dup:
                registry.register(
                    {
                        "route_key": "甲",
                        "aliases": [],
                        "instance_id": "2",
                        "domain_name": "d2",
                        "instance_root": "a",
                    },
                    root,
                )
            self.assertIn("ROUTE_KEY_CONFLICT", str(dup.exception))
            with self.assertRaises(SystemExit) as alias:
                registry.register(
                    {
                        "route_key": "乙",
                        "aliases": ["别名"],
                        "instance_id": "3",
                        "domain_name": "d3",
                        "instance_root": "a",
                    },
                    root,
                )
            self.assertIn("ROUTE_KEY_CONFLICT", str(alias.exception))

    def test_skill_internal_and_protocol_escape(self):
        import registry

        skill_root = Path(__file__).resolve().parents[1]
        errors = registry.validate_entry(
            {
                "route_key": "x",
                "instance_id": "x",
                "domain_name": "x",
                "instance_root": ".",
                "protocol_entry": "../outside.md",
            },
            skill_root,
            require_exists=True,
        )
        self.assertIn("SKILL_INTERNAL_TARGET", errors)
        self.assertIn("PROTOCOL_PATH_ESCAPE", errors)

    def test_bare_push_ambiguous_with_two_instances(self):
        import route_parse
        import registry

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "one").mkdir()
            (root / "two").mkdir()
            (root / ".serial-job-session").mkdir()
            registry.atomic_write_json(
                root / ".serial-job-session" / "instances.json",
                {
                    "schema": registry.SCHEMA,
                    "instances": [
                        {
                            "route_key": "demo-domain",
                            "aliases": [],
                            "instance_id": "1",
                            "domain_name": "a",
                            "instance_root": "one",
                            "enabled": True,
                        },
                        {
                            "route_key": "协议进化",
                            "aliases": [],
                            "instance_id": "2",
                            "domain_name": "b",
                            "instance_root": "two",
                            "enabled": True,
                        },
                    ],
                },
            )
            parsed = route_parse.parse_command("推进一下", repo_root=root)
            self.assertFalse(parsed["ok"])
            self.assertEqual(parsed["diagnosis"], "ROUTE_AMBIGUOUS")

    def test_migrate_refuses_live_and_keeps_terminal_readonly(self):
        import migrate_protocol
        import registry

        repo = registry.find_repo_root(Path(__file__))
        live = repo / ".cursor/workflows/example-domain"
        if (live / "INSTANCE.json").is_file():
            rc = migrate_protocol.main(
                ["dry-run", "--instance", str(live), "--route-key", "demo-domain", "--repo-root", str(repo)]
            )
            self.assertEqual(rc, 1)

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = root / "jobs"
            jobs.mkdir()
            (root / "CURRENT.md").write_text("status: idle\n", encoding="utf-8")
            (root / "INSTANCE.json").write_text(
                json.dumps(
                    {
                        "schema": "serial-job-session-instance/v1",
                        "instance_id": "copy",
                        "domain_name": "copy",
                        "instance_root": str(root),
                    }
                ),
                encoding="utf-8",
            )
            (jobs / "ready.md").write_text(
                "```yaml\nid: ready\nshape: 1\nstatus: ready\nestimate: 1\n```\n",
                encoding="utf-8",
            )
            terminal = (
                "```yaml\nid: old\nshape: 2\nstatus: accepted\nattempt: 1\nelapsed: 1m\n```\n"
            )
            (jobs / "old.md").write_text(terminal, encoding="utf-8")
            rc = migrate_protocol.main(
                ["apply", "--instance", str(root), "--route-key", "副本", "--repo-root", str(root)]
            )
            self.assertEqual(rc, 0)
            ready = (jobs / "ready.md").read_text(encoding="utf-8")
            self.assertIn("estimate_total:", ready)
            self.assertEqual((jobs / "old.md").read_text(encoding="utf-8"), terminal)
            instance = json.loads((root / "INSTANCE.json").read_text(encoding="utf-8"))
            self.assertEqual(instance["schema"], "serial-job-session-instance/v2")
            self.assertEqual(instance["route_key"], "副本")
            (jobs / "ready.md").write_text(ready + "\n# dirty\n", encoding="utf-8")
            rc = migrate_protocol.main(["recover", "--instance", str(root), "--route-key", "副本"])
            self.assertEqual(rc, 0)
            recovered = json.loads((root / "INSTANCE.json").read_text(encoding="utf-8"))
            self.assertEqual(recovered.get("schema"), "serial-job-session-instance/v1")

    def test_fixture_catalog_exists(self):
        catalog = (
            Path(__file__).resolve().parents[1]
            / "Test"
            / "serial-job-session"
            / "fixtures"
            / "baseline-2.2.json"
        )
        data = json.loads(catalog.read_text(encoding="utf-8"))
        ids = {item["id"] for item in data["known_defects"]}
        self.assertIn("stale_dependency_70", ids)
        self.assertIn("missing_library_119_126", ids)


class QualityGateTests(unittest.TestCase):
    def test_eval_catalog_covers_required_themes(self):
        import quality_gate

        self.assertEqual(quality_gate.eval_theme_gaps(), [])

    def test_identity_mismatch_is_red(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ValidatorTests().make_domain(root)
            env = (root / "ENV.md").read_text(encoding="utf-8")
            (root / "ENV.md").write_text(
                env.replace("domain_name: demo", "domain_name: demo\nroute_key: alpha"),
                encoding="utf-8",
            )
            manifest = json.loads((root / "INSTANCE.json").read_text(encoding="utf-8"))
            manifest["schema"] = "serial-job-session-instance/v2"
            manifest["route_key"] = "beta"
            (root / "INSTANCE.json").write_text(json.dumps(manifest), encoding="utf-8")
            result = validate_domain_pack.validate_domain(root)
            self.assertFalse(result["ok"])
            self.assertTrue(any("IDENTITY_MISMATCH" in item for item in result["errors"]), result["errors"])

    def test_faces_and_estimate_caps_red(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ValidatorTests().make_domain(root)
            job = (root / "jobs" / "2026-09-02_demo.md").read_text(encoding="utf-8")
            job = job.replace("faces: [code]", "faces: [a, b, c]")
            job = job.replace("estimate: 1", "estimate: 99")
            (root / "jobs" / "2026-09-02_demo.md").write_text(job, encoding="utf-8")
            result = validate_domain_pack.validate_domain(root)
            self.assertTrue(any("FACES_OVER_MAX" in item for item in result["errors"]), result["errors"])
            self.assertTrue(any("ESTIMATE_OVER_CAP" in item for item in result["errors"]), result["errors"])

    def test_plan_rollup_stale_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ValidatorTests().make_domain(root)
            plan = (root / "PLAN.md").read_text(encoding="utf-8")
            plan = plan.replace(
                "<!-- AUTO:PLAN-ROLLUP -->",
                "<!-- AUTO:PLAN-ROLLUP -->\nsource_hash: deadbeefdeadbeef\n",
            )
            (root / "PLAN.md").write_text(plan, encoding="utf-8")
            result = validate_domain_pack.validate_domain(root)
            self.assertTrue(any("PLAN_ROLLUP_STALE" in item for item in result["errors"]), result["errors"])

    def test_idle_open_job_illegal_state(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ValidatorTests().make_domain(root)
            job = (root / "jobs" / "2026-09-02_demo.md").read_text(encoding="utf-8")
            job = job.replace("status: accepted", "status: running")
            (root / "jobs" / "2026-09-02_demo.md").write_text(job, encoding="utf-8")
            result = validate_domain_pack.validate_domain(root)
            self.assertTrue(any("ILLEGAL_STATE" in item for item in result["errors"]), result["errors"])

    def test_crlf_job_still_parses(self):
        with tempfile.TemporaryDirectory() as temp:
            path = ValidatorTests().make_job(Path(temp))
            text = path.read_text(encoding="utf-8").replace("\n", "\r\n")
            path.write_bytes(text.encode("utf-8"))
            errors, card = validate_domain_pack.validate_job(path)
            self.assertEqual(errors, [])
            self.assertEqual(card["id"], "2026-09-02_demo")

    def test_close_fault_after_job_then_rollback(self):
        import os
        import close_transaction

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = root / "jobs"
            jobs.mkdir()
            original = "```yaml\nid: c\nshape: 1\nstatus: running\nattempt: 1\nclosure_revision: 0\nestimate: 1\nai_first_pass: 0.1\nai_rework: 0\nwall_elapsed: 0.1\n```\n"
            (jobs / "c.md").write_text(original, encoding="utf-8")
            (root / "CURRENT.md").write_text(
                "```yaml\nstatus: running\nactive_job: c\njob_id: c\n```\n",
                encoding="utf-8",
            )
            (root / "LOG.md").write_text("# LOG\n", encoding="utf-8")
            close_transaction.prepare(root, "c", "accepted", "cmd-fault", "control_owner")
            os.environ["SJS_FAULT_INJECT"] = "after_job"
            with self.assertRaises(RuntimeError):
                close_transaction.commit(root, "cmd-fault")
            os.environ.pop("SJS_FAULT_INJECT", None)
            close_transaction.recover(root, "rollback")
            self.assertIn("status: running", (jobs / "c.md").read_text(encoding="utf-8"))

    def test_close_idempotent_command(self):
        import close_transaction

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = root / "jobs"
            jobs.mkdir()
            (jobs / "c.md").write_text(
                "```yaml\nid: c\nshape: 1\nstatus: running\nattempt: 1\nclosure_revision: 0\nestimate: 1\nai_first_pass: 0.1\nai_rework: 0\nwall_elapsed: 0.1\n```\n",
                encoding="utf-8",
            )
            (root / "CURRENT.md").write_text(
                "```yaml\nstatus: running\nactive_job: c\njob_id: c\n```\n",
                encoding="utf-8",
            )
            (root / "LOG.md").write_text("# LOG\n", encoding="utf-8")
            first = close_transaction.prepare(root, "c", "accepted", "same-cmd", "control_owner")
            again = close_transaction.prepare(root, "c", "accepted", "same-cmd", "control_owner")
            self.assertEqual(first["command_id"], again["command_id"])
            close_transaction.commit(root, "same-cmd")
            third = close_transaction.commit(root, "same-cmd")
            self.assertEqual(third["phase"], "committed")

    def test_illegal_close_from_accepted(self):
        import close_transaction

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = root / "jobs"
            jobs.mkdir()
            (jobs / "c.md").write_text(
                "```yaml\nid: c\nshape: 1\nstatus: accepted\nattempt: 1\nclosure_revision: 1\n```\n",
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit) as ctx:
                close_transaction.prepare(root, "c", "accepted", "cmd", "control_owner")
            self.assertIn("ILLEGAL_CLOSE_FROM_accepted", str(ctx.exception))

    def test_worker_lost_stops_managed(self):
        import managed_runner

        result = managed_runner.ingest_result(
            {
                "schema": "serial-job-session-worker-result/v1",
                "job_id": "x",
                "status": "worker_lost",
                "close_requested": False,
                "control_writes": [],
                "writes": [],
            }
        )
        self.assertEqual(result["stop"], "worker_lost")

    def test_cold_start_not_low_risk(self):
        import managed_runner

        risk = managed_runner.classify_risk(
            {"faces": ["doc-config"], "human_accept": "none", "gate": True, "estimate": 0.5},
            history_pass_rate=None,
            sample_n=0,
        )
        self.assertNotEqual(risk, "low")

    def test_outbox_retry_then_clear(self):
        import post_close
        import close_transaction

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            box = {
                "schema": "serial-job-session-outbox/v1",
                "pending": ["progress_snapshot"],
                "done": [],
            }
            close_transaction.tx_dir(root).mkdir(parents=True)
            (close_transaction.tx_dir(root) / "outbox.json").write_text(
                json.dumps(box), encoding="utf-8"
            )
            drained = post_close.drain_outbox(root)
            self.assertEqual(drained["pending"], [])
            self.assertIn("progress_snapshot", drained["done"])

    def test_two_process_unfinished_tx(self):
        import close_transaction
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            jobs = root / "jobs"
            jobs.mkdir()
            yaml = "```yaml\nid: %s\nshape: %s\nstatus: running\nattempt: 1\nclosure_revision: 0\nai_first_pass: 0.1\nai_rework: 0\n```\n"
            (jobs / "a.md").write_text(yaml % ("a", "1"), encoding="utf-8")
            (jobs / "b.md").write_text(yaml % ("b", "2"), encoding="utf-8")
            close_transaction.prepare(root, "a", "accepted", "cmd-a", "control_owner")
            script = (
                "import sys; sys.path.insert(0, r'%s'); import close_transaction; "
                "from pathlib import Path\n"
                "try:\n"
                "    close_transaction.prepare(Path(r'%s'), 'b', 'accepted', 'cmd-b', 'control_owner')\n"
                "    print('opened')\n"
                "except SystemExit as exc:\n"
                "    print(exc)\n"
                % (
                    Path(close_transaction.__file__).resolve().parent.as_posix(),
                    Path(root).as_posix(),
                )
            )
            proc = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("UNFINISHED_TRANSACTION", proc.stdout)

    def test_git_open_run_close(self):
        import close_transaction
        import subprocess

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            jobs = root / "jobs"
            jobs.mkdir()
            (jobs / "c.md").write_text(
                "```yaml\nid: c\nshape: 1\nstatus: running\nattempt: 1\nclosure_revision: 0\nestimate: 1\nai_first_pass: 0.1\nai_rework: 0\nwall_elapsed: 0.1\n```\n",
                encoding="utf-8",
            )
            (root / "CURRENT.md").write_text(
                "```yaml\nstatus: running\nactive_job: c\njob_id: c\n```\n",
                encoding="utf-8",
            )
            (root / "LOG.md").write_text("# LOG\n", encoding="utf-8")
            close_transaction.prepare(root, "c", "accepted", "git-cmd", "control_owner")
            close_transaction.commit(root, "git-cmd")
            self.assertIn("status: accepted", (jobs / "c.md").read_text(encoding="utf-8"))
            journal = json.loads(
                close_transaction.journal_path(root).read_text(encoding="utf-8")
            )
            backup = Path(journal["backup_dir"]).resolve()
            private_root = (root / ".serial-job-session" / "private").resolve()
            self.assertTrue((private_root / ".gitignore").is_file())
            self.assertTrue(instantiate_domain.is_within(backup, private_root), backup)
            self.assertFalse((root / ".sjs-tx" / "backup").exists())

    def test_instantiate_in_git_repo_seeds_private_gitignore(self):
        import subprocess

        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            created = instantiate_domain.instantiate(repo / "demo-instance", "demo")
            private = repo / ".serial-job-session" / "private"
            self.assertTrue((private / ".gitignore").is_file())
            self.assertTrue((created / ".cursorindexingignore").is_file())
            ignore_text = (created / ".cursorindexingignore").read_text(encoding="utf-8")
            self.assertIn("output/", ignore_text)
            manifest = json.loads((created / "INSTANCE.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest.get("runtime_private"), ".serial-job-session/private")

    def test_calibrate_shrinks_toward_global(self):
        import calibrate

        few = calibrate.calibrate_estimate(10, [20], [10], [12, 11, 10], [10, 10, 10])
        many = calibrate.calibrate_estimate(
            10, [20] * 20, [10] * 20, [12, 11, 10], [10, 10, 10]
        )
        self.assertGreater(many["shrink_factor"], few["shrink_factor"])

    def test_interval_reversed_red(self):
        import time_ledger

        errors = time_ledger.diagnose_intervals(
            [{"label": "attempt 1", "start": "2026-09-03T12:00:00+08:00", "end": "2026-09-03T11:00:00+08:00"}]
        )
        self.assertIn("INTERVAL_REVERSED", errors)

    def test_replay_fixtures_match_goldens(self):
        import quality_gate

        replay = quality_gate.replay_fixtures()
        self.assertTrue(replay["ok"], replay)

    def test_workflow_depth_on_card(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ValidatorTests().make_domain(root)
            job = (root / "jobs" / "2026-09-02_demo.md").read_text(encoding="utf-8")
            job = job.replace("status: accepted", "status: accepted\nexecution_mode: embedded_workflow\nworkflow_max_depth: 2")
            (root / "jobs" / "2026-09-02_demo.md").write_text(job, encoding="utf-8")
            result = validate_domain_pack.validate_domain(root)
            self.assertTrue(any("WORKFLOW_DEPTH" in item for item in result["errors"]), result["errors"])


class FeishuOptionalTests(unittest.TestCase):
    def tearDown(self):
        import feishu_sync

        feishu_sync.set_invoke(None)

    def test_map_status_human_ops_not_done(self):
        import feishu_sync

        self.assertEqual(feishu_sync.map_status("running", "human_ops"), feishu_sync.STATUS_WAITING_HUMAN)
        self.assertNotEqual(feishu_sync.map_status("running", "human_ops"), feishu_sync.STATUS_DONE)
        self.assertEqual(feishu_sync.map_status("ready"), feishu_sync.STATUS_TODO)
        self.assertEqual(feishu_sync.map_status("accepted"), feishu_sync.STATUS_DONE)

    def test_short_name_column_wins_title(self):
        import menu_parse

        text = (
            "## 待做\n"
            "| 编号 | 短名 | 目标 | 依赖 | 预计 |\n"
            "|---|---|---|---|---|\n"
            "| 1 | 主 TOP 接入地形 | File Pattern 喂布局 | — | 0.3 |\n"
            "\n## 已结档\n"
        )
        rows, errors = menu_parse.parse_pending_table(text)
        self.assertEqual(errors, [])
        self.assertEqual(rows[0]["title"], "主 TOP 接入地形")
        self.assertEqual(rows[0]["goal"], "File Pattern 喂布局")

    def test_short_name_pile_heuristic(self):
        import menu_parse

        self.assertIn("SHORT_NAME_PILE", menu_parse.short_name_issues("导出布局 + 主 TOP + 骨架"))
        self.assertEqual(menu_parse.short_name_issues("主 TOP 接入地形"), [])

    def test_probe_fail_blocks_provision(self):
        import feishu_sync

        def boom(*_args, **_kwargs):
            raise feishu_sync.CliNotReady("CLI_NOT_READY")

        feishu_sync.set_invoke(boom)
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(feishu_sync.CliNotReady):
                feishu_sync.provision(instance_root=Path(temp), title="任务")

    def test_no_binding_skips_seed(self):
        import feishu_sync

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = feishu_sync.seed_menu("demo", instance_root=root)
            self.assertTrue(result.get("skipped"))
            self.assertEqual(result.get("reason"), "NO_BINDING")

    def test_outbox_feishu_only_when_bound(self):
        import close_transaction

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertNotIn("feishu_sync", close_transaction.pending_outbox_actions(root))
            (root / "feishu.json").write_text(
                json.dumps({"schema": "serial-job-session-feishu-binding/v1", "enabled": True}),
                encoding="utf-8",
            )
            self.assertIn("feishu_sync", close_transaction.pending_outbox_actions(root))

    def test_validate_requires_short_name_when_feishu_enabled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ValidatorTests().make_domain(root)
            (root / "JOB-SHAPES.md").write_text(
                "## 待做\n"
                "| 编号 | 目标 | 依赖 | 预计 |\n"
                "|---|---|---|---|\n"
                "| 9 | 校准估分 | — | 1 |\n"
                "## 已结档\n## 已作废\n",
                encoding="utf-8",
            )
            (root / "feishu.json").write_text(
                json.dumps({"schema": "serial-job-session-feishu-binding/v1", "enabled": True}),
                encoding="utf-8",
            )
            result = validate_domain_pack.validate_domain(root)
            self.assertTrue(
                any("SHORT_NAME_MISSING" in item for item in result["errors"]),
                result["errors"],
            )


class SkillIntegrityTests(unittest.TestCase):
    def test_newline_style_does_not_change_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lf = root / "sample.py"
            crlf = root / "sample_crlf.py"
            lf.write_bytes(b"print('ok')\n")
            crlf.write_bytes(b"print('ok')\r\n")
            self.assertEqual(check_skill_integrity.sha256(lf), check_skill_integrity.sha256(crlf))
            lf_hash = hashlib.sha256(b"print('ok')\n").hexdigest()
            self.assertIn(lf_hash, check_skill_integrity.acceptable_hashes(crlf))

    def test_committed_skill_integrity_manifest_is_green(self):
        result = check_skill_integrity.check_manifest()
        self.assertTrue(result["ok"], result)


if __name__ == "__main__":
    unittest.main()
