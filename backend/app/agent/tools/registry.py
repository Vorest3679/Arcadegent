"""Unified tool registry with provider-based validation and execution routing."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

from pydantic import ValidationError
from pydantic_core import ErrorDetails

from app.agent.tools.base import ToolDescriptor, ToolInputValidationError, ToolProvider
from app.agent.tools.builtin.provider import BuiltinToolProvider
from app.agent.tools.mcp_gateway import MCPToolGateway
from app.agent.tools.permission import ToolPermissionChecker, ToolPermissionError


@dataclass(frozen=True)
class ToolExecutionResult:
    """Normalized tool execution output."""

    call_id: str
    tool_name: str
    status: str
    output: dict[str, Any]
    error_message: str | None = None


class ToolRegistry:
    """Provider-oriented runtime entrypoint for builtin and MCP tools.
    面向提供程序的运行时入口，用于内置和 MCP 工具。
    This registry aggregates tools from multiple providers, 
    applies permission checks, validates input against provider-declared JSON Schemas, 
    and routes execution to the appropriate provider implementation.
    该注册表聚合来自多个提供程序的工具，应用权限检查，
    根据提供程序声明的 JSON Schema 验证输入，并将执行路由到适当的提供程序实现。 """

    def __init__(
        self,
        *,
        providers: list[ToolProvider] | None = None,
        permission_checker: ToolPermissionChecker,
        strict_schema: bool = True,
        **legacy_dependencies: Any,
    ) -> None:
        self._permission_checker = permission_checker
        self._strict_schema = strict_schema
        self._providers = list(
            providers or self._build_legacy_providers(legacy_dependencies=legacy_dependencies)
        )

    def get_tools(self, *, allowed_tools: list[str] | None = None) -> dict[str, ToolDescriptor]:
        tools, _ = self._collect_tools()
        if allowed_tools is None:
            return tools
        return {
            name: descriptor
            for name, descriptor in tools.items()
            if self._matches_allowed_tools(name, allowed_tools)
        }

    def gettools(self, *, allowed_tools: list[str] | None = None) -> dict[str, ToolDescriptor]:
        """Compatibility alias for provider-aggregated tool discovery.
        用于提供程序聚合工具发现的兼容性别名。"""
        return self.get_tools(allowed_tools=allowed_tools)

    def tool_definitions(self, *, allowed_tools: list[str]) -> list[dict[str, Any]]:
        """Return the tool definitions for the tools matching the allowed patterns, including their JSON Schemas.
        返回与允许的模式匹配的工具的工具定义，包括它们的 JSON Schema。"""
        tools = self.get_tools()
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

    def refresh_tools(self) -> None:
        for provider in self._providers:
            provider.refresh()

    def refresh_mcp_tools(self) -> None:
        for provider in self._providers:
            if provider.provider_name == "mcp":
                provider.refresh()

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

    def execute(
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
            tools, owners = self._collect_tools()
            descriptor = tools.get(tool_name)
            provider = owners.get(tool_name)
            if descriptor is None or provider is None:
                raise ValueError(f"unknown_tool:{tool_name}")
            validated = raw_arguments
            if descriptor.validator is not None:
                validated = descriptor.validator(raw_arguments)


            result = provider.execute(
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

    def _build_legacy_providers(self, *, legacy_dependencies: dict[str, Any]) -> list[ToolProvider]:
        """Build providers based on legacy dependencies, supporting both direct service instances and factory-based specifications.
        根据传统依赖关系构建提供程序，支持直接服务实例和基于工厂的规范。"""
        legacy = dict(legacy_dependencies)
        mcp_tool_gateway = legacy.pop("mcp_tool_gateway", None) or MCPToolGateway()
        builtin_services = {
            key: legacy.pop(key)
            for key in (
                "db_query_tool",
                "geo_resolve_tool",
                "route_plan_tool",
                "summary_tool",
                "select_next_subagent_tool",
            )
            if key in legacy
        }
        providers: list[ToolProvider] = []
        if builtin_services:
            providers.append(
                BuiltinToolProvider(
                    services={
                        **builtin_services,
                        "mcp_tool_gateway": mcp_tool_gateway,
                    }
                )
            )
        providers.append(mcp_tool_gateway)
        return providers

    def _collect_tools(self) -> tuple[dict[str, ToolDescriptor], dict[str, ToolProvider]]:
        """Aggregate tools from all providers, ensuring no name conflicts and building a mapping of tool names to their owning providers.
        从所有提供程序聚合工具，确保没有名称冲突，并构建工具名称到其所属提供程序的映射。"""
        tools: dict[str, ToolDescriptor] = {}
        owners: dict[str, ToolProvider] = {}
        for provider in self._providers:
            for name, descriptor in provider.get_tools().items():
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
                "strict": self._strict_schema,
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
