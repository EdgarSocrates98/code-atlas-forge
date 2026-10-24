"""Único módulo autorizado a invocar subprocess no pacote (audit lf-0005).

Toda chamada externa do Code Atlas passa por aqui: argv sem shell, somente o
binário `git` resolvido via ``shutil.which``. Nenhum outro módulo de ``src/``
pode importar ``subprocess`` — o teste de confinamento reprova.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

GIT_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class GitResult:
    """Resultado de uma invocação `git` (rc -1 = falha de execução/timeout)."""

    returncode: int
    stdout: bytes
    stderr: bytes = b""


def git_binary() -> str | None:
    """Caminho do binário `git` ou None se ausente."""
    return shutil.which("git")


def run_git(args: list[str], cwd: Path | None = None) -> GitResult:
    """Invoca `git` com argv seguro; nunca levanta por timeout/OS error."""
    binary = git_binary()
    if binary is None:
        return GitResult(returncode=-1, stdout=b"", stderr=b"git absent")
    argv = [binary, *args] if cwd is None else [binary, "-C", str(cwd), *args]
    try:
        out = subprocess.run(argv, capture_output=True, timeout=GIT_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return GitResult(returncode=-1, stdout=b"", stderr=str(exc).encode())
    return GitResult(returncode=out.returncode, stdout=out.stdout, stderr=out.stderr)


def git_version() -> str:
    """`git --version` ou string vazia em ausência/falha."""
    out = run_git(["--version"])
    return out.stdout.decode("utf-8", "replace").strip()
