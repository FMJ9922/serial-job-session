#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 serial-job-session 只读母版的文本文件完整性。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict


for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")


SKILL_ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = SKILL_ROOT / "skill-integrity.json"
VERSION_PATH = SKILL_ROOT / "VERSION"
TRACKED_SUFFIXES = {".md", ".py", ".json"}
EXCLUDED_NAMES = {
    "skill-integrity.json",
    "README.md",
    "CHANGELOG.md",
    "AUTHORS.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
}


def _lf_bytes(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def sha256(path: Path) -> str:
    """Canonical hash of skill text, always LF.

    ``check_manifest`` also accepts the raw working-tree hash. The committed
    baseline mixes LF and CRLF digests, and Windows checkouts rewrite LF blobs
    as CRLF without a content change.
    """
    return hashlib.sha256(_lf_bytes(path.read_bytes())).hexdigest()


def acceptable_hashes(path: Path) -> set:
    raw = path.read_bytes()
    hashes = {hashlib.sha256(raw).hexdigest()}
    normalized = _lf_bytes(raw)
    if normalized != raw:
        hashes.add(hashlib.sha256(normalized).hexdigest())
    return hashes


def tracked_files() -> Dict[str, str]:
    result: Dict[str, str] = {}
    for path in sorted(SKILL_ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(SKILL_ROOT)
        if relative.parts and relative.parts[0] in {"Test", ".git", ".github"}:
            continue
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if path.name in EXCLUDED_NAMES:
            continue
        if path.name != "VERSION" and path.suffix.lower() not in TRACKED_SUFFIXES:
            continue
        result[relative.as_posix()] = sha256(path)
    return result


def observed_hashes() -> Dict[str, set]:
    result: Dict[str, set] = {}
    for path in sorted(SKILL_ROOT.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(SKILL_ROOT)
        if relative.parts and relative.parts[0] in {"Test", ".git", ".github"}:
            continue
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if path.name in EXCLUDED_NAMES:
            continue
        if path.name != "VERSION" and path.suffix.lower() not in TRACKED_SUFFIXES:
            continue
        result[relative.as_posix()] = acceptable_hashes(path)
    return result


def atomic_write_json(path: Path, payload: dict) -> None:
    fd, temp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def update_manifest() -> None:
    version = VERSION_PATH.read_text(encoding="utf-8").strip()
    payload = {
        "schema": "serial-job-session-integrity/v1",
        "protocol_version": version,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "files": tracked_files(),
    }
    atomic_write_json(MANIFEST_PATH, payload)


def check_manifest() -> dict:
    if not MANIFEST_PATH.is_file():
        return {
            "ok": False,
            "error": "缺少 skill-integrity.json；需要由显式 Skill Maintainer 初始化",
            "missing": [],
            "extra": [],
            "modified": [],
        }
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "error": "完整性清单不可读：%s" % exc,
            "missing": [],
            "extra": [],
            "modified": [],
        }

    expected = manifest.get("files")
    if not isinstance(expected, dict):
        return {
            "ok": False,
            "error": "完整性清单缺 files object",
            "missing": [],
            "extra": [],
            "modified": [],
        }
    actual = observed_hashes()
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    modified = sorted(
        path for path in set(expected) & set(actual) if expected[path] not in actual[path]
    )
    return {
        "ok": not missing and not extra and not modified,
        "error": None,
        "missing": missing,
        "extra": extra,
        "modified": modified,
        "protocol_version": manifest.get("protocol_version"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="校验 serial-job-session 母版完整性")
    parser.add_argument(
        "--update",
        action="store_true",
        help="重建完整性基线；仅限用户明确要求维护 skill 本身",
    )
    parser.add_argument(
        "--maintenance-ack",
        default=None,
        help="更新时必须精确传 MODIFY_SKILL，防止运行时误更新基线",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    if args.update:
        if args.maintenance_ack != "MODIFY_SKILL":
            print(
                "[error] --update 需要 --maintenance-ack MODIFY_SKILL；禁止运行时自动更新基线",
                file=sys.stderr,
            )
            return 2
        try:
            update_manifest()
        except OSError as exc:
            print("[error] 更新完整性基线失败：%s" % exc, file=sys.stderr)
            return 1
        print("OK：已更新母版完整性基线")

    result = check_manifest()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif result["ok"]:
        print("OK：Skill 母版完整性通过（版本 %s）" % result.get("protocol_version"))
    else:
        if result.get("error"):
            print("[error] " + result["error"])
        for key in ("missing", "extra", "modified"):
            if result.get(key):
                print("[error] %s：%s" % (key, "、".join(result[key])))
        print("HARD STOP：禁止运行领域任务，也禁止自动更新完整性基线。")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
