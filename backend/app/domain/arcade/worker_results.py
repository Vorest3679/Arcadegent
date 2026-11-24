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

# Scalar/bookkeeping fields a worker inherits from its parent at mount. Workers
# may update them through tools; promotion only writes back the ones the worker
# actually wrote (see ``worker_memory_promotions``).
_WORKER_SCALAR_KEYS = (
    "last_request",
    "last_shop_id",
    "keyword",
    "last_db_query",
    "provider",
    "last_route_endpoints",
)
# Reserved worker-memory key with the values inherited at mount. It lets a late
# commit distinguish "worker changed this" from "worker merely inherited the
# parent's old snapshot"; without it a worker that never queried would restore
# the parent's stale value over a sibling worker's newer one.
_WORKER_MOUNT_BASELINE_KEY = "_worker_mount_baseline"
# Scalar keys the worker wrote after mount. A write of the same value still
# counts: a fresh query that happens to match the snapshot must stay consistent
# with the artifacts it produced.
_WORKER_WRITTEN_KEYS = "_worker_written_keys"
# Artifacts copied into a worker at mount time. Route/destination/view_payload
# are deliberately absent: a worker must plan them itself.
_WORKER_SNAPSHOT_KEYS = (
    "shop",
    "shops",
    "selected_shops",
    "total",
    "resolved_locations",
    "client_location",
)
# The envelope is the worker's tool result for the parent model. Full shop rows
# and routes already live in the parent's artifacts (promoted by
# ``ArtifactSink``) and reach the model through ``context_payload``; the
# envelope only names them. Each shop is listed once, with these fields.
_ENVELOPE_SHOP_FIELDS = (
    "source_id",
    "name",
    "province_name",
    "city_name",
    "county_name",
    "address",
    "arcade_count",
    "distance_m",
)
_ENVELOPE_SHOP_LIMIT = 10
_ENVELOPE_ROUTE_FIELDS = ("provider", "mode", "distance_m", "duration_s", "hint")


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
    baseline: dict[str, Any] = {}
    for key in _WORKER_SCALAR_KEYS:
        if key in parent_memory:
            value = deepcopy(parent_memory[key])
            memory[key] = value
            baseline[key] = deepcopy(value)
    if baseline:
        memory[_WORKER_MOUNT_BASELINE_KEY] = baseline
        memory[_WORKER_WRITTEN_KEYS] = []
    for key in _WORKER_SNAPSHOT_KEYS:
        value = get_working_memory_artifact(parent_memory, key)
        if value is not None:
            set_working_memory_artifact(memory, key, value)
    return memory


def mark_worker_memory_write(worker_memory: dict[str, Any], key: str) -> None:
    """Record a scalar field this worker actually wrote after mount.

    The helper is a no-op outside a mounted worker (the parent has no baseline),
    so domain tool-result handlers can mark writes unconditionally.
    """
    if not isinstance(worker_memory.get(_WORKER_MOUNT_BASELINE_KEY), dict):
        return
    written = worker_memory.get(_WORKER_WRITTEN_KEYS)
    if not isinstance(written, list):
        written = []
    if key not in written:
        written.append(key)
        worker_memory[_WORKER_WRITTEN_KEYS] = written


def _changed_since_mount(
    worker_memory: dict[str, Any],
    key: str,
    baseline: dict[str, Any],
    written: set[str],
) -> bool:
    """Whether the worker wrote the value or its snapshot value differs."""
    if key in written or key not in baseline:
        return True
    return worker_memory.get(key) != baseline[key]


def worker_memory_promotions(worker_memory: dict[str, Any]) -> dict[str, Any]:
    """Non-artifact worker fields that should reach the parent.

    Artifact values are merged generically by ``ArtifactSink`` from their
    metadata; these scalar/bookkeeping fields are arcade memory and stay here.
    Values the worker inherited from its mount snapshot are skipped, so a
    worker that never ran the matching tool cannot overwrite a sibling's newer
    value with the parent's old state. Worker memories created without a
    snapshot (direct tests or future callers) keep promoting their values.
    """
    baseline = worker_memory.get(_WORKER_MOUNT_BASELINE_KEY)
    if not isinstance(baseline, dict):
        baseline = {}
    written_raw = worker_memory.get(_WORKER_WRITTEN_KEYS)
    written: set[str] = set()
    if isinstance(written_raw, list):
        written = {key for key in written_raw if isinstance(key, str)}
    promoted: dict[str, Any] = {}
    if _changed_since_mount(worker_memory, "last_db_query", baseline, written) and isinstance(
        worker_memory.get("last_db_query"), dict
    ):
        promoted["last_db_query"] = deepcopy(worker_memory["last_db_query"])
    if _changed_since_mount(worker_memory, "provider", baseline, written) and isinstance(
        worker_memory.get("provider"), str
    ):
        promoted["provider"] = worker_memory["provider"]
    if _changed_since_mount(worker_memory, "last_route_endpoints", baseline, written) and isinstance(
        worker_memory.get("last_route_endpoints"), dict
    ):
        promoted["last_route_endpoints"] = deepcopy(worker_memory["last_route_endpoints"])
    if _changed_since_mount(worker_memory, "keyword", baseline, written) and isinstance(
        worker_memory.get("keyword"), str
    ):
        promoted["keyword"] = worker_memory["keyword"]
    if isinstance(worker_memory.get("last_mcp_result"), dict):
        promoted["last_mcp_result"] = deepcopy(worker_memory["last_mcp_result"])
    return promoted


def _shop_brief(row: dict[str, Any]) -> dict[str, Any]:
    """The identifying fields of one shop row; details stay in the artifacts."""
    return {key: row[key] for key in _ENVELOPE_SHOP_FIELDS if row.get(key) not in (None, "")}


def _route_brief(route: dict[str, Any]) -> dict[str, Any]:
    """Route metrics without the polyline; the map reads the full route artifact."""
    return {key: route[key] for key in _ENVELOPE_ROUTE_FIELDS if route.get(key) not in (None, "")}


def _promoted_keys(promoted_artifacts: dict[str, Any]) -> list[str]:
    """Names of the artifacts this worker committed to the parent, sorted."""
    return sorted(promoted_artifacts)


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
    """Build the worker result envelope consumed by the parent agent.

    The envelope is model input, so it stays small: each shop appears once
    with identifying fields only, routes carry metrics without polylines, and
    ``artifacts`` lists the names of what was committed rather than copying it.
    """
    committed = _promoted_keys(promoted_artifacts)
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
            "destination": _shop_brief(destination) if isinstance(destination, dict) else None,
            "route": _route_brief(route) if isinstance(route, dict) else None,
            "provider": worker_memory.get("provider") or (route.get("provider") if isinstance(route, dict) else None),
        }
        return {
            "worker": worker_name,
            "run_id": run_id,
            "status": status,
            "summary": summary,
            "result": result,
            "artifacts": committed,
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
        "total": total,
        # The committed cards, in display order; ``total`` counts all matches.
        "shops": [_shop_brief(row) for row in displayed_shops[:_ENVELOPE_SHOP_LIMIT]],
        "query": {key: value for key, value in query.items() if value not in (None, "")} if query else None,
    }
    return {
        "worker": worker_name,
        "run_id": run_id,
        "status": status,
        "summary": summary,
        "result": result,
        "artifacts": committed,
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
