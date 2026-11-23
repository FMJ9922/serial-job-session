"""Bounded role-specific context from projections, never evidence contents."""
from __future__ import annotations

import copy
import json
import re

from sjs_config import ROOT, canonical, effective_parameters, effective_policy, require, same_template, template_identity
from sjs_model import ancestors, blocked_dependencies


def _select_recovery_task(state, task_id):
    tasks = state["tasks"]
    if task_id is not None:
        require(task_id in tasks, "TASK_NOT_FOUND", task_id=task_id)
        return tasks[task_id], {"reason": "explicit", "active_leaf_ids": []}
    active = [task for task in tasks.values() if task["status"] in {"doing", "awaiting_acceptance"}]
    parents = {parent for task in active for parent in ancestors(state, task)}
    leaves = [task["id"] for task in active if task["id"] not in parents]
    if len(leaves) == 1:
        return tasks[leaves[0]], {"reason": "active_leaf", "active_leaf_ids": leaves}
    reason = "multiple_active_leaves" if leaves else "no_active_task"
    return None, {"reason": reason, "active_leaf_ids": leaves}


# Identify explicit statements in legacy progress only as clues for human review.
# Neither text classification nor this read-only view authorizes dispatch.
_PAUSE_REPORT = re.compile(
    r"^(?:用户(?:已(?:经)?)?(?:明确)?(?:要求|指示)?(?:暂停|停止派工)|"
    r"user (?:(?:has|explicitly) )*(?:paused|requested (?:a )?pause)|"
    r"(?:please )?stop dispatch)(?=$|[\s，。；;：:,!.])", re.IGNORECASE)
_RESUMED_REPORT = re.compile(
    r"[，。；;,!.]\s*(?:(?:但|随后|现)?(?:用户已(?:经)?(?:明确)?(?:要求)?恢复(?:派工|工作|执行)?|"
    r"(?:已)?(?:解除|取消|撤销)暂停)(?=$|[\s，。；;,.!])|"
    r"(?:but )?(?:user (?:has )?resumed|(?:work|dispatch) (?:has )?resumed|"
    r"pause (?:lifted|revoked))\b)", re.IGNORECASE)


def _recovery_dispatch(state, task):
    sources = []
    statuses = set()

    def note(status, task_id, field, value):
        statuses.add(status)
        sources.append({"scope": "workflow" if task_id is None else "task", "task_id": task_id,
                        "field": field, "value": copy.deepcopy(value)})

    if state["halted"]:
        note("halted", None, "halted", True)
    scopes = [(None, state["config"])]
    if task:
        scopes.extend((key, state["tasks"][key]) for key in reversed(ancestors(state, task)))
        scopes.append((task["id"], task))
    for task_id, owner in scopes:
        marker = owner.get("parameters", {}).get("recovery_pause")
        if isinstance(marker, dict) and isinstance(marker.get("source"), str) and marker["source"].strip():
            if isinstance(marker.get("paused"), bool):
                note("paused" if marker["paused"] else "unverified", task_id,
                     "parameters.recovery_pause", {"paused": marker["paused"], "source": marker["source"]})
        if owner.get("blocked_reason"):
            note("blocked", task_id, "blocked_reason", owner["blocked_reason"])
        progress = owner.get("progress", {})
        statements = [progress.get("summary", ""), *progress.get("decisions", [])]
        for statement in statements:
            if (isinstance(statement, str) and _PAUSE_REPORT.match(statement.strip())
                    and not _RESUMED_REPORT.search(statement)):
                note("review_pause", task_id, "progress", statement)
    status = next((key for key in ("halted", "paused", "review_pause", "blocked") if key in statuses),
                  "unverified")
    return {"allowed": False, "status": status, "sources": sources}


