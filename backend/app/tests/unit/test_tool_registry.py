"""Unit tests for tool registry validation and dispatch behavior."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastmcp import FastMCP

from app.agent.tools.builtin import BuiltinToolProvider
from app.agent.tools.builtin.route_plan_tool import RoutePlanTool
from app.agent.tools.mcp_gateway import MCPServerConfig, MCPToolGateway
from app.agent.tools.permission import ToolPermissionChecker
from app.agent.tools.registry import ToolRegistry, _is_strict_compatible
from app.infra.db.local import LocalArcadeStore
from app.protocol.messages import Location


def _run(awaitable):
    return asyncio.run(awaitable)


def _write_rows(path: Path) -> None:
    rows = [
        {
            "source": "bemanicn",
            "source_id": 1,
            "source_url": "https://map.bemanicn.com/s/1",
            "name": "Alpha Arcade",
            "province_code": "110000000000",
            "province_name": "Beijing",
            "city_code": "110100000000",
            "city_name": "Beijing",
            "county_code": "110101000000",
            "county_name": "Dongcheng",
            "arcades": [{"title_name": "maimai", "quantity": 2}],
        }
    ]
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")


def _build_registry(
    tmp_path: Path,
    *,
    mcp_tool_gateway: MCPToolGateway | None = None,
) -> ToolRegistry:
    data_path = tmp_path / "shops.jsonl"
    _write_rows(data_path)
    store = LocalArcadeStore.from_jsonl(data_path)
    gateway = mcp_tool_gateway or MCPToolGateway()
    return ToolRegistry(
        providers=[
            BuiltinToolProvider(
                runtime_services={
                    "store": store,
                    "mcp_tool_gateway": gateway,
                }
            ),
            gateway,
        ],
        permission_checker=ToolPermissionChecker(policy_file=tmp_path / "missing.yaml"),
        strict_schema=True,
    )


def _build_mcp_gateway() -> MCPToolGateway:
    mcp = FastMCP("Test AMap MCP")

    @mcp.tool(name="maps_direction_walking", description="步行路径规划，输入 origin 和 destination，输出 paths。")
    def maps_direction_walking(origin: str, destination: str) -> dict[str, object]:
        return {
            "origin": origin,
            "destination": destination,
            "paths": [
                {
                    "distance": 1234,
                    "duration": 678,
                    "steps": [
                        {
                            "instruction": "walk forward",
                            "polyline": "116.3,39.9;116.4,39.91",
                        }
                    ],
                }
            ],
        }

    gateway = MCPToolGateway(
        servers=[
            MCPServerConfig(
                name="amap",
                enabled=True,
                source=mcp,
                url="memory://amap",
                timeout_seconds=3,
                route_tool_name="maps_direction_walking",
            )
        ]
    )
    _run(gateway.refresh())
    return gateway


def test_strict_compatibility_check_requires_closed_objects() -> None:
    assert _is_strict_compatible(
        {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
            "additionalProperties": False,
        }
    )
    # Missing properties in required -> not strict compatible.
    assert not _is_strict_compatible(
        {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": [],
            "additionalProperties": False,
        }
    )
    # Open additionalProperties -> not strict compatible.
    assert not _is_strict_compatible(
        {
            "type": "object",
            "properties": {"a": {"type": "string"}},
            "required": ["a"],
        }
    )
    # Nested objects must also be closed.
    assert not _is_strict_compatible(
        {
            "type": "object",
            "properties": {
                "a": {
                    "type": "object",
                    "properties": {"b": {"type": "string"}},
                    "required": ["b"],
                }
            },
            "required": ["a"],
            "additionalProperties": False,
        }
    )


def test_tool_definitions_do_not_claim_strict_for_loose_business_schema(tmp_path: Path) -> None:
    registry = _build_registry(tmp_path)

    definitions = _run(registry.tool_definitions(allowed_tools=["db_query_tool"]))

    assert definitions
    function = definitions[0]["function"]
    # The business schema keeps its own shape; only the remote strict flag is gated.
    assert len(function["parameters"]["properties"]) > 2
    assert function["strict"] is False


def test_route_plan_unavailable_fails_without_estimate() -> None:
    import pytest
    with pytest.raises(RuntimeError, match="route_unavailable"):
        _run(RoutePlanTool().plan_route(provider="amap", mode="walking",
            origin=Location(lng=116.3, lat=39.9), destination=Location(lng=116.4, lat=39.91)))


def test_tool_registry_returns_validation_error_for_bad_args(tmp_path: Path) -> None:
    registry = _build_registry(tmp_path)
    result = _run(registry.execute(
        call_id="c1",
        tool_name="route_plan_tool",
        raw_arguments={
            "provider": "amap",
            "mode": "walking",
            "origin": {"lng": 116.3, "lat": 39.9},
            "destination": {"lng": 116.4},
        },
        allowed_tools=["route_plan_tool"],
    ))
    assert result.status == "failed"
    assert result.output["error"]["type"] == "validation_error"


def test_tool_registry_can_lookup_one_shop(tmp_path: Path) -> None:
    registry = _build_registry(tmp_path)
    result = _run(registry.execute(
        call_id="c2",
        tool_name="db_query_tool",
        raw_arguments={
            "keyword": None,
            "province_code": None,
            "city_code": None,
            "county_code": None,
            "has_arcades": None,
            "page": 1,
            "page_size": 1,
            "shop_id": 1,
        },
        allowed_tools=["db_query_tool"],
    ))
    assert result.status == "completed"
    assert result.output["shop"]["source_id"] == 1


def test_tool_registry_normalizes_city_name_in_city_code_field(tmp_path: Path) -> None:
    registry = _build_registry(tmp_path)
    result = _run(registry.execute(
        call_id="c3",
        tool_name="db_query_tool",
        raw_arguments={
            "keyword": "maimai",
            "province_code": None,
            "city_code": "Beijing",
            "county_code": None,
            "province_name": None,
            "city_name": None,
            "county_name": None,
            "has_arcades": True,
            "page": 1,
            "page_size": 10,
            "shop_id": None,
        },
        allowed_tools=["db_query_tool"],
    ))
    assert result.status == "completed"
    assert result.output["total"] == 1
    assert result.output["shops"][0]["source_id"] == 1


def test_tool_registry_supports_title_quantity_sorting(tmp_path: Path) -> None:
    data_path = tmp_path / "shops_sort.jsonl"
    rows = [
        {
            "source": "bemanicn",
            "source_id": 1,
            "source_url": "https://map.bemanicn.com/s/1",
            "name": "Alpha Arcade",
            "arcades": [{"title_name": "maimai", "quantity": 1}],
        },
        {
            "source": "bemanicn",
            "source_id": 2,
            "source_url": "https://map.bemanicn.com/s/2",
            "name": "Beta Arcade",
            "arcades": [{"title_name": "maimai", "quantity": 3}],
        },
        {
            "source": "bemanicn",
            "source_id": 3,
            "source_url": "https://map.bemanicn.com/s/3",
            "name": "Gamma Arcade",
            "arcades": [{"title_name": "sdvx", "quantity": 5}],
        },
    ]
    with data_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")

    store = LocalArcadeStore.from_jsonl(data_path)
    gateway = MCPToolGateway()
    registry = ToolRegistry(
        providers=[
            BuiltinToolProvider(
                runtime_services={
                    "store": store,
                    "mcp_tool_gateway": gateway,
                }
            ),
            gateway,
        ],
        permission_checker=ToolPermissionChecker(policy_file=tmp_path / "missing.yaml"),
        strict_schema=True,
    )
    result = _run(registry.execute(
        call_id="c4",
        tool_name="db_query_tool",
        raw_arguments={
            "keyword": None,
            "has_arcades": True,
            "sort_by": "title_quantity",
            "sort_order": "desc",
            "sort_title_name": "maimai",
            "page": 1,
            "page_size": 10,
        },
        allowed_tools=["db_query_tool"],
    ))
    assert result.status == "completed"
    assert result.output["total"] == 3
    assert [row["source_id"] for row in result.output["shops"]] == [2, 1, 3]
    assert result.output["query"]["sort_by"] == "title_quantity"
    assert result.output["query"]["sort_order"] == "desc"
    assert result.output["query"]["sort_title_name"] == "maimai"


def test_tool_registry_backfills_sort_title_name_from_keyword(tmp_path: Path) -> None:
    data_path = tmp_path / "shops_sort_keyword.jsonl"
    rows = [
        {
            "source": "bemanicn",
            "source_id": 1,
            "source_url": "https://map.bemanicn.com/s/1",
            "name": "Alpha Arcade",
            "arcades": [{"title_name": "maimai DX", "quantity": 2}],
        },
        {
            "source": "bemanicn",
            "source_id": 2,
            "source_url": "https://map.bemanicn.com/s/2",
            "name": "Beta Arcade",
            "arcades": [{"title_name": "maimai DX", "quantity": 5}],
        },
    ]
    with data_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")

    store = LocalArcadeStore.from_jsonl(data_path)
    gateway = MCPToolGateway()
    registry = ToolRegistry(
        providers=[
            BuiltinToolProvider(
                runtime_services={
                    "store": store,
                    "mcp_tool_gateway": gateway,
                }
            ),
            gateway,
        ],
        permission_checker=ToolPermissionChecker(policy_file=tmp_path / "missing.yaml"),
        strict_schema=True,
    )
    result = _run(registry.execute(
        call_id="c5",
        tool_name="db_query_tool",
        raw_arguments={
            "keyword": "maimai",
            "has_arcades": True,
            "sort_by": "title_quantity",
            "sort_order": "desc",
            "sort_title_name": None,
            "page": 1,
            "page_size": 10,
        },
        allowed_tools=["db_query_tool"],
    ))
    assert result.status == "completed"
    assert [row["source_id"] for row in result.output["shops"]] == [2, 1]
    assert result.output["query"]["sort_title_name"] == "maimai"


def test_tool_registry_supports_distance_sorting(tmp_path: Path) -> None:
    data_path = tmp_path / "shops_distance.jsonl"
    rows = [
        {
            "source": "bemanicn",
            "source_id": 1,
            "source_url": "https://map.bemanicn.com/s/1",
            "name": "Near Arcade",
            "longitude_wgs84": 116.397428,
            "latitude_wgs84": 39.90923,
            "arcades": [{"title_name": "maimai", "quantity": 1}],
        },
        {
            "source": "bemanicn",
            "source_id": 2,
            "source_url": "https://map.bemanicn.com/s/2",
            "name": "Far Arcade",
            "longitude_wgs84": 116.407428,
            "latitude_wgs84": 39.91923,
            "arcades": [{"title_name": "maimai", "quantity": 1}],
        },
    ]
    with data_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")

    store = LocalArcadeStore.from_jsonl(data_path)
    gateway = MCPToolGateway()
    registry = ToolRegistry(
        providers=[
            BuiltinToolProvider(
                runtime_services={
                    "store": store,
                    "mcp_tool_gateway": gateway,
                }
            ),
            gateway,
        ],
        permission_checker=ToolPermissionChecker(policy_file=tmp_path / "missing.yaml"),
        strict_schema=True,
    )
    result = _run(registry.execute(
        call_id="c_distance",
        tool_name="db_query_tool",
        raw_arguments={
            "has_arcades": True,
            "sort_by": "distance",
            "sort_order": "asc",
            "origin_lng": 116.397428,
            "origin_lat": 39.90923,
            "origin_coord_system": "wgs84",
            "page": 1,
            "page_size": 10,
        },
        allowed_tools=["db_query_tool"],
    ))
    assert result.status == "completed"
    assert [row["source_id"] for row in result.output["shops"]] == [1, 2]
    assert result.output["shops"][0]["distance_m"] == 0
    assert result.output["query"]["sort_by"] == "distance"
    assert result.output["query"]["origin_coord_system"] == "wgs84"


def test_tool_registry_includes_discovered_mcp_tools_when_allowed(tmp_path: Path) -> None:
    registry = _build_registry(tmp_path, mcp_tool_gateway=_build_mcp_gateway())

    definitions = _run(registry.tool_definitions(allowed_tools=["route_plan_tool", "mcp__*"]))
    names = [
        item["function"]["name"]
        for item in definitions
        if isinstance(item, dict) and isinstance(item.get("function"), dict)
    ]

    assert "route_plan_tool" in names
    assert "mcp__amap__maps_direction_walking" in names


def test_tool_registry_gettools_aggregates_builtin_and_mcp_tools(tmp_path: Path) -> None:
    registry = _build_registry(tmp_path, mcp_tool_gateway=_build_mcp_gateway())

    tools = _run(registry.gettools())

    assert "db_query_tool" in tools
    assert "result_selection_tool" in tools
    assert tools["db_query_tool"].provider == "builtin"
    assert "mcp__amap__maps_direction_walking" in tools
    assert tools["mcp__amap__maps_direction_walking"].provider == "mcp"
    assert tools["summary_tool"].metadata["prompt"].endswith("response_composition.md")


def test_result_selection_tool_resolves_only_runtime_candidates(tmp_path: Path) -> None:
    registry = _build_registry(tmp_path)
    context = {"artifacts": {"search_candidates": [{"source_id": 1, "name": "Alpha Arcade"}]}}
    prepared, hydrated = _run(registry.prepare_arguments(
        tool_name="result_selection_tool", raw_arguments={"selected_shop_ids": [1]}, runtime_context=context,
    ))
    result = _run(registry.execute(
        call_id="selection", tool_name="result_selection_tool", raw_arguments=prepared,
        allowed_tools=["result_selection_tool"],
    ))
    assert hydrated == ["selected_shops"]
    assert result.status == "completed"
    assert result.output["selected_shop_ids"] == [1]
    assert [shop["source_id"] for shop in result.output["shops"]] == [1]


def test_tool_registry_can_execute_discovered_mcp_tool(tmp_path: Path) -> None:
    registry = _build_registry(tmp_path, mcp_tool_gateway=_build_mcp_gateway())

    result = _run(registry.execute(
        call_id="c6",
        tool_name="mcp__amap__maps_direction_walking",
        raw_arguments={
            "origin": "116.3,39.9",
            "destination": "116.4,39.91",
        },
        allowed_tools=["mcp__*"],
    ))

    assert result.status == "completed"
    assert result.output["server"] == "amap"
    assert result.output["tool"] == "maps_direction_walking"
    assert result.output["route"]["distance_m"] == 1234
    assert result.output["route"]["duration_s"] == 678


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
