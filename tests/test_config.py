"""Testes da resolução de configuração (XDG/APPDATA/env/--root)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from code_atlas import config
from code_atlas.config import ConfigError, WorkspaceConfig


def test_env_override_config_dir() -> None:
    env = {"CODE_ATLAS_CONFIG_DIR": "/tmp/atlas-cfg"}
    assert config.config_dir(env) == Path("/tmp/atlas-cfg")


def test_env_override_data_dir() -> None:
    env = {"CODE_ATLAS_DATA_DIR": "/tmp/atlas-data"}
    assert config.data_dir(env) == Path("/tmp/atlas-data")


def test_xdg_config_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    env = {"XDG_CONFIG_HOME": "/xdg"}
    assert config.config_dir(env) == Path("/xdg/code-atlas")


def test_xdg_data_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    env = {"XDG_DATA_HOME": "/xdg-data"}
    assert config.data_dir(env) == Path("/xdg-data/code-atlas")


def test_windows_appdata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    env = {"APPDATA": "C:/Users/e/AppData/Roaming", "LOCALAPPDATA": "C:/Users/e/AppData/Local"}
    assert config.config_dir(env) == Path("C:/Users/e/AppData/Roaming/code-atlas")
    assert config.data_dir(env) == Path("C:/Users/e/AppData/Local/code-atlas")


def test_env_beats_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    env = {"XDG_CONFIG_HOME": "/xdg", "CODE_ATLAS_CONFIG_DIR": "/custom"}
    assert config.config_dir(env) == Path("/custom")


def test_resolve_root_arg_beats_env(tmp_path: Path) -> None:
    env_root = tmp_path / "env-root"
    env_root.mkdir()
    env = {"CODE_ATLAS_ROOT": str(env_root)}
    resolved = config.resolve_root(str(tmp_path / "arg-root"), env)
    assert resolved == (tmp_path / "arg-root").resolve()


def test_resolve_root_env_beats_cwd(tmp_path: Path) -> None:
    env_root = tmp_path / "env-root"
    env_root.mkdir()
    env = {"CODE_ATLAS_ROOT": str(env_root)}
    assert config.resolve_root(None, env) == env_root.resolve()


def test_resolve_root_default_cwd() -> None:
    assert config.resolve_root(None, {}) == Path.cwd().resolve()


def test_write_and_load_config(tmp_path: Path) -> None:
    env = {"CODE_ATLAS_CONFIG_DIR": str(tmp_path / "c"), "CODE_ATLAS_DATA_DIR": str(tmp_path / "d")}
    root = tmp_path / "ws"
    root.mkdir()
    path = config.write_config(root, env)
    loaded = WorkspaceConfig.load(path)
    assert loaded.root == root
    assert loaded.schema_version == config.CONFIG_SCHEMA_VERSION


def test_write_config_no_clobber(tmp_path: Path) -> None:
    env = {"CODE_ATLAS_CONFIG_DIR": str(tmp_path / "c"), "CODE_ATLAS_DATA_DIR": str(tmp_path / "d")}
    config.write_config(tmp_path, env)
    with pytest.raises(ConfigError):
        config.write_config(tmp_path, env)
    config.write_config(tmp_path, env, force=True)  # force ok


def test_load_config_invalid(tmp_path: Path) -> None:
    bad = tmp_path / "config.toml"
    bad.write_text("not toml [[[", encoding="utf-8")
    with pytest.raises(ConfigError):
        WorkspaceConfig.load(bad)


def test_load_config_missing_root(tmp_path: Path) -> None:
    bad = tmp_path / "config.toml"
    bad.write_text("schema_version = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        WorkspaceConfig.load(bad)
