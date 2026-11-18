"""Manifest adapter: local tools declared in a JSON manifest.

``BuiltinToolProvider`` is a :class:`LocalToolProvider` whose ``manifest``
tools come from ``tools_manifest.json``. The manifest only lists tool
definition files; each definition names its ``executor`` (and optional
``argument_preparer``) by import path. Services are not declared in the
manifest: the host builds them and passes them in ``services``. Metadata is
copied as plain data.

The adapter only translates its source into ``ToolSpec`` objects; the catalog,
the origin rules and the execution path are ``LocalToolProvider``'s. Hosts
that build ``ToolSpec`` objects in code register them through
``ToolRegistry.add_tools`` instead.
"""

from __future__ import annotations

from collections.abc import Mapping
import importlib
from pathlib import Path
from typing import Any

from app.agent.tools.local import LocalToolProvider
from app.agent.tools.schemas import load_json_schema
from app.agent.tools.spec import ArgumentPreparer, ToolSpec

_MANIFEST_KEYS = frozenset({"tools"})


def _import_object(import_path: str) -> Any:
    """Import a Python object given its full import path, e.g. "module.sub:object.attr".
    给定完整的导入路径（例如 "module.sub:object.attr"）导入 Python 对象。
    """
    module_path, separator, attribute_path = import_path.partition(":")
    if not separator or not module_path.strip() or not attribute_path.strip():
        raise ValueError(f"invalid_import_path:{import_path}")
    module = importlib.import_module(module_path.strip())
    current = module
    for attr in attribute_path.strip().split("."):
        current = getattr(current, attr)
    return current


class BuiltinToolProvider(LocalToolProvider):
    """Manifest-declared tools, reloadable through :meth:`refresh`.
    manifest 声明的工具，可通过 refresh 重新加载；运行时注册的工具在 refresh 后保留。"""

    def __init__(
        self,
        *,
        services: Mapping[str, Any] | None = None,
        manifest_path: Path | None = None,
    ) -> None:
        super().__init__(provider_name="builtin", services=services)
        self._manifest_path = manifest_path or Path(__file__).with_name("tools_manifest.json")
        self._tool_files: dict[str, Path] = {}  # tool_name -> definition file
        self.load(self._load_manifest_specs())

    async def refresh(self) -> None:
        """Reload the manifest; tools registered at runtime are kept."""
        self.load(self._load_manifest_specs())

    def health(self) -> dict[str, Any]:
        return {
            **super().health(),
            "manifest_path": self._manifest_path.name,
            "tool_files": {name: path.name for name, path in self._tool_files.items()},
        }

    def _load_manifest_specs(self) -> list[ToolSpec]:
        """Build specs from the manifest's tool definition files.
        根据 manifest 列出的工具定义文件构建 ToolSpec。"""
        specs: list[ToolSpec] = []
        tool_files: dict[str, Path] = {}
        for tool_path in self._manifest_tool_paths():
            item = load_json_schema(tool_path)
            executor_import = str(item.get("executor") or "").strip()
            metadata = item.get("metadata")
            spec = ToolSpec.from_definition(
                item,
                executor=_import_object(executor_import),
                argument_preparer=self._load_argument_preparer(item),
                metadata=metadata if isinstance(metadata, dict) else {},
            )
            specs.append(spec)
            tool_files[spec.name] = tool_path
        self._tool_files = tool_files
        return specs

    def _load_argument_preparer(self, item: dict[str, Any]) -> ArgumentPreparer | None:
        import_path = str(item.get("argument_preparer") or "").strip()
        if not import_path:
            return None
        preparer = _import_object(import_path)
        if not callable(preparer):
            raise ValueError(f"invalid_builtin_tool_argument_preparer:{import_path}")
        return preparer

    def _manifest_tool_paths(self) -> list[Path]:
        """Tool definition paths listed by the manifest, relative to the manifest file.
        解析 manifest，返回工具定义文件路径（相对路径以 manifest 所在目录为基准）。"""
        raw = load_json_schema(self._manifest_path)
        unknown = sorted(set(raw) - _MANIFEST_KEYS)
        if unknown:
            # The former ``services`` section is gone: hosts pass services in code.
            raise ValueError(f"unsupported_builtin_tool_manifest_keys:{','.join(unknown)}")
        tool_entries = raw.get("tools")
        if not isinstance(tool_entries, list):
            raise ValueError("builtin_tool_manifest_tools_must_be_list")

        tool_paths: list[Path] = []
        manifest_dir = self._manifest_path.parent
        for entry in tool_entries:
            if not isinstance(entry, str) or not entry.strip():
                raise ValueError("builtin_tool_manifest_tool_entry_must_be_relative_path")
            path = Path(entry.strip())
            if not path.is_absolute():
                path = manifest_dir / path
            tool_paths.append(path.resolve())
        return tool_paths
