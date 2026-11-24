"""Combine several plugin domains into the single domain the kernel receives.

The kernel knows one ``TurnDomain``. Plugins each contribute an optional
``BaseTurnDomain``; this composite fans lifecycle notifications out in install
order and combines data-producing policies. For a single plugin the result is
identical to using that domain directly.

The kernel's lifecycle guarantees stay in ``LifecycleDispatcher`` around this
object: kernel-owned memory protection, ``StateView`` write limits and
``emit``/``emit_safe`` error semantics are unchanged. A failing plugin hook does
not skip the hooks of later plugins; the first failure is re-raised afterwards
with a note naming the plugin and hook.
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from typing import Any

from app.agent.artifacts.model import Artifact
from app.agent.plugins.spec import PluginError
from app.agent.runtime.domain_port import BaseTurnDomain, ToolResultSignal
from app.agent.runtime.lifecycle import LifecycleContext, _is_default_hook
from app.agent.runtime.session_state import AgentSessionState
from app.agent.tools.registry import ToolExecutionResult

# Policies whose result cannot be merged: at most one plugin may override each.
_EXCLUSIVE_METHODS = (
    "build_response",
    "fallback_reply",
    "turn_display_snapshot",
    "build_worker_envelope",
)


class CompositeTurnDomain(BaseTurnDomain):
    """Fan out lifecycle hooks and merge data policies from several domains."""

    def __init__(self, domains: Sequence[tuple[str, BaseTurnDomain]] = ()) -> None:
        self._domains = tuple(domains)
        default = BaseTurnDomain()
        self._exclusive: dict[str, BaseTurnDomain] = {}
        for method in _EXCLUSIVE_METHODS:
            owners = [(name, domain) for name, domain in self._domains
                      if not _is_default_hook(domain, method, BaseTurnDomain)]
            if len(owners) > 1:
                names = ",".join(name for name, _domain in owners)
                raise PluginError(f"plugin:{owners[1][0]}:exclusive_hook_conflict:{method}:plugins:{names}")
            self._exclusive[method] = owners[0][1] if owners else default

    @property
    def plugin_names(self) -> tuple[str, ...]:
        return tuple(name for name, _domain in self._domains)

    def plugin_domains(self) -> tuple[tuple[str, BaseTurnDomain], ...]:
        """The combined ``(plugin name, domain)`` pairs, in install order."""
        return self._domains

    # -- lifecycle hooks -------------------------------------------------------

    async def _emit(self, hook: str, ctx: LifecycleContext) -> None:
        first: BaseException | None = None
        for name, domain in self._domains:
            if _is_default_hook(domain, hook):
                continue
            try:
                result = getattr(domain, hook)(ctx)
                if inspect.isawaitable(result):
                    await result
            except BaseException as exc:
                exc.add_note(f"plugin={name} hook={hook}")
                if not isinstance(exc, Exception):
                    raise  # cancellation and interpreter exits are not deferred
                first = first or exc
        if first is not None:
            raise first

    async def on_run_create(self, ctx: LifecycleContext) -> None:
        await self._emit("on_run_create", ctx)

    async def on_run_mount(self, ctx: LifecycleContext) -> None:
        await self._emit("on_run_mount", ctx)

    async def on_run_unmounted(self, ctx: LifecycleContext) -> None:
        await self._emit("on_run_unmounted", ctx)

    async def on_turn_create(self, ctx: LifecycleContext) -> None:
        await self._emit("on_turn_create", ctx)

    async def on_turn_mount(self, ctx: LifecycleContext) -> None:
        await self._emit("on_turn_mount", ctx)

    async def on_turn_before_unmount(self, ctx: LifecycleContext) -> None:
        await self._emit("on_turn_before_unmount", ctx)

    async def on_turn_unmounted(self, ctx: LifecycleContext) -> None:
        await self._emit("on_turn_unmounted", ctx)

    async def on_step_start(self, ctx: LifecycleContext) -> None:
        await self._emit("on_step_start", ctx)

    async def on_step_update(self, ctx: LifecycleContext) -> None:
        await self._emit("on_step_update", ctx)

    async def on_step_end(self, ctx: LifecycleContext) -> None:
        await self._emit("on_step_end", ctx)

    async def on_worker_mount(self, ctx: LifecycleContext) -> None:
        await self._emit("on_worker_mount", ctx)

    async def on_worker_unmounted(self, ctx: LifecycleContext) -> None:
        await self._emit("on_worker_unmounted", ctx)

    async def on_error(self, ctx: LifecycleContext) -> None:
        await self._emit("on_error", ctx)

    # -- data-producing policies ----------------------------------------------

    @staticmethod
    def _merge(method: str, values: Sequence[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        owners: dict[str, str] = {}
        for name, value in values:
            for key, item in value.items():
                if key in merged:
                    raise PluginError(f"plugin:{name}:{method}_conflict:{key}:plugins:{owners[key]},{name}")
                merged[key] = item
                owners[key] = name
        return merged

    def runtime_hints(self, state: AgentSessionState) -> dict[str, Any]:
        return self._merge("runtime_hints", [(n, d.runtime_hints(state)) for n, d in self._domains])

    def session_event_fields(self, state: AgentSessionState) -> dict[str, Any]:
        return self._merge("session_event_fields", [(n, d.session_event_fields(state)) for n, d in self._domains])

    def turn_summary(self, state: AgentSessionState) -> str:
        summaries = (domain.turn_summary(state).strip() for _name, domain in self._domains)
        return " | ".join(item for item in summaries if item)

    def tool_result_signal(self, *, state: AgentSessionState, result: ToolExecutionResult) -> ToolResultSignal:
        signals = [(name, domain.tool_result_signal(state=state, result=result)) for name, domain in self._domains]
        return ToolResultSignal(
            events=tuple(event for _name, signal in signals for event in signal.events),
            completed_fields=self._merge(
                "tool_result_signal_fields", [(name, dict(signal.completed_fields)) for name, signal in signals]
            ),
        )

    def artifacts_from_result(self, *, state: AgentSessionState, result: ToolExecutionResult) -> tuple[Artifact, ...]:
        return tuple(
            artifact
            for _name, domain in self._domains
            for artifact in domain.artifacts_from_result(state=state, result=result)
        )

    def worker_memory_promotions(self, worker_memory: dict[str, Any]) -> dict[str, Any]:
        return self._merge(
            "worker_memory_promotions", [(n, d.worker_memory_promotions(worker_memory)) for n, d in self._domains]
        )

    def build_worker_envelope(
        self,
        *,
        worker_name: str,
        run_id: str,
        task: str,
        worker_memory: dict[str, Any],
        final_text: str | None,
        promoted_artifacts: dict[str, Any],
        failed_error: str | None,
    ) -> dict[str, Any]:
        return self._exclusive["build_worker_envelope"].build_worker_envelope(
            worker_name=worker_name,
            run_id=run_id,
            task=task,
            worker_memory=worker_memory,
            final_text=final_text,
            promoted_artifacts=promoted_artifacts,
            failed_error=failed_error,
        )

    def fallback_reply(self, state: AgentSessionState, request: Any) -> str:
        return self._exclusive["fallback_reply"].fallback_reply(state, request)

    def turn_display_snapshot(self, state: AgentSessionState) -> dict[str, Any] | None:
        return self._exclusive["turn_display_snapshot"].turn_display_snapshot(state)

    async def build_response(self, *, session_id: str, state: AgentSessionState, final_text: str) -> Any:
        return await self._exclusive["build_response"].build_response(
            session_id=session_id, state=state, final_text=final_text
        )


__all__ = ["CompositeTurnDomain"]
