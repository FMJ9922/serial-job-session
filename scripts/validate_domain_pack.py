#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 serial-job-session 领域包的结构与状态一致性。

仅使用 Python 标准库。任务卡采用本 skill 明确定义的“受限 YAML”：
顶层 key: value、单行 [] 列表，不支持嵌套对象。这样统计与校验脚本对
同一份数据有确定解释，不会把无法解析的字段静默当成空值。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 复用统计脚本的 PLAN 解析：校验器与统计器必须用同一把尺子，
# 否则校验放过的阶段行会被统计脚本静默读成空值。
import estimate_stats
import instantiate_domain
import menu_parse
import time_ledger


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SKILL_ROOT = Path(__file__).resolve().parent.parent
VERSION_PATH = SKILL_ROOT / "VERSION"


REQUIRED_FILES = {
    "ENV.md": [
        "environment_status:",
        "environment_version:",
        "authorized_roots:",
        "pending_confirmations:",
        "## 探查口径",
        "## 路径",
        "## 探活清单",
        "## 单实例资源",
        "## 控制面写者",
        "## 面（faces）枚举",
        "`max_faces`",
        "## 待确认",
        "## 初始化记录",
    ],
    "CURRENT.md": ["## 现场"],
    "JOB-SHAPES.md": ["## 待做", "## 已结档", "## 已作废"],
    "PLAN.md": [
        "plan_status:",
        "## 口径声明",
        "## 阶段",
        "<!-- AUTO:PLAN-ROLLUP -->",
        "<!-- /AUTO:PLAN-ROLLUP -->",
    ],
    "NORMS.md": ["<!-- AUTO-NORMS:BEGIN -->", "<!-- AUTO-NORMS:END -->"],
    "FACTS.md": [],
    "LOG.md": [],
}

JOB_REQUIRED_KEYS = {
    "id",
    "shape",
    "estimate_group",
    "depends_on",
    "faces",
    "estimate",
    "estimate_source",
    "actor",
    "human_accept",
    "gate",
    "negative_fixtures",
    "expected_live_failures",
    "ruler_ok",
    "status",
    "attempt",
    "started_at",
    "active_started_at",
    "active_ended_at",
    "ended_at",
    "elapsed",
    "ai_net",
    "variance",
}

JOB_STATUSES = {
    "ready",
    "running",
    "done_pending",
    "rework_required",
    "accepted",
    "rejected",
}
CURRENT_STATUSES = {"idle", "running", "awaiting_acceptance"}
TERMINAL_STATUSES = {"accepted", "rejected"}
PLAN_STATUSES = {"draft", "confirmed"}
PHASE_STATUSES = {"pending", "in_progress", "done", "dropped"}
UNCERTAINTY_LEVELS = {"high", "med", "low"}
# 阶段粗估是十几张卡的合计，没有 plan 这个取值；混进单卡会污染偏差率主分布。
ESTIMATE_SOURCE_RE = re.compile(r"menu|fresh|backfill|similar:.+")
SOURCE_HASH_RE = re.compile(r"source_hash:\s*([0-9a-f]+)", re.I)
LEGAL_CLOSE_FROM = {"done_pending", "rework_required", "running"}
OPEN_JOB_STATUSES = {"running", "done_pending", "rework_required"}


def _first_token(value: object) -> str:
    """Return a safe first scalar token for legacy and v4 card fields."""

    parts = str(value or "").split()
    return parts[0] if parts else ""


