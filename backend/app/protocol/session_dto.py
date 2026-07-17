"""Database-oriented DTOs for persisting chat sessions to Supabase.

These models describe the exact row shape of the `chat_sessions` table and are
used by the mapper layer. They are intentionally separate from the public API
DTOs in `messages.py` and the runtime dataclasses in `session_state.py`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


TurnRoleType = Literal["user", "assistant", "tool"]
TurnScopeType = Literal["conversation", "worker"]
SessionStatusType = Literal["idle", "running", "completed", "failed"]


class AgentTurnRecord(BaseModel):
    """One persisted turn item inside `chat_sessions.turns`."""

    role: TurnRoleType
    content: str
    agent: str | None = None
    name: str | None = None
    call_id: str | None = None
    worker_run_id: str | None = None
    scope: TurnScopeType = "conversation"
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: str


class ChatSessionRecord(BaseModel):
    """Full row shape of the `chat_sessions` table."""

    session_id: str
    client_id: str | None = None
    # `intent` mirrors `AgentSessionState.intent`. It is set by the runtime when a
    # turn is inferred (search / search_nearby / navigate) and is exposed in the
    # public session detail/summary APIs. Deleting it would break the chat history
    # list and context restoration, so it is kept as a persisted column.
    intent: str = "search"
    active_subagent: str = "main_agent"
    status: SessionStatusType = "idle"
    last_error: str | None = None
    turn_index: int = 0
    previous_response_id: str | None = None
    working_memory: dict[str, Any] = Field(default_factory=dict)
    turns: list[AgentTurnRecord] = Field(default_factory=list)
    created_at: str
    updated_at: str


class ChatSessionCreateDto(BaseModel):
    """Fields used when inserting a new empty session row."""

    session_id: str
    client_id: str | None = None
    intent: str = "search"
    active_subagent: str = "main_agent"
    status: SessionStatusType = "idle"


class ChatSessionUpdateDto(BaseModel):
    """Fields used when upserting a full session snapshot."""

    session_id: str
    client_id: str | None = None
    intent: str = "search"
    active_subagent: str = "main_agent"
    status: SessionStatusType = "idle"
    last_error: str | None = None
    turn_index: int = 0
    previous_response_id: str | None = None
    working_memory: dict[str, Any] = Field(default_factory=dict)
    turns: list[AgentTurnRecord] = Field(default_factory=list)
