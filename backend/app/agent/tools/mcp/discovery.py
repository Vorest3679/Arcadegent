"""Tool discovery helpers for MCP-backed tool providers."""

from __future__ import annotations

# 这里定义了一些工具发现的辅助函数，主要用于处理 MCP 工具的描述符、
# URL 处理、时间格式化等功能。
# 这些函数包括：
# - `utc_now_iso()`: 获取当前 UTC 时间的 ISO 格式字符串。
# - `short()`: 将文本压缩成单行，并限制长度。
# - `coerce_str()`: 将任意值转换为字符串，如果不是字符串或者是空字符串，则返回 None。
# - `local_tool_name()`: 根据服务器名称和远程工具名称生成本地工具名称。
# - `infer_source_type()`: 根据工具的 source 属性推断工具的类型，处理不同格式的 source 输入，包括mcp服务器配置。
# - `with_query_param()`: 在 URL 中添加查询参数。
# - `mask_url()`: 对 URL 中的敏感信息进行掩码处理。
# - `discover_tools()`: 从原始工具列表中提取工具描述符。
# - `build_tool_definitions()`: 根据工具描述符构建工具定义列表，供模型使用。

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.agent.tools.mcp.models import (
    MCPToolDescriptor,
    MCP_TOOL_PREFIX,
    MCP_TOOL_WILDCARD,
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def short(text: str | None, *, limit: int = 160) -> str:
    if not isinstance(text, str):
        return ""
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return f"{compact[: max(1, limit - 3)].rstrip()}..."


def coerce_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def local_tool_name(server_name: str, remote_name: str) -> str:
    return f"{MCP_TOOL_PREFIX}{server_name}__{remote_name}"


def infer_source_type(source: Any) -> str:
    if isinstance(source, str):
        if source.startswith("http://") or source.startswith("https://"):
            return "http"
        if Path(source).suffix == ".py":
            return "script"
        return "string"
    if isinstance(source, dict):
        if "command" in source:
            return "stdio"
        if "url" in source:
            transport = coerce_str(source.get("transport") or source.get("type"))
            return transport or "http"
        mcp_servers = source.get("mcpServers")
        if isinstance(mcp_servers, dict) and len(mcp_servers) == 1:
            only_server = next(iter(mcp_servers.values()))
            if isinstance(only_server, dict):
                if "command" in only_server:
                    return "stdio"
                if "url" in only_server:
                    transport = coerce_str(only_server.get("transport") or only_server.get("type"))
                    return transport or "http"
        return "config"
    return type(source).__name__.lower()


def with_query_param(url: str, *, key: str, value: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault(key, value)
    return urlunparse(parsed._replace(query=urlencode(query)))


def mask_url(url: str | None) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return None
    parsed = urlparse(url)
    query: list[tuple[str, str]] = []
    for name, value in parse_qsl(parsed.query, keep_blank_values=True):
        if name.lower() == "key" and value:
            query.append((name, "***"))
        else:
            query.append((name, value))
    return urlunparse(parsed._replace(query=urlencode(query)))


def discover_tools(server_name: str, raw_tools: list[Any]) -> list[MCPToolDescriptor]:
    descriptors: list[MCPToolDescriptor] = []
    for tool in raw_tools:
        remote_name = str(getattr(tool, "name", "") or "").strip()
        if not remote_name:
            continue
        description = coerce_str(getattr(tool, "description", None)) or remote_name
        input_schema = getattr(tool, "inputSchema", None)
        if not isinstance(input_schema, dict):
            input_schema = {"type": "object", "properties": {}}
        output_schema = getattr(tool, "outputSchema", None)
        descriptors.append(
            MCPToolDescriptor(
                server_name=server_name,
                remote_name=remote_name,
                local_name=local_tool_name(server_name, remote_name),
                description=description,
                input_schema=input_schema,
                output_schema=output_schema if isinstance(output_schema, dict) else None,
            )
        )
    descriptors.sort(key=lambda item: item.local_name)
    return descriptors


def build_tool_definitions(
    descriptors: dict[str, MCPToolDescriptor],
    *,
    allowed_tools: list[str],
    strict: bool,
) -> list[dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    allow_all_mcp = MCP_TOOL_WILDCARD in allowed_tools
    for local_name in sorted(descriptors):
        descriptor = descriptors[local_name]
        if not (allow_all_mcp or local_name in allowed_tools):
            continue
        definitions.append(
            {
                "type": "function",
                "function": {
                    "name": descriptor.local_name,
                    "description": descriptor.description,
                    "parameters": descriptor.input_schema,
                    "strict": strict,
                },
            }
        )
    return definitions
