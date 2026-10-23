"""Arcade worker memory snapshots, artifact promotion and result envelopes."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.agent.runtime.session_state import (
    ensure_working_memory_shape,
    get_working_memory_artifact,
    set_working_memory_artifact,
)
from app.domain.arcade.replies import display_shops, memory_shops

_PROMOTABLE_KEYS = (
    "shop",
    "shops",
    "selected_shops",
    "total",
    "route",
    "resolved_locations",
    "client_location",
    "destination",
    "view_payload",
)


def _short(text: str | None, *, limit: int = 120) -> str:
    if not isinstance(text, str):
        return ""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(1, limit - 3)].rstrip()}..."


def worker_memory_snapshot(parent_memory: dict[str, Any]) -> dict[str, Any]:
    """Copy the promotable slice of parent memory into an isolated worker."""
    memory = ensure_working_memory_shape({})
    parent_memory = ensure_working_memory_shape(parent_memory)
    for key in ("last_request", "last_shop_id", "keyword", "last_db_query", "provider", "last_route_endpoints"):
        if key in parent_memory:
            memory[key] = deepcopy(parent_memory[key])
    for key in _PROMOTABLE_KEYS:
        value = get_working_memory_artifact(parent_memory, key)
        if value is not None:
            set_working_memory_artifact(memory, key, value)
    for key in ("route", "destination", "view_payload"):
        memory["artifacts"].pop(key, None)
    return memory


def promote_worker_artifacts(
    *,
    parent_memory: dict[str, Any],
    worker_memory: dict[str, Any],
    turn_index: int,
) -> dict[str, Any]:
    """Merge a finished worker's artifacts and supporting memory into the parent."""
    promoted: dict[str, Any] = {}
    for key in _PROMOTABLE_KEYS:
        value = get_working_memory_artifact(worker_memory, key)
        if key not in worker_memory.get("artifact_meta", {}):
            continue
        if value is None:
            continue
        set_working_memory_artifact(parent_memory, key, value, turn_index=turn_index)
        promoted[key] = deepcopy(value)
    if isinstance(worker_memory.get("last_db_query"), dict):
        parent_memory["last_db_query"] = deepcopy(worker_memory["last_db_query"])
    if isinstance(worker_memory.get("provider"), str):
        parent_memory["provider"] = worker_memory["provider"]
    if isinstance(worker_memory.get("last_route_endpoints"), dict):
        parent_memory["last_route_endpoints"] = deepcopy(worker_memory["last_route_endpoints"])
    if isinstance(worker_memory.get("keyword"), str):
        parent_memory["keyword"] = worker_memory["keyword"]
    if isinstance(worker_memory.get("last_mcp_result"), dict):
        parent_memory["last_mcp_result"] = deepcopy(worker_memory["last_mcp_result"])
    return promoted


