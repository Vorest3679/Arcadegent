"""Shared descriptor, validation, and async provider interfaces for runtime tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Protocol

from app.agent.artifacts.model import Artifact

ToolValidator = Callable[[dict[str, Any]], Any]

# Whether a tool call may overlap a sibling call in the same batch. The kernel
# supervisor reads this from the registry; unknown tools stay ``exclusive``.
ExecutionMode = Literal["exclusive", "parallel"]


@dataclass(frozen=True)
class ToolInputValidationError(Exception):
    """Provider-neutral validation error raised by tool-specific validators."""

    message: str
    details: list[dict[str, Any]] = field(default_factory=list)

    def __str__(self) -> str:
        return self.message


class ToolArgumentPreparationError(ValueError):
    """Argument preparation failed with a model-facing, log-safe reason.

    Preparers raise this when the message is derived from validated inputs
    (for example unknown candidate ids) rather than from an upstream payload.
    The dispatcher may log the message verbatim; other preparation exceptions
    are correlated by reference only.
    """


@dataclass(frozen=True)
class ToolDescriptor:
    """Provider-neutral tool metadata used by the runtime registry."""

    name: str
    description: str
    input_schema: dict[str, Any]
    kind: str = "function"
    output_schema: dict[str, Any] | None = None
    validator: ToolValidator | None = None
    capabilities: tuple[str, ...] = field(default_factory=tuple)
    execution_mode: ExecutionMode = "exclusive"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderExecutionResult:
    """Normalized provider execution result consumed by the registry."""

    status: str
    output: dict[str, Any]
    error_message: str | None = None
    artifacts: tuple[Artifact, ...] = ()


@dataclass(frozen=True)
class ToolOutcome:
    """Optional executor return type: model-facing output plus artifacts.

    Executors that only return a dict keep working; returning a ``ToolOutcome``
    lets a tool describe the opaque values it contributes without teaching the
    provider about any particular tool.
    """

    output: dict[str, Any]
    artifacts: tuple[Artifact, ...] = ()


class ToolProvider(Protocol):
    """Common provider surface implemented by builtin and MCP sources.

    This is a structural contract, not a base class: ``LocalToolProvider``,
    ``BuiltinToolProvider`` and the MCP provider satisfy it by shape and do not
    inherit from it. Consequently, "go to definition" on ``provider.execute``
    from the registry lands on the declaration below, not on the concrete
    implementation; use "go to implementation" to reach the provider method.
    这是结构化契约而非基类：各 provider 靠方法签名满足它、不显式继承，
    因此在 registry 中对 ``provider.execute`` 跳转会落到此声明而不是具体实现。
    对于本地工具，可在 ``LocalToolProvider`` 中找到实现；对于 MCP 工具，可在 ``MCPToolProvider`` 中找到实现。

    A provider carries no name: ``ToolRegistry`` is constructed with a
    ``{name: provider}`` mapping and is the only place that names providers.
    provider 本身不带名字，名字只由 ``ToolRegistry`` 的 ``{name: provider}`` 映射给出。
    """

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
