"""Unit tests for YAML-driven subagent profile overlay."""

from __future__ import annotations

from pathlib import Path

from app.agent.subagents.subagent_builder import SubAgentBuilder


def test_subagent_builder_loads_executable_fields_from_definitions() -> None:
    builder = SubAgentBuilder(
        definitions_dir=Path("app/agent/nodes/definitions"),
        enable_yaml_overlay=True,
    )

    intent_profile = builder.get("intent_router")
    search_profile = builder.get("search_agent")
    nav_profile = builder.get("navigation_agent")
    summary_profile = builder.get("summary_agent")

    assert intent_profile.prompt_file == "intent_router.md"
    assert intent_profile.allowed_tools == ["select_next_subagent"]
    assert intent_profile.skill_files == []

    assert search_profile.prompt_file == "search_agent.md"
    assert search_profile.allowed_tools == ["db_query_tool", "select_next_subagent"]
    assert search_profile.skill_files == ["search_result_reading.md"]

    assert nav_profile.prompt_file == "navigation_agent.md"
    assert nav_profile.allowed_tools == [
        "db_query_tool",
        "geo_resolve_tool",
        "route_plan_tool",
        "mcp__*",
        "select_next_subagent",
    ]
    assert nav_profile.skill_files == ["search_result_reading.md", "navigation_result_reading.md"]

    assert summary_profile.prompt_file == "summary_agent.md"
    assert summary_profile.allowed_tools == []
    assert summary_profile.skill_files == [
        "response_composition.md",
        "search_result_reading.md",
        "navigation_result_reading.md",
    ]
