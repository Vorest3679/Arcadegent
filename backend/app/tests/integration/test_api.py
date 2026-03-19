"""Integration tests for core FastAPI endpoints and chat session continuity."""

from __future__ import annotations

import json
import os
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


def _build_client(tmp_path: Path, *, session_store_path: Path | None = None) -> TestClient:
    data_path = tmp_path / "shops.jsonl"
    _seed_data(data_path)
    os.environ["ARCADE_DATA_JSONL"] = str(data_path)
    os.environ["CHAT_SESSION_STORE_PATH"] = str(session_store_path or (tmp_path / "chat_sessions.json"))
    os.environ["MCP_AMAP_ENABLED"] = "false"

    from app.main import create_app

    return TestClient(create_app())


def _build_client_with_rows(
    tmp_path: Path,
    rows: list[dict[str, object]],
    *,
    session_store_path: Path | None = None,
) -> TestClient:
    data_path = tmp_path / "shops_custom.jsonl"
    with data_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")
    os.environ["ARCADE_DATA_JSONL"] = str(data_path)
    os.environ["CHAT_SESSION_STORE_PATH"] = str(session_store_path or (tmp_path / "chat_sessions.json"))
    os.environ["MCP_AMAP_ENABLED"] = "false"

    from app.main import create_app

    return TestClient(create_app())


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
    assert detail["turn_count"] >= 2
    assert detail["turns"][0]["role"] == "user"
    assert detail["turns"][-1]["role"] == "assistant"


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