def build_worker_envelope(
    *,
    worker_name: str,
    run_id: str,
    task: str,
    worker_memory: dict[str, Any],
    final_text: str | None,
    promoted_artifacts: dict[str, Any],
    failed_error: str | None,
) -> dict[str, Any]:
    """Build the worker result envelope consumed by the parent agent."""
    if worker_name == "navigation_worker":
        destination = get_working_memory_artifact(worker_memory, "shop")
        if not isinstance(destination, dict):
            shops = display_shops(worker_memory)
            if isinstance(shops, list) and shops and isinstance(shops[0], dict):
                destination = shops[0]
        route = get_working_memory_artifact(worker_memory, "route")
        missing_fields: list[str] = []
        if not isinstance(destination, dict):
            missing_fields.append("destination")
        if not isinstance(route, dict) and not missing_fields:
            missing_fields.append("route")
        status = "failed" if failed_error else "completed"
        if missing_fields and status != "failed":
            status = "needs_input"
        summary = _build_navigation_worker_summary(
            destination=destination if isinstance(destination, dict) else None,
            route=route if isinstance(route, dict) else None,
            final_text=final_text,
            error=failed_error,
        )
        result = {
            "summary": summary,
            "destination": destination if isinstance(destination, dict) else None,
            "route": route if isinstance(route, dict) else None,
            "provider": worker_memory.get("provider") or (route.get("provider") if isinstance(route, dict) else None),
            "needs_clarification": bool(missing_fields),
            "missing_fields": missing_fields,
        }
        return {
            "worker": worker_name,
            "run_id": run_id,
            "status": status,
            "summary": summary,
            "result": result,
            "artifacts": promoted_artifacts,
            "missing_fields": missing_fields,
            "error": failed_error if status == "failed" else None,
            "task": task,
            "task_preview": _short(task, limit=120),
        }

    fresh_search = any(key in promoted_artifacts for key in ("shop", "shops", "total"))
    if not fresh_search:
        worker_memory = deepcopy(worker_memory)
        for key in ("shop", "shops", "total"):
            worker_memory.get("artifacts", {}).pop(key, None)
            worker_memory.pop(key, None)
        worker_memory.pop("last_db_query", None)
    candidate_shops = memory_shops(worker_memory)
    displayed_shops = display_shops(worker_memory)
    selected_shop = displayed_shops[0] if displayed_shops else None
    total_raw = get_working_memory_artifact(worker_memory, "total")
    total = int(total_raw) if isinstance(total_raw, int) else len(candidate_shops)
    query = worker_memory.get("last_db_query") if isinstance(worker_memory.get("last_db_query"), dict) else None
    missing_fields = []
    if not fresh_search:
        missing_fields.append("search_result")
    if total <= 0 and not query and not str(worker_memory.get("keyword") or "").strip():
        missing_fields.append("keyword")
    status = "failed" if failed_error else "completed"
    if missing_fields and status != "failed":
        status = "needs_input"
    summary = _build_search_worker_summary(
        total=total,
        shops=displayed_shops,
        final_text=final_text,
        error=failed_error,
    )
    result = {
        "summary": summary,
        "total": total,
        "shops": displayed_shops,
        "candidates": candidate_shops,
        "selected_shops": displayed_shops,
        "selected_shop": selected_shop if isinstance(selected_shop, dict) else None,
        "query": query,
        "needs_clarification": bool(missing_fields),
        "missing_fields": missing_fields,
    }
    return {
        "worker": worker_name,
        "run_id": run_id,
        "status": status,
        "summary": summary,
        "result": result,
        "artifacts": promoted_artifacts,
        "missing_fields": missing_fields,
        "error": failed_error if status == "failed" else None,
        "task": task,
        "task_preview": _short(task, limit=120),
    }


def _build_search_worker_summary(
    *,
    total: int,
    shops: list[dict[str, Any]],
    final_text: str | None,
    error: str | None,
) -> str:
    if isinstance(final_text, str) and final_text.strip():
        return final_text.strip()
    if error:
        return f"Search worker stopped after an error: {error}"
    if total <= 0:
        return "Search worker found no matching arcades for the current filters."
    top_name = str(shops[0].get("name") or "unknown arcade") if shops else "unknown arcade"
    return f"Search worker found {total} candidate arcades. Top result: {top_name}."


def _build_navigation_worker_summary(
    *,
    destination: dict[str, Any] | None,
    route: dict[str, Any] | None,
    final_text: str | None,
    error: str | None,
) -> str:
    if isinstance(final_text, str) and final_text.strip():
        return final_text.strip()
    if error:
        return f"Navigation worker stopped after an error: {error}"
    if route is None:
        return "Navigation worker could not finish route planning with the current inputs."
    destination_name = str((destination or {}).get("name") or "target arcade")
    mode = str(route.get("mode") or "route")
    distance = route.get("distance_m")
    duration = route.get("duration_s")
    return (
        f"Navigation worker prepared a {mode} route to {destination_name}"
        f" (distance_m={distance}, duration_s={duration})."
    )
