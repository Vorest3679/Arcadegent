"""Shared descriptor, validation, and async provider interfaces for runtime tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

ToolValidator = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class ToolInputValidationError(Exception):
    """Provider-neutral validation error raised by tool-specific validators."""

    message: str
    details: list[dict[str, Any]] = field(default_factory=list)

    def __str__(self) -> str:
        return self.message


@dataclass(frozen=True)
class ToolDescriptor:
    """Provider-neutral tool metadata used by the runtime registry."""

    name: str
    description: str
    provider: str
    input_schema: dict[str, Any]
    kind: str = "function"
    output_schema: dict[str, Any] | None = None
    validator: ToolValidator | None = None
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderExecutionResult:
    """Normalized provider execution result consumed by the registry."""

    status: str
    output: dict[str, Any]
    error_message: str | None = None


class ToolProvider(Protocol):
    """Common provider surface implemented by builtin and MCP sources."""

    @property
    def provider_name(self) -> str:
        """Stable provider id used for metadata and health aggregation."""

    async def get_tools(self) -> dict[str, ToolDescriptor]:
        """Return current tool descriptors keyed by local tool name."""

    async def execute(
        self,
        *,
        tool_name: str,
        raw_arguments: dict[str, Any],
        validated_arguments: Any | None = None,
    ) -> ProviderExecutionResult:
        """Execute one tool call and normalize the result."""

    async def refresh(self) -> None:
        """Refresh provider state when underlying tools can change."""

    def health(self) -> dict[str, Any]:
        """Expose provider-specific health metadata."""
