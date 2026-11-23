"""Plugin configuration: model helpers and the ``plugins.yaml`` reader.

Plugin configuration is declared by plugin code (a ``PluginConfig`` model) and
valued by the host's plugins file. The file lists which of the host's plugins
are enabled, in install order, and supplies their values; it never names code.

Environment placeholders are expanded only from the ``environ`` mapping the
caller passes, so this package never reads the process environment itself.
Errors are payload-free: they name the plugin, the field path and the error
type, never a value or a path.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any, get_args

import yaml
from pydantic import AfterValidator, BaseModel, BeforeValidator, SecretStr, ValidationError, ValidationInfo

from app.agent.plugins.spec import PluginConfig, PluginError

# ``${VAR}`` (required) or ``${VAR:-default}``; nothing else is an expression.
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
# A secret must be the whole value and may not carry a literal default.
_SECRET_PLACEHOLDER = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*(?::-)?\}")
_ENTRY_KEYS = frozenset({"disabled", "config"})


class PluginConfigError(PluginError):
    """The plugins file is invalid; the message contains no values."""


# -- model helpers ---------------------------------------------------------------


def _reject_empty_path(value: Any) -> Any:
    if isinstance(value, str) and not value.strip():
        raise ValueError("empty_path")
    return value


_WINDOWS_ABS_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def _resolve_path(value: Path, info: ValidationInfo) -> Path:
    if value.is_absolute() or _WINDOWS_ABS_PATH.match(str(value)):
        return value
    base_dir = (info.context or {}).get("base_dir")
    if base_dir is None:
        raise ValueError("relative_path_requires_base_dir")
    return (Path(base_dir) / value).resolve()


# A path field; relative values are anchored to the plugins file's directory.
RelPath = Annotated[Path, BeforeValidator(_reject_empty_path), AfterValidator(_resolve_path)]


def secret_values(config: BaseModel) -> dict[str, SecretStr | None]:
    """Every ``SecretStr`` in a config instance by dotted path (list items by index).

    Walks the validated instance rather than the annotations, so secrets inside
    lists and mappings are found too. A ``SecretStr | None`` field left as
    ``None`` is reported with value ``None``.
    """
    found: dict[str, SecretStr | None] = {}

    def walk(value: Any, path: str, annotation: Any) -> None:
        if isinstance(value, SecretStr):
            found[path] = value
        elif value is None:
            if _mentions_secret(annotation):
                found[path] = None
        elif isinstance(value, BaseModel):
            for name, field in type(value).model_fields.items():
                walk(getattr(value, name), f"{path}.{name}" if path else name, field.annotation)
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                walk(item, f"{path}.{index}", None)
        elif isinstance(value, Mapping):
            for key, item in value.items():
                walk(item, f"{path}.{key}", None)

    walk(config, "", None)
    return found


def _mentions_secret(annotation: Any) -> bool:
    if annotation is SecretStr:
        return True
    return any(_mentions_secret(item) for item in get_args(annotation))


def config_placeholders(config: BaseModel, *, prefix: str = "config") -> dict[str, str]:
    """Flatten a validated config into ``config.a.b`` placeholder values.

    Secrets are revealed for interpolation into plugin-owned resource files;
    callers must not log the result. ``None`` fields are omitted, so a resource
    that references them fails as unresolved instead of silently becoming
    ``"None"``.
    """
    values: dict[str, str] = {}

    def walk(value: Any, path: str) -> None:
        if isinstance(value, SecretStr):
            values[path] = value.get_secret_value()
        elif isinstance(value, BaseModel):
            for name in type(value).model_fields:
                walk(getattr(value, name), f"{path}.{name}")
        elif isinstance(value, Mapping):
            for key, item in value.items():
                walk(item, f"{path}.{key}")
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                walk(item, f"{path}.{index}")
        elif value is None:
            return
        elif isinstance(value, bool):
            values[path] = "true" if value else "false"
        elif isinstance(value, (str, int, float, Path)):
            values[path] = str(value)

    walk(config, prefix)
    return values


# -- plugins file --------------------------------------------------------------


class _UniqueKeyLoader(yaml.SafeLoader):
    """Reject ambiguous YAML mappings instead of silently overriding fields."""


def _unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str):
            raise PluginConfigError("plugin_config:non_string_key")
        if key in result:
            raise PluginConfigError("plugin_config:duplicate_key")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def read_plugins_file(path: Path) -> Any:
    """Parse the plugins file; structure is checked by :func:`resolve_plugin_configs`."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise PluginConfigError("plugin_config:missing_file") from None
    except OSError:
        raise PluginConfigError("plugin_config:unreadable_file") from None
    try:
        return yaml.load(text, Loader=_UniqueKeyLoader)
    except (yaml.YAMLError, RecursionError):
        raise PluginConfigError("plugin_config:invalid_yaml") from None


