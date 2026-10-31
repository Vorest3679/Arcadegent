"""Worker dispatch: isolated execution, then a serialized commit into the parent.

``start`` prepares a dedicated ``AgentSessionState`` and runs the worker loop
without merging artifacts, usage or evidence into the parent. ``finish``
performs those merges in the parent loop's coroutine and in the model's call
order, builds the envelope and restores the active agent.
``spawn`` is the one-shot entry (``start`` + ``finish``); the runtime hook uses
the two halves so parallel workers overlap while their commits stay ordered.

While workers overlap, ``ActiveWorkerSlots`` keeps one live ``active_subagent``:
the most recently started worker, or ``main_agent`` once the last one finishes.
Every worker event still carries its own run id, so the UI can tell them apart.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.agent.artifacts.sink import ArtifactSink
from app.agent.context.turn_recorder import merge_usage_totals, persist_worker_evidence_turns
from app.agent.runtime.agent_loop import STATUS_COMPLETED, STATUS_FAILED, LoopOutcome, LoopSpec
from app.agent.runtime.domain_port import TurnDomain
from app.agent.runtime.events import KernelEvent, emit_agent_changed, publish as publish_event
from app.agent.runtime.lifecycle import (
    LifecycleContext,
    LifecyclePhase,
    LifecycleScope,
    StateView,
    WorkerScope,
)
from app.agent.runtime.budget import RunBudget
from app.agent.runtime.ports import SessionStore
from app.agent.runtime.session_state import (
    AgentSessionState,
    AgentTurn,
    append_worker_run,
    ensure_working_memory_shape,
)
from app.agent.skills.registry import SkillRegistry
from app.agent.subagents.agent_node import AgentNode
from app.agent.subagents.subagent_builder import SubAgentBuilder

ContextFactory = Callable[..., LifecycleContext]
LoopRunner = Callable[[LoopSpec], Awaitable[LoopOutcome]]
LifecycleCall = Callable[[LifecycleContext], Awaitable[None]]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _short(text: str | None, *, limit: int = 120) -> str:
    if not isinstance(text, str):
        return ""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(1, limit - 3)].rstrip()}..."


def _invalid_worker_envelope(*, worker_name: str, task: str) -> dict[str, Any]:
    """Failure envelope for a dispatch naming an unknown worker or an empty task."""
    missing_fields = ["worker", "task"] if not task else ["worker"]
    return {
        "worker": worker_name or "unknown_worker",
        "run_id": f"wrk_{uuid4().hex[:10]}",
        "status": "failed",
        "summary": "Worker dispatch payload was incomplete.",
        "result": {
            "summary": "Worker dispatch payload was incomplete.",
            "missing_fields": ["worker", "task"],
        },
        "artifacts": {},
        "missing_fields": missing_fields,
        "error": "missing worker or task",
        "task": task,
        "task_preview": _short(task, limit=120),
    }


class ActiveWorkerSlots:
    """用于存储每个 AgentSessionState 的活跃 worker slots，
    确保在多个 worker 并行运行时，能够正确地恢复 active_subagent。
    每个 AgentSessionState 可以有多个活跃的 worker slots，
    每个 slot 由 run_id 和 worker_name 组成。
    ActiveWorkerSlots 提供了存储worker的栈结构，
    添加、移除和清理 slots 的方法，以便在 worker 完成或取消时，
    能够正确地恢复 active_subagent。
    """

    def __init__(self) -> None:
        # 元组会被封装成entry，entry[0]是parent_state，entry[1]是slots列表
        self._slots: dict[int, tuple[AgentSessionState, list[tuple[str, str]]]] = {}

    def _entry(self, parent: AgentSessionState) -> list[tuple[str, str]]:
        """查找parent的slots列表，如果不存在则创建一个新的空列表并返回"""
        entry = self._slots.get(id(parent))
        if entry is None:
            entry = (parent, [])
            self._slots[id(parent)] = entry
        return entry[1]

    def add(self, parent: AgentSessionState, *, run_id: str, worker_name: str) -> str:
        """注册新worker slot并返回之前的active_subagent"""
        previous = parent.active_subagent
        self._entry(parent).append((run_id, worker_name))
        return previous

    def next_active(self, parent: AgentSessionState, *, run_id: str) -> str:
        """切换到下一个活跃的worker，如果没有则返回main_agent"""
        entry = self._slots.get(id(parent))
        remaining = [
            name
            for item_run_id, name in (entry[1] if entry is not None else [])
            if item_run_id != run_id # 循环过滤掉当前的run_id，剩下的就是其他活跃的worker
        ]
        return remaining[-1] if remaining else "main_agent"

    def clear(self, parent: AgentSessionState) -> None:
        """Drop every slot for ``parent`` (turn-end safety net)."""
        self._slots.pop(id(parent), None)

    def remove(self, parent: AgentSessionState, *, run_id: str) -> str | None:
        """Remove a finished worker and return the next active name."""
        entry = self._slots.get(id(parent))
        if entry is None:
            return None
        slots = entry[1]
        remaining = [item for item in slots if item[0] != run_id]
        if len(remaining) == len(slots):
            return None
        if remaining:
            self._slots[id(parent)] = (entry[0], remaining)
        else:
            self._slots.pop(id(parent), None)
        return remaining[-1][1] if remaining else "main_agent"


@dataclass
class WorkerRun:
    """One worker invocation: isolated state plus the parent it commits into."""

    parent_state: AgentSessionState
    node: AgentNode
    worker_name: str
    task: str
    run_id: str
    state: AgentSessionState
    scope: WorkerScope
    ctx: LifecycleContext
    budget: RunBudget
    outcome: LoopOutcome | None = None
    final_text: str | None = None
    failed_error: str | None = None
    evidence_committed: bool = False
    committed: bool = False
    envelope: dict[str, Any] | None = None
    slot_registered: bool = False
    slot_released: bool = False
    unmounted: bool = False
    error_emitted: bool = False


@dataclass
class WorkerDispatch:
    """A started worker waiting for its ordered commit (or an invalid envelope)."""

    runner: "WorkerRunner"
    run: WorkerRun | None = None
    envelope: dict[str, Any] | None = None
    error: BaseException | None = None

    async def commit(self) -> dict[str, Any]:
        """Commit in the caller's coroutine; returns the domain envelope."""
        if self.run is None:
            if self.envelope is None:
                raise RuntimeError("worker dispatch has no run or envelope")
            return self.envelope
        return await self.runner.finish(self)


