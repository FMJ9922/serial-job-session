"""Pure event reducer and governance for v5. No filesystem or host calls."""
from __future__ import annotations

import copy
from datetime import datetime, timezone

from sjs_config import (RISKS, SJSError, clean, digest, effective_policy, human,
                        merge, number, policy_errors, require, within)

STATUSES = ("ready", "doing", "awaiting_acceptance", "accepted", "failed", "cancelled")
TERMINAL = {"accepted", "failed", "cancelled"}
ROLES = {"orchestrator", "assessor", "worker", "reviewer", "committer", "recorder"}


def empty_state():
    return {"schema": "sjs-state/v5", "revision": 0, "config": {}, "tasks": {},
            "directions": {}, "route": None, "roles": {}, "knowledge": {},
            "commands": {}, "cursors": {}, "segments": {}, "sync": {},
            "halted": False, "tokens_used": 0}


def actor_record(value):
    require(isinstance(value, dict) and isinstance(value.get("id"), str) and value["id"].strip()
            and value.get("kind") in {"agent", "human"}, "ACTOR_REQUIRED")
    return value


def assessment(value, phase, actor, task=None):
    require(isinstance(value, dict), "ASSESSMENT_REQUIRED", phase=phase)
    data = copy.deepcopy(value)
    require(data.get("id") and isinstance(data["id"], str), "ASSESSMENT_ID_REQUIRED")
    data["by"] = actor_record(data.get("by", actor))
    require(data.get("score") is None or number(data["score"], 0, 100), "INVALID_SCORE")
    require(data.get("risk") in RISKS, "RISK_REQUIRED")
    require(data.get("conclusion") in {"pass", "blocking", "uncertain"}, "ASSESSMENT_CONCLUSION_REQUIRED")
    require(isinstance(data.get("evidence"), list), "EVIDENCE_REQUIRED")
    require(isinstance(data.get("unknowns"), list), "UNKNOWNS_REQUIRED")
    require(data.get("rationale") and "recovery_cost" in data, "ASSESSMENT_RATIONALE_REQUIRED")
    if data.get("score") is None:
        require(data["unknowns"], "UNKNOWN_SCORE_REASON_REQUIRED")
    data["phase"] = phase
    if task:
        data["contract_version"] = task["contract_version"]
    return data


def decision(policy, phase, evaluated, actor, approval=None):
    if human(actor, approval):
        return {"allowed": True, "by": "human", "source": approval or actor["id"]}
    mode = policy["mode"]
    if mode == "manual":
        return {"allowed": False, "reason": "human_decision"}
    if not evaluated:
        return {"allowed": False, "reason": "assessment_required"}
    if mode == "full_ai":
        return {"allowed": True, "by": "agent", "risk_accepted": evaluated["risk"], "score": evaluated.get("score")}
    if evaluated.get("conclusion") != "pass":
        return {"allowed": False, "reason": "assessment_not_passed"}
    if not evaluated.get("evidence"):
        return {"allowed": False, "reason": "evidence_missing"}
    if evaluated.get("score") is None or evaluated["score"] < policy["thresholds"][phase]:
        return {"allowed": False, "reason": "below_threshold"}
    if RISKS[evaluated["risk"]] > RISKS[policy["risk_ceiling"]]:
        return {"allowed": False, "reason": "risk_exceeded"}
    return {"allowed": True, "by": "agent", "score": evaluated["score"], "risk": evaluated["risk"]}


def task_by_id(state, task_id):
    require(task_id in state["tasks"], "TASK_NOT_FOUND", task_id=task_id)
    return state["tasks"][task_id]


def blocked_dependencies(state, task):
    return [ref for ref in task["depends_on"] if state["tasks"].get(ref, {}).get("status") != "accepted"]


def ancestors(state, task):
    chain = []
    while task.get("parent_id"):
        chain.append(task["parent_id"])
        task = task_by_id(state, task["parent_id"])
    return chain


