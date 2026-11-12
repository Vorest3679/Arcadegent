"""Package boundary of the agent execution kernel (plan 4, section 3.1 and stage 8).

The kernel modules may only import ``app.agent.*`` (plus the standard library
and third-party packages). ``react_runtime.py`` is the application adapter and
the only allowed exception; ``_logging.py`` uses the standard library only
(plan 5, stage 1). The scan covers ``TYPE_CHECKING`` and function-local imports too.
"""

from __future__ import annotations

import ast
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parents[2] / "agent"

KERNEL_MODULES = (
    *sorted((AGENT_DIR / "runtime").glob("*.py")),
    *sorted((AGENT_DIR / "artifacts").glob("*.py")),
    AGENT_DIR / "llm" / "model_gateway.py",
    AGENT_DIR / "tools" / "execution.py",
    AGENT_DIR / "context" / "turn_recorder.py",
    AGENT_DIR / "subagents" / "agent_node.py",
    AGENT_DIR / "_logging.py",
)

ALLOWED = {
    AGENT_DIR / "runtime" / "react_runtime.py",
}


def _app_imports(path: Path) -> list[str]:
    """Absolute ``app`` modules imported anywhere in ``path``."""
    modules: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module == "app":
                modules.extend(f"app.{alias.name}" for alias in node.names)
            else:
                modules.append(node.module)
    return [name for name in modules if name == "app" or name.startswith("app.")]


def _outside_agent(module: str) -> bool:
    return module != "app.agent" and not module.startswith("app.agent.")


def test_kernel_modules_exist() -> None:
    missing = [path for path in KERNEL_MODULES if not path.is_file()]
    assert missing == []
    assert ALLOWED <= set(KERNEL_MODULES)


def test_kernel_modules_import_only_the_agent_package() -> None:
    violations = {
        path.relative_to(AGENT_DIR).as_posix(): bad
        for path in KERNEL_MODULES
        if path not in ALLOWED
        if (bad := [module for module in _app_imports(path) if _outside_agent(module)])
    }
    assert violations == {}


def test_allow_list_is_still_needed() -> None:
    """Each allowed module really crosses the boundary; drop it once it no longer does."""
    for path in ALLOWED:
        assert any(_outside_agent(module) for module in _app_imports(path)), path.name


def test_kernel_modules_use_no_relative_imports() -> None:
    relative = [
        path.relative_to(AGENT_DIR).as_posix()
        for path in KERNEL_MODULES
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.level > 0
    ]
    assert relative == []
