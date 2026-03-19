"""Tool discovery helpers for MCP-backed tool providers."""
"""工具发现助手，用于基于MCP的工具提供者。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from app.agent.tools.mcp.models import (
    MCPServerConfig,
    MCPToolDescriptor,
    MCP_TOOL_PREFIX,
    MCP_TOOL_WILDCARD,
)

"""这里定义了一些工具发现的辅助函数，主要用于处理MCP工具的描述符、URL处理、时间格式化等功能。
这些函数包括：
- `utc_now_iso()`: 获取当前UTC时间的ISO格式字符串。
- `short()`: 将文本压缩成单行，并限制长度。
- `coerce_str()`: 将任意值转换为字符串，如果不是字符串或者是空字符串，则返回None。
- `local_tool_name()`: 根据服务器名称和远程工具名称生成本地工具名称。
- `infer_source_type()`: 根据工具的source属性推断工具的类型。
- `with_query_param()`: 在URL中添加查询参数。
- `mask_url()`: 对URL中的敏感信息进行掩码处理。
- `discover_tools()`: 从原始工具列表中提取工具描述符。
- `build_tool_definitions()`: 根据工具描述符构建工具定义列表，供模型使用。
- `pick_route_tool()`: 根据工具描述符和服务器配置选择一个最合适的工具作为路径规划工具。
"""
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


def pick_route_tool(
    *,
    config: MCPServerConfig,
    descriptors: list[MCPToolDescriptor],
) -> str | None:
    preferred = (config.route_tool_name or "").strip()
    if preferred:
        if preferred.startswith(f"{MCP_TOOL_PREFIX}{config.name}__"):
            return preferred if any(item.local_name == preferred for item in descriptors) else None
        local_preferred = local_tool_name(config.name, preferred)
        return local_preferred if any(item.remote_name == preferred for item in descriptors) else None

    ranked: list[tuple[int, str]] = []
    for descriptor in descriptors:
        text = " ".join(
            [
                descriptor.remote_name.lower(),
                descriptor.description.lower(),
                json.dumps(descriptor.input_schema, ensure_ascii=False).lower(),
            ]
        )
        score = 0
        if "route" in text or "direction" in text or "路径" in text or "路线" in text or "导航" in text:
            score += 4
        if "walking" in text or "driving" in text or "walk" in text or "drive" in text:
            score += 2
        if "origin" in text or "destination" in text or "起点" in text or "终点" in text:
            score += 1
        if score > 0:
            ranked.append((score, descriptor.local_name))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (-item[0], item[1]))
    return ranked[0][1]
