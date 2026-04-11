"""Session state store for multi-turn ReAct execution."""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Literal

TurnRole = Literal["user", "assistant", "tool"]
TurnScope = Literal["conversation", "worker"]
SessionStatus = Literal["idle", "running", "completed", "failed"]
logger = logging.getLogger(__name__)


def _utc_now_iso() -> str:
    """Generate UTC ISO8601 timestamp used by chat session snapshots."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass
class AgentTurn:
    """One persisted turn item for model context reconstruction."""

    role: TurnRole
    content: str
    agent: str | None = None
    name: str | None = None
    call_id: str | None = None
    worker_run_id: str | None = None
    scope: TurnScope = "conversation"
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now_iso)


@dataclass
class AgentSessionState:
    """Session-level execution state and memory."""

    session_id: str
    turn_index: int = 0
    active_subagent: str = "main_agent"
    intent: str = "search"
    status: SessionStatus = "idle"
    last_error: str | None = None
    turns: list[AgentTurn] = field(default_factory=list)
    working_memory: dict[str, Any] = field(default_factory=dict)
    previous_response_id: str | None = None
    created_at: str = field(default_factory=_utc_now_iso)
    updated_at: str = field(default_factory=_utc_now_iso)


def ensure_working_memory_shape(memory: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize working memory into the phase-1 hub layout. 
    This allows flexible schema evolution while ensuring the expected sub-keys exist for easier access."""
    normalized = memory if isinstance(memory, dict) else {}
    artifacts = normalized.get("artifacts")
    if not isinstance(artifacts, dict):
        artifacts = {}
    normalized["artifacts"] = artifacts

    worker_runs = normalized.get("worker_runs")
    if not isinstance(worker_runs, list):
        worker_runs = []
    normalized["worker_runs"] = [item for item in worker_runs if isinstance(item, dict)]
    return normalized


def get_working_memory_artifact(memory: dict[str, Any] | None, key: str) -> Any:
    normalized = ensure_working_memory_shape(memory)
    artifacts = normalized["artifacts"]
    if key in artifacts:
        return artifacts.get(key)
    return normalized.get(key)


def set_working_memory_artifact(memory: dict[str, Any] | None, key: str, value: Any) -> None:
    normalized = ensure_working_memory_shape(memory)
    normalized["artifacts"][key] = deepcopy(value)


def append_worker_run(memory: dict[str, Any] | None, run: dict[str, Any], *, max_entries: int = 20) -> None:
    normalized = ensure_working_memory_shape(memory)
    worker_runs = normalized["worker_runs"]
    worker_runs.append(deepcopy(run))
    if len(worker_runs) > max_entries:
        del worker_runs[:-max_entries]


