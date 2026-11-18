"""Arcade business tools, registered in code through ``ToolRegistry.add_tools``.

``arcade_tool_specs()`` binds each JSON definition in ``schemas/`` to its
executor and argument preparer; ``arcade_tool_services(...)`` builds the
services those executors resolve from ``ToolContext``. The host (the
container) registers both in one call::

    await registry.add_tools(arcade_tool_specs(), services=arcade_tool_services(...))
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.agent.tools.spec import ArgumentPreparer, ToolExecutor, ToolSpec
from app.domain.arcade.tools.db_query_tool import DBQueryTool
from app.domain.arcade.tools.executors import db_query, geo_resolve, result_selection, route_plan, summary
from app.domain.arcade.tools.geo_resolve_tool import GeoResolveTool
from app.domain.arcade.tools.route_plan_tool import AMapConfig, RoutePlanTool
from app.domain.arcade.tools.summary_tool import SummaryTool
from app.infra.db.protocols import ArcadeRepository

SCHEMA_DIR = Path(__file__).resolve().with_name("schemas")

# Registration order is the catalog order (the previous manifest order).
_BINDINGS: tuple[tuple[str, ToolExecutor, ArgumentPreparer | None], ...] = (
    ("db_query_tool", db_query.execute, db_query.prepare_arguments),
    ("geo_resolve_tool", geo_resolve.execute, None),
    ("route_plan_tool", route_plan.execute, route_plan.prepare_arguments),
    ("summary_tool", summary.execute, summary.prepare_arguments),
    ("result_selection_tool", result_selection.execute, result_selection.prepare_arguments),
)


def arcade_tool_specs() -> list[ToolSpec]:
    """The arcade tools as validated specs, definitions read from ``schemas/``."""
    return [
        ToolSpec.from_definition(
            SCHEMA_DIR / f"{name}.json",
            executor=executor,
            argument_preparer=preparer,
        )
        for name, executor, preparer in _BINDINGS
    ]


def arcade_tool_services(
    *,
    store: ArcadeRepository,
    amap_config: AMapConfig | None,
    mcp_tool_gateway: Any | None = None,
) -> dict[str, Any]:
    """Services the arcade executors resolve by name.

    ``mcp_tool_gateway`` is optional: without it ``route_plan_tool`` uses the
    REST route service only.
    """
    services: dict[str, Any] = {
        "db_query_tool": DBQueryTool(store),
        "geo_resolve_tool": GeoResolveTool(),
        "route_plan_tool": RoutePlanTool(amap_config),
        "summary_tool": SummaryTool(),
    }
    if mcp_tool_gateway is not None:
        services["mcp_tool_gateway"] = mcp_tool_gateway
    return services


__all__ = [
    "AMapConfig",
    "DBQueryTool",
    "GeoResolveTool",
    "RoutePlanTool",
    "SummaryTool",
    "arcade_tool_services",
    "arcade_tool_specs",
]
