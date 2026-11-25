"""Resolve plugin-declared resources into host registry inputs.

Plugins declare skill roots, MCP server directories and prompt directories as
class-level data, with an optional ``resolved_resources`` override for
config-selected directories. The host reads those declarations before
constructing services that depend on them: the skill registry and the MCP
gateway. Paths are host-internal and never appear in plugin errors.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

from app.agent.plugins.config import config_placeholders
from app.agent.plugins.spec import Plugin, PluginConfig, PluginError, PluginResources
from app.agent.tools.mcp.gateway import MCPServerConfig, build_mcp_server_configs
from app.agent.tools.mcp.ports import MCPResultNormalizer, MCPToolSelector


def resolve_resources(name: str, plugin_type: type[Plugin], config: PluginConfig) -> PluginResources:
    """One plugin's validated resources for one validated config."""
    if not isinstance(config, plugin_type.config_model):
        raise PluginError(f"plugin:{name}:invalid_config_type")
    try:
        resources = plugin_type.resolved_resources(config)
    except Exception as exc:
        raise PluginError(f"plugin:{name}:invalid_resources") from exc
    if not isinstance(resources, PluginResources):
        raise PluginError(f"plugin:{name}:invalid_resources")
    for root in (*resources.skills, *resources.mcp_servers, *resources.prompts):
        if not isinstance(root, Path):
            raise PluginError(f"plugin:{name}:invalid_resources")
    _port_items(name, resources, "mcp_selectors", "select", "selector")
    _port_items(name, resources, "mcp_normalizers", "normalize", "normalizer")
    return resources


def _port_items(
    name: str,
    resources: PluginResources,
    attribute: str,
    method: str,
    kind: str,
) -> tuple[tuple[str, object], ...]:
    """Validated ``(binding name, port)`` pairs from one resource declaration."""
    ports = getattr(resources, attribute)
    if not isinstance(ports, Mapping):
        raise PluginError(f"plugin:{name}:invalid_mcp_{kind}")
    pairs: list[tuple[str, object]] = []
    for key, port in ports.items():
        if not isinstance(key, str) or not key.strip():
            raise PluginError(f"plugin:{name}:invalid_mcp_{kind}")
        if not callable(getattr(port, method, None)):
            raise PluginError(f"plugin:{name}:invalid_mcp_{kind}")
        pairs.append((key.strip(), port))
    return tuple(pairs)


def _collect_ports(
    resources: Mapping[str, PluginResources],
    attribute: str,
    method: str,
    kind: str,
) -> dict[str, object]:
    owned: dict[str, tuple[str, object]] = {}
    for plugin_name, item in resources.items():
        for key, port in _port_items(plugin_name, item, attribute, method, kind):
            owner, _claimed = owned.setdefault(key, (plugin_name, port))
            if owner != plugin_name:
                raise PluginError(
                    f"plugin:{plugin_name}:duplicate_mcp_{kind}:{key}:plugins:{owner},{plugin_name}"
                )
    return {key: port for key, (_owner, port) in owned.items()}


def collect_mcp_selectors(
    resources: Mapping[str, PluginResources],
) -> dict[str, MCPToolSelector]:
    """Selector ports in installation order, keyed by globally unique name."""
    return cast(
        dict[str, MCPToolSelector],
        _collect_ports(resources, "mcp_selectors", "select", "selector"),
    )


def collect_mcp_normalizers(
    resources: Mapping[str, PluginResources],
) -> dict[str, MCPResultNormalizer]:
    """Normalizer ports in installation order, keyed by MCP server name."""
    return cast(
        dict[str, MCPResultNormalizer],
        _collect_ports(resources, "mcp_normalizers", "normalize", "normalizer"),
    )


def resolve_plugin_resources(
    plugins: Mapping[str, type[Plugin]],
    configs: Mapping[str, PluginConfig],
) -> dict[str, PluginResources]:
    """Return each enabled plugin's resources, in config (installation) order."""
    resolved: dict[str, PluginResources] = {}
    for name, config in configs.items():
        plugin_type = plugins.get(name)
        if plugin_type is None:
            raise PluginError(f"plugin:{name}:unknown_plugin")
        resolved[name] = resolve_resources(name, plugin_type, config)
    return resolved


def collect_skill_roots(resources: Mapping[str, PluginResources]) -> tuple[Path, ...]:
    """Skill roots in installation order, de-duplicated."""
    return tuple(dict.fromkeys(root for item in resources.values() for root in item.skills))


def build_plugin_mcp_servers(
    configs: Mapping[str, PluginConfig],
    resources: Mapping[str, PluginResources],
    *,
    default_timeout_seconds: float,
) -> list[MCPServerConfig]:
    """Build MCP server configs from plugin directories, expanding config placeholders.

    Server names stay globally unique. Errors name the plugin and a stable code
    only: server files may contain secrets or host paths.
    """
    owned: dict[str, tuple[str, MCPServerConfig]] = {}
    for name, item in resources.items():
        for server in _plugin_mcp_servers(name, configs[name], item, default_timeout_seconds):
            # One lookup claims the name; the same name from any other file,
            # in this plugin or another, is a conflict.
            owner, claimed = owned.setdefault(server.name, (name, server))
            if claimed is not server:
                raise PluginError(f"plugin:{name}:duplicate_mcp_server:{server.name}:plugins:{owner},{name}")
    return [server for _owner, server in owned.values()]


def _plugin_mcp_servers(
    name: str,
    config: PluginConfig,
    resources: PluginResources,
    default_timeout_seconds: float,
) -> list[MCPServerConfig]:
    """Every server in one plugin's directories, in directory order."""
    placeholders = config_placeholders(config)
    try:
        return [
            server
            for directory in resources.mcp_servers
            for server in build_mcp_server_configs(
                config_dir=directory,
                default_timeout_seconds=default_timeout_seconds,
                placeholders=placeholders,
            )
        ]
    except ValueError as exc:
        raise PluginError(f"plugin:{name}:invalid_mcp_servers") from exc


__all__ = [
    "build_plugin_mcp_servers",
    "collect_mcp_normalizers",
    "collect_mcp_selectors",
    "collect_skill_roots",
    "resolve_plugin_resources",
    "resolve_resources",
]
