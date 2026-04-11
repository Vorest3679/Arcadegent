"""Unit tests for YAML-driven subagent profile overlay."""

from __future__ import annotations

from pathlib import Path

from app.agent.subagents.subagent_builder import SubAgentBuilder


def test_subagent_builder_loads_executable_fields_from_definitions() -> None:
    builder = SubAgentBuilder(
        definitions_dir=Path("backend/app/agent/nodes/definitions"),
        enable_yaml_overlay=True,
    )

    main_profile = builder.get("main_agent")
    search_profile = builder.get("search_worker")
    nav_profile = builder.get("navigation_worker")

    assert main_profile.prompt_file == "main_agent.md"
    assert main_profile.allowed_tools == [
        "invoke_worker",
        "db_query_tool",
        "geo_resolve_tool",
        "route_plan_tool",
        "summary_tool",
        "mcp__*",
    ]
    assert main_profile.skill_files == [
        "response_composition.md",
        "search_result_reading.md",
        "navigation_result_reading.md",
    ]

    assert search_profile.prompt_file == "search_worker.md"
    assert search_profile.allowed_tools == ["db_query_tool", "mcp__*"]
    assert search_profile.skill_files == ["search_result_reading.md"]

    assert nav_profile.prompt_file == "navigation_worker.md"
    assert nav_profile.allowed_tools == [
        "db_query_tool",
        "geo_resolve_tool",
        "route_plan_tool",
        "mcp__*",
    ]
    assert nav_profile.skill_files == ["search_result_reading.md", "navigation_result_reading.md"]
