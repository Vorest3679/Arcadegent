"""Plugin installation/composite contracts with business-free fake plugins (plan 5, stage 2c)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from app.agent.llm.provider_adapter import ModelResponse, ModelToolCall
from app.agent.plugins import (
    CompositeTurnDomain,
    Plugin,
    PluginConfig,
    PluginEntry,
    PluginError,
    PluginInfo,
    install_plugins,
)
from app.agent.runtime.domain_port import BaseTurnDomain
from app.agent.runtime.kernel import AgentKernel, TurnInput
from app.agent.runtime.lifecycle import LifecycleContext
from app.agent.runtime.session_state import AgentSessionState
from app.agent.subagents.agent_node import AgentNode
from app.agent.subagents.subagent_builder import SubAgentProfile
from app.agent.tools.local import LocalToolProvider
from app.agent.tools.permission import ToolPermissionChecker
from app.agent.tools.registry import ToolRegistry
from app.agent.tools.spec import ToolContext, ToolSpec, ToolSpecError
from app.session.injector import CollectingPublisher

_ECHO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"value": {"type": "string"}},
    "required": ["value"],
    "additionalProperties": False,
}


def _echo(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    return {"echo": f"{context.require('prefix')}:{args['value']}"}


def _echo_spec(name: str = "echo_tool", *, capabilities: tuple[str, ...] = ()) -> ToolSpec:
    return ToolSpec(name=name, description="Echo a value.", input_schema=_ECHO_SCHEMA,
                    executor=_echo, capabilities=capabilities)


class _FixtureDomain(BaseTurnDomain):
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def on_turn_mount(self, ctx: LifecycleContext) -> None:
        self.calls.append("turn.mount")
        ctx.memory["fixture_marker"] = True

    def runtime_hints(self, state: AgentSessionState) -> dict[str, Any]:
        return {"fixture_hint": "on"}

    async def build_response(self, *, session_id: str, state: AgentSessionState, final_text: str) -> Any:
        return {"reply": final_text, "fixture": True}


class _FixturePlugin(Plugin):
    info = PluginInfo("fixture", "1.0.0")

    def __init__(self, *, prefix: str = "fixture", capabilities: tuple[str, ...] = (),
                 domain: BaseTurnDomain | None = None) -> None:
        self.prefix = prefix
        self.capabilities = capabilities
        self.domain = domain if domain is not None else _FixtureDomain()

    def hooks(self, config: PluginConfig) -> BaseTurnDomain:
        return self.domain

    def tools(self, config: PluginConfig) -> LocalToolProvider:
        return LocalToolProvider(specs=[_echo_spec(capabilities=self.capabilities)],
                                 services={"prefix": self.prefix})


def _named(base: type[Plugin], name: str) -> type[Plugin]:
    return type(f"_{name.title()}Plugin", (base,), {"info": PluginInfo(name, "1.0.0")})


class _Context:
    def __init__(self, message: str) -> None:
        self.instructions = "fixture"
        self.messages = [{"role": "user", "content": message}]


class _ContextBuilder:
    def build(self, *, session_state: AgentSessionState, request: Any, subagent: SubAgentProfile) -> _Context:
        return _Context(request.message)


class _SubAgents:
    def get(self, name: str) -> SubAgentProfile:
        return SubAgentProfile(name="main_agent", prompt_file="", allowed_tools=["echo_tool"])

    def node(self, name: str) -> AgentNode:
        return AgentNode(profile=self.get(name), scope="conversation", publish_text=True, spawnable=())


class _Store:
    def __init__(self) -> None:
        self.states: dict[str, AgentSessionState] = {}

    def get_or_create_session(self, session_id: str) -> AgentSessionState:
        return self.states.setdefault(session_id, AgentSessionState(session_id=session_id))

    def save_session(self, state: AgentSessionState) -> None:
        self.states[state.session_id] = state


class _Provider:
    streaming = False

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.script: list[ModelResponse] = [
            ModelResponse(text=None, status="completed", protocol="chat_completions", reported_model="stub",
                          tool_calls=[ModelToolCall(call_id="call_echo", name="echo_tool", arguments={"value": "x"})]),
            ModelResponse(text="done", status="completed", protocol="chat_completions", reported_model="stub"),
        ]

    async def complete(self, **kwargs: Any) -> ModelResponse:
        self.calls.append(kwargs)
        return self.script.pop(0)


def _run_turn(plugin_set, tmp_path):
    provider = _Provider()
    registry = ToolRegistry(
        providers=dict(plugin_set.tool_providers),
        permission_checker=ToolPermissionChecker(policy_file=tmp_path / "no-policy.yaml"),
        local_provider=LocalToolProvider(),
    )
    store = _Store()
    kernel = AgentKernel(
        context_builder=_ContextBuilder(),  # type: ignore[arg-type]
        subagent_builder=_SubAgents(),  # type: ignore[arg-type]
        tool_registry=registry,
        provider_adapter=provider,  # type: ignore[arg-type]
        session_store=store,  # type: ignore[arg-type]
        max_steps=4,
        domain=plugin_set.domain,
    )
    turn = TurnInput(session_id="s_plugin", client_id=None, message="hello",
                     request=SimpleNamespace(message="hello"), request_payload={"message": "hello"})
    events = CollectingPublisher()
    result = asyncio.run(kernel.run_turn(turn, events=events))
    return result, events, provider, store, registry


def test_fake_plugin_tool_hook_and_presenter_run_a_turn(tmp_path) -> None:
    plugin = _FixturePlugin()
    plugin_set = install_plugins([PluginEntry(plugin=plugin)])

    assert plugin_set.domain.plugin_names == ("fixture",)
    assert list(plugin_set.tool_providers) == ["fixture"]
    assert plugin_set.health() == [{
        "name": "fixture", "version": "1.0.0", "trusted": False,
        "contributions": {"hooks": True, "tools": ["echo_tool"]}, "secrets": {},
    }]

    result, events, provider, store, registry = _run_turn(plugin_set, tmp_path)

    assert result.response == {"reply": "done", "fixture": True}
    assert "tool.completed" in events.names()
    assert provider.calls[0]["runtime_hints"]["fixture_hint"] == "on"
    assert plugin.domain.calls == ["turn.mount"]
    assert store.states["s_plugin"].working_memory["fixture_marker"] is True
    assert [tool.name for tool in asyncio.run(registry.list_tools(provider="fixture"))] == ["echo_tool"]


def test_installation_failures_are_labeled_and_leave_runtime_writes_unlocked() -> None:
    class _BrokenPlugin(Plugin):
        info = PluginInfo("broken", "1.0.0")

        def hooks(self, config: PluginConfig) -> BaseTurnDomain:
            raise RuntimeError("value-that-must-not-leak")

    class _PresenterOnly(Plugin):
        info = PluginInfo("presenter", "1.0.0")

        def hooks(self, config: PluginConfig) -> BaseTurnDomain:
            return _FixtureDomain()

    built: list[LocalToolProvider] = []

    class _RecordingPlugin(_FixturePlugin):
        def tools(self, config: PluginConfig) -> LocalToolProvider:
            built.append(super().tools(config))
            return built[-1]

    with pytest.raises(PluginError, match=r"^plugin:broken:build_failed:hooks$") as error:
        install_plugins([PluginEntry(plugin=_RecordingPlugin()), PluginEntry(plugin=_BrokenPlugin())])
    assert "value-that-must-not-leak" not in str(error.value)
    # A failed installation locks nothing: the provider it already built still accepts writes.
    assert built[0].add([_echo_spec("late_tool")]) == ["late_tool"]

    cases = [
        ([_FixturePlugin(), _FixturePlugin()], r"^plugin:fixture:duplicate_plugin$"),
        ([_FixturePlugin(), _named(_FixturePlugin, "other")()], r"^plugin:other:duplicate_tool:echo_tool$"),
        ([_FixturePlugin(capabilities=("spawn_agent",))],
         r"^plugin:fixture:reserved_capability:echo_tool:spawn_agent$"),
        ([_FixturePlugin(), _PresenterOnly()],
         r"^plugin:presenter:exclusive_hook_conflict:build_response:plugins:fixture,presenter$"),
    ]
    for plugins, code in cases:
        with pytest.raises(PluginError, match=code):
            install_plugins([PluginEntry(plugin=plugin) for plugin in plugins])


def test_trusted_plugin_may_declare_reserved_capabilities_and_runtime_writes_are_locked() -> None:
    plugin_set = install_plugins([PluginEntry(plugin=_FixturePlugin(capabilities=("spawn_agent",)), trusted=True)])
    provider = plugin_set.tool_providers["fixture"]

    assert plugin_set.health()[0]["trusted"] is True
    with pytest.raises(ToolSpecError, match="runtime_writes_locked"):
        provider.add([_echo_spec("late_tool")])


def test_load_plugins_reads_the_file_and_applies_host_decisions(tmp_path: Path) -> None:
    from app.agent.plugins import PluginConfigError, load_plugins

    class _Other(Plugin):
        info = PluginInfo("other", "1.0.0")

    plugins = {"fixture": _FixturePlugin(capabilities=("spawn_agent",)), "other": _Other()}
    path = tmp_path / "plugins.yaml"
    path.write_text("plugins:\n  other:\n  fixture:\n", encoding="utf-8")

    plugin_set = load_plugins(path, plugins=plugins, environ={}, trusted={"fixture"}, required={"fixture"})
    assert [(item.name, item.trusted) for item in plugin_set.installed] == [("other", False), ("fixture", True)]

    # Trust is the host's: the same file without it fails on the reserved capability.
    with pytest.raises(PluginError, match=r"^plugin:fixture:reserved_capability:echo_tool:spawn_agent$"):
        load_plugins(path, plugins={**plugins, "fixture": _FixturePlugin(capabilities=("spawn_agent",))}, environ={})
    path.write_text("plugins:\n  other:\n  fixture:\n    disabled: true\n", encoding="utf-8")
    with pytest.raises(PluginError, match=r"^plugin:fixture:required_plugin_disabled$"):
        load_plugins(path, plugins=plugins, environ={}, required={"fixture"})
    path.write_text("plugins:\n  ghost:\n", encoding="utf-8")
    with pytest.raises(PluginConfigError, match=r"^plugin:ghost:unknown_plugin$"):
        load_plugins(path, plugins=plugins, environ={})


def test_config_type_and_secrets_are_reported_without_values() -> None:
    class _Config(PluginConfig):
        api_key: SecretStr = SecretStr("")
        tokens: list[SecretStr] = []
        optional: SecretStr | None = None

    class _ConfiguredPlugin(Plugin):
        info = PluginInfo("configured", "1.0.0")
        config_model = _Config

    plugin_set = install_plugins([PluginEntry(
        plugin=_ConfiguredPlugin(),
        config=_Config(api_key=SecretStr("secret-value"), tokens=[SecretStr("")]),
    )])
    health = plugin_set.health()[0]

    assert health["secrets"] == {"api_key": "set", "tokens.0": "missing", "optional": "missing"}
    assert "secret-value" not in json.dumps(health)
    assert health["contributions"] == {"hooks": False, "tools": []}
    assert install_plugins([PluginEntry(plugin=_ConfiguredPlugin())]).installed[0].config == _Config()
    with pytest.raises(PluginError, match=r"^plugin:configured:invalid_config_type$"):
        install_plugins([PluginEntry(plugin=_ConfiguredPlugin(), config=PluginConfig())])


def test_merged_policy_conflicts_name_both_plugins() -> None:
    class _OtherDomain(BaseTurnDomain):
        def runtime_hints(self, state: AgentSessionState) -> dict[str, Any]:
            return {"fixture_hint": "other"}

    class _OtherPlugin(Plugin):
        info = PluginInfo("other", "1.0.0")

        def hooks(self, config: PluginConfig) -> BaseTurnDomain:
            return _OtherDomain()

    plugin_set = install_plugins([PluginEntry(plugin=_FixturePlugin()), PluginEntry(plugin=_OtherPlugin())])

    with pytest.raises(PluginError, match=r"runtime_hints_conflict:fixture_hint:plugins:fixture,other"):
        plugin_set.domain.runtime_hints(AgentSessionState(session_id="s"))


def test_a_failing_hook_does_not_skip_later_plugins() -> None:
    calls: list[str] = []

    class _Failing(BaseTurnDomain):
        async def on_turn_unmounted(self, ctx: LifecycleContext) -> None:
            calls.append("failing")
            raise RuntimeError("boom")

    class _Later(BaseTurnDomain):
        async def on_turn_unmounted(self, ctx: LifecycleContext) -> None:
            calls.append("later")

    domain = CompositeTurnDomain([("failing", _Failing()), ("later", _Later())])
    with pytest.raises(RuntimeError, match="boom") as error:
        asyncio.run(domain.on_turn_unmounted(None))  # type: ignore[arg-type]

    assert calls == ["failing", "later"]
    assert error.value.__notes__ == ["plugin=failing hook=on_turn_unmounted"]


def test_composite_without_plugins_keeps_base_domain_defaults() -> None:
    domain = CompositeTurnDomain()
    state = AgentSessionState(session_id="s")

    assert domain.runtime_hints(state) == {}
    assert domain.turn_display_snapshot(state) is None
    assert domain.turn_summary(state) == ""


def test_container_loads_the_plugins_file_and_wires_the_arcade_domain(tmp_path: Path, monkeypatch) -> None:
    from app.core.config import Settings
    from app.core.container import build_container
    from app.domain.arcade.domain import ArcadeTurnDomain

    data_path = tmp_path / "shops.jsonl"
    data_path.write_text(json.dumps({"source": "fixture", "source_id": 1, "name": "Alpha"}) + "\n", encoding="utf-8")
    (tmp_path / "no-mcp").mkdir()
    for name in ("AMAP_API_KEY", "AMAP_BASE_URL", "AMAP_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AMAP_API_KEY", "file-path-key")
    # No ``plugin_configs``: the container reads ``Settings.agent_plugins_file``
    # (the repository's plugins.yaml) with the process environment, like production.
    container = build_container(Settings(
        data_jsonl_path=data_path, arcade_geo_cache_path=tmp_path / "geo.json", mcp_servers_dir=tmp_path / "no-mcp",
        supabase_url="https://fixture.invalid", supabase_service_role_key="test-key",
    ))

    arcade_config = container.plugin_set.installed[1].config
    assert arcade_config.amap.api_key.get_secret_value() == "file-path-key"  # type: ignore[attr-defined]
    assert container.react_runtime._domain is container.plugin_set.domain
    domains = dict(container.plugin_set.domain.plugin_domains())
    assert list(domains) == ["arcade"]
    assert isinstance(domains["arcade"], ArcadeTurnDomain)
    assert domains["arcade"].payload_mapper is container.arcade_payload_mapper
    assert [(item["name"], item["trusted"]) for item in container.plugin_set.health()] == [
        ("core", True), ("arcade", False)]


@pytest.mark.parametrize("disabled", ["core", "arcade"])
def test_container_refuses_to_start_without_a_required_plugin(tmp_path: Path, disabled: str) -> None:
    from app.agent.plugins import EmptyConfig
    from app.core.config import Settings
    from app.core.container import build_container
    from app.domain.arcade.plugin import ArcadeConfig

    data_path = tmp_path / "shops.jsonl"
    data_path.write_text(json.dumps({"source": "fixture", "source_id": 1, "name": "Alpha"}) + "\n", encoding="utf-8")
    configs = {"core": EmptyConfig(), "arcade": ArcadeConfig()}
    del configs[disabled]
    settings = Settings(data_jsonl_path=data_path, arcade_geo_cache_path=tmp_path / "geo.json",
                        mcp_servers_dir=tmp_path, supabase_url="https://fixture.invalid",
                        supabase_service_role_key="test-key")

    with pytest.raises(ValueError, match=rf"^plugin:{disabled}:required_plugin_disabled$"):
        build_container(settings, plugin_configs=configs)
