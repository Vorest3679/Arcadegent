"""Focused tests for FastMCP MCP config loading and tool discovery."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys

from fastmcp import FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from fastapi.testclient import TestClient
import httpx

from app.agent.tools.mcp import (
    MCPServerConfig,
    MCPToolGateway,
    build_mcp_server_configs,
)


def _build_http_transport(app: object, *, path: str = "/mcp") -> StreamableHttpTransport:
    def _factory(
        *,
        headers: dict[str, str] | None = None,
        auth: httpx.Auth | None = None,
        follow_redirects: bool = True,
        timeout: httpx.Timeout | None = None,
        **_: object,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
            headers=headers,
            auth=auth,
            follow_redirects=follow_redirects,
            timeout=timeout,
        )

    return StreamableHttpTransport(
        url=f"http://testserver{path}",
        httpx_client_factory=_factory,
    )


def test_build_mcp_server_configs_accepts_standard_remote_json() -> None:
    configs = build_mcp_server_configs(
        raw_config={
            "mcpServers": {
                "fetch": {
                    "type": "sse",
                    "url": "https://mcp.api-inference.modelscope.net/",
                    "headers": {"X-Token": "secret"},
                }
            }
        },
        default_timeout_seconds=7,
    )

    assert len(configs) == 1
    config = configs[0]
    assert config.name == "fetch"
    assert config.enabled is True
    assert config.url == "https://mcp.api-inference.modelscope.net/"
    assert config.timeout_seconds == 7
    assert config.source_type == "sse"
    assert config.source["mcpServers"]["fetch"]["transport"] == "sse"


def test_build_mcp_server_configs_accepts_standard_stdio_json() -> None:
    configs = build_mcp_server_configs(
        raw_config={
            "mcpServers": {
                "assistant": {
                    "command": "python",
                    "args": ["./assistant_server.py"],
                    "env": {"LOG_LEVEL": "INFO"},
                    "cwd": "/tmp",
                    "enabled": False,
                    "route_tool_name": "assistant_answer",
                }
            }
        },
        default_timeout_seconds=11,
    )

    assert len(configs) == 1
    config = configs[0]
    assert config.name == "assistant"
    assert config.enabled is False
    assert config.timeout_seconds == 11
    assert config.route_tool_name == "assistant_answer"
    assert config.source_type == "stdio"
    assert config.source["mcpServers"]["assistant"]["command"] == "python"
    assert config.source["mcpServers"]["assistant"]["args"] == ["./assistant_server.py"]
    assert config.source["mcpServers"]["assistant"]["env"] == {"LOG_LEVEL": "INFO"}


def test_build_mcp_server_configs_reads_json_directory(tmp_path: Path) -> None:
    config_dir = tmp_path / "mcp_servers"
    config_dir.mkdir()
    (config_dir / "fetch.json").write_text(
        json.dumps(
            {
                "transport": "sse",
                "url": "https://mcp.api-inference.modelscope.net/",
            }
        ),
        encoding="utf-8",
    )
    (config_dir / "assistant.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "assistant": {
                        "command": "python",
                        "args": ["./assistant_server.py"],
                        "env": {"LOG_LEVEL": "INFO"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    configs = build_mcp_server_configs(
        config_dir=config_dir,
        default_timeout_seconds=8,
    )

    configs_by_name = {item.name: item for item in configs}
    assert set(configs_by_name) == {"fetch", "assistant"}
    assert configs_by_name["fetch"].source["mcpServers"]["fetch"]["transport"] == "sse"
    assert configs_by_name["assistant"].source["mcpServers"]["assistant"]["command"] == "python"


def test_build_mcp_server_configs_expands_env_placeholders(monkeypatch) -> None:
    monkeypatch.setenv("AMAP_API_KEY", "amap-key")

    configs = build_mcp_server_configs(
        raw_config={
            "mcpServers": {
                "amap": {
                    "transport": "streamable-http",
                    "url": "https://mcp.amap.com/mcp?key=${AMAP_API_KEY}",
                }
            }
        },
        default_timeout_seconds=8,
    )

    assert len(configs) == 1
    config = configs[0]
    assert config.name == "amap"
    assert config.url == "https://mcp.amap.com/mcp?key=amap-key"
    assert config.source["mcpServers"]["amap"]["url"] == "https://mcp.amap.com/mcp?key=amap-key"


def test_mcp_gateway_discovers_and_executes_tools_from_json_directory(tmp_path: Path) -> None:
    fixture_server = Path(__file__).resolve().parents[1] / "fixtures" / "mock_amap_mcp_server.py"
    config_dir = tmp_path / "mcp_servers"
    config_dir.mkdir()
    (config_dir / "amap.json").write_text(
        json.dumps(
            {
                "command": sys.executable,
                "args": [str(fixture_server)],
                "route_tool_name": "maps_direction_walking",
            }
        ),
        encoding="utf-8",
    )

    gateway = MCPToolGateway(
        servers=build_mcp_server_configs(
            config_dir=config_dir,
            default_timeout_seconds=3,
        )
    )

    asyncio.run(gateway.refresh())
    result = asyncio.run(
        gateway.execute(
            tool_name="mcp__amap__maps_direction_walking",
            raw_arguments={"origin": "116.3,39.9", "destination": "116.4,39.91"},
        )
    )

    assert result.status == "completed"
    assert result.output["server"] == "amap"
    assert result.output["tool"] == "maps_direction_walking"
    assert result.output["route"]["distance_m"] == 1350
    assert gateway.health()["servers"]["amap"]["discovered"] is True


def test_mcp_gateway_discovers_and_executes_http_tools() -> None:
    mcp = FastMCP("HTTP MCP")

    @mcp.tool(name="maps_direction_walking", description="步行路径规划，输入 origin 和 destination，输出 paths。")
    def maps_direction_walking(origin: str, destination: str) -> dict[str, object]:
        return {
            "origin": origin,
            "destination": destination,
            "paths": [
                {
                    "distance": 2468,
                    "duration": 1200,
                    "steps": [
                        {
                            "instruction": "walk forward",
                            "polyline": "116.3,39.9;116.31,39.901",
                        }
                    ],
                }
            ],
        }

    app = mcp.http_app(path="/mcp", transport="http")
    with TestClient(app):
        gateway = MCPToolGateway(
            servers=[
                MCPServerConfig(
                    name="amap",
                    enabled=True,
                    source=_build_http_transport(app),
                    url="http://testserver/mcp",
                    timeout_seconds=3,
                    route_tool_name="maps_direction_walking",
                    source_type="http",
                )
            ]
        )

        asyncio.run(gateway.refresh())
        definitions = asyncio.run(gateway.build_tool_definitions(allowed_tools=["mcp__*"], strict=True))
        names = [item["function"]["name"] for item in definitions]
        assert "mcp__amap__maps_direction_walking" in names

        result = asyncio.run(
            gateway.execute(
                tool_name="mcp__amap__maps_direction_walking",
                raw_arguments={"origin": "116.3,39.9", "destination": "116.4,39.91"},
            )
        )

        assert result.status == "completed"
        assert result.output["server"] == "amap"
        assert result.output["tool"] == "maps_direction_walking"
        assert result.output["route"]["distance_m"] == 2468
        assert result.output["route"]["duration_s"] == 1200
        assert gateway.health()["servers"]["amap"]["source_type"] == "http"
