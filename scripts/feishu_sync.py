# -*- coding: utf-8 -*-
"""本实例飞书任务表的轻量维护。不对外提供飞书 CLI。

  python scripts/feishu_sync.py --event probe
  python scripts/feishu_sync.py --route-key <key> --event provision --title "…"
  python scripts/feishu_sync.py --route-key <key> --event bind --wiki-url <URL>
  python scripts/feishu_sync.py --route-key <key> --event seed-menu
  python scripts/feishu_sync.py --route-key <key> --event upsert-job --job-id <id>

无 feishu.json 时 seed/upsert 退出 0。CLI 未通时 provision/bind/有绑定时的 upsert 拒绝。
同步失败不回滚 close_transaction。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(SKILL_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT / "scripts"))

STATUS_TODO = "todo"
STATUS_DOING = "doing"
STATUS_WAITING_HUMAN = "等待人类操作"
STATUS_PENDING_ACCEPT = "待验收"
STATUS_REWORK = "返工中"
STATUS_DONE = "done"
STATUS_REJECTED = "rejected"
STATUS_BLOCK = "block"

STATUS_OPTIONS = [
    STATUS_TODO,
    STATUS_DOING,
    STATUS_WAITING_HUMAN,
    STATUS_PENDING_ACCEPT,
    STATUS_REWORK,
    STATUS_DONE,
    STATUS_REJECTED,
    STATUS_BLOCK,
]

DEFAULT_FIELD_MAP = {
    "title": "任务",
    "status": "状态",
    "type": "类型",
    "priority": "优先级",
    "estimate": "估分",
    "human_estimate": "人类估分",
    "shape": "SJS 编号",
    "job_id": "SJS job-id",
    "phase_id": "阶段",
    "estimate_group": "估分类",
    "faces": "面",
    "actor": "actor",
    "human_accept": "人验",
    "ai_net": "ai_net",
    "elapsed": "墙钟",
    "business_wait": "business_wait",
    "variance": "偏差率",
    "attempt": "attempt",
    "started_at": "开始时间",
    "ended_at": "实际完成日期",
    "description": "详细描述",
    "route_key": "route_key",
    "instance_id": "instance_id",
}

FIELD_SPECS = {
    "title": {"type": "text"},
    "status": {
        "type": "select",
        "multiple": False,
        "options": [{"name": n, "hue": "Blue", "lightness": "Lighter"} for n in STATUS_OPTIONS],
    },
    "type": {
        "type": "select",
        "multiple": False,
        "options": [{"name": n, "hue": "Blue", "lightness": "Lighter"} for n in ("程序", "场景", "PM")],
    },
    "priority": {
        "type": "select",
        "multiple": False,
        "options": [{"name": n, "hue": "Blue", "lightness": "Lighter"} for n in ("P0", "P1", "P2")],
    },
    "estimate": {"type": "number", "style": {"type": "plain", "precision": 2}},
    "human_estimate": {"type": "number", "style": {"type": "plain", "precision": 2}},
    "shape": {"type": "number", "style": {"type": "plain", "precision": 0}},
    "job_id": {"type": "text"},
    "phase_id": {"type": "text"},
    "estimate_group": {"type": "text"},
    "faces": {"type": "text"},
    "actor": {"type": "text"},
    "human_accept": {"type": "text"},
    "ai_net": {"type": "number", "style": {"type": "plain", "precision": 4}},
    "elapsed": {"type": "text"},
    "business_wait": {"type": "number", "style": {"type": "plain", "precision": 4}},
    "variance": {"type": "number", "style": {"type": "plain", "precision": 2}},
    "attempt": {"type": "number", "style": {"type": "plain", "precision": 0}},
    "started_at": {"type": "datetime", "style": {"format": "yyyy/MM/dd HH:mm"}},
    "ended_at": {"type": "datetime", "style": {"format": "yyyy/MM/dd HH:mm"}},
    "description": {"type": "text"},
    "route_key": {"type": "text"},
    "instance_id": {"type": "text"},
}

InvokeFn = Callable[..., dict]
_INVOKE: Optional[InvokeFn] = None


class CliNotReady(RuntimeError):
    code = "CLI_NOT_READY"


class Skipped(RuntimeError):
    code = "NO_BINDING"


def set_invoke(fn: Optional[InvokeFn]) -> None:
    global _INVOKE
    _INVOKE = fn


def map_status(job_status: Optional[str], awaiting: Optional[str] = None) -> str:
    status = str(job_status or "").strip()
    wait = str(awaiting or "").strip()
    if status in {"", "ready"}:
        return STATUS_TODO
    if status == "running" and wait == "human_ops":
        return STATUS_WAITING_HUMAN
    if status == "running":
        return STATUS_DOING
    if status == "done_pending":
        return STATUS_PENDING_ACCEPT
    if status == "rework_required":
        return STATUS_REWORK
    if status == "accepted":
        return STATUS_DONE
    if status == "rejected":
        return STATUS_REJECTED
    return STATUS_BLOCK


def idempotent_key(instance_id: str, shape: Any) -> str:
    return "%s#%s" % (instance_id, shape)


def parse_current_awaiting(text: str) -> Optional[str]:
    match = re.search(r"^awaiting:\s*(.+)$", text or "", flags=re.M)
    if not match:
        return None
    raw = match.group(1).strip().strip("\"'")
    if raw in {"null", "None", "~", ""}:
        return None
    return raw


def iso_to_feishu_datetime(value: Optional[str]) -> Optional[str]:
    if not value or str(value).strip() in {"null", "None"}:
        return None
    text = str(value).strip()
    m = re.match(r"(\d{4}-\d{2}-\d{2})[T ](\d{2}:\d{2}:\d{2})", text)
    if not m:
        return None
    return "%s %s" % (m.group(1), m.group(2))


def to_float(value: Any) -> Optional[float]:
    if value is None or value == "" or str(value) in {"null", "None", "untracked"}:
        return None
    try:
        return float(str(value).rstrip("hH小时"))
    except (TypeError, ValueError):
        return None


def human_from_ai(estimate: Any) -> Optional[float]:
    ai = to_float(estimate)
    if ai is None:
        return None
    return round(ai * 7.0, 2)


def build_payload(
    *,
    field_map: Dict[str, str],
    title: str,
    status: str,
    shape: Any,
    job_id: Optional[str],
    phase_id: str = "",
    estimate_group: str = "",
    faces: Any = "",
    actor: str = "agent",
    human_accept: str = "none",
    estimate: Any = None,
    ai_net: Any = None,
    elapsed: Any = None,
    business_wait: Any = None,
    variance: Any = None,
    attempt: Any = None,
    started_at: Optional[str] = None,
    ended_at: Optional[str] = None,
    description: str = "",
    route_key: str = "",
    instance_id: str = "",
    task_type: str = "场景",
    priority: str = "P2",
) -> Dict[str, Any]:
    faces_text = faces
    if isinstance(faces, list):
        faces_text = ", ".join(str(x) for x in faces)
    values = {
        "title": title,
        "status": status,
        "type": task_type,
        "priority": priority,
        "estimate": to_float(estimate),
        "human_estimate": human_from_ai(estimate),
        "shape": to_float(shape),
        "job_id": job_id or "",
        "phase_id": phase_id or "",
        "estimate_group": estimate_group or "",
        "faces": str(faces_text or ""),
        "actor": actor or "",
        "human_accept": human_accept or "",
        "ai_net": to_float(ai_net),
        "elapsed": "" if elapsed is None else str(elapsed),
        "business_wait": to_float(business_wait),
        "variance": to_float(variance),
        "attempt": to_float(attempt),
        "started_at": iso_to_feishu_datetime(started_at),
        "ended_at": iso_to_feishu_datetime(ended_at),
        "description": description or "",
        "route_key": route_key or "",
        "instance_id": instance_id or "",
    }
    payload: Dict[str, Any] = {}
    for logical, feishu_name in field_map.items():
        if logical not in values:
            continue
        val = values[logical]
        if val is None or val == "":
            continue
        payload[feishu_name] = val
    return payload


def unwrap_cell(value: Any) -> Any:
    if isinstance(value, list) and value:
        return unwrap_cell(value[0])
    if isinstance(value, dict):
        for key in ("value", "text", "name", "number"):
            if key in value:
                return unwrap_cell(value[key])
    return value


def find_repo_root(start: Optional[Path] = None) -> Path:
    import registry as sjs_registry

    return sjs_registry.find_repo_root(start)


def _candidate_shims(start: Path) -> List[Path]:
    env = (os.environ.get("SJS_LARK_SHIM") or "").strip()
    found: List[Path] = []
    if env:
        found.append(Path(env))
    for base in [start, *start.parents]:
        found.append(base / ".cursor" / "tools" / "lark-cli" / "invoke-lark-cli.ps1")
    return found


def find_shim(start: Optional[Path] = None) -> Optional[Path]:
    root = (start or Path.cwd()).resolve()
    for path in _candidate_shims(root):
        if path.is_file():
            return path.resolve()
    return None


def _parse_json_blob(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0:
        raise RuntimeError("lark-cli non-JSON: %s" % text[:800])
    payload = json.loads(text[start : end + 1], strict=False)
    if payload.get("ok") is False:
        raise RuntimeError("lark-cli error: %s" % json.dumps(payload, ensure_ascii=False)[:1200])
    return payload


def invoke_lark(*args: str, cwd: Optional[Path] = None) -> dict:
    if _INVOKE is not None:
        return _INVOKE(*args, cwd=cwd)
    shim = find_shim(Path(cwd) if cwd else Path.cwd())
    if shim is not None:
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(shim),
            *args,
        ]
        proc = subprocess.run(
            cmd,
            cwd=str(cwd or find_repo_root()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        text = (proc.stdout or "").strip()
        if proc.returncode != 0:
            raise RuntimeError(
                "lark-cli failed (%s): %s" % (proc.returncode, (text or proc.stderr or "")[:800])
            )
        return _parse_json_blob(text)
    binary = shutil.which("lark-cli")
    if not binary:
        raise CliNotReady("CLI_NOT_READY")
    proc = subprocess.run(
        [binary, *args],
        cwd=str(cwd or Path.cwd()),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )
    text = (proc.stdout or "").strip()
    if proc.returncode != 0:
        raise RuntimeError("lark-cli failed (%s): %s" % (proc.returncode, text[:800]))
    return _parse_json_blob(text)


def probe() -> dict:
    try:
        data = invoke_lark("auth", "status", "--format", "json")
    except CliNotReady:
        return {"ok": False, "cli": "missing"}
    except Exception:
        return {"ok": False, "cli": "error"}
    if isinstance(data, dict) and data.get("ok") is False:
        return {"ok": False, "cli": "error"}
    return {"ok": True, "cli": "connected"}


def require_cli() -> dict:
    result = probe()
    if not result.get("ok"):
        raise CliNotReady("CLI_NOT_READY")
    return result


def feishu_path(instance_root: Path) -> Path:
    return instance_root / "feishu.json"


def load_binding(instance_root: Path) -> Optional[dict]:
    path = feishu_path(instance_root)
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("enabled") is False:
        return None
    field_map = dict(DEFAULT_FIELD_MAP)
    field_map.update(data.get("field_map") or {})
    data = dict(data)
    data["field_map"] = field_map
    return data


def save_binding(instance_root: Path, data: dict) -> Path:
    path = feishu_path(instance_root)
    payload = dict(data)
    payload.setdefault("schema", "serial-job-session-feishu-binding/v1")
    payload["enabled"] = True
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def parse_instance_root(route_key: Optional[str], instance_root: Optional[Path] = None) -> Path:
    if instance_root is not None:
        return Path(instance_root).resolve()
    if not route_key:
        raise SystemExit("ROUTE_KEY_REQUIRED")
    import registry as sjs_registry

    rec = sjs_registry.resolve(route_key, repo_root=find_repo_root())
    if not rec:
        raise SystemExit("ROUTE_NOT_FOUND:%s" % route_key)
    root = rec.get("instance_root") or rec.get("path")
    path = Path(str(root))
    if not path.is_absolute():
        path = find_repo_root() / path
    return path.resolve()


def load_job_card(instance_root: Path, job_id: str) -> dict:
    from estimate_stats import parse_card

    path = instance_root / "jobs" / ("%s.md" % job_id)
    card = parse_card(path)
    if not card:
        raise SystemExit("JOB_NOT_FOUND:%s" % job_id)
    return card


def menu_rows(instance_root: Path) -> Tuple[List[dict], List[dict]]:
    import menu_parse

    text = (instance_root / "JOB-SHAPES.md").read_text(encoding="utf-8")
    pending, _ = menu_parse.parse_pending_table(text)
    closed = []
    section = re.search(r"^##\s*已结档[^\n]*\n(.*?)(?=^##\s|\Z)", text, flags=re.M | re.S)
    if section:
        for line in section.group(1).splitlines():
            if not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 3 or cells[0] in {"编号", ""}:
                continue
            if re.fullmatch(r":?-{2,}:?", cells[0] or ""):
                continue
            try:
                shape = int(str(cells[0]).split()[0])
            except ValueError:
                continue
            closed.append(
                {
                    "shape": shape,
                    "title": cells[1],
                    "job_id": cells[2],
                    "estimate": cells[3] if len(cells) > 3 else "",
                }
            )
    return pending, closed


def _payload_dir(instance_root: Path) -> Path:
    dest = instance_root / "output" / ".feishu-sync"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def write_payload(instance_root: Path, obj: dict, name: str = "payload.json") -> Path:
    path = _payload_dir(instance_root) / name
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def resolve_base_token(bind: dict) -> str:
    if bind.get("base_token"):
        return str(bind["base_token"])
    token_arg = str(bind.get("wiki_url") or bind.get("wiki_node") or "")
    extra: List[str] = []
    if token_arg.startswith("http"):
        pass
    elif token_arg and not token_arg.startswith("wik"):
        extra = ["--obj-type", "bitable"]
    data = invoke_lark(
        "wiki",
        "+node-get",
        "--node-token",
        token_arg,
        *extra,
        "--as",
        "user",
        "--format",
        "json",
    )
    node = (data.get("data") or data.get("node") or data)
    if isinstance(node, dict) and "data" in node and isinstance(node["data"], dict):
        node = node["data"].get("node") or node["data"]
    obj_type = str(node.get("obj_type") or node.get("object_type") or "")
    token = str(node.get("obj_token") or node.get("objToken") or "")
    if obj_type and obj_type != "bitable":
        raise RuntimeError("wiki node obj_type=%s, expected bitable" % obj_type)
    if not token:
        raise RuntimeError("wiki node-get missing obj_token")
    return token


def field_list(base_token: str, table_id: str) -> List[dict]:
    data = invoke_lark(
        "base",
        "+field-list",
        "--base-token",
        base_token,
        "--table-id",
        table_id,
        "--as",
        "user",
        "--format",
        "json",
    )
    items = data.get("data") or data.get("items") or data.get("fields") or []
    if isinstance(items, dict):
        items = items.get("items") or items.get("fields") or []
    return list(items)


def field_names(fields: List[dict]) -> Dict[str, dict]:
    out = {}
    for item in fields:
        name = item.get("field_name") or item.get("name")
        if name:
            out[str(name)] = item
    return out


def parse_record_list_payload(data: dict) -> List[dict]:
    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    if isinstance(payload, dict) and payload.get("record_id_list") and payload.get("fields") is not None:
        names = [str(n) for n in (payload.get("fields") or [])]
        rows = payload.get("data") or []
        ids = payload.get("record_id_list") or []
        out: List[dict] = []
        for i, row in enumerate(rows):
            fields: Dict[str, Any] = {}
            if isinstance(row, list):
                for j, name in enumerate(names):
                    if j < len(row):
                        fields[name] = row[j]
            elif isinstance(row, dict):
                fields = dict(row)
            rid = ids[i] if i < len(ids) else None
            out.append({"record_id": rid, "fields": fields})
        return out
    items = data.get("data") or data.get("items") or data.get("records") or []
    if isinstance(items, dict):
        items = items.get("items") or items.get("records") or items.get("data") or []
    return list(items)


def record_list(base_token: str, table_id: str, limit: int = 200) -> List[dict]:
    data = invoke_lark(
        "base",
        "+record-list",
        "--base-token",
        base_token,
        "--table-id",
        table_id,
        "--limit",
        str(limit),
        "--as",
        "user",
        "--format",
        "json",
    )
    return parse_record_list_payload(data)


def create_field(instance_root: Path, base_token: str, table_id: str, spec: dict) -> dict:
    write_payload(instance_root, spec, "field.json")
    cwd = _payload_dir(instance_root)
    try:
        return invoke_lark(
            "base",
            "+field-create",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--as",
            "user",
            "--json",
            "@field.json",
            cwd=cwd,
        )
    except RuntimeError:
        slim = {k: spec[k] for k in ("name", "type") if k in spec}
        if spec.get("type") == "select":
            slim["multiple"] = spec.get("multiple", False)
            slim["options"] = spec.get("options") or []
        write_payload(instance_root, slim, "field.json")
        return invoke_lark(
            "base",
            "+field-create",
            "--base-token",
            base_token,
            "--table-id",
            table_id,
            "--as",
            "user",
            "--json",
            "@field.json",
            cwd=cwd,
        )


def ensure_fields(
    instance_root: Path,
    base_token: str,
    table_id: str,
    field_map: Dict[str, str],
    dry_run: bool = False,
) -> dict:
    existing = field_names(field_list(base_token, table_id))
    missing = []
    present = []
    for logical, feishu_name in field_map.items():
        if feishu_name in existing:
            present.append(feishu_name)
            continue
        spec = dict(FIELD_SPECS[logical])
        spec["name"] = feishu_name
        missing.append(spec)
    created = []
    if not dry_run:
        for spec in missing:
            created.append(create_field(instance_root, base_token, table_id, spec))
    return {"present": present, "missing": [s["name"] for s in missing], "created": created, "dry_run": dry_run}


def upsert_record(
    instance_root: Path,
    base_token: str,
    table_id: str,
    payload: dict,
    record_id: Optional[str] = None,
) -> dict:
    write_payload(instance_root, payload)
    args = [
        "base",
        "+record-upsert",
        "--base-token",
        base_token,
        "--table-id",
        table_id,
        "--as",
        "user",
        "--json",
        "@payload.json",
    ]
    if record_id:
        args.extend(["--record-id", record_id])
    return invoke_lark(*args, cwd=_payload_dir(instance_root))


def find_record(records: List[dict], field_map: Dict[str, str], shape: Any, instance_id: str) -> Optional[str]:
    shape_name = field_map.get("shape")
    inst_name = field_map.get("instance_id")
    want_shape = to_float(shape)
    for rec in records:
        rid = rec.get("record_id") or rec.get("id")
        fields = rec.get("fields") or rec.get("record") or rec
        if not isinstance(fields, dict):
            continue
        shape_val = unwrap_cell(fields.get(shape_name))
        inst_val = unwrap_cell(fields.get(inst_name))
        if want_shape is not None and to_float(shape_val) == want_shape:
            if not inst_val or str(inst_val) == instance_id:
                return str(rid) if rid else None
    return None


def description_for(row: dict, card: Optional[dict] = None) -> str:
    parts = [
        "SJS 编号 %s" % row.get("shape"),
        "phase %s" % (row.get("phase_id") or ""),
        "estimate_group %s" % (row.get("estimate_group") or ""),
        "目标 %s" % (row.get("goal") or ""),
    ]
    if card:
        parts.append("job_id %s" % card.get("id"))
        parts.append("status %s" % card.get("status"))
    return "\n".join(p for p in parts if p and not str(p).endswith(" "))


def _first_table_id(data: dict) -> str:
    items = data.get("data") or data.get("items") or data.get("tables") or []
    if isinstance(items, dict):
        items = items.get("items") or items.get("tables") or items.get("data") or []
    if not items:
        raise RuntimeError("table-list empty")
    first = items[0]
    if isinstance(first, dict):
        return str(first.get("table_id") or first.get("id") or "")
    return str(first)


def _node_fields(data: dict) -> dict:
    node = data.get("data") or data.get("node") or data
    if isinstance(node, dict) and "data" in node and isinstance(node["data"], dict):
        inner = node["data"].get("node") or node["data"]
        if isinstance(inner, dict):
            node = inner
    if not isinstance(node, dict):
        return {}
    return node


def provision(
    *,
    instance_root: Path,
    title: str,
    parent_node: Optional[str] = None,
    dry_run: bool = False,
) -> dict:
    require_cli()
    args = ["wiki", "+node-create", "--obj-type", "bitable", "--title", title, "--as", "user", "--format", "json"]
    if parent_node:
        args.extend(["--parent-node-token", parent_node])
    if dry_run:
        return {"ok": True, "dry_run": True, "event": "provision"}
    created = invoke_lark(*args)
    node = _node_fields(created)
    wiki_node = str(node.get("node_token") or node.get("token") or "")
    base_token = str(node.get("obj_token") or node.get("objToken") or "")
    if not base_token:
        raise RuntimeError("provision missing obj_token")
    tables = invoke_lark(
        "base",
        "+table-list",
        "--base-token",
        base_token,
        "--as",
        "user",
        "--format",
        "json",
    )
    table_id = _first_table_id(tables)
    bind = {
        "schema": "serial-job-session-feishu-binding/v1",
        "enabled": True,
        "title": title,
        "wiki_node": wiki_node,
        "wiki_url": str(node.get("url") or ""),
        "base_token": base_token,
        "table_id": table_id,
        "field_map": dict(DEFAULT_FIELD_MAP),
    }
    save_binding(instance_root, bind)
    ensure_fields(instance_root, base_token, table_id, bind["field_map"], dry_run=False)
    return {"ok": True, "wiki_node": wiki_node, "table_id": table_id}


def bind_existing(*, instance_root: Path, wiki_url: str, dry_run: bool = False) -> dict:
    require_cli()
    token_arg = wiki_url.strip()
    if dry_run:
        return {"ok": True, "dry_run": True, "event": "bind"}
    extra = []
    if token_arg.startswith("http"):
        pass
    data = invoke_lark(
        "wiki",
        "+node-get",
        "--node-token",
        token_arg,
        *extra,
        "--as",
        "user",
        "--format",
        "json",
    )
    node = _node_fields(data)
    base_token = str(node.get("obj_token") or node.get("objToken") or "")
    wiki_node = str(node.get("node_token") or node.get("token") or "")
    if not base_token:
        base_token = resolve_base_token({"wiki_url": token_arg, "wiki_node": wiki_node})
    tables = invoke_lark(
        "base",
        "+table-list",
        "--base-token",
        base_token,
        "--as",
        "user",
        "--format",
        "json",
    )
    table_id = _first_table_id(tables)
    bind = {
        "schema": "serial-job-session-feishu-binding/v1",
        "enabled": True,
        "wiki_url": token_arg,
        "wiki_node": wiki_node,
        "base_token": base_token,
        "table_id": table_id,
        "field_map": dict(DEFAULT_FIELD_MAP),
    }
    save_binding(instance_root, bind)
    ensure_fields(instance_root, base_token, table_id, bind["field_map"], dry_run=False)
    return {"ok": True, "wiki_node": wiki_node, "table_id": table_id}


def seed_menu(route_key: str, dry_run: bool = False, instance_root: Optional[Path] = None) -> dict:
    root = parse_instance_root(route_key, instance_root)
    bind = load_binding(root)
    if not bind:
        return {"ok": True, "skipped": True, "reason": "NO_BINDING"}
    require_cli()
    pending, closed = menu_rows(root)
    instance_id = json.loads((root / "INSTANCE.json").read_text(encoding="utf-8")).get("instance_id") or ""
    base_token = resolve_base_token(bind)
    field_report = ensure_fields(root, base_token, bind["table_id"], bind["field_map"], dry_run=dry_run)
    records = [] if dry_run else record_list(base_token, bind["table_id"])
    upserts = []
    rows = list(closed) + list(pending)
    for row in rows:
        title = row.get("title") or ""
        shape = row.get("shape")
        status = STATUS_DONE if row in closed else STATUS_TODO
        job_id = row.get("job_id") if row in closed else ""
        payload = build_payload(
            field_map=bind["field_map"],
            title=title,
            status=status,
            shape=shape,
            job_id=job_id,
            phase_id=str(row.get("phase_id") or ""),
            estimate_group=str(row.get("estimate_group") or ""),
            faces=row.get("faces") or "",
            actor=str(row.get("actor") or "agent"),
            human_accept=str(row.get("human_accept") or "none"),
            estimate=row.get("estimate"),
            description=description_for(row),
            route_key=route_key,
            instance_id=str(instance_id),
        )
        rec_id = None if dry_run else find_record(records, bind["field_map"], shape, str(instance_id))
        if dry_run:
            upserts.append({"shape": shape, "record_id": rec_id, "op": "dry-run"})
            continue
        result = upsert_record(root, base_token, bind["table_id"], payload, rec_id)
        upserts.append({"shape": shape, "record_id": rec_id, "result_ok": True})
        if rec_id is None:
            blob = result.get("data") if isinstance(result.get("data"), dict) else result
            record = blob.get("record") if isinstance(blob, dict) else None
            new_id = None
            if isinstance(record, dict):
                new_id = record.get("record_id") or record.get("id")
            if not new_id and isinstance(blob, dict):
                new_id = blob.get("record_id")
            if new_id:
                records.append({"record_id": new_id, "fields": payload})
    return {
        "ok": True,
        "table_id": bind["table_id"],
        "field_report": field_report,
        "row_count": len(rows),
        "upserts": len(upserts),
        "dry_run": dry_run,
    }


def upsert_job(
    route_key: str,
    job_id: str,
    dry_run: bool = False,
    instance_root: Optional[Path] = None,
) -> dict:
    root = parse_instance_root(route_key, instance_root)
    bind = load_binding(root)
    if not bind:
        return {"ok": True, "skipped": True, "reason": "NO_BINDING"}
    require_cli()
    card = load_job_card(root, job_id)
    instance_id = json.loads((root / "INSTANCE.json").read_text(encoding="utf-8")).get("instance_id") or ""
    awaiting = parse_current_awaiting((root / "CURRENT.md").read_text(encoding="utf-8"))
    pending, closed = menu_rows(root)
    menu = None
    for row in pending + closed:
        if int(row["shape"]) == int(str(card.get("shape")).split()[0]):
            menu = row
            break
    title = (menu or {}).get("title") or job_id
    payload = build_payload(
        field_map=bind["field_map"],
        title=title,
        status=map_status(card.get("status"), awaiting),
        shape=card.get("shape"),
        job_id=str(card.get("id") or job_id),
        phase_id=str((menu or {}).get("phase_id") or ""),
        estimate_group=str(card.get("estimate_group") or ""),
        faces=card.get("faces") or "",
        actor=str(card.get("actor") or "agent"),
        human_accept=str(card.get("human_accept") or "none"),
        estimate=card.get("estimate"),
        ai_net=card.get("ai_net"),
        elapsed=card.get("elapsed"),
        business_wait=card.get("business_wait"),
        variance=card.get("variance"),
        attempt=card.get("attempt"),
        started_at=card.get("started_at"),
        ended_at=card.get("ended_at") or card.get("closed_at"),
        description=description_for(menu or {"shape": card.get("shape")}, card),
        route_key=route_key,
        instance_id=str(instance_id),
    )
    if dry_run:
        return {"ok": True, "dry_run": True, "payload": payload, "status": payload.get(bind["field_map"]["status"])}
    base_token = resolve_base_token(bind)
    ensure_fields(root, base_token, bind["table_id"], bind["field_map"], dry_run=False)
    records = record_list(base_token, bind["table_id"])
    rec_id = find_record(records, bind["field_map"], card.get("shape"), str(instance_id))
    result = upsert_record(root, base_token, bind["table_id"], payload, rec_id)
    return {
        "ok": True,
        "record_id": rec_id,
        "status": payload.get(bind["field_map"]["status"]),
        "result_keys": list(result.keys())[:8],
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="SJS 实例飞书任务表轻量维护")
    parser.add_argument("--route-key")
    parser.add_argument("--instance")
    parser.add_argument("--job-id")
    parser.add_argument(
        "--event",
        choices=["probe", "provision", "bind", "seed-menu", "upsert-job", "auto"],
        default="auto",
    )
    parser.add_argument("--title")
    parser.add_argument("--parent-node")
    parser.add_argument("--wiki-url")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    event = args.event
    instance = Path(args.instance).resolve() if args.instance else None
    try:
        if event == "probe":
            result = probe()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get("ok") else 2
        if event == "auto":
            event = "upsert-job" if args.job_id else "seed-menu"
        if event == "provision":
            if not args.route_key and instance is None:
                raise SystemExit("ROUTE_KEY_REQUIRED")
            root = parse_instance_root(args.route_key, instance)
            if not args.title:
                raise SystemExit("--title required for provision")
            print(
                json.dumps(
                    provision(
                        instance_root=root,
                        title=args.title,
                        parent_node=args.parent_node,
                        dry_run=args.dry_run,
                    ),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if event == "bind":
            root = parse_instance_root(args.route_key, instance)
            if not args.wiki_url:
                raise SystemExit("--wiki-url required for bind")
            print(
                json.dumps(
                    bind_existing(instance_root=root, wiki_url=args.wiki_url, dry_run=args.dry_run),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if event == "seed-menu":
            print(
                json.dumps(
                    seed_menu(args.route_key or "", dry_run=args.dry_run, instance_root=instance),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if not args.job_id:
            raise SystemExit("--job-id required for upsert-job")
        print(
            json.dumps(
                upsert_job(
                    args.route_key or "",
                    args.job_id,
                    dry_run=args.dry_run,
                    instance_root=instance,
                ),
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except CliNotReady:
        print(json.dumps({"ok": False, "error": "CLI_NOT_READY"}, ensure_ascii=False, indent=2))
        return 2
    except Skipped as exc:
        print(json.dumps({"ok": True, "skipped": True, "reason": str(exc)}, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
