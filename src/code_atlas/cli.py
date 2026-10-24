"""Interface de linha de comando do Code Atlas.

Cycle 1: ``--version``, ``doctor``, ``init``, ``index``, ``status``.
Códigos de saída consistentes:

- 0: sucesso
- 1: erro de execução (inclui índice falho)
- 2: erro de uso (argparse)
- 3: bloqueado/unsafe (path inválido, config ausente para ação exigida)

``--json`` emite um único objeto JSON em stdout; erros vão para stderr.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from code_atlas import __version__, gitbin
from code_atlas.config import (
    ConfigError,
    config_file,
    data_dir,
    resolve_root,
    write_config,
)
from code_atlas.discovery.scanner import probe_git
from code_atlas.indexing import IndexCoordinator, IndexOutcome
from code_atlas.storage import connect, migrate, schema_version
from code_atlas.storage.store import Store

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_UNSAFE = 3

DB_FILENAME = "atlas.db"


def db_path() -> Path:
    """Banco do workspace (fora do repositório indexado)."""
    return data_dir() / DB_FILENAME


def _open_db() -> sqlite3.Connection:
    conn = connect(db_path())
    migrate(conn)
    return conn


@dataclass(frozen=True)
class Check:
    """Uma verificação do ``doctor``."""

    name: str
    value: str
    ok: bool
    detail: str = ""


def _probe_git() -> Check:
    if gitbin.git_binary() is None:
        return Check("git", "absent", True, "git binary not found; degraded discovery mode")
    version = gitbin.git_version()
    if not version:
        return Check("git", "error", True, "git --version failed")
    return Check("git", version, True)


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
    """Cria config, data dir e registra o repositório no índice local."""
    root = resolve_root(args.root)
    if not root.is_dir():
        print(f"error: root {root} is not a directory", file=sys.stderr)
        return EXIT_UNSAFE
    try:
        cfg = write_config(root, force=args.force)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    conn = _open_db()
    try:
        repo_id = Store(conn).get_or_create_repository(str(root))
        conn.commit()
    finally:
        conn.close()
    payload = {
        "config": str(cfg),
        "root": str(root),
        "data_dir": str(data_dir()),
        "database": str(db_path()),
        "repository_id": repo_id,
    }
    if args.json:
        print(json.dumps(payload))
    else:
        for k, v in payload.items():
            print(f"{k}: {v}")
    return EXIT_OK


def run_status(args: argparse.Namespace) -> int:
    """Relata estado do índice para a raiz resolvida (funciona pré-index)."""
    root = resolve_root(args.root)
    cfg = config_file()
    db_file = db_path()
    initialized = db_file.exists()

    payload: dict[str, object] = {
        "tool": "code-atlas",
        "version": __version__,
        "root": str(root),
        "config": str(cfg),
        "config_exists": cfg.exists(),
        "database": str(db_file),
        "initialized": initialized,
    }
    git_info = probe_git(root) if root.is_dir() else None
    git_payload: dict[str, object] | None = None
    if git_info is not None:
        git_payload = {
            "available": git_info.available,
            "is_repo": git_info.is_repo,
            "branch": git_info.branch,
            "head_sha": git_info.head_sha,
            "dirty": git_info.dirty,
            "is_worktree": git_info.is_worktree,
        }
    payload["git"] = git_payload

    if not initialized:
        payload["snapshot"] = None
        payload["counts"] = {"source_files": 0, "snapshots": 0, "index_jobs": 0}
        payload["schema_version"] = 0
        if not args.json:
            print(f"root: {root}")
            print("state: not initialized — run `code-atlas init` or `code-atlas index`")
            if git_info and git_info.is_repo:
                print(f"git: {git_info.branch or 'detached'}@{git_info.head_sha[:12]}")
        else:
            print(json.dumps(payload))
        return EXIT_OK

    conn = _open_db()
    try:
        store = Store(conn)
        payload["schema_version"] = schema_version(conn)
        row = conn.execute(
            "SELECT id FROM repositories WHERE root_handle = ?", (str(root),)
        ).fetchone()
        repo_id: int | None = int(row["id"]) if row else None
        payload["repository_id"] = repo_id
        count_queries = {
            "source_files": "SELECT COUNT(*) AS c FROM source_files",
            "snapshots": "SELECT COUNT(*) AS c FROM snapshots",
            "index_jobs": "SELECT COUNT(*) AS c FROM index_jobs",
        }
        payload["counts"] = {
            name: int(conn.execute(sql).fetchone()["c"])
            for name, sql in count_queries.items()
        }
        snap = store.latest_ready_snapshot(repo_id) if repo_id else None
        snap_payload: dict[str, object] | None = None
        if snap is not None:
            snap_payload = {
                "id": snap.id,
                "status": str(snap.status),
                "worktree_digest": snap.worktree_digest,
                "git_ref": snap.git_ref,
                "commit_sha": snap.commit_sha,
                "parent_snapshot_id": snap.parent_snapshot_id,
                "created_at": snap.created_at,
                "files": store.inventory_count(snap.id) if snap.id else 0,
            }
        payload["snapshot"] = snap_payload
    finally:
        conn.close()

    if args.json:
        print(json.dumps(payload))
    else:
        print(f"root:       {root}")
        print(f"schema:     v{payload['schema_version']}")
        if git_payload:
            print(f"git:        {git_payload['branch'] or 'detached'}"
                  f"@{str(git_payload['head_sha'])[:12]} "
                  f"{'(dirty)' if git_payload['dirty'] else '(clean)'}")
        if snap_payload:
            print(f"snapshot:   #{snap_payload['id']} {snap_payload['status']}"
                  f" — {snap_payload['files']} files")
        else:
            print("snapshot:   none (run `code-atlas index`)")
    return EXIT_OK


def run_index(args: argparse.Namespace) -> int:
    """Indexa o inventário do diretório-alvo via IndexCoordinator.

    Path arg com segmento ``..`` é rejeitado (hardening): para indexar um
    diretório pai, passe o caminho absoluto diretamente.
    """
    if args.path and ".." in Path(args.path).parts:
        print("error: '..' traversal is not allowed in PATH", file=sys.stderr)
        return EXIT_UNSAFE
    target = Path(args.path).expanduser().resolve() if args.path else resolve_root(args.root)
    if target == target.parent:
        print("error: refusing to index filesystem root", file=sys.stderr)
        return EXIT_UNSAFE
    if not target.is_dir():
        print(f"error: {target} is not a directory", file=sys.stderr)
        return EXIT_UNSAFE
    conn = _open_db()
    try:
        outcome: IndexOutcome = IndexCoordinator(conn).index(target, force=args.full)
    finally:
        conn.close()
    payload = {
        "tool": "code-atlas",
        "version": __version__,
        "root": str(target),
        "snapshot_id": outcome.snapshot_id,
        "job_id": outcome.job_id,
        "status": outcome.status,
        "files_indexed": outcome.files_indexed,
        "secrets_skipped": outcome.secrets_skipped,
        "warnings": list(outcome.warnings),
        "noop": outcome.noop,
        "error": outcome.error,
    }
    if args.json:
        print(json.dumps(payload))
    else:
        if outcome.status == "success":
            print(f"snapshot #{outcome.snapshot_id} READY — "
                  f"{outcome.files_indexed} inventory files")
        elif outcome.status == "noop":
            print(f"snapshot #{outcome.snapshot_id} unchanged — no-op index")
        else:
            print(f"index failed: {outcome.error}", file=sys.stderr)
        if outcome.secrets_skipped:
            print(f"secrets excluded: {outcome.secrets_skipped}")
        for w in outcome.warnings:
            print(f"warning: {w}", file=sys.stderr)
    return EXIT_OK if outcome.status in ("success", "noop") else EXIT_ERROR


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="code-atlas",
        description="Code Atlas — inteligência estrutural de código local-first (Cycle 1).",
        epilog="exit codes: 0 ok | 1 error | 2 usage | 3 unsafe/blocked",
    )
    parser.add_argument("--version", action="version", version=f"code-atlas {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_doctor = sub.add_parser("doctor", help="verifica ambiente, config e dependências")
    p_doctor.add_argument("--json", action="store_true", help="saída em JSON")
    p_doctor.set_defaults(func=run_doctor)

    p_init = sub.add_parser("init", help="inicializa config, data dir e registro do repo")
    p_init.add_argument("--root", metavar="PATH", help="raiz do workspace (default: cwd)")
    p_init.add_argument("--force", action="store_true", help="sobrescreve config existente")
    p_init.add_argument("--json", action="store_true", help="saída em JSON")
    p_init.set_defaults(func=run_init)

    p_index = sub.add_parser("index", help="indexa inventário de um diretório (snapshot)")
    p_index.add_argument("path", nargs="?", metavar="PATH", help="diretório a indexar")
    p_index.add_argument("--root", metavar="PATH", help="raiz alternativa (se PATH ausente)")
    p_index.add_argument(
        "--full", action="store_true", help="força re-scan mesmo sem mudanças"
    )
    p_index.add_argument("--json", action="store_true", help="saída em JSON")
    p_index.set_defaults(func=run_index)

    p_status = sub.add_parser("status", help="estado do índice (git, snapshot, migrações)")
    p_status.add_argument("--root", metavar="PATH", help="raiz do workspace (default: cwd)")
    p_status.add_argument("--json", action="store_true", help="saída em JSON")
    p_status.set_defaults(func=run_status)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))
