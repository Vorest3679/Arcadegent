"""Loop outcome and the step budget shared by a turn's main loop and workers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.agent.runtime.loop_guard import LoopGuard

STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_STEP_LIMIT = "step_limit"


@dataclass
class LoopOutcome:
    """How one agent loop (main or worker) ended.

    ``text`` is the final text when the loop produced one, even if the status is
    ``failed`` (a worker that recovered in prose after an unresolved tool error).
    ``error`` carries ``type``/``message``: the model error, an unresolved tool
    error, or the step-limit notice. ``pending_tool_error`` is the unresolved
    tool error still standing when the step budget ran out. ``tool_errors`` is
    every tool failure seen in the loop, in order, including ones a later
    success recovered from.
    """

    status: str
    text: str | None = None
    output_id: str | None = None
    error: dict[str, Any] | None = None
    pending_tool_error: str | None = None
    tool_errors: list[str] = field(default_factory=list)


class RunBudget:
    """Per-loop step cap plus an optional pool shared with child loops.

    The main loop owns the root budget; each worker takes ``child()``, which has
    its own per-loop cap but draws from the same pool, so a turn's total model
    calls are bounded no matter how many workers run.
    """

    def __init__(
        self,
        max_steps: int,
        *,
        total_steps: int | None = None,
        _pool: LoopGuard | None = None,
    ) -> None:
        self._max_steps = max_steps
        self._guard = LoopGuard(max_steps)
        self._pool = _pool if _pool is not None else (LoopGuard(total_steps) if total_steps else None)

    def child(self, max_steps: int | None = None) -> RunBudget:
        return RunBudget(max_steps or self._max_steps, _pool=self._pool)

    @property
    def exhausted(self) -> bool:
        return self._guard.exhausted or (self._pool is not None and self._pool.exhausted)

    def next(self) -> int:
        if self.exhausted:
            raise RuntimeError("max_steps_reached")
        if self._pool is not None:
            self._pool.next()
        return self._guard.next()
