"""Worker dispatch: isolated execution, then a serialized commit into the parent.

``run`` executes the worker loop against a dedicated ``AgentSessionState`` and
never merges artifacts, usage or evidence into the parent, so workers can
overlap once the supervisor allows it (stage 7). ``commit`` performs those
merges in the parent loop's coroutine and in the model's call order.
``spawn`` is the one-shot entry used by the tool layer and guarantees
exactly-once cleanup on success, failure and cancellation (fix C).
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
from app.agent.runtime.events import emit_agent_changed, publish as publish_event
from app.agent.runtime.lifecycle import (
    LifecycleContext,
    LifecyclePhase,
    LifecycleScope,
    StateView,
    WorkerScope,
)
from app.agent.runtime.budget import RunBudget
from app.agent.runtime.session_state import (
    AgentSessionState,
    AgentTurn,
    append_worker_run,
    ensure_working_memory_shape,
)
from app.agent.skills.registry import SkillRegistry
from app.agent.subagents.agent_node import AgentNode
from app.agent.subagents.subagent_builder import SubAgentBuilder
from app.infra.db.protocols import SessionStateRepository
from app.session.events import RunEvent

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


class WorkerRunner:
    """Runs one worker per ``AgentNode`` and merges it back into the parent."""

    def __init__(
        self,
        *,
        request: Any,
        request_payload: dict[str, Any],
        domain: TurnDomain,
        session_store: SessionStateRepository,
        subagent_builder: SubAgentBuilder,
        skill_registry: SkillRegistry | None,
        new_context: ContextFactory,
        emit: LifecycleCall,
        safe_emit: LifecycleCall,
        run_loop: LoopRunner,
        artifacts: ArtifactSink | None = None,
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

    async def run(self, run: WorkerRun) -> WorkerRun:
        """Mount the worker and run its loop; no parent memory is merged here.

        The worker's ``active_subagent`` transition is the live UI signal and
        still writes the parent state here (and saves it), so the front end
        follows the running worker. Evidence, usage, artifacts and the envelope
        wait for ``commit``. Multi-worker bookkeeping — the nearest running
        worker while several overlap, restoring ``main_agent`` only after all
        finish — belongs to the stage 7 supervisor, which is why no tool
        declares ``parallel`` yet. Errors and cancellation propagate to the
        caller, which is responsible for the error lifecycle and ``_recover``.
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
        previous_agent = parent.active_subagent
        parent.active_subagent = run.worker_name
        self._session_store.save_session(parent)
        emit_agent_changed(
            from_agent=previous_agent,
            to_agent=run.worker_name,
            reason="worker.started",
            worker_run_id=run.run_id,
        )
        publish_event(
            RunEvent.WORKER_STARTED,
            {
                "worker": run.worker_name,
                "run_id": run.run_id,
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
        if envelope["status"] == "failed":
            parent.last_error = envelope["error"]
            parent.working_memory["last_error"] = {"message": envelope["error"]}
            publish_event(
                RunEvent.WORKER_FAILED,
                {
                    "worker": run.worker_name,
                    "run_id": run.run_id,
                    "error": envelope["error"],
                    "active_subagent": run.worker_name,
                },
            )
        else:
            # Error recovery belongs to the batch layer: a successful spawn
            # must not erase a sibling tool failure from the same batch.
            publish_event(
                RunEvent.WORKER_COMPLETED,
                {
                    "worker": run.worker_name,
                    "run_id": run.run_id,
                    "status": envelope["status"],
                    "summary": envelope["summary"],
                    "active_subagent": run.worker_name,
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
        """One dispatch with guaranteed, exactly-once cleanup.

        On failure or cancellation the finished evidence is still committed to
        the parent and ``active_subagent`` is restored; the run's terminal
        status stays with the run/session layer.
        """
        if worker_name not in spawnable or not task:
            return _invalid_worker_envelope(worker_name=worker_name, task=task)
        run = self.prepare(parent_state=state, worker_name=worker_name, task=task, budget=budget)
        completed = False
        try:
            await self.run(run)
            envelope = self.commit(run)
            completed = True
            return envelope
        except asyncio.CancelledError as exc:
            self._mark_error(run, exc, reason="cancelled")
            await self._safe_emit(run.ctx)
            raise
        except Exception as exc:
            self._mark_error(run, exc, reason="failed")
            await self._safe_emit(run.ctx)
            raise
        finally:
            if not completed:
                self._recover(run)
            run.ctx.phase = LifecyclePhase.UNMOUNTED
            await self._safe_emit(run.ctx)

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
        parent = run.parent_state
        parent.active_subagent = "main_agent"
        parent.updated_at = _utc_now_iso()
        self._session_store.save_session(parent)
        emit_agent_changed(
            from_agent=run.worker_name,
            to_agent="main_agent",
            reason=reason,
            worker_run_id=run.run_id,
        )

    def _recover(self, run: WorkerRun) -> None:
        """Error/cancellation cleanup: keep finished evidence, restore the parent."""
        if run.committed:
            return
        self._commit_evidence(run)
        self._restore_parent(run, reason="worker.unmounted")

    def _mark_error(self, run: WorkerRun, exc: BaseException, *, reason: str) -> None:
        run.ctx.error = exc
        run.ctx.reason = reason
        run.ctx.phase = LifecyclePhase.ERROR


def _worker_failed_error(outcome: LoopOutcome) -> str | None:
    """Worker failure text: model/tool error first, then the step-limit notice."""
    if outcome.status == STATUS_COMPLETED:
        return None
    if outcome.status == STATUS_FAILED:
        return _short(str((outcome.error or {}).get("message") or "model call failed"), limit=240)
    if outcome.pending_tool_error is not None:
        return _short(outcome.pending_tool_error, limit=240)
    return str((outcome.error or {}).get("message") or "worker exhausted its step budget")
