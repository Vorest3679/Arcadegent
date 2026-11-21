"""Plugin protocol, configuration, composition and installation for the generic agent layer."""

from app.agent.plugins.composite import CompositeTurnDomain
from app.agent.plugins.config import (
    PluginConfigError,
    RelPath,
    read_plugins_file,
    resolve_plugin_configs,
    secret_values,
)
from app.agent.plugins.core import CorePlugin
from app.agent.plugins.host import (
    InstalledPlugin,
    PluginEntry,
    PluginSet,
    install_configured,
    install_plugins,
    load_plugins,
)
from app.agent.plugins.spec import EmptyConfig, Plugin, PluginConfig, PluginError, PluginInfo

__all__ = [
    "CompositeTurnDomain",
    "CorePlugin",
    "EmptyConfig",
    "InstalledPlugin",
    "Plugin",
    "PluginConfig",
    "PluginConfigError",
    "PluginEntry",
    "PluginError",
    "PluginInfo",
    "PluginSet",
    "RelPath",
    "install_configured",
    "install_plugins",
    "load_plugins",
    "read_plugins_file",
    "resolve_plugin_configs",
    "secret_values",
]
