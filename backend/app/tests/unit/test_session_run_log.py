"""Unit tests for the run-bound event log."""

from __future__ import annotations

import asyncio

from app.session.run_log import HEARTBEAT, RunLog, StreamEvent


async def _collect(log: RunLog, session_id: str, run_id: str, *, after_id: int | None = None) -> list:
    items = []
    async for item in log.subscribe(session_id, run_id, after_id=after_id, heartbeat_seconds=0.05):
        items.append(item)
    return items


def test_events_are_numbered_per_run_and_replayed_after_cursor() -> None:
    async def scenario() -> None:
        log = RunLog()
        log.open("s1", "r1")
        channel = log.channel("s1", "r1")
        channel.publish("tool.started", {"tool": "db"})
        channel.publish("assistant.token", {"delta": "hi"}, output_id="o1")
        log.seal("s1", "r1")

        items = await _collect(log, "s1", "r1", after_id=1)
        assert [(item.id, item.event) for item in items] == [(2, "assistant.token")]
        assert items[0].output_id == "o1"
        assert items[0].to_json()["run_id"] == "r1"

        log.open("s1", "r2")
        log.channel("s1", "r2").publish("tool.started")
        log.seal("s1", "r2")
        assert [item.id for item in await _collect(log, "s1", "r2")] == [1]

    asyncio.run(scenario())


def test_subscriber_receives_live_events_heartbeats_and_ends_after_seal() -> None:
    async def scenario() -> None:
        log = RunLog()
        log.open("s1", "r1")
        channel = log.channel("s1", "r1")
        received: list = []

        async def follow() -> None:
            async for item in log.subscribe("s1", "r1", after_id=None, heartbeat_seconds=0.02):
                received.append(item)

        follower = asyncio.create_task(follow())
        await asyncio.sleep(0.05)
        channel.publish("tool.started")
        await asyncio.sleep(0)
        channel.publish("tool.completed")
        log.seal("s1", "r1")
        await asyncio.wait_for(follower, timeout=1)

        events = [item.event for item in received if isinstance(item, StreamEvent)]
        assert events == ["tool.started", "tool.completed"]
        assert any(item is HEARTBEAT for item in received)

    asyncio.run(scenario())


def test_sealed_or_replaced_runs_drop_late_events() -> None:
    async def scenario() -> None:
        log = RunLog()
        log.open("s1", "r1")
        old = log.channel("s1", "r1")
        log.seal("s1", "r1")
        old.publish("tool.started")
        assert await _collect(log, "s1", "r1") == []

        log.open("s1", "r2")
        old.publish("late.event")
        assert log.append("s1", "r1", kind="event", event="late") is None
        assert not log.has_run("s1", "r1")
        assert log.has_run("s1", "r2")

    asyncio.run(scenario())


def test_evicted_cursor_yields_stream_reset_then_continues() -> None:
    async def scenario() -> None:
        log = RunLog(max_events_per_run=10)
        log.open("s1", "r1")
        channel = log.channel("s1", "r1")
        for index in range(15):
            channel.publish("assistant.token", {"delta": str(index)})
        log.seal("s1", "r1")

        items = await _collect(log, "s1", "r1", after_id=2)
        assert items[0].event == "stream.reset"
        assert items[0].kind == "control"
        assert items[0].id == 5
        assert items[0].data == {"head_id": 15}
        assert [item.id for item in items[1:]] == list(range(6, 16))

    asyncio.run(scenario())


def test_drop_session_ends_subscription_and_only_sealed_books_are_evicted() -> None:
    async def scenario() -> None:
        log = RunLog(max_sessions=1)
        log.open("s1", "r1")
        log.open("s2", "r2")
        # s1 is still active, so it survives the session limit.
        assert log.has_run("s1", "r1")
        log.seal("s1", "r1")
        log.open("s3", "r3")
        assert not log.has_run("s1", "r1")

        follower = asyncio.create_task(_collect(log, "s2", "r2"))
        await asyncio.sleep(0.01)
        log.drop_session("s2")
        assert await asyncio.wait_for(follower, timeout=1) == []

    asyncio.run(scenario())
