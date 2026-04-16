"""Executor for the builtin arcade search tool."""

from __future__ import annotations

import re
from typing import Any

from app.agent.tools.builtin.executor_utils import as_region_code_or_name, short_text
from app.agent.tools.builtin.provider import BuiltinToolContext
from app.infra.observability.logger import get_logger

logger = get_logger(__name__)


def _memory_artifact(memory: dict[str, Any], key: str) -> Any:
    artifacts = memory.get("artifacts")
    if isinstance(artifacts, dict) and key in artifacts:
        return artifacts.get(key)
    return memory.get(key)


def _is_nearby_search_request(memory: dict[str, Any]) -> bool:
    last_request = memory.get("last_request")
    if not isinstance(last_request, dict):
        return False
    if last_request.get("intent") == "search_nearby":
        return True
    message = str(last_request.get("message") or "").lower()
    return bool(re.search(r"附近|最近|nearby|nearest|near me|near ", message))


def prepare_arguments(raw_arguments: dict[str, Any], runtime_context: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    args = dict(raw_arguments)
    hydrated: list[str] = []
    location = _memory_artifact(runtime_context, "client_location")
    if not isinstance(location, dict):
        return args, hydrated

    current_sort = args.get("sort_by")
    normalized_sort = str(current_sort or "").strip().lower()
    if _is_nearby_search_request(runtime_context) and normalized_sort in {"", "default"}:
        args["sort_by"] = "distance"
        normalized_sort = "distance"
        hydrated.append("sort_by")

    if normalized_sort != "distance":
        return args, hydrated

    if args.get("sort_order") is None:
        args["sort_order"] = "asc"
        hydrated.append("sort_order")
    if args.get("origin_lng") is None and location.get("lng") is not None:
        args["origin_lng"] = location.get("lng")
        hydrated.append("origin_lng")
    if args.get("origin_lat") is None and location.get("lat") is not None:
        args["origin_lat"] = location.get("lat")
        hydrated.append("origin_lat")
    if args.get("origin_coord_system") is None:
        args["origin_coord_system"] = "wgs84"
        hydrated.append("origin_coord_system")
    return args, hydrated


def execute(context: BuiltinToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Normalize region filters and execute the store-backed shop query."""
    tool = context.require("db_query_tool")

    shop_id = args.get("shop_id")
    if shop_id is not None:
        return {"shop": tool.get_shop(shop_id)}

    province_code, province_name = as_region_code_or_name(
        args.get("province_code"),
        args.get("province_name"),
    )
    city_code, city_name = as_region_code_or_name(
        args.get("city_code"),
        args.get("city_name"),
    )
    county_code, county_name = as_region_code_or_name(
        args.get("county_code"),
        args.get("county_name"),
    )

    sort_by = str(args.get("sort_by") or "default")
    sort_order = str(args.get("sort_order") or "desc")
    sort_title_name = args.get("sort_title_name")
    origin_lng = args.get("origin_lng")
    origin_lat = args.get("origin_lat")
    origin_coord_system = str(args.get("origin_coord_system") or "wgs84")
    if sort_by == "title_quantity" and not (sort_title_name or "").strip():
        keyword = (args.get("keyword") or "").strip()
        if keyword:
            parts = [part for part in re.split(r"\s+", keyword) if part]
            if parts:
                sort_title_name = parts[-1]

    rows, total = tool.search_shops(
        keyword=args.get("keyword"),
        province_code=province_code,
        city_code=city_code,
        county_code=county_code,
        province_name=province_name,
        city_name=city_name,
        county_name=county_name,
        has_arcades=args.get("has_arcades"),
        page=int(args["page"]),
        page_size=int(args["page_size"]),
        sort_by=sort_by,
        sort_order=sort_order,
        sort_title_name=sort_title_name,
        origin_lng=origin_lng,
        origin_lat=origin_lat,
        origin_coord_system=origin_coord_system,
    )
    logger.info(
        "db_query_tool.filters keyword=%s province_code=%s city_code=%s county_code=%s province_name=%s city_name=%s county_name=%s has_arcades=%s sort_by=%s sort_order=%s sort_title_name=%s origin_lng=%s origin_lat=%s origin_coord_system=%s page=%s page_size=%s total=%s",
        short_text(args.get("keyword")),
        province_code,
        city_code,
        county_code,
        province_name,
        city_name,
        county_name,
        args.get("has_arcades"),
        sort_by,
        sort_order,
        short_text(sort_title_name),
        origin_lng,
        origin_lat,
        origin_coord_system,
        args["page"],
        args["page_size"],
        total,
    )
    return {
        "shops": rows,
        "total": total,
        "query": {
            "keyword": args.get("keyword"),
            "province_code": province_code,
            "city_code": city_code,
            "county_code": county_code,
            "province_name": province_name,
            "city_name": city_name,
            "county_name": county_name,
            "has_arcades": args.get("has_arcades"),
            "sort_by": sort_by,
            "sort_order": sort_order,
            "sort_title_name": sort_title_name,
            "origin_lng": origin_lng,
            "origin_lat": origin_lat,
            "origin_coord_system": origin_coord_system,
            "page": args["page"],
            "page_size": args["page_size"],
        },
    }
