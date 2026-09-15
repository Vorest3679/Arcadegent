"""Frozen synthetic oracles for production retrieval and online-only routing.

These are tool contracts, not a model-generated 20-case benchmark.
"""

import asyncio
from pathlib import Path

import httpx
import pytest

from app.agent.tools.builtin.route_plan_tool import AMapConfig, RoutePlanTool
from app.infra.db.local import LocalArcadeStore
from app.protocol.messages import Location

# Keep low-level store contracts isolated from the richer conversational fixture.
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures/arcades/legacy-contracts.jsonl"


@pytest.mark.parametrize("filters, expected", [
    ({"city_name": "上海", "title_name": "maimai"}, [900001, 900003]),
    ({"city_name": "北京", "title_name": "maimai"}, [900002]),
    ({"city_name": "上海市", "title_name": "CHUNITHM"}, [900003]),
    ({"city_name": "上海", "title_name": "nonexistent"}, []),
    ({"city_name": "不存在市", "title_name": "maimai"}, []),
    ({"county_name": "黄浦区", "has_arcades": True}, [900001]),
    ({"city_name": "上海", "has_arcades": False}, [900004]),
    ({"shop_name": "合成近点机厅"}, [900001]),
    ({"city_name": "上海", "title_name": "maimai", "sort_by": "title_quantity",
      "sort_title_name": "maimai", "sort_order": "desc"}, [900003, 900001]),
    ({"city_name": "上海", "title_name": "maimai", "sort_by": "title_quantity",
      "sort_title_name": "maimai", "sort_order": "asc"}, [900001, 900003]),
    ({"title_name": "maimai", "sort_by": "distance", "sort_order": "asc",
      "origin_lng": 121.47, "origin_lat": 31.23, "origin_coord_system": "wgs84"}, [900001, 900003, 900002]),
    ({"city_name": "上海", "title_name": "maimai", "page_size": 1}, [900001]),
    ({"city_name": "上海", "title_name": "maimai", "page_size": 1, "page": 2}, [900003]),
    ({"city_name": "上海", "title_name": "maimai", "page_size": 1, "page": 3}, []),
], ids=["city-title", "other-city", "other-title", "no-title", "no-city", "district",
        "no-machines", "shop-name", "quantity-desc", "quantity-asc", "distance",
        "page-one", "page-two", "page-out-of-range"])
def test_frozen_search_oracle(filters, expected):
    store = LocalArcadeStore.from_jsonl(FIXTURE)
    assert store.health()["loaded_rows"] == 4
    assert store.health()["bad_lines"] == 0
    arguments = dict(keyword=None, province_code=None, city_code=None, county_code=None,
                     has_arcades=None, page=1, page_size=10, sort_order="asc")
    arguments.update(filters)
    shops, total = store.list_shops(**arguments)
    assert [shop["source_id"] for shop in shops] == expected
    if filters.get("page_size") == 1:
        assert total == 2
    else:
        assert total == len(expected)
    if filters.get("sort_by") == "distance":
        assert shops[0]["distance_m"] == pytest.approx(0, abs=1)
        assert 1000 < shops[1]["distance_m"] < 3000
        assert shops[2]["distance_m"] > 1_000_000


@pytest.mark.parametrize("mode", ["walking", "driving"])
def test_route_preserves_service_geometry_and_metrics(monkeypatch, mode):
    original = httpx.AsyncClient
    def handle(request):
        assert request.url.path == f"/v3/direction/{mode}"
        assert request.url.params["origin"] == "121.47,31.23"
        assert request.url.params["destination"] == "121.49,31.23"
        return httpx.Response(200, json={"route": {"paths": [{"distance": "2500", "duration": "1800",
            "steps": [{"polyline": "121.47,31.23;121.48,31.24;121.49,31.23"}]}]}})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(handle), **kwargs))
    tool = RoutePlanTool(AMapConfig("synthetic-key", "https://fixture.invalid", 1))
    route = asyncio.run(tool.plan_route(provider="amap", mode=mode,
        origin=Location(lng=121.47, lat=31.23), destination=Location(lng=121.49, lat=31.23)))
    assert route.mode == mode and route.distance_m == 2500 and route.duration_s == 1800
    assert [(point.lng, point.lat) for point in route.polyline] == [
        (121.47, 31.23), (121.48, 31.24), (121.49, 31.23)]
    assert all(point.coord_system == "gcj02" for point in route.polyline)


@pytest.mark.parametrize("body", [{"status": "0", "info": "INVALID_USER_KEY"}, {"route": {"paths": []}}])
def test_service_failure_never_returns_estimated_route(monkeypatch, body):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)), **kwargs))
    tool = RoutePlanTool(AMapConfig("synthetic-key", "https://fixture.invalid", 1))
    with pytest.raises(RuntimeError, match="route_unavailable"):
        asyncio.run(tool.plan_route(provider="amap", mode="walking",
            origin=Location(lng=121.47, lat=31.23), destination=Location(lng=121.49, lat=31.23)))