def compute_plan_source_hash(domain: Path) -> str:
    shapes = domain / "JOB-SHAPES.md"
    plan = domain / "PLAN.md"
    menu_text = shapes.read_text(encoding="utf-8", errors="replace") if shapes.is_file() else ""
    rows, _ = menu_parse.parse_pending_table(menu_text)
    meta, phases, _ = (
        estimate_stats.parse_plan(plan) if plan.is_file() else ({}, [], [])
    )
    payload = {
        "plan_version": meta.get("plan_version"),
        "phases": [
            {
                "name": getattr(phase, "name", None),
                "status": getattr(phase, "status", None),
                "low": getattr(phase, "low", None),
                "high": getattr(phase, "high", None),
                "shapes": list(getattr(phase, "shapes", []) or []),
            }
            for phase in phases
        ],
        "pending": [
            {
                "shape": row.get("shape"),
                "depends_on": row.get("depends_on"),
                "estimate": row.get("estimate"),
            }
            for row in rows
        ],
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def env_cap(env_text: str, needle: str) -> Optional[float]:
    match = re.search(
        r"\|\s*`?%s`?[^|\n]*\|\s*`?(\d+(?:\.\d+)?)" % re.escape(needle),
        env_text,
    )
    if not match:
        return None
    return float(match.group(1))


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def parse_scalar(raw: str):
    text = re.sub(r"\s+#.*$", "", raw.strip()).strip()
    if not text or text.lower() in {"null", "none", "~"}:
        return None
    if text.startswith("[") and text.endswith("]"):
        inner = text[1:-1].strip()
        if not inner:
            return []
        return [part.strip().strip("\"'") for part in inner.split(",") if part.strip()]
    if text.lower() in {"true", "false"}:
        return text.lower() == "true"
    return text.strip("\"'")


def first_yaml(path: Path) -> Tuple[Optional[Dict[str, object]], List[str], str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"```ya?ml\s*\n(.*?)\n```", text, flags=re.S)
    if not match:
        return None, ["没有 fenced yaml 块"], ""
    body = match.group(1)
    data: Dict[str, object] = {}
    errors: List[str] = []
    for lineno, line in enumerate(body.splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")):
            # CURRENT 的 parallel_pending_jobs 是唯一允许的嵌套展示块；
            # 任务卡本身仍必须保持顶层受限 YAML。
            continue
        key, sep, value = line.partition(":")
        if not sep:
            errors.append("YAML 第 %d 行不是 key: value" % lineno)
            continue
        key = key.strip()
        if key in data:
            errors.append("YAML 重复字段：%s" % key)
        data[key] = parse_scalar(value)
    return data, errors, body


def is_positive_number(value) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def validate_job(path: Path) -> Tuple[List[str], Optional[Dict[str, object]]]:
    errors: List[str] = []
    card, parse_errors, yaml_body = first_yaml(path)
    errors.extend(parse_errors)
    if card is None:
        return errors, None

    indented = [
        line
        for line in yaml_body.splitlines()
        if line.startswith((" ", "\t")) and line.strip() and not line.lstrip().startswith("#")
    ]
    if indented:
        errors.append("任务卡主 YAML 不允许嵌套；列表必须使用单行 []")

    missing = sorted(JOB_REQUIRED_KEYS - set(card))
    if missing:
        errors.append("缺必填字段：" + ", ".join(missing))

    status = str(card.get("status") or "")
    if status not in JOB_STATUSES:
        errors.append("非法 status：%s" % (status or "<空>"))

    if not isinstance(card.get("faces"), list):
        errors.append("faces 必须是单行列表")
    if not isinstance(card.get("depends_on"), list):
        errors.append("depends_on 必须是单行列表")
    if not isinstance(card.get("negative_fixtures"), list):
        errors.append("negative_fixtures 必须是单行列表")
    if not isinstance(card.get("expected_live_failures"), list):
        errors.append("expected_live_failures 必须是单行列表")
    if card.get("estimate_group") is None:
        errors.append("estimate_group 必填")
    elif str(card.get("estimate_group")) == str(card.get("shape")):
        errors.append("estimate_group 不得等于唯一 shape 编号")

    source = card.get("estimate_source")
    if source is None:
        if status != "ready":
            errors.append("开工卡 estimate_source 必填")
    elif str(source) == "plan":
        errors.append("estimate_source 不得为 plan：PLAN.md 的阶段粗估不许当单卡预计")
    elif not ESTIMATE_SOURCE_RE.fullmatch(str(source)):
        errors.append(
            "非法 estimate_source：%s（只允许 menu | similar:{job-id} | fresh | backfill）" % source
        )

    try:
        attempt = int(str(card.get("attempt")))
        if attempt < 1:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("attempt 必须是 >= 1 的整数")

    if status != "ready" and not is_positive_number(card.get("estimate")):
        errors.append("开工卡 estimate 必须 > 0")
    if status in {"running", "done_pending", "rework_required"} and card.get("started_at") is None:
        errors.append("%s 卡缺 started_at" % status)
    if status == "running" and card.get("active_started_at") is None:
        errors.append("running 卡缺 active_started_at")
    if status in {"done_pending", "rework_required"} and card.get("active_ended_at") is None:
        errors.append("%s 卡缺 active_ended_at" % status)

    if status in TERMINAL_STATUSES:
        for key in ("started_at", "active_ended_at", "ended_at", "elapsed"):
            if card.get(key) is None:
                errors.append("终态卡缺 %s" % key)
        if card.get("estimate_source") != "backfill":
            ai_net = card.get("ai_net")
            if ai_net is None:
                errors.append("非 backfill 终态卡缺 ai_net；无法复算时显式写 untracked")
            elif ai_net == "untracked":
                if card.get("variance") is not None:
                    errors.append("ai_net:untracked 时 variance 必须为 null")
            elif card.get("variance") is None:
                errors.append("有 ai_net 的终态卡缺 variance")

    ledger = time_ledger.compute_ledger(card, path.read_text(encoding="utf-8", errors="replace"))
    for item in ledger.get("interval_errors") or []:
        errors.append("%s：%s" % (path.name, item))

    if card.get("gate") is True:
        if not card.get("negative_fixtures"):
            errors.append("gate:true 必须有 negative_fixtures")
        if status == "accepted" and card.get("ruler_ok") is not True:
            errors.append("accepted 的 gate 卡必须 ruler_ok:true")

    required_sections = [
        "## 目标",
        "## 依赖门禁",
        "## 下游接口",
        "## 估分依据",
        "## 验收方式",
        "## 产物路径",
        "## live",
        "## 执行区间",
        "## 拟写入 FACTS",
    ]
    text = path.read_text(encoding="utf-8", errors="replace")
    for heading in required_sections:
        if heading not in text:
            errors.append("缺章节：%s" % heading)

    return errors, card


def validate_plan(path: Path) -> Tuple[List[str], List[str]]:
    """校验启动阶段估分单：状态、阶段行合法性，以及「未拆阶段不许写单点值」这条防假精度闸。"""
    errors: List[str] = []
    warnings: List[str] = []
    meta, phases, parse_errors = estimate_stats.parse_plan(path)
    for item in parse_errors:
        tagged = "PLAN.md：%s" % item
        if item.startswith("PHASE_OVER_24H"):
            warnings.append(tagged)
        else:
            errors.append(tagged)

    plan_status = str(meta.get("plan_status") or "")
    if plan_status not in PLAN_STATUSES:
        errors.append("PLAN.md：plan_status 必须是 draft 或 confirmed")
    try:
        if int(str(meta.get("plan_version"))) < 1:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("PLAN.md：plan_version 必须是 >= 1 的整数")
    if not isinstance(meta.get("library_ref"), list):
        errors.append("PLAN.md：library_ref 必须是单行列表")

    if plan_status == "confirmed":
        for key in ("estimated_at", "confirmed_at"):
            if meta.get(key) is None:
                errors.append("PLAN.md：confirmed 时 %s 必填" % key)
        if not phases:
            errors.append("PLAN.md：confirmed 时「阶段」表不得为空")

    for phase in phases:
        label = phase.name or "<空>"
        if phase.status not in PHASE_STATUSES:
            errors.append(
                "PLAN.md：阶段「%s」非法状态 %s（pending | in_progress | done | dropped）"
                % (label, phase.status or "<空>")
            )
        if phase.unc not in UNCERTAINTY_LEVELS:
            errors.append("PLAN.md：阶段「%s」unc 必须是 high | med | low" % label)
        if not phase.criterion:
            errors.append("PLAN.md：阶段「%s」缺收口判据" % label)
        if phase.status == "dropped":
            continue
        if phase.low is None or phase.high is None:
            errors.append("PLAN.md：阶段「%s」缺预计区间" % label)
            continue
        single_point = phase.high <= phase.low
        if single_point and not (phase.unc == "low" and phase.shapes):
            errors.append(
                "PLAN.md：阶段「%s」写成单点工时；未拆成菜单条目（或 unc 非 low）时必须写区间" % label
            )
        elif phase.unc == "high" and phase.high < 2 * phase.low:
            warnings.append(
                "PLAN.md：阶段「%s」标 unc:high 但区间宽度不足 2 倍，确认不是假精度" % label
            )
        if phase.high > 16:
            warnings.append(
                "PHASE_OVER_16H：阶段「%s」超过推荐 16h，须写清不能继续拆的理由" % label
            )
    shape_owner: Dict[int, str] = {}
    for phase in phases:
        for shape in phase.shapes:
            prior = shape_owner.get(shape)
            if prior and prior != phase.name:
                errors.append(
                    "SHAPE_MULTI_PHASE：shape %s 同时出现在「%s」与「%s」"
                    % (shape, prior, phase.name)
                )
            else:
                shape_owner[shape] = phase.name
    return errors, warnings


def validate_domain(domain: Path, allow_draft: bool = False) -> dict:
    errors: List[str] = []
    warnings: List[str] = []

    if not domain.is_dir():
        return {"ok": False, "errors": ["领域包目录不存在：%s" % domain], "warnings": []}

    domain = domain.resolve()
    if is_within(domain, SKILL_ROOT.resolve()):
        errors.append("实例目录位于 skill 母版内部，禁止运行：%s" % domain)

    instance_path = domain / "INSTANCE.json"
    instance: Dict[str, object] = {}
    library_path: Optional[str] = None
    if not instance_path.is_file():
        errors.append("缺文件：INSTANCE.json；必须使用 instantiate_domain.py 创建实例")
    else:
        try:
            loaded = json.loads(instance_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("根必须是 object")
            instance = loaded
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append("INSTANCE.json 无效：%s" % exc)
        if instance:
            if instance.get("schema") not in {
                "serial-job-session-instance/v1",
                "serial-job-session-instance/v2",
            }:
                errors.append("INSTANCE.json：schema 不受支持")
            if instance.get("source_skill") != "serial-job-session":
                errors.append("INSTANCE.json：source_skill 不匹配")
            if not instance.get("instance_id"):
                errors.append("INSTANCE.json：缺 instance_id")
            for key in ("instance_root", "writable_scope"):
                value = instance.get(key)
                if not value:
                    errors.append("INSTANCE.json：%s 必须等于当前实例根" % key)
                    continue
                resolved = instantiate_domain.resolve_declared(value, domain)
                if resolved != domain:
                    errors.append("INSTANCE.json：%s 必须等于当前实例根" % key)
            configured_library = instance.get("estimate_library")
            if not configured_library:
                warnings.append(
                    "INSTANCE.json 缺 estimate_library：本实例结档数据不会进跨实例估分库（旧实例可补写该字段）"
                )
            else:
                candidate = instantiate_domain.resolve_declared(
                    configured_library, domain
                )
                if is_within(candidate, SKILL_ROOT.resolve()):
                    errors.append(
                        "INSTANCE.json：estimate_library 位于 skill 母版内，禁止运行：%s" % candidate
                    )
                else:
                    library_path = instantiate_domain.to_portable(candidate, domain)
            try:
                current_version = VERSION_PATH.read_text(encoding="utf-8").strip()
                instance_version = str(instance.get("protocol_version") or "")
                instance_major = instance_version.split(".", 1)[0]
                current_major = current_version.split(".", 1)[0]
                if instance_major == "3" and current_major == "4":
                    # Protocol v3 instances remain live during migration.  The
                    # runtime guard dispatches them to their v3 validators;
                    # VERSION 4 must not make an untouched v3 instance fail
                    # solely because the skill has advanced.
                    warnings.append(
                        "INSTANCE.json：保留协议 v3，由运行时按 v3 分流（实例 %s / skill %s）"
                        % (instance_version or "<空>", current_version)
                    )
                elif instance_major != current_major:
                    errors.append(
                        "INSTANCE.json：协议主版本不兼容（实例 %s / skill %s）"
                        % (instance_version or "<空>", current_version)
                    )
                elif instance_version != current_version:
                    warnings.append(
                        "INSTANCE.json：协议版本不同（实例 %s / skill %s），建议显式迁移"
                        % (instance_version, current_version)
                    )
            except OSError as exc:
                errors.append("无法读取 skill VERSION：%s" % exc)

    for path in domain.rglob("*"):
        try:
            resolved = path.resolve()
        except OSError as exc:
            errors.append("无法解析实例路径 %s：%s" % (path.relative_to(domain), exc))
            continue
        if not is_within(resolved, domain):
            errors.append("实例包含指向外部的链接或 junction：%s" % path.relative_to(domain))

    for name, markers in REQUIRED_FILES.items():
        path = domain / name
        if not path.is_file():
            errors.append("缺文件：%s" % name)
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "{{" in text:
            errors.append("%s 仍有 {{…}} 占位符" % name)
        for marker in markers:
            if marker not in text:
                errors.append("%s 缺标记：%s" % (name, marker))

    plan_path = domain / "PLAN.md"
    if plan_path.is_file():
        plan_errors, plan_warnings = validate_plan(plan_path)
        errors.extend(plan_errors)
        warnings.extend(plan_warnings)

    env_path = domain / "ENV.md"
    environment_status: Optional[str] = None
    if env_path.is_file():
        env_text = env_path.read_text(encoding="utf-8", errors="replace")
        env, env_parse_errors, env_yaml = first_yaml(env_path)
        errors.extend("ENV.md：%s" % item for item in env_parse_errors)
        if env:
            environment_status = str(env.get("environment_status") or "")
            if environment_status not in {"draft", "locked"}:
                errors.append("ENV.md：environment_status 必须是 draft 或 locked")
            elif environment_status == "draft":
                if allow_draft:
                    warnings.append("ENV.md 仍是 draft：只允许继续 Bootstrap，不得开任务卡")
                else:
                    errors.append(
                        "ENV.md 仍是 draft；确认并锁定后再开工，草案检查请加 --allow-draft"
                    )
            elif environment_status == "locked":
                if env.get("confirmed_at") is None:
                    errors.append("ENV.md：locked 时 confirmed_at 必填")
                if env.get("pending_confirmations") != []:
                    errors.append("ENV.md：locked 时 pending_confirmations 必须是 []")
                if "无待确认项。" not in env_text:
                    errors.append("ENV.md：locked 时“待确认”节必须写“无待确认项。”")

            if env.get("domain_name") is None:
                errors.append("ENV.md：domain_name 必填")
            elif instance and str(env.get("domain_name")) != str(instance.get("domain_name")):
                errors.append("ENV.md：domain_name 与 INSTANCE.json 不一致")
            roots = env.get("authorized_roots")
            if not isinstance(roots, list) or not roots:
                errors.append("ENV.md：authorized_roots 必须是非空单行列表")
            if not isinstance(env.get("pending_confirmations"), list):
                errors.append("ENV.md：pending_confirmations 必须是单行列表")
            if env.get("discovered_at") is None:
                errors.append("ENV.md：discovered_at 必填")
            try:
                version = int(str(env.get("environment_version")))
                if version < 1:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append("ENV.md：environment_version 必须是 >= 1 的整数")

        max_faces_match = re.search(r"\|\s*`max_faces`\s*\|\s*`?(\d+)", env_text)
        if not max_faces_match:
            errors.append("ENV.md：无法读取 max_faces，必须在领域参数表写 1–3")
        elif int(max_faces_match.group(1)) not in {1, 2, 3}:
            errors.append("ENV.md：max_faces 必须为 1–3")

    jobs_dir = domain / "jobs"
    if not jobs_dir.is_dir():
        errors.append("缺目录：jobs/")
        return {"ok": False, "errors": errors, "warnings": warnings}

    cards: Dict[str, Dict[str, object]] = {}
    for path in sorted(jobs_dir.glob("*.md")):
        if path.name.startswith("_"):
            continue
        if "{{" in path.read_text(encoding="utf-8", errors="replace"):
            errors.append("%s 仍有 {{…}} 占位符" % path.relative_to(domain))
        job_errors, card = validate_job(path)
        errors.extend("%s：%s" % (path.relative_to(domain), item) for item in job_errors)
        if card:
            job_id = str(card.get("id") or path.stem)
            if job_id in cards:
                errors.append("重复 job id：%s" % job_id)
            cards[job_id] = card
            if job_id != path.stem:
                warnings.append("%s：文件名与 id 不同" % path.relative_to(domain))

    if environment_status == "draft" and cards:
        errors.append("ENV.md 仍是 draft 时不得存在已开任务卡")

    current_path = domain / "CURRENT.md"
    if current_path.is_file():
        current, current_errors, current_yaml = first_yaml(current_path)
        errors.extend("CURRENT.md：%s" % item for item in current_errors)
        if current:
            status = str(current.get("status") or "")
            active_job = current.get("active_job")
            if status not in CURRENT_STATUSES:
                errors.append("CURRENT.md：非法 status %s" % (status or "<空>"))
            if status == "idle" and active_job is not None:
                errors.append("CURRENT.md：idle 时 active_job 必须为空")
            if status in {"running", "awaiting_acceptance"}:
                if active_job is None:
                    errors.append("CURRENT.md：%s 时 active_job 必填" % status)
                elif str(active_job) not in cards:
                    errors.append("CURRENT.md：active_job 不存在对应任务卡：%s" % active_job)
                else:
                    job_status = str(cards[str(active_job)].get("status") or "")
                    expected = {"running"} if status == "running" else {"done_pending"}
                    if job_status not in expected:
                        errors.append(
                            "CURRENT.md：%s 与任务卡 status=%s 不一致" % (status, job_status)
                        )

        for job_id in re.findall(r"^\s+-\s+job_id:\s*([^\s#]+)", current_yaml, flags=re.M):
            if job_id not in cards:
                errors.append("CURRENT.md：并行 job 不存在对应任务卡：%s" % job_id)

    if instance:
        schema = str(instance.get("schema") or "")
        if schema.endswith("/v2") and not instance.get("route_key"):
            errors.append("INSTANCE.json：v2 缺 route_key")
        if instance.get("route_key") and current_path.is_file():
            current_text = current_path.read_text(encoding="utf-8", errors="replace")
            if "control_mode:" not in current_text:
                warnings.append("CURRENT.md：建议声明 control_mode: manual|managed")
            if "managed_run_id:" not in current_text:
                warnings.append("CURRENT.md：建议声明 managed_run_id")
            if "lease_holder:" not in current_text:
                warnings.append("CURRENT.md：建议声明 lease_holder（与并行/托管共用，勿另起控制面）")
            if "active_transaction:" not in current_text:
                warnings.append("CURRENT.md：建议声明 active_transaction")

    tx_journal = domain / ".sjs-tx" / "journal.json"
    if tx_journal.is_file():
        try:
            tx = json.loads(tx_journal.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            errors.append("未完成事务 journal 不可读")
        else:
            if tx.get("phase") == "prepared":
                errors.append("UNFINISHED_TRANSACTION：禁止开新卡，先 recover/rollback")
            outbox = domain / ".sjs-tx" / "outbox.json"
            if outbox.is_file():
                box = json.loads(outbox.read_text(encoding="utf-8"))
                if box.get("pending"):
                    warnings.append("POST_CLOSE_PENDING：" + ",".join(box["pending"]))

    pending_index = re.findall(
        r"^(\d+)\.\s+\*\*.+?\*\*.*?依赖：([^）)]+)",
        (domain / "JOB-SHAPES.md").read_text(encoding="utf-8", errors="replace")
        if (domain / "JOB-SHAPES.md").is_file()
        else "",
        flags=re.M,
    )
    menu_text = (
        (domain / "JOB-SHAPES.md").read_text(encoding="utf-8", errors="replace")
        if (domain / "JOB-SHAPES.md").is_file()
        else ""
    )
    table_rows, table_errors = menu_parse.parse_pending_table(menu_text)
    for item in table_errors:
        if item == "MENU_TABLE_UNPARSED":
            warnings.append(item)
        else:
            errors.append(item)
    for dup in menu_parse.duplicate_shapes(table_rows):
        errors.append("MENU_DUP_SHAPE：%s" % dup)
    for node in menu_parse.cycle_shapes(table_rows):
        errors.append("MENU_CYCLE：%s" % node)
    feishu_bind = domain / "feishu.json"
    if feishu_bind.is_file():
        try:
            feishu_data = json.loads(feishu_bind.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            errors.append("FEISHU_BINDING_UNREADABLE")
            feishu_data = {}
        if feishu_data.get("enabled") is not False:
            for row in table_rows:
                short = (row.get("short_name") or "").strip()
                if not short:
                    errors.append("SHORT_NAME_MISSING：shape %s" % row["shape"])
                    continue
                for issue in menu_parse.short_name_issues(short):
                    errors.append("%s：shape %s" % (issue, row["shape"]))

    detail_deps = menu_parse.parse_detail_deps(menu_text)
    for row in table_rows:
        listed = detail_deps.get(row["shape"])
        if listed is not None and set(listed) != set(row["depends_on"]):
            errors.append("MENU_DEP_DRIFT：shape %s" % row["shape"])
    menu_deps = {}
    for num, deps in pending_index:
        menu_deps[int(num)] = [int(p.strip()) for p in deps.split(",") if p.strip().isdigit()]
    for row in table_rows:
        menu_deps.setdefault(row["shape"], row["depends_on"])
    for job_id, card in cards.items():
        if str(card.get("status")) in TERMINAL_STATUSES:
            continue
        shape_raw = _first_token(card.get("shape"))
        try:
            shape = int(shape_raw)
        except ValueError:
            continue
        card_deps = []
        for item in card.get("depends_on") or []:
            try:
                card_deps.append(int(_first_token(item)))
            except ValueError:
                continue
        if shape in menu_deps:
            menu = set(menu_deps[shape])
            have = set(card_deps)
            missing = sorted(menu - have)
            extra = sorted(have - menu)
            if missing:
                errors.append(
                    "STALE_DEPENDENCY：%s 卡缺少菜单依赖 %s" % (job_id, missing)
                )
            elif extra:
                warnings.append(
                    "DEPENDS_EXTRA：%s 卡比菜单多依赖 %s" % (job_id, extra)
                )

    env_text = ""
    if env_path.is_file():
        env_text = env_path.read_text(encoding="utf-8", errors="replace")
    env_data, _, _ = first_yaml(env_path) if env_path.is_file() else ({}, [], "")
    env_data = env_data or {}
    if instance.get("route_key") and env_data.get("route_key"):
        if str(instance.get("route_key")) != str(env_data.get("route_key")):
            errors.append(
                "IDENTITY_MISMATCH：INSTANCE.route_key=%s ENV.route_key=%s"
                % (instance.get("route_key"), env_data.get("route_key"))
            )
    if instance.get("instance_id") and env_data.get("instance_id"):
        if str(instance.get("instance_id")) != str(env_data.get("instance_id")):
            errors.append("IDENTITY_MISMATCH：instance_id 不一致")

    registry_ref = instance.get("registry_ref")
    if registry_ref and instance.get("instance_id"):
        import registry as registry_mod

        repo = registry_mod.find_repo_root(domain)
        declared = instantiate_domain.resolve_declared(str(registry_ref), domain)
        if declared.is_file():
            try:
                payload = json.loads(declared.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                errors.append("REGISTRY_UNREADABLE")
            else:
                hits = [
                    item
                    for item in payload.get("instances") or []
                    if str(item.get("instance_id")) == str(instance.get("instance_id"))
                    or str(item.get("route_key")) == str(instance.get("route_key") or "")
                ]
                if not hits:
                    errors.append("IDENTITY_MISMATCH：注册表找不到本实例")
                else:
                    entry = hits[0]
                    if instance.get("route_key") and entry.get("route_key") != instance.get("route_key"):
                        errors.append("IDENTITY_MISMATCH：注册表 route_key")
                    root = instantiate_domain.resolve_declared(str(entry.get("instance_root") or ""), domain)
                    if root != domain:
                        errors.append("IDENTITY_MISMATCH：注册表 instance_root")

    max_faces = None
    faces_match = re.search(r"\|\s*`max_faces`\s*\|\s*`?(\d+)", env_text)
    if faces_match:
        max_faces = int(faces_match.group(1))
    estimate_cap = env_cap(env_text, "estimate")
    known_shapes = set(menu_deps)
    for row in table_rows:
        known_shapes.add(row["shape"])
    for match in re.finditer(r"^\|\s*(\d+)\s*\|", menu_text, flags=re.M):
        known_shapes.add(int(match.group(1)))
    for match in re.finditer(r"^(\d+)\.\s+", menu_text, flags=re.M):
        known_shapes.add(int(match.group(1)))
    decision_by_shape: Dict[int, Dict[str, object]] = {}
    for job_id, card in cards.items():
        try:
            shape_n = int(_first_token(card.get("shape")))
        except ValueError:
            continue
        faces = card.get("faces") if isinstance(card.get("faces"), list) else []
        if max_faces is not None and len(faces) > max_faces:
            errors.append("FACES_OVER_MAX：%s len(faces)=%d > %d" % (job_id, len(faces), max_faces))
        estimate_val = card.get("estimate_total") or card.get("estimate")
        if estimate_cap is not None and is_positive_number(estimate_val) and float(estimate_val) > estimate_cap:
            errors.append("ESTIMATE_OVER_CAP：%s estimate=%s > %s" % (job_id, estimate_val, estimate_cap))
        if str(card.get("status")) in OPEN_JOB_STATUSES and current_path.is_file():
            current_meta, _, _ = first_yaml(current_path)
            if current_meta and str(current_meta.get("status") or "") == "idle":
                if str(current_meta.get("active_job") or "") not in {job_id, "null", ""}:
                    errors.append("ILLEGAL_STATE：idle 时仍有 running/open 卡 %s" % job_id)
        if card.get("decision") is True:
            decision_by_shape[shape_n] = card
        pre_shape = card.get("precondition_shape")
        pre_result = str(card.get("precondition_result") or "")
        if pre_shape not in {None, "", "null"}:
            try:
                pre_n = int(_first_token(pre_shape))
            except ValueError:
                pre_n = None
            if pre_n is not None:
                parent = decision_by_shape.get(pre_n)
                if parent is None:
                    for other in cards.values():
                        try:
                            if int(_first_token(other.get("shape"))) == pre_n:
                                parent = other
                                break
                        except ValueError:
                            continue
                if parent is None:
                    errors.append("PRECONDITION_UNMET：%s 找不到决策卡 %s" % (job_id, pre_n))
                elif str(parent.get("status")) != "accepted":
                    errors.append("PRECONDITION_UNMET：%s 决策卡未结档" % job_id)
                elif str(card.get("status")) in {"accepted", "rejected"}:
                    pass
                elif pre_result and str(parent.get("decision_result") or parent.get("result") or "") not in {
                    pre_result,
                    "",
                    "null",
                    None,
                }:
                    actual = parent.get("decision_result") or parent.get("result")
                    if actual not in {None, "", "null"} and str(actual) != pre_result:
                        errors.append(
                            "PRECONDITION_MISMATCH：%s want %s got %s" % (job_id, pre_result, actual)
                        )
        for dep in card.get("depends_on") or []:
            try:
                dep_n = int(_first_token(dep))
            except ValueError:
                continue
            if known_shapes and dep_n not in known_shapes:
                errors.append("MENU_ORPHAN_DEP：%s depends_on %s 不在菜单" % (job_id, dep_n))

    if current_path.is_file():
        current_meta, _, current_yaml = first_yaml(current_path)
        current_meta = current_meta or {}
        if str(current_meta.get("status") or "") == "idle":
            for job_id, card in cards.items():
                if str(card.get("status")) in OPEN_JOB_STATUSES:
                    errors.append("ILLEGAL_STATE：CURRENT idle 但 %s 为 %s" % (job_id, card.get("status")))
        lease_raw = current_meta.get("lease_resources") or []
        if isinstance(lease_raw, str):
            lease_held = re.findall(r"[A-Za-z0-9_-]+", lease_raw)
        elif isinstance(lease_raw, list):
            lease_held = [str(x) for x in lease_raw]
        else:
            lease_held = []
        holder = current_meta.get("lease_holder")
        if holder and lease_held:
            for job_id, card in cards.items():
                if str(card.get("id") or job_id) == str(holder):
                    continue
                if str(card.get("status")) not in OPEN_JOB_STATUSES:
                    continue
                faces = {str(x).lower() for x in (card.get("faces") or [])}
                overlap = faces & {x.lower() for x in lease_held} & {"houdini", "unity", "heu"}
                if overlap:
                    errors.append("LEASE_CONFLICT：%s 与 lease %s 争用 %s" % (job_id, holder, sorted(overlap)))
        wf_depth = None
        for job_id, card in cards.items():
            if str(card.get("execution_mode")) == "embedded_workflow":
                try:
                    depth = int(str(card.get("workflow_max_depth") or 1))
                except ValueError:
                    depth = 1
                if depth > 1:
                    errors.append("WORKFLOW_DEPTH：%s max_depth=%s" % (job_id, depth))

    plan_path = domain / "PLAN.md"
    if plan_path.is_file() and "<!-- AUTO:PLAN-ROLLUP -->" in plan_path.read_text(
        encoding="utf-8", errors="replace"
    ):
        plan_text = plan_path.read_text(encoding="utf-8", errors="replace")
        auto = plan_text.split("<!-- AUTO:PLAN-ROLLUP -->")[-1].split("<!-- /AUTO:PLAN-ROLLUP -->")[0]
        stored = SOURCE_HASH_RE.search(auto)
        digest = compute_plan_source_hash(domain)
        if stored is None:
            warnings.append("PLAN_ROLLUP_NO_HASH")
        elif stored.group(1) != digest:
            errors.append("PLAN_ROLLUP_STALE：stored %s live %s" % (stored.group(1), digest))

    tx_journal = domain / ".sjs-tx" / "journal.json"
    if tx_journal.is_file():
        try:
            tx = json.loads(tx_journal.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            tx = {}
        if tx.get("phase") == "committed":
            outbox = domain / ".sjs-tx" / "outbox.json"
            if not outbox.is_file():
                errors.append("POST_CLOSE_DROPPED：committed 后 outbox 缺失")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "jobs": len(cards),
        "environment_status": environment_status,
        "estimate_library": library_path,
        "plan_source_hash": compute_plan_source_hash(domain) if (domain / "PLAN.md").is_file() else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 serial-job-session 领域包")
    parser.add_argument("--domain", required=True, help="领域包根目录")
    parser.add_argument(
        "--allow-draft",
        action="store_true",
        help="允许 environment_status:draft，仅供 Bootstrap 草案检查；仍禁止开任务卡",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    result = validate_domain(
        Path(args.domain).expanduser().resolve(),
        allow_draft=args.allow_draft,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in result.get("errors", []):
            print("[error] " + item)
        for item in result.get("warnings", []):
            print("[warn] " + item)
        if result["ok"]:
            print("OK：领域包结构与状态一致（任务卡 %d 张）" % result.get("jobs", 0))
        else:
            print("FAILED：%d 个错误，%d 个警告" % (len(result["errors"]), len(result["warnings"])))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
