"""Database query, filtering, sorting, and selection behavior."""

import json
from pathlib import Path

from app.agent.tools.builtin import BuiltinToolProvider
from app.agent.tools.mcp_gateway import MCPToolGateway
from app.agent.tools.permission import ToolPermissionChecker
from app.agent.tools.registry import ToolRegistry
from app.infra.db.local import LocalArcadeStore
from backend.app.tests.unit._tool_registry_test_support import _build_registry, _run


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
