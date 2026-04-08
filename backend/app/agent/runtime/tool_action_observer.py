"""Tool action/observation coordinator extracted from ReactRuntime."""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone

from app.agent.events.replay_buffer import ReplayBuffer
from app.agent.llm.provider_adapter import ModelToolCall
from app.agent.orchestration.transition_policy import TransitionPolicy
from app.agent.runtime.session_state import AgentSessionState, AgentTurn, SessionStateStore
from app.agent.tools.registry import ToolExecutionResult, ToolRegistry
from app.infra.observability.logger import get_logger

logger = get_logger(__name__)


def _short(text: str | None, *, limit: int = 120) -> str:
    if not isinstance(text, str):
        return ""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(1, limit - 3)].rstrip()}..."


def _chunk_stream_text(text: str, *, max_chars: int = 18) -> list[str]:
    source = text if isinstance(text, str) else ""
    if not source:
        return []
    chunks: list[str] = []
    current: list[str] = []
    for char in source:
        current.append(char)
        if char in {"\n", "\u3002", "\uff01", "\uff1f", ".", "!", "?"} or len(current) >= max_chars:
            piece = "".join(current)
            if piece:
                chunks.append(piece)
            current = []
    if current:
        piece = "".join(current)
        if piece:
            chunks.append(piece)
    return chunks


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_intent(raw: str | None) -> str:
    if raw == "navigate":
        return "navigate"
    if raw == "search_nearby":
        return "search_nearby"
    return "search"


