"""Business-decoupling guard rails for the whole ``app.agent`` package (plan 5, stage 0).

Two exact allow lists describe the coupling that is still left:

* ``ALLOWED_APP_IMPORTS``: ``app.*`` modules outside ``app.agent`` each file imports.
* ``ALLOWED_BUSINESS_TOKENS``: arcade/map identifiers each file still mentions
  (code, comments and docstrings alike).

Both checks compare for equality, so a new coupling fails the test and a removed
one requires deleting its entry. The lists may only shrink; the plan is done
when both are empty. ``test_runtime_package_boundary.py`` keeps its stricter
rule for the kernel modules.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[2] / "agent"

BUSINESS_TOKENS = re.compile(
    r"shop|arcade|amap|gcj02|db_query_tool|route_plan_tool|geo_resolve_tool|summary_tool"
    r"|result_selection_tool|search_worker|navigation_worker|map_artifacts|RouteSummaryDto"
    r"|GeoPoint|机厅|路线|导航",
    re.IGNORECASE,
)

ALLOWED_APP_IMPORTS: dict[str, tuple[str, ...]] = {
    "_logging.py": ("app.infra.observability.logger",),
    "context/context_builder.py": ("app.protocol.messages",),
    "llm/llm_config.py": ("app.core.config",),
    "llm/provider_adapter.py": ("app.infra.observability.logger",),
    "runtime/react_runtime.py": ("app.protocol.messages",),
    "tools/builtin/db_query_tool.py": ("app.infra.db.protocols",),
    "tools/builtin/executors/db_query.py": ("app.infra.observability.logger",),
    "tools/builtin/executors/route_plan.py": ("app.protocol.messages", "app.services.coordinate_transform"),
    "tools/builtin/executors/summary.py": ("app.protocol.messages",),
    "tools/builtin/geo_resolve_tool.py": ("app.protocol.messages",),
    "tools/builtin/route_plan_tool.py": ("app.protocol.messages",),
    "tools/builtin/summary_tool.py": ("app.infra.observability.logger", "app.protocol.messages"),
    "tools/mcp/dispatcher.py": ("app.protocol.messages",),
    "tools/mcp/gateway.py": ("app.infra.observability.logger", "app.protocol.messages"),
}

ALLOWED_BUSINESS_TOKENS: dict[str, tuple[str, ...]] = {
    "context/context_builder.py": ("amap", "arcade", "shop"),
    "context/context_payload.py": ("arcade", "shop"),
    "runtime/events.py": ("arcade",),
    "runtime/kernel.py": ("map_artifacts",),
    "runtime/react_runtime.py": ("arcade",),
    "runtime/session_state.py": ("navigation_worker", "search_worker"),
    "skills/config.py": ("arcade",),
    "subagents/subagent_builder.py": (
        "db_query_tool", "geo_resolve_tool", "navigation_worker", "result_selection_tool",
        "route_plan_tool", "search_worker", "summary_tool",
    ),
    "tools/builtin/__init__.py": ("db_query_tool", "geo_resolve_tool", "route_plan_tool", "summary_tool"),
    "tools/builtin/db_query_tool.py": ("arcade", "shop"),
    "tools/builtin/executors/db_query.py": ("arcade", "db_query_tool", "gcj02", "shop"),
    "tools/builtin/executors/geo_resolve.py": ("geo_resolve_tool",),
    "tools/builtin/executors/result_selection.py": ("db_query_tool", "result_selection_tool", "shop"),
    "tools/builtin/executors/route_plan.py": ("amap", "gcj02", "route_plan_tool", "shop", "路线"),
    "tools/builtin/executors/summary.py": ("arcade", "routesummarydto", "shop", "summary_tool"),
    "tools/builtin/geo_outlier_filter.py": ("arcade", "gcj02", "shop"),
    "tools/builtin/geo_resolve_tool.py": ("amap",),
    "tools/builtin/route_plan_tool.py": ("amap", "gcj02", "geopoint", "routesummarydto", "路线"),
    "tools/builtin/summary_tool.py": ("arcade", "routesummarydto", "shop", "summary_tool"),
    "tools/mcp/discovery.py": ("导航", "路线"),
    "tools/mcp/dispatcher.py": ("amap", "gcj02", "geopoint", "routesummarydto", "导航", "路线"),
    "tools/mcp/gateway.py": ("amap", "gcj02", "geopoint", "routesummarydto", "路线"),
    "tools/registry.py": ("db_query_tool", "geo_resolve_tool", "route_plan_tool", "summary_tool"),
}


def _agent_sources() -> list[Path]:
    return sorted(path for path in AGENT_DIR.rglob("*.py") if "__pycache__" not in path.parts)


def _outside_agent_imports(path: Path) -> tuple[str, ...]:
    """Absolute ``app.*`` modules outside ``app.agent``, including local and TYPE_CHECKING imports."""
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == "app":
                modules.update(f"app.{alias.name}" for alias in node.names)
            else:
                modules.add(node.module)
    return tuple(sorted(
        name for name in modules
        if (name == "app" or name.startswith("app."))
        and name != "app.agent" and not name.startswith("app.agent.")
    ))


def _business_tokens(path: Path) -> tuple[str, ...]:
    text = path.read_text(encoding="utf-8")
    return tuple(sorted({match.group(0).lower() for match in BUSINESS_TOKENS.finditer(text)}))


def _scan(reader) -> dict[str, tuple[str, ...]]:
    return {
        path.relative_to(AGENT_DIR).as_posix(): found
        for path in _agent_sources()
        if (found := reader(path))
    }


def test_agent_imports_outside_the_package_match_the_allow_list() -> None:
    assert _scan(_outside_agent_imports) == ALLOWED_APP_IMPORTS


def test_agent_business_identifiers_match_the_allow_list() -> None:
    assert _scan(_business_tokens) == ALLOWED_BUSINESS_TOKENS


def test_allow_lists_only_reference_existing_files() -> None:
    existing = {path.relative_to(AGENT_DIR).as_posix() for path in _agent_sources()}
    assert set(ALLOWED_APP_IMPORTS) <= existing
    assert set(ALLOWED_BUSINESS_TOKENS) <= existing
