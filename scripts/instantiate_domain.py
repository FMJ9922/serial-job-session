#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从只读母版安全创建 serial-job-session 领域实例。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

import check_skill_integrity
import registry as sjs_registry


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


SKILL_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_ROOT = SKILL_ROOT / "domain-pack-template"
VERSION_FILE = SKILL_ROOT / "VERSION"

LIBRARY_DIRNAME = ".serial-job-session"
LIBRARY_FILENAME = "estimate-library.jsonl"
PRIVATE_DIRNAME = "private"
PRIVATE_GITIGNORE = (
    "# serial-job-session 本机运行时（Close / migrate 回滚快照）。勿提交。\n"
    "*\n"
    "!.gitignore\n"
)
FORBIDDEN_INSTANCE_DIRNAMES = frozenset(
    {"feature-docs", "system-docs", "drafts", "human-readme"}
)

# The skill VERSION remains the legacy/v3 maintenance version until the v4
# release is explicitly cut.  New instances nevertheless use the v4 card and
# runtime contract by default; an untouched v3 instance is never rewritten by
# this module.  Keeping the two values separate is what lets a caller create a
# migration fixture without changing the repository's release marker.
V4_PROTOCOL_VERSION = "4.0.0"
V4_REQUIRED_READS = ("INSTANCE.json", "ENV.md", "NORTH-STAR.md", "CURRENT.md")
V3_REQUIRED_READS = ("ENV.md", "CURRENT.md", "JOB-SHAPES.md")


def normalize_instance_version(value: Optional[str] = "v4", *, legacy: bool = False) -> str:
    """Return the explicit instance generation (``v4`` or ``v3``).

    ``legacy`` is a CLI/API convenience for callers that do not want to rely
    on a spelling for the old protocol.  It is intentionally opt-in so a
    missing argument cannot silently create another legacy pack.
    """

    raw = "v4" if value is None else str(value).strip().casefold()
    if legacy:
        if raw not in {"", "v4", "4", "4.x", "latest"}:
            raise ValueError("legacy 与 instance_version 冲突：%s" % value)
        raw = "v3"
    if raw in {"v4", "4", "4.x", "4.0", "4.0.0", "latest", "default"}:
        return "v4"
    if raw in {"v3", "3", "3.x", "3.0", "3.0.0", "legacy", "old"}:
        return "v3"
    raise ValueError("不支持的 instance-version：%s（可选 v4、v3、legacy）" % value)


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def load_version() -> str:
    return VERSION_FILE.read_text(encoding="utf-8").strip()


def find_repo_root(start: Path) -> Path:
    """向上找 `.git`（目录或 worktree 文件）。不调子进程，避免依赖 git 可执行。"""
    for candidate in [start, *start.parents]:
        if (candidate / ".git").exists():
            return candidate
    return None


def posix_rel(path: Path, base: Path):
    """path 相对 base 的 posix 字符串；不在 base 下则 None。"""
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return None


def to_portable(path: Path, start: Path) -> str:
    """优先写成 git 仓库根的相对路径（正斜杠）；不在仓库内则仍写绝对路径。"""
    repo = find_repo_root(start)
    if repo is not None:
        rel = posix_rel(path, repo)
        if rel is not None:
            return rel
    return str(path.resolve())


def resolve_declared(value, instance_root: Path) -> Path:
    """读 INSTANCE.json 里的路径：绝对路径原样解析；相对路径相对仓库根（无仓库则相对实例上级）。"""
    raw = str(value).strip().replace("\\", "/")
    candidate = Path(raw)
    if candidate.is_absolute():
        return candidate.expanduser().resolve()
    repo = find_repo_root(instance_root)
    if repo is not None:
        return (repo / raw).resolve()
    return (instance_root.parent / raw).resolve()


def resolve_library(target: Path, skill_root: Path):
    """跨实例估分库：默认落实例所在 git 仓库根，随仓库提交，全组共用。"""
    repo_root = find_repo_root(target.parent)
    scope = "repo" if repo_root is not None else "instance-parent"
    base = repo_root if repo_root is not None else target.parent
    library = (base / LIBRARY_DIRNAME / LIBRARY_FILENAME).resolve()
    if is_within(library, skill_root):
        raise ValueError("估分库不得落在 skill 母版内：%s" % library)
    return library, scope


def instance_id_of(instance_root: Path) -> str:
    manifest = instance_root / "INSTANCE.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            iid = str(data.get("instance_id") or "").strip()
            if iid:
                return iid
        except (OSError, TypeError, json.JSONDecodeError):
            pass
    digest = hashlib.sha256(str(instance_root.resolve()).encode("utf-8")).hexdigest()
    return digest[:16]


