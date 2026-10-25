"""Step budgets: a per-loop cap plus a pool shared by a turn's loops."""

from __future__ import annotations


class LoopGuard:
    """Track loop progression and enforce max step limits."""

    def __init__(self, max_steps: int) -> None:
        self._max_steps = max(1, max_steps)
        self._step = 0

    def next(self) -> int:
        if self._step >= self._max_steps:
            raise RuntimeError("max_steps_reached")
        self._step += 1
        return self._step

    @property
    def exhausted(self) -> bool:
        return self._step >= self._max_steps


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