def _validate_policy_change(base, updates, actor, approval):
    combined = merge(base, updates)
    require(not policy_errors(combined), "INVALID_POLICY", fields=policy_errors(combined))
    if not human(actor, approval):
        rank = {"manual": 0, "conditional": 1, "full_ai": 2}
        relaxed = rank[combined["mode"]] > rank[base["mode"]]
        relaxed |= base.get("independent_review", True) and not combined["independent_review"]
        if base["mode"] == combined["mode"] == "conditional":
            relaxed |= RISKS[combined["risk_ceiling"]] > RISKS[base["risk_ceiling"]]
            relaxed |= any(combined["thresholds"][k] < base["thresholds"][k] for k in base["thresholds"])
        relaxed |= combined["max_retries"] > base["max_retries"]
        require(not relaxed, "POLICY_RELAXATION_REQUIRES_HUMAN")
    return combined


def _validate_contract(contract):
    require(isinstance(contract, dict) and contract.get("goal"), "CONTRACT_GOAL_REQUIRED")
    checks = contract.get("checks")
    require(isinstance(checks, list) and checks, "CONTRACT_CHECKS_REQUIRED")
    ids = []
    for check in checks:
        require(isinstance(check, dict) and isinstance(check.get("id"), str) and check.get("description"), "INVALID_CHECK")
        require(isinstance(check.get("required", True), bool), "INVALID_CHECK")
        ids.append(check["id"])
    require(len(set(ids)) == len(ids), "DUPLICATE_CHECK")


def _validate_graph(state):
    visited, visiting = set(), set()
    def visit(key):
        require(key not in visiting, "DEPENDENCY_CYCLE", task_id=key)
        if key in visited:
            return
        visiting.add(key)
        task = state["tasks"][key]
        for ref in task["depends_on"]:
            require(ref in state["tasks"], "DEPENDENCY_NOT_FOUND", task_id=ref)
            require(ref not in ancestors(state, task), "ANCESTOR_DEPENDENCY")
            visit(ref)
        # Parent completion depends on required children, even if their edge
        # is not written in depends_on. Include it in cycle detection.
        for child in state["tasks"].values():
            if child.get("parent_id") == key and child.get("required", True):
                visit(child["id"])
        visiting.remove(key)
        visited.add(key)
    for key in state["tasks"]:
        visit(key)


def _check_scope(cfg, task, parent=None):
    auth = cfg["authorization"]
    scopes = parent["paths"] if parent else auth["paths"]
    tools = parent["tools"] if parent else auth["tools"]
    require(all(within(p, scopes, cfg["workspace"]) for p in task["paths"]), "PATH_NOT_AUTHORIZED")
    require(set(task["tools"]) <= set(tools), "TOOL_NOT_AUTHORIZED")


def _start_resources(state, task):
    cfg = state["config"]
    active = [t for t in state["tasks"].values()
              if t["id"] != task["id"] and t["status"] in {"doing", "awaiting_acceptance"}
              and not any(c.get("parent_id") == t["id"] and c["status"] not in TERMINAL for c in state["tasks"].values())
              and t["id"] not in ancestors(state, task)]
    require(len(active) < cfg["limits"]["max_parallel"], "PARALLEL_LIMIT")
    for other in active:
        require(not set(task["resources"]) & set(other["resources"]), "RESOURCE_BUSY", owner=other["id"])
        overlap = any(within(p, other["paths"], cfg["workspace"]) or within(q, task["paths"], cfg["workspace"])
                      for p in task["paths"] for q in other["paths"])
        require(not overlap, "WRITE_SCOPE_OVERLAP", owner=other["id"])


def _budget(state, now, task):
    cfg = state["config"]
    budget = cfg.get("budget", {})
    require(not state["halted"], "WORKFLOW_HALTED")
    if budget.get("deadline"):
        require(datetime.fromisoformat(now) <= datetime.fromisoformat(budget["deadline"]), "DEADLINE_EXCEEDED")
    if budget.get("max_tasks") is not None and not task.get("parent_id"):
        started = sum(bool(t.get("started_at")) and not t.get("parent_id") for t in state["tasks"].values())
        require(started < budget["max_tasks"], "TASK_BUDGET_EXHAUSTED")
    if budget.get("max_tokens") is not None:
        require(state["tokens_used"] < budget["max_tokens"], "TOKEN_BUDGET_EXHAUSTED")


