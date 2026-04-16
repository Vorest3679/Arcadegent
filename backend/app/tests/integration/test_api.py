"""Integration tests for core FastAPI endpoints and chat session continuity."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient


def _seed_data(path: Path) -> None:
    rows = [
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
        }
    ]
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")


def _clear_mcp_env() -> None:
    for name in (
        "MCP_SERVERS_DIR",
        "MCP_DEFAULT_TIMEOUT_SECONDS",
    ):
        os.environ.pop(name, None)


def _clear_llm_env() -> None:
    for name in (
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_TIMEOUT_SECONDS",
    ):
        os.environ.pop(name, None)


def _build_client(
    tmp_path: Path,
    *,
    session_store_path: Path | None = None,
    mcp_servers_dir: Path | None = None,
    cache_path: Path | None = None,
) -> TestClient:
    data_path = tmp_path / "shops.jsonl"
    empty_mcp_dir = tmp_path / "mcp_empty"
    empty_mcp_dir.mkdir(exist_ok=True)
    _seed_data(data_path)
    _clear_mcp_env()
    _clear_llm_env()
    os.environ["ARCADE_DATA_JSONL"] = str(data_path)
    os.environ["ARCADE_DATA_SOURCE"] = "jsonl"
    os.environ["CHAT_SESSION_STORE_PATH"] = str(session_store_path or (tmp_path / "chat_sessions.json"))
    os.environ["ARCADE_GEO_CACHE_PATH"] = str(cache_path or (tmp_path / "arcade_geo_cache.json"))
    os.environ["LLM_API_KEY"] = ""
    os.environ["LLM_BASE_URL"] = "https://api.example.invalid/v1"
    os.environ["LLM_MODEL"] = "test-model"
    os.environ["AMAP_API_KEY"] = "test-amap-key"
    os.environ["MCP_SERVERS_DIR"] = str(mcp_servers_dir or empty_mcp_dir)

    from app.main import create_app

    client = TestClient(create_app())
    client.__enter__()
    return client


def _build_client_with_rows(
    tmp_path: Path,
    rows: list[dict[str, object]],
    *,
    session_store_path: Path | None = None,
    cache_path: Path | None = None,
) -> TestClient:
    data_path = tmp_path / "shops_custom.jsonl"
    empty_mcp_dir = tmp_path / "mcp_empty"
    empty_mcp_dir.mkdir(exist_ok=True)
    with data_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")
    _clear_mcp_env()
    _clear_llm_env()
    os.environ["ARCADE_DATA_JSONL"] = str(data_path)
    os.environ["ARCADE_DATA_SOURCE"] = "jsonl"
    os.environ["CHAT_SESSION_STORE_PATH"] = str(session_store_path or (tmp_path / "chat_sessions.json"))
    os.environ["ARCADE_GEO_CACHE_PATH"] = str(cache_path or (tmp_path / "arcade_geo_cache.json"))
    os.environ["LLM_API_KEY"] = ""
    os.environ["LLM_BASE_URL"] = "https://api.example.invalid/v1"
    os.environ["LLM_MODEL"] = "test-model"
    os.environ["AMAP_API_KEY"] = "test-amap-key"
    os.environ["MCP_SERVERS_DIR"] = str(empty_mcp_dir)

    from app.main import create_app

    client = TestClient(create_app())
    client.__enter__()
    return client


def _wait_for_session_status(
    client: TestClient,
    session_id: str,
    expected_status: str,
    *,
    timeout_seconds: float = 3.0,
) -> dict[str, object]:
    deadline = time.monotonic() + timeout_seconds
    last_payload: dict[str, object] | None = None
    while time.monotonic() < deadline:
        resp = client.get(f"/api/v1/chat/sessions/{session_id}")
        if resp.status_code == 200:
            payload = resp.json()
            if isinstance(payload, dict):
                last_payload = payload
                if payload.get("status") == expected_status:
                    return payload
        time.sleep(0.05)
    raise AssertionError(
        f"session '{session_id}' did not reach status '{expected_status}', last_payload={last_payload}"
    )


def test_health_arcades_and_chat(tmp_path: Path) -> None:
    client = _build_client(tmp_path)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["mcp"]["enabled"] is False

    listing = client.get("/api/v1/arcades", params={"keyword": "Gamma"})
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    assert body["items"][0]["source_id"] == 10

    chat_resp = client.post("/api/chat", json={"message": "find Gamma", "page_size": 3})
    assert chat_resp.status_code == 200
    assert chat_resp.json()["intent"] in {"search", "search_nearby"}


def test_arcade_list_enriches_geo_and_writes_cache(tmp_path: Path) -> None:
    cache_path = tmp_path / "arcade_geo_cache.json"
    row = {
        "source": "bemanicn",
        "source_id": 21,
        "source_url": "https://map.bemanicn.com/s/21",
        "name": "Geo Arcade",
        "address": "Nanjing Road",
        "province_code": "310000000000",
        "province_name": "Shanghai",
        "city_code": "310100000000",
        "city_name": "Shanghai",
        "county_code": "310101000000",
        "county_name": "Huangpu",
        "updated_at": "2026-04-13T00:00:00Z",
        "arcades": [{"title_name": "maimai", "quantity": 2}],
    }
    client = _build_client_with_rows(tmp_path, [row], cache_path=cache_path)
    client.app.state.container.arcade_geo_resolver._request_geocode = lambda **_: {  # type: ignore[method-assign]
        "status": "1",
        "geocodes": [{"location": "121.475,31.228"}],
    }

    resp = client.get("/api/v1/arcades")

    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["geo"]["gcj02"]["lng"] == 121.475
    assert cache_path.exists()


def test_arcade_detail_returns_geo(tmp_path: Path) -> None:
    row = {
        "source": "bemanicn",
        "source_id": 22,
        "source_url": "https://map.bemanicn.com/s/22",
        "name": "Detail Geo Arcade",
        "address": "Xidan",
        "province_code": "110000000000",
        "province_name": "Beijing",
        "city_code": "110100000000",
        "city_name": "Beijing",
        "county_code": "110102000000",
        "county_name": "Xicheng",
        "updated_at": "2026-04-13T00:00:00Z",
        "arcades": [{"title_name": "CHUNITHM", "quantity": 1}],
    }
    client = _build_client_with_rows(tmp_path, [row])
    client.app.state.container.arcade_geo_resolver._request_geocode = lambda **_: {  # type: ignore[method-assign]
        "status": "1",
        "geocodes": [{"location": "116.3974,39.9087"}],
    }

    resp = client.get("/api/v1/arcades/22")

    assert resp.status_code == 200
    assert resp.json()["geo"]["gcj02"]["lat"] == 39.9087


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
                                "payload": {"final": True},
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
    client = _build_client(tmp_path, session_store_path=session_store_path)

    resp = client.get("/api/v1/chat/sessions/legacy-session")

    assert resp.status_code == 200
    body = resp.json()
    assert body["route"]["origin"]["lng"] == 116.397428
    assert body["destination"]["source_id"] == 10
    assert "client_location" in body
    assert "view_payload" in body


def test_health_reports_mcp_tools_loaded_from_config_directory(tmp_path: Path) -> None:
    mcp_dir = tmp_path / "mcp_servers"
    mcp_dir.mkdir()
    fixture_server = Path(__file__).resolve().parents[1] / "fixtures" / "mock_amap_mcp_server.py"
    (mcp_dir / "amap.json").write_text(
        json.dumps(
            {
                "command": sys.executable,
                "args": [str(fixture_server)],
                "route_tool_name": "maps_direction_walking",
            }
        ),
        encoding="utf-8",
    )

    client = _build_client(tmp_path, mcp_servers_dir=mcp_dir)

    health = client.get("/health")
    assert health.status_code == 200
    payload = health.json()
    assert payload["mcp"]["enabled"] is True
    assert payload["mcp"]["discovered_tool_count"] == 1
    assert payload["mcp"]["servers"]["amap"]["discovered"] is True
    assert payload["mcp"]["servers"]["amap"]["selected_route_tool"] == "mcp__amap__maps_direction_walking"
    assert payload["mcp"]["servers"]["amap"]["available_tools"] == ["mcp__amap__maps_direction_walking"]


def test_chat_reuses_session_context(tmp_path: Path) -> None:
    client = _build_client(tmp_path)

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

    sessions_resp = client.get("/api/v1/chat/sessions")
    assert sessions_resp.status_code == 200
    sessions = sessions_resp.json()
    assert sessions
    assert sessions[0]["session_id"] == session_id
    assert sessions[0]["turn_count"] >= 2

    detail_resp = client.get(f"/api/v1/chat/sessions/{session_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["session_id"] == session_id
    assert detail["status"] == "completed"
    assert detail["turn_count"] >= 2
    turns = detail["turns"]
    assert turns
    assert turns[0]["role"] == "user"
    assert turns[-1]["role"] == "assistant"

    delete_resp = client.delete(f"/api/v1/chat/sessions/{session_id}")
    assert delete_resp.status_code == 204

    deleted_detail = client.get(f"/api/v1/chat/sessions/{session_id}")
    assert deleted_detail.status_code == 404


def test_chat_sessions_survive_app_restart(tmp_path: Path) -> None:
    session_store_path = tmp_path / "persisted_chat_sessions.json"
    client = _build_client(tmp_path, session_store_path=session_store_path)

    first_resp = client.post("/api/chat", json={"message": "find Gamma", "page_size": 3})
    assert first_resp.status_code == 200
    session_id = first_resp.json()["session_id"]

    restarted_client = _build_client(tmp_path, session_store_path=session_store_path)

    sessions_resp = restarted_client.get("/api/v1/chat/sessions")
    assert sessions_resp.status_code == 200
    sessions = sessions_resp.json()
    assert sessions
    assert any(row["session_id"] == session_id for row in sessions)

    detail_resp = restarted_client.get(f"/api/v1/chat/sessions/{session_id}")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["session_id"] == session_id
    assert detail["status"] == "completed"
    assert detail["turn_count"] >= 2
    assert detail["turns"][0]["role"] == "user"
    assert detail["turns"][-1]["role"] == "assistant"


def test_second_turn_resets_stream_replay_buffer(tmp_path: Path) -> None:
    client = _build_client(tmp_path)

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


def test_chat_dispatch_runs_in_background(tmp_path: Path) -> None:
    client = _build_client(tmp_path)

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

    sessions_resp = client.get("/api/v1/chat/sessions")
    assert sessions_resp.status_code == 200
    sessions = sessions_resp.json()
    session_row = next(row for row in sessions if row["session_id"] == session_id)
    assert session_row["status"] == "completed"


def test_chat_dispatch_rejects_duplicate_running_session(tmp_path: Path) -> None:
    client = _build_client(tmp_path)
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


def test_arcades_api_supports_title_quantity_sorting(tmp_path: Path) -> None:
    client = _build_client_with_rows(
        tmp_path,
        [
            {
                "source": "bemanicn",
                "source_id": 10,
                "source_url": "https://map.bemanicn.com/s/10",
                "name": "Gamma Arcade",
                "arcades": [{"title_name": "maimai", "quantity": 1}],
            },
            {
                "source": "bemanicn",
                "source_id": 11,
                "source_url": "https://map.bemanicn.com/s/11",
                "name": "Delta Arcade",
                "arcades": [{"title_name": "maimai", "quantity": 4}],
            },
            {
                "source": "bemanicn",
                "source_id": 12,
                "source_url": "https://map.bemanicn.com/s/12",
                "name": "Epsilon Arcade",
                "arcades": [{"title_name": "sdvx", "quantity": 2}],
            },
        ],
    )

    resp = client.get(
        "/api/v1/arcades",
        params={
            "has_arcades": "true",
            "sort_by": "title_quantity",
            "sort_order": "desc",
            "sort_title_name": "maimai",
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 3
    assert [row["source_id"] for row in payload["items"]] == [11, 10, 12]


def test_arcades_api_supports_distance_sorting(tmp_path: Path) -> None:
    client = _build_client_with_rows(
        tmp_path,
        [
            {
                "source": "bemanicn",
                "source_id": 10,
                "source_url": "https://map.bemanicn.com/s/10",
                "name": "Near Arcade",
                "longitude_wgs84": 116.397428,
                "latitude_wgs84": 39.90923,
                "arcades": [{"title_name": "maimai", "quantity": 1}],
            },
            {
                "source": "bemanicn",
                "source_id": 11,
                "source_url": "https://map.bemanicn.com/s/11",
                "name": "Far Arcade",
                "longitude_wgs84": 116.407428,
                "latitude_wgs84": 39.91923,
                "arcades": [{"title_name": "maimai", "quantity": 1}],
            },
        ],
    )

    resp = client.get(
        "/api/v1/arcades",
        params={
            "has_arcades": "true",
            "sort_by": "distance",
            "sort_order": "asc",
            "origin_lng": 116.397428,
            "origin_lat": 39.90923,
            "origin_coord_system": "wgs84",
        },
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 2
    assert [row["source_id"] for row in payload["items"]] == [10, 11]
    assert payload["items"][0]["distance_m"] == 0
