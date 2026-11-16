"""Unified async tool registry with provider-based validation and execution routing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

from pydantic import ValidationError
from pydantic_core import ErrorDetails

from app.agent.artifacts.model import Artifact
from app.agent.tools.base import ExecutionMode, ToolDescriptor, ToolInputValidationError, ToolProvider
from app.agent.tools.local import LocalToolProvider
from app.agent.tools.permission import ToolPermissionChecker, ToolPermissionError
from app.agent.tools.spec import ToolSpec, ToolSpecError


def _is_strict_compatible(schema: Any) -> bool:
    """Check whether a JSON Schema satisfies provider strict-mode constraints.

    Strict mode requires every object node to declare
    ``additionalProperties: false`` and to list all of its properties in
    ``required``. Business schemas that do not meet these rules must not be
    projected with ``strict=true``; they keep full local validation instead.
    """
    if not isinstance(schema, dict):
        return True
    properties = schema.get("properties")
    if isinstance(properties, dict):
        required = schema.get("required")
        if not isinstance(required, list) or set(required) != set(properties):
            return False
        if schema.get("additionalProperties") is not False:
            return False
        if any(not _is_strict_compatible(sub) for sub in properties.values()):
            return False
    items = schema.get("items")
    if isinstance(items, dict) and not _is_strict_compatible(items):
        return False
    for key in ("anyOf", "oneOf", "allOf"):
        variants = schema.get(key)
        if isinstance(variants, list) and any(not _is_strict_compatible(item) for item in variants):
            return False
    return True


@dataclass(frozen=True)
class ToolExecutionResult:
    """Normalized tool execution output."""

    call_id: str
    tool_name: str
    status: str
    output: dict[str, Any]
    error_message: str | None = None
    capabilities: tuple[str, ...] = ()
    artifacts: tuple[Artifact, ...] = ()


class ToolRegistry:
    """Provider-oriented runtime entrypoint for local and MCP tools.
    面向提供程序的运行时入口，用于本地工具和 MCP 工具。

    The registry aggregates tools from its providers, applies permission
    checks, validates input against each tool's JSON Schema and routes
    execution to the owning provider. It also manages one local tool catalog
    (``local_provider``, required): ``list_tools`` / ``add_tools`` /
    ``update_tools`` / ``delete_tools`` / ``refresh_tools``. The catalog is
    passed explicitly, so which provider receives writes is never inferred from
    the ``providers`` order, and it is registered as a provider unless it is
    already in ``providers``. Registering a tool never grants access to it:
    visibility still comes from each agent's ``allowed_tools`` and the policy.
    该注册表聚合各 provider 的工具，做权限检查、按 JSON Schema 校验输入并路由执行；
    同时管理显式传入的本地工具目录的增删改查。注册不等于授权。"""

    def __init__(
        self,
        *,
        providers: Sequence[ToolProvider] = (),
        permission_checker: ToolPermissionChecker,
        strict_schema: bool = True,
        local_provider: LocalToolProvider,
    ) -> None:
        self._permission_checker = permission_checker
        self._strict_schema = strict_schema
        self._local = local_provider
        self._providers: list[ToolProvider] = [
            provider for provider in providers if provider is not local_provider
        ]
        self._providers.append(local_provider)

    @property
    def local_provider(self) -> LocalToolProvider:
        """The catalog that ``add_tools`` / ``update_tools`` / ``delete_tools`` write to."""
        return self._local

    def providers(self) -> tuple[ToolProvider, ...]:
        """The providers the registry aggregates, local catalog last."""
        return tuple(self._providers)

    # -- catalog management ----------------------------------------------------

    async def list_tools(
        self,
        *,
        allowed_tools: list[str] | None = None,
        provider: str | None = None,
    ) -> list[ToolDescriptor]:
        """Current tools sorted by name, optionally filtered by patterns and provider."""
        tools = await self.get_tools(allowed_tools=allowed_tools)
        return [
            tools[name]
            for name in sorted(tools)
            if provider is None or tools[name].provider == provider
        ]

    async def add_tools(
        self,
        specs: Iterable[ToolSpec],
        *,
        services: Mapping[str, Any] | None = None,
    ) -> list[str]:
        """Register new local tools (and services for their executors) atomically.

        Fails with ``ToolSpecError`` and changes nothing when a name already
        exists in any provider, repeats within the batch, or a spec uses a
        reserved capability.
        """
        batch = list(specs)
        tools, _ = await self._collect_tools()
        for spec in batch:
            name = getattr(spec, "name", None)
            if isinstance(name, str) and name in tools:
                raise ToolSpecError(f"duplicate_tool:{name}")
        return self._local.add(batch, services=services)

    async def update_tools(self, specs: Iterable[ToolSpec]) -> list[str]:
        """Replace registered local tools by name; every name must already be registered."""
        return self._local.update(specs)

    async def delete_tools(self, names: Iterable[str]) -> list[str]:
        """Remove registered local tools by name; every name must already be registered."""
        return self._local.remove(names)

    async def refresh_tools(self, *, provider: str | None = None) -> None:
        """Reload tool sources (MCP discovery, manifests); all providers by default."""
        for item in self._providers:
            if provider is None or item.provider_name == provider:
                await item.refresh()

    async def get_tools(self, *, allowed_tools: list[str] | None = None) -> dict[str, ToolDescriptor]:
        tools, _ = await self._collect_tools()
        if allowed_tools is None:
            return tools
        return {
            name: descriptor
            for name, descriptor in tools.items()
            if self._matches_allowed_tools(name, allowed_tools)
        }

    async def gettools(self, *, allowed_tools: list[str] | None = None) -> dict[str, ToolDescriptor]:
        """Compatibility alias for provider-aggregated tool discovery.
        用于提供程序聚合工具发现的兼容性别名。"""
        return await self.get_tools(allowed_tools=allowed_tools)

    async def tool_definitions(self, *, allowed_tools: list[str]) -> list[dict[str, Any]]:
        """Return the tool definitions for the tools matching the allowed patterns, including their JSON Schemas.
        返回与允许的模式匹配的工具的工具定义，包括它们的 JSON Schema。"""
        tools = await self.get_tools()
        definitions: list[dict[str, Any]] = []
        seen: set[str] = set()
        for pattern in allowed_tools:
            matched_names = [name for name in tools if fnmatchcase(name, pattern)]
            if any(token in pattern for token in "*?["):
                matched_names.sort()
            for name in matched_names:
                if name in seen:
                    continue
                descriptor = tools[name]
                definitions.append(self._definition_from_descriptor(descriptor))
                seen.add(name)
        return definitions

    async def refresh_mcp_tools(self) -> None:
        await self.refresh_tools(provider="mcp")

    async def execution_mode(self, tool_name: str) -> ExecutionMode:
        """Concurrency mode declared by the tool descriptor.

        Unknown tools stay ``exclusive``: a missing descriptor must never turn
        into an unsafe parallel execution. The supervisor reads the batch's
        modes once through :meth:`execution_modes`.
        """
        return (await self.execution_modes([tool_name]))[tool_name]

    async def execution_modes(self, tool_names: list[str] | tuple[str, ...]) -> dict[str, ExecutionMode]:
        """Declared concurrency modes for one batch, in one descriptor pass.

        Unknown tools are reported as ``exclusive``. This is the batch API the
        supervisor uses so a model step does not re-collect descriptors per
        call.
        """
        tools, _ = await self._collect_tools()
        modes: dict[str, ExecutionMode] = {}
        for name in tool_names:
            if name in modes:
                continue
            descriptor = tools.get(name)
            modes[name] = descriptor.execution_mode if descriptor is not None else "exclusive"
            # 未知的按照 exclusive 处理，避免并行执行；已知的按照 descriptor 中的 execution_mode 处理
        return modes

    def provider_health(self) -> dict[str, Any]:
        return {
            provider.provider_name: provider.health()
            for provider in self._providers
        }

    def mcp_health(self) -> dict[str, Any]:
        for provider in self._providers:
            if provider.provider_name == "mcp":
                return provider.health()
        return {
            "enabled": False,
            "discovered_tool_count": 0,
            "servers": {},
        }

    async def execute(
        self,
        *,
        call_id: str,
        tool_name: str,
        raw_arguments: dict[str, Any],
        allowed_tools: list[str],
    ) -> ToolExecutionResult:
        try:
            # Perform permission check before any other processing to fail fast on unauthorized access.
            # 在任何其他处理之前执行权限检查，以便在未经授权访问时快速失败。
            self._permission_checker.ensure_allowed(tool_name=tool_name, allowed_tools=allowed_tools)
            tools, owners = await self._collect_tools()
            descriptor = tools.get(tool_name)
            provider = owners.get(tool_name)
            if descriptor is None or provider is None:
                raise ValueError(f"unknown_tool:{tool_name}")
            validated = raw_arguments
            if descriptor.validator is not None:
                validated = descriptor.validator(raw_arguments)

            result = await provider.execute(
                tool_name=tool_name,
                raw_arguments=raw_arguments,
                validated_arguments=validated,
            )
            return ToolExecutionResult(
                call_id=call_id,
                tool_name=tool_name,
                status=result.status,
                output=result.output,
                error_message=result.error_message,
                capabilities=descriptor.capabilities,
                artifacts=result.artifacts,
            )
        except ToolPermissionError as exc:
            return self._failed(
                call_id=call_id,
                tool_name=tool_name,
                error_type="permission_error",
                message=str(exc),
            )
        except ValidationError as exc:
            return self._failed(
                call_id=call_id,
                tool_name=tool_name,
                error_type="validation_error",
                message=str(exc),
                details=exc.errors(),
            )
        except ToolInputValidationError as exc:
            return self._failed(
                call_id=call_id,
                tool_name=tool_name,
                error_type="validation_error",
                message=str(exc),
                details=exc.details,
            )
        except Exception as exc:  # pragma: no cover
            return self._failed(
                call_id=call_id,
                tool_name=tool_name,
                error_type="runtime_error",
                message=str(exc),
            )

    async def _collect_tools(self) -> tuple[dict[str, ToolDescriptor], dict[str, ToolProvider]]:
        """Aggregate tools from all providers, ensuring no name conflicts and building a mapping of tool names to their owning providers.
        从所有提供程序聚合工具，确保没有名称冲突，并构建工具名称到其所属提供程序的映射。"""
        tools: dict[str, ToolDescriptor] = {}
        owners: dict[str, ToolProvider] = {}
        for provider in self._providers:
            for name, descriptor in (await provider.get_tools()).items():
                if name in tools:
                    raise ValueError(f"duplicate_tool:{name}")
                tools[name] = descriptor
                owners[name] = provider
        return tools, owners

    def _definition_from_descriptor(self, descriptor: ToolDescriptor) -> dict[str, Any]:
        return {
            "type": descriptor.kind,
            "function": {
                "name": descriptor.name,
                "description": descriptor.description,
                "parameters": descriptor.input_schema,
                # Remote strict mode is only claimed when the business schema already
                # satisfies the provider's strict-subset rules; local validation is
                # unaffected and still runs against the full business schema.
                "strict": self._strict_schema and _is_strict_compatible(descriptor.input_schema),
            },
        }

    def _matches_allowed_tools(self, tool_name: str, allowed_tools: list[str]) -> bool:
        return any(fnmatchcase(tool_name, pattern) for pattern in allowed_tools)

    def _failed(
        self,
        *,
        call_id: str,
        tool_name: str,
        error_type: str,
        message: str,
        details: list[ErrorDetails] | list[dict[str, Any]] | None = None,
    ) -> ToolExecutionResult:
        payload: dict[str, Any] = {
            "error": {
                "type": error_type,
                "message": message,
            }
        }
        if details is not None:
            payload["error"]["details"] = details
        return ToolExecutionResult(
            call_id=call_id,
            tool_name=tool_name,
            status="failed",
            output=payload,
            error_message=message,
        )

    async def prepare_arguments(
        self,
        *,
        tool_name: str,
        raw_arguments: dict[str, Any],
        runtime_context: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], list[str]]:
        tools, owners = await self._collect_tools()
        if tool_name not in tools:
            raise ValueError(f"unknown_tool:{tool_name}")
        provider = owners[tool_name]
        preparer = getattr(provider, "prepare_arguments", None)
        if not callable(preparer):
            return dict(raw_arguments), []
        return preparer(
            tool_name=tool_name,
            raw_arguments=raw_arguments,
            runtime_context=runtime_context,
        )
