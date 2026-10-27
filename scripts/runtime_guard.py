#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""领域运行前的一键硬闸：母版完整性 + 实例隔离 + 领域状态。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import check_skill_integrity
import validate_domain_pack
import registry as sjs_registry
from close_transaction import load_journal
import sjs_v4


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


def preflight(instance: Path, allow_draft: bool = False) -> dict:
    # Version is an instance property.  A v4 skill release must not force the
    # still-live protocol-v3 instance through v4's card/state checks.
    try:
        manifest_path = instance / "INSTANCE.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    except (OSError, ValueError):
        manifest = {}
    protocol = str(manifest.get("protocol_version") or "")
    if protocol.split(".", 1)[0] == "4":
        integrity = check_skill_integrity.check_manifest()
        root = instance.resolve()
        errors = []
        warnings = []
        if validate_domain_pack.is_within(root, validate_domain_pack.SKILL_ROOT.resolve()):
            errors.append("实例目录位于 skill 母版内部，禁止运行：%s" % root)
        for key in ("instance_root", "writable_scope"):
            declared = manifest.get(key)
            if not declared:
                errors.append("INSTANCE.json：%s 必须等于当前实例根" % key)
                continue
            try:
                resolved = validate_domain_pack.instantiate_domain.resolve_declared(declared, root)
            except Exception as exc:
                errors.append("INSTANCE.json：%s 无法解析：%s" % (key, exc))
                continue
            if resolved != root:
                errors.append("INSTANCE.json：%s 必须等于当前实例根" % key)
        # A v4 run still obeys ENV isolation.  Do not infer locked from the
        # protocol number: Bootstrap drafts and a half-confirmed environment
        # must remain visible to the caller.
        environment_status = None
        env_path = root / "ENV.md"
        if not env_path.is_file():
            errors.append("缺文件：ENV.md")
        else:
            env, env_errors, _ = validate_domain_pack.first_yaml(env_path)
            errors.extend("ENV.md：%s" % item for item in env_errors)
            environment_status = str((env or {}).get("environment_status") or "")
            if environment_status == "draft":
                if allow_draft:
                    warnings.append("ENV.md 仍是 draft：只允许继续 Bootstrap，不得开任务卡")
                else:
                    errors.append("ENV.md 仍是 draft；确认并锁定后再开工")
            elif environment_status != "locked":
                errors.append("ENV.md：environment_status 必须是 draft 或 locked")
            elif (env or {}).get("confirmed_at") is None or (env or {}).get("pending_confirmations") != []:
                errors.append("ENV.md：locked 时确认字段不完整")
        # Reject links/junctions that escape the instance.  This check is
        # intentionally kept in the v4 path rather than relying on the
        # bounded card snapshot.
        for path in root.rglob("*"):
            try:
                if not validate_domain_pack.is_within(path.resolve(), root):
                    errors.append("实例包含指向外部的链接或 junction：%s" % path.relative_to(root))
            except OSError as exc:
                errors.append("无法解析实例路径 %s：%s" % (path.relative_to(root), exc))
        snapshot = sjs_v4.load_snapshot(root)
        unfinished = sjs_v4._find_unfinished_transaction(root, snapshot.get("instance") or {})
        if unfinished:
            errors.append("未完成 v4 事务：%s" % unfinished.get("command_id"))
        domain = sjs_v4.check_instance(root, compare_projections=False)
        snapshot = domain.pop("snapshot", {}) or {}
        domain_errors = list(domain.get("errors") or [])
        domain_warnings = list(domain.get("warnings") or [])
        domain_errors.extend(errors)
        domain_warnings.extend(warnings)
        active = sum(1 for card in snapshot.get("cards") or [] if card.get("status") in {"doing", "awaiting_acceptance"})
        domain = {
            "ok": not domain_errors,
            "errors": domain_errors,
            "warnings": list(dict.fromkeys(domain_warnings)),
            "revision": snapshot.get("revision", 0),
            "card_count": len(snapshot.get("cards") or []),
            "active_count": active,
            "environment_status": environment_status,
            "derived_views": {
                str(path): bool((snapshot.get("views") or {}).get(path, {}).get("exists"))
                for path in sjs_v4.DERIVED_FILES
            },
        }
        return {
            "ok": bool(domain.get("ok")) and (integrity.get("ok") if integrity else False),
            "stage": "ready" if domain.get("ok") and integrity.get("ok") else "instance_validation",
            "integrity": integrity,
            "domain": domain,
            "estimate_library": (manifest.get("estimate_library") if isinstance(manifest, dict) else None),
            "protocol_dispatch": "v4",
        }
    journal = load_journal(instance)
    if journal and journal.get("phase") == "prepared":
        return {
            "ok": False,
            "stage": "unfinished_transaction",
            "integrity": None,
            "domain": None,
            "error": "未完成 Close 事务，只允许 recover 或 rollback",
        }
    integrity = check_skill_integrity.check_manifest()
    if not integrity["ok"]:
        return {
            "ok": False,
            "stage": "skill_integrity",
            "integrity": integrity,
            "domain": None,
        }
    domain = validate_domain_pack.validate_domain(instance, allow_draft=allow_draft)
    return {
        "ok": domain["ok"],
        "stage": "ready" if domain["ok"] else "instance_validation",
        "integrity": integrity,
        "domain": domain,
        # 估分库跨实例、跨项目，路径隔离与实例本身同等重要：落进母版即判红。
        "estimate_library": domain.get("estimate_library"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="serial-job-session 运行时一键硬闸")
    parser.add_argument("--instance", required=False, help="领域实例根目录")
    parser.add_argument("--route-key", help="从注册表解析实例")
    parser.add_argument(
        "--allow-draft",
        action="store_true",
        help="仅 Bootstrap 使用；允许 draft，但仍禁止运行任务卡",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    if args.route_key:
        entry = sjs_registry.resolve(args.route_key)
        if not entry:
            print("HARD STOP：ROUTE_KEY_NOT_FOUND")
            return 1
        repo = sjs_registry.find_repo_root()
        path_errors = sjs_registry.validate_entry(entry, repo)
        if path_errors:
            print("HARD STOP：" + ";".join(path_errors))
            return 1
        instance = Path(entry["instance_root"])
        if not instance.is_absolute():
            instance = (repo / instance).resolve()
    elif args.instance:
        instance = Path(args.instance).expanduser().resolve()
    else:
        print("HARD STOP：需要 --instance 或 --route-key")
        return 1
    result = preflight(instance, args.allow_draft)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["ok"]:
        status = result["domain"].get("environment_status")
        if status == "draft":
            print("OK：母版完整，实例草案合法；仅允许继续 Bootstrap。")
        else:
            print("OK：母版完整，实例隔离与状态校验通过，可以执行被点名任务。")
        print("估分库：%s" % (result.get("estimate_library") or "未配置（结档数据不进跨实例库）"))
        for item in (result.get("domain") or {}).get("warnings", []):
            print("[warn] " + item)
    else:
        print("HARD STOP：%s 未通过。" % result["stage"])
        integrity = result.get("integrity") or {}
        for key in ("missing", "extra", "modified"):
            if integrity.get(key):
                print("[error] skill %s：%s" % (key, "、".join(integrity[key])))
        domain = result.get("domain") or {}
        for item in domain.get("errors", []):
            print("[error] " + item)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
