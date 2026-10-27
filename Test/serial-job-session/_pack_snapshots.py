#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from pathlib import Path
import json
import shutil
import sys

root = Path(__file__).resolve().parent / "fixtures"
snaps = root / "snapshots"
gold = root / "goldens"
if snaps.exists():
    shutil.rmtree(snaps)
gold.mkdir(parents=True, exist_ok=True)
snaps.mkdir(parents=True, exist_ok=True)

job70_live = """# 70
```yaml
id: 2026-08-28_human-region-farm-forest-mountain
shape: 70 人类 · 田/林/山域闭合环创作
estimate_group: human-layout
depends_on: [52, 53, 100, 112, 114, 116, 117, 126]
faces: []
estimate: null
estimate_source: backfill
actor: human
human_accept: unity
gate: false
status: ready
attempt: 1
```
"""
job70_stale = job70_live.replace(
    "[52, 53, 100, 112, 114, 116, 117, 126]",
    "[52, 53, 100, 112, 114, 116, 117, 118]",
)

job115 = """# 115
```yaml
id: 2026-09-02_v2-interp-migrate-authored
shape: 115
estimate_group: v2-layout-import
depends_on: [114]
faces: [python-compiler, unity-csharp]
estimate: 1.0
estimate_source: menu
status: accepted
attempt: 7
ai_net: 0.60h
variance: 0.60
```
"""
job118 = """# 118
```yaml
id: 2026-09-03_v2-render-live-ground-preview
shape: 118
estimate_group: wysiwyg-loop
depends_on: [117, 89, 97, 99]
faces: [unity-csharp]
estimate: 0.8
status: rejected
superseded_by: 119-reject
attempt: 3
ai_net: 0.65h
variance: 0.81
```
"""
job119 = """# 119
```yaml
id: 2026-09-03_v2-reject-118-isolate
shape: 119
estimate_group: preview-authority
status: accepted
attempt: 1
ai_net: 0.02h
```
"""
job126 = """# 126
```yaml
id: 2026-09-03_v2-preview-e2e-gate
shape: 126
estimate_group: preview-authority
status: accepted
attempt: 4
ai_net: 1.04h
variance: 0.69
```
"""
job89 = """# 89
```yaml
id: 2026-08-30_v2-live-ground-vertex-splat
shape: 89 （A4 必做前置）
estimate_group: wysiwyg-loop
status: accepted
attempt: 5
ai_net: untracked
variance: null
```
"""
job103 = """# 103
```yaml
id: 2026-09-01_v2-tile-pack-pdg-hip
shape: 103
estimate_group: wysiwyg-loop
status: accepted
attempt: 1
ai_net: untracked
variance: null
reopen_started_at: "2026-09-01T18:46:56+08:00"
reopen_basis: 109+110 accepted
```
"""

menu_numbered_126 = """# JOB-SHAPES
## 待做
70. **人类 · 田/林/山域闭合环创作**（依赖：52, 53, 100, 112, 114, 116, 117, 126）
## 已结档
"""
menu_table_only = """# JOB-SHAPES
## 待做
| 编号 | 目标 | estimate_group | 依赖 |
|---|---|---|---|
| 70 | 人类环 | human-layout | 52, 53, 126 |
## 已结档
"""
plan_stale = """# PLAN
| 阶段 | 收口判据 | 预计区间 h |
|---|---|---|
| A4 编辑期回路解耦 | 100 人眼 | 16–26 |
<!-- AUTO:PLAN-ROLLUP -->
_由 `estimate_stats.py --plan` 于 2026-09-02T21:04:19+08:00 生成，勿手改本块。_
<!-- /AUTO:PLAN-ROLLUP -->
"""
plan_fresh = """# PLAN
| 阶段 | 收口判据 | 预计区间 h |
|---|---|---|
| A4 编辑期回路解耦 | 100 人眼 | 16–26 |
<!-- AUTO:PLAN-ROLLUP -->
_由 `estimate_stats.py --plan` 于 2026-09-03T15:47:35+08:00 生成，勿手改本块。_
<!-- /AUTO:PLAN-ROLLUP -->
"""

v1_115 = json.dumps(
    {
        "schema": "serial-job-session-estimate-library/v1",
        "instance_id": "8f3c1a2e-4b6d-4e91-9c70-a1b2c3d4e5f6",
        "job_id": "2026-09-02_v2-interp-migrate-authored",
        "status": "rejected",
        "ai_net": 0.18,
        "estimate": 1.0,
    },
    ensure_ascii=False,
)
v2_115 = json.dumps(
    {
        "schema": "serial-job-session-estimate-library/v2",
        "instance_id": "8f3c1a2e-4b6d-4e91-9c70-a1b2c3d4e5f6",
        "job_id": "2026-09-02_v2-interp-migrate-authored",
        "status": "accepted",
        "ai_net": 0.6,
        "estimate": 1.0,
        "event": "closed",
        "closure_revision": 1,
    },
    ensure_ascii=False,
)
v2_119 = json.dumps(
    {
        "schema": "serial-job-session-estimate-library/v2",
        "job_id": "2026-09-03_v2-reject-118-isolate",
        "status": "accepted",
        "closure_revision": 1,
    },
    ensure_ascii=False,
)
v2_126 = json.dumps(
    {
        "schema": "serial-job-session-estimate-library/v2",
        "job_id": "2026-09-03_v2-preview-e2e-gate",
        "status": "accepted",
        "closure_revision": 1,
    },
    ensure_ascii=False,
)

