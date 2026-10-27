#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""只读回放诊断：对脱敏 snapshot 判红，禁止 import 活实例路径。"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

PENDING_LINE = re.compile(
    r"^(?P<num>\d+)\.\s+\*\*(?P<title>.+?)\*\*.*?依赖：(?P<deps>[^）)]+)",
    re.M,
)
TABLE_ROW = re.compile(
    r"^\|\s*(\d+)\s*\|\s*[^|]*\|\s*[^|]*\|\s*([^|]*)\|",
    re.M,
)
YAML_BLOCK = re.compile(r"```ya?ml\s*\n(.*?)```", re.S)
PLAN_GEN = re.compile(r"于\s*([0-9T:+\-]+)\s*生成")
PHASE_A4 = re.compile(r"A4.*16[–\-－]26")

REQUIRED_CLOSED = [
    "2026-09-03_v2-reject-118-isolate",
    "2026-09-03_v2-restore-117-baseline",
    "2026-09-03_v2-hda-hip-git-ingest",
    "2026-09-03_v2-curve-input-channel",
    "2026-09-03_v2-tile-pack-focus-heu",
    "2026-09-03_v2-unity-thin-bridge",
    "2026-09-03_v2-preview-depth-cleanup",
    "2026-09-03_v2-preview-e2e-gate",
]


def parse_scalar(raw: str):
    text = raw.strip()
    text = re.sub(r"\s+#.*$", "", text).strip()
    if not text or text in {"null", "None", "~"}:
        return None
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [p.strip().strip("\"'") for p in inner.split(",") if p.strip()]
    return text.strip("\"'")


def parse_card_text(text: str) -> dict:
    match = YAML_BLOCK.search(text or "")
    if not match:
        return {}
    card = {}
    for line in match.group(1).splitlines():
        if ":" not in line or line.startswith(" "):
            continue
        key, value = line.split(":", 1)
        card[key.strip()] = parse_scalar(value)
    return card


def load_cards(snapshot: Path) -> Dict[str, dict]:
    jobs = snapshot / "jobs"
    cards = {}
    if not jobs.is_dir():
        return cards
    for path in jobs.glob("*.md"):
        card = parse_card_text(path.read_text(encoding="utf-8"))
        job_id = str(card.get("id") or path.stem)
        cards[job_id] = card
    return cards


def ints(values) -> List[int]:
    out = []
    for item in values or []:
        try:
            out.append(int(str(item).split()[0]))
        except ValueError:
            continue
    return out


def menu_numbered_deps(text: str) -> Dict[int, List[int]]:
    menu = {}
    for match in PENDING_LINE.finditer(text or ""):
        deps = [int(p) for p in re.findall(r"\d+", match.group("deps"))]
        menu[int(match.group("num"))] = deps
    return menu


def menu_table_deps(text: str) -> Dict[int, List[int]]:
    menu = {}
    pending = text.split("## 待做")[-1].split("## 已结档")[0] if "## 待做" in text else text
    for match in TABLE_ROW.finditer(pending):
        shape = int(match.group(1))
        deps_cell = match.group(2)
        menu[shape] = [int(p) for p in re.findall(r"\d+", deps_cell)]
    return menu


def read_jsonl(path: Path) -> List[dict]:
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def latest_library(records: List[dict]) -> Dict[str, dict]:
    ranked: Dict[str, dict] = {}
    for record in records:
        key = str(record.get("job_id") or "")
        schema = str(record.get("schema") or "")
        rev = int(record.get("closure_revision") or (0 if "v1" in schema else 1))
        prev = ranked.get(key)
        prev_rev = int((prev or {}).get("_rev") or -1)
        if prev is None or rev >= prev_rev:
            ranked[key] = dict(record, _rev=rev)
    return ranked


