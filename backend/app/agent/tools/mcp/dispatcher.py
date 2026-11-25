"""Execution and generic serialization for MCP-backed tools.

The dispatcher only turns a FastMCP tool result into JSON-safe data: content
blocks, structured content, ``data`` and error text. Domain-specific result
mapping is a separate port (see :mod:`app.agent.tools.mcp.ports`) applied by
the gateway.
"""

from __future__ import annotations

import json
from typing import Any

from app.agent.tools.mcp.client_manager import MCPClientManager
from app.agent.tools.mcp.discovery import coerce_str
from app.agent.tools.mcp.models import MCPExecutionResult, MCPServerConfig, MCPToolDescriptor


def _serialize_json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        return str(value)


def _serialize_content_block(block: Any) -> dict[str, Any]:
    if hasattr(block, "model_dump"):
        try:
            dumped = block.model_dump(mode="json")  # type: ignore[attr-defined]
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass
    block_type = getattr(block, "type", None)
    text = getattr(block, "text", None)
    payload: dict[str, Any] = {"type": block_type or type(block).__name__}
    if isinstance(text, str):
        payload["text"] = text
    else:
        payload["value"] = str(block)
    return payload


def _extract_text_from_content(content: list[dict[str, Any]]) -> str | None:
    chunks: list[str] = []
    for item in content:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "text":
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            chunks.append(text.strip())
    merged = "\n".join(chunks).strip()
    return merged or None


class MCPDispatcher:
    """Execute MCP tools and return a generic, JSON-safe result payload."""

    def __init__(self, *, client_manager: MCPClientManager | None = None) -> None:
        self._client_manager = client_manager or MCPClientManager()

    async def execute(
        self,
        *,
        config: MCPServerConfig,
        descriptor: MCPToolDescriptor,
        raw_arguments: dict[str, Any],
    ) -> MCPExecutionResult:
        if not isinstance(raw_arguments, dict):
            return MCPExecutionResult(
                status="failed",
                output={"error": {"type": "validation_error", "message": "tool arguments must be an object"}},
                error_message="tool arguments must be an object",
            )

        try:
            result = await self._client_manager.call_tool(
                config=config,
                remote_name=descriptor.remote_name,
                arguments=raw_arguments,
            )
        except Exception as exc:
            message = str(exc)
            return MCPExecutionResult(
                status="failed",
                output={"error": {"type": "runtime_error", "message": message}},
                error_message=message,
            )

        content = [_serialize_content_block(item) for item in list(getattr(result, "content", []) or [])]
        structured_content = getattr(result, "structured_content", None)
        data = getattr(result, "data", None)
        is_error = bool(getattr(result, "is_error", False))
        payload: dict[str, Any] = {
            "server": descriptor.server_name,
            "tool": descriptor.remote_name,
            "content": content,
            "structured_content": _serialize_json_safe(structured_content),
            "data": _serialize_json_safe(data),
            "is_error": is_error,
        }

        if is_error:
            message = (
                _extract_text_from_content(content)
                or coerce_str(payload.get("data"))
                or f"mcp tool '{descriptor.remote_name}' returned is_error=true"
            )
            payload["error"] = {"type": "runtime_error", "message": message}
            return MCPExecutionResult(status="failed", output=payload, error_message=message)
        return MCPExecutionResult(status="completed", output=payload)
