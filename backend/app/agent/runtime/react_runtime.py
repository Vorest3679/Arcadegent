"""Hub runtime: main-agent/worker orchestration with session-level state accumulation."""

from __future__ import annotations

import asyncio
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.agent.context.context_builder import ContextBuilder
from app.agent.events.replay_buffer import ReplayBuffer
from app.agent.llm.provider_adapter import ProviderAdapter
from app.agent.runtime.loop_guard import LoopGuard
from app.agent.runtime.session_state import (
    AgentSessionState,
    AgentTurn,
    SessionOwnershipError,
    append_worker_run,
    ensure_working_memory_shape,
    get_working_memory_artifact,
    set_working_memory_artifact,
)
from app.infra.db.protocols import SessionStateRepository
from app.agent.subagents.subagent_builder import SubAgentBuilder, SubAgentProfile
from app.agent.tools.registry import ToolExecutionResult, ToolRegistry
from app.infra.observability.logger import get_logger
from app.protocol.messages import (
    ChatRequest,
    ChatResponse,
    IntentType,
)
from app.services.arcade_payload_mapper import ArcadePayloadMapper

logger = get_logger(__name__)


def _infer_intent(message: str) -> IntentType:
    """Fallback intent inference aligned with provider adapter behavior."""
    text = message.strip().lower()
    if re.search(r"导航|路线|怎么去|how to go|route|go to", text):
        return "navigate"
    if re.search(r"附近|nearby|near", text):
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
    """Heuristic keyword extraction for working memory population and logging."""
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
        r"(帮我找|请帮我找|帮忙找|附近哪里有|附近有没有|有没有|找一下|查一下|搜索|查询|机厅)",
        " ",
        text,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,.!?，。！？")
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
        if char in {"\n", "。", "！", "？", ".", "!", "?"} or len(current) >= max_chars:
            piece = "".join(current)
            if piece:
                chunks.append(piece)
            current = []
    if current:
        piece = "".join(current)
        if piece:
            chunks.append(piece)
    return chunks


