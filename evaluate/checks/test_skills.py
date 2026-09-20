"""Offline Agent Skills format, confinement, refresh and execution contracts."""

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest
import yaml

from app.agent.context.context_builder import ContextBuilder
from app.agent.runtime.session_state import AgentSessionState, state_from_dict, state_to_dict
from app.agent.skills.config import SkillConfig, load_skill_config
from app.agent.skills.execution import SkillExecution, bind_skill_execution, current_skill_invocation
from app.agent.skills.registry import SkillError, SkillRegistry, parse_skill
from app.agent.subagents.subagent_builder import SubAgentBuilder
from app.agent.tools.builtin import BuiltinToolProvider
from app.agent.tools.permission import ToolPermissionChecker
from app.agent.tools.registry import ToolRegistry
from app.protocol.messages import ChatRequest


def write_skill(root, directory="sample-skill", *, body="BODY_MARKER", **fields):
    folder = root / directory
    folder.mkdir(parents=True, exist_ok=True)
    metadata = {"name": directory, "description": "Use for synthetic skill tests.", **fields}
    (folder / "SKILL.md").write_text("---\n" + yaml.safe_dump(metadata) + "---\n" + body, encoding="utf-8")
    return folder


def tool_registry(registry, tmp_path):
    return ToolRegistry(
        providers=[BuiltinToolProvider(runtime_services={"skill_registry": registry})],
        permission_checker=ToolPermissionChecker(policy_file=tmp_path / "missing.yaml"),
    )


def test_bundled_skills_use_standard_format():
    config = load_skill_config(Path("backend/skill.config.py"))
    registry = SkillRegistry(config)
    catalog = registry.list_skills(agent_name="main_agent")
    assert {item["name"] for item in catalog} == {
        "search-result-reading", "navigation-result-reading", "response-composition",
    }
    for entry in catalog:
        assert registry.read_skill(entry["name"], agent_name="main_agent").content


def test_standard_optional_fields_unicode_names_and_arbitrary_markdown(tmp_path):
    folder = write_skill(tmp_path, "技能-café", body="", license="MIT", compatibility="Python 3.11+",
                         metadata={"author": "fixture", "version": "1.0"}, **{"allowed-tools": "Read Bash(git:*)"})
    metadata, body = parse_skill((folder / "SKILL.md").read_text(), folder.name)
    assert metadata["metadata"]["version"] == "1.0"
    assert metadata["allowed-tools"] == "Read Bash(git:*)"
    assert body == ""  # The standard imposes no required body structure.
    assert SkillRegistry(SkillConfig(roots=[tmp_path])).list_skills(agent_name="any")[0]["name"] == folder.name


@pytest.mark.parametrize("fields", [
    {"name": "Uppercase"}, {"name": "wrong-directory"}, {"name": "bad_name"},
    {"name": "-bad"}, {"name": "bad-"}, {"name": "bad--name"}, {"name": "a" * 65},
    {"description": ""}, {"description": "x" * 1025}, {"description": 42},
    {"compatibility": "x" * 501}, {"compatibility": 12}, {"metadata": {"version": 1}},
    {"metadata": []}, {"license": []}, {"allowed-tools": []}, {"unknown": "extension"},
])
def test_invalid_metadata_is_isolated(fields, tmp_path, caplog):
    write_skill(tmp_path, **fields)
    write_skill(tmp_path, "valid-skill")
    catalog = SkillRegistry(SkillConfig(roots=[tmp_path])).list_skills(agent_name="main_agent")
    assert [entry["name"] for entry in catalog] == ["valid-skill"]
    assert "skill_skipped" in caplog.text
    assert str(tmp_path) not in caplog.text and "BODY_MARKER" not in caplog.text


@pytest.mark.parametrize("content", [
    "plain markdown", "---\nname: sample-skill", "---\n[]\n---\nbody",
    "---\nname: [broken\n---\nbody", "---\nname: sample-skill\nname: sample-skill\n---\nbody",
    "---\nname: sample-skill\n---\nbody",
])
def test_malformed_frontmatter_is_rejected(content):
    with pytest.raises(SkillError):
        parse_skill(content, "sample-skill")


