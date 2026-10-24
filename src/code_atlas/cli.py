"""Interface de linha de comando do Code Atlas.

Cycle 1: ``--version``, ``doctor``, ``init``. Comandos ``status``/``index``
chegam no vertical slice (lf-0004). Códigos de saída consistentes:

- 0: sucesso
- 1: erro de execução
- 2: erro de uso (argparse)
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from code_atlas import __version__
from code_atlas.config import (
    ConfigError,
    config_file,
    data_dir,
    resolve_root,
    write_config,
)

EXIT_OK = 0
EXIT_ERROR = 1


@dataclass(frozen=True)
class Check:
    """Uma verificação do ``doctor``."""

    name: str
    value: str
    ok: bool
    detail: str = ""


def _probe_git() -> Check:
    git = shutil.which("git")
    if git is None:
        return Check("git", "absent", True, "git binary not found; degraded discovery mode")
    try:
        out = subprocess.run(
            [git, "--version"], capture_output=True, text=True, timeout=10, check=False
        )
        return Check("git", out.stdout.strip() or "unknown", True)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Check("git", "error", True, f"probe failed: {exc}")


def _probe_fts5() -> bool:
    try:
        con = sqlite3.connect(":memory:")
        try:
            con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        finally:
            con.close()
        return True
    except sqlite3.Error:
        return False


def _first_writable_ancestor(path: Path) -> tuple[Path, bool]:
    """Primeiro ancestral existente de ``path`` e se é gravável."""
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current, current.exists() and os.access(current, os.W_OK)


def run_doctor(args: argparse.Namespace) -> int:
    """Executa verificações reais de ambiente. Sai 0 se não houver falhas."""
    checks: list[Check] = []

    py_ok = sys.version_info >= (3, 12)
    checks.append(
        Check(
            "python",
            sys.version.split()[0],
            py_ok,
            "" if py_ok else "requires >= 3.12",
        )
    )
    checks.append(Check("sqlite", sqlite3.sqlite_version, True))
    checks.append(Check("fts5", "available" if _probe_fts5() else "unavailable", _probe_fts5()))
    checks.append(_probe_git())

    cfg = config_file()
    checks.append(
        Check(
            "config",
            str(cfg),
            True,
            "exists" if cfg.exists() else "missing (run `code-atlas init`)",
        )
    )
    dd = data_dir()
    ancestor, writable = _first_writable_ancestor(dd)
    checks.append(
        Check(
            "data_dir",
            str(dd),
            writable,
            f"writable via {ancestor}" if writable else f"not writable (checked {ancestor})",
        )
    )

    failed = [c for c in checks if not c.ok]
    if args.json:
        payload = {
            "tool": "code-atlas",
            "version": __version__,
            "healthy": not failed,
            "checks": [c.__dict__ for c in checks],
        }
        print(json.dumps(payload, indent=2))
    else:
        width = max(len(c.name) for c in checks)
        for c in checks:
            mark = "ok" if c.ok else "FAIL"
            suffix = f"  {c.detail}" if c.detail else ""
            print(f"{c.name:<{width}}  {mark:<4}  {c.value}{suffix}")
        if failed:
            print(f"\n{len(failed)} check(s) failed", file=sys.stderr)
    return EXIT_ERROR if failed else EXIT_OK


def run_init(args: argparse.Namespace) -> int:
    """Cria a configuração do workspace sem sobrescrever config existente."""
    root = resolve_root(args.root)
    if not root.is_dir():
        print(f"error: root {root} is not a directory", file=sys.stderr)
        return EXIT_ERROR
    try:
        cfg = write_config(root, force=args.force)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if args.json:
        print(json.dumps({"config": str(cfg), "root": str(root), "data_dir": str(data_dir())}))
    else:
        print(f"config:  {cfg}")
        print(f"root:    {root}")
        print(f"data:    {data_dir()}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="code-atlas",
        description="Code Atlas — inteligência estrutural de código local-first (Cycle 1).",
    )
    parser.add_argument("--version", action="version", version=f"code-atlas {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_doctor = sub.add_parser("doctor", help="verifica ambiente, config e dependências")
    p_doctor.add_argument("--json", action="store_true", help="saída em JSON")
    p_doctor.set_defaults(func=run_doctor)

    p_init = sub.add_parser("init", help="inicializa a configuração do workspace")
    p_init.add_argument("--root", metavar="PATH", help="raiz do workspace (default: cwd)")
    p_init.add_argument("--force", action="store_true", help="sobrescreve config existente")
    p_init.add_argument("--json", action="store_true", help="saída em JSON")
    p_init.set_defaults(func=run_init)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))
