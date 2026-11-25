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
    "context/context_builder.py": ("app.protocol.messages",),
    "runtime/react_runtime.py": ("app.protocol.messages",),
}

ALLOWED_BUSINESS_TOKENS: dict[str, tuple[str, ...]] = {
    "context/context_builder.py": ("amap", "arcade", "shop"),
    "context/context_payload.py": ("arcade", "shop"),
    "runtime/kernel.py": ("map_artifacts",),
    "runtime/react_runtime.py": ("arcade",),
    "runtime/session_state.py": ("navigation_worker", "search_worker"),
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


def test_plugin_modules_only_import_the_agent_package() -> None:
    """Stage 2c: the plugin protocol and host are business-neutral."""
    plugin_dir = AGENT_DIR / "plugins"
    sources = [path for path in plugin_dir.rglob("*.py") if "__pycache__" not in path.parts]

    assert sources
    assert {path.name: _outside_agent_imports(path) for path in sources if _outside_agent_imports(path)} == {}
    assert {path.name: _business_tokens(path) for path in sources if _business_tokens(path)} == {}
