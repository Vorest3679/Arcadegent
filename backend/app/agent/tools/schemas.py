"""JSON Schema helpers used by provider-backed tool registration.
提供程序支持的工具注册使用的 JSON Schema 帮助程序。"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator

from app.agent.tools.base import ToolInputValidationError

_MISSING = object()


def load_json_schema(path: Path) -> dict[str, Any]:
    """Load one tool schema/definition JSON file from disk."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"tool_schema_must_be_object:{path}")
    return payload


def build_json_schema_validator(
    schema: dict[str, Any],
    *,
    source: str,
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    """Create a validator that applies schema defaults before validating."""
    validator = Draft202012Validator(schema)

    def validate(arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(arguments, dict):
            raise ToolInputValidationError(
                message=f"{source}: tool arguments must be a JSON object",
                details=[
                    {
                        "type": "type_error",
                        "loc": [],
                        "msg": "tool arguments must be a JSON object",
                        "input": arguments,
                    }
                ],
            )
        normalized = _apply_defaults(schema=schema, value=arguments, root_schema=schema)
        errors = sorted(
            validator.iter_errors(normalized),
            key=lambda item: [str(part) for part in item.absolute_path],
        )
        if not errors:
            return normalized
        raise ToolInputValidationError(
            message=f"{source}: tool input does not satisfy declared JSON Schema",
            details=[
                {
                    "type": "jsonschema_validation_error",
                    "loc": list(error.absolute_path),
                    "msg": error.message,
                    "input": error.instance,
                    "ctx": {
                        "validator": error.validator,
                        "validator_value": error.validator_value,
                    },
                }
                for error in errors
            ],
        )

    return validate


def _apply_defaults(
    *,
    schema: dict[str, Any],
    value: Any,
    root_schema: dict[str, Any],
) -> Any:
    resolved = _resolve_schema(schema=schema, root_schema=root_schema)
    branch = _pick_branch_schema(schema=resolved, value=value, root_schema=root_schema)

    if value is _MISSING:
        if "default" in branch:
            return deepcopy(branch["default"])
        return value

    if isinstance(value, dict):
        properties = branch.get("properties", {})
        if isinstance(properties, dict):
            normalized = dict(value)
            for key, property_schema in properties.items():
                child_value = normalized.get(key, _MISSING)
                normalized_child = _apply_defaults(
                    schema=property_schema,
                    value=child_value,
                    root_schema=root_schema,
                )
                if normalized_child is _MISSING:
                    continue
                normalized[key] = normalized_child
            return normalized

    if isinstance(value, list):
        items_schema = branch.get("items")
        if isinstance(items_schema, dict):
            return [
                _apply_defaults(schema=items_schema, value=item, root_schema=root_schema)
                for item in value
            ]
    return value


def _pick_branch_schema(
    *,
    schema: dict[str, Any],
    value: Any,
    root_schema: dict[str, Any],
) -> dict[str, Any]:
    """If the schema has anyOf/oneOf branches, pick the first one that matches the value (or has a default) and return it.
    如果 schema 有 anyOf/oneOf 分支，选择第一个与值匹配（或具有默认值）的分支并返回它。"""
    for branch_key in ("anyOf", "oneOf"):
        candidates = schema.get(branch_key)
        if not isinstance(candidates, list):
            continue
        non_null_candidates = [
            _resolve_schema(candidate, root_schema=root_schema)
            for candidate in candidates
            if not (isinstance(candidate, dict) and candidate.get("type") == "null")
        ]
        if value is _MISSING:
            for candidate in non_null_candidates:
                if "default" in candidate:
                    return candidate
            return schema
        for candidate in non_null_candidates:
            if Draft202012Validator(candidate).is_valid(value):
                return candidate
        return non_null_candidates[0] if non_null_candidates else schema
    return schema


def _resolve_schema(
    schema: dict[str, Any],
    *,
    root_schema: dict[str, Any],
) -> dict[str, Any]:
    current = schema
    visited: set[str] = set()
    while isinstance(current, dict) and "$ref" in current:
        ref = str(current["$ref"])
        if ref in visited:
            raise ValueError(f"cyclic_json_schema_ref:{ref}")
        visited.add(ref)
        current = _resolve_json_pointer(root_schema, ref)
    return current if isinstance(current, dict) else schema


def _resolve_json_pointer(root_schema: dict[str, Any], ref: str) -> Any:
    """Resolve a JSON Pointer reference within the root schema, ensuring it is a local reference and does not contain cycles.
    在根模式中解析 JSON Pointer 引用，确保它是本地引用并且不包含循环。"""
    if not ref.startswith("#/"):
        raise ValueError(f"unsupported_json_schema_ref:{ref}")
    current: Any = root_schema
    for token in ref[2:].split("/"):
        key = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or key not in current:
            raise ValueError(f"unresolved_json_schema_ref:{ref}")
        current = current[key]
    return current