rest_ids = [
    "2026-09-03_v2-restore-117-baseline",
    "2026-09-03_v2-hda-hip-git-ingest",
    "2026-09-03_v2-curve-input-channel",
    "2026-09-03_v2-tile-pack-focus-heu",
    "2026-09-03_v2-unity-thin-bridge",
    "2026-09-03_v2-preview-depth-cleanup",
]
rest_cards = {
    jid: (
        "# %s\n```yaml\nid: %s\nshape: 120\nstatus: accepted\nattempt: 1\nai_net: 0.1h\n```\n"
        % (jid, jid)
    )
    for jid in rest_ids
}
rest_lib = "\n".join(
    json.dumps(
        {
            "schema": "serial-job-session-estimate-library/v2",
            "job_id": jid,
            "status": "accepted",
            "closure_revision": 1,
        },
        ensure_ascii=False,
    )
    for jid in rest_ids
)


def write_snap(name, expected, files, command):
    dest = snaps / name
    dest.mkdir(parents=True)
    (dest / "jobs").mkdir()
    (dest / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "serial-job-session-fixture/v1",
                "id": name,
                "command": command,
                "expected_codes": expected,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    for rel, content in files.items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


write_snap(
    "live-min",
    ["COMPAT_MULTI_ATTEMPT", "COMPAT_UNTRACKED", "COMPAT_SPECIAL_REOPEN"],
    {
        "JOB-SHAPES.md": menu_numbered_126,
        "PLAN.md": plan_fresh,
        "jobs/2026-08-28_human-region-farm-forest-mountain.md": job70_live,
        "jobs/2026-09-02_v2-interp-migrate-authored.md": job115,
        "jobs/2026-09-03_v2-render-live-ground-preview.md": job118,
        "jobs/2026-09-03_v2-reject-118-isolate.md": job119,
        "jobs/2026-09-03_v2-preview-e2e-gate.md": job126,
        "jobs/2026-08-30_v2-live-ground-vertex-splat.md": job89,
        "jobs/2026-09-01_v2-tile-pack-pdg-hip.md": job103,
        **{("jobs/%s.md" % jid): rest_cards[jid] for jid in rest_ids},
        "estimate-library.jsonl": "\n".join([v1_115, v2_115, v2_119, v2_126, rest_lib])
        + "\n",
        "stats_overall.json": json.dumps(
            {"overall": {"jobs": ["2026-09-02_v2-interp-migrate-authored"]}},
            ensure_ascii=False,
            indent=2,
        ),
    },
    "py -3 diagnose_replay.py fixtures/snapshots/live-min",
)

write_snap(
    "neg-stale-dep-70",
    ["STALE_DEPENDENCY"],
    {
        "JOB-SHAPES.md": menu_numbered_126,
        "jobs/2026-08-28_human-region-farm-forest-mountain.md": job70_stale,
    },
    "py -3 diagnose_replay.py fixtures/snapshots/neg-stale-dep-70",
)

write_snap(
    "neg-rejected-in-main",
    ["REJECTED_IN_SUCCESS_SAMPLE"],
    {
        "jobs/2026-09-03_v2-render-live-ground-preview.md": job118,
        "stats_overall.json": json.dumps(
            {"overall": {"jobs": ["2026-09-03_v2-render-live-ground-preview"]}},
            ensure_ascii=False,
            indent=2,
        ),
    },
    "py -3 diagnose_replay.py fixtures/snapshots/neg-rejected-in-main",
)

write_snap(
    "neg-library-stale-115",
    ["LIBRARY_STALE_REVISION"],
    {
        "jobs/2026-09-02_v2-interp-migrate-authored.md": job115,
        "estimate-library.jsonl": v1_115 + "\n",
    },
    "py -3 diagnose_replay.py fixtures/snapshots/neg-library-stale-115",
)

write_snap(
    "neg-plan-rollup-stale",
    ["PLAN_ROLLUP_STALE"],
    {"PLAN.md": plan_stale},
    "py -3 diagnose_replay.py fixtures/snapshots/neg-plan-rollup-stale",
)

write_snap(
    "neg-missing-library-119-126",
    ["POST_CLOSE_INCOMPLETE"],
    {
        "jobs/2026-09-03_v2-reject-118-isolate.md": job119,
        "jobs/2026-09-03_v2-preview-e2e-gate.md": job126,
        **{("jobs/%s.md" % jid): rest_cards[jid] for jid in rest_ids},
        "estimate-library.jsonl": "",
    },
    "py -3 diagnose_replay.py fixtures/snapshots/neg-missing-library-119-126",
)

write_snap(
    "neg-menu-table-unparsed",
    ["MENU_TABLE_UNPARSED"],
    {"JOB-SHAPES.md": menu_table_only},
    "py -3 diagnose_replay.py fixtures/snapshots/neg-menu-table-unparsed",
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import diagnose_replay

gold.mkdir(parents=True, exist_ok=True)
for snapshot in sorted(p for p in snaps.iterdir() if p.is_dir()):
    report = diagnose_replay.replay_one(snapshot)
    payload = {
        "id": report["id"],
        "command": report["command"],
        "expected_codes": report["expected_codes"],
        "observed_codes": report["observed_codes"],
        "status_summary": report["status_summary"],
        "projection_golden": {
            "ok": report["ok"],
            "missing": report["missing"],
            "extra_blocking": report["extra_blocking"],
        },
    }
    (gold / (report["id"] + ".json")).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if not report["ok"]:
        raise SystemExit("fixture not green vs expected: %s" % json.dumps(report, ensure_ascii=False))

print("wrote", sorted(p.name for p in snaps.iterdir()))
