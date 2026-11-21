"""Install plugins into one immutable ``PluginSet``.

:func:`load_plugins` is the host's single entry point: it reads the plugins
file, keeps the enabled plugins in file order, validates their configs, checks
required plugins and installs them. :func:`install_plugins` is the step that
takes already-chosen entries (tests and embedders that build configs in code).

Installation is a pure function: build members run, the results are checked
against each other, and either a complete ``PluginSet`` is returned or
``PluginError("plugin:<name>:<code>[:detail]")`` is raised. Nothing is
registered anywhere until the caller hands the set to the registries, so a
failure leaves no partial state. A future reload builds a new set the same way
and swaps the reference.

Trust is decided by the host per plugin: only a trusted plugin's tools may
declare reserved capabilities (``spawn_agent`` and friends). Runtime writes to
every plugin tool provider are locked on success, so a plugin cannot add tools
after the checks.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, TypeVar

from app.agent.plugins.composite import CompositeTurnDomain
from app.agent.plugins.config import read_plugins_file, resolve_plugin_configs, secret_values
from app.agent.plugins.spec import Plugin, PluginConfig, PluginError, PluginInfo
from app.agent.runtime.domain_port import BaseTurnDomain
from app.agent.tools.local import LocalToolProvider

_T = TypeVar("_T")


@dataclass(frozen=True)
class PluginEntry:
    """One plugin to install: an instance built with its dependencies, its config and trust."""

    plugin: Plugin
    config: PluginConfig | None = None  # ``None``: the plugin's default config
    trusted: bool = False


@dataclass(frozen=True)
class InstalledPlugin:
    """One installed plugin and the objects it contributed."""

    plugin: Plugin
    config: PluginConfig
    trusted: bool
    domain: BaseTurnDomain | None
    tools: LocalToolProvider | None

    @property
    def name(self) -> str:
        return self.plugin.info.name

    def health(self) -> dict[str, Any]:
        """Public, payload-free description for ``/health``."""
        return {
            "name": self.name,
            "version": self.plugin.info.version,
            "trusted": self.trusted,
            "contributions": {
                "hooks": self.domain is not None,
                "tools": sorted(self.tools.entries()) if self.tools is not None else [],
            },
            "secrets": {
                path: "set" if value is not None and value.get_secret_value() else "missing"
                for path, value in secret_values(self.config).items()
            },
        }


@dataclass(frozen=True)
class PluginSet:
    """Immutable result of an installation, handed to the runtime and the HTTP layer."""

    installed: tuple[InstalledPlugin, ...]
    domain: CompositeTurnDomain

    @property
    def tool_providers(self) -> Mapping[str, LocalToolProvider]:
        """Plugin tool providers keyed by plugin name, in installation order."""
        return MappingProxyType({
            item.name: item.tools for item in self.installed if item.tools is not None
        })

    def health(self) -> list[dict[str, Any]]:
        return [item.health() for item in self.installed]


def install_plugins(entries: Sequence[PluginEntry]) -> PluginSet:
    """Build every entry, check them together, and return the set or raise."""
    installed: list[InstalledPlugin] = []
    tool_owners: dict[str, str] = {}
    for entry in entries:
        item = _build(entry)
        if any(other.name == item.name for other in installed):
            raise PluginError(f"plugin:{item.name}:duplicate_plugin")
        for tool_name in item.tools.entries() if item.tools is not None else ():
            if tool_name in tool_owners:
                raise PluginError(f"plugin:{item.name}:duplicate_tool:{tool_name}")
            tool_owners[tool_name] = item.name
        installed.append(item)
    domain = CompositeTurnDomain([(item.name, item.domain) for item in installed if item.domain is not None])
    for item in installed:
        if item.tools is not None:
            item.tools.lock_runtime_writes(trusted=item.trusted)
    return PluginSet(installed=tuple(installed), domain=domain)


def load_plugins(
    path: Path,
    *,
    plugins: Mapping[str, Plugin],
    environ: Mapping[str, str],
    trusted: Collection[str] = (),
    required: Collection[str] = (),
) -> PluginSet:
    """Read the plugins file at ``path`` and install what it enables.

    ``plugins`` are the host's plugin instances by name (the only names the
    file may use); ``trusted`` and ``required`` are host decisions the file
    cannot change. ``environ`` supplies placeholder values.
    """
    configs = resolve_plugin_configs(
        read_plugins_file(path),
        base_dir=path.parent,
        models={name: plugin.config_model for name, plugin in plugins.items()},
        environ=environ,
    )
    return install_configured(plugins, configs, trusted=trusted, required=required)


def install_configured(
    plugins: Mapping[str, Plugin],
    configs: Mapping[str, PluginConfig],
    *,
    trusted: Collection[str] = (),
    required: Collection[str] = (),
) -> PluginSet:
    """Install the plugins named by ``configs``, in its order, with those configs."""
    for name in configs:
        if name not in plugins:
            raise PluginError(f"plugin:{name}:unknown_plugin")
    for name in required:
        if name not in configs:
            # A disabled required plugin would silently drop tools or hooks
            # the host relies on, so refuse instead.
            raise PluginError(f"plugin:{name}:required_plugin_disabled")
    return install_plugins([
        PluginEntry(plugin=plugins[name], config=config, trusted=name in trusted)
        for name, config in configs.items()
    ])


def _build(entry: PluginEntry) -> InstalledPlugin:
    if not isinstance(entry, PluginEntry) or not isinstance(entry.plugin, Plugin):
        raise PluginError("plugin:invalid_entry")
    plugin = entry.plugin
    info = getattr(plugin, "info", None)
    if not isinstance(info, PluginInfo):
        raise PluginError("plugin:invalid_info")
    name = info.name
    model = plugin.config_model
    if not (isinstance(model, type) and issubclass(model, PluginConfig)):
        raise PluginError(f"plugin:{name}:invalid_config_model")
    config = entry.config if entry.config is not None else model()
    if not isinstance(config, model):
        raise PluginError(f"plugin:{name}:invalid_config_type")

    domain = _member(name, "hooks", plugin.hooks, config)
    if domain is not None and not isinstance(domain, BaseTurnDomain):
        raise PluginError(f"plugin:{name}:invalid_hook_domain")
    tools = _member(name, "tools", plugin.tools, config)
    if tools is not None:
        if not isinstance(tools, LocalToolProvider):
            raise PluginError(f"plugin:{name}:invalid_tool_provider")
        for tool_name, tool in tools.entries().items():
            if tool.origin != "plugin":
                raise PluginError(f"plugin:{name}:untrusted_tool_origin:{tool_name}")
            if not entry.trusted and tool.spec.reserved_capabilities:
                capabilities = ",".join(sorted(tool.spec.reserved_capabilities))
                raise PluginError(f"plugin:{name}:reserved_capability:{tool_name}:{capabilities}")
    return InstalledPlugin(plugin=plugin, config=config, trusted=entry.trusted, domain=domain, tools=tools)


def _member(name: str, member: str, build: Callable[[PluginConfig], _T], config: PluginConfig) -> _T:
    try:
        return build(config)
    except PluginError as exc:
        message = str(exc)
        raise PluginError(message if message.startswith("plugin:") else f"plugin:{name}:{message}") from exc
    except Exception as exc:
        # The original exception may carry configuration values or paths;
        # keep it as the cause but never put its text in the public code.
        raise PluginError(f"plugin:{name}:build_failed:{member}") from exc


__all__ = ["InstalledPlugin", "PluginEntry", "PluginSet", "install_configured", "install_plugins", "load_plugins"]
