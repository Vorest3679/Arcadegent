"""Shared fixtures for tool registry unit tests."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastmcp import FastMCP

from app.agent.tools.builtin import BuiltinToolProvider
from app.agent.tools.mcp_gateway import MCPServerConfig, MCPToolGateway
from app.agent.tools.permission import ToolPermissionChecker
from app.agent.tools.registry import ToolRegistry
from app.infra.db.local import LocalArcadeStore


def _run(awaitable):
    return asyncio.run(awaitable)

def _write_rows(path: Path) -> None:
    rows = [
        {
            "source": "bemanicn",
            "source_id": 1,
            "source_url": "https://map.bemanicn.com/s/1",
            "name": "Alpha Arcade",
            "province_code": "110000000000",
            "province_name": "Beijing",
            "city_code": "110100000000",
            "city_name": "Beijing",
            "county_code": "110101000000",
            "county_name": "Dongcheng",
            "arcades": [{"title_name": "maimai", "quantity": 2}],
        }
    ]
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False))
            handle.write("\n")

def _build_registry(
    tmp_path: Path,
    *,
    mcp_tool_gateway: MCPToolGateway | None = None,
) -> ToolRegistry:
    data_path = tmp_path / "shops.jsonl"
    _write_rows(data_path)
    store = LocalArcadeStore.from_jsonl(data_path)
    gateway = mcp_tool_gateway or MCPToolGateway()
    return ToolRegistry(
        providers=[
            BuiltinToolProvider(
                runtime_services={
                    "store": store,
                    "mcp_tool_gateway": gateway,
                }
            ),
            gateway,
        ],
        permission_checker=ToolPermissionChecker(policy_file=tmp_path / "missing.yaml"),
        strict_schema=True,
    )

def _build_mcp_gateway() -> MCPToolGateway:
    mcp = FastMCP("Test AMap MCP")

    @mcp.tool(name="maps_direction_walking", description="步行路径规划，输入 origin 和 destination，输出 paths。")
    def maps_direction_walking(origin: str, destination: str) -> dict[str, object]:
        return {
            "origin": origin,
            "destination": destination,
            "paths": [
                {
                    "distance": 1234,
                    "duration": 678,
                    "steps": [
                        {
                            "instruction": "walk forward",
                            "polyline": "116.3,39.9;116.4,39.91",
                        }
                    ],
                }
            ],
        }

    gateway = MCPToolGateway(
        servers=[
            MCPServerConfig(
                name="amap",
                enabled=True,
                source=mcp,
                url="memory://amap",
                timeout_seconds=3,
                route_tool_name="maps_direction_walking",
            )
        ]
    )
    _run(gateway.refresh())
    return gateway