class SessionStateStore:
    """Thread-safe session store keyed by session_id with optional disk persistence."""

    def __init__(self, *, storage_path: Path | None = None) -> None:
        self._lock = Lock()
        self._states: dict[str, AgentSessionState] = {}
        self._storage_path = storage_path
        self._load_from_disk()

    def get_or_create(self, session_id: str) -> AgentSessionState:
        with self._lock:
            state = self._states.get(session_id)
            if state is None:
                state = AgentSessionState(session_id=session_id)
                self._states[session_id] = state
            return deepcopy(state)

    def snapshot(self, session_id: str) -> AgentSessionState | None:
        """Return deep-copied session state for API serialization."""
        with self._lock:
            state = self._states.get(session_id)
            if state is None:
                return None
            return deepcopy(state)

    def list_snapshots(self, *, limit: int = 50) -> list[AgentSessionState]:
        """Return recent session snapshots sorted by updated_at desc."""
        safe_limit = max(1, min(limit, 200))
        with self._lock:
            snapshots = [deepcopy(item) for item in self._states.values()]
        snapshots.sort(key=lambda item: item.updated_at, reverse=True)
        return snapshots[:safe_limit]

    def delete(self, session_id: str) -> bool:
        """Delete one session by id; return True when it existed."""
        with self._lock:
            existed = session_id in self._states
            if existed:
                del self._states[session_id]
                self._flush_to_disk_locked()
            return existed

    def save(self, state: AgentSessionState) -> None:
        """Persist one mutated session state and flush the snapshot file."""
        with self._lock:
            self._states[state.session_id] = deepcopy(state)
            self._flush_to_disk_locked()

    def _load_from_disk(self) -> None:
        if self._storage_path is None or not self._storage_path.exists():
            return
        try:
            payload = json.loads(self._storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("session_store.load_failed path=%s error=%s", self._storage_path, exc)
            return

        raw_sessions = payload.get("sessions") if isinstance(payload, dict) else payload
        if not isinstance(raw_sessions, list):
            logger.warning("session_store.load_invalid path=%s", self._storage_path)
            return

        restored: dict[str, AgentSessionState] = {}
        for raw_state in raw_sessions:
            state = _state_from_dict(raw_state)
            if state is not None:
                restored[state.session_id] = state
        self._states = restored

    def _flush_to_disk_locked(self) -> None:
        if self._storage_path is None: #if no storage path, skip disk flush
            return
        # Sort sessions by updated_at desc for better readability, though not required for loading.
        snapshots = sorted(self._states.values(), key=lambda item: item.updated_at, reverse=True)
        # save the whole session list as one JSON object to simplify loading and future schema evolution, even if it may rewrite unchanged sessions
        payload = {
            "version": 1,
            "sessions": [_state_to_dict(item) for item in snapshots],
        }
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._storage_path.with_name(f"{self._storage_path.name}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self._storage_path)


def _state_to_dict(state: AgentSessionState) -> dict[str, Any]:
    return {
        "session_id": state.session_id,
        "turn_index": state.turn_index,
        "active_subagent": state.active_subagent,
        "intent": state.intent,
        "status": state.status,
        "last_error": state.last_error,
        "turns": [
            {
                "role": turn.role,
                "content": turn.content,
                "agent": turn.agent,
                "name": turn.name,
                "call_id": turn.call_id,
                "worker_run_id": turn.worker_run_id,
                "scope": turn.scope,
                "payload": turn.payload,
                "created_at": turn.created_at,
            }
            for turn in state.turns
        ],
        "working_memory": ensure_working_memory_shape(state.working_memory),
        "previous_response_id": state.previous_response_id,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
    }


def _state_from_dict(raw: object) -> AgentSessionState | None:
    if not isinstance(raw, dict):
        return None
    session_id = raw.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return None
    turns_raw = raw.get("turns")
    turns: list[AgentTurn] = []
    if isinstance(turns_raw, list):
        for item in turns_raw:
            turn = _turn_from_dict(item)
            if turn is not None:
                turns.append(turn)
    working_memory = ensure_working_memory_shape(raw.get("working_memory"))
    return AgentSessionState(
        session_id=session_id,
        turn_index=_coerce_int(raw.get("turn_index"), default=0),
        active_subagent=_coerce_active_agent(raw.get("active_subagent"), default="main_agent"),
        intent=_coerce_str(raw.get("intent"), default="search"),
        status=_coerce_status(raw.get("status"), default="completed" if turns else "idle"),
        last_error=raw.get("last_error") if isinstance(raw.get("last_error"), str) else None,
        turns=turns,
        working_memory=working_memory,
        previous_response_id=raw.get("previous_response_id")
        if isinstance(raw.get("previous_response_id"), str)
        else None,
        created_at=_coerce_str(raw.get("created_at"), default=_utc_now_iso()),
        updated_at=_coerce_str(raw.get("updated_at"), default=_utc_now_iso()),
    )


def _turn_from_dict(raw: object) -> AgentTurn | None:
    if not isinstance(raw, dict):
        return None
    role = raw.get("role")
    content = raw.get("content")
    if role not in {"user", "assistant", "tool"} or not isinstance(content, str):
        return None
    payload = raw.get("payload")
    return AgentTurn(
        role=role,
        content=content,
        agent=raw.get("agent") if isinstance(raw.get("agent"), str) else None,
        name=raw.get("name") if isinstance(raw.get("name"), str) else None,
        call_id=raw.get("call_id") if isinstance(raw.get("call_id"), str) else None,
        worker_run_id=raw.get("worker_run_id") if isinstance(raw.get("worker_run_id"), str) else None,
        scope=raw.get("scope") if raw.get("scope") in {"conversation", "worker"} else "conversation",
        payload=payload if isinstance(payload, dict) else {},
        created_at=_coerce_str(raw.get("created_at"), default=_utc_now_iso()),
    )


def _coerce_int(raw: object, *, default: int) -> int:
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return default
    return default


def _coerce_str(raw: object, *, default: str) -> str:
    if isinstance(raw, str) and raw:
        return raw
    return default


def _coerce_status(raw: object, *, default: SessionStatus) -> SessionStatus:
    if raw in {"idle", "running", "completed", "failed"}:
        return raw
    return default


def _coerce_active_agent(raw: object, *, default: str) -> str:
    if not isinstance(raw, str) or not raw:
        return default
    mapping = {
        "intent_router": "main_agent",
        "summary_agent": "main_agent",
        "search_agent": "search_worker",
        "navigation_agent": "navigation_worker",
    }
    return mapping.get(raw, raw)
