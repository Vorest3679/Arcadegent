"""Map between runtime session models and Supabase `chat_sessions` rows."""

from __future__ import annotations

from typing import Any

from app.agent.runtime.session_state import (
    AgentSessionState,
    AgentTurn,
    ensure_working_memory_shape,
)
from app.protocol.session_dto import AgentTurnRecord, ChatSessionRecord


def _utc_now_iso() -> str:
    """Runtime default timestamp; duplicated here to avoid circular imports."""
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ChatSessionMapper:
    """Bidirectional conversion between `AgentSessionState` and `ChatSessionRecord`."""

    @staticmethod
    def to_record(state: AgentSessionState) -> ChatSessionRecord:
        """Serialize a runtime state into a DB row DTO."""
        return ChatSessionRecord(
            session_id=state.session_id,
            client_id=state.client_id,
            intent=state.intent,
            active_subagent=state.active_subagent,
            status=state.status,
            last_error=state.last_error,
            turn_index=state.turn_index,
            previous_response_id=state.previous_response_id,
            working_memory=ensure_working_memory_shape(state.working_memory),
            turns=[ChatSessionMapper._turn_to_record(turn) for turn in state.turns],
            created_at=state.created_at or _utc_now_iso(),
            updated_at=state.updated_at or _utc_now_iso(),
        )

    @staticmethod
    def from_record(record: ChatSessionRecord) -> AgentSessionState:
        """Deserialize a DB row DTO back into a runtime state."""
        return AgentSessionState(
            session_id=record.session_id,
            client_id=record.client_id,
            turn_index=record.turn_index,
            active_subagent=record.active_subagent,
            intent=record.intent,
            status=record.status,
            last_error=record.last_error,
            turns=[ChatSessionMapper._turn_from_record(turn) for turn in record.turns],
            working_memory=ensure_working_memory_shape(record.working_memory),
            previous_response_id=record.previous_response_id,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _turn_to_record(turn: AgentTurn) -> AgentTurnRecord:
        return AgentTurnRecord(
            role=turn.role,
            content=turn.content,
            agent=turn.agent,
            name=turn.name,
            call_id=turn.call_id,
            worker_run_id=turn.worker_run_id,
            scope=turn.scope,
            payload=dict(turn.payload) if isinstance(turn.payload, dict) else {},
            created_at=turn.created_at,
        )

    @staticmethod
    def _turn_from_record(record: AgentTurnRecord) -> AgentTurn:
        return AgentTurn(
            role=record.role,
            content=record.content,
            agent=record.agent,
            name=record.name,
            call_id=record.call_id,
            worker_run_id=record.worker_run_id,
            scope=record.scope,
            payload=dict(record.payload),
            created_at=record.created_at,
        )
