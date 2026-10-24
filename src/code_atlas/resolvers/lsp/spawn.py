"""Único site de subprocess para servidores LSP (lf-0018).

Mesmo padrão de confinamento de ``gitbin.py``: argv sem shell, binário
resolvido via ``shutil.which``, comando NUNCA derivado do conteúdo do
repositório indexado — somente de configuração explícita do usuário
(``config.toml [resolvers.lsp]``). Este arquivo está na whitelist do
teste de confinamento de subprocess.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

# Guardrails de recursos (documentados no review):
MAX_LSP_SERVERS = 4            # processos simultâneos máximos
SPAWN_ENV_PREFIXES = ("PATH=", "HOME=", "LANG=", "LC_", "SYSTEMROOT=",
                      "WINDIR=", "TEMP=", "TMP=")

_active_count = 0


# Tipo do processo spawnado — lifecycle anota via este alias (o audit
# confina `import subprocess` a este arquivo).
LspProcess = subprocess.Popen[bytes]


class SpawnError(RuntimeError):
    """Binário ausente, limite atingido ou falha de spawn."""


@dataclass(frozen=True)
class LspServerSpec:
    """Configuração explícita de um servidor LSP (opt-in do usuário)."""

    name: str
    cmd: tuple[str, ...]            # argv completo — nunca do repo
    languages: frozenset[str]       # linguagens cobertas ("" = qualquer)
    root_uri: str = "file:///"      # rootUri do initialize
    enabled: bool = False           # opt-in: desligado por padrão
    request_timeout_s: float = 10.0


def _leak_env() -> dict[str, str]:
    """Ambiente minimizado — só variáveis necessárias para stdio."""
    import os

    env: dict[str, str] = {}
    for k, v in os.environ.items():
        if any((k.startswith(p.rstrip("=")) and k == p.rstrip("=")) or
               k.startswith(p) for p in SPAWN_ENV_PREFIXES):
            env[k] = v
    return env


def spawn_server(spec: LspServerSpec) -> subprocess.Popen[bytes]:
    """Spawna o servidor stdio; nunca levanta por binário ausente —
    devolve erro tipado via ``SpawnError``."""
    global _active_count
    if not spec.cmd:
        raise SpawnError(f"lsp {spec.name}: empty command")
    binary = shutil.which(spec.cmd[0])
    if binary is None:
        raise SpawnError(f"lsp {spec.name}: binary {spec.cmd[0]!r} not found")
    if _active_count >= MAX_LSP_SERVERS:
        raise SpawnError(
            f"lsp: server limit {MAX_LSP_SERVERS} reached")
    try:
        proc = subprocess.Popen(
            [binary, *spec.cmd[1:]],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=_leak_env(),
        )
    except OSError as exc:
        raise SpawnError(f"lsp {spec.name}: spawn failed: {exc}") from exc
    _active_count += 1
    return proc


def release_server(proc: subprocess.Popen[bytes]) -> None:
    """Encerra e reaps o processo (sem zombies)."""
    global _active_count
    try:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass
    _active_count = max(0, _active_count - 1)
