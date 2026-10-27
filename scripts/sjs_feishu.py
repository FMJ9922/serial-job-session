"""Opt-in Lark Base mirror. CLI transport is injectable; never runs at init."""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path
from urllib.parse import urlparse

from sjs_config import SJSError, canonical, digest, require
import sjs_store as store

FIELDS = {"key": "SJS key", "title": "任务", "status": "状态", "description": "详细描述", "version": "合同版本"}
STATUS = {"ready": "todo", "doing": "doing", "awaiting_acceptance": "待验收", "accepted": "done", "failed": "block", "cancelled": "rejected"}


def invoke_cli(args, *, cwd, config):
    prefix = config.get("cli") or [shutil.which("lark-cli") or "lark-cli"]
    require(isinstance(prefix, list) and prefix and all(isinstance(s, str) for s in prefix), "INVALID_CLI_PREFIX")
    response = subprocess.run([*prefix, *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60, shell=False)
    require(response.returncode == 0, "LARK_CLI_FAILED", returncode=response.returncode)
    text = response.stdout.strip()
    start, end = text.find("{"), text.rfind("}")
    require(start >= 0 and end >= start, "LARK_NON_JSON_RESPONSE")
    data = json.loads(text[start:end + 1])
    require(data.get("ok") is not False and data.get("code", 0) in (0, "0"), "LARK_API_FAILED")
    return data


def _data(response):
    data = response.get("data", response)
    return data


def _items(response, key):
    data = _data(response)
    if isinstance(data, list):
        return data
    return data.get("items", data.get(key, data.get("data", [])))


def _node(response):
    data = _data(response)
    return data.get("node", data)


def _unwrap(value):
    if isinstance(value, list):
        return "".join(str(i.get("text", i.get("value", ""))) if isinstance(i, dict) else str(i) for i in value)
    if isinstance(value, dict):
        return value.get("text", value.get("value", value.get("name", "")))
    return value


def _records(response):
    data = _data(response)
    if isinstance(data, dict) and "record_id_list" in data:
        fields = data.get("fields", [])
        return [{"record_id": rid, "fields": dict(zip(fields, row)) if isinstance(row, list) else row}
                for rid, row in zip(data["record_id_list"], data.get("data", []))]
    return _items(response, "records")


def _task_fields(cfg, task):
    fields = {**FIELDS, **cfg.get("field_map", {})}
    status = STATUS[task["status"]]
    if task.get("blocked_reason"):
        status = "等待人类操作" if task["blocked_reason"] in {"human_decision", "below_threshold", "risk_exceeded"} else "block"
    return {fields["key"]: cfg["instance_id"] + "/" + task["id"], fields["title"]: task["title"],
            fields["status"]: status, fields["version"]: str(task["contract_version"]),
            fields["description"]: canonical({"goal": task["contract"]["goal"], "status": task["status"],
                 "parent_id": task.get("parent_id"), "candidate": task.get("candidate"), "estimate": task.get("estimate"),
                 "blocked_reason": task.get("blocked_reason"), "last_feedback": task.get("last_feedback")})}


def sync(instance, *, event="flush", dry_run=False, invoke=None):
    root = Path(instance).resolve()
    initial = store.load_snapshot(root)
    require(initial["config"]["modules"]["feishu"], "FEISHU_DISABLED")
    require(event in {"probe", "provision", "bind", "flush"}, "UNKNOWN_SYNC_EVENT")
    cfg = initial["config"]["feishu"]
    if dry_run:
        return {"ok": True, "dry_run": True, "event": event, "target": {k: cfg.get(k) for k in ("mode", "title", "wiki_url", "table_id")},
                "task_ids": list(initial["tasks"]), "remote_calls": 0}
    # Hold the same controller lease across external writes and their local
    # receipts. A second controller cannot race the mirror with newer tasks.
    with store.file_lock(store.private_dir(root) / "writer.lock"):
        state = store.load_snapshot(root)
        private = store.private_dir(root)
        with tempfile.TemporaryDirectory(prefix="sync-", dir=private) as temporary:
            def call(*args, payload=None):
                if payload is not None:
                    (Path(temporary) / "payload.json").write_text(canonical(payload), encoding="utf-8")
                    args = (*args, "--json", "@payload.json")
                args = list(args)
                if "--format" not in args:
                    args.extend(["--format", "json"])
                response = invoke(args, cwd=temporary) if invoke else invoke_cli(args, cwd=temporary, config=cfg)
                require(isinstance(response, dict) and response.get("ok") is not False and response.get("code", 0) in (0, "0"), "LARK_API_FAILED")
                return response
            def receipt(values):
                nonlocal state
                store._commit(root, state, {"command_id": "sync-" + uuid.uuid4().hex, "expected_revision": state["revision"],
                    "actor": {"id": "sync-adapter", "kind": "agent"}, "operations": [{"op": "sync_receipt", "receipt": values}]})
                state = store.load_snapshot(root)
            def list_records(binding):
                all_records, seen, token = [], set(), None
                while True:
                    args = ["base", "+record-list", "--base-token", binding["base_token"], "--table-id", binding["table_id"], "--limit", "200", "--as", "user"]
                    if token:
                        args += ["--page-token", token]
                    response = call(*args)
                    all_records.extend(_records(response))
                    data = _data(response)
                    if not isinstance(data, dict) or not data.get("has_more"):
                        return all_records
                    token = data.get("page_token") or data.get("next_page_token")
                    require(token and token not in seen, "REMOTE_PAGINATION_INCOMPLETE")
                    seen.add(token)
            try:
                call("auth", "status")
                if event == "probe":
                    return {"ok": True, "cli": "connected"}
                binding = state["sync"].get("binding")
                if not binding:
                    if cfg["mode"] == "create":
                        require(event == "provision", "PROVISION_REQUIRED")
                        require(state["sync"].get("provision_status") not in {"pending", "unknown"}, "PROVISION_OUTCOME_UNKNOWN")
                        receipt({"provision_status": "pending"})
                        args = ["wiki", "+node-create", "--obj-type", "bitable", "--title", cfg["title"], "--as", "user"]
                        if cfg.get("parent_node"):
                            args += ["--parent-node-token", cfg["parent_node"]]
                        created = _node(call(*args))
                        require(created.get("obj_token"), "BASE_TOKEN_MISSING")
                        binding = {"base_token": created["obj_token"], "wiki_node": created.get("node_token"), "table_id": None}
                        # Persist the remote identity before table/field work.
                        receipt({"binding": binding, "provision_status": "created"})
                    else:
                        if cfg.get("base_token"):
                            binding = {"base_token": cfg["base_token"], "table_id": cfg.get("table_id")}
                        else:
                            url = cfg["wiki_url"]
                            parsed = urlparse(url)
                            token = parsed.path.rstrip("/").split("/")[-1] if parsed.scheme else url
                            node = _node(call("wiki", "+node-get", "--node-token", token, "--as", "user"))
                            require(node.get("obj_type", "bitable") == "bitable" and node.get("obj_token"), "NOT_A_BASE")
                            binding = {"base_token": node["obj_token"], "table_id": cfg.get("table_id")}
                        receipt({"binding": binding})
                if not binding.get("table_id"):
                    tables = _items(call("base", "+table-list", "--base-token", binding["base_token"], "--as", "user"), "tables")
                    require(len(tables) == 1, "TABLE_SELECTION_REQUIRED")
                    binding = {**binding, "table_id": tables[0].get("table_id", tables[0].get("id"))}
                    require(binding["table_id"], "TABLE_ID_MISSING")
                    receipt({"binding": binding})
                field_map = {**FIELDS, **cfg.get("field_map", {})}
                existing = _items(call("base", "+field-list", "--base-token", binding["base_token"], "--table-id", binding["table_id"], "--as", "user"), "fields")
                names = {f.get("field_name", f.get("name")) for f in existing}
                for name in field_map.values():
                    if name not in names:
                        call("base", "+field-create", "--base-token", binding["base_token"], "--table-id", binding["table_id"], "--as", "user", payload={"name": name, "type": "text"})
                if event in {"provision", "bind"}:
                    receipt({"last_error": None})
                    return {"ok": True, "binding": {"table_id": binding["table_id"]}, "ready": True}
                rows = list_records(binding)
                by_key = {}
                for row in rows:
                    key = _unwrap(row.get("fields", {}).get(field_map["key"]))
                    if key:
                        require(key not in by_key, "DUPLICATE_REMOTE_KEY")
                        by_key[key] = row
                records = dict(state["sync"].get("records", {}))
                synced, pending = [], []
                for task_id, task in state["tasks"].items():
                    fields = _task_fields({**cfg, "instance_id": state["config"]["instance_id"]}, task)
                    fingerprint = digest(fields)
                    previous = records.get(task_id, {})
                    if previous.get("fingerprint") == fingerprint and previous.get("status") == "synced":
                        continue
                    key = fields[field_map["key"]]
                    row = by_key.get(key)
                    record_id = (row or {}).get("record_id", (row or {}).get("id"))
                    if not record_id and previous.get("status") == "pending":
                        pending.append(task_id)
                        continue
                    records[task_id] = {"status": "pending", "record_id": record_id, "fingerprint": fingerprint}
                    receipt({"records": records})
                    args = ["base", "+record-upsert", "--base-token", binding["base_token"], "--table-id", binding["table_id"], "--as", "user"]
                    if record_id:
                        args += ["--record-id", record_id]
                    call(*args, payload={"fields": fields})
                    observed = next((r for r in list_records(binding) if _unwrap(r.get("fields", {}).get(field_map["key"])) == key), None)
                    require(observed is not None and all(str(_unwrap(observed["fields"].get(k))) == str(v) for k, v in fields.items()), "SYNC_READBACK_FAILED")
                    records[task_id] = {"status": "synced", "record_id": observed.get("record_id", observed.get("id")), "fingerprint": fingerprint}
                    receipt({"records": records, "last_error": None})
                    synced.append(task_id)
                return {"ok": not pending, "synced": synced, "pending": pending,
                        "warning": "Pending remote creates require readback or explicit reconciliation; they are not blindly recreated." if pending else None}
            except Exception as exc:
                code = exc.code if isinstance(exc, SJSError) else type(exc).__name__
                values = {"last_error": code}
                if state["sync"].get("provision_status") == "pending":
                    values["provision_status"] = "unknown"
                receipt(values)
                return {"ok": False, "error": code, "retryable": code != "PROVISION_OUTCOME_UNKNOWN", "local_results_preserved": True}
