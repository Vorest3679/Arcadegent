"""Transitional tool-output mapping for the arcade domain.

Tools that describe their own artifacts (``db_query_tool``,
``result_selection_tool``, ``route_plan_tool``) no longer need a branch here:
the kernel applies their artifacts first, then this module updates the
non-artifact domain memory. Worker artifacts arrive through ``ArtifactSink``
promotion; only MCP results still go through :func:`artifacts_from_result`
until their producer carries artifacts itself.

``apply_tool_result`` is the state-change half; ``artifacts_from_result`` is the
data-producing half. Both are called for every completed result.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.agent.artifacts.model import Artifact
from app.agent.artifacts.sink import ArtifactSink
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


_LEGACY_TURN_KEYS = ("search_candidates", "route", "destination", "view_payload")


def prepare_turn_memory(
    memory: dict[str, Any],
    *,
    artifact_sink: ArtifactSink | None = None,
) -> dict[str, Any]:
    """Drop arcade artifacts whose scope ended with the previous turn.

    Kernel scratch keys (``reply``, ``last_error``) are reset by the runtime.
    Session-scoped artifacts (search results, selection, total) survive so a
    follow-up reply can still show the last map; turn-scoped route/shop/detail
    artifacts must not leak into a new answer. ``legacy_keys`` covers sessions
    saved before ``scope`` was recorded.
    """
    prepared = ensure_working_memory_shape(memory)
    if artifact_sink is not None:
        artifact_sink.begin_turn(prepared, legacy_keys=_LEGACY_TURN_KEYS)
        return prepared
    for key in _LEGACY_TURN_KEYS:
        prepared["artifacts"].pop(key, None)
        prepared.pop(key, None)
        prepared.get("artifact_meta", {}).pop(key, None)
    return prepared


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


def artifacts_from_result(
    *,
    state: DomainState,
    result: ToolExecutionResult,
) -> tuple[Artifact, ...]:
    """Transitional artifacts for MCP results (the last tool-name branch).

    Search/selection/route tools return their own artifacts and worker results
    are promoted by ``ArtifactSink``; this mapping keeps MCP route/geocode
    normalization in the domain until the MCP producer describes artifacts.
    """
    if (
        result.status != "completed"
        or not isinstance(result.output, dict)
        or not result.tool_name.startswith("mcp__")
    ):
        return ()
    output = result.output
    artifacts: list[Artifact] = []
    route = output.get("route")
    if isinstance(route, dict):
        artifacts.append(Artifact("route", route, display=True))
        destination = get_working_memory_artifact(state.working_memory, "shop")
        if isinstance(destination, dict):
            artifacts.append(Artifact("destination", destination, display=True))
    data = output.get("data")
    locations = data.get("locations") if isinstance(data, dict) else None
    if isinstance(locations, list) and locations:
        artifacts.append(Artifact("resolved_locations", locations, scope="session"))
    return tuple(artifacts)


def apply_tool_result(
    *,
    state: DomainState,
    result: ToolExecutionResult,
    intent_policy: IntentPolicy,
) -> None:
    """Update non-artifact arcade memory from one completed tool result.

    Artifacts are applied by the kernel's ``ArtifactSink`` before this hook;
    failure bookkeeping, the concluding ``reply`` and intent finalization are
    owned by the runtime/domain methods around it.
    """
    if result.status != "completed" or not isinstance(result.output, dict):
        return
    memory = ensure_working_memory_shape(state.working_memory)
    output = result.output

    if result.tool_name == "invoke_worker":
        status = output.get("status")
        worker = output.get("worker")
        if status == "needs_input" and worker == "navigation_worker":
            memory["navigation_pending"] = {"turn_index": state.turn_index}
        elif status == "completed" and worker == "navigation_worker":
            memory.pop("navigation_pending", None)
        return

    if result.tool_name == "db_query_tool":
        shop_payload = output.get("shop")
        if isinstance(shop_payload, dict):
            source_id = shop_payload.get("source_id")
            if source_id is not None:
                memory["last_shop_id"] = source_id
            return
        shops = output.get("shops")
        if isinstance(shops, list) and shops:
            first = shops[0] if isinstance(shops[0], dict) else None
            if isinstance(first, dict) and first.get("source_id") is not None:
                memory["last_shop_id"] = first.get("source_id")
        query_meta = output.get("query")
        if isinstance(query_meta, dict):
            memory["last_db_query"] = query_meta
        last_request = memory.get("last_request")
        if isinstance(last_request, dict):
            memory["keyword"] = last_request.get("keyword") or intent_policy.extract_keyword(
                str(last_request.get("message") or "")
            )
        return

    if result.tool_name == "result_selection_tool":
        selected = output.get("shops")
        if isinstance(selected, list) and selected:
            first = selected[0] if isinstance(selected[0], dict) else None
            if isinstance(first, dict) and first.get("source_id") is not None:
                memory["last_shop_id"] = first.get("source_id")
        return

    if result.tool_name == "geo_resolve_tool":
        provider = output.get("provider")
        if isinstance(provider, str):
            memory["provider"] = provider
        return

    if result.tool_name == "route_plan_tool":
        route = output.get("route")
        if isinstance(route, dict):
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
                set_working_memory_artifact(
                    memory,
                    "destination",
                    destination,
                    turn_index=state.turn_index,
                    display=True,
                    scope="turn",
                    promote=True,
                )
            memory.pop("navigation_pending", None)
        return

    if result.tool_name.startswith("mcp__"):
        memory["last_mcp_result"] = output
        if isinstance(output.get("route"), dict):
            memory.pop("navigation_pending", None)
        return