def diagnose(snapshot: Path) -> dict:
    cards = load_cards(snapshot)
    shapes_text = ""
    shapes_path = snapshot / "JOB-SHAPES.md"
    if shapes_path.is_file():
        shapes_text = shapes_path.read_text(encoding="utf-8")
    numbered = menu_numbered_deps(shapes_text)
    table = menu_table_deps(shapes_text)
    codes: List[str] = []
    details: List[str] = []

    job70 = None
    for card in cards.values():
        raw_shape = str(card.get("shape") or "").split()[0]
        if raw_shape == "70":
            job70 = card
            break
    menu70 = table.get(70) or numbered.get(70) or []
    if job70 is not None and menu70:
        have = ints(job70.get("depends_on"))
        if 118 in have and 126 in menu70 and 126 not in have:
            codes.append("STALE_DEPENDENCY")
            details.append("job70 depends_on has 118; menu 70 lists 126")

    stats_path = snapshot / "stats_overall.json"
    if stats_path.is_file():
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
        main_jobs = set(stats.get("overall", {}).get("jobs") or [])
        rejected = {
            job_id
            for job_id, card in cards.items()
            if str(card.get("status")) == "rejected"
        }
        leak = sorted(rejected & main_jobs)
        if leak:
            codes.append("REJECTED_IN_SUCCESS_SAMPLE")
            details.append("rejected in overall.jobs: " + ",".join(leak))

    lib_rows = read_jsonl(snapshot / "estimate-library.jsonl")
    latest = latest_library(lib_rows)
    card115 = cards.get("2026-09-02_v2-interp-migrate-authored")
    lib115 = latest.get("2026-09-02_v2-interp-migrate-authored")
    if card115 and str(card115.get("status")) == "accepted":
        if lib115 is None or str(lib115.get("status")) != "accepted":
            codes.append("LIBRARY_STALE_REVISION")
            details.append("latest library 115 is %s" % (None if lib115 is None else lib115.get("status")))

    plan_path = snapshot / "PLAN.md"
    if plan_path.is_file():
        plan = plan_path.read_text(encoding="utf-8")
        generated = PLAN_GEN.search(plan)
        if PHASE_A4.search(plan) and generated:
            stamp = generated.group(1)
            if stamp.startswith("2026-09-02"):
                codes.append("PLAN_ROLLUP_STALE")
                details.append("AUTO rollup %s vs A4 16-26" % stamp)

    accepted_ids = {
        job_id for job_id, card in cards.items() if str(card.get("status")) == "accepted"
    }
    lib_ids = {str(r.get("job_id") or "") for r in lib_rows}
    missing = [job_id for job_id in REQUIRED_CLOSED if job_id in accepted_ids and job_id not in lib_ids]
    if missing:
        codes.append("POST_CLOSE_INCOMPLETE")
        details.append("library missing " + ",".join(missing))

    if shapes_text and "## 待做" in shapes_text and "|" in shapes_text:
        if not numbered:
            codes.append("MENU_TABLE_UNPARSED")
            details.append("pending table present but numbered menu parser matched 0 rows")

    for job_id, card in cards.items():
        attempt = card.get("attempt")
        try:
            attempt_n = int(attempt)
        except (TypeError, ValueError):
            attempt_n = 1
        if attempt_n and attempt_n > 1:
            codes.append("COMPAT_MULTI_ATTEMPT")
            details.append("%s attempt=%s" % (job_id, attempt))
            break
    for job_id, card in cards.items():
        if str(card.get("ai_net")) == "untracked":
            codes.append("COMPAT_UNTRACKED")
            details.append("%s ai_net=untracked" % job_id)
            break
    for job_id, card in cards.items():
        if card.get("reopen_started_at") or card.get("reopen_basis"):
            codes.append("COMPAT_SPECIAL_REOPEN")
            details.append(job_id)
            break

    codes = list(dict.fromkeys(codes))
    return {
        "snapshot": snapshot.name,
        "codes": codes,
        "details": details,
        "card_count": len(cards),
    }


def load_manifest(snapshot: Path) -> dict:
    path = snapshot / "manifest.json"
    return json.loads(path.read_text(encoding="utf-8"))


def replay_one(snapshot: Path) -> dict:
    manifest = load_manifest(snapshot)
    observed = diagnose(snapshot)
    expected = list(manifest.get("expected_codes") or [])
    missing = [code for code in expected if code not in observed["codes"]]
    blocking_seen = [
        code
        for code in observed["codes"]
        if code in {
            "STALE_DEPENDENCY",
            "REJECTED_IN_SUCCESS_SAMPLE",
            "LIBRARY_STALE_REVISION",
            "PLAN_ROLLUP_STALE",
            "POST_CLOSE_INCOMPLETE",
            "MENU_TABLE_UNPARSED",
        }
    ]
    extra_blocking = [code for code in blocking_seen if code not in expected]
    ok = not missing and not extra_blocking
    return {
        "id": manifest.get("id"),
        "command": manifest.get("command"),
        "expected_codes": expected,
        "observed_codes": observed["codes"],
        "details": observed["details"],
        "status_summary": "RED" if blocking_seen else "GREEN",
        "ok": ok,
        "missing": missing,
        "extra_blocking": extra_blocking,
    }


def main(argv: Optional[List[str]] = None) -> int:
    root = Path(__file__).resolve().parent / "fixtures" / "snapshots"
    if argv and argv[0] not in ("-h", "--help"):
        root = Path(argv[0])
    reports = []
    for snapshot in sorted(p for p in root.iterdir() if p.is_dir()):
        if not (snapshot / "manifest.json").is_file():
            continue
        reports.append(replay_one(snapshot))
    print(json.dumps(reports, ensure_ascii=False, indent=2))
    return 0 if all(item["ok"] for item in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
