"""Manifest-driven builtin tool provider with split manifest and per-tool JSON files.
由 manifest 驱动的内置工具提供程序，具有分离的 manifest 和每个工具的 JSON 文件。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import importlib
import os
from pathlib import Path
from typing import Any, Callable, Mapping

from app.agent.tools.base import ProviderExecutionResult, ToolDescriptor
from app.agent.tools.schemas import build_json_schema_validator, load_json_schema

_DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[4]

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
class BuiltinToolContext:
    """Shared resolver exposed to builtin executors."""

    resolver: Callable[[str], Any]

    def require(self, service_name: str) -> Any:
        return self.resolver(service_name)

    def get(self, service_name: str) -> Any | None:
        try:
            return self.resolver(service_name)
        except ValueError:
            return None


BuiltinToolExecutor = Callable[[BuiltinToolContext, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class _BuiltinServiceSpec:
    factory: str
    dependencies: dict[str, Any] = field(default_factory=dict)
    singleton: bool = True # whether to cache the resolved instance for future use


@dataclass(frozen=True)
class _BuiltinToolBinding:
    descriptor: ToolDescriptor
    executor: BuiltinToolExecutor
    source_path: Path


class BuiltinToolProvider:
    """Load builtin tools from a manifest index plus per-tool JSON definitions.
    从 manifest 索引和每个工具的 JSON 定义中加载内置工具。"""

    def __init__(
        self,
        *,
        runtime_services: Mapping[str, Any] | None = None,
        services: Mapping[str, Any] | None = None,
        manifest_path: Path | None = None,
    ) -> None:
        merged_runtime_services: dict[str, Any] = {"project_root": _DEFAULT_PROJECT_ROOT}
        if services is not None:
            merged_runtime_services.update(services)
        if runtime_services is not None:
            merged_runtime_services.update(runtime_services)
        self._runtime_services = merged_runtime_services
        self._context = BuiltinToolContext(self._resolve_service)
        self._manifest_path = manifest_path or Path(__file__).with_name("tools_manifest.json")
        self._bindings: dict[str, _BuiltinToolBinding] = {} # tool_name -> binding
        self._service_specs: dict[str, _BuiltinServiceSpec] = {} # service_name -> spec
        self._service_cache: dict[str, Any] = {} # service_name -> instance
        self._resolving_services: set[str] = set() # service_names currently being resolved (for cycle detection)
        self.refresh() # load tools and services on initialization

    @property
    def provider_name(self) -> str:
        return "builtin"

    def refresh(self) -> None:
        self._service_specs = {} # service_name -> spec
        self._service_cache = {} # service_name -> instance
        self._resolving_services = set() # service_names currently being resolved (for cycle detection)
        self._bindings = self._load_bindings()

    def get_tools(self) -> dict[str, ToolDescriptor]:
        return {
            name: binding.descriptor
            for name, binding in self._bindings.items()
        }

    def execute(
        self,
        *,
        tool_name: str,
        raw_arguments: dict[str, Any],
        validated_arguments: Any | None = None,
    ) -> ProviderExecutionResult:
        """Execute a builtin tool by name with the given arguments, validating against the tool's declared JSON Schema.
        根据工具声明的 JSON Schema 验证，使用给定的参数按名称执行内置工具。
        """
        _ = raw_arguments
        binding = self._bindings.get(tool_name)
        if binding is None:
            raise ValueError(f"unknown_tool:{tool_name}")
        payload = validated_arguments if isinstance(validated_arguments, dict) else raw_arguments
        output = binding.executor(self._context, payload)
        return ProviderExecutionResult(status="completed", output=output)

    def health(self) -> dict[str, Any]:
        return {
            "manifest_path": str(self._manifest_path),
            "tool_count": len(self._bindings),
            "tools": list(self._bindings),
            "tool_files": {
                name: str(binding.source_path)
                for name, binding in self._bindings.items()
            },
            "service_count": len(self._service_specs),
            "service_specs": sorted(self._service_specs),
            "runtime_services": sorted(self._runtime_services),
            "resolved_services": sorted(self._service_cache),
        }

    def _load_bindings(self) -> dict[str, _BuiltinToolBinding]:
        """Load tool bindings from manifest and tool definition files, resolving service dependencies as needed.
        根据需要从 manifest 和工具定义文件加载工具绑定，解析服务依赖关系。"""
        raw_manifest = load_json_schema(self._manifest_path)
        tool_paths, service_specs = self._parse_manifest(raw_manifest)
        self._service_specs = service_specs

        bindings: dict[str, _BuiltinToolBinding] = {}
        for tool_path in tool_paths:
            item = load_json_schema(tool_path)
            name = str(item.get("name") or "").strip()
            description = str(item.get("description") or "").strip()
            executor_import = str(item.get("executor") or "").strip()
            kind = str(item.get("kind") or "function").strip() or "function"
            input_schema = item.get("input_schema")
            capabilities = tuple(
                value.strip()
                for value in item.get("capabilities", [])
                if isinstance(value, str) and value.strip()
            )
            if not name or not description or not executor_import or not isinstance(input_schema, dict):
                raise ValueError(f"invalid_builtin_tool_definition:{tool_path}")
            if name in bindings:
                raise ValueError(f"duplicate_builtin_tool:{name}")

            executor = _import_object(executor_import)
            metadata = self._resolve_metadata_value(item.get("metadata"))
            bindings[name] = _BuiltinToolBinding(
                descriptor=ToolDescriptor(
                    name=name,
                    description=description,
                    provider=self.provider_name,
                    kind=kind,
                    input_schema=input_schema,
                    validator=build_json_schema_validator(input_schema, source=str(tool_path)),
                    capabilities=capabilities,
                    metadata=metadata if isinstance(metadata, dict) else {},
                ),
                executor=executor,
                source_path=tool_path,
            )
        return bindings

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

    def _resolve_service(self, service_name: str) -> Any:
        """Resolve a service by name, instantiating it if needed based on its specification and dependencies.
        根据规范和依赖关系按名称解析服务，必要时实例化它。"""
        normalized_name = service_name.strip()
        if not normalized_name:
            raise ValueError("empty_builtin_service_name")
        if normalized_name in self._runtime_services:
            return self._runtime_services[normalized_name]
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
