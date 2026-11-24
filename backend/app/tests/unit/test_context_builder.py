"""Unit tests for context builder skill and context payload injection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.agent.context.context_builder import ContextBuilder
from app.agent.context.prompt_loader import DirectoryPromptLoader, PromptError
from app.agent.skills.config import SkillConfig
from app.agent.skills.registry import SkillRegistry
from app.agent.runtime.session_state import AgentSessionState, AgentTurn
from app.agent.subagents.roster import AgentDefinition
from app.protocol.messages import ChatRequest


def test_context_builder_passes_through_model_transcripts(tmp_path: Path) -> None:
    prompt_root = tmp_path / "prompts"
    prompt_root.mkdir()
    (prompt_root / "system_base.md").write_text("base prompt", encoding="utf-8")
    (prompt_root / "main_agent.md").write_text("main prompt", encoding="utf-8")

    builder = ContextBuilder(prompt_loader=DirectoryPromptLoader([prompt_root], base_prompt="system_base.md"), skill_registry=None, history_turn_limit=10)
    state = AgentSessionState(
        session_id="s_transcript",
        active_subagent="main_agent",
        turns=[
            AgentTurn(role="user", content="find maimai"),
            AgentTurn(
                role="assistant",
                content="",
                agent="main_agent",
                payload={
                    "model": {
                        "transcript": {
                            "chat_message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {"name": "db_query_tool", "arguments": "{}"},
                                    }
                                ],
                            },
                            "responses_output": [
                                {
                                    "type": "function_call",
                                    "call_id": "call_1",
                                    "name": "db_query_tool",
                                    "arguments": "{}",
                                }
                            ],
                        }
                    }
                },
            ),
            AgentTurn(
                role="tool",
                name="db_query_tool",
                call_id="call_1",
                content='{"total": 0}',
            ),
        ],
    )

    context = builder.build(
        session_state=state,
        request=ChatRequest(message="find maimai"),
        subagent=AgentDefinition(
            name="main_agent",
            prompt="main_agent.md",
            allowed_tools=[],
            role="entry",
        ),
    )

    assistant_message = context.messages[1]
    assert assistant_message["chat_message"]["tool_calls"][0]["id"] == "call_1"
    assert assistant_message["responses_output"][0]["call_id"] == "call_1"
    assert context.messages[2] == {
        "role": "tool",
        "content": '{"total": 0}',
        "name": "db_query_tool",
        "tool_call_id": "call_1",
    }


def test_context_builder_injects_directory_and_detail_blocks(tmp_path: Path) -> None:
    prompt_root = tmp_path / "prompts"
    skill_root = tmp_path / "skills"
    prompt_root.mkdir()
    skill_root.mkdir()
    (prompt_root / "system_base.md").write_text("base prompt", encoding="utf-8")
    (prompt_root / "main_agent.md").write_text("main prompt", encoding="utf-8")
    (skill_root / "search-result-reading").mkdir()
    (skill_root / "search-result-reading" / "SKILL.md").write_text(
        "---\nname: search-result-reading\ndescription: Interpret search results.\n---\nsearch skill",
        encoding="utf-8",
    )

    builder = ContextBuilder(
        prompt_loader=DirectoryPromptLoader([prompt_root], base_prompt="system_base.md"),
        skill_registry=SkillRegistry(SkillConfig(roots=[skill_root])),
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
        subagent=AgentDefinition(
            name="main_agent",
            prompt="main_agent.md",
            allowed_tools=[],
            role="entry",
        ),
    )

    assert "base prompt" in context.instructions
    assert "main prompt" in context.instructions
    assert '"name": "search-result-reading"' in context.instructions
    assert "Interpret search results." in context.instructions
    assert "search skill" not in context.instructions
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
    (skill_root / "navigation-result-reading").mkdir()
    (skill_root / "navigation-result-reading" / "SKILL.md").write_text(
        "---\nname: navigation-result-reading\ndescription: Interpret routes.\n---\nnav skill",
        encoding="utf-8",
    )

    builder = ContextBuilder(
        prompt_loader=DirectoryPromptLoader([prompt_root], base_prompt="system_base.md"),
        skill_registry=SkillRegistry(SkillConfig(roots=[skill_root])),
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
        subagent=AgentDefinition(
            name="main_agent",
            prompt="main_agent.md",
            allowed_tools=[],
            role="entry",
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
        prompt_loader=DirectoryPromptLoader([prompt_root], base_prompt="system_base.md"),
        skill_registry=SkillRegistry(SkillConfig(roots=[skill_root])),
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
        subagent=AgentDefinition(
            name="navigation_worker",
            prompt="navigation_worker.md",
            allowed_tools=[],
        ),
    )

    assert '"last_mcp_result"' in context.instructions
    assert '"tool": "maps_geo"' in context.instructions
    assert '"name": "虹口足球场"' in context.instructions


def test_recent_tool_results_only_carry_results_outside_the_message_window(tmp_path: Path) -> None:
    prompt_root = tmp_path / "prompts"
    skill_root = tmp_path / "skills"
    prompt_root.mkdir()
    skill_root.mkdir()
    (prompt_root / "system_base.md").write_text("base prompt", encoding="utf-8")
    (prompt_root / "navigation_worker.md").write_text("navigation prompt", encoding="utf-8")

    builder = ContextBuilder(
        prompt_loader=DirectoryPromptLoader([prompt_root], base_prompt="system_base.md"),
        skill_registry=SkillRegistry(SkillConfig(roots=[skill_root])),
        history_turn_limit=2,
    )
    state = AgentSessionState(
        session_id="s4",
        active_subagent="navigation_worker",
        turns=[
            AgentTurn(role="user", content="先定位起点", scope="worker", worker_run_id="wrk_1"),
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
            AgentTurn(role="user", content="再定位终点", scope="worker", worker_run_id="wrk_1"),
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
            AgentTurn(role="user", content="换一条路线", scope="worker", worker_run_id="wrk_1"),
            AgentTurn(
                role="tool",
                name="mcp__amap__maps_geo",
                call_id="call_geo_3",
                content='{"locations": []}',
                scope="worker",
                worker_run_id="wrk_1",
                payload={"status": "completed", "arguments": {"keywords": "新终点"}},
            ),
        ],
    )

    context = builder.build(
        session_state=state,
        request=ChatRequest(message="从虹口足球场去街机烈火怎么走"),
        subagent=AgentDefinition(
            name="navigation_worker",
            prompt="navigation_worker.md",
            allowed_tools=[],
        ),
    )

    assert '"recent_tool_results"' in context.instructions
    # The old result fell out of the message window and stays in the runtime state.
    assert '"call_id": "call_geo_1"' in context.instructions
    assert '"arguments": {"keywords": "虹口足球场"}' in context.instructions
    # The fresh result is a native tool message already; runtime state does not repeat it.
    assert '"call_id": "call_geo_2"' not in context.instructions
    assert [message.get("tool_call_id") for message in context.messages if message["role"] == "tool"] == [
        "call_geo_2",
        "call_geo_3",
    ]
    assert 'ENGINE_RESPONSE_DATA_ERROR' in "".join(
        message.get("content") or "" for message in context.messages
    )


def test_entry_agent_sees_worker_results_only_through_its_envelope(tmp_path: Path) -> None:
    """A worker's inner tool calls stay in worker scope; the entry agent reads its envelope."""
    prompt_root = tmp_path / "prompts"
    skill_root = tmp_path / "skills"
    prompt_root.mkdir()
    skill_root.mkdir()
    (prompt_root / "system_base.md").write_text("base prompt", encoding="utf-8")
    (prompt_root / "main_agent.md").write_text("main prompt", encoding="utf-8")

    builder = ContextBuilder(
        prompt_loader=DirectoryPromptLoader([prompt_root], base_prompt="system_base.md"),
        skill_registry=SkillRegistry(SkillConfig(roots=[skill_root])),
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
            AgentTurn(
                role="tool",
                name="invoke_worker",
                call_id="call_worker",
                content='{"worker": "navigation_worker", "status": "completed", "summary": "已定位虹口足球场"}',
                agent="main_agent",
                payload={
                    "status": "completed",
                    "result": {"worker": "navigation_worker", "status": "completed", "summary": "已定位虹口足球场"},
                },
            ),
        ],
    )

    context = builder.build(
        session_state=state,
        request=ChatRequest(message="帮我找从虹口足球场出发的机厅"),
        subagent=AgentDefinition(
            name="main_agent",
            prompt="main_agent.md",
            allowed_tools=[],
            role="entry",
        ),
    )

    tool_messages = [message for message in context.messages if message["role"] == "tool"]
    assert [message.get("tool_call_id") for message in tool_messages] == ["call_worker"]
    assert '"summary": "已定位虹口足球场"' in tool_messages[0]["content"]
    # Worker internals stay out of the entry agent's runtime state.
    assert "call_geo_worker" not in context.instructions
    assert '"worker_run_id": "wrk_2"' not in context.instructions