class WorkerRunner:
    """Runs one worker per ``AgentNode`` and merges it back into the parent."""

    def __init__(
        self,
        *,
        request: Any,
        request_payload: dict[str, Any],
        domain: TurnDomain,
        session_store: SessionStore,
        subagent_builder: SubAgentBuilder,
        skill_registry: SkillRegistry | None,
        new_context: ContextFactory,
        emit: LifecycleCall,
        safe_emit: LifecycleCall,
        run_loop: LoopRunner,
        artifacts: ArtifactSink | None = None,
        slots: ActiveWorkerSlots | None = None,
    ) -> None:
        self._request = request
        self._request_payload = request_payload
        self._domain = domain
        self._artifacts = artifacts or ArtifactSink()
        self._session_store = session_store
        self._subagent_builder = subagent_builder
        self._skill_registry = skill_registry
        self._new_context = new_context
        self._emit = emit
        self._safe_emit = safe_emit
        self._run_loop = run_loop
        self._slots = slots or ActiveWorkerSlots()

    def prepare(
        self,
        *,
        parent_state: AgentSessionState,
        worker_name: str,
        task: str,
        budget: RunBudget,
    ) -> WorkerRun:
        """Allocate the worker's isolated state and lifecycle scope."""
        worker_state = AgentSessionState(
            session_id=parent_state.session_id,
            turn_index=parent_state.turn_index,
            active_subagent=worker_name,
            status="running",
            turns=[],
        )
        run_id = f"wrk_{uuid4().hex[:10]}"
        scope = WorkerScope(
            worker_name=worker_name,
            run_id=run_id,
            state=StateView(worker_state),
            parent_state=StateView(parent_state, writable=False),
        )
        ctx = self._new_context(
            LifecycleScope.WORKER,
            LifecyclePhase.MOUNT,
            session_id=parent_state.session_id,
            worker_run_id=run_id,
            worker=scope,
            turn_index=worker_state.turn_index,
        )
        return WorkerRun(
            parent_state=parent_state,
            node=self._subagent_builder.node(worker_name),
            worker_name=worker_name,
            task=task,
            run_id=run_id,
            state=worker_state,
            scope=scope,
            ctx=ctx,
            budget=budget,
        )

    async def start(
        self,
        *,
        state: AgentSessionState,
        worker_name: str,
        task: str,
        spawnable: tuple[str, ...],
        budget: RunBudget,
    ) -> WorkerDispatch:
        """Validate, mount and run a worker; do not merge the parent yet.

        ``CancelledError`` performs the exactly-once cleanup here because the
        run is ending. Other execution errors are stored on the dispatch so the
        ordered ``finish`` phase can recover evidence and restore the parent.
        """
        if worker_name not in spawnable or not task:
            return WorkerDispatch(
                runner=self,
                envelope=_invalid_worker_envelope(worker_name=worker_name, task=task),
            )
        run = self.prepare(
            parent_state=state,
            worker_name=worker_name,
            task=task,
            budget=budget,
        )
        dispatch = WorkerDispatch(runner=self, run=run)
        try:
            await self.run(run)
        except asyncio.CancelledError as exc:
            await self._emit_error(run, exc, reason="cancelled")
            self._recover(run)
            await self._unmount(run)
            raise
        except Exception as exc:
            # Defer the parent merge to finish()/commit order; the tool call
            # still ends as a failed result once that runs.
            await self._emit_error(run, exc, reason="failed")
            dispatch.error = exc
        return dispatch

    async def run(self, run: WorkerRun) -> WorkerRun:
        """Mount the worker and run its loop; no parent memory is merged here.

        The worker's ``active_subagent`` transition is the live UI signal:
        while several workers overlap the parent shows the most recently
        started one, and ``main_agent`` returns once the last one finishes.
        Evidence, usage, artifacts and the envelope wait for ``finish``.
        Errors and cancellation propagate to ``start``, which owns recovery.
        """
        if self._skill_registry is not None:
            await asyncio.to_thread(self._skill_registry.refresh)
        parent = run.parent_state
        run.state.working_memory = ensure_working_memory_shape(run.state.working_memory)
        await self._emit(run.ctx)
        run.state.turns.append(
            AgentTurn(
                role="user",
                content=run.task,
                agent=run.worker_name,
                worker_run_id=run.run_id,
                scope="worker",
                payload={"dispatch": True},
            )
        )
        previous_agent = self._slots.add(
            parent, run_id=run.run_id, worker_name=run.worker_name
        )
        run.slot_registered = True
        parent.active_subagent = run.worker_name
        self._session_store.save_session(parent)
        emit_agent_changed(
            from_agent=previous_agent,
            to_agent=run.worker_name,
            reason="worker.started",
            worker_run_id=run.run_id,
        )
        publish_event(
            KernelEvent.WORKER_STARTED,
            {
                "worker": run.worker_name,
                "run_id": run.run_id,
                "worker_run_id": run.run_id,
                "task_preview": _short(run.task, limit=160),
                "active_subagent": run.worker_name,
            },
        )

        run.outcome = await self._run_loop(
            LoopSpec(
                session_id=parent.session_id,
                state=run.state,
                node=run.node,
                request=self._request,
                request_payload=self._request_payload,
                budget=run.budget,
                scope={"worker_run_id": run.run_id, "worker": run.scope},
                worker_run_id=run.run_id,
                publish_text=False,
                persist=False,
                fail_on_tool_error=True,
            )
        )
        run.final_text = run.outcome.text
        run.failed_error = _worker_failed_error(run.outcome)
        return run

    async def finish(self, dispatch: WorkerDispatch) -> dict[str, Any]:
        """Commit a finished worker in the caller's coroutine, exactly once.

        Promotion, evidence, usage, the envelope and the ``active_subagent``
        restore run here so parallel workers can commit one by one in model
        call order. ``unmounted`` is emitted on success, failure and
        cancellation; a failed commit still recovers evidence once.
        """
        run = dispatch.run
        if run is None:
            if dispatch.envelope is None:
                raise RuntimeError("worker dispatch has no run or envelope")
            return dispatch.envelope
        try:
            if dispatch.error is not None:
                raise dispatch.error
            envelope = self.commit(run)
        except asyncio.CancelledError as exc:
            await self._emit_error(run, exc, reason="cancelled")
            self._recover(run)
            raise
        except Exception as exc:
            await self._emit_error(run, exc, reason="failed")
            self._recover(run)
            raise
        finally:
            await self._unmount(run)
        dispatch.envelope = envelope
        return envelope

    def commit(self, run: WorkerRun) -> dict[str, Any]:
        """Merge a finished worker into the parent; idempotent per run.

        Promotion, evidence, usage, the envelope and the ``active_subagent``
        restore run here so parallel workers (stage 7) can commit one by one in
        the parent loop's coroutine and in model call order.
        """
        if run.committed and run.envelope is not None:
            return run.envelope
        parent = run.parent_state
        promoted_artifacts = self._artifacts.promote(
            child_memory=run.state.working_memory,
            parent_memory=parent.working_memory,
            turn_index=parent.turn_index,
            status="failed" if run.failed_error is not None else "completed",
            run_id=run.run_id,
        )
        parent.working_memory.update(
            deepcopy(self._domain.worker_memory_promotions(run.state.working_memory))
        )
        self._commit_evidence(run)
        envelope = self._domain.build_worker_envelope(
            worker_name=run.worker_name,
            run_id=run.run_id,
            task=run.task,
            worker_memory=run.state.working_memory,
            final_text=run.final_text,
            promoted_artifacts=promoted_artifacts,
            failed_error=run.failed_error,
        )
        envelope["usage"] = deepcopy(run.state.working_memory.get("usage_totals") or {})
        append_worker_run(parent.working_memory, envelope)
        active_after = self._slots.next_active(parent, run_id=run.run_id)
        if envelope["status"] == "failed":
            parent.last_error = envelope["error"]
            parent.working_memory["last_error"] = {"message": envelope["error"]}
            publish_event(
                KernelEvent.WORKER_FAILED,
                {
                    "worker": run.worker_name,
                    "run_id": run.run_id,
                    "worker_run_id": run.run_id,
                    "error": envelope["error"],
                    "active_subagent": active_after,
                },
            )
        else:
            # Error recovery belongs to the batch layer: a successful spawn
            # must not erase a sibling tool failure from the same batch.
            publish_event(
                KernelEvent.WORKER_COMPLETED,
                {
                    "worker": run.worker_name,
                    "run_id": run.run_id,
                    "worker_run_id": run.run_id,
                    "status": envelope["status"],
                    "summary": envelope["summary"],
                    "active_subagent": active_after,
                },
            )
        self._restore_parent(run, reason="worker.completed")
        run.envelope = envelope
        run.committed = True
        return envelope

    async def spawn(
        self,
        *,
        state: AgentSessionState,
        worker_name: str,
        task: str,
        spawnable: tuple[str, ...],
        budget: RunBudget,
    ) -> dict[str, Any]:
        """One-shot dispatch (``start`` + ordered ``finish``)."""
        dispatch = await self.start(
            state=state,
            worker_name=worker_name,
            task=task,
            spawnable=spawnable,
            budget=budget,
        )
        return await dispatch.commit()

    def _commit_evidence(self, run: WorkerRun) -> None:
        """Merge usage and finished worker evidence exactly once."""
        if run.evidence_committed:
            return
        run.evidence_committed = True
        merge_usage_totals(
            parent_memory=run.parent_state.working_memory,
            worker_memory=run.state.working_memory,
        )
        persist_worker_evidence_turns(parent_state=run.parent_state, worker_state=run.state)

    def _restore_parent(self, run: WorkerRun, *, reason: str) -> None:
        """Release the worker slot and restore the next active agent."""
        if run.slot_released:
            return
        run.slot_released = True
        if not run.slot_registered:
            return
        parent = run.parent_state
        target = self._slots.remove(parent, run_id=run.run_id)
        if target is None:
            return
        parent.active_subagent = target
        parent.updated_at = _utc_now_iso()
        self._session_store.save_session(parent)
        emit_agent_changed(
            from_agent=run.worker_name,
            to_agent=target,
            reason=reason,
            worker_run_id=run.run_id,
        )

    def _recover(self, run: WorkerRun) -> None:
        """Error/cancellation cleanup: keep finished evidence, restore the parent."""
        if run.committed:
            return
        self._commit_evidence(run)
        self._restore_parent(run, reason="worker.unmounted")

    async def _unmount(self, run: WorkerRun) -> None:
        """Emit ``unmounted`` exactly once per worker."""
        if run.unmounted:
            return
        run.unmounted = True
        run.ctx.phase = LifecyclePhase.UNMOUNTED
        await self._safe_emit(run.ctx)

    async def _emit_error(self, run: WorkerRun, exc: BaseException, *, reason: str) -> None:
        """Emit the worker error hook once, whatever path reports it."""
        if run.error_emitted:
            return
        run.error_emitted = True
        run.ctx.error = exc
        run.ctx.reason = reason
        run.ctx.phase = LifecyclePhase.ERROR
        await self._safe_emit(run.ctx)


def _worker_failed_error(outcome: LoopOutcome) -> str | None:
    """Worker failure text: model/tool error first, then the step-limit notice."""
    if outcome.status == STATUS_COMPLETED:
        return None
    if outcome.status == STATUS_FAILED:
        return _short(str((outcome.error or {}).get("message") or "model call failed"), limit=240)
    if outcome.pending_tool_error is not None:
        return _short(outcome.pending_tool_error, limit=240)
    return str((outcome.error or {}).get("message") or "worker exhausted its step budget")
