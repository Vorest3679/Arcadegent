"""Unit tests for run state rules and the run manager."""

from __future__ import annotations

import asyncio

import pytest

from app.session.injector import RunContext, RunPublisher
from app.session.models import (
    InvalidTransitionError,
    RunConflictError,
    RunNotFoundError,
    RunRecord,
    ServiceDrainingError,
    SessionBusyError,
    transition,
)
from app.session.run_log import RunLog, StreamEvent
from app.session.runs import RunManager


def _manager() -> tuple[RunManager, RunLog]:
    log = RunLog()
    return RunManager(log=log), log


async def _events(log: RunLog, session_id: str, run_id: str) -> list[StreamEvent]:
    return [
        item
        async for item in log.subscribe(session_id, run_id, after_id=None, heartbeat_seconds=0.05)
        if isinstance(item, StreamEvent)
    ]


def test_transition_rules() -> None:
    record = RunRecord(session_id="s", run_id="r")
    assert transition(record, "running") is True
    assert record.started_at is not None
    assert transition(record, "completed") is True
    assert record.finished_at is not None
    assert transition(record, "completed") is False
    with pytest.raises(InvalidTransitionError):
        transition(record, "cancelled")

    cancelled = RunRecord(session_id="s", run_id="r2")
    with pytest.raises(InvalidTransitionError):
        transition(cancelled, "cancelled")
    transition(cancelled, "cancelling")
    transition(cancelled, "cancelled")
    assert cancelled.is_terminal

    # A cancel that arrives after execution ended keeps the real outcome.
    late = RunRecord(session_id="s", run_id="r3", status="running")
    transition(late, "cancelling")
    transition(late, "completed")
    assert late.status == "completed"


def test_dispatch_runs_in_background_and_finishes_in_order() -> None:
    async def scenario() -> None:
        manager, log = _manager()
        order: list[str] = []

        async def executor(context: RunContext) -> str:
            context.events.publish("tool.started", {"tool": "db"})
            order.append("executor")
            return "done"

        async def on_terminal(record: RunRecord, events: RunPublisher) -> None:
            order.append(f"hook:{record.status}")
            events.publish("assistant.completed", {"reply": "ok"})

        record = manager.dispatch("s1", executor, on_terminal=on_terminal)
        assert record.status == "pending"
        assert manager.is_active("s1")
        events = await _events(log, "s1", record.run_id)

        assert order == ["executor", "hook:completed"]
        assert [(item.kind, item.event, item.status) for item in events] == [
            ("control", "run.state", "running"),
            ("event", "tool.started", None),
            ("event", "assistant.completed", None),
            ("control", "run.state", "completed"),
        ]
        assert [item.id for item in events] == [1, 2, 3, 4]
        assert not manager.is_active("s1")
        assert manager.current("s1").status == "completed"

    asyncio.run(scenario())


def test_same_session_is_mutually_exclusive_until_finished() -> None:
    async def scenario() -> None:
        manager, _log = _manager()
        gate = asyncio.Event()

        async def executor(context: RunContext) -> None:
            await gate.wait()

        first = manager.dispatch("s1", executor)
        with pytest.raises(SessionBusyError):
            manager.dispatch("s1", executor)
        other = manager.dispatch("s2", executor)
        gate.set()
        await asyncio.sleep(0.05)
        assert manager.current("s1").status == "completed"
        assert other.status == "completed"
        second = manager.dispatch("s1", executor)
        assert second.run_id != first.run_id
        await asyncio.sleep(0.05)

    asyncio.run(scenario())


