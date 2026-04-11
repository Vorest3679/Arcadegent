"""Unit tests for context builder skill and context payload injection."""

from __future__ import annotations

from pathlib import Path

from app.agent.context.context_builder import ContextBuilder
from app.agent.runtime.session_state import AgentSessionState, AgentTurn
from app.agent.subagents.subagent_builder import SubAgentProfile
from app.protocol.messages import ChatRequest


def test_context_builder_injects_directory_and_detail_blocks(tmp_path: Path) -> None:
    prompt_root = tmp_path / "prompts"
    skill_root = tmp_path / "skills"
    prompt_root.mkdir()
    skill_root.mkdir()
    (prompt_root / "system_base.md").write_text("base prompt", encoding="utf-8")
    (prompt_root / "main_agent.md").write_text("main prompt", encoding="utf-8")
    (skill_root / "search_result_reading.md").write_text("search skill", encoding="utf-8")

    builder = ContextBuilder(
        prompt_root=prompt_root,
        skill_root=skill_root,
        history_turn_limit=6,
    )
    state = AgentSessionState(
        session_id="s1",
        active_subagent="main_agent",
        turns=[
            AgentTurn(role="user", content="find maimai"),
            AgentTurn(
                role="tool",
                name="db_query_tool",
                call_id="call_1",
                content='{"total": 2}',
            ),
        ],
    )
    state.working_memory.update(
        {
            "keyword": "maimai",
            "total": 2,
            "last_db_query": {
                "keyword": "maimai",
                "city_name": "Shanghai",
                "sort_by": "title_quantity",
                "sort_title_name": "maimai",
            },
            "shops": [
                {
                    "source_id": 1,
                    "name": "Alpha",
                    "city_name": "Shanghai",
                    "transport": "Metro line 2",
                    "comment": "Late-night crowd",
                    "arcades": [
                        {
                            "title_name": "maimai DX",
                            "quantity": 4,
                            "comment": "well maintained",
                        }
                    ],
                }
            ],
        }
    )

    context = builder.build(
        session_state=state,
        request=ChatRequest(message="find maimai"),
        subagent=SubAgentProfile(
            name="main_agent",
            prompt_file="main_agent.md",
            allowed_tools=[],
            skill_files=["search_result_reading.md"],
        ),
    )

    assert "base prompt" in context.instructions
    assert "main prompt" in context.instructions
    assert "Skill reference: search_result_reading.md" in context.instructions
    assert "search skill" in context.instructions
    assert '"context_payload"' in context.instructions
    assert '"directory"' in context.instructions
    assert '"search_catalog"' in context.instructions
    assert '"shop_details"' in context.instructions
    assert '"detail_sections": ["basic", "transport", "arcades", "comment"]' in context.instructions
    assert '"transport": {"summary": "Metro line 2"}' in context.instructions
    assert '"comment": {"summary": "Late-night crowd"}' in context.instructions
    assert '"quantity": 4' in context.instructions
    assert context.messages[1]["role"] == "tool"


def test_context_builder_injects_route_block_before_shop_details(tmp_path: Path) -> None:
    prompt_root = tmp_path / "prompts"
    skill_root = tmp_path / "skills"
    prompt_root.mkdir()
    skill_root.mkdir()
    (prompt_root / "system_base.md").write_text("base prompt", encoding="utf-8")
    (prompt_root / "main_agent.md").write_text("main prompt", encoding="utf-8")
    (skill_root / "navigation_result_reading.md").write_text("nav skill", encoding="utf-8")

    builder = ContextBuilder(
        prompt_root=prompt_root,
        skill_root=skill_root,
        history_turn_limit=6,
    )
    state = AgentSessionState(session_id="s2", active_subagent="main_agent")
    state.working_memory.update(
        {
            "shop": {"source_id": 7, "name": "Bravo", "city_name": "Shanghai"},
            "route": {
                "provider": "amap",
                "mode": "walking",
                "distance_m": 900,
                "duration_s": 720,
                "hint": "Head north.",
            },
        }
    )

    context = builder.build(
        session_state=state,
        request=ChatRequest(message="route to bravo"),
        subagent=SubAgentProfile(
            name="main_agent",
            prompt_file="main_agent.md",
            allowed_tools=[],
            skill_files=["navigation_result_reading.md"],
        ),
    )

    assert '"route"' in context.instructions
    assert '"destination_name": "Bravo"' in context.instructions
    assert '"reading_order": ["route", "search_catalog", "shop_details"]' in context.instructions


def test_context_builder_exposes_last_mcp_result_in_runtime_hint(tmp_path: Path) -> None:
    prompt_root = tmp_path / "prompts"
    skill_root = tmp_path / "skills"
    prompt_root.mkdir()
    skill_root.mkdir()
    (prompt_root / "system_base.md").write_text("base prompt", encoding="utf-8")
    (prompt_root / "navigation_worker.md").write_text("navigation prompt", encoding="utf-8")

    builder = ContextBuilder(
        prompt_root=prompt_root,
        skill_root=skill_root,
        history_turn_limit=6,
    )
    state = AgentSessionState(session_id="s3", active_subagent="navigation_worker")
    state.working_memory["last_mcp_result"] = {
        "server": "amap",
        "tool": "maps_geo",
        "data": {
            "locations": [
                {"name": "虹口足球场", "lng": 121.48, "lat": 31.27},
                {"name": "街机烈火", "lng": 121.49, "lat": 31.28},
            ]
        },
    }

    context = builder.build(
        session_state=state,
        request=ChatRequest(message="从虹口足球场去街机烈火怎么走"),
        subagent=SubAgentProfile(
            name="navigation_worker",
            prompt_file="navigation_worker.md",
            allowed_tools=[],
            skill_files=[],
        ),
    )

    assert '"last_mcp_result"' in context.instructions
    assert '"tool": "maps_geo"' in context.instructions
    assert '"name": "虹口足球场"' in context.instructions


