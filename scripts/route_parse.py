#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""解析 `route_key：意图` 与兼容自然表达。"""

from __future__ import annotations

import re
from typing import Optional

from registry import list_instances, resolve

STANDARD = re.compile(r"^\s*(?P<key>[^:：\s][^:：]*?)\s*[:：]\s*(?P<intent>.+?)\s*$")
KNOWN_INTENTS = {
    "推进": "menu",
    "菜单": "menu",
    "下一步": "menu",
    "验收通过": "accept",
    "打回": "reject",
    "托管": "managed",
    "托管执行": "managed",
    "夜跑": "overnight",
    "挂领域": "instantiate",
    "初始化": "instantiate",
    "出估分单": "estimate",
    "审 NORMS": "norms_review",
}
HANDOFF_HTML_HDA = ("html 灌 hda", "玩法布局 html", "sync play layout", "导出 html 到 houdini")


def classify_intent(text: str) -> str:
    compact = (text or "").strip()
    lowered = compact.lower()
    if any(token in lowered for token in HANDOFF_HTML_HDA):
        return "handoff_html_hda"
    for needle, intent in KNOWN_INTENTS.items():
        if needle in compact:
            return intent
    if compact.startswith("做") or "按菜单做" in compact:
        return "run_named"
    return "unknown"


def _match_key_in_text(raw: str, instances: list) -> list:
    hits = []
    for entry in instances:
        keys = [entry.get("route_key") or ""] + list(entry.get("aliases") or [])
        for key in keys:
            if key and key in raw:
                hits.append(entry)
                break
    return hits


def parse_command(text: str, bound_route_key: Optional[str] = None, repo_root=None) -> dict:
    raw = (text or "").strip()
    intent = classify_intent(raw)
    if intent.startswith("handoff_"):
        return {
            "ok": False,
            "route_key": None,
            "intent": intent,
            "intent_text": raw,
            "diagnosis": "HANDOFF",
            "entry": None,
        }

    match = STANDARD.match(raw)
    if match:
        key = match.group("key").strip()
        intent_text = match.group("intent").strip()
        intent = classify_intent(intent_text) if classify_intent(intent_text) != "unknown" else classify_intent(raw)
        if intent.startswith("handoff_"):
            return {
                "ok": False,
                "route_key": key,
                "intent": intent,
                "intent_text": intent_text,
                "diagnosis": "HANDOFF",
                "entry": None,
            }
        entry = resolve(key, repo_root)
        return {
            "ok": bool(entry),
            "route_key": key,
            "intent": intent,
            "intent_text": intent_text,
            "diagnosis": None if entry else "ROUTE_KEY_NOT_FOUND",
            "entry": entry,
        }

    if bound_route_key:
        return {
            "ok": True,
            "route_key": bound_route_key,
            "intent": intent,
            "intent_text": raw,
            "diagnosis": None,
            "entry": resolve(bound_route_key, repo_root),
        }

    instances = [e for e in list_instances(repo_root) if e.get("enabled", True)]
    if intent != "unknown" and len(instances) == 1:
        only = instances[0]
        return {
            "ok": True,
            "route_key": only.get("route_key"),
            "intent": intent,
            "intent_text": raw,
            "diagnosis": "SINGLE_INSTANCE_FALLBACK",
            "entry": only,
        }

    keyed = _match_key_in_text(raw, instances)
    if len(keyed) == 1:
        entry = keyed[0]
        return {
            "ok": True,
            "route_key": entry.get("route_key"),
            "intent": classify_intent(raw.replace(entry.get("route_key") or "", "")),
            "intent_text": raw,
            "diagnosis": "NATURAL_KEY",
            "entry": entry,
        }
    if len(keyed) > 1:
        return {
            "ok": False,
            "route_key": None,
            "intent": intent,
            "intent_text": raw,
            "diagnosis": "ROUTE_AMBIGUOUS",
            "entry": None,
        }

    return {
        "ok": False,
        "route_key": None,
        "intent": intent,
        "intent_text": raw,
        "diagnosis": "ROUTE_AMBIGUOUS" if len(instances) > 1 else "NO_ROUTE",
        "entry": None,
    }
