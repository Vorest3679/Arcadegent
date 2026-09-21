"""Route planning argument preparation and provider behavior."""

import asyncio
from pathlib import Path

import pytest

from app.agent.tools.builtin.route_plan_tool import RoutePlanTool
from app.protocol.messages import Location
from backend.app.tests.unit._tool_registry_test_support import (
    _build_mcp_gateway,
    _build_registry,
    _run,
    _write_rows,
)


def test_route_plan_tool_prefers_amap_mcp_when_available(tmp_path: Path) -> None:
    registry = _build_registry(tmp_path, mcp_tool_gateway=_build_mcp_gateway())

    result = _run(registry.execute(
        call_id="c7",
        tool_name="route_plan_tool",
        raw_arguments={
            "provider": "amap",
            "mode": "walking",
            "origin": {"lng": 116.3, "lat": 39.9},
            "destination": {"lng": 116.4, "lat": 39.91},
        },
        allowed_tools=["route_plan_tool", "mcp__*"],
    ))

    assert result.status == "completed"
    assert result.output["route"]["provider"] == "amap"
    assert result.output["route"]["distance_m"] == 1234
    assert result.output["route"]["duration_s"] == 678

def test_amap_mcp_does_not_use_walking_tool_for_driving() -> None:
    gateway = _build_mcp_gateway()
    route = _run(gateway.plan_amap_route(
        mode="driving",
        origin=Location(lng=116.3, lat=39.9),
        destination=Location(lng=116.4, lat=39.91),
    ))
    assert route is None

def test_route_plan_arguments_bind_named_shop_candidates(tmp_path: Path) -> None:
    registry = _build_registry(tmp_path, mcp_tool_gateway=_build_mcp_gateway())
    memory = {
        "provider": "amap",
        "artifacts": {
            "search_candidates": [
                {"source_id": 1, "name": "街机烈火", "longitude_gcj02": 121.455483, "latitude_gcj02": 31.229618},
                {"source_id": 6, "name": "风云再起上海人民广场店", "longitude_gcj02": 121.473024, "latitude_gcj02": 31.228048},
            ]
        },
    }

    prepared, hydrated = _run(registry.prepare_arguments(
        tool_name="route_plan_tool",
        raw_arguments={
            "provider": "amap",
            "mode": "walking",
            "origin": "上海市街机烈火机厅",
            "destination": "风云再起上海人民广场店",
        },
        runtime_context=memory,
    ))

    assert hydrated == ["origin", "destination"]
    assert prepared["origin"] == {"lng": 121.455483, "lat": 31.229618}
    assert prepared["destination"] == {"lng": 121.473024, "lat": 31.228048}
    result = _run(registry.execute(
        call_id="named-route",
        tool_name="route_plan_tool",
        raw_arguments=prepared,
        allowed_tools=["route_plan_tool"],
    ))
    assert result.status == "completed"
    assert result.output["route"]["origin"]["lng"] == 121.455483
    assert result.output["route"]["destination"]["lng"] == 121.473024

def test_route_plan_converts_browser_wgs84_origin_to_gcj02(tmp_path: Path) -> None:
    registry = _build_registry(tmp_path, mcp_tool_gateway=_build_mcp_gateway())
    browser_wgs84 = {"lng": 121.473024, "lat": 31.228048, "accuracy_m": 25}

    prepared, hydrated = _run(registry.prepare_arguments(
        tool_name="route_plan_tool",
        raw_arguments={
            "provider": "amap",
            "mode": "walking",
            "origin": {"lng": browser_wgs84["lng"], "lat": browser_wgs84["lat"]},
            "destination": {"lng": 121.473495, "lat": 31.228154},
        },
        runtime_context={"artifacts": {"client_location": browser_wgs84}},
    ))

    assert hydrated == ["origin"]
    assert prepared["origin"]["lng"] > browser_wgs84["lng"] + 0.004
    assert prepared["origin"]["lat"] < browser_wgs84["lat"]
    assert prepared["destination"] == {"lng": 121.473495, "lat": 31.228154}

def test_route_plan_arguments_reuse_endpoints_for_mode_switch(tmp_path: Path) -> None:
    registry = _build_registry(tmp_path, mcp_tool_gateway=_build_mcp_gateway())
    prepared, hydrated = _run(registry.prepare_arguments(
        tool_name="route_plan_tool",
        raw_arguments={"provider": "amap", "mode": "driving"},
        runtime_context={
            "last_request": {"message": "临时改开车了，起点终点都不变，帮我换一下路线。"},
            "last_route_endpoints": {
                "origin": {"lng": 121.455483, "lat": 31.229618},
                "destination": {"lng": 121.473024, "lat": 31.228048},
            },
        },
    ))

    assert hydrated == ["origin", "destination"]
    assert prepared["mode"] == "driving"
    assert prepared["origin"]["lng"] == 121.455483
    assert prepared["destination"]["lng"] == 121.473024

