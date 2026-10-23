"""Arcade fallback replies, display projection and API response shaping."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from typing import Any

from app.agent.runtime.session_state import AgentSessionState, get_working_memory_artifact
from app.domain.arcade.intent import IntentPolicy
from app.protocol.messages import ChatRequest, ChatResponse
from app.services.arcade_payload_mapper import ArcadePayloadMapper


def memory_shops(memory: dict[str, Any]) -> list[dict[str, Any]]:
    """Merge the list artifact with a single detail artifact, deduplicated by id."""
    shops_raw: list[dict[str, Any]] = []
    memory_shops_artifact = get_working_memory_artifact(memory, "shops")
    if isinstance(memory_shops_artifact, list):
        shops_raw.extend(item for item in memory_shops_artifact if isinstance(item, dict))
    memory_shop = get_working_memory_artifact(memory, "shop")
    if isinstance(memory_shop, dict):
        source_id = memory_shop.get("source_id")
        exists = any(item.get("source_id") == source_id for item in shops_raw)
        if not exists:
            shops_raw.append(memory_shop)
    return shops_raw


def display_shops(memory: dict[str, Any]) -> list[dict[str, Any]]:
    """Return explicitly committed cards, with legacy candidate fallback.

    ``selected_shops`` is authoritative even when it is an empty list. The
    fallback keeps direct/legacy callers functional while all worker search
    paths now commit selection through result_selection_tool.
    """
    selected = get_working_memory_artifact(memory, "selected_shops")
    if isinstance(selected, list):
        return [item for item in selected if isinstance(item, dict)]
    return memory_shops(memory)


def snapshot_turn_map_artifacts(state: AgentSessionState) -> dict[str, Any] | None:
    """Archive only map artifacts produced by the current conversation turn."""
    memory = state.working_memory
    meta = memory.get("artifact_meta")
    if not isinstance(meta, dict):
        return None
    display_keys = ("shop", "shops", "selected_shops", "route", "destination", "view_payload")
    has_fresh_display_artifact = any(
        isinstance(meta.get(key), dict) and meta[key].get("turn_index") == state.turn_index
        for key in display_keys
    )
    if not has_fresh_display_artifact:
        return None

    destination = get_working_memory_artifact(memory, "destination")
    if not isinstance(destination, dict):
        destination = get_working_memory_artifact(memory, "shop")
    route = get_working_memory_artifact(memory, "route")
    client_location = get_working_memory_artifact(memory, "client_location")
    view_payload = get_working_memory_artifact(memory, "view_payload")
    return deepcopy({
        "shops": display_shops(memory),
        "route": route if isinstance(route, dict) else None,
        "client_location": client_location if isinstance(client_location, dict) else None,
        "destination": destination if isinstance(destination, dict) else None,
        "view_payload": view_payload if isinstance(view_payload, dict) else None,
    })


def fallback_reply(
    state: AgentSessionState,
    request: ChatRequest,
    *,
    intent_policy: IntentPolicy,
) -> str:
    """Fallback reply to guarantee API always returns text."""
    reply = state.working_memory.get("reply")
    if isinstance(reply, str) and reply.strip():
        return reply.strip()

    if intent_policy.normalize(state.intent) == "navigate":
        if get_working_memory_artifact(state.working_memory, "route"):
            return "路线已经准备好了，但总结环节没有产出完整文本，请重试一次。"
        if request.shop_id is None and state.working_memory.get("last_shop_id") is None:
            return "请再明确一下起点和目标地点，我会继续规划路线。"
        return "导航流程还没有完成，请再试一次。"

    shops_payload = display_shops(state.working_memory)
    if shops_payload:
        top = shops_payload[0]
        return f"我已经找到匹配机厅，先看 {top.get('name') or 'unknown arcade'}。"
    last_error = state.working_memory.get("last_error")
    if isinstance(last_error, dict):
        message = last_error.get("message")
        if isinstance(message, str) and message.strip():
            return f"请求已处理，但工具执行失败：{message.strip()}"
    keyword = str(state.working_memory.get("keyword") or "").strip()
    if keyword:
        return f"已收到请求，但暂时没有找到和“{keyword}”相关的结果，可以换个关键词试试。"
    return "已收到请求，但暂时没有足够结果，可以换个关键词或区域再试试。"


async def build_response(
    *,
    payload_mapper: ArcadePayloadMapper | None,
    session_id: str,
    state: AgentSessionState,
    final_text: str,
    intent_policy: IntentPolicy,
) -> ChatResponse:
    """Build API response from memory-level shop and route payloads."""
    if payload_mapper is None:
        raise RuntimeError("arcade payload mapper is not configured")
    raw_shops = display_shops(state.working_memory)[:20]
    shops = await asyncio.to_thread(payload_mapper.summaries_from_rows, raw_shops)
    route_obj = payload_mapper.route_from_payload(
        get_working_memory_artifact(state.working_memory, "route")
    )

    intent = intent_policy.normalize(state.intent)
    if route_obj is not None:
        intent = "navigate"
    return ChatResponse(
        session_id=session_id,
        intent=intent,
        reply=final_text,
        shops=shops,
        route=route_obj,
    )


def turn_summary(state: AgentSessionState, *, intent_policy: IntentPolicy) -> str:
    """Short domain summary for runtime logs; contains no request or reply text."""
    return (
        f"intent={intent_policy.normalize(state.intent)} "
        f"shops={len(memory_shops(state.working_memory))}"
    )