def ensure_private_gitignore(private: Path) -> Path:
    private.mkdir(parents=True, exist_ok=True)
    marker = private / ".gitignore"
    if not marker.is_file():
        marker.write_text(PRIVATE_GITIGNORE, encoding="utf-8", newline="\n")
    return private


def resolve_private_root(instance_root: Path, skill_root: Optional[Path] = None) -> Optional[Path]:
    """Git 仓内：`{repo}/.serial-job-session/private`。无仓库则 None。"""
    repo = find_repo_root(instance_root.resolve())
    if repo is None:
        return None
    private = (repo / LIBRARY_DIRNAME / PRIVATE_DIRNAME).resolve()
    root = (skill_root or SKILL_ROOT).resolve()
    if is_within(private, root):
        raise ValueError("运行时 private 不得落在 skill 母版内：%s" % private)
    return private


def runtime_backup_dir(
    instance_root: Path,
    kind: str,
    command_id: Optional[str] = None,
) -> Path:
    """Close / migrate 快照。有 git 时离开领域包，落到仓库 private；无 git 才回退实例 `.sjs-tx`。"""
    instance_root = instance_root.resolve()
    private = resolve_private_root(instance_root)
    if private is None:
        folder = "backup" if kind == "tx-backup" else kind
        dest = instance_root / ".sjs-tx" / folder
        if command_id:
            dest = dest / str(command_id)
        dest.mkdir(parents=True, exist_ok=True)
        return dest
    ensure_private_gitignore(private)
    dest = private / kind / instance_id_of(instance_root)
    if command_id:
        dest = dest / str(command_id)
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def forbidden_docs_dirname(path: Path) -> Optional[str]:
    """路径任一截断名落在文档树则返回该名。"""
    for part in path.resolve().parts:
        if part.lower() in FORBIDDEN_INSTANCE_DIRNAMES:
            return part
    return None


def default_workflows_root(start: Path) -> Optional[Path]:
    """Default instance root: `{repo}/.cursor/workflows`."""
    repo = find_repo_root(start)
    if repo is None:
        return None
    return repo / ".cursor" / "workflows"


