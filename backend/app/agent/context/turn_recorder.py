"""Turn persistence and usage accounting for the ReAct runtime.

Model calls, tool results and worker evidence are recorded here as
``AgentTurn`` entries. Event publishing stays with the caller so the run's
event order remains unchanged.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.agent.runtime.session_state import (
    AgentSessionState,
    AgentTurn,
    ensure_working_memory_shape,
)

if TYPE_CHECKING:
    from app.agent.llm.provider_adapter import ModelResponse
    from app.agent.tools.registry import ToolExecutionResult
    from app.infra.db.protocols import SessionStateRepository


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_usage_totals() -> dict[str, int]:
    return {
        "calls": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_input_tokens": 0,
        "reasoning_tokens": 0,
        "usage_missing": 0,
    }


def append_turn(
    state: AgentSessionState,
    turn: AgentTurn,
    *,
    persist: bool = True,
    session_store: SessionStateRepository | None = None,
) -> None:
    """Append a turn to the session state, with optional persistence."""
    state.turns.append(turn)
    state.updated_at = _utc_now_iso()
    if persist:
        if session_store is None:
            raise RuntimeError("session_store is required to persist turns")
        session_store.save_session(state)


def record_model_call(
    *,
    state: AgentSessionState,
    response: ModelResponse,
    agent_name: str,
    step: int,
    worker_run_id: str | None = None,
    final_reply: bool = False,
) -> None:
    """Persist per-call model evidence: transcript, usage, status and raw tool-call arguments.

    ``final_reply`` marks the evidence whose text is also stored as the turn's
    final assistant reply; the context projection uses that marker to avoid
    replaying the same text twice.
    """
    memory = ensure_working_memory_shape(state.working_memory)
    usage_totals = memory.get("usage_totals")
    if not isinstance(usage_totals, dict):
        usage_totals = _new_usage_totals()
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
    model_payload: dict[str, Any] = {
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
    if final_reply:
        # Keep the raw evidence, but tell the context projection that the
        # final assistant turn owns this text.
        model_payload["final_reply"] = True
    append_turn(
        state,
        AgentTurn(
            role="assistant",
            content=response.text or "",
            agent=agent_name,
            worker_run_id=worker_run_id,
            scope="worker" if worker_run_id else "conversation",
            payload={"model": model_payload},
        ),
        persist=False,
    )


def record_tool_result_turn(
    *,
    state: AgentSessionState,
    result: ToolExecutionResult,
    agent_name: str,
    worker_run_id: str | None,
    tool_arguments: dict[str, Any] | None,
    persist: bool,
    argument_evidence: dict[str, Any] | None = None,
    session_store: SessionStateRepository | None = None,
) -> None:
    """Append the tool-result turn; the caller publishes the matching events."""
    append_turn(
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
        session_store=session_store,
    )


def merge_usage_totals(
    *,
    parent_memory: dict[str, Any],
    worker_memory: dict[str, Any],
) -> None:
    """Add a worker's usage counters to its parent's totals exactly once."""
    worker_totals = worker_memory.get("usage_totals")
    if not isinstance(worker_totals, dict):
        return
    parent_memory = ensure_working_memory_shape(parent_memory)
    parent_totals = parent_memory.get("usage_totals")
    if not isinstance(parent_totals, dict):
        parent_totals = _new_usage_totals()
        parent_memory["usage_totals"] = parent_totals
    for key, value in worker_totals.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            parent_totals[key] = int(parent_totals.get(key) or 0) + int(value)


def persist_worker_evidence_turns(
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
        append_turn(
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