def test_cancel_waits_for_finish_and_repeated_cancels_share_it() -> None:
    async def scenario() -> None:
        manager, log = _manager()
        started = asyncio.Event()
        hook_calls: list[str | None] = []

        async def executor(context: RunContext) -> None:
            started.set()
            await asyncio.sleep(10)

        async def on_terminal(record: RunRecord, events: RunPublisher) -> None:
            await asyncio.sleep(0.02)
            hook_calls.append(record.cancel_reason)
            events.publish("session.failed", {"error": record.cancel_reason})

        record = manager.dispatch("s1", executor, on_terminal=on_terminal)
        await started.wait()
        first, second = await asyncio.gather(
            manager.cancel("s1", record.run_id, reason="first"),
            manager.cancel("s1", record.run_id, reason="second"),
        )
        assert first is second
        assert first.status == "cancelled"
        assert first.cancel_reason == "first"
        assert hook_calls == ["first"]
        assert not manager.is_active("s1")

        events = await _events(log, "s1", record.run_id)
        assert [item.status or item.event for item in events] == [
            "running",
            "cancelling",
            "session.failed",
            "cancelled",
        ]
        # Cancelling a finished run returns its terminal record unchanged.
        assert (await manager.cancel("s1", record.run_id, reason="again")).status == "cancelled"

    asyncio.run(scenario())


def test_cancel_before_the_task_starts_still_finishes_and_releases() -> None:
    async def scenario() -> None:
        manager, _log = _manager()
        ran: list[bool] = []
        hooked: list[str] = []

        async def executor(context: RunContext) -> None:
            ran.append(True)

        async def on_terminal(record: RunRecord, events: RunPublisher) -> None:
            hooked.append(record.status)

        record = manager.dispatch("s1", executor, on_terminal=on_terminal)
        result = await manager.cancel("s1", record.run_id, reason="early")
        assert result.status == "cancelled"
        assert ran == []
        assert hooked == ["cancelled"]
        assert not manager.is_active("s1")
        manager.dispatch("s1", executor)
        await asyncio.sleep(0.01)

    asyncio.run(scenario())


def test_cancel_caller_disconnect_does_not_interrupt_finish() -> None:
    async def scenario() -> None:
        manager, _log = _manager()
        started = asyncio.Event()
        hook_done = asyncio.Event()

        async def executor(context: RunContext) -> None:
            started.set()
            await asyncio.sleep(10)

        async def on_terminal(record: RunRecord, events: RunPublisher) -> None:
            await asyncio.sleep(0.05)
            hook_done.set()

        record = manager.dispatch("s1", executor, on_terminal=on_terminal)
        await started.wait()
        caller = asyncio.create_task(manager.cancel("s1", record.run_id, reason="gone"))
        await asyncio.sleep(0.01)
        caller.cancel()
        await asyncio.wait_for(hook_done.wait(), timeout=1)
        await asyncio.sleep(0.01)
        assert manager.current("s1").status == "cancelled"
        assert not manager.is_active("s1")

    asyncio.run(scenario())


def test_cancel_racing_the_normal_finish_keeps_completion_and_seals() -> None:
    """Regression: a cancel landing after the executor returned, before finishing ran."""

    async def scenario() -> None:
        manager, log = _manager()
        returned = asyncio.Event()
        hooked: list[str] = []

        async def executor(context: RunContext) -> str:
            returned.set()
            return "answer"

        async def on_terminal(record: RunRecord, events: RunPublisher) -> None:
            hooked.append(record.status)

        async def run_and_cancel(start):
            task = asyncio.create_task(start())
            await returned.wait()
            # The executor has returned; its finish task exists but has not run.
            cancelled = await manager.cancel("s1", manager.current("s1").run_id, reason="late")
            return task, cancelled

        # Background run.
        task, cancelled = await run_and_cancel(
            lambda: asyncio.sleep(0, manager.dispatch("s1", executor, on_terminal=on_terminal))
        )
        await task
        assert cancelled.status == "completed"
        assert hooked == ["completed"]
        assert not manager.is_active("s1")
        events = await asyncio.wait_for(_events(log, "s1", cancelled.run_id), timeout=1)
        assert [item.status for item in events if item.kind == "control"] == ["running", "cancelling", "completed"]

        # Inline run: the caller still gets the executor's result.
        returned.clear()
        hooked.clear()
        inline_task, inline_cancelled = await run_and_cancel(
            lambda: manager.run_inline("s1", executor, on_terminal=on_terminal)
        )
        result, record = await inline_task
        assert result == "answer"
        assert record.status == "completed"
        assert inline_cancelled is record
        assert hooked == ["completed"]
        assert not manager.is_active("s1")

    asyncio.run(scenario())