def _extract_keyword(message: str) -> str:
    text = message.strip()
    if not text:
        return ""
    latin_matches = re.findall(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,40}", text)
    if latin_matches:
        candidate = latin_matches[-1].strip()
        if " " in candidate:
            pieces = [item for item in re.split(r"\s+", candidate) if item]
            if pieces:
                candidate = pieces[-1]
        return candidate
    cleaned = re.sub(
        r"(\u5e2e\u6211\u627e|\u8bf7\u5e2e\u6211\u627e|\u5e2e\u5fd9\u627e|\u9644\u8fd1\u54ea\u91cc\u6709|\u9644\u8fd1\u6709\u6ca1\u6709|\u6709\u6ca1\u6709|\u627e\u4e00\u4e0b|\u67e5\u4e00\u4e0b|\u641c\u7d22|\u67e5\u8be2|\u673a\u5385)",
        " ",
        text,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.!?\uFF0C\u3002\uFF01\uFF1F")
    return cleaned or text


class ToolActionObserver:
    """Self-contained tool action/observation coordinator."""

    def __init__(
        self,
        *,
        tool_registry: ToolRegistry,
        transition_policy: TransitionPolicy,
        replay_buffer: ReplayBuffer,
        session_store: SessionStateStore,
    ) -> None:
        self._tool_registry = tool_registry
        self._transition_policy = transition_policy
        self._replay_buffer = replay_buffer
        self._session_store = session_store

    async def execute_tool_calls(
        self,
        *,
        session_id: str,
        state: AgentSessionState,
        tool_calls: list[ModelToolCall],
        allowed_tools: list[str],
    ) -> bool:
        terminal = False
        execution_tasks: list[asyncio.Task[ToolExecutionResult]] = []
        for call in tool_calls:
            prepared_args, hydrated_fields = self._prepare_tool_arguments(
                state=state,
                tool_name=call.name,
                raw_arguments=call.arguments,
            )
            logger.info(
                "tool.call session_id=%s tool=%s call_id=%s args=%s",
                session_id,
                call.name,
                call.call_id,
                _short(json.dumps(prepared_args, ensure_ascii=False), limit=220),
            )
            if hydrated_fields:
                logger.debug(
                    "tool.call.hydrated session_id=%s tool=%s call_id=%s fields=%s",
                    session_id,
                    call.name,
                    call.call_id,
                    hydrated_fields,
                )
            self._replay_buffer.append(
                session_id,
                "tool.started",
                {
                    "tool": call.name,
                    "call_id": call.call_id,
                    "active_subagent": state.active_subagent,
                },
            )
            execution_tasks.append(
                asyncio.create_task(
                    self._tool_registry.execute(
                        call_id=call.call_id,
                        tool_name=call.name,
                        raw_arguments=prepared_args,
                        allowed_tools=allowed_tools,
                    )
                )
            )
        results = await asyncio.gather(*execution_tasks) if execution_tasks else []
        for result in results:
            self.record_tool_result(session_id=session_id, state=state, result=result)
            if self._transition_policy.is_terminal_tool(
                tool_name=result.tool_name,
                tool_status=result.status,
            ):
                terminal = True
        return terminal

    def record_tool_result(
        self,
        *,
        session_id: str,
        state: AgentSessionState,
        result: ToolExecutionResult,
    ) -> None:
        previous_subagent = state.active_subagent
        if result.status == "completed":
            completed_payload: dict[str, object] = {
                "tool": result.tool_name,
                "call_id": result.call_id,
                "active_subagent": previous_subagent,
            }
            route = result.output.get("route")
            if isinstance(route, dict):
                completed_payload["distance_m"] = route.get("distance_m")
                self._replay_buffer.append(session_id, "navigation.route_ready", route)
            self._replay_buffer.append(session_id, "tool.completed", completed_payload)
            if result.tool_name == "db_query_tool":
                total = int(result.output.get("total") or 0)
                logger.info(
                    "tool.completed session_id=%s tool=%s total=%s",
                    session_id,
                    result.tool_name,
                    total,
                )
            else:
                logger.info(
                    "tool.completed session_id=%s tool=%s",
                    session_id,
                    result.tool_name,
                )
            logger.debug(
                "tool.observe session_id=%s tool=%s status=%s output_keys=%s output_preview=%s",
                session_id,
                result.tool_name,
                result.status,
                sorted(list(result.output.keys())),
                self._tool_output_preview(result.output),
            )
        else:
            error_message = result.error_message
            if not isinstance(error_message, str) or not error_message:
                error_message = "tool execution failed"
            self._replay_buffer.append(
                session_id,
                "tool.failed",
                {
                    "tool": result.tool_name,
                    "call_id": result.call_id,
                    "error": error_message,
                    "active_subagent": previous_subagent,
                },
            )
            logger.warning(
                "tool.failed session_id=%s tool=%s error=%s",
                session_id,
                result.tool_name,
                _short(error_message, limit=160),
            )
            logger.debug(
                "tool.observe session_id=%s tool=%s status=%s output_keys=%s output_preview=%s",
                session_id,
                result.tool_name,
                result.status,
                sorted(list(result.output.keys())),
                self._tool_output_preview(result.output),
            )

        self._append_turn(
            state,
            AgentTurn(
                role="tool",
                name=result.tool_name,
                call_id=result.call_id,
                content=json.dumps(result.output, ensure_ascii=False),
                payload={"status": result.status, "result": result.output},
            ),
        )
        self._apply_tool_memory(state=state, result=result)
        if result.status == "completed" and result.tool_name == "summary_tool":
            reply = result.output.get("reply")
            if isinstance(reply, str) and reply.strip() and not bool(state.working_memory.get("assistant_token_emitted")):
                self._emit_assistant_tokens(
                    session_id=session_id,
                    active_subagent=previous_subagent,
                    text=reply.strip(),
                )
                state.working_memory["assistant_token_emitted"] = True
        next_subagent = self._transition_policy.next_subagent(
            current_subagent=state.active_subagent,
            tool_name=result.tool_name,
            tool_status=result.status,
            tool_output=result.output,
            fallback_intent=state.intent,
            has_route=bool(state.working_memory.get("route")),
            has_shops=bool(state.working_memory.get("shops"))
            or bool(state.working_memory.get("shop")),
        )
        state.active_subagent = next_subagent
        shops_payload = state.working_memory.get("shops")
        shops_count = len(shops_payload) if isinstance(shops_payload, list) else 0
        logger.debug(
            "tool.memory session_id=%s tool=%s status=%s has_shop=%s shops=%s total=%s has_route=%s has_reply=%s next_subagent=%s",
            session_id,
            result.tool_name,
            result.status,
            isinstance(state.working_memory.get("shop"), dict),
            shops_count,
            state.working_memory.get("total"),
            bool(state.working_memory.get("route")),
            bool(str(state.working_memory.get("reply") or "").strip()),
            state.active_subagent,
        )
        if previous_subagent != state.active_subagent:
            self._emit_subagent_changed(
                session_id=session_id,
                from_subagent=previous_subagent,
                to_subagent=state.active_subagent,
                reason="tool.transition",
                tool_name=result.tool_name,
                tool_status=result.status,
            )
            logger.debug(
                "chat.transition session_id=%s from=%s tool=%s status=%s to=%s",
                session_id,
                previous_subagent,
                result.tool_name,
                result.status,
                state.active_subagent,
            )
        self._session_store.save(state)

    def _prepare_tool_arguments(
        self,
        *,
        state: AgentSessionState,
        tool_name: str,
        raw_arguments: dict[str, object],
    ) -> tuple[dict[str, object], list[str]]:
        if tool_name != "summary_tool":
            return dict(raw_arguments), []

        args = dict(raw_arguments)
        hydrated: list[str] = []

        topic = args.get("topic")
        if topic not in {"search", "navigation"}:
            inferred_topic = "navigation" if bool(state.working_memory.get("route")) else "search"
            args["topic"] = inferred_topic
            topic = inferred_topic
            hydrated.append("topic")

        if topic == "navigation":
            if not isinstance(args.get("route"), dict):
                route = state.working_memory.get("route")
                if isinstance(route, dict):
                    args["route"] = route
                    hydrated.append("route")
            shop_name = args.get("shop_name")
            if not isinstance(shop_name, str) or not shop_name.strip():
                shop_value = state.working_memory.get("shop")
                candidate_name: str | None = None
                if isinstance(shop_value, dict):
                    name = shop_value.get("name")
                    if isinstance(name, str) and name.strip():
                        candidate_name = name.strip()
                if candidate_name is None:
                    shops_value = state.working_memory.get("shops")
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
            total = state.working_memory.get("total")
            if isinstance(total, int):
                args["total"] = total
                hydrated.append("total")
        if not isinstance(args.get("shops"), list):
            shops = state.working_memory.get("shops")
            if isinstance(shops, list):
                args["shops"] = shops
                hydrated.append("shops")
        query_meta = state.working_memory.get("last_db_query")
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
            memory_keyword = state.working_memory.get("keyword")
            if isinstance(memory_keyword, str) and memory_keyword.strip():
                args["keyword"] = memory_keyword.strip()
                hydrated.append("keyword")
        return args, hydrated

    def _apply_tool_memory(self, *, state: AgentSessionState, result: ToolExecutionResult) -> None:
        if result.tool_name == "select_next_subagent" and result.status == "completed":
            next_subagent = result.output.get("next_subagent")
            if isinstance(next_subagent, str) and next_subagent:
                state.working_memory["next_subagent_candidate"] = next_subagent
            next_intent = result.output.get("intent")
            if isinstance(next_intent, str):
                state.intent = _normalize_intent(next_intent)
            done = result.output.get("done")
            if isinstance(done, bool):
                state.working_memory["subagent_done"] = done
            return

        if result.status != "completed":
            state.working_memory["last_error"] = result.output.get("error")
            return

        if result.tool_name == "db_query_tool":
            shop_payload = result.output.get("shop")
            if isinstance(shop_payload, dict):
                state.working_memory["shop"] = shop_payload
                source_id = shop_payload.get("source_id")
                if source_id is not None:
                    state.working_memory["last_shop_id"] = source_id
                return
            shops = result.output.get("shops")
            if isinstance(shops, list):
                state.working_memory["shops"] = shops
                if shops:
                    first = shops[0] if isinstance(shops[0], dict) else None
                    if isinstance(first, dict) and first.get("source_id") is not None:
                        state.working_memory["last_shop_id"] = first.get("source_id")
            total = result.output.get("total")
            if total is not None:
                state.working_memory["total"] = int(total)
            query_meta = result.output.get("query")
            if isinstance(query_meta, dict):
                state.working_memory["last_db_query"] = query_meta
            last_request = state.working_memory.get("last_request")
            if isinstance(last_request, dict):
                state.working_memory["keyword"] = last_request.get("keyword") or _extract_keyword(
                    str(last_request.get("message") or "")
                )
            return

        if result.tool_name == "geo_resolve_tool":
            provider = result.output.get("provider")
            if isinstance(provider, str):
                state.working_memory["provider"] = provider
            return

        if result.tool_name == "route_plan_tool":
            route = result.output.get("route")
            if isinstance(route, dict):
                state.working_memory["route"] = route
                state.intent = "navigate"
            return

        if result.tool_name.startswith("mcp__"):
            route = result.output.get("route")
            if isinstance(route, dict):
                state.working_memory["route"] = route
                state.intent = "navigate"
            data = result.output.get("data")
            if isinstance(data, dict):
                locations = data.get("locations")
                if isinstance(locations, list) and locations:
                    state.working_memory["resolved_locations"] = locations
            state.working_memory["last_mcp_result"] = result.output
            return

        if result.tool_name == "summary_tool":
            reply = result.output.get("reply")
            if isinstance(reply, str) and reply.strip():
                state.working_memory["reply"] = reply.strip()
            return

    def _tool_output_preview(self, output: dict[str, object]) -> str:
        if not output:
            return "{}"
        total = output.get("total")
        if isinstance(total, int):
            shops = output.get("shops")
            if isinstance(shops, list):
                return f"total={total},shops={len(shops)}"
            return f"total={total}"
        reply = output.get("reply")
        if isinstance(reply, str):
            return f"reply={_short(reply, limit=120)}"
        route = output.get("route")
        if isinstance(route, dict):
            distance = route.get("distance_m")
            duration = route.get("duration_s")
            return f"route(distance_m={distance},duration_s={duration})"
        data = output.get("data")
        if isinstance(data, dict):
            locations = data.get("locations")
            if isinstance(locations, list):
                return f"locations={len(locations)}"
        provider = output.get("provider")
        if isinstance(provider, str):
            return f"provider={provider}"
        return _short(json.dumps(output, ensure_ascii=False), limit=220)

    def _emit_subagent_changed(
        self,
        *,
        session_id: str,
        to_subagent: str,
        reason: str,
        from_subagent: str | None = None,
        tool_name: str | None = None,
        tool_status: str | None = None,
    ) -> None:
        payload: dict[str, object] = {
            "active_subagent": to_subagent,
            "to_subagent": to_subagent,
            "reason": reason,
        }
        if from_subagent:
            payload["from_subagent"] = from_subagent
        if tool_name:
            payload["tool"] = tool_name
        if tool_status:
            payload["tool_status"] = tool_status
        self._replay_buffer.append(session_id, "subagent.changed", payload)

    def emit_session_subagent_started(self, *, session_id: str, to_subagent: str) -> None:
        self._emit_subagent_changed(
            session_id=session_id,
            to_subagent=to_subagent,
            reason="session.started",
        )

    def _emit_assistant_tokens(
        self,
        *,
        session_id: str,
        active_subagent: str,
        text: str,
    ) -> None:
        chunks = _chunk_stream_text(text)
        if not chunks:
            return
        total = len(chunks)
        merged = ""
        for idx, chunk in enumerate(chunks, start=1):
            merged += chunk
            self._replay_buffer.append(
                session_id,
                "assistant.token",
                {
                    "delta": chunk,
                    "content": merged,
                    "index": idx,
                    "total": total,
                    "active_subagent": active_subagent,
                    "text_preview": _short(merged, limit=120),
                },
            )

    def _append_turn(self, state: AgentSessionState, turn: AgentTurn) -> None:
        state.turns.append(turn)
        state.updated_at = _utc_now_iso()
