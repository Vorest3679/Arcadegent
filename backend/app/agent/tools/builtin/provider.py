"""Manifest adapter: builtin tools declared in a JSON manifest.

``BuiltinToolProvider`` is a :class:`LocalToolProvider` whose tools come from
``tools_manifest.json``: each manifest entry is a JSON tool definition with
``executor`` / ``argument_preparer`` import strings, and the manifest
``services`` section is a small factory container (import strings,
environment variables, paths). It only translates its source into
``ToolSpec`` objects; the catalog, the origin rules and the execution path are
``LocalToolProvider``'s. Hosts that build ``ToolSpec`` objects in code can use
a plain ``LocalToolProvider`` and register tools through
``ToolRegistry.add_tools`` instead.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import importlib
import os
from pathlib import Path
from typing import Any

from app.agent.tools.local import LocalToolProvider
from app.agent.tools.schemas import load_json_schema
from app.agent.tools.spec import ArgumentPreparer, ToolSpec


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


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


@dataclass(frozen=True)
class _BuiltinServiceSpec:
    factory: str
    dependencies: dict[str, Any] = field(default_factory=dict)
    singleton: bool = True # whether to cache the resolved instance for future use


class BuiltinToolProvider(LocalToolProvider):
    """Manifest-declared tools, reloadable through :meth:`refresh`.
    manifest 声明的工具，可通过 refresh 重新加载；运行时注册的工具在 refresh 后保留。"""

    def __init__(
        self,
        *,
        runtime_services: Mapping[str, Any] | None = None,
        services: Mapping[str, Any] | None = None,
        manifest_path: Path | None = None,
    ) -> None:
        # No default project root: a manifest path value with ``base: project_root``
        # needs the host to pass ``project_root`` in ``runtime_services``.
        merged_runtime_services: dict[str, Any] = {}
        if services is not None:
            merged_runtime_services.update(services)
        if runtime_services is not None:
            merged_runtime_services.update(runtime_services)
        super().__init__(
            provider_name="builtin",
            services=merged_runtime_services,
            service_resolver=self._resolve_manifest_service,
        )
        self._manifest_path = manifest_path or Path(__file__).with_name("tools_manifest.json")
        self._tool_files: dict[str, Path] = {} # tool_name -> definition file
        self._service_specs: dict[str, _BuiltinServiceSpec] = {} # service_name -> spec
        self._service_cache: dict[str, Any] = {} # service_name -> instance
        self._resolving_services: set[str] = set() # service_names currently being resolved (for cycle detection)
        self.load(self._load_manifest_specs())

    async def refresh(self) -> None:
        """Reload the manifest; tools registered at runtime are kept."""
        self._service_specs = {}
        self._service_cache = {}
        self._resolving_services = set()
        self.load(self._load_manifest_specs())

    def health(self) -> dict[str, Any]:
        return {
            **super().health(),
            "manifest_path": self._manifest_path.name,
            "tool_files": {name: path.name for name, path in self._tool_files.items()},
            "service_count": len(self._service_specs),
            "service_specs": sorted(self._service_specs),
            "runtime_services": sorted(self._services),
            "resolved_services": sorted(self._service_cache),
        }

    def _load_manifest_specs(self) -> list[ToolSpec]:
        """Build specs from the manifest and its tool definition files, resolving metadata values.
        根据 manifest 和工具定义文件构建 ToolSpec，并解析 metadata 中的引用值。"""
        raw_manifest = load_json_schema(self._manifest_path)
        tool_paths, service_specs = self._parse_manifest(raw_manifest)
        self._service_specs = service_specs

        specs: list[ToolSpec] = []
        tool_files: dict[str, Path] = {}
        for tool_path in tool_paths:
            item = load_json_schema(tool_path)
            executor_import = str(item.get("executor") or "").strip()
            metadata = self._resolve_metadata_value(item.get("metadata"))
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

    def _parse_manifest(
        self,
        raw: dict[str, Any],
    ) -> tuple[list[Path], dict[str, _BuiltinServiceSpec]]:
        """Parse the manifest JSON, extracting tool definition paths and builtin service specifications.
        解析 manifest JSON，提取工具定义路径和内置服务规范。"""
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

        raw_services = raw.get("services", {})
        if not isinstance(raw_services, dict):
            raise ValueError("builtin_tool_manifest_services_must_be_object")

        service_specs: dict[str, _BuiltinServiceSpec] = {}
        for service_name, payload in raw_services.items():
            if not isinstance(service_name, str) or not isinstance(payload, dict):
                continue
            normalized_name = service_name.strip()
            factory = str(payload.get("factory") or "").strip()
            if not normalized_name or not factory:
                continue
            raw_dependencies = payload.get("dependencies", {})
            dependencies: dict[str, Any] = {}
            if isinstance(raw_dependencies, dict):
                for parameter_name, reference in raw_dependencies.items():
                    if not isinstance(parameter_name, str):
                        continue
                    parameter = parameter_name.strip()
                    if parameter:
                        dependencies[parameter] = reference
            service_specs[normalized_name] = _BuiltinServiceSpec(
                factory=factory,
                dependencies=dependencies,
                singleton=bool(payload.get("singleton", True)),
            )
        return tool_paths, service_specs

    def _resolve_manifest_service(self, service_name: str) -> Any:
        """Resolve a service by name, instantiating it if needed based on its specification and dependencies.
        根据规范和依赖关系按名称解析服务，必要时实例化它。"""
        normalized_name = service_name.strip()
        if not normalized_name:
            raise ValueError("empty_builtin_service_name")
        if normalized_name in self._service_cache:
            return self._service_cache[normalized_name]

        spec = self._service_specs.get(normalized_name)
        if spec is None:
            raise ValueError(f"unknown_builtin_service:{normalized_name}")
        if normalized_name in self._resolving_services:
            raise ValueError(f"cyclic_builtin_service_dependency:{normalized_name}")

        self._resolving_services.add(normalized_name)
        try:
            factory = _import_object(spec.factory)
            kwargs = {
                parameter_name: self._resolve_dependency_value(reference)
                for parameter_name, reference in spec.dependencies.items()
            }
            instance = factory(**kwargs) # instantiate the service using the factory and resolved dependencies
            if spec.singleton:
                self._service_cache[normalized_name] = instance
            return instance
        finally:
            self._resolving_services.discard(normalized_name)

    def _resolve_dependency_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._resolve_reference(value)
        if isinstance(value, list):
            return [self._resolve_dependency_value(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "ref" in value:
            return self._resolve_reference(str(value["ref"]))
        if "env" in value:
            return self._resolve_env_value(value)
        if "path" in value:
            return self._resolve_path_value(value)
        if "value" in value:
            return value.get("value")
        return {
            key: self._resolve_dependency_value(item)
            for key, item in value.items()
        }

    def _resolve_metadata_value(self, value: Any) -> Any:
        if isinstance(value, list):
            return [self._resolve_metadata_value(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "ref" in value:
            return self._resolve_reference(str(value["ref"]))
        if "env" in value:
            return self._resolve_env_value(value)
        if "path" in value:
            return self._resolve_path_value(value)
        return {
            key: self._resolve_metadata_value(item)
            for key, item in value.items()
        }

    def _resolve_env_value(self, spec: dict[str, Any]) -> Any:
        env_name = str(spec.get("env") or "").strip()
        if not env_name:
            raise ValueError("empty_env_name")
        default = spec.get("default")
        value: Any = os.getenv(env_name, default)
        cast = str(spec.get("cast") or "").strip().lower()
        if not cast:
            return value
        if cast == "int":
            return int(value)
        if cast == "float":
            return float(value)
        if cast == "bool":
            return _coerce_bool(value)
        if cast == "string":
            return str(value)
        raise ValueError(f"unsupported_env_cast:{cast}")

    def _resolve_path_value(self, spec: dict[str, Any]) -> Any:
        raw_path = str(spec.get("path") or "").strip()
        if not raw_path:
            raise ValueError("empty_path_value")
        path = Path(raw_path)
        if not path.is_absolute():
            base_ref = str(spec.get("base") or "project_root").strip()
            base_path = self._resolve_reference(base_ref)
            path = Path(base_path) / path
        if _coerce_bool(spec.get("as_string", False)):
            return str(path)
        return path

    def _resolve_reference(self, reference: str) -> Any:
        parts = [part.strip() for part in reference.split(".") if part.strip()]
        if not parts:
            raise ValueError(f"invalid_builtin_dependency_reference:{reference}")

        current = self._resolve_service(parts[0])
        for part in parts[1:]:
            if isinstance(current, Mapping) and part in current:
                current = current[part]
                continue
            if hasattr(current, part):
                current = getattr(current, part)
                continue
            raise ValueError(f"unresolved_builtin_dependency_reference:{reference}")
        return current