def reduce_command(state, command, *, replay=False):
    """Reduce one durable envelope, validating only on its initial commit."""
    result = copy.deepcopy(state)
    operations = command["operations"]
    actor, now, seq = command["actor"], command["at"], command["seq"]
    outcomes = []
    for op in operations:
        name = op.get("op")
        cfg = result["config"]
        task = result["tasks"].get(op.get("task_id"))
        if name == "initialize":
            if not replay:
                require(result["revision"] == 0, "ALREADY_INITIALIZED")
            result["config"] = copy.deepcopy(op["config"])
            result["run_hash"] = op["run_hash"]
        elif name == "create":
            key = op.get("task_id")
            parent = result["tasks"].get(op.get("parent_id"))
            policy = op.get("policy", {})
            if not replay:
                require(isinstance(key, str) and key.strip() and len(key) <= 160, "TASK_ID_REQUIRED")
                require(key not in result["tasks"], "TASK_EXISTS")
                _validate_contract(op.get("contract"))
                if op.get("parent_id"):
                    require(parent is not None and parent["status"] == "doing", "PARENT_NOT_ACTIVE")
                    require(parent.get("nesting_authorized"), "NESTING_NOT_AUTHORIZED")
                    require(len(ancestors(result, parent)) + 1 <= cfg["limits"]["max_depth"], "DEPTH_LIMIT")
                    require(sum(t.get("parent_id") == parent["id"] for t in result["tasks"].values()) < cfg["limits"]["max_children"], "CHILD_LIMIT")
                if policy:
                    _validate_policy_change(effective_policy(cfg, parent, result["tasks"]), policy, actor, op.get("approval"))
                require(isinstance(op.get("parameters", {}), dict), "INVALID_PARAMETERS")
            scopes = parent if parent else cfg["authorization"]
            task = {"id": key, "title": op.get("title", op["contract"]["goal"]),
                    "contract": copy.deepcopy(op["contract"]), "contract_version": 1,
                    "depends_on": list(op.get("depends_on", [])), "parent_id": op.get("parent_id"),
                    "required": op.get("required", True), "status": "ready", "attempt": 0,
                    "policy": copy.deepcopy(policy),
                    "parameters": clean(op.get("parameters", {})),
                    "policy_source": op.get("approval") or actor, "policy_revision": seq,
                    "paths": list(op.get("paths", scopes["paths"])), "tools": list(op.get("tools", scopes["tools"])),
                    "resources": sorted(set(op.get("resources", [])) | set((parent or {}).get("resources", []))),
                    "domain": op.get("domain", (parent or {}).get("domain", "default")),
                    "assessments": {}, "used_assessment_ids": [], "candidate": None,
                    "candidate_versions": [], "reviewers": [], "contributors": [],
                    "overrides": op.get("overrides", []), "revision": seq, "created_at": now}
            if not replay:
                _check_scope(cfg, task, parent)
                require(isinstance(task["required"], bool), "INVALID_REQUIRED_FLAG")
            result["tasks"][key] = task
        elif name == "assess":
            if not replay:
                require(task is not None and task["status"] not in TERMINAL, "TASK_NOT_ACTIVE")
                require(op.get("phase") in {"feasibility", "acceptance"}, "INVALID_ASSESSMENT_PHASE")
            phase = op["phase"]
            data = assessment(op["assessment"], phase, actor, task) if not replay else copy.deepcopy(op["assessment"])
            if not replay:
                require(data["id"] not in task["used_assessment_ids"], "ASSESSMENT_ID_REUSED")
                if "contract_version" in op["assessment"]:
                    require(op["assessment"]["contract_version"] == task["contract_version"], "STALE_CONTRACT")
                if phase == "acceptance":
                    require(task["candidate"] and data.get("candidate_version") == task["candidate"]["version"], "STALE_CANDIDATE")
                    require(op["assessment"].get("contract_version") == task["contract_version"], "STALE_CONTRACT")
                    policy = effective_policy(cfg, task, result["tasks"])
                    if policy["independent_review"]:
                        require(data["by"]["id"] not in task["contributors"], "REVIEWER_NOT_INDEPENDENT")
                    if task["attempt"] >= 2:
                        require(data["by"]["id"] not in task["reviewers"], "FRESH_REVIEWER_REQUIRED")
                op["assessment"] = copy.deepcopy(data)
            task["assessments"][phase] = data
            task["used_assessment_ids"].append(data["id"])
        elif name == "start":
            if not replay:
                require(task is not None and task["status"] == "ready", "TASK_NOT_READY")
                require(not blocked_dependencies(result, task), "DEPENDENCY_BLOCKED", dependencies=blocked_dependencies(result, task))
                require(all(result["tasks"][p]["status"] == "doing" for p in ancestors(result, task)), "PARENT_NOT_ACTIVE")
                _budget(result, now, task)
            evaluated = task["assessments"].get("feasibility")
            verdict = op.get("verdict") if replay else decision(effective_policy(cfg, task, result["tasks"]), "feasibility", evaluated, actor, op.get("approval"))
            op["verdict"] = verdict
            if verdict["allowed"]:
                if not replay:
                    _start_resources(result, task)
                task.update(status="doing", started_at=now, blocked_reason=None)
            else:
                task["blocked_reason"] = verdict["reason"]
            outcomes.append({"task_id": task["id"], "decision": verdict})
        elif name == "report":
            if not replay:
                require(task is not None and task["status"] in {"doing", "awaiting_acceptance"}, "TASK_NOT_DOING")
                children = [t["id"] for t in result["tasks"].values() if t.get("parent_id") == task["id"] and t["required"] and t["status"] != "accepted"]
                require(not children, "CHILDREN_NOT_ACCEPTED", children=children)
                require(op.get("candidate_version") and op["candidate_version"] not in task["candidate_versions"], "CANDIDATE_VERSION_REUSED")
                require(isinstance(op.get("evidence"), list) and op["evidence"], "CANDIDATE_EVIDENCE_REQUIRED")
                actor_record(op.get("by", actor))
            contributor = op.get("by", actor)["id"]
            task["contributors"] = sorted(set(task["contributors"]) | {contributor} | set(op.get("contributors", [])))
            task["candidate_versions"].append(op["candidate_version"])
            task["candidate"] = {"version": op["candidate_version"], "contract_version": task["contract_version"],
                                 "evidence": copy.deepcopy(op["evidence"]), "summary": op.get("summary", "")}
            task["assessments"].pop("acceptance", None)
            task.update(status="awaiting_acceptance", blocked_reason=None)
        elif name == "accept":
            data = (task or {}).get("assessments", {}).get("acceptance")
            if not replay:
                require(task is not None and task["status"] == "awaiting_acceptance", "NOT_AWAITING_ACCEPTANCE")
                require(data is not None, "ACCEPTANCE_ASSESSMENT_REQUIRED")
                require(data["candidate_version"] == task["candidate"]["version"] and data["contract_version"] == task["contract_version"], "STALE_ACCEPTANCE")
                require(data["conclusion"] == "pass", "ACCEPTANCE_NOT_PASSED")
                checks = data.get("checks", {})
                for check in task["contract"]["checks"]:
                    if check.get("required", True):
                        observed = checks.get(check["id"], {})
                        require(observed.get("status") == "passed" and observed.get("evidence"), "REQUIRED_CHECK_NOT_PASSED", check_id=check["id"])
                children = [t["id"] for t in result["tasks"].values() if t.get("parent_id") == task["id"] and t["required"] and t["status"] != "accepted"]
                require(not children, "CHILDREN_NOT_ACCEPTED", children=children)
                require(not blocked_dependencies(result, task), "DEPENDENCY_BLOCKED")
            verdict = op.get("verdict") if replay else decision(effective_policy(cfg, task, result["tasks"]), "acceptance", data, actor, op.get("approval"))
            op["verdict"] = verdict
            if verdict["allowed"]:
                task.update(status="accepted", accepted_at=now, closed_at=now, blocked_reason=None,
                            accepted_version=task["candidate"]["version"], learning=op.get("learning", "none"))
                for child in result["tasks"].values():
                    if child["status"] not in TERMINAL and task["id"] in ancestors(result, child):
                        child.update(status="cancelled", closed_at=now, revision=seq,
                                     last_feedback="Parent accepted; unfinished optional scope cancelled", recovery="Preserve optional results for a separately authorized task.")
            else:
                task["blocked_reason"] = verdict["reason"]
            outcomes.append({"task_id": task["id"], "decision": verdict})
        elif name in {"reject", "fail", "cancel"}:
            if not replay:
                require(task is not None and task["status"] not in TERMINAL, "TASK_NOT_ACTIVE")
                require(op.get("reason"), "REASON_REQUIRED")
            if name == "reject":
                if not replay:
                    require(task["status"] == "awaiting_acceptance", "NOT_AWAITING_ACCEPTANCE")
                reviewer = task["assessments"].get("acceptance", {}).get("by", {}).get("id")
                if reviewer:
                    task["reviewers"].append(reviewer)
                if task["attempt"] < effective_policy(cfg, task, result["tasks"])["max_retries"]:
                    task.update(status="doing", attempt=task["attempt"] + 1, blocked_reason=None)
                else:
                    task.update(status="failed", closed_at=now)
            else:
                task.update(status="failed" if name == "fail" else "cancelled", closed_at=now)
            task["last_feedback"] = op["reason"]
            task["preserved_results"] = op.get("preserved_results", (task.get("candidate") or {}).get("evidence", []))
            task["recovery"] = op.get("recovery", "Preserve artifacts; do not reset or stash shared changes.")
            if task["status"] in {"failed", "cancelled"}:
                task["learning"] = op.get("learning", "none")
                if task["status"] == "failed" and effective_policy(cfg, task, result["tasks"])["on_failure"] == "stop":
                    result["halted"] = True
                for child in result["tasks"].values():
                    if child["status"] not in TERMINAL and task["id"] in ancestors(result, child):
                        child.update(status="cancelled", closed_at=now, revision=seq, last_feedback="Parent " + task["status"],
                                     recovery="Preserve child results; parent is no longer executable.")
                failed = task
                while failed.get("parent_id") and failed.get("required", True):
                    parent = result["tasks"][failed["parent_id"]]
                    if parent["status"] in TERMINAL:
                        break
                    parent.update(status="failed", closed_at=now, revision=seq,
                                  last_feedback="Required child did not complete: " + failed["id"],
                                  preserved_results=(parent.get("candidate") or {}).get("evidence", []),
                                  recovery="Preserve branch results; create a new explicitly scoped contract if needed.")
                    if effective_policy(cfg, parent, result["tasks"])["on_failure"] == "stop":
                        result["halted"] = True
                    for child in result["tasks"].values():
                        if child["status"] not in TERMINAL and parent["id"] in ancestors(result, child):
                            child.update(status="cancelled", closed_at=now, revision=seq,
                                         last_feedback="Required sibling failed; branch cannot complete", recovery="Preserve branch results.")
                    failed = parent
        elif name == "revise_contract":
            if not replay:
                require(task is not None and task["status"] not in TERMINAL, "TASK_NOT_ACTIVE")
                _validate_contract(op.get("contract"))
                require(op.get("reason"), "REASON_REQUIRED")
                require(not any((task["id"] in t["depends_on"] or t.get("parent_id") == task["id"]) and t.get("started_at") for t in result["tasks"].values()), "DOWNSTREAM_ALREADY_STARTED")
                old = {c["id"]: c for c in task["contract"]["checks"] if c.get("required", True)}
                new = {c["id"]: c for c in op["contract"]["checks"] if c.get("required", True)}
                if any(k not in new or old[k] != new[k] for k in old):
                    require(human(actor, op.get("approval")), "REQUIRED_CHECK_CHANGE_REQUIRES_HUMAN")
            task["contract"] = copy.deepcopy(op["contract"])
            task["contract_version"] += 1
            task["candidate"] = None
            task["assessments"] = {}
            task["contract_change_reason"] = op["reason"]
            if task.get("started_at"):
                task["status"] = "doing"
        elif name == "patch":
            if not replay:
                require(task is not None and task["status"] == "accepted", "PATCH_REQUIRES_ACCEPTED_TASK")
                require(op.get("summary") and op.get("evidence"), "PATCH_EVIDENCE_REQUIRED")
                require(not any(k in op for k in ("contract", "candidate_version", "policy", "status")), "PATCH_CANNOT_CHANGE_CONTRACT")
            task["last_patch"] = {"summary": op["summary"], "evidence": op["evidence"], "seq": seq}
        elif name == "block":
            if not replay:
                require(task is not None and task["status"] not in TERMINAL, "TASK_NOT_ACTIVE")
                require(op.get("reason"), "REASON_REQUIRED")
            task["blocked_reason"] = op["reason"]
        elif name == "progress":
            if not replay:
                require(task is not None and task["status"] not in TERMINAL, "TASK_NOT_ACTIVE")
                require(isinstance(op.get("summary"), str) and op["summary"].strip(), "PROGRESS_SUMMARY_REQUIRED")
                for key in ("questions", "decisions", "next_steps"):
                    require(isinstance(op.get(key, []), list), "INVALID_PROGRESS", field=key)
            task["progress"] = {"summary": op["summary"], "questions": op.get("questions", []),
                                "decisions": op.get("decisions", []), "next_steps": op.get("next_steps", []), "seq": seq}
        elif name == "set_policy":
            base = effective_policy(cfg, task, result["tasks"])
            if not replay:
                _validate_policy_change(base, op["policy"], actor, op.get("approval"))
            if task:
                task["policy"] = merge(task["policy"], op["policy"])
                task["policy_source"] = op.get("approval") or actor
                task["policy_revision"] = seq
            else:
                cfg["policy"] = merge(cfg["policy"], op["policy"])
                cfg["policy_revision"] = seq
        elif name == "nest":
            if not replay:
                require(task is not None and task["status"] == "doing", "TASK_NOT_DOING")
                require(op.get("reason"), "NESTING_REASON_REQUIRED")
            data = task["assessments"].get("feasibility")
            verdict = op.get("verdict") if replay else decision(effective_policy(cfg, task, result["tasks"]), "feasibility", data, actor, op.get("approval"))
            op["verdict"] = verdict
            task["nesting_authorized"] = verdict["allowed"]
            task["nesting_reason"] = op["reason"]
            outcomes.append({"task_id": task["id"], "decision": verdict})
        elif name == "assess_direction":
            data = assessment(op["assessment"], "direction", actor) if not replay else copy.deepcopy(op["assessment"])
            if not replay:
                require(data["id"] not in result["directions"], "ASSESSMENT_ID_REUSED")
                require(data.get("route") and data.get("candidates"), "DIRECTION_OPTIONS_REQUIRED")
                data["goal"] = cfg["goal"]
                op["assessment"] = copy.deepcopy(data)
            result["directions"][data["id"]] = data
        elif name == "apply_direction":
            data = result["directions"].get(op.get("assessment_id"))
            if not replay:
                require(data is not None and data.get("goal") == cfg["goal"], "DIRECTION_ASSESSMENT_REQUIRED")
            verdict = op.get("verdict") if replay else decision(cfg["policy"], "direction", data, actor, op.get("approval"))
            op["verdict"] = verdict
            if verdict["allowed"]:
                result["route"] = {"assessment_id": data["id"], "route": data["route"], "seq": seq}
            outcomes.append({"decision": verdict})
        elif name == "assign_role":
            slot = op.get("slot")
            if not replay:
                require(slot and op.get("role") in ROLES and op.get("domain") and op.get("handle") and op.get("host_session"), "ROLE_BINDING_REQUIRED")
                if cfg.get("budget", {}).get("max_active_agents") is not None:
                    active = sum(v["status"] == "active" and k != slot for k, v in result["roles"].items())
                    require(active < cfg["budget"]["max_active_agents"], "AGENT_BUDGET_EXHAUSTED")
            result["roles"][slot] = {k: op[k] for k in ("role", "domain", "handle", "host_session")}
            result["roles"][slot].update(status="active", definition_version=1, seq=seq)
        elif name == "set_overrides":
            if not replay:
                require(op.get("reason"), "OVERRIDE_REASON_REQUIRED")
            if task:
                task["overrides"] = copy.deepcopy(op["overrides"])
            else:
                cfg["overrides"] = copy.deepcopy(op["overrides"])
        elif name == "set_parameters":
            if not replay:
                require(isinstance(op.get("parameters"), dict) and op.get("reason"), "INVALID_PARAMETERS")
            target = task if task is not None else cfg
            target["parameters"] = merge(target.get("parameters", {}), clean(op["parameters"]))
            target["parameters_revision"] = seq
        elif name == "invalidate_role":
            if not replay:
                require(op.get("slot") in result["roles"] and op.get("reason"), "ROLE_NOT_FOUND")
            result["roles"][op["slot"]].update(status="invalid", reason=op["reason"])
        elif name == "knowledge":
            if not replay:
                require(op.get("id") and op.get("text") and op.get("evidence"), "KNOWLEDGE_EVIDENCE_REQUIRED")
                require(isinstance(op.get("tags", []), list), "INVALID_TAGS")
            for old in op.get("replaces", []):
                result["knowledge"].pop(old, None)
            result["knowledge"][op["id"]] = {k: copy.deepcopy(op.get(k)) for k in ("text", "evidence", "tags", "task_id")}
        elif name == "timing":
            if not replay:
                require(cfg["modules"]["estimates"] and task is not None, "ESTIMATES_DISABLED_OR_TASK_MISSING")
                sample = op["sample"]
                previous = task.get("timing", {}).get(sample["id"])
                if previous and previous.get("thread_id"):
                    require((previous.get("thread_id"), previous.get("turn_id")) == (sample.get("thread_id"), sample.get("turn_id")), "TIMING_CONTEXT_CHANGED")
                if sample.get("thread_id"):
                    for other in result["tasks"].values():
                        for key, old in other.get("timing", {}).items():
                            if (other["id"], key) != (task["id"], sample["id"]):
                                require((old.get("thread_id"), old.get("turn_id")) != (sample["thread_id"], sample["turn_id"]), "DUPLICATE_TIMING_CONTEXT")
            task.setdefault("timing", {})[op["sample"]["id"]] = copy.deepcopy(op["sample"])
        elif name == "estimate":
            if not replay:
                require(cfg["modules"]["estimates"] and task is not None, "ESTIMATES_DISABLED_OR_TASK_MISSING")
                require(number(op.get("hours")) and op.get("source"), "INVALID_ESTIMATE")
            task["estimate"] = {"hours": op["hours"], "source": op["source"], "group": op.get("group", "default")}
            task.setdefault("initial_estimate", copy.deepcopy(task["estimate"]))
        elif name == "sync_receipt":
            if not replay:
                require(cfg["modules"]["feishu"], "FEISHU_DISABLED")
            result["sync"].update(copy.deepcopy(op["receipt"]))
        elif name == "usage":
            if not replay:
                require(number(op.get("tokens")) and op.get("source"), "INVALID_USAGE")
            result["tokens_used"] += op["tokens"]
        elif name == "rebind_template":
            if not replay:
                require(human(actor, op.get("approval")) and op.get("reason"), "TEMPLATE_REBIND_REQUIRES_HUMAN")
            cfg["template"] = copy.deepcopy(op["template"])
        elif name == "resume":
            if not replay:
                require(human(actor, op.get("approval")), "RESUME_REQUIRES_HUMAN")
            result["halted"] = False
        else:
            raise SJSError("UNKNOWN_OPERATION", operation=name)
        if task is not None:
            task["revision"] = seq
            task["updated_at"] = now
    if not replay:
        _validate_graph(result)
    result["revision"] = seq
    result["commands"][command["command_id"]] = {"seq": seq, "request_hash": command["request_hash"], "outcomes": outcomes}
    result["updated_at"] = now
    return result, outcomes