def test_route_json_string_coordinates_are_prepared_before_validation(tmp_path: Path) -> None:
    registry = _build_registry(tmp_path, mcp_tool_gateway=_build_mcp_gateway())
    prepared, hydrated = _run(registry.prepare_arguments(
        tool_name="route_plan_tool",
        raw_arguments={"provider": "amap", "mode": "walking",
                       "origin": '{"lng":121.455483,"lat":31.229618}',
                       "destination": '{"lng":121.473024,"lat":31.228048}'},
        runtime_context={},
    ))
    assert hydrated == ["origin", "destination"]
    result = _run(registry.execute(call_id="json-points", tool_name="route_plan_tool",
                                   raw_arguments=prepared, allowed_tools=["route_plan_tool"]))
    assert result.status == "completed"
    assert result.output["route"]["origin"]["lng"] == 121.455483

def test_route_malformed_json_coordinates_remain_invalid(tmp_path: Path) -> None:
    registry = _build_registry(tmp_path)
    prepared, _ = _run(registry.prepare_arguments(
        tool_name="route_plan_tool",
        raw_arguments={"provider": "amap", "mode": "walking",
                       "origin": '{"lng":true,"lat":31}',
                       "destination": '{broken'}, runtime_context={},
    ))
    result = _run(registry.execute(call_id="bad-points", tool_name="route_plan_tool",
                                   raw_arguments=prepared, allowed_tools=["route_plan_tool"]))
    assert result.status == "failed"
    assert result.output["error"]["type"] == "validation_error"

def test_amap_failure_diagnostics_do_not_expose_key(monkeypatch):
    import httpx
    import pytest
    from app.agent.tools.builtin.route_plan_tool import AMapConfig

    original = httpx.AsyncClient
    for body, expected in [({"status": "0", "infocode": "10001"}, "amap_api_error_10001"),
                           ({"status": "0", "infocode": "secret-key"}, "amap_api_error_unknown")]:
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)), **kwargs))
        with pytest.raises(RuntimeError, match=expected) as error:
            _run(RoutePlanTool(AMapConfig("secret-key", "https://route.invalid", 1)).plan_route(
                provider="amap", mode="walking", origin=Location(lng=121, lat=31),
                destination=Location(lng=122, lat=31)))
        assert "secret-key" not in str(error.value)

    def fail(request):
        raise httpx.ConnectError(str(request.url), request=request)

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(fail), **kwargs))
    with pytest.raises(RuntimeError, match="amap_transport_ConnectError") as error:
        _run(RoutePlanTool(AMapConfig("secret-key", "https://route.invalid", 1)).plan_route(
            provider="amap", mode="walking", origin=Location(lng=121, lat=31),
            destination=Location(lng=122, lat=31)))
    assert "secret-key" not in str(error.value)

@pytest.mark.parametrize("mode", ["walking", "driving"])
def test_route_uses_explicit_settings_not_process_env(tmp_path, monkeypatch, mode):
    import asyncio
    import httpx
    from app.core.config import Settings
    from app.core.container import build_container

    data_path = tmp_path / "shops.jsonl"
    _write_rows(data_path)
    monkeypatch.setenv("AMAP_API_KEY", "wrong-process-key")
    monkeypatch.setenv("AMAP_BASE_URL", "https://wrong.invalid")
    (tmp_path / "no-mcp").mkdir()
    settings = Settings(data_jsonl_path=data_path,
                              supabase_url="https://fixture.invalid",
                              supabase_service_role_key="test-key",
                              mcp_servers_dir=tmp_path / "no-mcp",
                              amap_api_key="settings-key", amap_base_url="https://route.invalid",
                              amap_timeout_seconds=3)
    container = build_container(settings)
    original = httpx.AsyncClient

    def handle(request):
        assert request.url.host == "route.invalid"
        assert request.url.path == f"/v3/direction/{mode}"
        assert request.url.params["key"] == "settings-key"
        assert request.url.params["origin"] == "121.455483,31.229618"
        assert request.url.params["destination"] == "121.473024,31.228048"
        return httpx.Response(200, json={"status": "1", "route": {"paths": [
            {"distance": "2000", "duration": "1500", "steps": [
                {"polyline": "121.455483,31.229618;121.473024,31.228048"}]}]}})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(handle), **kwargs))
    result = asyncio.run(container.tool_registry.execute(
        call_id="route", tool_name="route_plan_tool", allowed_tools=["route_plan_tool"],
        raw_arguments={"provider": "amap", "mode": mode,
                       "origin": {"lng":121.455483,"lat":31.229618},
                       "destination": {"lng":121.473024,"lat":31.228048}}))
    assert result.status == "completed"
    assert result.output["route"]["distance_m"] == 2000
    assert len(result.output["route"]["polyline"]) == 2
