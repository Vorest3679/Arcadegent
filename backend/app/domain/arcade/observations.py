"""Map arcade tool outputs into working-memory artifacts and intent state.

This is the stage-2 home of the former ``ReactRuntime._apply_tool_memory`` and
its turn-preparation helpers. The transitions intentionally match the previous
implementation so the stage-0 golden snapshots stay byte-identical.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.agent.runtime.domain_port import ToolResultSignal
from app.agent.runtime.lifecycle import DomainState
from app.agent.runtime.session_state import (
    ensure_working_memory_shape,
    get_working_memory_artifact,
    set_working_memory_artifact,
)
from app.agent.tools.registry import ToolExecutionResult
from app.domain.arcade.intent import IntentPolicy
from app.session.events import RunEvent


def prepare_turn_memory(memory: dict[str, Any]) -> dict[str, Any]:
    """Drop arcade artifacts that only belong to the previous turn.

    Kernel scratch keys (``reply``, ``last_error``) are reset by the runtime.
    """
    prepared = ensure_working_memory_shape(memory)
    for key in ("search_candidates",):
        prepared["artifacts"].pop(key, None)
        prepared.pop(key, None)
        prepared.get("artifact_meta", {}).pop(key, None)
    for key in ("route", "destination", "view_payload"):
        prepared["artifacts"].pop(key, None)
        prepared.pop(key, None)
        prepared.get("artifact_meta", {}).pop(key, None)
    return prepared


def append_search_candidates(
    memory: dict[str, Any],
    rows: list[Any],
    *,
    turn_index: int,
) -> None:
    """Accumulate deduplicated candidates only for the active search turn."""
    existing = get_working_memory_artifact(memory, "search_candidates")
    candidates = existing if isinstance(existing, list) else []
    by_id = {
        row.get("source_id"): row
        for row in candidates
        if isinstance(row, dict) and type(row.get("source_id")) is int
    }
    ordered = [row for row in candidates if isinstance(row, dict) and type(row.get("source_id")) is int]
    for row in rows:
        if not isinstance(row, dict) or type(row.get("source_id")) is not int:
            continue
        source_id = row["source_id"]
        if source_id not in by_id:
            ordered.append(row)
            by_id[source_id] = row
    set_working_memory_artifact(memory, "search_candidates", ordered, turn_index=turn_index)


def tool_result_signal(result: ToolExecutionResult) -> ToolResultSignal:
    """Publish the arcade route event and distance field for route outputs."""
    if result.status != "completed":
        return ToolResultSignal()
    route = result.output.get("route") if isinstance(result.output, dict) else None
    if not isinstance(route, dict):
        return ToolResultSignal()
    return ToolResultSignal(
        events=((RunEvent.NAVIGATION_ROUTE_READY, route),),
        completed_fields={"distance_m": route.get("distance_m")},
    )


def apply_tool_result(
    *,
    state: DomainState,
    result: ToolExecutionResult,
    intent_policy: IntentPolicy,
) -> None:
    """Update arcade working memory and intent from one completed tool result.

    Failure bookkeeping and the concluding ``reply`` are owned by the runtime.
    """
    if result.status != "completed":
        return
    memory = ensure_working_memory_shape(state.working_memory)

    if result.tool_name == "invoke_worker":
        envelope = result.output if isinstance(result.output, dict) else {}
        result_payload = envelope.get("result")
        if isinstance(result_payload, dict):
            destination = result_payload.get("destination")
            if isinstance(destination, dict):
                set_working_memory_artifact(memory, "destination", destination, turn_index=state.turn_index)
            route = result_payload.get("route")
            if isinstance(route, dict):
                set_working_memory_artifact(memory, "route", route, turn_index=state.turn_index)
                intent_policy.mark_navigate(state)
            view_payload = result_payload.get("view_payload")
            if isinstance(view_payload, dict):
                set_working_memory_artifact(memory, "view_payload", view_payload, turn_index=state.turn_index)
        return

    if result.tool_name == "db_query_tool":
        # A fresh query changes the answer context. Keep its candidates for
        # selection, but never carry a previous turn's displayed cards into it.
        selected_meta = memory.get("artifact_meta", {}).get("selected_shops", {})
        if selected_meta.get("turn_index") != state.turn_index:
            memory["artifacts"].pop("selected_shops", None)
            memory.get("artifact_meta", {}).pop("selected_shops", None)
        shop_payload = result.output.get("shop")
        if isinstance(shop_payload, dict):
            set_working_memory_artifact(memory, "shop", shop_payload, turn_index=state.turn_index)
            append_search_candidates(memory, [shop_payload], turn_index=state.turn_index)
            source_id = shop_payload.get("source_id")
            if source_id is not None:
                memory["last_shop_id"] = source_id
            return
        shops = result.output.get("shops")
        if isinstance(shops, list):
            set_working_memory_artifact(memory, "shops", shops, turn_index=state.turn_index)
            append_search_candidates(memory, shops, turn_index=state.turn_index)
            if shops:
                first = shops[0] if isinstance(shops[0], dict) else None
                if isinstance(first, dict) and first.get("source_id") is not None:
                    memory["last_shop_id"] = first.get("source_id")
        total = result.output.get("total")
        if total is not None:
            set_working_memory_artifact(memory, "total", int(total), turn_index=state.turn_index)
        query_meta = result.output.get("query")
        if isinstance(query_meta, dict):
            memory["last_db_query"] = query_meta
        last_request = memory.get("last_request")
        if isinstance(last_request, dict):
            memory["keyword"] = last_request.get("keyword") or intent_policy.extract_keyword(
                str(last_request.get("message") or "")
            )
        return

    if result.tool_name == "result_selection_tool":
        selected = result.output.get("shops")
        if isinstance(selected, list):
            set_working_memory_artifact(memory, "selected_shops", selected, turn_index=state.turn_index)
            if selected:
                memory["last_shop_id"] = selected[0].get("source_id")
        return

    if result.tool_name == "geo_resolve_tool":
        provider = result.output.get("provider")
        if isinstance(provider, str):
            memory["provider"] = provider
        return

    if result.tool_name == "route_plan_tool":
        route = result.output.get("route")
        if isinstance(route, dict):
            set_working_memory_artifact(memory, "route", route, turn_index=state.turn_index)
            origin = route.get("origin")
            destination_point = route.get("destination")
            if isinstance(origin, dict) and isinstance(destination_point, dict):
                memory["last_route_endpoints"] = {
                    "origin": deepcopy(origin),
                    "destination": deepcopy(destination_point),
                    "provider": route.get("provider"),
                    "mode": route.get("mode"),
                }
            destination = get_working_memory_artifact(memory, "shop")
            if isinstance(destination, dict):
                set_working_memory_artifact(memory, "destination", destination, turn_index=state.turn_index)
            intent_policy.mark_navigate(state)
        return

    if result.tool_name.startswith("mcp__"):
        route = result.output.get("route")
        if isinstance(route, dict):
            set_working_memory_artifact(memory, "route", route, turn_index=state.turn_index)
            destination = get_working_memory_artifact(memory, "shop")
            if isinstance(destination, dict):
                set_working_memory_artifact(memory, "destination", destination, turn_index=state.turn_index)
            intent_policy.mark_navigate(state)
        data = result.output.get("data")
        if isinstance(data, dict):
            locations = data.get("locations")
            if isinstance(locations, list) and locations:
                set_working_memory_artifact(memory, "resolved_locations", locations, turn_index=state.turn_index)
        memory["last_mcp_result"] = result.output
        return
