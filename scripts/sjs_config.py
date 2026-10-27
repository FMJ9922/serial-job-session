"""Shared v5 configuration. No host, optional-module or network discovery."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODES = ("manual", "conditional", "full_ai")
RISKS = {"low": 0, "medium": 1, "high": 2}


class SJSError(RuntimeError):
    def __init__(self, code, message=None, **details):
        super().__init__(message or code)
        self.code, self.details = code, details


def require(condition, code, **details):
    if not condition:
        raise SJSError(code, **details)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if not (v is None or isinstance(v, str) and not v.strip())}
    if isinstance(value, list):
        return [clean(v) for v in value]
    return value.strip() if isinstance(value, str) else value


def merge(base, extra):
    result = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def number(value, minimum=0, maximum=None):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and value >= minimum and (maximum is None or value <= maximum)


def template_identity():
    # Use the release manifest as a stable shared-method identity; check's full
    # integrity pass detects unrecorded changes without scanning on every turn.
    manifest = json.loads((ROOT / "skill-integrity.json").read_text(encoding="utf-8"))
    return {"version": (ROOT / "VERSION").read_text().strip(), "integrity": digest(manifest["files"])}


def policy_errors(policy):
    errors = []
    if policy.get("mode") not in MODES:
        errors.append("policy.mode")
    if policy.get("on_failure") not in {"skip", "stop"}:
        errors.append("policy.on_failure")
    if not isinstance(policy.get("max_retries"), int) or isinstance(policy.get("max_retries"), bool) or policy["max_retries"] < 0:
        errors.append("policy.max_retries")
    if not isinstance(policy.get("independent_review"), bool):
        errors.append("policy.independent_review")
    if policy.get("mode") == "conditional":
        if policy.get("risk_ceiling") not in RISKS:
            errors.append("policy.risk_ceiling")
        for phase in ("feasibility", "direction", "acceptance"):
            if not number(policy.get("thresholds", {}).get(phase), 0, 100):
                errors.append("policy.thresholds." + phase)
        if not policy.get("confirmation"):
            errors.append("policy.confirmation")
    return errors


def prepare_config(raw):
    require(isinstance(raw, dict), "CONFIG_OBJECT_REQUIRED")
    raw = clean(raw)
    require(raw.get("schema", "sjs-run/v5") == "sjs-run/v5", "UNSUPPORTED_PROTOCOL")
    for key in ("policy", "authorization", "modules", "limits", "rotation", "budget", "feishu", "estimates", "parameters"):
        require(key not in raw or isinstance(raw[key], dict), "INVALID_CONFIG_SECTION", field=key)
    defaults = json.loads((ROOT / "defaults.json").read_text(encoding="utf-8"))
    cfg = merge(defaults, raw)
    missing = policy_errors(cfg.get("policy", {}))
    for field in ("goal", "route_key"):
        if not isinstance(cfg.get(field), str) or not cfg[field]:
            missing.append(field)
    auth = cfg.get("authorization", {})
    if not isinstance(auth.get("paths"), list) or not auth["paths"] or not all(isinstance(p, str) and p.strip() for p in auth["paths"]):
        missing.append("authorization.paths")
    if not isinstance(auth.get("tools"), list) or not all(isinstance(p, str) for p in auth["tools"]):
        missing.append("authorization.tools")
    modules = cfg.get("modules", {})
    for key in ("estimates", "feishu"):
        if not isinstance(modules.get(key), bool):
            missing.append("modules." + key)
    if modules.get("feishu") is True:
        binding = cfg.get("feishu", {})
        if binding.get("mode") not in {"create", "bind"}:
            missing.append("feishu.mode")
        elif binding["mode"] == "create" and not binding.get("title"):
            missing.append("feishu.title")
        elif binding["mode"] == "bind" and not (binding.get("wiki_url") or binding.get("base_token") and binding.get("table_id")):
            missing.append("feishu.target")
    if modules.get("estimates") is True and not cfg.get("estimates", {}).get("library"):
        missing.append("estimates.library")
    for group in ("limits", "rotation"):
        for key, value in cfg[group].items():
            require(isinstance(value, int) and not isinstance(value, bool) and value > 0, "INVALID_LIMIT", field=group + "." + key)
    require(isinstance(cfg["context_chars"], int) and not isinstance(cfg["context_chars"], bool) and cfg["context_chars"] > 0, "INVALID_CONTEXT_BUDGET")
    for key in ("max_tasks", "max_active_agents", "max_tokens"):
        if key in cfg.get("budget", {}):
            value = cfg["budget"][key]
            require(isinstance(value, int) and not isinstance(value, bool) and value > 0, "INVALID_BUDGET", field=key)
    if cfg.get("budget", {}).get("deadline"):
        try:
            deadline = datetime.fromisoformat(cfg["budget"]["deadline"])
            require(deadline.tzinfo is not None, "DEADLINE_TIMEZONE_REQUIRED")
        except (ValueError, TypeError) as exc:
            raise SJSError("INVALID_DEADLINE") from exc
    if missing:
        return {"ok": False, "error": "DECISIONS_REQUIRED", "missing": missing,
                "choices": {"policy.mode": list(MODES), "policy.on_failure": ["skip", "stop"],
                            "modules.estimates": [True, False], "modules.feishu": [True, False]},
                "suggestions": {"policy.on_failure": "skip", "policy.max_retries": 3}}
    import schema_validate
    errors = schema_validate.validate_named(cfg, "run.schema.json")
    require(not errors, "INVALID_CONFIG", errors=errors)
    return {"ok": True, "config": cfg}


def effective_policy(cfg, task=None, tasks=None):
    base = cfg["policy"]
    if task and task.get("parent_id") and tasks:
        base = effective_policy(cfg, tasks[task["parent_id"]], tasks)
    return merge(base, (task or {}).get("policy", {}))


def effective_parameters(cfg, task=None, tasks=None):
    base = cfg.get("parameters", {})
    if task and task.get("parent_id") and tasks:
        base = effective_parameters(cfg, tasks[task["parent_id"]], tasks)
    return merge(base, (task or {}).get("parameters", {}))


def human(actor, approval=None):
    return actor.get("kind") == "human" or bool(isinstance(approval, dict) and approval.get("by") and approval.get("source"))


def validate_overrides(overrides, instance):
    require(isinstance(overrides, list), "INVALID_OVERRIDES")
    steps = set()
    for item in overrides:
        require(isinstance(item, dict) and item.get("step") in {"research", "implement", "verify", "report"}, "INVALID_OVERRIDE_STEP")
        rel = Path(item.get("path", ""))
        require(len(rel.parts) == 2 and rel.parts[0] == "overrides" and rel.name not in {".", ".."}, "INVALID_OVERRIDE_PATH")
        p = (Path(instance) / rel).resolve()
        require(p.is_relative_to(Path(instance).resolve()) and p.is_file(), "OVERRIDE_NOT_FOUND")
        require(item.get("reason"), "OVERRIDE_REASON_REQUIRED")
        require(item["step"] not in steps, "DUPLICATE_OVERRIDE_STEP")
        steps.add(item["step"])
        observed = hashlib.sha256(p.read_bytes()).hexdigest()
        if item.get("sha256"):
            require(item["sha256"] == observed, "OVERRIDE_CHANGED", path=item["path"])
        item["sha256"] = observed


def within(path, scopes, workspace):
    p = (Path(workspace) / path).resolve()
    return any(p.is_relative_to((Path(workspace) / scope).resolve()) for scope in scopes)
