"""``LocalToolProvider``: a mutable catalog of in-process tools built from ``ToolSpec``.

Every entry records its origin:

* ``manifest`` — loaded from a host configuration file (a JSON manifest, a
  plugin bundle, …) by :meth:`LocalToolProvider.load` and owned by that file:
  changed through the file plus another ``load``, not through updates. The
  source is trusted host configuration, so it may declare reserved
  capabilities (``invoke_worker`` declares ``spawn_agent``);
* ``registered`` — added at runtime; fully manageable, but may not declare
  ``RESERVED_CAPABILITIES``.

Writes are batch-atomic: a batch is validated as a whole and either applied
completely or not at all. Each write swaps in a new entry map, so a reader that
already holds a snapshot (for example tool definitions sent to the model in
this step) is never mutated underneath. A tool removed between definition and
execution fails that call with ``unknown_tool``.

The provider knows nothing about where specs come from: a manifest adapter
subclasses it (or builds it) and hands the parsed specs to :meth:`load`.
Only two write paths exist, one per origin: :meth:`add` for runtime
registration and :meth:`load` for a trusted source's full tool set.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from app.agent.tools.base import ProviderExecutionResult, ToolDescriptor, ToolOutcome
from app.agent.tools.spec import ToolContext, ToolSpec, ToolSpecError

ToolOrigin = Literal["manifest", "registered"]
_MUTABLE_ORIGINS: frozenset[str] = frozenset({"registered"})


@dataclass(frozen=True)
class LocalToolEntry:
    spec: ToolSpec
    origin: ToolOrigin
    descriptor: ToolDescriptor


class LocalToolProvider:
    """In-process ``ToolProvider`` whose tools can be added, updated and removed.

    It satisfies :class:`ToolProvider` structurally (duck typing); it does not
    subclass the protocol, so runtime dispatch goes through the concrete
    :meth:`execute` below rather than the protocol declaration.
    以结构化方式（鸭子类型）满足 ``ToolProvider`` 协议，不显式继承，执行走下面的具体方法。
    """

    def __init__(
        self,
        *,
        provider_name: str = "local",
        services: Mapping[str, Any] | None = None,
        specs: Iterable[ToolSpec] = (),
    ) -> None:
        self._provider_name = provider_name
        self._services: dict[str, Any] = dict(services or {})
        self._context = ToolContext(self._resolve_service)
        self._entries: Mapping[str, LocalToolEntry] = MappingProxyType({})
        self._revision = 0
        if specs:
            self.add(specs)

    # -- ToolProvider protocol -------------------------------------------------

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def revision(self) -> int:
        """Incremented on every successful write."""
        return self._revision

    async def get_tools(self) -> dict[str, ToolDescriptor]:
        return {name: entry.descriptor for name, entry in self._entries.items()}

    async def execute(
        self,
        *,
        tool_name: str,
        raw_arguments: dict[str, Any],
        validated_arguments: Any | None = None,
    ) -> ProviderExecutionResult:
        entry = self._entries.get(tool_name)
        if entry is None:
            raise ValueError(f"unknown_tool:{tool_name}")
        executor = entry.spec.executor
        payload = validated_arguments if isinstance(validated_arguments, dict) else raw_arguments
        if inspect.iscoroutinefunction(executor):
            raw_output = await executor(self._context, payload)
        else:
            raw_output = await asyncio.to_thread(executor, self._context, payload)
            if inspect.isawaitable(raw_output):
                raw_output = await raw_output
        if isinstance(raw_output, ToolOutcome):
            return ProviderExecutionResult(status="completed", output=raw_output.output, artifacts=raw_output.artifacts)
        if not isinstance(raw_output, dict):
            raise ValueError(f"invalid_tool_output:{tool_name}")
        return ProviderExecutionResult(status="completed", output=raw_output)

    def prepare_arguments(
        self,
        *,
        tool_name: str,
        raw_arguments: dict[str, Any],
        runtime_context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        entry = self._entries.get(tool_name)
        if entry is None:
            raise ValueError(f"unknown_tool:{tool_name}")
        preparer = entry.spec.argument_preparer
        if preparer is None:
            return dict(raw_arguments), []
        return preparer(dict(raw_arguments), dict(runtime_context or {}))

    async def refresh(self) -> None:
        """Code-defined tools have no external source to reload."""

    def health(self) -> dict[str, Any]:
        origins: dict[str, list[str]] = {}
        for name, entry in self._entries.items():
            origins.setdefault(entry.origin, []).append(name)
        return {
            "tool_count": len(self._entries),
            "tools": list(self._entries),
            "origins": {origin: sorted(names) for origin, names in sorted(origins.items())},
            "revision": self._revision,
            "services": sorted(self._services),
        }

    # -- catalog management ----------------------------------------------------

    def entries(self) -> Mapping[str, LocalToolEntry]:
        """Current snapshot; it never changes after it is returned."""
        return self._entries

    def add(
        self,
        specs: Iterable[ToolSpec],
        *,
        services: Mapping[str, Any] | None = None,
    ) -> list[str]:
        """Register new runtime tools (and the services they need), atomically.

        Any name already in this catalog, a spec declaring a reserved
        capability, or a service name already bound to a different object fails
        the whole batch before anything changes.
        """
        batch = self._checked_batch(specs, trusted=False)
        for spec in batch:
            if spec.name in self._entries:
                raise ToolSpecError(f"duplicate_tool:{spec.name}")
        new_services = self._checked_services(services or {})
        self._services.update(new_services)
        self._commit({**self._entries, **self._entries_for(batch, "registered")})
        return [spec.name for spec in batch]

    def update(self, specs: Iterable[ToolSpec]) -> list[str]:
        """Replace registered tools by name; each must already exist."""
        batch = self._checked_batch(specs, trusted=False)
        for spec in batch:
            self._require_mutable(spec.name)
        self._commit({**self._entries, **self._entries_for(batch, "registered")})
        return [spec.name for spec in batch]

    def remove(self, names: Iterable[str]) -> list[str]:
        """Remove registered tools by name; each must exist."""
        targets = list(dict.fromkeys(names))
        for name in targets:
            self._require_mutable(name)
        self._commit({name: entry for name, entry in self._entries.items() if name not in targets})
        return targets

    def load(self, specs: Iterable[ToolSpec]) -> list[str]:
        """Install a trusted source's specs in place of its previous tools.

        Used at construction and again on every reload (``refresh``); tools of
        other origins, including ``registered`` ones, are kept. The source is
        trusted host configuration, so its specs may declare reserved
        capabilities (``invoke_worker`` declares ``spawn_agent``).
        """
        batch = self._checked_batch(specs, trusted=True)
        kept = {name: entry for name, entry in self._entries.items() if entry.origin != "manifest"}
        for spec in batch:
            if spec.name in kept:
                raise ToolSpecError(f"duplicate_tool:{spec.name}")
        self._commit({**kept, **self._entries_for(batch, "manifest")})
        return [spec.name for spec in batch]

    # -- internals ---------------------------------------------------------------

    def _checked_batch(self, specs: Iterable[ToolSpec], *, trusted: bool) -> list[ToolSpec]:
        batch = list(specs)
        seen: set[str] = set()
        for spec in batch:
            if not isinstance(spec, ToolSpec):
                raise ToolSpecError("invalid_tool_spec")
            if spec.name in seen:
                raise ToolSpecError(f"duplicate_tool:{spec.name}")
            seen.add(spec.name)
            if not trusted and spec.reserved_capabilities:
                raise ToolSpecError(
                    f"reserved_capability:{spec.name}:{','.join(sorted(spec.reserved_capabilities))}"
                )
        return batch

    def _checked_services(self, services: Mapping[str, Any]) -> dict[str, Any]:
        checked: dict[str, Any] = {}
        for name, service in services.items():
            if not isinstance(name, str) or not name.strip() or name != name.strip():
                raise ToolSpecError("invalid_service_name")
            if name in self._services and self._services[name] is not service:
                raise ToolSpecError(f"duplicate_service:{name}")
            checked[name] = service
        return checked

    def _require_mutable(self, name: str) -> None:
        entry = self._entries.get(name)
        if entry is None:
            raise ToolSpecError(f"unknown_tool:{name}")
        if entry.origin not in _MUTABLE_ORIGINS:
            raise ToolSpecError(f"immutable_tool:{name}:{entry.origin}")

    def _entries_for(self, batch: list[ToolSpec], origin: ToolOrigin) -> dict[str, LocalToolEntry]:
        return {
            spec.name: LocalToolEntry(spec=spec, origin=origin, descriptor=spec.to_descriptor(provider=self._provider_name))
            for spec in batch
        }

    def _commit(self, entries: dict[str, LocalToolEntry]) -> None:
        self._entries = MappingProxyType(entries)
        self._revision += 1

    def _resolve_service(self, service_name: str) -> Any:
        name = service_name.strip()
        if not name:
            raise ValueError("empty_tool_service_name")
        if name in self._services:
            return self._services[name]
        raise ValueError(f"unknown_tool_service:{name}")
