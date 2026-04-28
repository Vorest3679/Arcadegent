"""Unit tests for ReactRuntime helper behaviors."""

from __future__ import annotations

from app.agent.tools.builtin.executors import db_query as db_query_executor
from app.agent.tools.builtin.executors import summary as summary_executor
from app.agent.runtime.react_runtime import ReactRuntime, _chunk_stream_text
from app.agent.runtime.session_state import (
    AgentTurn,
    AgentSessionState,
    get_working_memory_artifact,
    set_working_memory_artifact,
)
from app.agent.tools.registry import ToolExecutionResult


def _runtime() -> ReactRuntime:
    return object.__new__(ReactRuntime)


def test_prepare_tool_arguments_hydrates_search_summary_from_memory() -> None:
    state = AgentSessionState(session_id="s1")
    set_working_memory_artifact(state.working_memory, "total", 5)
    set_working_memory_artifact(state.working_memory, "shops", [{"name": "A"}, {"name": "B"}])
    state.working_memory["keyword"] = "shanghai huangpu"

    args, hydrated = summary_executor.prepare_arguments({"topic": "search"}, state.working_memory)

    assert args["topic"] == "search"
    assert args["total"] == 5
    assert isinstance(args["shops"], list)
    assert args["keyword"] == "shanghai huangpu"
    assert hydrated == ["total", "shops", "keyword"]


def test_prepare_tool_arguments_hydrates_navigation_summary_from_memory() -> None:
    state = AgentSessionState(session_id="s2")
    set_working_memory_artifact(state.working_memory, "route", {"provider": "amap", "mode": "walking"})
    set_working_memory_artifact(state.working_memory, "shops", [{"name": "Foo Arcade"}])

    args, hydrated = summary_executor.prepare_arguments({"topic": "navigation"}, state.working_memory)

    assert args["topic"] == "navigation"
    assert isinstance(args["route"], dict)
    assert args["shop_name"] == "Foo Arcade"
    assert hydrated == ["route", "shop_name"]


def test_db_query_argument_preparer_keeps_regular_search_unchanged() -> None:
    state = AgentSessionState(session_id="s3")

    args, hydrated = db_query_executor.prepare_arguments({"page": 1}, state.working_memory)

    assert args == {"page": 1}
    assert hydrated == []


def test_prepare_tool_arguments_hydrates_nearby_db_query_from_client_location() -> None:
    state = AgentSessionState(session_id="s_nearby")
    state.working_memory["last_request"] = {"message": "附近最近的机厅", "page_size": 5}
    set_working_memory_artifact(
        state.working_memory,
        "client_location",
        {"lng": 116.397428, "lat": 39.90923, "accuracy_m": 20},
    )

    args, hydrated = db_query_executor.prepare_arguments({"page": 1, "page_size": 5}, state.working_memory)

    assert args["sort_by"] == "distance"
    assert args["sort_order"] == "asc"
    assert args["origin_lng"] == 116.397428
    assert args["origin_lat"] == 39.90923
    assert args["origin_coord_system"] == "wgs84"
    assert hydrated == ["sort_by", "sort_order", "origin_lng", "origin_lat", "origin_coord_system"]


def test_prepare_tool_arguments_hydrates_nearby_db_query_from_mcp_location() -> None:
    state = AgentSessionState(session_id="s_nearby_mcp")
    state.working_memory["last_request"] = {"message": "鲁迅公园附近的机厅", "page_size": 10}
    set_working_memory_artifact(
        state.working_memory,
        "resolved_locations",
        [{"name": "鲁迅公园", "location": "121.48819,31.27687"}],
    )

    args, hydrated = db_query_executor.prepare_arguments({"page": 1, "page_size": 10}, state.working_memory)

    assert args["sort_by"] == "distance"
    assert args["sort_order"] == "asc"
    assert args["origin_lng"] == 121.48819
    assert args["origin_lat"] == 31.27687
    assert args["origin_coord_system"] == "gcj02"
    assert hydrated == ["sort_by", "sort_order", "origin_lng", "origin_lat", "origin_coord_system"]


def test_prepare_tool_arguments_hydrates_sort_fields_from_last_db_query() -> None:
    state = AgentSessionState(session_id="s4")
    set_working_memory_artifact(state.working_memory, "total", 8)
    set_working_memory_artifact(state.working_memory, "shops", [{"name": "A"}])
    state.working_memory["keyword"] = "maimai"
    state.working_memory["last_db_query"] = {
        "sort_by": "title_quantity",
        "sort_order": "desc",
        "sort_title_name": "maimai",
    }

    args, hydrated = summary_executor.prepare_arguments({"topic": "search"}, state.working_memory)

    assert args["sort_by"] == "title_quantity"
    assert args["sort_order"] == "desc"
    assert args["sort_title_name"] == "maimai"
    assert "sort_by" in hydrated
    assert "sort_order" in hydrated
    assert "sort_title_name" in hydrated


