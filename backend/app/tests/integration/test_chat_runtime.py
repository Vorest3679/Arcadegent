from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.agent.llm.provider_adapter import ModelResponse
from app.agent.runtime.session_state import AgentSessionState, state_from_dict, state_to_dict
from app.infra.db.protocols import SessionStateRepository
from backend.app.tests.integration._api_test_support import (
    InMemorySessionStateRepository,
    _build_client,
    _build_client_with_rows,
    _stub_provider_adapter,
    _wait_for_session_status,
)


def test_skill_discovery_loading_business_tools_and_execution_isolation(tmp_path, monkeypatch):
    """Exercise real tool dispatch/context/session boundaries with a deterministic model."""
    import app.core.container as container_module
    from app.agent.llm.provider_adapter import ModelToolCall
    from app.agent.skills.config import SkillConfig

    root = tmp_path / "skills"
    root.mkdir()
    monkeypatch.setattr(container_module, "load_skill_config", lambda _: SkillConfig(roots=[root]))
    client = _build_client(tmp_path)
    runtime = client.app.state.container.react_runtime
    main_calls = 0
    worker_calls = 0

    def create_skill(name, body):
        folder = root / name
        folder.mkdir(exist_ok=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: Use for synthetic integration tasks.\n---\n{body}",
            encoding="utf-8",
        )
        return folder

    def call(name, args):
        return ModelResponse(tool_calls=[ModelToolCall(f"{name}-{main_calls}-{worker_calls}", name, args)])

    async def fake_complete(*, instructions, messages, tools, runtime_hints=None):
        nonlocal main_calls, worker_calls
        assert "PARENT_BODY_MARKER" not in json.dumps(messages)
        assert "WORKER_BODY_MARKER" not in json.dumps(messages)
        if runtime_hints["active_subagent"] == "search_worker":
            worker_calls += 1
            assert "PARENT_BODY_MARKER" not in instructions
            assert "REFERENCE_MARKER" not in instructions
            assert '"name": "worker-skill"' in instructions
            if worker_calls % 2:
                assert "WORKER_BODY_MARKER" not in instructions
                return call("read_skill", {"name": "worker-skill"})
            assert instructions.count("WORKER_BODY_MARKER") == 1
            return ModelResponse(text="worker finished")

        main_calls += 1
        assert "WORKER_BODY_MARKER" not in instructions
        if main_calls == 1:
            assert '"name": "parent-skill"' not in instructions
            folder = create_skill("parent-skill", "PARENT_BODY_MARKER. Read references/guide.md.")
            (folder / "references").mkdir()
            (folder / "references" / "guide.md").write_text("REFERENCE_MARKER")
            return call("list_skills", {})
        if main_calls == 2:
            assert '"name": "parent-skill"' in instructions
            assert "PARENT_BODY_MARKER" not in instructions
            return call("read_skill", {"name": "parent-skill", "path": "SKILL.md"})
        if main_calls == 3:
            assert instructions.count("PARENT_BODY_MARKER") == 1
            create_skill("parent-skill", "NEW_BODY_FOR_NEXT_TURN")
            return ModelResponse(tool_calls=[
                ModelToolCall("reference", "read_skill", {"name": "parent-skill", "path": "references/guide.md"}),
                ModelToolCall("duplicate", "read_skill", {"name": "parent-skill"}),
            ])
        if 4 <= main_calls <= 7:
            assert instructions.count("PARENT_BODY_MARKER") == 1
            assert instructions.count("REFERENCE_MARKER") == 1
            assert "NEW_BODY_FOR_NEXT_TURN" not in instructions
        if main_calls == 4:
            return call("db_query_tool", {"shop_name": "Gamma Arcade", "page": 1, "page_size": 5})
        if main_calls == 5:
            assert "Gamma Arcade" in instructions
            create_skill("worker-skill", "WORKER_BODY_MARKER")
            return call("invoke_worker", {"worker": "search_worker", "task": "Inspect the available skill."})
        if main_calls == 6:
            return call("invoke_worker", {"worker": "search_worker", "task": "Inspect it in a fresh execution."})
        if main_calls == 7:
            return ModelResponse(text="已找到 Gamma Arcade。")
        assert main_calls == 8
        assert "PARENT_BODY_MARKER" not in instructions and "REFERENCE_MARKER" not in instructions
        assert "NEW_BODY_FOR_NEXT_TURN" not in instructions
        return ModelResponse(text="新一轮尚未加载技能。")

    runtime._provider_adapter.complete = fake_complete
    response = client.post("/api/chat", json={"message": "find Gamma"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["reply"] == "已找到 Gamma Arcade。"
    assert worker_calls == 4
    state = client.app.state.container.session_store.get_session(payload["session_id"])
    query = next(turn for turn in state.turns if turn.name == "db_query_tool")
    assert query.payload["status"] == "completed"
    assert query.payload["result"]["total"] == 1
    for turn in state.turns:
        assert "PARENT_BODY_MARKER" not in turn.content
        assert "REFERENCE_MARKER" not in json.dumps(turn.payload)
        if turn.name == "read_skill":
            assert turn.payload["status"] == "completed"
            assert turn.payload["result"]["status"] == "loaded"
    assert "PARENT_BODY_MARKER" not in json.dumps(state_to_dict(state))
    response = client.post("/api/chat", json={"message": "继续", "session_id": payload["session_id"]})
    assert response.status_code == 200
    assert response.json()["reply"] == "新一轮尚未加载技能。"


def test_navigation_worker_hydrates_route_strings_and_returns_route(tmp_path: Path) -> None:
    from app.agent.llm.provider_adapter import ModelToolCall

    mcp_dir = tmp_path / "navigation_mcp"
    mcp_dir.mkdir()
    fixture_server = Path(__file__).resolve().parents[1] / "fixtures" / "mock_amap_mcp_server.py"
    (mcp_dir / "amap.json").write_text(json.dumps({
        "command": sys.executable,
        "args": [str(fixture_server)],
        "route_tool_name": "maps_direction_walking",
    }), encoding="utf-8")
    client = _build_client(tmp_path, mcp_servers_dir=mcp_dir)
    adapter = client.app.state.container.react_runtime._provider_adapter
    main_calls = 0
    worker_calls = 0

    async def fake_complete(**kwargs):
        nonlocal main_calls, worker_calls
        if kwargs["runtime_hints"]["active_subagent"] == "main_agent":
            main_calls += 1
            if main_calls == 1:
                return ModelResponse(tool_calls=[ModelToolCall(
                    "dispatch-route", "invoke_worker",
                    {"worker": "navigation_worker", "task": "从起点步行到 Gamma Arcade"},
                )])
            return ModelResponse(text="步行路线已经规划完成。")

        worker_calls += 1
        if worker_calls == 1:
            return ModelResponse(tool_calls=[ModelToolCall(
                "destination", "db_query_tool", {"shop_id": 10, "page": 1, "page_size": 1},
            )])
        if worker_calls == 2:
            return ModelResponse(tool_calls=[ModelToolCall(
                "provider", "geo_resolve_tool", {"province_code": "110000000000"},
            )])
        if worker_calls == 3:
            return ModelResponse(tool_calls=[ModelToolCall(
                "route", "route_plan_tool", {
                    "provider": "amap",
                    "mode": "walking",
                    "origin": "116.3,39.9",
                    "destination": "116.32,39.905",
                },
            )])
        return ModelResponse(text="route ready")

    adapter.complete = fake_complete  # type: ignore[method-assign]
    response = client.post("/api/chat", json={"message": "从起点走到 Gamma Arcade"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "navigate"
    assert payload["route"]["mode"] == "walking"
    assert payload["route"]["origin"]["lng"] == 116.3
    assert payload["route"]["destination"]["lng"] == 116.32
    state = client.app.state.container.session_store.get_session(payload["session_id"])
    assert state.working_memory["last_route_endpoints"]["destination"]["lng"] == 116.32
    route_turn = next(turn for turn in state.turns if turn.name == "route_plan_tool")
    assert route_turn.payload["argument_evidence"]["hydrated_fields"] == ["origin", "destination"]

def test_chat_records_native_tool_round_trip(tmp_path: Path) -> None:
    from app.agent.llm.provider_adapter import ModelToolCall

    client = _build_client(tmp_path)
    adapter = client.app.state.container.react_runtime._provider_adapter
    captured_messages: list[list[dict[str, object]]] = []
    calls = {"count": 0}

    async def fake_complete(*, instructions, messages, tools, runtime_hints=None):
        captured_messages.append(messages)
        calls["count"] += 1
        if calls["count"] == 1:
            return ModelResponse(
                tool_calls=[
                    ModelToolCall(
                        call_id="call_1",
                        name="db_query_tool",
                        arguments={"keyword": "Gamma", "page": 1, "page_size": 3},
                        raw_arguments='{"keyword": "Gamma", "page": 1, "page_size": 3}',
                    )
                ],
                status="completed",
                protocol="responses",
                reported_model="stub-model",
                transcript={
                    "responses_output": [
                        {
                            "type": "function_call",
                            "call_id": "call_1",
                            "name": "db_query_tool",
                            "arguments": '{"keyword": "Gamma", "page": 1, "page_size": 3}',
                        }
                    ]
                },
            )
        return ModelResponse(text="found Gamma", status="completed", protocol="responses")

    adapter.complete = fake_complete  # type: ignore[method-assign]

    resp = client.post("/api/chat", json={"message": "find Gamma", "page_size": 3})

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["reply"] == "found Gamma"
    assert payload["shops"] and payload["shops"][0]["source_id"] == 10

    # The second model call must receive the native tool round trip:
    # assistant transcript (responses_output) followed by the paired tool output.
    assert calls["count"] == 2
    second_call = captured_messages[1]
    assistant_message = next(item for item in second_call if item.get("responses_output"))
    assert assistant_message["responses_output"][0]["call_id"] == "call_1"
    tool_message = next(item for item in second_call if item.get("role") == "tool")
    assert tool_message["tool_call_id"] == "call_1"

    detail = client.get(f"/api/chat/sessions/{payload['session_id']}").json()
    assert detail["status"] == "completed"
    model_turns = [
        turn.__dict__ for turn in client.app.state.container.session_store.get_session(payload["session_id"]).turns
        if turn.role == "assistant" and turn.payload.get("model")
    ]
    assert model_turns
    first_model = model_turns[0]["payload"]["model"]
    assert first_model["tool_calls"][0]["raw_arguments"] == '{"keyword": "Gamma", "page": 1, "page_size": 3}'
    tool_turns = [turn.__dict__ for turn in client.app.state.container.session_store.get_session(payload["session_id"]).turns if turn.role == "tool"]
    assert tool_turns
    evidence = tool_turns[0].get("payload", {}).get("argument_evidence")
    assert evidence is not None
    assert evidence["parsed_arguments"]["keyword"] == "Gamma"

def test_chat_marks_session_failed_when_model_errors(tmp_path: Path) -> None:
    client = _build_client(tmp_path)  # LLM_API_KEY is empty, so the provider errors out

    resp = client.post("/api/chat", json={"message": "find Gamma", "page_size": 3})

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["reply"]  # fallback reply is still returned to the caller

    detail_resp = client.get(f"/api/chat/sessions/{payload['session_id']}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["status"] == "failed"
    assert detail["last_error"]

    replay_buffer = client.app.state.container.replay_buffer
    events = replay_buffer.list_events(payload["session_id"])
    assert any(event.event == "session.failed" for event in events)
    completed = [event for event in events if event.event == "assistant.completed"]
    assert not completed
    stream = client.get(f"/api/stream/{payload['session_id']}")
    assert "event: session.failed" in stream.text

def test_second_turn_resets_stream_replay_buffer(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    _stub_provider_adapter(client)

    first_resp = client.post("/api/chat", json={"message": "松江区有哪些机厅可以去？", "page_size": 3})
    assert first_resp.status_code == 200
    session_id = first_resp.json()["session_id"]

    replay_buffer = client.app.state.container.replay_buffer
    first_events = replay_buffer.list_events(session_id)
    assert first_events
    first_event_ids = {event.id for event in first_events}
    assert any(event.event == "assistant.completed" for event in first_events)

    second_resp = client.post(
        "/api/chat",
        json={"session_id": session_id, "message": "上海松江区", "page_size": 3},
    )
    assert second_resp.status_code == 200

    second_events = replay_buffer.list_events(session_id)
    assert second_events
    assert all(event.id not in first_event_ids for event in second_events)
    assert any(event.event == "assistant.completed" for event in second_events)

def test_incomplete_text_fails_without_success_event(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    async def fake_complete(**kwargs):
        return ModelResponse(text="partial", status="incomplete", error={"type": "incomplete_response", "message": "length"})
    client.app.state.container.react_runtime._provider_adapter.complete = fake_complete
    response = client.post("/api/chat", json={"message": "find Gamma"}).json()
    state = client.app.state.container.session_store.get_session(response["session_id"])
    assert state.status == "failed"
    stream = client.get(f"/api/stream/{state.session_id}").text
    assert "event: session.failed" in stream
    assert "event: assistant.completed" not in stream

def test_invalid_json_cannot_be_hydrated_into_success(tmp_path: Path) -> None:
    from app.agent.llm.provider_adapter import ModelToolCall
    client = _build_client(tmp_path)
    calls = 0
    async def fake_complete(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(tool_calls=[ModelToolCall("bad", "summary_tool", {}, raw_arguments="{", parse_error="invalid JSON")])
        return ModelResponse(text="Unable to summarize")
    client.app.state.container.react_runtime._provider_adapter.complete = fake_complete
    response = client.post("/api/chat", json={"message": "find Gamma"}).json()
    events = client.app.state.container.replay_buffer.list_events(response["session_id"])
    assert [event.event for event in events if event.event.startswith("tool.")] == ["tool.started", "tool.failed"]
    state = client.app.state.container.session_store.get_session(response["session_id"])
    evidence = next(turn.payload["argument_evidence"] for turn in state.turns if turn.role == "tool")
    assert evidence["parse_error"] == "invalid JSON"
    assert evidence["hydrated_fields"] == []

def test_previous_route_is_not_promoted_as_new_result() -> None:
    from app.agent.runtime.react_runtime import ReactRuntime
    from app.agent.runtime.session_state import set_working_memory_artifact
    runtime = object.__new__(ReactRuntime)
    memory = {}
    set_working_memory_artifact(memory, "route", {"distance_m": 100}, turn_index=1)
    set_working_memory_artifact(memory, "shops", [{"source_id": 10}], turn_index=1)
    memory["last_route_endpoints"] = {
        "origin": {"lng": 116.3, "lat": 39.9},
        "destination": {"lng": 116.4, "lat": 39.91},
    }
    prepared = runtime._prepare_turn_memory(memory)
    assert "route" not in prepared["artifacts"]
    worker = runtime._build_worker_memory_snapshot(prepared)
    assert worker["artifacts"]["shops"] == [{"source_id": 10}]
    assert worker["last_route_endpoints"] == memory["last_route_endpoints"]
    assert runtime._promote_worker_artifacts(parent_memory=memory, worker_memory=worker, turn_index=2) == {}
    assert memory["artifact_meta"]["shops"]["turn_index"] == 1
    set_working_memory_artifact(worker, "shops", [], turn_index=2)
    assert runtime._promote_worker_artifacts(parent_memory=memory, worker_memory=worker, turn_index=2) == {"shops": []}

def test_online_route_unavailable_does_not_estimate() -> None:
    import pytest
    from app.agent.tools.builtin.route_plan_tool import RoutePlanTool
    from app.protocol.messages import Location
    with pytest.raises(RuntimeError, match="route_unavailable"):
        asyncio.run(RoutePlanTool().plan_route(provider="amap", mode="walking", origin=Location(lng=116, lat=39), destination=Location(lng=117, lat=40)))

def test_history_window_keeps_complete_tool_group() -> None:
    from app.agent.context.context_builder import ContextBuilder
    from app.agent.runtime.session_state import AgentTurn
    builder = ContextBuilder(prompt_root=Path("."), history_turn_limit=4)
    turns = [AgentTurn(role="user", content="query"), AgentTurn(role="assistant", content="", payload={"model": {"transcript": {"responses_output": [{"type": "function_call", "call_id": "c"}]}}})]
    turns.extend(AgentTurn(role="tool", content="result", call_id=str(i)) for i in range(5))
    retained = builder._tail_turns(turns, scope="conversation")
    assert retained == turns

def test_incomplete_worker_call_is_not_executed(tmp_path: Path) -> None:
    from app.agent.llm.provider_adapter import ModelToolCall
    client = _build_client(tmp_path)
    async def fake_complete(**kwargs):
        if kwargs["runtime_hints"]["active_subagent"] == "main_agent":
            if any(message.get("role") == "tool" for message in kwargs["messages"]):
                return ModelResponse(text="Worker failed")
            return ModelResponse(tool_calls=[ModelToolCall("dispatch", "invoke_worker", {"worker": "search_worker", "task": "find Gamma"})])
        return ModelResponse(text="partial", tool_calls=[ModelToolCall("partial", "db_query_tool", {"page": 1, "page_size": 3})], error={"type": "incomplete_response", "message": "length"})
    client.app.state.container.react_runtime._provider_adapter.complete = fake_complete
    response = client.post("/api/chat", json={"message": "find Gamma"}).json()
    events = client.app.state.container.replay_buffer.list_events(response["session_id"])
    assert any(event.event == "worker.failed" for event in events)
    assert not any(event.event == "tool.started" and event.data.get("call_id") == "partial" for event in events)
    assert any(event.event == "tool.failed" and event.data.get("call_id") == "dispatch" for event in events)

def test_route_metrics_without_geometry_does_not_invent_polyline() -> None:
    from app.agent.tools.mcp.dispatcher import _extract_route_from_mapping
    route = _extract_route_from_mapping({"distance": 100, "duration": 90}, remote_name="maps_direction_walking", raw_arguments={"origin": "116,39", "destination": "116.01,39.01"}, depth=0)
    assert route is not None
    assert route.distance_m == 100
    assert route.polyline == []
