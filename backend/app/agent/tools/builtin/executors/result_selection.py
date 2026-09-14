"""Commit a bounded, ordered set of search results for UI presentation."""

from __future__ import annotations

from typing import Any

from app.agent.tools.builtin.provider import BuiltinToolContext


def _artifact(memory: dict[str, Any], key: str) -> Any:
    artifacts = memory.get("artifacts")
    return artifacts.get(key) if isinstance(artifacts, dict) and key in artifacts else memory.get(key)


def prepare_arguments(raw_arguments: dict[str, Any], runtime_context: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Resolve IDs only from results retrieved during this turn.

    The model may choose and order IDs, but it cannot manufacture shop payloads or
    resurrect a candidate from a previous turn.
    """
    args = dict(raw_arguments)
    ids = args.get("selected_shop_ids")
    if not isinstance(ids, list) or any(type(item) is not int for item in ids):
        raise ValueError("selected_shop_ids must be an array of integers")
    if len(ids) != len(set(ids)):
        raise ValueError("selected_shop_ids must not contain duplicates")
    candidates = _artifact(runtime_context, "search_candidates")
    if not isinstance(candidates, list):
        raise ValueError("no current-turn search candidates are available")
    by_id = {
        item.get("source_id"): item
        for item in candidates
        if isinstance(item, dict) and type(item.get("source_id")) is int
    }
    unknown = [source_id for source_id in ids if source_id not in by_id]
    if unknown:
        raise ValueError(f"selected_shop_ids are not current-turn candidates: {unknown}")
    args["selected_shops"] = [by_id[source_id] for source_id in ids]
    return args, ["selected_shops"]


def execute(context: BuiltinToolContext, args: dict[str, Any]) -> dict[str, Any]:
    """Return prepared rows; runtime owns their memory lifecycle."""
    _ = context
    return {
        "selected_shop_ids": list(args["selected_shop_ids"]),
        "shops": list(args["selected_shops"]),
    }