def resolve_plugin_configs(
    raw: Any,
    *,
    base_dir: Path,
    models: Mapping[str, type[PluginConfig]],
    environ: Mapping[str, str],
) -> dict[str, PluginConfig]:
    """Validate a plugins document; return the enabled plugins' configs in install order.

    Format: ``{"plugins": {<name>: {"disabled": bool, "config": {...}} | null}}``.
    Every ``<name>`` must be a key of ``models``.
    """
    if not isinstance(raw, Mapping) or set(raw) != {"plugins"} or not isinstance(raw["plugins"], Mapping):
        raise PluginConfigError("plugin_config:invalid_root")
    configs: dict[str, PluginConfig] = {}
    for name, item in raw["plugins"].items():
        model = models.get(name)
        if model is None:
            raise PluginConfigError(f"plugin:{name}:unknown_plugin")
        item = {} if item is None else item
        if not isinstance(item, Mapping):
            raise PluginConfigError(f"plugin:{name}:invalid_entry")
        unknown = sorted(set(item) - _ENTRY_KEYS)
        if unknown:
            raise PluginConfigError(f"plugin:{name}:unknown_key:{unknown[0]}")
        disabled = item.get("disabled", False)
        if not isinstance(disabled, bool):
            raise PluginConfigError(f"plugin:{name}:invalid_disabled")
        raw_config = item.get("config")
        raw_config = {} if raw_config is None else raw_config
        if not isinstance(raw_config, Mapping):
            raise PluginConfigError(f"plugin:{name}:invalid_config:config:not_a_mapping")
        if disabled:
            continue
        safe_paths: dict[str, bool] = {}
        values = _expand(name, raw_config, "", environ, safe_paths)
        try:
            config = model.model_validate(values, context={"base_dir": base_dir})
        except ValidationError as exc:
            raise PluginConfigError(f"plugin:{name}:{_validation_codes(exc)}") from None
        for path in secret_values(config):
            if not safe_paths.get(path, True):
                raise PluginConfigError(f"plugin:{name}:invalid_config:{path}:secret_requires_placeholder")
        configs[name] = config
    return configs


def _expand(name: str, value: Any, path: str, environ: Mapping[str, str], safe_paths: dict[str, bool]) -> Any:
    """Expand placeholders recursively, recording per leaf whether it was a secret-safe placeholder.

    A value that is a whole ``${VAR:-}`` with ``VAR`` unset or empty drops its
    key, so the model default applies.
    """
    if isinstance(value, Mapping):
        expanded: dict[str, Any] = {}
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            if isinstance(item, str) and _drops_key(item, environ):
                safe_paths[child] = True
                continue
            expanded[key] = _expand(name, item, child, environ, safe_paths)
        return expanded
    if isinstance(value, list):
        return [_expand(name, item, f"{path}.{index}", environ, safe_paths) for index, item in enumerate(value)]
    if not isinstance(value, str):
        safe_paths[path] = False
        return value
    safe_paths[path] = bool(_SECRET_PLACEHOLDER.fullmatch(value))
    return _expand_string(name, value, environ)


def _drops_key(text: str, environ: Mapping[str, str]) -> bool:
    match = _PLACEHOLDER.fullmatch(text)
    return match is not None and match.group(2) == "" and not environ.get(match.group(1))


def _expand_string(name: str, text: str, environ: Mapping[str, str]) -> str:
    # Malformed placeholders are checked on the raw text, so an environment
    # value that merely contains ``${`` is not mistaken for one.
    if "${" in _PLACEHOLDER.sub("", text):
        raise PluginConfigError(f"plugin:{name}:invalid_placeholder")

    def replace(match: re.Match[str]) -> str:
        variable, default = match.group(1), match.group(2)
        if default is None:
            if variable not in environ:
                raise PluginConfigError(f"plugin:{name}:missing_env:{variable}")
            return environ[variable]
        return environ.get(variable) or default

    return _PLACEHOLDER.sub(replace, text)


def _validation_codes(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors(include_input=False, include_url=False):
        location = ".".join(str(item) for item in error.get("loc", ())) or "config"
        parts.append(f"invalid_config:{location}:{error.get('type', 'invalid')}")
    return ";".join(parts) or "invalid_config"


__all__ = [
    "PluginConfigError",
    "RelPath",
    "config_placeholders",
    "read_plugins_file",
    "resolve_plugin_configs",
    "secret_values",
]