def project_pointer(payload, pointer, *, keys_only=False):
    """Select an exact full-view value; object keys are opaque, including '/' and '~'."""
    require(not pointer or pointer.startswith("/"), "INVALID_JSON_POINTER", pointer=pointer)
    value = payload
    for token in pointer.split("/")[1:]:
        require(not re.search(r"~(?![01])", token), "INVALID_JSON_POINTER", pointer=pointer)
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, list):
            require(re.fullmatch(r"0|[1-9][0-9]*", key) and len(key) <= len(str(len(value)))
                    and int(key) < len(value), "POINTER_NOT_FOUND", pointer=pointer)
            value = value[int(key)]
        else:
            require(isinstance(value, dict) and key in value, "POINTER_NOT_FOUND", pointer=pointer)
            value = value[key]
    result = {"revision": payload["revision"], "task_id": payload["task_id"], "pointer": pointer}
    if keys_only:
        require(isinstance(value, dict), "POINTER_OBJECT_REQUIRED", pointer=pointer)
        result["keys"] = sorted(value)
    else:
        result["value"] = value
    return result


def build_context(state, *, task_id=None, role="orchestrator", domain=None,
                  host_session=None, since=None, max_chars=None, handoff=False, view="full"):
    definitions = json.loads((ROOT / "roles.json").read_text(encoding="utf-8"))
    require(role in definitions and role != "version", "UNKNOWN_ROLE")
    tasks, cfg = state["tasks"], state["config"]
    require(view in {"full", "recovery"}, "INVALID_CONTEXT_VIEW")
    recovery_view = view == "recovery"
    if recovery_view:
        task, selection = _select_recovery_task(state, task_id)
    elif task_id is not None:
        require(task_id in tasks, "TASK_NOT_FOUND", task_id=task_id)
        task = tasks[task_id]
    else:
        task = next((t for t in tasks.values() if t["status"] in {"doing", "awaiting_acceptance"}), None)
    domain = domain or (task or {}).get("domain", "default")
    candidates = [r for r in state["roles"].values() if r["status"] == "active" and r["role"] == role and r["domain"] == domain
                  and r["definition_version"] == definitions["version"] and r["host_session"] == host_session]
    if task and role == "reviewer":
        candidates = [r for r in candidates if r["handle"] not in task["contributors"]
                      and not (task["attempt"] >= 2 and r["handle"] in task["reviewers"])]
    reuse = {"action": "reuse", "handle": candidates[-1]["handle"], "validate_with_host": True} if candidates else {
        "action": "spawn", "reason": "No eligible handle in the current host session; use this handoff as initialization."}
    payload = {"schema": "sjs-context/v5", "revision": state["revision"], "task_id": (task or {}).get("id"),
               "goal": cfg["goal"], "authorization": cfg["authorization"], "workspace": cfg["workspace"],
               "constraints": cfg.get("constraints", []), "policy": effective_policy(cfg, task, tasks),
               "limits": cfg["limits"], "budget": cfg.get("budget", {}), "halted": state["halted"],
               "template": cfg["template"], "modules": cfg["modules"],
               "route": state["route"], "role": {"name": role, "version": definitions["version"], "instructions": definitions[role], "session": reuse},
               "task": None, "menu": [], "knowledge": [], "warnings": [], "historical_scan": False,
               "output_evidence_read": False, "same_task": bool(handoff and task)}
    if recovery_view:
        full_args = ["--view", "full", "--task-id", task["id"]] if task else None
        payload.update(schema="sjs-recovery/v5", selection=selection, full_view_args=full_args,
                       rebind_required=not same_template(cfg["template"], template_identity()),
                       dispatch=_recovery_dispatch(state, task), tokens_used=state["tokens_used"])
        # Recovery reports identity only. The legacy role.session includes a spawn recommendation.
        del payload["role"]["session"]
        payload["warnings"].append("READ_ONLY_RECOVERY: check full terms, effective parameters and current user instructions before execution")
    else:
        payload["parameters"] = effective_parameters(cfg, task, tasks)
    if task:
        keys = ("id", "title", "parent_id", "contract", "contract_version", "status", "blocked_reason", "attempt", "revision",
                "paths", "tools", "resources", "candidate", "overrides", "progress", "last_feedback", "recovery", "accepted_version", "last_patch")
        if recovery_view:
            keys = ("id", "title", "parent_id", "contract_version", "status", "blocked_reason", "attempt",
                    "revision", "paths", "tools", "resources", "candidate", "progress", "last_feedback", "accepted_version")
        payload["task"] = {k: copy.deepcopy(task[k]) for k in keys if k in task}
        if recovery_view:
            payload["task"]["contract"] = {k: copy.deepcopy(task["contract"][k]) for k in ("goal", "checks")}
            payload["task"]["blocked_by"] = blocked_dependencies(state, task)
            payload["task"]["omitted_contract_pointers"] = [
                "/task/contract/" + key.replace("~", "~0").replace("/", "~1")
                for key in task["contract"] if key not in {"goal", "checks"}]
        else:
            overrides = {o["step"]: o for o in cfg.get("overrides", [])}
            overrides.update({o["step"]: o for o in task.get("overrides", [])})
            payload["task"]["overrides"] = list(overrides.values())
            payload["task"]["assessments"] = copy.deepcopy(task["assessments"])
            payload["task"]["dependencies"] = [{"id": key, "status": tasks[key]["status"], "version": tasks[key].get("accepted_version"),
                                                 "outputs": (tasks[key].get("candidate") or {}).get("evidence", [])} for key in task["depends_on"]]
            payload["task"]["children"] = [{"id": t["id"], "status": t["status"], "required": t["required"]} for t in tasks.values() if t.get("parent_id") == task["id"]]
            tags = set(task["contract"].get("tags", [])) | {domain}
            for key, item in state["knowledge"].items():
                if not item.get("tags") or tags & set(item["tags"]):
                    payload["knowledge"].append({"id": key, **item})
    menu_tasks = tasks.values()
    ambiguous = recovery_view and selection["reason"] == "multiple_active_leaves"
    if ambiguous:
        menu_tasks = (tasks[key] for key in selection["active_leaf_ids"])
    elif recovery_view and task:
        menu_tasks = ()
    for t in menu_tasks:
        if t["status"] not in {"accepted", "cancelled"}:
            payload["menu"].append({"id": t["id"], "title": t["title"], "status": t["status"],
                                    "parent_id": t.get("parent_id"), "blocked_by": blocked_dependencies(state, t),
                                    "ready": t["status"] == "ready" and not blocked_dependencies(state, t) and not state["halted"],
                                    "reason": t.get("blocked_reason")})
            if recovery_view:
                del payload["menu"][-1]["ready"]
                if ambiguous:
                    payload["menu"][-1]["dispatch_status"] = _recovery_dispatch(state, t)["status"]
    payload["menu_count"] = len(payload["menu"])
    if not ambiguous:
        payload["menu"] = payload["menu"][:32]
    if since is not None:
        require(isinstance(since, int) and 0 <= since <= state["revision"], "INVALID_CONTEXT_CURSOR")
        changed = [t["id"] for t in tasks.values() if t["revision"] > since]
        payload["changes"] = {"since_revision": since, "task_ids": changed[:32], "task_count": len(changed),
                              "route_changed": bool(state["route"] and state["route"]["seq"] > since),
                              "policy_changed": cfg.get("policy_revision", 1) > since}
    if not task:
        payload["warnings"].append("MENU_ONLY: choose a task according to the selected autonomy policy; context does not start it")
    limit = max_chars or cfg["context_chars"]
    require(isinstance(limit, int) and limit > 0, "INVALID_CONTEXT_BUDGET")
    # Only optional summaries are shed; preserve contract and scope.
    while not ambiguous and len(canonical(payload)) > limit and payload["menu"]:
        payload["menu"].pop()
    while len(canonical(payload)) > limit and payload["knowledge"]:
        payload["knowledge"].pop()
    if len(payload["menu"]) < payload["menu_count"]:
        payload["warnings"].append("MENU_TRUNCATED")
    if len(canonical(payload)) > limit:
        payload["warnings"].append("CONTEXT_OVER_BUDGET: protected contract and authorization retained")
    payload["measurements"] = {"chars": len(canonical(payload)), "bytes": len(canonical(payload).encode("utf-8")), "limit_chars": limit}
    return payload
