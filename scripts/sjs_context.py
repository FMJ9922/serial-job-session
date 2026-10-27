"""Bounded role-specific context from projections, never evidence contents."""
from __future__ import annotations

import copy
import json

from sjs_config import ROOT, canonical, effective_parameters, effective_policy, require
from sjs_model import blocked_dependencies


def build_context(state, *, task_id=None, role="orchestrator", domain=None,
                  host_session=None, since=None, max_chars=None, handoff=False):
    definitions = json.loads((ROOT / "roles.json").read_text(encoding="utf-8"))
    require(role in definitions and role != "version", "UNKNOWN_ROLE")
    tasks, cfg = state["tasks"], state["config"]
    if task_id is not None:
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
               "parameters": effective_parameters(cfg, task, tasks),
               "route": state["route"], "role": {"name": role, "version": definitions["version"], "instructions": definitions[role], "session": reuse},
               "task": None, "menu": [], "knowledge": [], "warnings": [], "historical_scan": False,
               "output_evidence_read": False, "same_task": bool(handoff and task)}
    if task:
        keys = ("id", "title", "parent_id", "contract", "contract_version", "status", "blocked_reason", "attempt", "revision",
                "paths", "tools", "resources", "candidate", "overrides", "progress", "last_feedback", "recovery", "accepted_version", "last_patch")
        payload["task"] = {k: copy.deepcopy(task[k]) for k in keys if k in task}
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
    for t in tasks.values():
        if t["status"] not in {"accepted", "cancelled"}:
            payload["menu"].append({"id": t["id"], "title": t["title"], "status": t["status"],
                                    "parent_id": t.get("parent_id"), "blocked_by": blocked_dependencies(state, t),
                                    "ready": t["status"] == "ready" and not blocked_dependencies(state, t) and not state["halted"],
                                    "reason": t.get("blocked_reason")})
    payload["menu_count"] = len(payload["menu"])
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
    while len(canonical(payload)) > limit and payload["menu"]:
        payload["menu"].pop()
    while len(canonical(payload)) > limit and payload["knowledge"]:
        payload["knowledge"].pop()
    if len(payload["menu"]) < payload["menu_count"]:
        payload["warnings"].append("MENU_TRUNCATED")
    if len(canonical(payload)) > limit:
        payload["warnings"].append("CONTEXT_OVER_BUDGET: protected contract and authorization retained")
    payload["measurements"] = {"chars": len(canonical(payload)), "bytes": len(canonical(payload).encode("utf-8")), "limit_chars": limit}
    return payload
