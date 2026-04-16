"""Executor for the builtin deterministic summary tool."""

from __future__ import annotations

from typing import Any

from app.agent.tools.builtin.provider import BuiltinToolContext
from app.protocol.messages import RouteSummaryDto


def _memory_artifact(memory: dict[str, Any], key: str) -> Any:
    artifacts = memory.get("artifacts")
    if isinstance(artifacts, dict) and key in artifacts:
        return artifacts.get(key)
    return memory.get(key)


def prepare_arguments(raw_arguments: dict[str, Any], runtime_context: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    args = dict(raw_arguments)
    hydrated: list[str] = []

    topic = args.get("topic")
    if topic not in {"search", "navigation"}:
        inferred_topic = "navigation" if bool(_memory_artifact(runtime_context, "route")) else "search"
        args["topic"] = inferred_topic
        topic = inferred_topic
        hydrated.append("topic")

    if topic == "navigation":
        if not isinstance(args.get("route"), dict):
            route = _memory_artifact(runtime_context, "route")
            if isinstance(route, dict):
                args["route"] = route
                hydrated.append("route")
        shop_name = args.get("shop_name")
        if not isinstance(shop_name, str) or not shop_name.strip():
            shop_value = _memory_artifact(runtime_context, "shop")
            candidate_name: str | None = None
            if isinstance(shop_value, dict):
                name = shop_value.get("name")
                if isinstance(name, str) and name.strip():
                    candidate_name = name.strip()
            if candidate_name is None:
                shops_value = _memory_artifact(runtime_context, "shops")
                if isinstance(shops_value, list) and shops_value:
                    first = shops_value[0]
                    if isinstance(first, dict):
                        name = first.get("name")
                        if isinstance(name, str) and name.strip():
                            candidate_name = name.strip()
            if candidate_name is not None:
                args["shop_name"] = candidate_name
                hydrated.append("shop_name")
        return args, hydrated

    if args.get("total") is None:
        total = _memory_artifact(runtime_context, "total")
        if isinstance(total, int):
            args["total"] = total
            hydrated.append("total")
    if not isinstance(args.get("shops"), list):
        shops = _memory_artifact(runtime_context, "shops")
        if isinstance(shops, list):
            args["shops"] = shops
            hydrated.append("shops")
    query_meta = runtime_context.get("last_db_query")
    if isinstance(query_meta, dict):
        query_sort_by = query_meta.get("sort_by")
        query_sort_order = query_meta.get("sort_order")
        query_sort_title_name = query_meta.get("sort_title_name")

        if isinstance(query_sort_by, str) and query_sort_by.strip().lower() == "title_quantity":
            if args.get("sort_by") != "title_quantity":
                args["sort_by"] = "title_quantity"
                hydrated.append("sort_by")
            if isinstance(query_sort_order, str) and query_sort_order.strip():
                if args.get("sort_order") != query_sort_order.strip():
                    args["sort_order"] = query_sort_order.strip()
                    hydrated.append("sort_order")
            if isinstance(query_sort_title_name, str) and query_sort_title_name.strip():
                if args.get("sort_title_name") != query_sort_title_name.strip():
                    args["sort_title_name"] = query_sort_title_name.strip()
                    hydrated.append("sort_title_name")
        else:
            if args.get("sort_by") is None and isinstance(query_sort_by, str) and query_sort_by.strip():
                args["sort_by"] = query_sort_by.strip()
                hydrated.append("sort_by")
            if args.get("sort_order") is None and isinstance(query_sort_order, str) and query_sort_order.strip():
                args["sort_order"] = query_sort_order.strip()
                hydrated.append("sort_order")
            if (
                args.get("sort_title_name") is None
                and isinstance(query_sort_title_name, str)
                and query_sort_title_name.strip()
            ):
                args["sort_title_name"] = query_sort_title_name.strip()
                hydrated.append("sort_title_name")
    keyword = args.get("keyword")
    if not isinstance(keyword, str) or not keyword.strip():
        memory_keyword = runtime_context.get("keyword")
        if isinstance(memory_keyword, str) and memory_keyword.strip():
            args["keyword"] = memory_keyword.strip()
            hydrated.append("keyword")
    return args, hydrated


def execute(context: BuiltinToolContext, args: dict[str, Any]) -> dict[str, str]:
    """Format either search or navigation context into a stable reply string."""
    tool = context.require("summary_tool")
    if args["topic"] == "navigation":
        route_payload = args.get("route") or {}
        route = RouteSummaryDto.model_validate(route_payload)
        reply = tool.summarize_navigation(
            shop_name=args.get("shop_name") or "target arcade",
            route=route,
        )
    else:
        reply = tool.summarize_search(
            keyword=args.get("keyword"),
            total=int(args.get("total") or 0),
            shops=args.get("shops") or [],
            sort_by=args.get("sort_by"),
            sort_order=args.get("sort_order"),
            sort_title_name=args.get("sort_title_name"),
        )
    return {"reply": reply}