class ReactRuntime:
    """Main-agent hub runtime with synchronous worker execution."""

    def __init__(
        self,
        *,
        context_builder: ContextBuilder,
        subagent_builder: SubAgentBuilder,
        tool_registry: ToolRegistry,
        provider_adapter: ProviderAdapter,
        session_store: SessionStateRepository,
        replay_buffer: ReplayBuffer,
        arcade_payload_mapper: ArcadePayloadMapper,
        max_steps: int,
    ) -> None:
        self._context_builder = context_builder
        self._subagent_builder = subagent_builder
        self._tool_registry = tool_registry
        self._provider_adapter = provider_adapter
        self._session_store = session_store
        self._replay_buffer = replay_buffer
        self._arcade_payload_mapper = arcade_payload_mapper
        self._max_steps = max(2, max_steps)

    def prepare_session(self, session_id: str, *, client_id: str | None = None) -> None:
        """Clear stale stream events and mark the session as running for a fresh turn."""
        state = self._session_store.get_or_create_session(session_id)
        self._bind_client_scope(state, client_id)
        state.status = "running"
        state.last_error = None
        state.updated_at = _utc_now_iso()
        state.working_memory = ensure_working_memory_shape(state.working_memory)
        self._session_store.save_session(state)
        self._replay_buffer.reset(session_id)

    def cancel_session(self, session_id: str, *, reason: str) -> None:
        """Mark an interrupted background run as terminal without clearing its context."""
        state = self._session_store.get_or_create_session(session_id)
        if state.status != "running":
            return
        state.status = "failed"
        state.last_error = reason
        state.working_memory = ensure_working_memory_shape(state.working_memory)
        state.working_memory["last_error"] = {"message": reason, "source": "stream"}
        state.updated_at = _utc_now_iso()
        self._session_store.save_session(state)
        self._replay_buffer.append(
            session_id,
            "session.failed",
            {"error": reason, "active_subagent": state.active_subagent},
        )

    async def run_chat(self, request: ChatRequest) -> ChatResponse:
        """Session-aware chat execution with main-agent orchestration."""
        session_id = request.session_id or f"s_{uuid4().hex[:12]}"
        state = self._session_store.get_or_create_session(session_id)
        self._bind_client_scope(state, request.client_id)
        state.status = "running"
        state.last_error = None
        state.updated_at = _utc_now_iso()
        state.working_memory = ensure_working_memory_shape(state.working_memory)
        self._session_store.save_session(state)
        try:
            return await self._run_chat_session(request=request, session_id=session_id, state=state)
        except asyncio.CancelledError:
            # The orchestrator records the terminal state after the task has
            # stopped. Do not overwrite that preserved session context here.
            raise
        except Exception as exc:
            error_message = _short(f"{type(exc).__name__}: {exc}", limit=280) if str(exc) else type(exc).__name__
            state.status = "failed"
            state.last_error = error_message
            state.working_memory["last_error"] = {"message": error_message}
            state.updated_at = _utc_now_iso()
            self._session_store.save_session(state)
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

        state.active_subagent = "main_agent"
        state.working_memory = self._prepare_turn_memory(state.working_memory)

        request_payload = request.model_dump(mode="json")
        state.working_memory["last_request"] = request_payload
        if request.location is not None:
            set_working_memory_artifact(
                state.working_memory,
                "client_location",
                request.location.model_dump(mode="json", exclude_none=True),
                turn_index=state.turn_index,
            )
        if request.shop_id is not None:
            state.working_memory["last_shop_id"] = request.shop_id
        state.working_memory["keyword"] = request.keyword or _extract_keyword(request.message)
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
                agent="main_agent",
                scope="conversation",
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
        self._emit_agent_changed(
            session_id=session_id,
            to_agent=state.active_subagent,
            reason="session.started",
        )

        final_text, model_error = await self._run_main_agent(
            request=request,
            session_id=session_id,
            state=state,
        )

        reply_source = "model"
        if not final_text:
            logger.warning(
                "chat.fallback session_id=%s reason=empty_model_output last_error=%s",
                session_id,
                _short(str(state.working_memory.get("last_error") or ""), limit=180),
            )
            final_text = self._fallback_reply(state, request)
            reply_source = "fallback"

        if not bool(state.working_memory.get("assistant_token_emitted")):
            self._emit_assistant_tokens(
                session_id=session_id,
                text=final_text,
                active_subagent="main_agent",
            )
        self._append_turn(
            state,
            AgentTurn(
                role="assistant",
                content=final_text,
                agent="main_agent",
                scope="conversation",
                payload={
                    "final": True,
                    "reply_source": reply_source,
                    "map_artifacts": self._snapshot_turn_map_artifacts(state),
                },
            ),
        )
        response = await self._build_response(session_id=session_id, state=state, final_text=final_text)
        state.active_subagent = "main_agent"
        state.working_memory["reply"] = final_text
        state.updated_at = _utc_now_iso()
        if model_error is not None:
            state.status = "failed"
            state.last_error = _short(
                str(model_error.get("message") or "model call failed"),
                limit=280,
            )
        else:
            state.status = "completed"
            state.last_error = None
        self._session_store.save_session(state)
        self._replay_buffer.append(
            session_id,
            "session.failed" if model_error else "assistant.completed",
            {
                "reply": final_text,
                "active_subagent": state.active_subagent,
                "reply_source": reply_source,
                "model_error": model_error,
                "error": state.last_error,
            },
        )
        logger.info(
            "chat.done session_id=%s intent=%s shops=%s reply=%s",
            session_id,
            _normalize_intent(state.intent),
            len(self._memory_shops(state.working_memory)),
            _short(final_text, limit=160),
        )
        return response

    def _snapshot_turn_map_artifacts(self, state: AgentSessionState) -> dict[str, Any] | None:
        """Archive only map artifacts produced by the current conversation turn."""
        memory = ensure_working_memory_shape(state.working_memory)
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
            "shops": self._display_shops(memory),
            "route": route if isinstance(route, dict) else None,
            "client_location": client_location if isinstance(client_location, dict) else None,
            "destination": destination if isinstance(destination, dict) else None,
            "view_payload": view_payload if isinstance(view_payload, dict) else None,
        })

    async def _run_main_agent(
        self,
        *,
        request: ChatRequest,
        session_id: str,
        state: AgentSessionState,
    ) -> tuple[str | None, dict[str, Any] | None]:
        profile = self._subagent_builder.get("main_agent")
        guard = LoopGuard(self._max_steps)
        final_text: str | None = None
        model_error: dict[str, Any] | None = None

        while not guard.exhausted:
            step = guard.next()
            state.active_subagent = profile.name
            context = self._context_builder.build(
                session_state=state,
                request=request,
                subagent=profile,
            )
            logger.debug(
                "chat.context session_id=%s step=%s subagent=%s allowed_tools=%s message_count=%s",
                session_id,
                step,
                state.active_subagent,
                profile.allowed_tools,
                len(context.messages),
            )
            model_response = await self._provider_adapter.complete(
                instructions=context.instructions,
                messages=context.messages,
                tools=await self._tool_registry.tool_definitions(allowed_tools=profile.allowed_tools),
                runtime_hints={
                    "active_subagent": state.active_subagent,
                    "intent": state.intent,
                    "request": request.model_dump(mode="json"),
                    "memory": state.working_memory,
                },
            )
            self._record_model_call(
                state=state,
                response=model_response,
                agent_name=profile.name,
                step=step,
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

            if model_response.error is not None:
                model_error = dict(model_response.error)
                state.working_memory["last_error"] = {
                    "message": model_error.get("message"),
                    "type": model_error.get("type"),
                    "source": "model",
                }
                logger.warning(
                    "chat.model_error session_id=%s step=%s error=%s",
                    session_id,
                    step,
                    _short(str(model_error.get("message") or ""), limit=200),
                )
                break

            if model_response.tool_calls:
                await self._execute_tool_calls(
                    session_id=session_id,
                    request=request,
                    session_state=state,
                    tool_calls=model_response.tool_calls,
                    profile=profile,
                )
                if state.working_memory.get("reply"):
                    final_text = str(state.working_memory.get("reply"))
                    break
                continue

            if model_response.text:
                final_text = model_response.text.strip()
                break

            if state.working_memory.get("reply"):
                final_text = str(state.working_memory.get("reply"))
                break

        if final_text is None and model_error is None:
            model_error = {"type": "step_limit", "message": "agent exhausted its step budget"}
        return final_text, model_error

    def _record_model_call(
        self,
        *,
        state: AgentSessionState,
        response: Any,
        agent_name: str,
        step: int,
        worker_run_id: str | None = None,
    ) -> None:
        """Persist per-call model evidence: transcript, usage, status and raw tool-call arguments."""
        memory = ensure_working_memory_shape(state.working_memory)
        usage_totals = memory.get("usage_totals")
        if not isinstance(usage_totals, dict):
            usage_totals = {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_input_tokens": 0,
                "reasoning_tokens": 0,
                "usage_missing": 0,
            }
            memory["usage_totals"] = usage_totals
        usage_totals["calls"] += 1
        usage = response.usage if isinstance(response.usage, dict) else {}
        if any(usage.get(key) is not None for key in ("input_tokens", "output_tokens", "total_tokens")):
            # cached/reasoning tokens are subsets of input/output; they are tracked
            # alongside, never added on top of the totals.
            for key in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens"):
                value = usage.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    usage_totals[key] += int(value)
        else:
            usage_totals["usage_missing"] += 1
        self._append_turn(
            state,
            AgentTurn(
                role="assistant",
                content=response.text or "",
                agent=agent_name,
                worker_run_id=worker_run_id,
                scope="worker" if worker_run_id else "conversation",
                payload={
                    "model": {
                        "step": step,
                        "protocol": response.protocol,
                        "reported_model": response.reported_model,
                        "response_id": response.response_id,
                        "status": response.status,
                        "finish_reason": response.finish_reason,
                        "error": deepcopy(response.error),
                        "usage": deepcopy(response.usage),
                        "raw_usage": deepcopy(response.raw_usage),
                        "provider_ttft_ms": response.provider_ttft_ms,
                        "duration_ms": response.duration_ms,
                        "stream_mode": response.stream_mode,
                        "transcript": deepcopy(response.transcript),
                        "tool_calls": [
                            {
                                "call_id": call.call_id,
                                "name": call.name,
                                "arguments": deepcopy(call.arguments),
                                "raw_arguments": deepcopy(call.raw_arguments),
                                "parse_error": call.parse_error,
                            }
                            for call in response.tool_calls
                        ],
                    }
                },
            ),
            persist=False,
        )

    @staticmethod
    def _merge_usage_totals(
        *,
        parent_memory: dict[str, Any],
        worker_memory: dict[str, Any],
    ) -> None:
        worker_totals = worker_memory.get("usage_totals")
        if not isinstance(worker_totals, dict):
            return
        parent_memory = ensure_working_memory_shape(parent_memory)
        parent_totals = parent_memory.get("usage_totals")
        if not isinstance(parent_totals, dict):
            parent_totals = {
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_input_tokens": 0,
                "reasoning_tokens": 0,
                "usage_missing": 0,
            }
            parent_memory["usage_totals"] = parent_totals
        for key, value in worker_totals.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                parent_totals[key] = int(parent_totals.get(key) or 0) + int(value)

    async def _execute_tool_calls(
        self,
        *,
        session_id: str,
        request: ChatRequest,
        session_state: AgentSessionState,
        tool_calls: list[Any],
        profile: SubAgentProfile,
        worker_run_id: str | None = None,
        persist: bool = True,
    ) -> None:
        for call in tool_calls:
            # The trace starts when the model proposes the tool, so argument
            # preparation failures are also captured as a complete tool span.
            self._replay_buffer.append(
                session_id,
                "tool.started",
                {
                    "tool": call.name,
                    "call_id": call.call_id,
                    "active_subagent": profile.name,
                    "worker_run_id": worker_run_id,
                },
            )
            preparation_error: str | None = None
            try:
                if call.parse_error:
                    raise ValueError(f"invalid tool arguments: {call.parse_error}")
                prepared_args, hydrated_fields = await self._tool_registry.prepare_arguments(
                    tool_name=call.name,
                    raw_arguments=call.arguments,
                    runtime_context=session_state.working_memory,
                )
            except Exception as exc:
                prepared_args, hydrated_fields = dict(call.arguments), []
                preparation_error = f"{type(exc).__name__}: {exc}"
            argument_evidence = {
                "raw_arguments": deepcopy(call.raw_arguments),
                "parsed_arguments": deepcopy(call.arguments),
                "parse_error": call.parse_error,
                "prepared_arguments": deepcopy(prepared_args),
                "hydrated_fields": list(hydrated_fields),
                "preparation_error": preparation_error,
            }
            if preparation_error is not None:
                logger.warning(
                    "tool.prepare_failed session_id=%s tool=%s call_id=%s agent=%s error=%s",
                    session_id,
                    call.name,
                    call.call_id,
                    profile.name,
                    _short(preparation_error, limit=200),
                )
                result = ToolExecutionResult(
                    call_id=call.call_id,
                    tool_name=call.name,
                    status="failed",
                    output={
                        "error": {
                            "type": "argument_preparation_error",
                            "message": preparation_error,
                        }
                    },
                    error_message=preparation_error,
                )
                self._record_tool_result(
                    session_id=session_id,
                    state=session_state,
                    result=result,
                    agent_name=profile.name,
                    worker_run_id=worker_run_id,
                    tool_arguments=prepared_args,
                    argument_evidence=argument_evidence,
                    persist=persist,
                )
                continue
            logger.info(
                "tool.call session_id=%s tool=%s call_id=%s agent=%s args=%s",
                session_id,
                call.name,
                call.call_id,
                profile.name,
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
            result = await self._tool_registry.execute(
                call_id=call.call_id,
                tool_name=call.name,
                raw_arguments=prepared_args,
                allowed_tools=profile.allowed_tools,
            )
            if result.status == "completed" and result.tool_name == "invoke_worker":
                envelope = await self._run_worker(
                    session_id=session_id,
                    request=request,
                    state=session_state,
                    worker_name=str(result.output.get("worker") or "").strip(),
                    task=str(result.output.get("task") or "").strip(),
                )
                result = ToolExecutionResult(
                    call_id=result.call_id,
                    tool_name=result.tool_name,
                    status="failed" if envelope.get("status") == "failed" else "completed",
                    output=envelope,
                    error_message=envelope.get("error"),
                )
            self._record_tool_result(
                session_id=session_id,
                state=session_state,
                result=result,
                agent_name=profile.name,
                worker_run_id=worker_run_id,
                tool_arguments=prepared_args,
                argument_evidence=argument_evidence,
                persist=persist,
            )

    async def _run_worker(
        self,
        *,
        session_id: str,
        request: ChatRequest,
        state: AgentSessionState,
        worker_name: str,
        task: str,
    ) -> dict[str, Any]:
        if worker_name not in {"search_worker", "navigation_worker"} or not task:
            return {
                "worker": worker_name or "unknown_worker",
                "run_id": f"wrk_{uuid4().hex[:10]}",
                "status": "failed",
                "summary": "Worker dispatch payload was incomplete.",
                "result": {
                    "summary": "Worker dispatch payload was incomplete.",
                    "missing_fields": ["worker", "task"],
                },
                "artifacts": {},
                "missing_fields": ["worker", "task"] if not task else ["worker"],
                "error": "missing worker or task",
                "task": task,
                "task_preview": _short(task, limit=120),
            }

        worker_profile = self._subagent_builder.get(worker_name)
        run_id = f"wrk_{uuid4().hex[:10]}"
        worker_state = AgentSessionState(
            session_id=state.session_id,
            turn_index=state.turn_index,
            active_subagent=worker_name,
            intent=state.intent,
            status="running",
            turns=[],
            working_memory=self._build_worker_memory_snapshot(state.working_memory),
        )
        worker_state.working_memory = ensure_working_memory_shape(worker_state.working_memory)
        worker_state.turns.append(
            AgentTurn(
                role="user",
                content=task,
                agent=worker_name,
                worker_run_id=run_id,
                scope="worker",
                payload={"dispatch": True},
            )
        )

        previous_agent = state.active_subagent
        state.active_subagent = worker_name
        self._session_store.save_session(state)
        self._emit_agent_changed(
            session_id=session_id,
            from_agent=previous_agent,
            to_agent=worker_name,
            reason="worker.started",
            worker_run_id=run_id,
        )
        self._replay_buffer.append(
            session_id,
            "worker.started",
            {
                "worker": worker_name,
                "run_id": run_id,
                "task_preview": _short(task, limit=160),
                "active_subagent": worker_name,
            },
        )

        final_text: str | None = None
        failed_error: str | None = None
        guard = LoopGuard(self._max_steps)
        while not guard.exhausted:
            step = guard.next()
            context = self._context_builder.build(
                session_state=worker_state,
                request=request,
                subagent=worker_profile,
            )
            logger.debug(
                "worker.context session_id=%s worker=%s run_id=%s step=%s allowed_tools=%s message_count=%s",
                session_id,
                worker_name,
                run_id,
                step,
                worker_profile.allowed_tools,
                len(context.messages),
            )
            model_response = await self._provider_adapter.complete(
                instructions=context.instructions,
                messages=context.messages,
                tools=await self._tool_registry.tool_definitions(allowed_tools=worker_profile.allowed_tools),
                runtime_hints={
                    "active_subagent": worker_name,
                    "intent": worker_state.intent,
                    "worker_run_id": run_id,
                    "request": request.model_dump(mode="json"),
                    "memory": worker_state.working_memory,
                },
            )
            self._record_model_call(
                state=worker_state,
                response=model_response,
                agent_name=worker_name,
                step=step,
                worker_run_id=run_id,
            )
            if model_response.response_id:
                worker_state.previous_response_id = model_response.response_id
            logger.info(
                "worker.step session_id=%s worker=%s run_id=%s step=%s tool_calls=%s has_text=%s",
                session_id,
                worker_name,
                run_id,
                step,
                len(model_response.tool_calls),
                bool(model_response.text),
            )

            if model_response.error is not None:
                failed_error = _short(
                    str(model_response.error.get("message") or "model call failed"),
                    limit=240,
                )
                logger.warning(
                    "worker.model_error session_id=%s worker=%s run_id=%s step=%s error=%s",
                    session_id,
                    worker_name,
                    run_id,
                    step,
                    failed_error,
                )
                break

            if model_response.tool_calls:
                await self._execute_tool_calls(
                    session_id=session_id,
                    request=request,
                    session_state=worker_state,
                    tool_calls=model_response.tool_calls,
                    profile=worker_profile,
                    worker_run_id=run_id,
                    persist=False,
                )
                if worker_state.working_memory.get("last_error"):
                    failed_error = _short(
                        str(worker_state.working_memory.get("last_error")),
                        limit=240,
                    )
                else:
                    failed_error = None
                continue

            if model_response.text:
                final_text = model_response.text.strip()
            break

        if final_text is None and guard.exhausted and failed_error is None:
            failed_error = "worker exhausted its step budget"
        promoted_artifacts = self._promote_worker_artifacts(
            parent_memory=state.working_memory,
            worker_memory=worker_state.working_memory,
            turn_index=state.turn_index,
        )
        self._merge_usage_totals(
            parent_memory=state.working_memory,
            worker_memory=worker_state.working_memory,
        )
        self._persist_worker_evidence_turns(parent_state=state, worker_state=worker_state)
        envelope = self._build_worker_envelope(
            worker_name=worker_name,
            run_id=run_id,
            task=task,
            worker_memory=worker_state.working_memory,
            final_text=final_text,
            promoted_artifacts=promoted_artifacts,
            failed_error=failed_error,
        )
        envelope["usage"] = deepcopy(worker_state.working_memory.get("usage_totals") or {})
        append_worker_run(state.working_memory, envelope)
        if envelope["status"] == "failed":
            state.last_error = envelope["error"]
            state.working_memory["last_error"] = {"message": envelope["error"]}
            self._replay_buffer.append(
                session_id,
                "worker.failed",
                {
                    "worker": worker_name,
                    "run_id": run_id,
                    "error": envelope["error"],
                    "active_subagent": worker_name,
                },
            )
        else:
            state.last_error = None
            state.working_memory.pop("last_error", None)
            self._replay_buffer.append(
                session_id,
                "worker.completed",
                {
                    "worker": worker_name,
                    "run_id": run_id,
                    "status": envelope["status"],
                    "summary": envelope["summary"],
                    "active_subagent": worker_name,
                },
            )

        state.active_subagent = "main_agent"
        state.updated_at = _utc_now_iso()
        self._session_store.save_session(state)
        self._emit_agent_changed(
            session_id=session_id,
            from_agent=worker_name,
            to_agent="main_agent",
            reason="worker.completed",
            worker_run_id=run_id,
        )
        return envelope

    def _record_tool_result(
        self,
        *,
        session_id: str,
        state: AgentSessionState,
        result: ToolExecutionResult,
        agent_name: str,
        worker_run_id: str | None,
        tool_arguments: dict[str, Any] | None,
        persist: bool,
        argument_evidence: dict[str, Any] | None = None,
    ) -> None:
        """Record the result of a tool execution, emitting appropriate events and updating session state."""
        if result.status == "completed":
            payload: dict[str, Any] = {
                "tool": result.tool_name,
                "call_id": result.call_id,
                "active_subagent": agent_name,
                "worker_run_id": worker_run_id,
            }
            route = result.output.get("route")
            if isinstance(route, dict):
                payload["distance_m"] = route.get("distance_m")
                self._replay_buffer.append(session_id, "navigation.route_ready", route)
            self._replay_buffer.append(session_id, "tool.completed", payload)
            logger.info(
                "tool.completed session_id=%s tool=%s agent=%s",
                session_id,
                result.tool_name,
                agent_name,
            )
        else:
            error_message = result.error_message or "tool execution failed"
            self._replay_buffer.append(
                session_id,
                "tool.failed",
                {
                    "tool": result.tool_name,
                    "call_id": result.call_id,
                    "error": error_message,
                    "active_subagent": agent_name,
                    "worker_run_id": worker_run_id,
                },
            )
            logger.warning(
                "tool.failed session_id=%s tool=%s agent=%s error=%s",
                session_id,
                result.tool_name,
                agent_name,
                _short(error_message, limit=160),
            )

        self._append_turn(
            state,
            AgentTurn(
                role="tool",
                name=result.tool_name,
                call_id=result.call_id,
                content=json.dumps(result.output, ensure_ascii=False),
                agent=agent_name,
                worker_run_id=worker_run_id,
                scope="worker" if worker_run_id else "conversation",
                payload={
                    "status": result.status,
                    "result": result.output,
                    "arguments": deepcopy(tool_arguments) if isinstance(tool_arguments, dict) else {},
                    "argument_evidence": deepcopy(argument_evidence) if isinstance(argument_evidence, dict) else None,
                },
            ),
            persist=persist,
        )
        self._apply_tool_memory(state=state, result=result)

    def _apply_tool_memory(self, *, state: AgentSessionState, result: ToolExecutionResult) -> None:
        memory = ensure_working_memory_shape(state.working_memory)
        if result.status != "completed":
            error_payload = result.output.get("error")
            memory["last_error"] = error_payload if error_payload is not None else result.error_message
            state.updated_at = _utc_now_iso()
            return

        memory.pop("last_error", None)

        if result.tool_name == "invoke_worker":
            envelope = result.output if isinstance(result.output, dict) else {}
            if envelope.get("status") == "failed":
                memory["last_error"] = {"message": envelope.get("error") or "worker failed"}
            result_payload = envelope.get("result")
            if isinstance(result_payload, dict):
                destination = result_payload.get("destination")
                if isinstance(destination, dict):
                    set_working_memory_artifact(memory, "destination", destination, turn_index=state.turn_index)
                route = result_payload.get("route")
                if isinstance(route, dict):
                    set_working_memory_artifact(memory, "route", route, turn_index=state.turn_index)
                    state.intent = "navigate"
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
                self._append_search_candidates(memory, [shop_payload], turn_index=state.turn_index)
                source_id = shop_payload.get("source_id")
                if source_id is not None:
                    memory["last_shop_id"] = source_id
                return
            shops = result.output.get("shops")
            if isinstance(shops, list):
                set_working_memory_artifact(memory, "shops", shops, turn_index=state.turn_index)
                self._append_search_candidates(memory, shops, turn_index=state.turn_index)
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
                memory["keyword"] = last_request.get("keyword") or _extract_keyword(
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
                state.intent = "navigate"
            return

        if result.tool_name.startswith("mcp__"):
            route = result.output.get("route")
            if isinstance(route, dict):
                set_working_memory_artifact(memory, "route", route, turn_index=state.turn_index)
                destination = get_working_memory_artifact(memory, "shop")
                if isinstance(destination, dict):
                    set_working_memory_artifact(memory, "destination", destination, turn_index=state.turn_index)
                state.intent = "navigate"
            data = result.output.get("data")
            if isinstance(data, dict):
                locations = data.get("locations")
                if isinstance(locations, list) and locations:
                    set_working_memory_artifact(memory, "resolved_locations", locations, turn_index=state.turn_index)
            memory["last_mcp_result"] = result.output
            return

        if result.tool_name == "summary_tool":
            reply = result.output.get("reply")
            if isinstance(reply, str) and reply.strip():
                memory["reply"] = reply.strip()
            return

    def _build_worker_memory_snapshot(self, parent_memory: dict[str, Any]) -> dict[str, Any]:
        memory = ensure_working_memory_shape({})
        parent_memory = ensure_working_memory_shape(parent_memory)
        for key in ("last_request", "last_shop_id", "keyword", "last_db_query", "provider", "last_route_endpoints"):
            if key in parent_memory:
                memory[key] = deepcopy(parent_memory[key])
        for key in ("shop", "shops", "selected_shops", "total", "route", "resolved_locations", "client_location", "destination", "view_payload"):
            value = get_working_memory_artifact(parent_memory, key)
            if value is not None:
                set_working_memory_artifact(memory, key, value)
        for key in ("route", "destination", "view_payload"):
            memory["artifacts"].pop(key, None)
        return memory

    def _promote_worker_artifacts(
        self,
        *,
        parent_memory: dict[str, Any],
        worker_memory: dict[str, Any],
        turn_index: int,
    ) -> dict[str, Any]:
        promoted: dict[str, Any] = {}
        for key in ("shop", "shops", "selected_shops", "total", "route", "resolved_locations", "client_location", "destination", "view_payload"):
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

    def _persist_worker_evidence_turns(
        self,
        *,
        parent_state: AgentSessionState,
        worker_state: AgentSessionState,
    ) -> None:
        """Copy worker tool turns and model-call evidence turns into the parent session."""
        for turn in worker_state.turns:
            is_model_turn = (
                turn.role == "assistant"
                and isinstance(turn.payload, dict)
                and isinstance(turn.payload.get("model"), dict)
            )
            if turn.role != "tool" and not is_model_turn:
                continue
            self._append_turn(
                parent_state,
                AgentTurn(
                    role=turn.role,
                    content=turn.content,
                    agent=turn.agent,
                    name=turn.name,
                    call_id=turn.call_id,
                    worker_run_id=turn.worker_run_id,
                    scope=turn.scope,
                    payload=deepcopy(turn.payload),
                    created_at=turn.created_at,
                ),
                persist=False,
            )

    def _build_worker_envelope(
        self,
        *,
        worker_name: str,
        run_id: str,
        task: str,
        worker_memory: dict[str, Any],
        final_text: str | None,
        promoted_artifacts: dict[str, Any],
        failed_error: str | None,
    ) -> dict[str, Any]:
        if worker_name == "navigation_worker":
            destination = get_working_memory_artifact(worker_memory, "shop")
            if not isinstance(destination, dict):
                shops = self._display_shops(worker_memory)
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
            summary = self._build_navigation_worker_summary(
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
        candidate_shops = self._memory_shops(worker_memory)
        displayed_shops = self._display_shops(worker_memory)
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
        summary = self._build_search_worker_summary(
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
        self,
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
        self,
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

    def _fallback_reply(self, state: AgentSessionState, request: ChatRequest) -> str:
        """Fallback reply to guarantee API always returns text."""
        reply = state.working_memory.get("reply")
        if isinstance(reply, str) and reply.strip():
            return reply.strip()

        if _normalize_intent(state.intent) == "navigate":
            if get_working_memory_artifact(state.working_memory, "route"):
                return "路线已经准备好了，但总结环节没有产出完整文本，请重试一次。"
            if request.shop_id is None and state.working_memory.get("last_shop_id") is None:
                return "请再明确一下起点和目标地点，我会继续规划路线。"
            return "导航流程还没有完成，请再试一次。"

        shops_payload = self._display_shops(state.working_memory)
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

    async def _build_response(self, *, session_id: str, state: AgentSessionState, final_text: str) -> ChatResponse:
        """Build API response from memory-level shop and route payloads."""
        raw_shops = self._display_shops(state.working_memory)[:20]
        shops = await asyncio.to_thread(self._arcade_payload_mapper.summaries_from_rows, raw_shops)
        route_obj = self._arcade_payload_mapper.route_from_payload(
            get_working_memory_artifact(state.working_memory, "route")
        )

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

    def _memory_shops(self, memory: dict[str, Any]) -> list[dict[str, Any]]:
        shops_raw: list[dict[str, Any]] = []
        memory_shops = get_working_memory_artifact(memory, "shops")
        if isinstance(memory_shops, list):
            shops_raw.extend(item for item in memory_shops if isinstance(item, dict))
        memory_shop = get_working_memory_artifact(memory, "shop")
        if isinstance(memory_shop, dict):
            source_id = memory_shop.get("source_id")
            exists = any(item.get("source_id") == source_id for item in shops_raw)
            if not exists:
                shops_raw.append(memory_shop)
        return shops_raw

    def _display_shops(self, memory: dict[str, Any]) -> list[dict[str, Any]]:
        """Return explicitly committed cards, with legacy candidate fallback.

        `selected_shops` is authoritative even when it is an empty list. The
        fallback keeps direct/legacy callers functional while all worker search
        paths now commit selection through result_selection_tool.
        """
        selected = get_working_memory_artifact(memory, "selected_shops")
        if isinstance(selected, list):
            return [item for item in selected if isinstance(item, dict)]
        return self._memory_shops(memory)

    def _append_search_candidates(
        self,
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

    def _prepare_turn_memory(self, memory: dict[str, Any]) -> dict[str, Any]:
        prepared = ensure_working_memory_shape(memory)
        prepared.pop("reply", None)
        prepared.pop("last_error", None)
        for key in ("search_candidates",):
            prepared["artifacts"].pop(key, None)
            prepared.pop(key, None)
            prepared.get("artifact_meta", {}).pop(key, None)
        for key in ("route", "destination", "view_payload"):
            prepared["artifacts"].pop(key, None)
            prepared.pop(key, None)
            prepared.get("artifact_meta", {}).pop(key, None)
        prepared["assistant_token_emitted"] = False
        return prepared

    def _bind_client_scope(self, state: AgentSessionState, client_id: str | None) -> None:
        """Attach a browser-owned client id to new sessions and reject cross-client writes."""
        if client_id is None:
            return
        if state.client_id is not None and state.client_id != client_id:
            raise SessionOwnershipError(state.session_id)
        if state.client_id is None:
            state.client_id = client_id

    def _append_turn(self, state: AgentSessionState, turn: AgentTurn, *, persist: bool = True) -> None:
        """Append a turn to the session state, with optional persistence."""
        state.turns.append(turn)
        state.updated_at = _utc_now_iso()
        if persist:
            self._session_store.save_session(state)

    def _emit_agent_changed(
        self,
        *,
        session_id: str,
        to_agent: str,
        reason: str,
        from_agent: str | None = None,
        worker_run_id: str | None = None,
    ) -> None:
        """Emit an agent change event to the replay buffer."""
        payload: dict[str, Any] = {
            "active_subagent": to_agent,
            "to_subagent": to_agent,
            "reason": reason,
        }
        if from_agent:
            payload["from_subagent"] = from_agent
        if worker_run_id:
            payload["worker_run_id"] = worker_run_id
        self._replay_buffer.append(session_id, "subagent.changed", payload)

    def _emit_assistant_tokens(
        self,
        *,
        session_id: str,
        text: str,
        active_subagent: str,
    ) -> None:
        """Emit assistant token events to the replay buffer, splitting the text into chunks if necessary.

        These events are chunked locally after the full reply text is available
        (provider requests are non-streaming), so they must not be read as
        provider TTFT or real token-throughput measurements.
        """
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
                    "stream_mode": "synthetic",
                },
            )