def test_run_inline_returns_its_record_when_the_next_run_starts_first() -> None:
    """Regression: the next run must not break a finished inline run's return."""

    async def scenario() -> None:
        manager, _log = _manager()

        async def quick(context: RunContext) -> str:
            return "first"

        async def start_next_as_soon_as_released() -> RunRecord:
            while manager.is_active("s1") or manager.current("s1") is None:
                await asyncio.sleep(0)
            return manager.dispatch("s1", quick)

        inline = asyncio.create_task(manager.run_inline("s1", quick))
        follower = asyncio.create_task(start_next_as_soon_as_released())
        (result, record), next_record = await asyncio.gather(inline, follower)
        assert result == "first"
        assert record.status == "completed"
        assert next_record.run_id != record.run_id
        await asyncio.sleep(0.01)

    asyncio.run(scenario())


def test_cancel_targets_only_the_named_run() -> None:
    async def scenario() -> None:
        manager, _log = _manager()
        gate = asyncio.Event()

        async def quick(context: RunContext) -> None:
            return None

        async def slow(context: RunContext) -> None:
            await gate.wait()

        old = manager.dispatch("s1", quick)
        await asyncio.sleep(0.01)
        new = manager.dispatch("s1", slow)
        with pytest.raises(RunConflictError):
            await manager.cancel("s1", old.run_id, reason="stale")
        assert manager.current("s1").run_id == new.run_id
        assert manager.is_active("s1")
        with pytest.raises(RunNotFoundError):
            await manager.cancel("s2", new.run_id, reason="wrong session")
        gate.set()
        await asyncio.sleep(0.01)
        with pytest.raises(RunNotFoundError):
            await manager.cancel("s1", old.run_id, reason="stale")

    asyncio.run(scenario())


def test_executor_failure_and_hook_failure_keep_terminal_semantics() -> None:
    async def scenario() -> None:
        manager, log = _manager()

        async def broken(context: RunContext) -> None:
            raise RuntimeError("secret detail")

        async def failing_hook(record: RunRecord, events: RunPublisher) -> None:
            raise OSError("store down")

        record = manager.dispatch("s1", broken)
        events = await _events(log, "s1", record.run_id)
        assert record.status == "failed"
        assert record.error_code == "executor_failed"
        assert events[-1].data == {"error_code": "executor_failed"}
        assert "secret" not in str(events[-1].to_json())

        async def fine(context: RunContext) -> str:
            return "ok"

        result, inline = await manager.run_inline("s2", fine, on_terminal=failing_hook)
        assert result == "ok"
        assert inline.status == "completed"
        assert inline.error_code == "persist_failed"
        assert not manager.is_active("s2")

    asyncio.run(scenario())


def test_run_inline_propagates_errors_after_finishing() -> None:
    async def scenario() -> None:
        manager, _log = _manager()

        async def broken(context: RunContext) -> None:
            raise ValueError("bad input")

        with pytest.raises(ValueError):
            await manager.run_inline("s1", broken)
        assert manager.current("s1").status == "failed"
        assert not manager.is_active("s1")

    asyncio.run(scenario())


def test_drain_cancels_active_runs_and_rejects_new_ones() -> None:
    async def scenario() -> None:
        manager, _log = _manager()

        async def forever(context: RunContext) -> None:
            await asyncio.sleep(10)

        first = manager.dispatch("s1", forever)
        second = manager.dispatch("s2", forever)
        await asyncio.sleep(0.01)
        drain = manager.start_drain(timeout=1)
        assert manager.start_drain(timeout=1) is drain
        assert await drain == 0
        assert first.status == "cancelled"
        assert second.status == "cancelled"
        with pytest.raises(ServiceDrainingError):
            manager.dispatch("s3", forever)

    asyncio.run(scenario())


def test_forget_refuses_active_sessions_and_drops_finished_ones() -> None:
    async def scenario() -> None:
        manager, log = _manager()
        gate = asyncio.Event()

        async def executor(context: RunContext) -> None:
            await gate.wait()

        record = manager.dispatch("s1", executor)
        with pytest.raises(SessionBusyError):
            manager.forget("s1")
        gate.set()
        await asyncio.sleep(0.01)
        manager.forget("s1")
        assert manager.current("s1") is None
        assert not log.has_run("s1", record.run_id)

    asyncio.run(scenario())