def test_tool_results_are_capped_in_model_input(tmp_path: Path) -> None:
    prompt_root = tmp_path / "prompts"
    prompt_root.mkdir()
    (prompt_root / "main_agent.md").write_text("main prompt", encoding="utf-8")
    builder = ContextBuilder(prompt_loader=DirectoryPromptLoader([prompt_root]), history_turn_limit=6)
    huge = {"data": ["x" * 100] * 400}
    state = AgentSessionState(
        session_id="s6",
        turns=[
            AgentTurn(role="user", content="q"),
            AgentTurn(
                role="tool",
                name="mcp__fixture__big",
                call_id="call_big",
                content=json.dumps(huge),
                payload={"status": "completed", "result": huge},
            ),
        ],
        working_memory={"last_mcp_result": huge},
    )

    context = builder.build(
        session_state=state,
        request=ChatRequest(message="q"),
        subagent=AgentDefinition(name="main_agent", prompt="main_agent.md", allowed_tools=(), role="entry"),
    )

    tool_message = next(message for message in context.messages if message["role"] == "tool")
    # Head + marker + tail: the closing part survives so a cut JSON still ends visibly.
    assert 4096 + 1024 <= len(tool_message["content"]) < 8192
    assert "omitted" in tool_message["content"]
    assert len(context.instructions) < 4 * 8192
    assert '"truncated": true' in context.instructions


def test_prompt_loader_searches_plugin_directories_in_order(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (second / "main_agent.md").write_text("second", encoding="utf-8")
    (first / "system_base.md").write_text("base", encoding="utf-8")
    loader = DirectoryPromptLoader([first, second], base_prompt="system_base.md")

    assert loader.base_prompt() == "base"
    assert loader.load("main_agent.md") == "second"
    for name in ("missing.md", "../outside.md", str(second / "main_agent.md")):
        with pytest.raises(PromptError, match=r"^prompt_not_found$"):
            loader.load(name)
