"""Unit tests for ReactRuntime helper behaviors."""

from __future__ import annotations

from app.agent.tools.builtin.executors import db_query as db_query_executor
from app.agent.tools.builtin.executors import summary as summary_executor
from app.agent.tools.builtin.executors import result_selection as result_selection_executor
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


def test_result_selection_commits_only_ordered_current_candidates() -> None:
    state = AgentSessionState(session_id="s_selection")
    set_working_memory_artifact(
        state.working_memory,
        "search_candidates",
        [{"source_id": 2, "name": "near"}, {"source_id": 1, "name": "far"}, {"source_id": 3, "name": "excluded"}],
        turn_index=1,
    )

    args, hydrated = result_selection_executor.prepare_arguments(
        {"selected_shop_ids": [2, 1]}, state.working_memory
    )
    output = result_selection_executor.execute(None, args)  # type: ignore[arg-type]

    assert hydrated == ["selected_shops"]
    assert output["selected_shop_ids"] == [2, 1]
    assert [shop["source_id"] for shop in output["shops"]] == [2, 1]


def test_result_selection_rejects_stale_or_duplicate_ids_and_allows_empty() -> None:
    state = AgentSessionState(session_id="s_empty_selection")
    set_working_memory_artifact(state.working_memory, "search_candidates", [{"source_id": 1}], turn_index=1)

    import pytest
    with pytest.raises(ValueError, match="current-turn candidates"):
        result_selection_executor.prepare_arguments({"selected_shop_ids": [2]}, state.working_memory)
    with pytest.raises(ValueError, match="duplicates"):
        result_selection_executor.prepare_arguments({"selected_shop_ids": [1, 1]}, state.working_memory)
    args, _ = result_selection_executor.prepare_arguments({"selected_shop_ids": []}, state.working_memory)
    assert result_selection_executor.execute(None, args)["shops"] == []  # type: ignore[arg-type]


def test_explicit_selection_overrides_candidates_and_detail_for_display() -> None:
    runtime = _runtime()
    memory: dict = {}
    set_working_memory_artifact(memory, "shops", [{"source_id": 1}, {"source_id": 2}, {"source_id": 3}], turn_index=1)
    set_working_memory_artifact(memory, "shop", {"source_id": 3}, turn_index=1)
    set_working_memory_artifact(memory, "selected_shops", [{"source_id": 2}, {"source_id": 1}], turn_index=1)

    assert [row["source_id"] for row in runtime._display_shops(memory)] == [2, 1]


def test_query_replaces_old_selection_and_selection_commits_new_cards() -> None:
    runtime = _runtime()
    state = AgentSessionState(session_id="s_new_query")
    set_working_memory_artifact(state.working_memory, "selected_shops", [{"source_id": 99}], turn_index=1)
    state.turn_index = 2
    runtime._apply_tool_memory(
        state=state,
        result=ToolExecutionResult(call_id="query", tool_name="db_query_tool", status="completed", output={
            "shops": [{"source_id": 1}, {"source_id": 2}, {"source_id": 3}], "total": 3,
        }),
    )
    assert get_working_memory_artifact(state.working_memory, "selected_shops") is None
    runtime._apply_tool_memory(
        state=state,
        result=ToolExecutionResult(call_id="select", tool_name="result_selection_tool", status="completed", output={
            "selected_shop_ids": [2, 1], "shops": [{"source_id": 2}, {"source_id": 1}],
        }),
    )
    assert [row["source_id"] for row in runtime._display_shops(state.working_memory)] == [2, 1]


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
    assert get_working_memory_artifact(worker_memory, "route") is None
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
        turn_index=parent_state.turn_index,
    )

    assert parent_state.working_memory["last_mcp_result"]["tool"] == "maps_geo"


def test_persist_worker_evidence_turns_copies_tool_payload_arguments() -> None:
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

    runtime._persist_worker_evidence_turns(
        parent_state=parent_state,
        worker_state=worker_state,
    )

    assert len(parent_state.turns) == 1
    assert parent_state.turns[0].name == "db_query_tool"
    assert parent_state.turns[0].payload["arguments"]["city_name"] == "上海"
