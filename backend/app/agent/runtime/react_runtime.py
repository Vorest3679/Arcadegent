"""ReAct runtime: model tool-loop with session-level state accumulation."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from uuid import uuid4

from app.agent.context.context_builder import ContextBuilder
from app.agent.events.replay_buffer import ReplayBuffer
from app.agent.llm.provider_adapter import ProviderAdapter
from app.agent.orchestration.transition_policy import TransitionPolicy
from app.agent.runtime.loop_guard import LoopGuard
from app.agent.runtime.session_state import AgentSessionState, AgentTurn, SessionStateStore
from app.agent.runtime.tool_action_observer import ToolActionObserver
from app.agent.subagents.subagent_builder import SubAgentBuilder
from app.agent.tools.registry import ToolRegistry
from app.infra.observability.logger import get_logger
from app.protocol.messages import (
    ArcadeShopSummaryDto,
    ChatRequest,
    ChatResponse,
    IntentType,
    RouteSummaryDto,
)

logger = get_logger(__name__)


def _infer_intent(message: str) -> IntentType:
    """Fallback intent inference aligned with provider adapter behavior."""
    text = message.strip().lower()
    if re.search(r"\u5bfc\u822a|\u8def\u7ebf|\u600e\u4e48\u53bb|how to go|route|go to", text):
        return "navigate"
    if re.search(r"\u9644\u8fd1|nearby|near", text):
        return "search_nearby"
    return "search"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_intent(raw: str | None) -> IntentType:
    if raw == "navigate":
        return "navigate"
    if raw == "search_nearby":
        return "search_nearby"
    return "search"


def _extract_keyword(message: str) -> str:
    # Extract a usable DB keyword from user message.
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


def _short(text: str | None, *, limit: int = 120) -> str:
    if not isinstance(text, str):
        return ""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(1, limit - 3)].rstrip()}..."


def _chunk_stream_text(text: str, *, max_chars: int = 18) -> list[str]:
    """Split final reply into stable SSE chunks to avoid per-char event flooding."""
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


def _summary_row(raw: dict) -> ArcadeShopSummaryDto:
    """Map internal shop payload to API summary DTO."""
    return ArcadeShopSummaryDto(
        source=str(raw.get("source") or ""),
        source_id=int(raw.get("source_id") or 0),
        source_url=str(raw.get("source_url") or ""),
        name=str(raw.get("name") or "unknown arcade"),
        name_pinyin=raw.get("name_pinyin"),
        address=raw.get("address"),
        transport=raw.get("transport"),
        province_code=raw.get("province_code"),
        province_name=raw.get("province_name"),
        city_code=raw.get("city_code"),
        city_name=raw.get("city_name"),
        county_code=raw.get("county_code"),
        county_name=raw.get("county_name"),
        status=raw.get("status"),
        type=raw.get("type"),
        pay_type=raw.get("pay_type"),
        locked=raw.get("locked"),
        ea_status=raw.get("ea_status"),
        price=raw.get("price"),
        start_time=raw.get("start_time"),
        end_time=raw.get("end_time"),
        fav_count=raw.get("fav_count"),
        updated_at=raw.get("updated_at"),
        arcade_count=int(raw.get("arcade_count") or 0),
    )


class ReactRuntime:
    """Function-calling ReAct orchestrator with session persistence."""

    def __init__(
        self,
        *,
        context_builder: ContextBuilder,
        subagent_builder: SubAgentBuilder,
        tool_registry: ToolRegistry,
        provider_adapter: ProviderAdapter,
        session_store: SessionStateStore,
        transition_policy: TransitionPolicy,
        replay_buffer: ReplayBuffer,
        max_steps: int,
    ) -> None:
        self._context_builder = context_builder
        self._subagent_builder = subagent_builder
        self._tool_registry = tool_registry
        self._provider_adapter = provider_adapter
        self._session_store = session_store
        self._replay_buffer = replay_buffer
        self._max_steps = max(2, max_steps)
        self._tool_action_observer = ToolActionObserver(
            tool_registry=tool_registry,
            transition_policy=transition_policy,
            replay_buffer=replay_buffer,
            session_store=session_store,
        )

    def prepare_session(self, session_id: str) -> None:
        """Clear stale stream events and mark the session as running for a fresh turn."""
        state = self._session_store.get_or_create(session_id)
        state.status = "running"
        state.last_error = None
        state.updated_at = _utc_now_iso()
        self._session_store.save(state)
        self._replay_buffer.reset(session_id)

    async def run_chat(self, request: ChatRequest) -> ChatResponse:
        """Session-aware chat execution with multi-turn tool loop."""
        session_id = request.session_id or f"s_{uuid4().hex[:12]}"
        state = self._session_store.get_or_create(session_id)
        state.status = "running"
        state.last_error = None
        state.updated_at = _utc_now_iso()
        self._session_store.save(state)
        try:
            return await self._run_chat_session(request=request, session_id=session_id, state=state)
        except Exception as exc:
            error_message = _short(f"{type(exc).__name__}: {exc}", limit=280) if str(exc) else type(exc).__name__
            state.status = "failed"
            state.last_error = error_message
            state.working_memory["last_error"] = {"message": error_message}
            state.updated_at = _utc_now_iso()
            self._session_store.save(state)
            self._replay_buffer.append(
                session_id,
                "session.failed",
                {
                    "error": error_message,
                    "active_subagent": state.active_subagent,
                },
            )
            logger.exception(
                "chat.failed session_id=%s active_subagent=%s",
                session_id,
                state.active_subagent,
            )
            raise

    async def _run_chat_session(
        self,
        *,
        request: ChatRequest,
        session_id: str,
        state: AgentSessionState,
    ) -> ChatResponse:
        state.turn_index += 1

        inferred_intent = request.intent or _infer_intent(request.message)
        if request.intent is not None:
            state.intent = request.intent
        elif inferred_intent in {"navigate", "search_nearby"}:
            state.intent = inferred_intent
        elif not state.intent:
            state.intent = inferred_intent

        state.active_subagent = "intent_router"

        request_payload = request.model_dump(mode="json")
        state.working_memory["last_request"] = request_payload
        if request.location is not None:
            state.working_memory["client_location"] = request.location.model_dump(
                mode="json",
                exclude_none=True,
            )
        if request.shop_id is not None:
            state.working_memory["last_shop_id"] = request.shop_id
        if request.keyword:
            state.working_memory["keyword"] = request.keyword
        else:
            state.working_memory["keyword"] = _extract_keyword(request.message)
        state.working_memory["assistant_token_emitted"] = False
        logger.info(
            "chat.start session_id=%s turn_index=%s intent=%s keyword=%s message=%s",
            session_id,
            state.turn_index,
            state.intent,
            _short(str(state.working_memory.get("keyword") or ""), limit=48),
            _short(request.message, limit=140),
        )

        self._append_turn(
            state,
            AgentTurn(
                role="user",
                content=request.message,
                payload=request_payload,
            ),
        )
        self._replay_buffer.append(
            session_id,
            "session.started",
            {
                "intent": state.intent,
                "model": "react-runtime",
                "active_subagent": state.active_subagent,
            },
        )
        self._tool_action_observer.emit_session_subagent_started(
            session_id=session_id,
            to_subagent=state.active_subagent,
        )

        guard = LoopGuard(self._max_steps)
        final_text: str | None = None
        # ReAct loop: context -> model -> tools -> memory -> next turn.
        while not guard.exhausted:
            step = guard.next()
            subagent = self._subagent_builder.get(state.active_subagent)
            context = self._context_builder.build(
                session_state=state,
                request=request,
                subagent=subagent,
            )
            tool_message_count = sum(
                1
                for message in context.messages
                if isinstance(message, dict) and str(message.get("role")) == "tool"
            )
            shops_payload = state.working_memory.get("shops")
            shops_count = len(shops_payload) if isinstance(shops_payload, list) else 0
            route_ready = bool(state.working_memory.get("route"))
            logger.debug(
                "chat.context session_id=%s step=%s subagent=%s allowed_tools=%s message_count=%s tool_messages=%s memory_shops=%s memory_total=%s memory_route=%s",
                session_id,
                step,
                state.active_subagent,
                subagent.allowed_tools,
                len(context.messages),
                tool_message_count,
                shops_count,
                state.working_memory.get("total"),
                route_ready,
            )
            if state.active_subagent == "summary_agent" and shops_count > 0 and tool_message_count <= 0:
                logger.warning(
                    "summary.context.missing_tool_messages session_id=%s step=%s shops=%s total=%s message_count=%s",
                    session_id,
                    step,
                    shops_count,
                    state.working_memory.get("total"),
                    len(context.messages),
                )
            model_response = await self._provider_adapter.complete(
                instructions=context.instructions,
                messages=context.messages,
                tools=await self._tool_registry.tool_definitions(allowed_tools=subagent.allowed_tools),
                runtime_hints={
                    "active_subagent": state.active_subagent,
                    "intent": state.intent,
                    "request": request_payload,
                    "memory": state.working_memory,
                },
            )
            if model_response.response_id:
                state.previous_response_id = model_response.response_id
            logger.info(
                "chat.step session_id=%s step=%s subagent=%s tool_calls=%s has_text=%s",
                session_id,
                step,
                state.active_subagent,
                len(model_response.tool_calls),
                bool(model_response.text),
            )

            if model_response.tool_calls:
                terminal_after_tools = await self._tool_action_observer.execute_tool_calls(
                    session_id=session_id,
                    state=state,
                    tool_calls=model_response.tool_calls,
                    allowed_tools=subagent.allowed_tools,
                )
                if terminal_after_tools:
                    final_text = str(state.working_memory.get("reply") or "")
                    break
                continue

            if model_response.text:
                final_text = model_response.text
                break

            if state.working_memory.get("reply"):
                final_text = str(state.working_memory.get("reply"))
                break

        if not final_text:
            logger.warning(
                "chat.fallback session_id=%s reason=empty_model_output last_error=%s",
                session_id,
                _short(str(state.working_memory.get("last_error") or ""), limit=180),
            )
            final_text = self._fallback_reply(state, request)

        if not bool(state.working_memory.get("assistant_token_emitted")):
            self._emit_assistant_tokens(
                session_id=session_id,
                text=final_text,
                active_subagent=state.active_subagent,
            )
        self._append_turn(
            state,
            AgentTurn(
                role="assistant",
                content=final_text,
                payload={"final": True},
            ),
        )
        state.status = "completed"
        state.last_error = None
        state.working_memory["reply"] = final_text
        state.updated_at = _utc_now_iso()
        self._session_store.save(state)
        self._replay_buffer.append(
            session_id,
            "assistant.completed",
            {
                "reply": final_text,
                "active_subagent": state.active_subagent,
            },
        )
        logger.info(
            "chat.done session_id=%s intent=%s shops=%s reply=%s",
            session_id,
            _normalize_intent(state.intent),
            len(state.working_memory.get("shops") or []),
            _short(final_text, limit=160),
        )
        return self._build_response(session_id=session_id, state=state, final_text=final_text)

    def _fallback_reply(self, state: AgentSessionState, request: ChatRequest) -> str:
        """Fallback reply to guarantee API always returns text."""
        reply = state.working_memory.get("reply")
        if isinstance(reply, str) and reply.strip():
            return reply.strip()

        if _normalize_intent(state.intent) == "navigate":
            if state.working_memory.get("route"):
                return "route is ready, retry once to get a complete summary."
            if request.shop_id is None and state.working_memory.get("last_shop_id") is None:
                return "please provide shop_id before asking for navigation."
            return "navigation flow is incomplete, please retry."

        shops_payload = state.working_memory.get("shops")
        if isinstance(shops_payload, list) and shops_payload:
            top = shops_payload[0] if isinstance(shops_payload[0], dict) else None
            if isinstance(top, dict):
                return f"matched arcades found, start with {top.get('name') or 'unknown arcade'}."
        last_error = state.working_memory.get("last_error")
        if isinstance(last_error, dict):
            message = last_error.get("message")
            if isinstance(message, str) and message.strip():
                return f"request processed but tool failed: {message.strip()}"
        keyword = str(state.working_memory.get("keyword") or "").strip()
        if keyword:
            return f"request received but no sufficient result for '{keyword}', try another keyword."
        return "request received but no sufficient result, try another keyword."

    def _build_response(self, *, session_id: str, state: AgentSessionState, final_text: str) -> ChatResponse:
        """Build API response from memory-level shop and route payloads."""
        shops_raw: list[dict] = []
        memory_shops = state.working_memory.get("shops")
        if isinstance(memory_shops, list):
            shops_raw.extend(item for item in memory_shops if isinstance(item, dict))
        memory_shop = state.working_memory.get("shop")
        if isinstance(memory_shop, dict):
            source_id = memory_shop.get("source_id")
            exists = any(item.get("source_id") == source_id for item in shops_raw)
            if not exists:
                shops_raw.append(memory_shop)
        shops = [_summary_row(row) for row in shops_raw[:20]]

        route_obj: RouteSummaryDto | None = None
        memory_route = state.working_memory.get("route")
        if isinstance(memory_route, dict):
            try:
                route_obj = RouteSummaryDto.model_validate(memory_route)
            except Exception:
                route_obj = None

        intent = _normalize_intent(state.intent)
        if route_obj is not None:
            intent = "navigate"
        return ChatResponse(
            session_id=session_id,
            intent=intent,
            reply=final_text,
            shops=shops,
            route=route_obj,
        )

    def _append_turn(self, state: AgentSessionState, turn: AgentTurn) -> None:
        state.turns.append(turn)
        state.updated_at = _utc_now_iso()
        self._session_store.save(state)

    def _emit_assistant_tokens(
        self,
        *,
        session_id: str,
        text: str,
        active_subagent: str,
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