def instantiate(
    target: Path,
    domain_name: str,
    route_key: Optional[str] = None,
    instance_version: str = "v4",
    *,
    legacy: bool = False,
) -> Path:
    domain_name = domain_name.strip()
    if not domain_name:
        raise ValueError("domain-name 不能为空")
    key = (route_key or domain_name).strip()
    if not key:
        raise ValueError("route-key 不能为空")
    generation = normalize_instance_version(instance_version, legacy=legacy)
    if not TEMPLATE_ROOT.is_dir():
        raise FileNotFoundError("母版模板不存在：%s" % TEMPLATE_ROOT)
    integrity = check_skill_integrity.check_manifest()
    if not integrity["ok"]:
        raise ValueError("skill 母版完整性判红，禁止实例化：%s" % integrity)

    target = target.expanduser().resolve()
    skill_root = SKILL_ROOT.resolve()
    if is_within(target, skill_root):
        raise ValueError("SKILL_INTERNAL_TARGET：实例目录不得位于 skill 母版内：%s" % target)
    blocked = forbidden_docs_dirname(target)
    if blocked:
        hint = default_workflows_root(target.parent)
        where = hint.as_posix() if hint is not None else "{repo}/.cursor/workflows/{slug}"
        raise ValueError(
            "DOCS_TREE_FORBIDDEN：实例不得放在文档树「%s」下。默认工作目录：%s/<slug>"
            % (blocked, where)
        )
    if target.exists():
        raise FileExistsError("目标已存在，拒绝覆盖：%s" % target)
    repo_early = find_repo_root(target.parent)
    if repo_early is not None:
        probe = {"route_key": key, "aliases": [], "instance_id": "__pending__"}
        for existing in sjs_registry.list_instances(repo_early):
            if sjs_registry.conflict_errors(probe, existing):
                raise ValueError("ROUTE_KEY_CONFLICT")

    target.parent.mkdir(parents=True, exist_ok=True)
    library, library_scope = resolve_library(target, skill_root)
    staging_parent = Path(
        tempfile.mkdtemp(prefix=".serial-job-session-", dir=str(target.parent))
    )
    staging_instance = staging_parent / "instance"
    try:
        shutil.copytree(TEMPLATE_ROOT, staging_instance)
        if generation == "v3":
            # Keep the old generation available by explicit choice.  The
            # template directory also contains the v4 card skeleton; placing
            # the v3 skeleton at the conventional filename avoids making a
            # legacy caller accidentally author a v4 card.  Existing v3
            # instances are never passed through this path because target
            # creation refuses an existing directory.
            old_template = staging_instance / "jobs" / "_template-v3.md"
            active_template = staging_instance / "jobs" / "_template.md"
            if old_template.is_file():
                shutil.copyfile(old_template, active_template)
        elif not (staging_instance / "NORTH-STAR.md").is_file():
            # Older checked-in templates predate the v4 north-star entry.  A
            # new v4 instance must still expose the stable source that the
            # context adapter reads; this draft is deliberately concise and
            # is meant to be completed during bootstrap.
            (staging_instance / "NORTH-STAR.md").write_text(
                "# %s · NORTH-STAR\n\n"
                "status: draft\n\n"
                "## 领域目标\n\n"
                "- 待领域负责人确认本实例的稳定目标与禁止项。\n"
                "\n"
                "## 保护约束\n\n"
                "- 在确认前保持环境为 draft，不伪造现场、验收或完成状态。\n"
                % domain_name,
                encoding="utf-8",
                newline="\n",
            )
        instance_id = str(uuid.uuid4())
        repo = find_repo_root(target.parent)
        instance_rel = to_portable(target, target)
        protocol_version = V4_PROTOCOL_VERSION if generation == "v4" else load_version()
        required_reads = list(V4_REQUIRED_READS if generation == "v4" else V3_REQUIRED_READS)
        manifest = {
            "schema": "serial-job-session-instance/v2",
            "instance_id": instance_id,
            "domain_name": domain_name,
            "route_key": key,
            "protocol_version": protocol_version,
            "instance_generation": generation,
            "source_skill": "serial-job-session",
            "source_skill_root_at_creation": to_portable(skill_root, target),
            "instance_root": instance_rel,
            "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "writable_scope": instance_rel,
            "estimate_library": to_portable(library, target),
            "estimate_library_scope": library_scope,
            "registry_ref": ".serial-job-session/instances.json",
            "protocol_entry": "protocol/PROTOCOL.md",
            "intent_entry": "protocol/INTENTS.md",
            "required_reads": required_reads,
        }
        if repo is not None:
            manifest["runtime_private"] = ".serial-job-session/private"
        (staging_instance / "INSTANCE.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(staging_instance, target)
    except Exception:
        shutil.rmtree(staging_parent, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(staging_parent, ignore_errors=True)
    if repo is not None:
        try:
            sjs_registry.register(
                {
                    "route_key": key,
                    "aliases": [],
                    "instance_id": instance_id,
                    "domain_name": domain_name,
                    "instance_root": instance_rel,
                    "protocol_entry": "protocol/PROTOCOL.md",
                    "intent_entry": "protocol/INTENTS.md",
                    "required_reads": required_reads,
                    "enabled": True,
                    "protocol_version": protocol_version,
                    "instance_generation": generation,
                },
                repo,
            )
        except SystemExit as exc:
            shutil.rmtree(target, ignore_errors=True)
            raise ValueError("注册表写入失败，已回滚实例：%s" % exc)
        ensure_private_gitignore(repo / LIBRARY_DIRNAME / PRIVATE_DIRNAME)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(
        description="从 serial-job-session 只读母版创建独立领域实例"
    )
    parser.add_argument("--target", required=True, help="skill 目录之外的新实例目录")
    parser.add_argument("--domain-name", required=True, help="领域名称")
    parser.add_argument("--route-key", help="确定性路由关键字，默认等于领域名")
    parser.add_argument(
        "--instance-version",
        "--protocol-version",
        dest="instance_version",
        default="v4",
        help="新实例代际：默认 v4；显式传 v3/legacy 才创建旧协议实例",
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="显式创建 legacy/v3 实例（与默认 v4 互斥）",
    )
    args = parser.parse_args()

    try:
        target = instantiate(
            Path(args.target),
            args.domain_name,
            args.route_key,
            args.instance_version,
            legacy=args.legacy,
        )
    except (OSError, ValueError) as exc:
        print("[error] %s" % exc, file=sys.stderr)
        return 1
    manifest = json.loads((target / "INSTANCE.json").read_text(encoding="utf-8"))
    print("OK：已创建领域实例：%s" % target)
    print(
        "估分库（%s）：%s"
        % (manifest.get("estimate_library_scope"), manifest.get("estimate_library"))
    )
    if manifest.get("estimate_library_scope") == "repo":
        print("  建议把该文件提交进 git，后续新工作流冷启动时才有参考估分。")
    else:
        print("  实例不在 git 仓库内，库落在实例上级目录；要全组共用请把它移进仓库并改 INSTANCE.json。")
    if manifest.get("runtime_private"):
        print("本机 private：%s（gitignore；Close/migrate 备份不写进领域包）" % manifest["runtime_private"])
    print("下一步：主 Agent 只修改该实例，并将 ENV.md 保持为 draft 直到用户确认。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
