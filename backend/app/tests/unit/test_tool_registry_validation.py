"""Validation and schema behavior for the tool registry."""

from pathlib import Path

import pytest

from app.agent.tools.builtin.route_plan_tool import RoutePlanTool
from app.agent.tools.registry import _is_strict_compatible
from app.protocol.messages import Location
from backend.app.tests.unit._tool_registry_test_support import _build_registry, _run


def test_strict_compatibility_check_requires_closed_objects() -> None:
    assert _is_strict_compatible(
        {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
            "additionalProperties": False,
        }
    )
    # Missing properties in required -> not strict compatible.
    assert not _is_strict_compatible(
        {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": [],
            "additionalProperties": False,
        }
    )
    # Open additionalProperties -> not strict compatible.
    assert not _is_strict_compatible(
        {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
        }
    )
    # Nested objects must also be closed.
    assert not _is_strict_compatible(
        {
            "type": "object",
            "properties": {
                "a": {
                    "type": "object",
                    "properties": {"b": {"type": "string"}},
                    "required": ["b"],
                }
            },
            "required": ["a"],
            "additionalProperties": False,
        }
    )

def test_tool_definitions_do_not_claim_strict_for_loose_business_schema(tmp_path: Path) -> None:
    registry = _build_registry(tmp_path)

    definitions = _run(registry.tool_definitions(allowed_tools=["db_query_tool"]))

    assert definitions
    function = definitions[0]["function"]
    # The business schema keeps its own shape; only the remote strict flag is gated.
    assert len(function["parameters"]["properties"]) > 2
    assert function["strict"] is False

def test_route_plan_unavailable_fails_without_estimate() -> None:
    import pytest
    with pytest.raises(RuntimeError, match="route_unavailable"):
        _run(RoutePlanTool().plan_route(provider="amap", mode="walking",
            origin=Location(lng=116.3, lat=39.9), destination=Location(lng=116.4, lat=39.91)))

def test_tool_registry_returns_validation_error_for_bad_args(tmp_path: Path) -> None:
    registry = _build_registry(tmp_path)
    result = _run(registry.execute(
        call_id="c1",
        tool_name="route_plan_tool",
        raw_arguments={
            "provider": "amap",
            "mode": "walking",
            "origin": {"lng": 116.3, "lat": 39.9},
            "destination": {"lng": 116.4},
        },
        allowed_tools=["route_plan_tool"],
    ))
    assert result.status == "failed"
    assert result.output["error"]["type"] == "validation_error"
