"""Run registry: one active run per session, its task, cancellation and finish.

The manager allocates run ids, owns the asyncio tasks, and finishes every run
exactly once in a fixed order: terminal status -> terminal hook (application
persistence) -> terminal ``run.state`` event -> seal the log -> release the
session. It never inspects business data.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from app.infra.observability.logger import get_logger, log_exception_frames, log_ref
from app.session.injector import RunContext, RunExecutor, TerminalHook
from app.session.models import (
    InvalidTransitionError,
    RunConflictError,
    RunNotFoundError,
    RunRecord,
    RunStatus,
    ServiceDrainingError,
    SessionBusyError,
    transition,
    utc_now_iso,
)
from app.session.run_log import RunLog

logger = get_logger(__name__)


class RunManager:
    """Allocate runs, own their tasks and finish them exactly once."""

    def __init__(self, *, log: RunLog) -> None:
        self._log = log
        self._current: dict[str, RunRecord] = {}  # session_id -> latest run
        self._runs: dict[str, RunRecord] = {}  # run_id -> record (latest run per session only)
        self._tasks: dict[str, asyncio.Task[Any]] = {}
        self._hooks: dict[str, TerminalHook] = {}
        self._finishers: dict[str, asyncio.Task[RunRecord]] = {}
        self._done: dict[str, asyncio.Future[RunRecord]] = {}
        self._active: set[str] = set()
        self._draining = False
        self._drain_task: asyncio.Task[int] | None = None

    # -- queries ----------------------------------------------------------
    def current(self, session_id: str) -> RunRecord | None:
        """The session's current or most recent run."""
        return self._current.get(session_id)

    def is_active(self, session_id: str) -> bool:
        """Whether the session still holds an unfinished run."""
        return session_id in self._active

    # -- execution --------------------------------------------------------
    def dispatch(
        self,
        session_id: str,
        executor: RunExecutor,
        *,
        on_terminal: TerminalHook | None = None,
    ) -> RunRecord:
        """Start ``executor`` as a background task and return the accepted run."""
        context = self._accept(session_id, on_terminal)
        task = asyncio.create_task(self._execute(context, executor), name=f"session-run-{context.run_id}")
        self._tasks[context.run_id] = task
        task.add_done_callback(lambda done: self._on_task_done(context, done))
        return self._runs[context.run_id]

    async def run_inline(
        self,
        session_id: str,
        executor: RunExecutor,
        *,
        on_terminal: TerminalHook | None = None,
    ) -> tuple[Any, RunRecord]:
        """Run ``executor`` in the caller's task with the same lifecycle as dispatch."""
        context = self._accept(session_id, on_terminal)
        # Keep the record: once finished, a next run may replace the registry entry.
        record = self._runs[context.run_id]
        current_task = asyncio.current_task()
        if current_task is not None:
            self._tasks[context.run_id] = current_task
        result = await self._execute(context, executor)
        return result, record

    # -- cancel / drain -----------------------------------------------------
    async def cancel(self, session_id: str, run_id: str, *, reason: str) -> RunRecord:
        """Cancel one run and wait until it is fully finished.

        Only the first request cancels the task; later requests wait for the
        same finish and keep the first reason. When this returns, the task has
        stopped, the terminal hook ran and the log is sealed.
        """
        record = self._runs.get(run_id)
        if record is None or record.session_id != session_id:
            current = self._current.get(session_id)
            if current is not None and not current.is_terminal:
                raise RunConflictError(session_id, run_id)
            raise RunNotFoundError(session_id, run_id)
        if record.is_terminal: # is_terminal=true表示已经进入终止流程，直接返回
            return record
        if record.status != "cancelling":
            transition(record, "cancelling")
            record.cancel_reason = reason
            self._publish_state(record)
            task = self._tasks.get(run_id)
            # Once finishing has started, execution is over: only wait for it.
            if run_id not in self._finishers and task is not None and not task.done():
                task.cancel() # 这里task是asyncio任务，开始取消
            logger.info("run.cancel session_ref=%s run_ref=%s", log_ref(session_id), log_ref(run_id))
        return await asyncio.shield(self._done[run_id]) #保护取消不被打扰

    def start_drain(self, timeout: float) -> asyncio.Task[int]:
        """Begin draining once; later calls return the same task."""
        if self._drain_task is None:
            self._drain_task = asyncio.get_running_loop().create_task(self.drain(timeout)) 
            #create_task创建一个异步任务，开始执行drain方法
        return self._drain_task

    async def drain(self, timeout: float) -> int:
        """Stop accepting runs, cancel active ones and wait; return how many did not finish."""
        self._draining = True
        active = [record for record in self._current.values() if not record.is_terminal]
        if not active:
            return 0
        waiters = [
            asyncio.ensure_future(self.cancel(record.session_id, record.run_id, reason="service_shutdown"))
            for record in active
        ] # 创建一个取消任务列表，等待所有活跃的run被取消
        finished, pending = await asyncio.wait(waiters, timeout=timeout)
        for waiter in finished:
            if waiter.exception() is not None:
                logger.warning("run.drain.cancel_failed exception_type=%s", type(waiter.exception()).__name__)
        if pending:
            logger.warning("run.drain.timeout unfinished=%s", len(pending))
        return len(pending)

    def forget(self, session_id: str) -> None:
        """Drop a deleted session's run record and log."""
        if self.is_active(session_id):
            raise SessionBusyError(session_id)
        record = self._current.pop(session_id, None)
        if record is not None:
            self._runs.pop(record.run_id, None)
            self._done.pop(record.run_id, None)
        self._log.drop_session(session_id)

    # -- internals --------------------------------------------------------
    def _accept(self, session_id: str, on_terminal: TerminalHook | None) -> RunContext:
        if self._draining:
            raise ServiceDrainingError()
        if session_id in self._active:
            raise SessionBusyError(session_id)
        run_id = f"r_{uuid4().hex[:12]}"
        record = RunRecord(session_id=session_id, run_id=run_id)
        previous = self._current.get(session_id)
        if previous is not None:
            self._runs.pop(previous.run_id, None)
            self._done.pop(previous.run_id, None)
        self._current[session_id] = record
        self._runs[run_id] = record
        self._active.add(session_id)
        self._done[run_id] = asyncio.get_running_loop().create_future()
        if on_terminal is not None:
            self._hooks[run_id] = on_terminal
        self._log.open(session_id, run_id)
        logger.debug("run.accepted session_ref=%s run_ref=%s", log_ref(session_id), log_ref(run_id))
        return RunContext(session_id=session_id, run_id=run_id, events=self._log.channel(session_id, run_id))

    async def _execute(self, context: RunContext, executor: RunExecutor) -> Any:
        record = self._runs[context.run_id]
        try:
            if record.status == "pending":
                transition(record, "running")
                self._publish_state(record)
            result = await executor(context)
        except asyncio.CancelledError:
            await self._finish(context, "cancelled")
            raise
        except Exception as exc:
            logger.error(
                "run.failed session_ref=%s run_ref=%s exception_type=%s app_frames=%s",
                log_ref(context.session_id),
                log_ref(context.run_id),
                type(exc).__name__,
                log_exception_frames(exc),
            )
            await self._finish(context, "failed", error_code="executor_failed")
            raise
        # The executor returned, so the run completed even if a cancel arrived
        # after its last await.
        await self._finish(context, "completed")
        return result

    def _on_task_done(self, context: RunContext, task: asyncio.Task[Any]) -> None:
        """Finish runs whose task was cancelled before its coroutine started."""
        if not task.cancelled():
            # Retrieve the exception so asyncio does not report it as unhandled;
            # _execute has already logged and finished the run.
            task.exception()
        done = self._done.get(context.run_id)
        if done is None or done.done() or context.run_id in self._finishers:
            return
        asyncio.get_running_loop().create_task(self._finish(context, "cancelled"))

    async def _finish(self, context: RunContext, status: RunStatus, *, error_code: str | None = None) -> RunRecord:
        """Finish a run exactly once, including the terminal hook and log seal.
        What's different from _finish_once is that this method ensures that 
        only one coroutine performs the finish, 
        while others await the same result. 
        It also shields the finish from cancellation of the caller.
        """
        done = self._done.get(context.run_id)
        if done is not None and done.done():
            return done.result()
        finisher = self._finishers.get(context.run_id)
        if finisher is None:
            finisher = asyncio.get_running_loop().create_task(self._finish_once(context, status, error_code))
            self._finishers[context.run_id] = finisher
        # Shield so a second cancellation of the caller cannot interrupt the
        # terminal hook or leave the session reserved.
        return await asyncio.shield(finisher)

    async def _finish_once(self, context: RunContext, status: RunStatus, error_code: str | None) -> RunRecord:
        record = self._runs[context.run_id]
        try:
            self._settle(record, status, error_code)
            hook = self._hooks.pop(context.run_id, None)
            if hook is not None:
                try:
                    await hook(record, context.events) # 执行终止钩子，进行持久化操作
                except Exception as exc:
                    # The run keeps its terminal status; the client still gets
                    # the terminal event and only sees the persistence code.
                    record.error_code = record.error_code or "persist_failed"
                    logger.error(
                        "run.terminal_hook.failed session_ref=%s run_ref=%s exception_type=%s app_frames=%s",
                        log_ref(context.session_id),
                        log_ref(context.run_id),
                        type(exc).__name__,
                        log_exception_frames(exc),
                    )
        finally:
            # Always close the stream and release the session, even if settling
            # or the hook failed unexpectedly, so no SSE subscriber waits forever.
            try:
                self._publish_state(record)
            finally:
                self._log.seal(context.session_id, context.run_id)
            self._tasks.pop(context.run_id, None)
            self._finishers.pop(context.run_id, None)
            self._active.discard(context.session_id)
            done = self._done.get(context.run_id)
            if done is not None and not done.done():
                done.set_result(record)
        logger.debug(
            "run.finished session_ref=%s run_ref=%s status=%s",
            log_ref(context.session_id),
            log_ref(context.run_id),
            record.status,
        )
        return record

    @staticmethod
    def _settle(record: RunRecord, status: RunStatus, error_code: str | None) -> None:
        """Move the record to its terminal status, reconciling a pending cancel."""
        try:
            if status == "cancelled" and record.status in {"pending", "running"}:
                transition(record, "cancelling")
            transition(record, status, error_code=error_code)
        except InvalidTransitionError as exc:
            # A programming error; still end the run so it cannot stay open.
            logger.error(
                "run.settle.invalid session_ref=%s run_ref=%s detail=%s",
                log_ref(record.session_id),
                log_ref(record.run_id),
                exc,
            )
            if not record.is_terminal:
                record.status = "failed"
                record.error_code = "invalid_transition"
                record.finished_at = utc_now_iso()

    def _publish_state(self, record: RunRecord) -> None:
        data: dict[str, Any] = {}
        if record.error_code:
            data["error_code"] = record.error_code
        self._log.append(
            record.session_id,
            record.run_id,
            kind="control",
            event="run.state",
            data=data,
            status=record.status,
        ) # 声明一条 run.state 事件，表示run的状态发生了变化，可能是pending->running->completed/cancelled/failed
