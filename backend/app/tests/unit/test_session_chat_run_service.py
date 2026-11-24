"""Unit tests for chat run preparation outside the event loop."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from app.agent.runtime.session_state import AgentSessionState, SessionOwnershipError
from app.protocol.messages import ChatRequest
from app.services.chat_run_service import ChatRunService


class _RecordingStore:
    """Session store that records calls and can simulate remote latency."""

    def __init__(self, *, delay: float = 0.0) -> None:
        self.delay = delay
        self.states: dict[str, AgentSessionState] = {}
        self.reads: list[str] = []
        self.saves: list[str] = []
        self.active = 0
        self.max_active = 0

    def get_or_create_session(self, session_id: str) -> AgentSessionState:
        self.reads.append(session_id)
        time.sleep(self.delay)
        state = self.states.get(session_id)
        if state is None:
            state = AgentSessionState(session_id=session_id)
            self.states[session_id] = state
        return state

    def save_session(self, state: AgentSessionState) -> None:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delay)
            self.saves.append(state.session_id)
            self.states[state.session_id] = state
        finally:
            self.active -= 1


class _IdleRuns:
    """Run manager stub: not draining and no run active."""

    draining = False

    @staticmethod
    def is_active(session_id: str) -> bool:
        return False


def _service(store: _RecordingStore) -> ChatRunService:
    # Preparation touches only the run manager's admission checks and the store.
    return ChatRunService(runtime=Any, runs=_IdleRuns(), session_store=store)  # type: ignore[arg-type]


def test_new_session_skips_read_and_saves_once() -> None:
    asyncio.run(_new_session_scenario())


async def _new_session_scenario() -> None:
    store = _RecordingStore()
    service = _service(store)

    prepared = await service._prepare(ChatRequest(message="最近的机厅"))

    assert prepared.session_id is not None
    assert prepared.session_id.startswith("s_")
    assert store.reads == []
    assert store.saves == [prepared.session_id]
    assert store.states[prepared.session_id].status == "running"
    assert service._prepare_locks == {}


def test_existing_session_is_read_before_it_is_marked_running() -> None:
    asyncio.run(_existing_session_scenario())


async def _existing_session_scenario() -> None:
    store = _RecordingStore()
    store.states["s_existing"] = AgentSessionState(session_id="s_existing", status="idle")
    service = _service(store)

    prepared = await service._prepare(ChatRequest(session_id="s_existing", message="继续"))

    assert prepared.session_id == "s_existing"
    assert store.reads == ["s_existing"]
    assert store.saves == ["s_existing"]
    assert store.states["s_existing"].status == "running"
    assert service._prepare_locks == {}


def test_ownership_mismatch_does_not_save_or_leak_the_lock() -> None:
    asyncio.run(_ownership_scenario())


async def _ownership_scenario() -> None:
    store = _RecordingStore()
    store.states["s_owned"] = AgentSessionState(session_id="s_owned", client_id="alice")
    service = _service(store)

    with pytest.raises(SessionOwnershipError):
        await service._prepare(ChatRequest(session_id="s_owned", client_id="bob", message="继续"))

    assert store.saves == []
    assert service._prepare_locks == {}


def test_same_session_preparations_are_serialized() -> None:
    asyncio.run(_serialized_scenario())


async def _serialized_scenario() -> None:
    store = _RecordingStore(delay=0.05)
    store.states["s_serial"] = AgentSessionState(session_id="s_serial")
    service = _service(store)

    await asyncio.gather(
        *(service._prepare(ChatRequest(session_id="s_serial", message=f"第{i}次")) for i in range(3))
    )

    assert store.max_active == 1
    assert store.saves == ["s_serial", "s_serial", "s_serial"]
    assert service._prepare_locks == {}


def test_preparation_does_not_block_the_event_loop() -> None:
    asyncio.run(_non_blocking_scenario())


async def _non_blocking_scenario() -> None:
    store = _RecordingStore(delay=0.2)
    store.states["s_slow"] = AgentSessionState(session_id="s_slow")
    service = _service(store)
    ticks = 0
    done = False

    async def tick() -> None:
        nonlocal ticks
        while not done:
            await asyncio.sleep(0.01)
            ticks += 1

    ticker = asyncio.create_task(tick())
    try:
        await service._prepare(ChatRequest(session_id="s_slow", message="继续"))
    finally:
        done = True
        await ticker

    assert ticks >= 5
