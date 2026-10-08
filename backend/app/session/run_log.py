"""Run log: one bounded, numbered event book per session for its latest run.

Events of a run are numbered from 1 and kept in arrival order. Subscribers
replay what they missed, then wait for new events; a sealed and fully
delivered book ends every subscription. Business payloads are stored as-is.

All methods must be called from the event loop thread.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

from app.infra.observability.logger import get_logger, log_ref
from app.session.injector import RunPublisher
from app.session.models import utc_now_iso

logger = get_logger(__name__)

EventKind = Literal["event", "control"]


@dataclass(frozen=True)
class StreamEvent:
    """Transport envelope of one run event."""

    id: int
    session_id: str
    run_id: str
    kind: EventKind
    event: str
    data: dict[str, Any] = field(default_factory=dict)
    output_id: str | None = None
    status: str | None = None
    at: str = field(default_factory=utc_now_iso)

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "session_id": self.session_id,
            "run_id": self.run_id,
            "kind": self.kind,
            "event": self.event,
            "at": self.at,
            "data": self.data,
        }
        if self.output_id is not None:
            payload["output_id"] = self.output_id
        if self.status is not None:
            payload["status"] = self.status
        return payload


class Heartbeat:
    """Yielded by subscribe() when no event arrived within the heartbeat window."""


HEARTBEAT = Heartbeat()


@dataclass
class _Book:
    run_id: str
    events: deque[StreamEvent]
    next_id: int = 1
    sealed: bool = False
    # Highest id pushed out of the bounded deque; cursors below it have a gap.
    evicted_until: int = 0
    wakeup: asyncio.Event = field(default_factory=asyncio.Event)

    def wake(self) -> None:
        self.wakeup.set()
        self.wakeup = asyncio.Event()


class _RunChannel:
    """RunPublisher bound to one run of the log."""

    def __init__(self, log: RunLog, session_id: str, run_id: str) -> None:
        self._log = log
        self._session_id = session_id
        self._run_id = run_id

    def publish(
        self,
        event: str,
        data: dict[str, Any] | None = None,
        *,
        output_id: str | None = None,
    ) -> None:
        self._log.append(
            self._session_id,
            self._run_id,
            kind="event",
            event=event,
            data=data,
            output_id=output_id,
        )


class RunLog:
    """Keep the latest run's events per session and serve ordered subscriptions."""

    def __init__(self, *, max_events_per_run: int = 2000, max_sessions: int = 500) -> None:
        self._max_events_per_run = max(10, max_events_per_run)
        self._max_sessions = max(1, max_sessions)
        self._books: OrderedDict[str, _Book] = OrderedDict()

    def open(self, session_id: str, run_id: str) -> None:
        """Start a fresh book for a new run, replacing the session's previous one."""
        previous = self._books.pop(session_id, None)
        if previous is not None:
            previous.sealed = True
            previous.wake()
        self._books[session_id] = _Book(run_id=run_id, events=deque(maxlen=self._max_events_per_run))
        self._evict_idle_sessions()

    def channel(self, session_id: str, run_id: str) -> RunPublisher:
        """Publisher the runtime uses for business events of this run."""
        return _RunChannel(self, session_id, run_id)

    def append(
        self,
        session_id: str,
        run_id: str,
        *,
        kind: EventKind,
        event: str,
        data: dict[str, Any] | None = None,
        output_id: str | None = None,
        status: str | None = None,
    ) -> StreamEvent | None:
        """Number and store one event; returns None when the book is sealed or replaced."""
        book = self._books.get(session_id)
        if book is None or book.run_id != run_id or book.sealed:
            logger.debug(
                "run_log.dropped session_ref=%s run_ref=%s event=%s",
                log_ref(session_id),
                log_ref(run_id),
                event,
            )
            return None
        item = StreamEvent(
            id=book.next_id,
            session_id=session_id,
            run_id=run_id,
            kind=kind,
            event=event,
            data=dict(data or {}),
            output_id=output_id,
            status=status,
        )
        book.next_id += 1
        if len(book.events) == book.events.maxlen:
            book.evicted_until = book.events[0].id
        book.events.append(item)
        self._books.move_to_end(session_id)
        book.wake()
        return item

    def seal(self, session_id: str, run_id: str) -> None:
        """Refuse further events for this run and let subscribers finish."""
        book = self._books.get(session_id)
        if book is None or book.run_id != run_id or book.sealed:
            return
        book.sealed = True
        book.wake()

    def has_run(self, session_id: str, run_id: str) -> bool:
        book = self._books.get(session_id)
        return book is not None and book.run_id == run_id

    def latest_run_id(self, session_id: str) -> str | None:
        book = self._books.get(session_id)
        return book.run_id if book is not None else None

    async def subscribe(
        self,
        session_id: str,
        run_id: str,
        *,
        after_id: int | None,
        heartbeat_seconds: float,
    ) -> AsyncIterator[StreamEvent | Heartbeat]:
        """订阅指定 run：先补发游标之后已有的事件，再持续等待新事件。

        ``after_id`` 是客户端已经收到的最后一个事件编号，只发送编号更大的事件。
        如果该 session 当前没有对应的 run，订阅立即结束。

        事件队列有长度上限，旧事件可能已被淘汰。如果客户端游标落在这个缺口里，
        先发送 ``stream.reset`` 控制事件，通知客户端从 session 详情重新加载状态；
        然后从仍保留的事件继续发送。run 已封存且没有待发送事件时，订阅结束；
        等待新事件超时则发送一次心跳。
        """
        book = self._books.get(session_id)
        if book is None or book.run_id != run_id:
            return
        cursor = max(0, after_id or 0)
        while True:
            if self._books.get(session_id) is not book:
                return
            if cursor < book.evicted_until:
                cursor = book.evicted_until
                yield StreamEvent(
                    id=cursor,
                    session_id=session_id,
                    run_id=run_id,
                    kind="control",
                    event="stream.reset",
                    data={"head_id": book.next_id - 1},
                )
                continue
            pending = [item for item in book.events if item.id > cursor]
            if pending:
                # 先固定本轮待发送事件的快照；即使发送期间队列淘汰了旧事件，
                # 快照中的事件仍可发完。之后新追加的事件会在下一轮检查。
                for item in pending:
                    cursor = item.id
                    yield item
                continue
            if book.sealed:
                return
            waiter = book.wakeup
            try:
                await asyncio.wait_for(waiter.wait(), timeout=heartbeat_seconds)
            except asyncio.TimeoutError:
                yield HEARTBEAT

    def drop_session(self, session_id: str) -> None:
        """Forget a deleted session's book."""
        book = self._books.pop(session_id, None)
        if book is not None:
            book.sealed = True
            book.wake()

    def _evict_idle_sessions(self) -> None:
        # Only sealed books are evicted; an active run never loses its log.
        overflow = len(self._books) - self._max_sessions
        if overflow <= 0:
            return
        for session_id in [key for key, book in self._books.items() if book.sealed][:overflow]:
            self._books.pop(session_id, None)