def test_prepare_tool_arguments_overrides_default_sort_with_title_quantity_context() -> None:
    state = AgentSessionState(session_id="s5")
    set_working_memory_artifact(state.working_memory, "total", 6)
    set_working_memory_artifact(state.working_memory, "shops", [{"name": "A"}])
    state.working_memory["last_db_query"] = {
        "sort_by": "title_quantity",
        "sort_order": "desc",
        "sort_title_name": "maimai",
    }

    args, hydrated = summary_executor.prepare_arguments({"topic": "search", "sort_by": "default"}, state.working_memory)

    assert args["sort_by"] == "title_quantity"
    assert args["sort_order"] == "desc"
    assert args["sort_title_name"] == "maimai"
    assert "sort_by" in hydrated


def test_chunk_stream_text_keeps_order_and_sentence_boundary() -> None:
    text = "First sentence. Second sentence is a little longer and should be chunked!"

    chunks = _chunk_stream_text(text, max_chars=8)

    assert "".join(chunks) == text
    assert any(item.endswith(".") for item in chunks)
    assert any(item.endswith("!") for item in chunks)


def test_build_worker_memory_snapshot_copies_promotable_artifacts() -> None:
    runtime = _runtime()
    state = AgentSessionState(session_id="s_snapshot")
    set_working_memory_artifact(state.working_memory, "shops", [{"name": "Alpha"}])
    set_working_memory_artifact(state.working_memory, "route", {"provider": "amap", "mode": "walking"})
    state.working_memory["keyword"] = "maimai"
    state.working_memory["last_db_query"] = {"keyword": "maimai"}

    worker_memory = runtime._build_worker_memory_snapshot(state.working_memory)

    assert get_working_memory_artifact(worker_memory, "shops")[0]["name"] == "Alpha"
    assert get_working_memory_artifact(worker_memory, "route")["mode"] == "walking"
    assert worker_memory["keyword"] == "maimai"
    assert worker_memory["last_db_query"]["keyword"] == "maimai"


def test_prepare_turn_memory_clears_stale_reply() -> None:
    runtime = _runtime()
    memory = {
        "reply": "old reply",
        "assistant_token_emitted": True,
    }

    prepared = runtime._prepare_turn_memory(memory)

    assert "reply" not in prepared
    assert prepared["assistant_token_emitted"] is False


def test_apply_tool_memory_keeps_mcp_resolved_locations() -> None:
    runtime = _runtime()
    state = AgentSessionState(session_id="s_mcp_geo")

    runtime._apply_tool_memory(
        state=state,
        result=ToolExecutionResult(
            call_id="call_geo",
            tool_name="mcp__amap__maps_geo",
            status="completed",
            output={
                "server": "amap",
                "tool": "maps_geo",
                "data": {
                    "locations": [
                        {"name": "鲁迅公园", "lng": 121.48819, "lat": 31.27687},
                    ]
                },
            },
        ),
    )

    assert get_working_memory_artifact(state.working_memory, "resolved_locations")[0]["name"] == "鲁迅公园"
    assert state.working_memory["last_mcp_result"]["tool"] == "maps_geo"


def test_promote_worker_artifacts_keeps_last_mcp_result() -> None:
    runtime = _runtime()
    parent_state = AgentSessionState(session_id="s_parent")
    worker_state = AgentSessionState(session_id="s_worker")
    worker_state.working_memory["last_mcp_result"] = {
        "server": "amap",
        "tool": "maps_geo",
        "data": {
            "locations": [
                {"name": "虹口足球场", "lng": 121.48, "lat": 31.27},
            ]
        },
    }

    runtime._promote_worker_artifacts(
        parent_memory=parent_state.working_memory,
        worker_memory=worker_state.working_memory,
    )

    assert parent_state.working_memory["last_mcp_result"]["tool"] == "maps_geo"


def test_persist_worker_tool_turns_copies_tool_payload_arguments() -> None:
    runtime = _runtime()
    parent_state = AgentSessionState(session_id="s_parent")
    worker_state = AgentSessionState(
        session_id="s_worker",
        turns=[
            AgentTurn(
                role="tool",
                content='{"ok":true}',
                agent="search_worker",
                name="db_query_tool",
                call_id="call_1",
                worker_run_id="wrk_1",
                scope="worker",
                payload={
                    "status": "completed",
                    "arguments": {"city_name": "上海"},
                    "result": {"ok": True},
                },
            )
        ],
    )

    runtime._persist_worker_tool_turns(
        parent_state=parent_state,
        worker_state=worker_state,
    )

    assert len(parent_state.turns) == 1
    assert parent_state.turns[0].name == "db_query_tool"
    assert parent_state.turns[0].payload["arguments"]["city_name"] == "上海"
