from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.agent.llm.provider_adapter import ModelResponse
from app.agent.runtime.session_state import AgentSessionState, state_from_dict
from app.infra.db.protocols import SessionStateRepository
from backend.app.tests.integration._api_test_support import (
    InMemorySessionStateRepository,
    _build_client,
    _build_client_with_rows,
    _stub_provider_adapter,
    _wait_for_session_status,
)
def test_chat_session_detail_supports_legacy_route_payload(tmp_path: Path) -> None:
    session_store_path = tmp_path / "legacy_chat_sessions.json"
    session_store_path.write_text(
        json.dumps(
            {
                "version": 1,
                "sessions": [
                    {
                        "session_id": "legacy-session",
                        "turn_index": 1,
                        "active_subagent": "main_agent",
                        "intent": "navigate",
                        "status": "completed",
                        "last_error": None,
                        "turns": [
                            {
                                "role": "user",
                                "content": "how to go",
                                "payload": {},
                                "created_at": "2026-04-13T00:00:00Z",
                            },
                            {
                                "role": "assistant",
                                "content": "route ready",
                                "payload": {
                                    "final": True,
                                    "map_artifacts": {
                                        "shops": [
                                            {
                                                "source": "bemanicn",
                                                "source_id": 10,
                                                "source_url": "https://map.bemanicn.com/s/10",
                                                "name": "Gamma Arcade",
                                                "address": "Test Address",
                                                "longitude_wgs84": 116.397428,
                                                "latitude_wgs84": 39.90923,
                                                "arcade_count": 1,
                                            }
                                        ],
                                        "route": {
                                            "provider": "amap",
                                            "mode": "walking",
                                            "distance_m": 1200,
                                            "duration_s": 900,
                                            "polyline": [
                                                {"lng": 116.397428, "lat": 39.90923},
                                                {"lng": 116.407428, "lat": 39.91923},
                                            ],
                                        },
                                        "destination": {
                                            "source": "bemanicn",
                                            "source_id": 10,
                                            "source_url": "https://map.bemanicn.com/s/10",
                                            "name": "Gamma Arcade",
                                            "address": "Test Address",
                                            "arcade_count": 1,
                                        },
                                        "view_payload": {"version": 1, "scene": "agent_route"},
                                    },
                                },
                                "created_at": "2026-04-13T00:00:10Z",
                            },
                        ],
                        "working_memory": {
                            "artifacts": {
                                "shops": [
                                    {
                                        "source": "bemanicn",
                                        "source_id": 10,
                                        "source_url": "https://map.bemanicn.com/s/10",
                                        "name": "Gamma Arcade",
                                        "address": "Test Address",
                                        "province_code": "110000000000",
                                        "province_name": "Beijing",
                                        "city_code": "110100000000",
                                        "city_name": "Beijing",
                                        "county_code": "110101000000",
                                        "county_name": "Dongcheng",
                                        "updated_at": "2026-02-20T00:00:00Z",
                                        "longitude_wgs84": 116.397428,
                                        "latitude_wgs84": 39.90923,
                                        "arcades": [{"title_name": "CHUNITHM", "quantity": 2}],
                                        "arcade_count": 1,
                                    }
                                ],
                                "route": {
                                    "provider": "amap",
                                    "mode": "walking",
                                    "distance_m": 1200,
                                    "duration_s": 900,
                                    "polyline": [
                                        {"lng": 116.397428, "lat": 39.90923},
                                        {"lng": 116.407428, "lat": 39.91923},
                                    ],
                                },
                            },
                            "reply": "route ready",
                        },
                        "previous_response_id": None,
                        "created_at": "2026-04-13T00:00:00Z",
                        "updated_at": "2026-04-13T00:00:10Z",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    fake_store = InMemorySessionStateRepository()
    legacy = json.loads(session_store_path.read_text(encoding="utf-8"))
    for raw in legacy.get("sessions", []):
        state = state_from_dict(raw)
        if state is not None:
            fake_store.seed(state)
    client = _build_client(tmp_path, session_store=fake_store)

    resp = client.get("/api/chat/sessions/legacy-session")

    assert resp.status_code == 200
    body = resp.json()
    assert body["route"]["origin"]["lng"] == 116.397428
    assert body["destination"]["source_id"] == 10
    assert body["turns"][-1]["map_artifacts"]["route"]["distance_m"] == 1200
    assert body["turns"][-1]["map_artifacts"]["destination"]["source_id"] == 10
    assert "client_location" in body
    assert "view_payload" in body

def test_chat_reuses_session_context(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    _stub_provider_adapter(client)

    first_resp = client.post("/api/chat", json={"message": "find Gamma", "page_size": 3})
    assert first_resp.status_code == 200
    first_payload = first_resp.json()
    session_id = first_payload["session_id"]

    second_resp = client.post(
        "/api/chat",
        json={"session_id": session_id, "message": "continue with previous result"},
    )
    assert second_resp.status_code == 200
    second_payload = second_resp.json()
    assert second_payload["session_id"] == session_id
    if first_payload["shops"]:
        assert first_payload["shops"][0]["source_id"] == 10
        assert second_payload["shops"]
        assert second_payload["shops"][0]["source_id"] == 10

    sessions_resp = client.get("/api/chat/sessions")
    assert sessions_resp.status_code == 200
    sessions = sessions_resp.json()
    assert sessions
    assert sessions[0]["session_id"] == session_id
    assert sessions[0]["turn_count"] >= 2

    detail_resp = client.get(f"/api/chat/sessions/{session_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["session_id"] == session_id
    assert detail["status"] == "completed"
    assert detail["turn_count"] >= 2
    turns = detail["turns"]
    assert turns
    assert turns[0]["role"] == "user"
    assert turns[-1]["role"] == "assistant"

    delete_resp = client.delete(f"/api/chat/sessions/{session_id}")
    assert delete_resp.status_code == 204

    deleted_detail = client.get(f"/api/chat/sessions/{session_id}")
    assert deleted_detail.status_code == 404

def test_chat_sessions_survive_app_restart(tmp_path: Path) -> None:
    shared_store = InMemorySessionStateRepository()
    client = _build_client(tmp_path, session_store=shared_store)
    _stub_provider_adapter(client)

    first_resp = client.post("/api/chat", json={"message": "find Gamma", "page_size": 3})
    assert first_resp.status_code == 200
    session_id = first_resp.json()["session_id"]

    restarted_client = _build_client(tmp_path, session_store=shared_store)
    _stub_provider_adapter(restarted_client)

    sessions_resp = restarted_client.get("/api/chat/sessions")
    assert sessions_resp.status_code == 200
    sessions = sessions_resp.json()
    assert sessions
    assert any(row["session_id"] == session_id for row in sessions)

    detail_resp = restarted_client.get(f"/api/chat/sessions/{session_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["session_id"] == session_id
    assert detail["status"] == "completed"
    assert detail["turn_count"] >= 2
    assert detail["turns"][0]["role"] == "user"
    assert detail["turns"][-1]["role"] == "assistant"

def test_chat_sessions_are_scoped_by_client_id(tmp_path: Path) -> None:
    client = _build_client(tmp_path)

    first_resp = client.post(
        "/api/chat",
        json={"client_id": "client-a", "message": "find Gamma", "page_size": 3},
    )
    assert first_resp.status_code == 200
    first_session_id = first_resp.json()["session_id"]

    second_resp = client.post(
        "/api/chat",
        json={"client_id": "client-b", "message": "find Gamma", "page_size": 3},
    )
    assert second_resp.status_code == 200
    second_session_id = second_resp.json()["session_id"]

    first_list_resp = client.get("/api/chat/sessions", params={"client_id": "client-a"})
    assert first_list_resp.status_code == 200
    assert [row["session_id"] for row in first_list_resp.json()] == [first_session_id]

    second_list_resp = client.get("/api/chat/sessions", params={"client_id": "client-b"})
    assert second_list_resp.status_code == 200
    assert [row["session_id"] for row in second_list_resp.json()] == [second_session_id]

    wrong_detail_resp = client.get(
        f"/api/chat/sessions/{first_session_id}",
        params={"client_id": "client-b"},
    )
    assert wrong_detail_resp.status_code == 404

    wrong_continue_resp = client.post(
        "/api/chat",
        json={
            "client_id": "client-b",
            "session_id": first_session_id,
            "message": "continue from another client",
        },
    )
    assert wrong_continue_resp.status_code == 404

    wrong_delete_resp = client.delete(
        f"/api/chat/sessions/{first_session_id}",
        params={"client_id": "client-b"},
    )
    assert wrong_delete_resp.status_code == 404

    right_delete_resp = client.delete(
        f"/api/chat/sessions/{first_session_id}",
        params={"client_id": "client-a"},
    )
    assert right_delete_resp.status_code == 204

def test_chat_dispatch_runs_in_background(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    _stub_provider_adapter(client)

    dispatch_resp = client.post("/api/chat/sessions", json={"message": "find Gamma", "page_size": 3})
    assert dispatch_resp.status_code == 202
    dispatch_payload = dispatch_resp.json()
    session_id = dispatch_payload["session_id"]
    assert dispatch_payload["status"] == "running"

    detail = _wait_for_session_status(client, session_id, "completed")
    assert detail["session_id"] == session_id
    assert detail["reply"]
    assert detail["turn_count"] >= 2
    assert detail["turns"][0]["role"] == "user"
    assert detail["turns"][-1]["role"] == "assistant"

    sessions_resp = client.get("/api/chat/sessions")
    assert sessions_resp.status_code == 200
    sessions = sessions_resp.json()
    session_row = next(row for row in sessions if row["session_id"] == session_id)
    assert session_row["status"] == "completed"

def test_chat_dispatch_rejects_duplicate_running_session(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    _stub_provider_adapter(client)
    runtime = client.app.state.container.react_runtime
    original_run_chat = runtime.run_chat

    async def slow_run_chat(request):
        await asyncio.sleep(0.2)
        return await original_run_chat(request)

    runtime.run_chat = slow_run_chat  # type: ignore[method-assign]

    session_id = "s_duplicate123"
    first_resp = client.post(
        "/api/chat/sessions",
        json={"session_id": session_id, "message": "find Gamma", "page_size": 3},
    )
    assert first_resp.status_code == 202

    second_resp = client.post(
        "/api/chat/sessions",
        json={"session_id": session_id, "message": "find Gamma again", "page_size": 3},
    )
    assert second_resp.status_code == 409

    _wait_for_session_status(client, session_id, "completed")

def test_cancel_running_chat_preserves_context_for_the_next_input(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    adapter = client.app.state.container.react_runtime._provider_adapter

    async def slow_complete(*, instructions, messages, tools, runtime_hints=None):
        await asyncio.sleep(5)
        return ModelResponse(text="too late", status="completed", protocol="responses")

    adapter.complete = slow_complete  # type: ignore[method-assign]
    session_id = "s_cancelled123"
    first = client.post(
        "/api/chat/sessions",
        json={"session_id": session_id, "message": "find Gamma", "page_size": 3},
    )
    assert first.status_code == 202

    cancelled = client.post(f"/api/chat/sessions/{session_id}/cancel")
    assert cancelled.status_code == 200
    cancelled_detail = cancelled.json()
    assert cancelled_detail["status"] == "failed"
    assert "上下文" in cancelled_detail["last_error"]
    assert [turn["content"] for turn in cancelled_detail["turns"]] == ["find Gamma"]

    _stub_provider_adapter(client, reply="continued from saved context")
    second = client.post(
        "/api/chat/sessions",
        json={"session_id": session_id, "message": "continue", "page_size": 3},
    )
    assert second.status_code == 202
    completed = _wait_for_session_status(client, session_id, "completed")
    assert [turn["content"] for turn in completed["turns"]] == [
        "find Gamma",
        "continue",
        "continued from saved context",
    ]

def test_session_detail_hides_model_evidence_turns(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
    _stub_provider_adapter(client, reply="only show once")

    response = client.post("/api/chat", json={"session_id": "s_visible123", "message": "hello"})
    assert response.status_code == 200
    detail = client.get("/api/chat/sessions/s_visible123").json()
    assert [turn["content"] for turn in detail["turns"]] == ["hello", "only show once"]
    assert detail["turn_count"] == 2
