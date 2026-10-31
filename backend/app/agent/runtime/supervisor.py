"""Tool batch supervision: wait policy, model-order settling and cancellation.

The kernel's Watch phase owns the tools it submitted for the current batch and
decides when to continue. Production uses ``WaitPolicy(mode="all")``: a batch
waits for every call it started, and each call's commit phase runs afterwards
in model call order. ``first_or_interval`` is the reserved background strategy:
it returns completed calls plus the calls still pending after one interval;
placeholder results and cross-step delivery are intentionally not wired yet.

Cancellation is always safe: cancelling the waiting coroutine cancels every
tracked call, finalizes each event span and leaves no asyncio task behind.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

from app.agent.tools.execution import ToolDispatcher, ToolTask
from app.agent.tools.registry import ToolExecutionResult
from app.infra.observability.logger import get_logger, log_ref

logger = get_logger(__name__)

WaitMode = Literal["all", "first_or_interval"]


@dataclass(frozen=True)
class WaitPolicy:
    """How the supervisor waits for a submitted batch.

    ``all`` is the only production policy. ``first_or_interval`` returns after
    any call completes or after ``interval_s`` seconds, whichever comes first.
    """

    mode: WaitMode = "all"
    interval_s: float = 5.0


@dataclass(frozen=True)
class Settled:
    """Calls settled by one wait, in model call order.

    ``pending`` is always empty for ``mode="all"``. The background strategy
    returns the calls still running so a future loop can start another step
    while keeping their tool-call/result pairing.
    """

    done: tuple[ToolTask, ...] = ()
    pending: tuple[ToolTask, ...] = ()


class LoopSupervisor:
    """Tracks the batch's tool tasks and waits for them under a policy."""

    def __init__(self, *, dispatcher: ToolDispatcher, policy: WaitPolicy | None = None) -> None:
        self._dispatcher = dispatcher
        self._policy = policy or WaitPolicy()
        self._tracked: list[ToolTask] = []

    @property
    def policy(self) -> WaitPolicy:
        return self._policy

    @property
    def tracked(self) -> tuple[ToolTask, ...]:
        return tuple(self._tracked)

    def track(self, *tasks: ToolTask) -> None:
        """Register submitted calls so cancellation can reach them."""
        for task in tasks:
            if not any(item is task for item in self._tracked):
                self._tracked.append(task) # 把刚 submit 的 ToolTask 登记到 self._tracked

    def release(self, task: ToolTask) -> None:
        """Stop tracking a call whose result was settled and recorded."""
        self._tracked = [item for item in self._tracked if item is not task]
        # 把已经 settle + 记录完的 ToolTask 从清单移除

    async def wait(self, tasks: list[ToolTask] | tuple[ToolTask, ...]) -> Settled:
        """Wait for ``tasks`` according to the configured policy.

        ``mode="all"`` waits for every task but deliberately does not release
        them: the batch recorder must still settle/record them in model order.
        It calls ``release`` after recording, or ``cancel_all`` on failure.
        ``mode="first_or_interval"`` releases the calls it returns as done and
        keeps the pending ones tracked for a later wait. Cancellation of the
        waiting coroutine cancels and finalizes every tracked call before the
        ``CancelledError`` propagates.
        """
        if not tasks:
            return Settled()
        self.track(*tasks)
        if self._policy.mode == "all":
            try:
                await asyncio.gather(
                    *(task.task for task in tasks if task.task is not None),
                    return_exceptions=True,
                )
            except asyncio.CancelledError:
                await self.cancel_all()
                raise
            return Settled(done=tuple(tasks))
        return await self._wait_first_or_interval(tasks)

    async def cancel_all(self) -> tuple[ToolExecutionResult, ...]:
        """Cancel and finalize every tracked call, in submission order.

        Finalized calls (already committed, for example a fast sibling that
        completed before the cancellation) keep their original result, so
        calling this again is safe. Each result is returned so the caller can
        decide whether to record it. A non-cancellation commit error is logged
        and does not stop the remaining calls from being finalized; an outer
        cancellation still propagates immediately.
        """
        results: list[ToolExecutionResult] = []
        tracked = list(self._tracked)
        # Signal every unfinished call first: a second outer cancellation while
        # finalizing one call must not leave later siblings running.
        for task in tracked:
            if task.task is not None and not task.task.done():
                task.task.cancel()
        for task in tracked:
            try:
                results.append(await self._dispatcher.cancel(task))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "tool.cancel_failed tool_ref=%s call_ref=%s",
                    log_ref(task.call.name),
                    log_ref(task.call.call_id),
                )
            finally:
                self.release(task)
        return tuple(results)

    async def _wait_first_or_interval(
        self,
        tasks: list[ToolTask] | tuple[ToolTask, ...],
    ) -> Settled:
        """Reserved: return after one completion or one interval."""
        # A task without an asyncio.Task can never make progress; treat it as
        # done so it does not stay in ``pending`` forever. Settling it will
        # surface the programmer error.
        done_ids = {
            id(task.task)
            for task in tasks
            if task.task is not None and task.task.done()
        }
        missing_ids = {id(task) for task in tasks if task.task is None}
        running = [
            task.task
            for task in tasks
            if task.task is not None and not task.task.done()
        ]
        if running:
            try:
                completed, _ = await asyncio.wait(
                    running,
                    timeout=self._policy.interval_s,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            except asyncio.CancelledError:
                await self.cancel_all()
                raise
            done_ids.update(id(item) for item in completed)
        done = tuple(
            task
            for task in tasks
            if id(task) in missing_ids or (task.task is not None and id(task.task) in done_ids)
        )
        done_set = {id(task) for task in done}
        pending = tuple(task for task in tasks if id(task) not in done_set)
        for task in done:
            self.release(task)
        return Settled(done=done, pending=pending)


__all__ = ["LoopSupervisor", "Settled", "WaitMode", "WaitPolicy"]


# Placeholder contract for the reserved background strategy (not wired):
# a pending task keeps its ``call``/``prepared_args``/``argument_evidence`` so a
# later step can present a synthetic ``{"status": "pending", "call_id": ...}``
# result and deliver the real envelope after the tool-call/result pair for the
# original model call has been produced. No production path calls this today.
