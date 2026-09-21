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
def test_health_arcades_and_chat(tmp_path: Path) -> None:
    client = _build_client(tmp_path)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["mcp"]["enabled"] is False

    listing = client.get("/api/arcades", params={"keyword": "Gamma"})
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

    resp = client.get("/api/arcades")

    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["geo"]["gcj02"]["lng"] == 121.475
    assert cache_path.exists()

def test_arcade_list_supports_shop_name_search_without_title_matches(tmp_path: Path) -> None:
    client = _build_client_with_rows(
        tmp_path,
        [
            {
                "source": "bemanicn",
                "source_id": 31,
                "source_url": "https://map.bemanicn.com/s/31",
                "name": "星际传奇人民广场店",
                "name_pinyin": "xing-ji-chuan-qi-ren-min-guang-chang-dian",
                "arcades": [{"title_name": "SOUND VOLTEX", "quantity": 1}],
            },
            {
                "source": "bemanicn",
                "source_id": 32,
                "source_url": "https://map.bemanicn.com/s/32",
                "name": "Gamma Arcade",
                "arcades": [{"title_name": "maimai", "quantity": 2}],
            },
        ],
    )

    by_shop_name = client.get("/api/arcades", params={"shop_name": "星际传奇"})
    assert by_shop_name.status_code == 200
    assert by_shop_name.json()["total"] == 1
    assert by_shop_name.json()["items"][0]["source_id"] == 31

    by_title_as_shop_name = client.get("/api/arcades", params={"shop_name": "maimai"})
    assert by_title_as_shop_name.status_code == 200
    assert by_title_as_shop_name.json()["total"] == 0

    legacy_keyword = client.get("/api/arcades", params={"keyword": "maimai"})
    assert legacy_keyword.status_code == 200
    assert legacy_keyword.json()["total"] == 1
    assert legacy_keyword.json()["items"][0]["source_id"] == 32

def test_arcade_list_supports_title_name_filter(tmp_path: Path) -> None:
    client = _build_client_with_rows(
        tmp_path,
        [
            {
                "source": "bemanicn",
                "source_id": 41,
                "source_url": "https://map.bemanicn.com/s/41",
                "name": "星际传奇一号店",
                "arcades": [{"title_name": "CHUNITHM", "quantity": 1}],
            },
            {
                "source": "bemanicn",
                "source_id": 42,
                "source_url": "https://map.bemanicn.com/s/42",
                "name": "星际传奇二号店",
                "arcades": [{"title_name": "SOUND VOLTEX", "quantity": 1}],
            },
            {
                "source": "bemanicn",
                "source_id": 43,
                "source_url": "https://map.bemanicn.com/s/43",
                "name": "Delta Arcade",
                "arcades": [{"title_name": "CHUNITHM", "quantity": 1}],
            },
        ],
    )

    resp = client.get(
        "/api/arcades",
        params={"shop_name": "星际传奇", "title_name": "CHUNITHM"},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1
    assert payload["items"][0]["source_id"] == 41

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

    resp = client.get("/api/arcades/22")

    assert resp.status_code == 200
    assert resp.json()["geo"]["gcj02"]["lat"] == 39.9087

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
        "/api/arcades",
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
        "/api/arcades",
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
