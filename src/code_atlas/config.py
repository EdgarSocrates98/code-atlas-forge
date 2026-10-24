"""Resolução de configuração do Code Atlas.

Ordem de precedência (Blueprint ADR-013):

1. Argumento explícito (``--root``).
2. Variáveis de ambiente ``CODE_ATLAS_*``.
3. ``XDG_CONFIG_HOME``/``XDG_DATA_HOME`` (Linux) ou ``%APPDATA%``/``%LOCALAPPDATA%`` (Windows).
4. Defaults por plataforma.

Nada é escrito dentro do repositório indexado por padrão.
"""

from __future__ import annotations

import os
import sys
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ENV_PREFIX = "CODE_ATLAS_"
ENV_ROOT = "CODE_ATLAS_ROOT"
ENV_CONFIG_DIR = "CODE_ATLAS_CONFIG_DIR"
ENV_DATA_DIR = "CODE_ATLAS_DATA_DIR"

APP_DIRNAME = "code-atlas"
CONFIG_FILENAME = "config.toml"
CONFIG_SCHEMA_VERSION = 1

_Env = Mapping[str, str]


def _is_windows() -> bool:
    return sys.platform == "win32"


def config_dir(env: _Env | None = None) -> Path:
    """Diretório de configuração (arquivo ``config.toml``)."""
    e = os.environ if env is None else env
    if override := e.get(ENV_CONFIG_DIR):
        return Path(override).expanduser()
    if _is_windows():
        base = e.get("APPDATA") or e.get("LOCALAPPDATA")
        if base:
            return Path(base) / APP_DIRNAME
        return Path.home() / "AppData" / "Roaming" / APP_DIRNAME
    base = e.get("XDG_CONFIG_HOME")
    return (Path(base) if base else Path.home() / ".config") / APP_DIRNAME


def data_dir(env: _Env | None = None) -> Path:
    """Diretório de dados (bancos SQLite, manifests)."""
    e = os.environ if env is None else env
    if override := e.get(ENV_DATA_DIR):
        return Path(override).expanduser()
    if _is_windows():
        base = e.get("LOCALAPPDATA") or e.get("APPDATA")
        if base:
            return Path(base) / APP_DIRNAME
        return Path.home() / "AppData" / "Local" / APP_DIRNAME
    base = e.get("XDG_DATA_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / APP_DIRNAME


def config_file(env: _Env | None = None) -> Path:
    """Caminho completo do ``config.toml``."""
    return config_dir(env) / CONFIG_FILENAME


def resolve_root(arg: str | None = None, env: _Env | None = None) -> Path:
    """Resolve a raiz do workspace: argumento > ``CODE_ATLAS_ROOT`` > cwd."""
    e = os.environ if env is None else env
    candidate = arg or e.get(ENV_ROOT)
    if candidate:
        return Path(candidate).expanduser().resolve()
    return Path.cwd().resolve()


@dataclass(frozen=True)
class WorkspaceConfig:
    """Configuração persistida do workspace (``config.toml``)."""

    root: Path
    schema_version: int = CONFIG_SCHEMA_VERSION
    # Tabela [resolvers] crua — parsing tipado em resolvers/bootstrap
    # (lf-0027); ausente = sem resolvers opt-in.
    resolvers: Mapping[str, object] | None = None
    # [indexing] retain_snapshots — máximo de snapshots STALE mantidos
    # por repositório (lf-0031). 0/ausente = retenção ilimitada (default:
    # histórico completo preservado para auditoria).
    retain_snapshots: int = 0

    def to_toml(self) -> str:
        return (
            "# Code Atlas workspace config\n"
            f"schema_version = {self.schema_version}\n"
            "\n"
            "[workspace]\n"
            f'root = "{self.root.as_posix()}"\n'
        )

    @staticmethod
    def load(path: Path) -> WorkspaceConfig:
        """Carrega o ``config.toml``. Levanta ``ConfigError`` se inválido."""
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"cannot read config {path}: {exc}") from exc
        try:
            root = Path(raw["workspace"]["root"])
        except (KeyError, TypeError) as exc:
            raise ConfigError(f"config {path} missing [workspace].root") from exc
        res = raw.get("resolvers")
        if res is not None and not isinstance(res, dict):
            raise ConfigError(f"config {path}: [resolvers] must be a table")
        idx = raw.get("indexing", {})
        if not isinstance(idx, dict):
            raise ConfigError(f"config {path}: [indexing] must be a table")
        retain = idx.get("retain_snapshots", 0)
        if not isinstance(retain, int) or isinstance(retain, bool) or retain < 0:
            raise ConfigError(
                f"config {path}: [indexing].retain_snapshots must be an "
                "integer >= 0"
            )
        return WorkspaceConfig(
            root=root,
            schema_version=int(raw.get("schema_version", 0)),
            resolvers=res,
            retain_snapshots=retain,
        )


class ConfigError(Exception):
    """Erro de configuração do workspace."""


def write_config(root: Path, env: _Env | None = None, *, force: bool = False) -> Path:
    """Escreve ``config.toml`` e garante o diretório de dados.

    Levanta ``ConfigError`` se o arquivo já existir e ``force`` for falso.
    """
    cfg_path = config_file(env)
    if cfg_path.exists() and not force:
        raise ConfigError(f"config already exists at {cfg_path} (use --force to overwrite)")
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(WorkspaceConfig(root=root).to_toml(), encoding="utf-8")
    data_dir(env).mkdir(parents=True, exist_ok=True)
    return cfg_path