def test_context_builder_includes_recent_tool_results_history(tmp_path: Path) -> None:
    prompt_root = tmp_path / "prompts"
    skill_root = tmp_path / "skills"
    prompt_root.mkdir()
    skill_root.mkdir()
    (prompt_root / "system_base.md").write_text("base prompt", encoding="utf-8")
    (prompt_root / "navigation_worker.md").write_text("navigation prompt", encoding="utf-8")

    builder = ContextBuilder(
        prompt_root=prompt_root,
        skill_root=skill_root,
        history_turn_limit=6,
    )
    state = AgentSessionState(
        session_id="s4",
        active_subagent="navigation_worker",
        turns=[
            AgentTurn(role="user", content="从虹口足球场去街机烈火怎么走", scope="worker", worker_run_id="wrk_1"),
            AgentTurn(
                role="tool",
                name="mcp__amap__maps_geo",
                call_id="call_geo_1",
                content='{"server":"amap","tool":"maps_geo","data":{"locations":[{"name":"虹口足球场","lng":"121.48","lat":"31.27"}]}}',
                scope="worker",
                worker_run_id="wrk_1",
                payload={
                    "status": "completed",
                    "arguments": {
                        "keywords": "虹口足球场",
                    },
                    "result": {
                        "server": "amap",
                        "tool": "maps_geo",
                        "data": {
                            "locations": [
                                {"name": "虹口足球场", "lng": "121.48", "lat": "31.27"},
                            ]
                        },
                    },
                },
            ),
            AgentTurn(
                role="tool",
                name="mcp__amap__maps_geo",
                call_id="call_geo_2",
                content='{"error":{"type":"runtime_error","message":"API 调用失败：ENGINE_RESPONSE_DATA_ERROR"}}',
                scope="worker",
                worker_run_id="wrk_1",
                payload={
                    "status": "failed",
                    "arguments": {
                        "keywords": "街机烈火",
                    },
                    "result": {
                        "error": {
                            "type": "runtime_error",
                            "message": "API 调用失败：ENGINE_RESPONSE_DATA_ERROR",
                        }
                    },
                },
            ),
        ],
    )

    context = builder.build(
        session_state=state,
        request=ChatRequest(message="从虹口足球场去街机烈火怎么走"),
        subagent=SubAgentProfile(
            name="navigation_worker",
            prompt_file="navigation_worker.md",
            allowed_tools=[],
            skill_files=[],
        ),
    )

    assert '"recent_tool_results"' in context.instructions
    assert '"call_id": "call_geo_1"' in context.instructions
    assert '"status": "completed"' in context.instructions
    assert '"status": "failed"' in context.instructions
    assert '"arguments": {"keywords": "虹口足球场"}' in context.instructions
    assert 'ENGINE_RESPONSE_DATA_ERROR' in context.instructions


def test_context_builder_main_agent_sees_worker_tool_history(tmp_path: Path) -> None:
    prompt_root = tmp_path / "prompts"
    skill_root = tmp_path / "skills"
    prompt_root.mkdir()
    skill_root.mkdir()
    (prompt_root / "system_base.md").write_text("base prompt", encoding="utf-8")
    (prompt_root / "main_agent.md").write_text("main prompt", encoding="utf-8")

    builder = ContextBuilder(
        prompt_root=prompt_root,
        skill_root=skill_root,
        history_turn_limit=6,
    )
    state = AgentSessionState(
        session_id="s5",
        active_subagent="main_agent",
        turns=[
            AgentTurn(role="user", content="帮我找从虹口足球场出发的机厅"),
            AgentTurn(
                role="tool",
                name="mcp__amap__maps_geo",
                call_id="call_geo_worker",
                content='{"server":"amap","tool":"maps_geo","data":{"locations":[{"name":"虹口足球场","lng":"121.48","lat":"31.27"}]}}',
                agent="navigation_worker",
                scope="worker",
                worker_run_id="wrk_2",
                payload={
                    "status": "completed",
                    "arguments": {
                        "keywords": "虹口足球场",
                    },
                    "result": {
                        "server": "amap",
                        "tool": "maps_geo",
                        "data": {
                            "locations": [
                                {"name": "虹口足球场", "lng": "121.48", "lat": "31.27"},
                            ]
                        },
                    },
                },
            ),
        ],
    )

    context = builder.build(
        session_state=state,
        request=ChatRequest(message="帮我找从虹口足球场出发的机厅"),
        subagent=SubAgentProfile(
            name="main_agent",
            prompt_file="main_agent.md",
            allowed_tools=[],
            skill_files=[],
        ),
    )

    assert '"recent_tool_results"' in context.instructions
    assert '"agent": "navigation_worker"' in context.instructions
    assert '"worker_run_id": "wrk_2"' in context.instructions
    assert '"arguments": {"keywords": "虹口足球场"}' in context.instructions
    assert '"name": "虹口足球场"' in context.instructions
