#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""标准库 JSON Schema 子集校验。不引入第三方依赖。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Optional, Union


def _join(path: str, key: Union[str, int]) -> str:
    if isinstance(key, int):
        return "%s[%d]" % (path, key)
    if path == "$":
        return "$.%s" % key
    return "%s.%s" % (path, key)


def validate_instance(data: Any, schema: dict, path: str = "$") -> List[str]:
    errors: List[str] = []
    if not isinstance(schema, dict):
        return ["SCHEMA_INVALID:%s" % path]

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed = expected_type if isinstance(expected_type, list) else [expected_type]
        if not _type_ok(data, allowed):
            errors.append("TYPE_MISMATCH:%s" % path)
            return errors

    if "const" in schema and data != schema["const"]:
        errors.append("CONST_MISMATCH:%s" % path)

    if "enum" in schema and data not in schema["enum"]:
        errors.append("ENUM_MISMATCH:%s" % path)

    if isinstance(data, str) and "minLength" in schema:
        if len(data) < int(schema["minLength"]):
            errors.append("MIN_LENGTH:%s" % path)

    if isinstance(data, (int, float)) and not isinstance(data, bool):
        if "maximum" in schema and data > schema["maximum"]:
            errors.append("MAXIMUM:%s" % path)
        if "minimum" in schema and data < schema["minimum"]:
            errors.append("MINIMUM:%s" % path)

    if isinstance(data, dict):
        for field in schema.get("required") or []:
            if field not in data:
                errors.append("MISSING_FIELD:%s" % _join(path, field))
        props = schema.get("properties") or {}
        for key, value in data.items():
            if key in props:
                errors.extend(validate_instance(value, props[key], _join(path, key)))

    if isinstance(data, list):
        if "maxItems" in schema and len(data) > int(schema["maxItems"]):
            errors.append("MAX_ITEMS:%s" % path)
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(data):
                errors.extend(validate_instance(item, item_schema, _join(path, index)))

    return errors


def _type_ok(data: Any, allowed: List[str]) -> bool:
    mapping = {
        "object": dict,
        "array": list,
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "null": type(None),
    }
    for name in allowed:
        if name == "null" and data is None:
            return True
        if name == "integer" and isinstance(data, bool):
            continue
        if name == "number" and isinstance(data, bool):
            continue
        py = mapping.get(name)
        if py and isinstance(data, py):
            if name == "integer" and isinstance(data, bool):
                continue
            return True
    return False


def load_schema(name: str, schemas_dir: Optional[Path] = None) -> dict:
    root = schemas_dir or Path(__file__).resolve().parent.parent / "schemas"
    path = root / name
    return json.loads(path.read_text(encoding="utf-8"))


def validate_named(data: Any, schema_file: str, schemas_dir: Optional[Path] = None) -> List[str]:
    return validate_instance(data, load_schema(schema_file, schemas_dir))
