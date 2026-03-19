"""Local FastMCP fixture server used for manual MVP curl verification."""

from __future__ import annotations

from fastmcp import FastMCP

mcp = FastMCP("Mock AMap MCP")


@mcp.tool(
    name="maps_direction_walking",
    description="步行路径规划，输入 origin 和 destination，输出 origin、destination、paths。",
)
def maps_direction_walking(origin: str, destination: str) -> dict[str, object]:
    return {
        "origin": origin,
        "destination": destination,
        "paths": [
            {
                "distance": 1350,
                "duration": 960,
                "steps": [
                    {
                        "instruction": "Head east",
                        "polyline": "116.3,39.9;116.31,39.901",
                    },
                    {
                        "instruction": "Continue north",
                        "polyline": "116.31,39.901;116.32,39.905",
                    },
                ],
            }
        ],
    }


if __name__ == "__main__":
    mcp.run()
