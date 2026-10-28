"""Stage 6 acceptance: artifact display, intent and detail consistency.

These tests exercise the diagnosis-A cases end to end: an explicit empty
selection stays empty, a new search replaces old cards/routes, a plain search
does not inherit a previous navigate label, and the session detail agrees with
the immediate response.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from app.agent.artifacts.model import Artifact
from app.agent.artifacts.sink import ArtifactSink
from app.agent.llm.provider_adapter import ModelResponse, ModelToolCall
from app.agent.runtime.session_state import AgentSessionState
from backend.app.tests.integration._api_test_support import (
    InMemorySessionStateRepository,
    _build_client_with_rows,
)


def _rows() -> list[dict[str, object]]:
    base = {
        "source": "bemanicn",
        "address": "Test Address",
        "province_code": "110000000000",
        "province_name": "Beijing",
        "city_code": "110100000000",
        "city_name": "Beijing",
        "county_code": "110101000000",
        "county_name": "Dongcheng",
        "updated_at": "2026-02-20T00:00:00Z",
        "arcades": [{"title_name": "CHUNITHM", "quantity": 2}],
    }
    return [
        {
            **base,
            "source_id": 10,
            "source_url": "https://map.bemanicn.com/s/10",
            "name": "Gamma Arcade",
            "longitude_wgs84": 116.397428,
            "latitude_wgs84": 39.90923,
        },
        {
            **base,
            "source_id": 11,
            "source_url": "https://map.bemanicn.com/s/11",
            "name": "Beta Arcade",
            "longitude_wgs84": 116.407428,
            "latitude_wgs84": 39.91923,
        },
    ]


def _install(client: TestClient, responses: list[ModelResponse]) -> None:
    script = iter(responses)

    async def fake_complete(**_kwargs: Any) -> ModelResponse:
        return next(script)

    client.app.state.container.react_runtime._provider_adapter.complete = fake_complete  # type: ignore[method-assign]


def _post(client: TestClient, message: str, **extra: Any) -> dict[str, Any]:
    response = client.post("/api/chat", json={"client_id": "test-client", "message": message, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def _ids(rows: list[dict[str, Any]]) -> list[int]:
    return [row["source_id"] for row in rows]


def _last_assistant_turn(detail: dict[str, Any]) -> dict[str, Any]:
    return next(turn for turn in reversed(detail["turns"]) if turn["role"] == "assistant")


def _query(keyword: str) -> ModelResponse:
    return ModelResponse(tool_calls=[ModelToolCall(f"q_{keyword}", "db_query_tool", {"keyword": keyword, "page": 1, "page_size": 5})])


def test_explicit_empty_selection_stays_empty_in_response_and_detail(tmp_path) -> None:
    client = _build_client_with_rows(tmp_path, _rows())
    _install(client, [
        _query("Gamma"),
        ModelResponse(tool_calls=[ModelToolCall("sel", "result_selection_tool", {"selected_shop_ids": []})]),
        ModelResponse(text="没有符合的机厅。"),
    ])

    response = _post(client, "find Gamma", page_size=5)
    detail = client.get(f"/api/chat/sessions/{response['session_id']}").json()

    assert response["shops"] == []
    assert detail["shops"] == []
    state = client.app.state.container.session_store.get_session(response["session_id"])
    final = next(turn for turn in state.turns if turn.payload.get("final"))
    assert final.payload["map_artifacts"]["shops"] == []


def test_ordered_selection_matches_between_response_and_session_detail(tmp_path) -> None:
    client = _build_client_with_rows(tmp_path, _rows())
    _install(client, [
        _query("Arcade"),
        ModelResponse(tool_calls=[ModelToolCall("sel", "result_selection_tool", {"selected_shop_ids": [11]})]),
        ModelResponse(text="先看 Beta。"),
    ])

    response = _post(client, "find Arcade", page_size=5)
    detail = client.get(f"/api/chat/sessions/{response['session_id']}").json()

    assert _ids(response["shops"]) == [11]
    assert _ids(detail["shops"]) == [11]
    assert _ids(_last_assistant_turn(detail)["map_artifacts"]["shops"]) == [11]


def test_new_search_replaces_old_selection_and_cards(tmp_path, chat_clock) -> None:
    client = _build_client_with_rows(tmp_path, _rows(), chat_clock=chat_clock)
    _install(client, [
        _query("Gamma"),
        ModelResponse(tool_calls=[ModelToolCall("sel", "result_selection_tool", {"selected_shop_ids": [10]})]),
        ModelResponse(text="找到 Gamma。"),
        _query("Beta"),
        ModelResponse(text="找到 Beta。"),
    ])

    first = _post(client, "find Gamma", page_size=5)
    chat_clock.advance()
    second = _post(client, "find Beta", page_size=5, session_id=first["session_id"])
    detail = client.get(f"/api/chat/sessions/{first['session_id']}").json()

    assert _ids(first["shops"]) == [10]
    assert _ids(second["shops"]) == [11]
    assert _ids(detail["shops"]) == [11]
    assert _ids(_last_assistant_turn(detail)["map_artifacts"]["shops"]) == [11]
    state = client.app.state.container.session_store.get_session(first["session_id"])
    assert "selected_shops" not in state.working_memory["artifacts"]


def test_new_search_drops_previous_turn_route_and_shop_detail(tmp_path) -> None:
    store = InMemorySessionStateRepository()
    seeded = AgentSessionState(session_id="s_previous", client_id="test-client", turn_index=1, status="completed")
    ArtifactSink().apply(
        seeded,
        [
            Artifact("shops", [{"source_id": 10, "name": "Gamma Arcade"}], display=True, scope="session"),
            Artifact("selected_shops", [{"source_id": 10, "name": "Gamma Arcade"}], display=True, scope="session"),
            Artifact("shop", {"source_id": 10, "name": "Gamma Arcade"}, display=True),
            Artifact("route", {"distance_m": 1200}, display=True),
            Artifact("destination", {"source_id": 10, "name": "Gamma Arcade"}, display=True),
        ],
    )
    store.seed(seeded)
    client = _build_client_with_rows(tmp_path, _rows(), session_store=store)
    _install(client, [_query("Beta"), ModelResponse(text="找到 Beta。")])

    response = _post(client, "find Beta", page_size=5, session_id="s_previous")
    detail = client.get("/api/chat/sessions/s_previous").json()

    assert _ids(response["shops"]) == [11]
    assert response["route"] is None
    assert _ids(detail["shops"]) == [11]
    assert detail["route"] is None
    assistant = _last_assistant_turn(detail)
    assert _ids(assistant["map_artifacts"]["shops"]) == [11]
    assert assistant["map_artifacts"]["route"] is None


def test_plain_search_does_not_inherit_previous_navigate(tmp_path, chat_clock) -> None:
    client = _build_client_with_rows(tmp_path, _rows(), chat_clock=chat_clock)
    _install(client, [
        ModelResponse(text="好的，请告诉我起点。"),
        _query("Gamma"),
        ModelResponse(text="找到 Gamma。"),
    ])

    first = _post(client, "导航到 Gamma Arcade")
    assert first["intent"] == "navigate"
    chat_clock.advance()
    second = _post(client, "find Gamma", page_size=5, session_id=first["session_id"])

    assert second["intent"] == "search"
    state = client.app.state.container.session_store.get_session(first["session_id"])
    assert state.intent == "search"


def test_navigation_clarification_keeps_navigate_intent(tmp_path, chat_clock) -> None:
    client = _build_client_with_rows(tmp_path, _rows(), chat_clock=chat_clock)
    main_steps = iter([
        ModelResponse(tool_calls=[ModelToolCall(
            "w1", "invoke_worker", {"worker": "navigation_worker", "task": "从起点导航到 Gamma Arcade"},
        )]),
        ModelResponse(text="请告诉我你的起点。"),
        ModelResponse(text="好的，请继续。"),
    ])
    worker_steps = iter([ModelResponse(text="需要起点才能规划路线。")])

    async def fake_complete(**kwargs: Any) -> ModelResponse:
        active = kwargs["runtime_hints"]["active_subagent"]
        return next(main_steps) if active == "main_agent" else next(worker_steps)

    client.app.state.container.react_runtime._provider_adapter.complete = fake_complete  # type: ignore[method-assign]

    first = _post(client, "导航到 Gamma Arcade")
    assert first["intent"] == "navigate"
    chat_clock.advance()
    second = _post(client, "我在人民广场", session_id=first["session_id"])

    assert second["intent"] == "navigate"
    state = client.app.state.container.session_store.get_session(first["session_id"])
    assert state.intent == "navigate"


def test_intent_is_derived_per_turn_and_finalized_from_current_route() -> None:
    from app.domain.arcade.intent import IntentPolicy
    from app.protocol.messages import ChatRequest

    policy = IntentPolicy()
    state = AgentSessionState(session_id="s_intent", turn_index=1)
    policy.classify(state, ChatRequest(message="导航到 Gamma Arcade"))
    assert state.intent == "navigate"

    # No route yet: a plain reply is a clarification and keeps the navigation.
    policy.classify(state, ChatRequest(message="继续"))
    assert state.intent == "navigate"
    policy.classify(state, ChatRequest(message="find Gamma"))
    assert state.intent == "search"

    # A completed route ends the pending navigation; a plain reply is search.
    state.intent = "navigate"
    ArtifactSink().apply(state, [Artifact("route", {"distance_m": 10}, display=True)])
    policy.classify(state, ChatRequest(message="继续"))
    assert state.intent == "search"

    state.turn_index = 2
    ArtifactSink().apply(state, [Artifact("route", {"distance_m": 20}, display=True)])
    policy.classify(state, ChatRequest(message="继续"))
    assert state.intent == "search"
    policy.finalize(state)
    assert state.intent == "navigate"


def test_worker_search_without_selection_clears_previous_selection(tmp_path, chat_clock) -> None:
    client = _build_client_with_rows(tmp_path, _rows(), chat_clock=chat_clock)
    main_steps = iter([
        _query("Gamma"),
        ModelResponse(tool_calls=[ModelToolCall("sel", "result_selection_tool", {"selected_shop_ids": [10]})]),
        ModelResponse(text="找到 Gamma。"),
        ModelResponse(tool_calls=[ModelToolCall(
            "w1", "invoke_worker", {"worker": "search_worker", "task": "查找 Beta 机厅"},
        )]),
        ModelResponse(text="worker found Beta."),
    ])
    worker_steps = iter([_query("Beta"), ModelResponse(text="found Beta")])

    async def fake_complete(**kwargs: Any) -> ModelResponse:
        active = kwargs["runtime_hints"]["active_subagent"]
        return next(main_steps) if active == "main_agent" else next(worker_steps)

    client.app.state.container.react_runtime._provider_adapter.complete = fake_complete  # type: ignore[method-assign]

    first = _post(client, "find Gamma", page_size=5)
    assert _ids(first["shops"]) == [10]
    chat_clock.advance()
    second = _post(client, "find Beta", page_size=5, session_id=first["session_id"])

    assert _ids(second["shops"]) == [11]
    state = client.app.state.container.session_store.get_session(first["session_id"])
    assert "selected_shops" not in state.working_memory["artifacts"]


def test_container_injects_one_artifact_sink(tmp_path) -> None:
    client = _build_client_with_rows(tmp_path, _rows())
    container = client.app.state.container

    assert container.react_runtime._artifacts is container.react_runtime._domain.artifact_sink
