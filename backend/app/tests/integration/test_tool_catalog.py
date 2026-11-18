"""ToolSpec validation and the registry's local tool catalog management (plan 5, stage 2a)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from app.agent.artifacts.model import Artifact
from app.agent.tools.base import ProviderExecutionResult, ToolDescriptor, ToolOutcome
from app.agent.tools.builtin import BuiltinToolProvider
from app.agent.tools.local import LocalToolProvider
from app.agent.tools.permission import ToolPermissionChecker
from app.agent.tools.registry import ToolRegistry
from app.agent.tools.spec import ToolContext, ToolSpec, ToolSpecError

OBJECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"text": {"type": "string"}, "times": {"type": "integer", "default": 1}},
    "required": ["text"],
    "additionalProperties": False,
}


def _echo(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
    return {"echo": args["text"] * args["times"]}


def _spec(name: str = "echo_tool", **overrides: Any) -> ToolSpec:
    values: dict[str, Any] = {
        "name": name,
        "description": "Echo the text.",
        "input_schema": OBJECT_SCHEMA,
        "executor": _echo,
    }
    values.update(overrides)
    return ToolSpec(**values)


def _registry(tmp_path: Path, *providers: Any, local: LocalToolProvider | None = None) -> ToolRegistry:
    return ToolRegistry(
        providers=list(providers),
        permission_checker=ToolPermissionChecker(policy_file=tmp_path / "no-policy.yaml"),
        local_provider=local if local is not None else LocalToolProvider(),
    )


def _run(awaitable):
    return asyncio.run(awaitable)


class _RemoteProvider:
    """Minimal non-local provider that owns one tool."""

    provider_name = "remote"

    def __init__(self) -> None:
        self.refreshed = 0

    async def get_tools(self) -> dict[str, ToolDescriptor]:
        return {"remote_tool": ToolDescriptor(
            name="remote_tool", description="remote", provider="remote", input_schema={"type": "object"})}

    async def execute(self, *, tool_name, raw_arguments, validated_arguments=None) -> ProviderExecutionResult:
        return ProviderExecutionResult(status="completed", output={})

    async def refresh(self) -> None:
        self.refreshed += 1

    def health(self) -> dict[str, Any]:
        return {}


# -- ToolSpec ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"name": "1tool"}, "invalid_tool_name"),
        ({"name": "has space"}, "invalid_tool_name"),
        ({"name": "x" * 65}, "invalid_tool_name"),
        ({"name": "mcp__amap__route"}, "reserved_tool_prefix:mcp__amap__route"),
        ({"description": "  "}, "invalid_tool_description:echo_tool"),
        ({"input_schema": {"type": "array"}}, "invalid_tool_input_schema:echo_tool:root_must_be_object"),
        ({"input_schema": {"type": "object", "properties": {"a": {"type": 3}}}}, "invalid_tool_input_schema:echo_tool"),
        ({"execution_mode": "sometimes"}, "invalid_tool_execution_mode:echo_tool"),
        ({"kind": "web_search"}, "invalid_tool_kind:echo_tool"),
        ({"executor": "module:function"}, "invalid_tool_executor:echo_tool"),
        ({"argument_preparer": 3}, "invalid_tool_argument_preparer:echo_tool"),
        ({"capabilities": "read_only"}, "invalid_tool_capabilities:echo_tool"),
        ({"capabilities": ("read_only", "")}, "invalid_tool_capabilities:echo_tool"),
        ({"metadata": ["x"]}, "invalid_tool_metadata:echo_tool"),
    ],
)
def test_invalid_specs_cannot_be_constructed(overrides: dict[str, Any], code: str) -> None:
    with pytest.raises(ToolSpecError, match=code):
        _spec(**overrides)


def test_spec_normalizes_and_validates_arguments() -> None:
    metadata = {"owner": "tests"}
    spec = _spec(capabilities=(" read_only", "read_only", "concludes_turn"), metadata=metadata)
    metadata["owner"] = "changed"

    assert spec.capabilities == ("read_only", "concludes_turn")
    assert spec.metadata["owner"] == "tests"
    with pytest.raises(TypeError):
        spec.metadata["owner"] = "x"  # type: ignore[index]
    assert spec.validate_arguments({"text": "a"}) == {"text": "a", "times": 1}
    descriptor = spec.to_descriptor(provider="local")
    assert (descriptor.name, descriptor.provider, descriptor.execution_mode) == ("echo_tool", "local", "exclusive")
    assert descriptor.validator is not None and descriptor.validator({"text": "b"})["times"] == 1


def test_from_definition_reads_json_and_ignores_import_strings(tmp_path: Path) -> None:
    definition = {
        "name": "echo_tool",
        "description": "Echo the text.",
        "executor": "nowhere.module:missing",
        "capabilities": ["read_only"],
        "execution_mode": "Parallel",
        "metadata": {"from": "file"},
        "input_schema": OBJECT_SCHEMA,
    }
    path = tmp_path / "echo_tool.json"
    path.write_text(json.dumps(definition), encoding="utf-8")

    from_path = ToolSpec.from_definition(path, executor=_echo)
    from_mapping = ToolSpec.from_definition(definition, executor=_echo, metadata={"from": "host"})

    assert from_path.executor is _echo and from_path.execution_mode == "parallel"
    assert from_path.capabilities == ("read_only",) and from_path.metadata["from"] == "file"
    assert from_mapping.metadata["from"] == "host"
    with pytest.raises(ToolSpecError, match="unreadable_tool_definition:missing.json"):
        ToolSpec.from_definition(tmp_path / "missing.json", executor=_echo)


# -- registry catalog management ---------------------------------------------


def test_add_list_execute_and_delete_a_registered_tool(tmp_path: Path) -> None:
    registry = _registry(tmp_path)

    assert _run(registry.add_tools([_spec()])) == ["echo_tool"]
    listed = _run(registry.list_tools())
    assert [(tool.name, tool.provider) for tool in listed] == [("echo_tool", "local")]
    assert registry.local_provider.health()["origins"] == {"registered": ["echo_tool"]}

    result = _run(registry.execute(call_id="c1", tool_name="echo_tool",
                                   raw_arguments={"text": "ab", "times": 2}, allowed_tools=["echo_tool"]))
    assert (result.status, result.output) == ("completed", {"echo": "abab"})

    assert _run(registry.delete_tools(["echo_tool"])) == ["echo_tool"]
    assert _run(registry.list_tools()) == []
    gone = _run(registry.execute(call_id="c2", tool_name="echo_tool",
                                 raw_arguments={"text": "ab"}, allowed_tools=["echo_tool"]))
    assert gone.status == "failed" and gone.error_message == "unknown_tool:echo_tool"


def test_registration_does_not_grant_access(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    _run(registry.add_tools([_spec()]))

    assert _run(registry.tool_definitions(allowed_tools=["other_tool"])) == []
    assert _run(registry.list_tools(allowed_tools=["other_*"])) == []
    denied = _run(registry.execute(call_id="c1", tool_name="echo_tool",
                                   raw_arguments={"text": "a"}, allowed_tools=["other_tool"]))
    assert denied.status == "failed" and denied.output["error"]["type"] == "permission_error"
    definitions = _run(registry.tool_definitions(allowed_tools=["echo_tool"]))
    assert [item["function"]["name"] for item in definitions] == ["echo_tool"]


def test_update_replaces_a_registered_tool_in_place(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    _run(registry.add_tools([_spec("first_tool"), _spec("second_tool")]))

    async def shout(context: ToolContext, args: dict[str, Any]) -> ToolOutcome:
        return ToolOutcome(output={"echo": args["text"].upper()},
                           artifacts=(Artifact(key="last_echo", value=args["text"]),))

    assert _run(registry.update_tools([_spec("first_tool", executor=shout, execution_mode="parallel")])) == [
        "first_tool"]
    assert list(_run(registry.get_tools())) == ["first_tool", "second_tool"]
    assert _run(registry.execution_mode("first_tool")) == "parallel"
    result = _run(registry.execute(call_id="c1", tool_name="first_tool",
                                   raw_arguments={"text": "hi"}, allowed_tools=["first_tool"]))
    assert result.output == {"echo": "HI"} and result.artifacts[0].key == "last_echo"


def test_failed_batches_change_nothing(tmp_path: Path) -> None:
    registry = _registry(tmp_path, _RemoteProvider())
    service = object()
    _run(registry.add_tools([_spec("kept_tool")], services={"store": service}))
    revision = registry.local_provider.revision

    failures = [
        ([_spec("new_tool"), _spec("new_tool")], None, "duplicate_tool:new_tool"),
        ([_spec("new_tool"), _spec("kept_tool")], None, "duplicate_tool:kept_tool"),
        ([_spec("new_tool"), _spec("remote_tool")], None, "duplicate_tool:remote_tool"),
        ([_spec("new_tool", capabilities=("spawn_agent",))], None, "reserved_capability:new_tool:spawn_agent"),
        ([_spec("new_tool")], {"store": object()}, "duplicate_service:store"),
    ]
    for specs, services, code in failures:
        with pytest.raises(ToolSpecError, match=code):
            _run(registry.add_tools(specs, services=services))

    assert registry.local_provider.revision == revision
    assert [tool.name for tool in _run(registry.list_tools())] == ["kept_tool", "remote_tool"]
    assert registry.local_provider.health()["services"] == ["store"]
    # concludes_turn is a declarative capability local tools may use.
    assert _run(registry.add_tools([_spec("final_tool", capabilities=("concludes_turn",))])) == ["final_tool"]


def test_update_and_delete_only_touch_registered_tools(tmp_path: Path) -> None:
    (tmp_path / "manifest_tool.json").write_text(json.dumps({
        "name": "manifest_tool", "description": "from file",
        "executor": "app.agent.tools.builtin.executors.invoke_worker:execute", "input_schema": {"type": "object"},
    }), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"tools": ["manifest_tool.json"]}), encoding="utf-8")
    registry = _registry(tmp_path, local=BuiltinToolProvider(manifest_path=tmp_path / "manifest.json"))
    _run(registry.add_tools([_spec("registered_tool")]))

    with pytest.raises(ToolSpecError, match="immutable_tool:manifest_tool:manifest"):
        _run(registry.delete_tools(["manifest_tool"]))
    with pytest.raises(ToolSpecError, match="immutable_tool:manifest_tool:manifest"):
        _run(registry.update_tools([_spec("manifest_tool")]))
    with pytest.raises(ToolSpecError, match="unknown_tool:missing_tool"):
        _run(registry.update_tools([_spec("missing_tool")]))
    with pytest.raises(ToolSpecError, match="unknown_tool:missing_tool"):
        _run(registry.delete_tools(["registered_tool", "missing_tool"]))
    assert "registered_tool" in _run(registry.get_tools())


def test_refresh_reloads_the_manifest_and_keeps_registered_tools(tmp_path: Path) -> None:
    definition = {"name": "manifest_tool", "description": "v1",
                  "executor": "app.agent.tools.builtin.executors.invoke_worker:execute", "input_schema": {"type": "object"}}
    (tmp_path / "manifest_tool.json").write_text(json.dumps(definition), encoding="utf-8")
    (tmp_path / "manifest.json").write_text(json.dumps({"tools": ["manifest_tool.json"]}), encoding="utf-8")
    builtin = BuiltinToolProvider(manifest_path=tmp_path / "manifest.json")
    remote = _RemoteProvider()
    registry = _registry(tmp_path, remote, local=builtin)
    _run(registry.add_tools([_spec("registered_tool")]))
    before = _run(registry.get_tools())

    (tmp_path / "manifest_tool.json").write_text(json.dumps({**definition, "description": "v2"}), encoding="utf-8")
    _run(registry.refresh_tools(provider="builtin"))
    after = _run(registry.get_tools())

    assert registry.local_provider is builtin
    assert (before["manifest_tool"].description, after["manifest_tool"].description) == ("v1", "v2")
    assert {"registered_tool", "remote_tool"} <= set(after)
    assert remote.refreshed == 0
    _run(registry.refresh_tools())
    assert remote.refreshed == 1
    # A snapshot handed out before the write is not mutated by it.
    assert before["manifest_tool"].description == "v1"


def test_registered_executors_resolve_services_and_preparers(tmp_path: Path) -> None:
    class Store:
        def lookup(self, key: str) -> str:
            return f"value-for-{key}"

    def read(context: ToolContext, args: dict[str, Any]) -> dict[str, Any]:
        return {"value": context.require("store").lookup(args["text"]), "missing": context.get("nothing")}

    def prepare(args: dict[str, Any], memory: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        return {"text": args.get("text") or memory["last_key"]}, ["filled text from memory"]

    registry = _registry(tmp_path)
    _run(registry.add_tools([_spec("read_tool", executor=read, argument_preparer=prepare)],
                            services={"store": Store()}))

    prepared, warnings = _run(registry.prepare_arguments(
        tool_name="read_tool", raw_arguments={}, runtime_context={"last_key": "k1"}))
    result = _run(registry.execute(call_id="c1", tool_name="read_tool",
                                   raw_arguments=prepared, allowed_tools=["read_tool"]))

    assert (prepared, warnings) == ({"text": "k1"}, ["filled text from memory"])
    assert result.output == {"value": "value-for-k1", "missing": None}


def test_registry_writes_to_the_explicit_local_provider(tmp_path: Path) -> None:
    builtin = BuiltinToolProvider()
    explicit = LocalToolProvider(provider_name="host")

    # The catalog is passed in, never inferred from the providers list.
    assert _registry(tmp_path, local=explicit).local_provider is explicit
    assert _registry(tmp_path, local=builtin).local_provider is builtin

    # The manifest is trusted host configuration: it may declare reserved
    # capabilities that runtime registration rejects.
    local = _registry(tmp_path, local=builtin).local_provider.entries()
    assert local["invoke_worker"].origin == "manifest"
    assert "spawn_agent" in local["invoke_worker"].spec.capabilities
    assert "invoke_worker" not in _registry(tmp_path).local_provider.entries()

    # The catalog is aggregated whether or not it was listed in ``providers``.
    listed = _registry(tmp_path, builtin, local=builtin)
    assert sum(provider is builtin for provider in listed.providers()) == 1
    assert "invoke_worker" in _run(listed.get_tools())
    assert list(listed.provider_health()) == ["builtin"]


def test_container_registers_arcade_tools_in_code(tmp_path: Path) -> None:
    from app.core.config import Settings
    from app.core.container import build_container
    from app.domain.arcade.tools import arcade_tool_specs

    data_path = tmp_path / "shops.jsonl"
    data_path.write_text(json.dumps({"source": "fixture", "source_id": 1, "name": "Alpha"}) + "\n", encoding="utf-8")
    (tmp_path / "no-mcp").mkdir()
    container = build_container(Settings(
        data_jsonl_path=data_path, arcade_geo_cache_path=tmp_path / "geo.json",
        mcp_servers_dir=tmp_path / "no-mcp",
        supabase_url="https://fixture.invalid", supabase_service_role_key="test-key",
    ))

    # Plan 5, stage 2b: the agent manifest keeps only the generic tools; arcade
    # tools are runtime-registered by the host together with their services.
    entries = container.tool_registry.local_provider.entries()
    origins = {name: entry.origin for name, entry in entries.items()}
    arcade = [spec.name for spec in arcade_tool_specs()]
    assert origins == {
        "invoke_worker": "manifest", "list_skills": "manifest", "read_skill": "manifest",
        **dict.fromkeys(arcade, "registered"),
    }
    services = container.tool_registry.local_provider.health()["services"]
    assert services == sorted(["skill_registry", "db_query_tool", "geo_resolve_tool", "route_plan_tool",
                               "summary_tool", "mcp_tool_gateway"])
    assert "concludes_turn" in entries["summary_tool"].spec.capabilities
    assert all(not entries[name].spec.reserved_capabilities for name in arcade)
