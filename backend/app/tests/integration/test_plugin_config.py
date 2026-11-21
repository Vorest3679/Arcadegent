"""``plugins.yaml`` loading and redacted configuration errors (plan 5, stage 2c)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr

from app.agent.plugins import (
    EmptyConfig,
    PluginConfig,
    PluginConfigError,
    RelPath,
    read_plugins_file,
    resolve_plugin_configs,
)


class _Nested(PluginConfig):
    path: RelPath | None = None
    token: SecretStr = SecretStr("")


class _Config(PluginConfig):
    text: str = "default"
    count: float = 1.0
    enabled: bool = False
    optional: RelPath | None = None
    nested: _Nested = _Nested()
    items: list[_Nested] = []
    api_key: SecretStr = SecretStr("")


MODELS: dict[str, type[PluginConfig]] = {"core": EmptyConfig, "fixture": _Config}


def _load_file(path: Path, *, models: dict[str, type[PluginConfig]], environ: dict[str, str]):
    return resolve_plugin_configs(read_plugins_file(path), base_dir=path.parent, models=models, environ=environ)


def _resolve(config: dict, *, base_dir: Path, environ: dict[str, str] | None = None) -> _Config:
    configs = resolve_plugin_configs(
        {"plugins": {"fixture": {"config": config}}}, base_dir=base_dir, models=MODELS, environ=environ or {})
    result = configs["fixture"]
    assert isinstance(result, _Config)
    return result


def test_placeholders_expand_convert_types_and_resolve_paths(tmp_path: Path) -> None:
    config = _resolve(
        {
            "text": "${GREETING} world ${SUFFIX:-!}",
            "count": "${COUNT:-}",
            "enabled": "${FLAG:-}",
            "optional": "${OPTIONAL_PATH:-}",
            "nested": {"path": "nested/dir"},
        },
        base_dir=tmp_path,
        environ={"GREETING": "hello", "COUNT": "8", "FLAG": "true", "OPTIONAL_PATH": ""},
    )

    assert config.text == "hello world !"
    assert config.count == 8.0
    assert config.enabled is True
    assert config.optional is None
    assert config.nested.path == (tmp_path / "nested/dir").resolve()


def test_empty_placeholders_strings_and_defaults(tmp_path: Path) -> None:
    # A whole ``${VAR:-}`` that is unset or empty falls back to the model default.
    assert _resolve({"optional": "${OPTIONAL_PATH:-}", "count": "${COUNT:-}"}, base_dir=tmp_path) == _Config()
    # A YAML empty string is kept as a value.
    assert _resolve({"text": ""}, base_dir=tmp_path).text == ""
    # An embedded empty placeholder only empties its own span.
    assert _resolve({"text": "pre-${MISSING:-}-post"}, base_dir=tmp_path).text == "pre--post"
    # ``${VAR:-default}`` applies the default when the variable is empty.
    assert _resolve({"text": "${X:-fallback}"}, base_dir=tmp_path, environ={"X": ""}).text == "fallback"
    # An environment value may itself contain ``${``.
    assert _resolve({"text": "${X}"}, base_dir=tmp_path, environ={"X": "a${b"}).text == "a${b"


def test_required_and_malformed_placeholders_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(PluginConfigError, match=r"^plugin:fixture:missing_env:MISSING$"):
        _resolve({"text": "${MISSING}"}, base_dir=tmp_path)
    with pytest.raises(PluginConfigError, match=r"^plugin:fixture:invalid_placeholder$"):
        _resolve({"text": "${not valid}"}, base_dir=tmp_path)


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ({"plugins": {"nope": None}}, r"^plugin:nope:unknown_plugin$"),
        ({"plugins": {"fixture": {"bogus": 1}}}, r"^plugin:fixture:unknown_key:bogus$"),
        ({"plugins": {"fixture": {"config": {"bogus": 1}}}}, r"^plugin:fixture:invalid_config:bogus:extra_forbidden$"),
        ({"plugins": {"fixture": {"config": {"nested": {"bogus": 1}}}}},
         r"^plugin:fixture:invalid_config:nested.bogus:extra_forbidden$"),
        ({"plugins": {"fixture": {"disabled": "yes"}}}, r"^plugin:fixture:invalid_disabled$"),
        ({"plugins": {"fixture": {"config": []}}}, r"^plugin:fixture:invalid_config:config:not_a_mapping$"),
        ({"plugins": {"fixture": []}}, r"^plugin:fixture:invalid_entry$"),
        ({"plugins": []}, r"^plugin_config:invalid_root$"),
        ({"plugins": {}, "extra": []}, r"^plugin_config:invalid_root$"),
    ],
)
def test_structural_errors_are_labeled(tmp_path: Path, raw: dict, code: str) -> None:
    with pytest.raises(PluginConfigError, match=code):
        resolve_plugin_configs(raw, base_dir=tmp_path, models=MODELS, environ={})


def test_entries_keep_order_and_disabled_entries_are_skipped(tmp_path: Path) -> None:
    configs = resolve_plugin_configs(
        {"plugins": {"fixture": {"config": {"text": "x"}}, "core": None}},
        base_dir=tmp_path, models=MODELS, environ={})
    assert list(configs) == ["fixture", "core"]
    assert resolve_plugin_configs(
        {"plugins": {"core": None, "fixture": {"disabled": True, "config": {"bogus": 1}}}},
        base_dir=tmp_path, models=MODELS, environ={},
    ).keys() == {"core"}


@pytest.mark.parametrize(
    "config",
    [
        {"api_key": "{{SECRET_ah3n3bdt}}"},
        {"api_key": "${UNSET_VAR:-{{SECRET_ah3n3bdt}}}"},  # a literal default would land in the YAML
        {"api_key": "prefix-${API_KEY}"},
        {"nested": {"token": "{{SECRET_ah3n3bdt}}"}},
        {"items": [{"token": "{{SECRET_ah3n3bdt}}"}]},
    ],
)
def test_secret_plaintext_is_rejected_without_echoing_the_value(tmp_path: Path, config: dict) -> None:
    with pytest.raises(PluginConfigError, match=r"secret_requires_placeholder$") as error:
        _resolve(config, base_dir=tmp_path, environ={"API_KEY": "from-env"})

    assert "plain-secret-value" not in str(error.value)


def test_secret_placeholders_are_accepted(tmp_path: Path) -> None:
    environ = {"API_KEY": "secret-value", "TOKEN": "token-value"}
    config = _resolve(
        {"api_key": "${API_KEY}", "items": [{"token": "${TOKEN:-}"}], "nested": {"token": "${UNSET:-}"}},
        base_dir=tmp_path, environ=environ)

    assert config.api_key.get_secret_value() == "secret-value"
    assert config.items[0].token.get_secret_value() == "token-value"
    assert config.nested.token.get_secret_value() == ""


def test_validation_error_contains_path_and_type_but_no_value(tmp_path: Path) -> None:
    with pytest.raises(PluginConfigError, match=r"^plugin:fixture:invalid_config:count:float_parsing$") as error:
        _resolve({"count": "${COUNT:-not-a-number-secret}"}, base_dir=tmp_path)

    assert "not-a-number-secret" not in str(error.value)


def test_file_loading_uses_the_file_directory_for_relative_paths(tmp_path: Path) -> None:
    path = tmp_path / "plugins.yaml"
    path.write_text(
        "plugins:\n"
        "  fixture:\n"
        "    config:\n"
        "      nested:\n"
        "        path: relative/nested\n",
        encoding="utf-8",
    )
    config = _load_file(path, models=MODELS, environ={})["fixture"]

    assert isinstance(config, _Config)
    assert config.nested.path == (tmp_path / "relative/nested").resolve()


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("plugins: {}\nplugins: {}\n", "plugin_config:duplicate_key"),
        ("plugins: {}\n1: x\n", "plugin_config:non_string_key"),
        ("plugins: [\n", "plugin_config:invalid_yaml"),
    ],
)
def test_invalid_yaml_files_are_rejected(tmp_path: Path, text: str, code: str) -> None:
    path = tmp_path / "plugins.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(PluginConfigError, match=rf"^{code}$"):
        _load_file(path, models=MODELS, environ={})


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(PluginConfigError, match=r"^plugin_config:missing_file$"):
        _load_file(tmp_path / "missing.yaml", models=MODELS, environ={})


def test_repository_plugins_yaml_matches_the_settings_defaults() -> None:
    from app.agent.plugins import CorePlugin
    from app.core.config import Settings
    from app.domain.arcade.plugin import ArcadeConfig, ArcadePlugin

    settings = Settings()
    models = {"core": CorePlugin.config_model, "arcade": ArcadePlugin.config_model}
    configs = _load_file(settings.agent_plugins_file, models=models, environ={})
    assert list(configs) == ["core", "arcade"]
    amap = configs["arcade"]
    assert isinstance(amap, ArcadeConfig)
    assert (amap.amap.base_url, amap.amap.timeout_seconds) == (settings.amap_base_url, settings.amap_timeout_seconds)
    assert amap.amap.api_key.get_secret_value() == ""


def test_agent_plugins_file_resolves_relative_paths_like_other_agent_files(monkeypatch, tmp_path: Path) -> None:
    from app.core.config import Settings

    expected = Settings().agent_plugins_file
    monkeypatch.setenv("AGENT_PLUGINS_FILE", "backend/plugins.yaml")
    # From the repository root and from ``backend/`` the same file is found.
    monkeypatch.chdir(expected.parents[1])
    assert Settings.from_env().agent_plugins_file.resolve() == expected
    monkeypatch.chdir(expected.parent)
    assert Settings.from_env().agent_plugins_file.resolve() == expected
