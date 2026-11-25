"""Plugin protocol, configuration, composition and installation for the generic agent layer.

Plugin authors import everything they need from here::

    from app.agent.plugins import AgentDefinition, AgentRoster, Plugin, PluginConfig, PluginInfo
"""

from app.agent.plugins.composite import CompositeTurnDomain
from app.agent.plugins.config import (
    PluginConfigError,
    RelPath,
    config_placeholders,
    read_plugins_file,
    resolve_plugin_configs,
    secret_values,
)
from app.agent.plugins.core import CoreConfig, CorePlugin, CoreSkillsConfig
from app.agent.plugins.host import (
    InstalledPlugin,
    PluginEntry,
    PluginSet,
    install_configured,
    install_plugins,
    load_plugin_configs,
    load_plugins,
    require_enabled_plugins,
)
from app.agent.plugins.resources import (
    build_plugin_mcp_servers,
    collect_mcp_normalizers,
    collect_mcp_selectors,
    collect_skill_roots,
    resolve_plugin_resources,
)
from app.agent.plugins.spec import (
    EmptyConfig,
    Plugin,
    PluginConfig,
    PluginError,
    PluginInfo,
    PluginResources,
)
from app.agent.subagents.roster import AgentDefinition, AgentRoster, AgentRosterError
from app.agent.tools.local import LocalToolProvider
from app.agent.tools.mcp.ports import MCPResultNormalizer, MCPToolSelector

__all__ = [
    "AgentDefinition",
    "AgentRoster",
    "AgentRosterError",
    "CompositeTurnDomain",
    "CoreConfig",
    "CorePlugin",
    "CoreSkillsConfig",
    "EmptyConfig",
    "InstalledPlugin",
    "LocalToolProvider",
    "MCPResultNormalizer",
    "MCPToolSelector",
    "Plugin",
    "PluginConfig",
    "PluginConfigError",
    "PluginEntry",
    "PluginError",
    "PluginInfo",
    "PluginResources",
    "PluginSet",
    "RelPath",
    "build_plugin_mcp_servers",
    "collect_mcp_normalizers",
    "collect_mcp_selectors",
    "collect_skill_roots",
    "config_placeholders",
    "install_configured",
    "install_plugins",
    "load_plugin_configs",
    "load_plugins",
    "read_plugins_file",
    "require_enabled_plugins",
    "resolve_plugin_configs",
    "resolve_plugin_resources",
    "secret_values",
]
