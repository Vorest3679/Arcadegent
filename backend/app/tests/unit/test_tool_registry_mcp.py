"""MCP discovery and dispatch behavior for the tool registry."""

from pathlib import Path

from backend.app.tests.unit._tool_registry_test_support import (
    _build_mcp_gateway,
    _build_registry,
    _run,
)


def test_tool_registry_includes_discovered_mcp_tools_when_allowed(tmp_path: Path) -> None:
    registry = _build_registry(tmp_path, mcp_tool_gateway=_build_mcp_gateway())

    definitions = _run(registry.tool_definitions(allowed_tools=["route_plan_tool", "mcp__*"]))
    names = [
        item["function"]["name"]
        for item in definitions
        if isinstance(item, dict) and isinstance(item.get("function"), dict)
    ]

    assert "route_plan_tool" in names
    assert "mcp__amap__maps_direction_walking" in names

def test_tool_registry_gettools_aggregates_builtin_and_mcp_tools(tmp_path: Path) -> None:
    registry = _build_registry(tmp_path, mcp_tool_gateway=_build_mcp_gateway())

    tools = _run(registry.gettools())

    assert "db_query_tool" in tools
    assert "result_selection_tool" in tools
    assert tools["db_query_tool"].provider == "builtin"
    assert "mcp__amap__maps_direction_walking" in tools
    assert tools["mcp__amap__maps_direction_walking"].provider == "mcp"
    assert tools["summary_tool"].metadata["prompt"].endswith("response-composition/SKILL.md")

def test_tool_registry_can_execute_discovered_mcp_tool(tmp_path: Path) -> None:
    registry = _build_registry(tmp_path, mcp_tool_gateway=_build_mcp_gateway())

    result = _run(registry.execute(
        call_id="c6",
        tool_name="mcp__amap__maps_direction_walking",
        raw_arguments={
            "origin": "116.3,39.9",
            "destination": "116.4,39.91",
        },
        allowed_tools=["mcp__*"],
    ))

    assert result.status == "completed"
    assert result.output["server"] == "amap"
    assert result.output["tool"] == "maps_direction_walking"
    assert result.output["route"]["distance_m"] == 1234
    assert result.output["route"]["duration_s"] == 678
