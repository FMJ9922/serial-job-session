#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""仓库级 route_key 注册表。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import schema_validate

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

SCHEMA = "serial-job-session-registry/v1"
SKILL_ROOT = Path(__file__).resolve().parent.parent


def find_repo_root(start: Optional[Path] = None) -> Path:
    cur = (start or Path.cwd()).resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / ".git").exists() or (candidate / ".serial-job-session").exists():
            return candidate
    return cur


def registry_path(repo_root: Optional[Path] = None) -> Path:
    root = repo_root or find_repo_root()
    return root / ".serial-job-session" / "instances.json"


def empty_registry() -> dict:
    return {"schema": SCHEMA, "updated_at": None, "instances": []}


def load_registry(path: Optional[Path] = None) -> dict:
    target = path or registry_path()
    if not target.exists():
        return empty_registry()
    data = json.loads(target.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise SystemExit(f"未知注册表 schema: {data.get('schema')}")
    data.setdefault("instances", [])
    return data


def atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
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


def normalize_key(value: str) -> str:
    return (value or "").strip().lower().replace(" ", "")


def _escape_ok(rel: str) -> bool:
    if not rel:
        return True
    posix = rel.replace("\\", "/")
    return ".." not in Path(posix).parts and not posix.startswith("/")


def _resolve_instance_root(root: str, repo_root: Path) -> Path:
    candidate = Path(root)
    if candidate.is_absolute():
        return candidate.resolve()
    return (repo_root / root).resolve()


def validate_entry(entry: dict, repo_root: Path, require_exists: bool = True) -> List[str]:
    errors: List[str] = []
    if not entry.get("route_key"):
        errors.append("ROUTE_KEY_MISSING")
    if not entry.get("instance_id"):
        errors.append("INSTANCE_ID_MISSING")
    root = str(entry.get("instance_root") or "")
    if not root:
        errors.append("INSTANCE_ROOT_MISSING")
    else:
        path = _resolve_instance_root(root, repo_root)
        try:
            path.relative_to(repo_root.resolve())
        except ValueError:
            errors.append("INSTANCE_ROOT_ESCAPE")
        try:
            path.relative_to(SKILL_ROOT.resolve())
            errors.append("SKILL_INTERNAL_TARGET")
        except ValueError:
            pass
        if require_exists and not path.exists():
            errors.append("INSTANCE_ROOT_MISSING_ON_DISK")
    for field in ("protocol_entry", "intent_entry"):
        rel = entry.get(field)
        if rel and not _escape_ok(str(rel)):
            errors.append("PROTOCOL_PATH_ESCAPE")
    for rel in entry.get("required_reads") or []:
        if not _escape_ok(str(rel)):
            errors.append("REQUIRED_READ_ESCAPE")
            break
    return errors


def all_keys(entry: dict) -> List[str]:
    keys = [normalize_key(entry.get("route_key", ""))]
    keys.extend(normalize_key(a) for a in entry.get("aliases") or [])
    return [k for k in keys if k]


def conflict_errors(entry: dict, existing: dict) -> List[str]:
    if existing.get("instance_id") == entry.get("instance_id"):
        return []
    left = set(all_keys(entry))
    right = set(all_keys(existing))
    if left & right:
        return ["ROUTE_KEY_CONFLICT"]
    return []


def register(
    entry: dict,
    repo_root: Optional[Path] = None,
    path: Optional[Path] = None,
    require_exists: bool = True,
) -> dict:
    root = repo_root or find_repo_root()
    target = path or registry_path(root)
    data = load_registry(target)
    schema_errors = schema_validate.validate_named({"schema": SCHEMA, "instances": [entry]}, "registry.schema.json")
    named = [err for err in schema_errors if err.startswith("MISSING_FIELD")]
    if named:
        raise SystemExit(";".join(named))
    for existing in data["instances"]:
        conflicts = conflict_errors(entry, existing)
        if conflicts:
            raise SystemExit(";".join(conflicts))
    errors = validate_entry(entry, root, require_exists=require_exists)
    if errors:
        raise SystemExit(";".join(errors))
    replaced = False
    for i, existing in enumerate(data["instances"]):
        if existing.get("instance_id") == entry.get("instance_id"):
            data["instances"][i] = entry
            replaced = True
            break
    if not replaced:
        data["instances"].append(entry)
    data["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    atomic_write_json(target, data)
    return data


def resolve(route_key: str, repo_root: Optional[Path] = None) -> Optional[dict]:
    root = repo_root or find_repo_root()
    data = load_registry(registry_path(root))
    needle = normalize_key(route_key)
    matches = []
    for entry in data["instances"]:
        if not entry.get("enabled", True):
            continue
        keys = [normalize_key(entry.get("route_key", ""))] + [
            normalize_key(a) for a in entry.get("aliases") or []
        ]
        if needle in keys:
            matches.append(entry)
    if len(matches) > 1:
        raise SystemExit("ROUTE_KEY_AMBIGUOUS")
    return matches[0] if matches else None


def list_instances(repo_root: Optional[Path] = None) -> List[dict]:
    root = repo_root or find_repo_root()
    return load_registry(registry_path(root)).get("instances") or []


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="serial-job-session 注册表")
    parser.add_argument("action", choices=["list", "resolve", "validate", "register"])
    parser.add_argument("--route-key")
    parser.add_argument("--instance-id")
    parser.add_argument("--domain-name")
    parser.add_argument("--instance-root")
    parser.add_argument("--protocol-entry", default="protocol/PROTOCOL.md")
    parser.add_argument("--intent-entry", default="protocol/INTENTS.md")
    parser.add_argument("--repo-root")
    args = parser.parse_args(argv)
    root = Path(args.repo_root).resolve() if args.repo_root else find_repo_root()
    if args.action == "list":
        print(json.dumps(list_instances(root), ensure_ascii=False, indent=2))
        return 0
    if args.action == "resolve":
        if not args.route_key:
            raise SystemExit("--route-key 必填")
        hit = resolve(args.route_key, root)
        if not hit:
            raise SystemExit("ROUTE_KEY_NOT_FOUND")
        print(json.dumps(hit, ensure_ascii=False, indent=2))
        return 0
    if args.action == "validate":
        data = load_registry(registry_path(root))
        errors: List[str] = []
        seen: Dict[str, str] = {}
        for entry in data["instances"]:
            errors.extend(validate_entry(entry, root))
            for key in all_keys(entry):
                if key in seen and seen[key] != entry.get("instance_id", ""):
                    errors.append("ROUTE_KEY_CONFLICT")
                seen[key] = entry.get("instance_id", "")
        if errors:
            print("\n".join(errors))
            return 1
        print("OK")
        return 0
    if args.action == "register":
        entry = {
            "route_key": args.route_key,
            "aliases": [],
            "instance_id": args.instance_id,
            "domain_name": args.domain_name,
            "instance_root": args.instance_root,
            "protocol_entry": args.protocol_entry,
            "intent_entry": args.intent_entry,
            "required_reads": ["ENV.md", "CURRENT.md", "JOB-SHAPES.md"],
            "enabled": True,
            "protocol_version": "3.0.0",
        }
        register(entry, root)
        print("OK")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