def test_refresh_add_edit_delete_collisions_and_filters(tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir(); second.mkdir()
    registry = SkillRegistry(SkillConfig(
        roots=[first, second], disabled_skills=["disabled"],
        agent_skills={"search_worker": ["sample-skill"], "navigation_worker": []},
    ))
    assert registry.list_skills(agent_name="main_agent") == []
    folder = write_skill(first)
    write_skill(first, "disabled")
    write_skill(first, "another")
    registry.refresh()
    assert len(registry.list_skills(agent_name="main_agent")) == 2
    assert len(registry.list_skills(agent_name="search_worker")) == 1
    assert registry.list_skills(agent_name="navigation_worker") == []
    for name, agent in [("disabled", "main_agent"), ("another", "search_worker"), ("sample-skill", "navigation_worker")]:
        with pytest.raises(SkillError, match="skill_unavailable"):
            registry.read_skill(name, agent_name=agent)
    write_skill(first, description="Updated description", body="updated body")
    registry.refresh()
    assert registry.list_skills(agent_name="search_worker")[0]["description"] == "Updated description"
    assert registry.read_skill("sample-skill", agent_name="main_agent").content == "updated body"
    duplicate = write_skill(second)
    registry.refresh()
    assert registry.list_skills(agent_name="search_worker") == []
    shutil.rmtree(duplicate)
    registry.refresh()
    assert len(registry.list_skills(agent_name="search_worker")) == 1
    shutil.rmtree(folder)
    registry.refresh()
    assert registry.list_skills(agent_name="search_worker") == []


def test_confined_text_resources_and_limits(tmp_path):
    root = tmp_path / "skills"
    folder = write_skill(root)
    registry = SkillRegistry(SkillConfig(roots=[root], max_file_bytes=512))
    outside = tmp_path / "outside.txt"
    outside.write_text("OUTSIDE_SECRET")
    (folder / "escape").symlink_to(outside)
    (folder / "references").mkdir()
    (folder / "references" / "guide.txt").write_text("reference")
    (folder / "alias.txt").symlink_to(folder / "references" / "guide.txt")
    (folder / "binary").write_bytes(b"\x00binary")
    (folder / "encoding").write_bytes(b"\xff")
    (folder / "huge").write_text("x" * 513)
    os.mkfifo(folder / "pipe")
    for path in ["../outside.txt", str(outside), "escape", "binary", "encoding", "huge", "pipe", "references", "C:\\outside", "", "."]:
        with pytest.raises(SkillError) as error:
            registry.read_skill("sample-skill", path, agent_name="main_agent")
        assert "OUTSIDE_SECRET" not in str(error.value) and str(tmp_path) not in str(error.value)
    assert registry.read_skill("sample-skill", "alias.txt", agent_name="main_agent").content == "reference"
    (folder / "scripts").mkdir()
    (folder / "scripts" / "example.py").write_text("raise RuntimeError('must not execute')")
    assert "must not execute" in registry.read_skill("sample-skill", "scripts/example.py", agent_name="main_agent").content
    external = write_skill(tmp_path / "external", "external-skill")
    (root / "external-skill").symlink_to(external, target_is_directory=True)
    registry.refresh()
    assert [item["name"] for item in registry.list_skills(agent_name="main_agent")] == ["sample-skill"]


def test_snapshot_deduplication_capacity_and_transient_serialization(tmp_path):
    folder = write_skill(tmp_path)
    size = (folder / "SKILL.md").stat().st_size
    (folder / "reference.txt").write_text("reference")
    registry = SkillRegistry(SkillConfig(roots=[tmp_path], max_loaded_bytes=size))
    state = AgentSessionState(session_id="synthetic")
    execution = state.skill_execution
    receipt = execution.load(registry, agent_name="main_agent", name="sample-skill", path="SKILL.md")
    assert receipt == {"name": "sample-skill", "path": "SKILL.md", "status": "loaded"}
    write_skill(tmp_path, body="CHANGED")
    registry.refresh()
    execution.load(registry, agent_name="main_agent", name="sample-skill", path="./SKILL.md")
    assert execution.loaded_bytes == size
    assert execution.resources[("sample-skill", "SKILL.md")].content == "BODY_MARKER"
    with pytest.raises(SkillError, match="capacity"):
        execution.load(registry, agent_name="main_agent", name="sample-skill", path="reference.txt")
    assert len(execution.resources) == 1
    serialized = state_to_dict(state)
    assert "BODY_MARKER" not in json.dumps(serialized)
    assert not state_from_dict(serialized).skill_execution.resources
    assert SkillExecution().load(registry, agent_name="main_agent", name="sample-skill", path="SKILL.md") == receipt


def test_tools_refresh_context_and_bind_identity_without_body_in_history(tmp_path):
    root = tmp_path / "skills"
    root.mkdir()
    registry = SkillRegistry(SkillConfig(roots=[root], agent_skills={"search_worker": []}))
    tools = tool_registry(registry, tmp_path)
    state = AgentSessionState(session_id="synthetic")
    builder = ContextBuilder(prompt_root=tmp_path / "prompts", history_turn_limit=4, skill_registry=registry)
    profile = SubAgentBuilder().get("main_agent")
    write_skill(root)

    async def invoke(name, arguments, agent="main_agent"):
        with bind_skill_execution(agent, state.skill_execution):
            return await tools.execute(call_id="fixture", tool_name=name, raw_arguments=arguments,
                                       allowed_tools=["list_skills", "read_skill"])

    assert registry.list_skills(agent_name="main_agent") == []
    result = asyncio.run(invoke("list_skills", {}))
    assert result.status == "completed" and len(result.output["skills"]) == 1
    before = builder.build(session_state=state, request=ChatRequest(message="fixture"), subagent=profile)
    assert '"name": "sample-skill"' in before.instructions and "BODY_MARKER" not in before.instructions
    result = asyncio.run(invoke("read_skill", {"name": "sample-skill"}))
    assert result.status == "completed" and "BODY_MARKER" not in json.dumps(result.output)
    after = builder.build(session_state=state, request=ChatRequest(message="fixture"), subagent=profile)
    assert after.instructions.count("BODY_MARKER") == 1
    assert "BODY_MARKER" not in json.dumps(after.messages)
    assert asyncio.run(invoke("read_skill", {"name": "sample-skill"}, "search_worker")).status == "failed"
    spoofed = asyncio.run(invoke("read_skill", {"name": "sample-skill", "agent_name": "main_agent"}, "search_worker"))
    assert spoofed.status == "failed"
    with pytest.raises(SkillError, match="context_required"):
        current_skill_invocation()


def test_concurrent_tool_invocations_do_not_share_identity_or_loaded_state(tmp_path):
    write_skill(tmp_path)
    registry = SkillRegistry(SkillConfig(roots=[tmp_path], agent_skills={"search_worker": []}))
    tools = tool_registry(registry, tmp_path)
    main, worker = SkillExecution(), SkillExecution()

    async def run(agent, execution):
        with bind_skill_execution(agent, execution):
            return await tools.execute(call_id=agent, tool_name="read_skill",
                                       raw_arguments={"name": "sample-skill"}, allowed_tools=["read_skill"])

    async def together():
        return await asyncio.gather(run("main_agent", main), run("search_worker", worker))

    results = asyncio.run(together())
    assert [result.status for result in results] == ["completed", "failed"]
    assert main.resources and not worker.resources


def test_config_paths_are_relative_to_config_and_legacy_yaml_is_rejected(tmp_path, monkeypatch):
    config_path = tmp_path / "skill.config.py"
    config_path.write_text('from app.agent.skills.config import SkillConfig\nconfig = SkillConfig(roots=["skills"])\n')
    monkeypatch.chdir(tmp_path.parent)
    assert load_skill_config(config_path).roots == [(tmp_path / "skills").resolve()]
    with pytest.raises(ValueError):
        SkillConfig(max_file_bytes=0)
    with pytest.raises(ValueError):
        SkillConfig(max_loaded_bytes=True)
    (tmp_path / "query.yaml").write_text("id: query\nskill_files: []\n")
    with pytest.raises(ValueError, match="skill.config.py"):
        SubAgentBuilder(definitions_dir=tmp_path)
