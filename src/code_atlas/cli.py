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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from code_atlas import __version__, gitbin
from code_atlas.config import (
    ConfigError,
    config_file,
    data_dir,
    resolve_root,
    write_config,
)
from code_atlas.discovery.identity import identity_key_for_root
from code_atlas.discovery.scanner import probe_git
from code_atlas.indexing import IndexCoordinator, IndexOutcome
from code_atlas.search.structural import (
    DEFAULT_LIMIT,
    MAX_DEPTH,
    MAX_LIMIT,
    SCHEMA_VERSION,
    SearchFilters,
    StructuralSearch,
    clamp_limit,
)
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


def _resolver_factories(
    target: Path,
    *,
    scip_path: str | None = None,
    only: Sequence[str] | None = None,
) -> tuple[Sequence[Any], list[str], dict[str, str], int]:
    """Resolvers opt-in + ``retain_snapshots`` do ``config.toml``.

    ``[resolvers]`` (lf-0027): ausência de config/tabela → zero
    factories (syntactic only). Erros de bootstrap são devolvidos
    como strings de diagnóstico — nunca levantam.
    ``[indexing] retain_snapshots`` (lf-0031): 0 = ilimitado.
    lf-0032: ``scip_path`` sobrescreve ``[resolvers.scip].index_path``;
    ``only`` restringe os resolvers ativos a ``scip`` / ``lsp`` /
    ``lsp:<nome>`` (``syntactic`` é sempre o baseline).
    """
    from code_atlas.resolvers.bootstrap import resolver_factories

    cfg_path = config_file()
    resolvers: Any = None
    retain = 0
    errors: list[str] = []
    if cfg_path.exists():
        from code_atlas.config import WorkspaceConfig

        try:
            cfg = WorkspaceConfig.load(cfg_path)
            resolvers = cfg.resolvers
            retain = cfg.retain_snapshots
        except ConfigError as exc:
            return (), [f"config load failed: {exc}"], {}, 0
    table: dict[str, Any] = dict(resolvers or {})
    if scip_path:
        scip = dict(table.get("scip") or {})
        scip["index_path"] = scip_path
        table["scip"] = scip
    if only:
        names = {n.strip() for n in only if n.strip()}
        known_lsp = set(
            ((resolvers or {}).get("lsp", {}) or {}).get("servers", {})
            or {}
        ) if isinstance((resolvers or {}).get("lsp"), dict) else set()
        unknown = sorted(
            n for n in names
            if n not in ("syntactic", "scip", "lsp")
            and not (n.startswith("lsp:") and n[4:] in known_lsp)
        )
        for u in unknown:
            errors.append(f"--resolver: resolver desconhecido '{u}'")
        if "scip" not in names:
            table.pop("scip", None)
        lsp = table.get("lsp")
        if isinstance(lsp, dict) and "lsp" not in names:
            wanted = {n[4:] for n in names if n.startswith("lsp:")}
            servers = dict(lsp.get("servers") or {})
            if wanted:
                lsp = dict(lsp)
                lsp["servers"] = {
                    k: v for k, v in servers.items() if k in wanted
                }
                table["lsp"] = lsp
            else:
                table.pop("lsp", None)
    versions: dict[str, str] = {}
    factories, boot_errors = resolver_factories(table or None, target, versions)
    errors.extend(boot_errors)
    return tuple(factories), errors, versions, retain


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
        payload: dict[str, Any] = {
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
        # lf-0021: identidade lógica via probe git — sem scan completo.
        repo_id = Store(conn).get_or_create_repository(
            identity_key_for_root(root), str(root)
        )
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
        # Resolvers opt-in (lf-0027): [resolvers] do config.toml —
        # erros de bootstrap viram diagnóstico no payload, não crash.
        # lf-0032: --scip sobrescreve o index_path; --resolver restringe
        # os resolvers ativos — nome desconhecido é erro tipado (nunca
        # uma flag que silenciosamente não faz nada).
        factories, boot_errors, res_versions, retain, fatal = (
            _index_resolvers(target, args))
        if fatal:
            conn.close()
            return _emit_error(args, fatal)
        coord = IndexCoordinator(
            conn, resolver_factories=factories,
            resolver_versions=res_versions,
            snapshot_retention=retain)
        # lf-0022: incremental é o default — ``--full`` é o escape
        # (force desliga o caminho incremental internamente).
        outcome: IndexOutcome = coord.index(target, force=args.full, incremental=True)
        plan_counts = (
            coord.last_plan.counts() if coord.last_plan is not None else {}
        )
    finally:
        conn.close()
    payload = {
        "tool": "code-atlas",
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "root": str(target),
        "snapshot_id": outcome.snapshot_id,
        "job_id": outcome.job_id,
        "status": outcome.status,
        "files_indexed": outcome.files_indexed,
        "secrets_skipped": outcome.secrets_skipped,
        "warnings": list(outcome.warnings),
        "noop": outcome.noop,
        "error": outcome.error,
        "stats": outcome.stats,
        "plan": plan_counts,
        "diagnostics": boot_errors,
    }
    if args.json:
        print(json.dumps(payload))
    else:
        _print_index_human(outcome)
    return EXIT_OK if outcome.status in ("success", "noop") else EXIT_ERROR


def _print_index_human(outcome: IndexOutcome) -> None:
    if outcome.status == "success":
        print(f"snapshot #{outcome.snapshot_id} READY — "
              f"{outcome.files_indexed} inventory files")
        s = outcome.stats
        if s.get("occurrences") or s.get("relations"):
            print(f"graph: {s.get('symbols_new', 0)} symbols, "
                  f"{s.get('occurrences', 0)} occurrences, "
                  f"{s.get('relations', 0)} relations "
                  f"({s.get('files_failed', 0)} files failed)")
    elif outcome.status == "noop":
        print(f"snapshot #{outcome.snapshot_id} unchanged — no-op index")
    else:
        print(f"index failed: {outcome.error}", file=sys.stderr)
    if outcome.secrets_skipped:
        print(f"secrets excluded: {outcome.secrets_skipped}")
    for w in outcome.warnings:
        print(f"warning: {w}", file=sys.stderr)


def _index_resolvers(
    target: Path, args: argparse.Namespace,
) -> tuple[Sequence[Any], list[str], dict[str, str], int, str]:
    """Flags de resolver do ``index`` → factories/versions/retain.

    ``fatal`` não-vazio = erro tipado de flag (resolver desconhecido)
    — o caller aborta sem indexar.
    """
    only: list[str] = []
    for r in args.resolver or ():
        only.extend(p for p in r.split(",") if p.strip())
    factories, errors, versions, retain = _resolver_factories(
        target, scip_path=args.scip, only=only or None)
    bad = "; ".join(
        e for e in errors if e.startswith("--resolver:"))
    return factories, errors, versions, retain, bad


def run_semantic_status(args: argparse.Namespace) -> int:
    """lf-0032: resolvers installed/enabled/used + cobertura do READY.

    Lê só o snapshot READY persistido — nunca inventa cobertura.
    ``installed``/``enabled`` vêm de ``describe_resolvers`` (read-only:
    nenhum server LSP é spawnado); ``used`` vem de
    ``relations.resolver`` — a única fonte autoritativa de uso real.
    """
    target = (
        Path(args.path).expanduser().resolve()
        if args.path else resolve_root(args.root)
    )
    conn = _open_db()
    try:
        payload = _semantic_payload(conn, target)
    finally:
        conn.close()
    return _emit_page(args, payload)


def _semantic_payload(
    conn: sqlite3.Connection, target: Path
) -> dict[str, Any]:
    """Monta o payload do ``semantic-status`` lendo só o READY."""
    search = StructuralSearch(conn)
    repo_id = search.repo_for_root(str(target))
    snap = (
        search.resolve_snapshot(repo_id, None)[0]
        if repo_id is not None else None
    )
    coverage, used = _resolver_usage(conn, snap)
    reports, boot_errors = _resolver_reports(target, used)
    analyzer_versions = dict(snap.analyzer_versions) if snap else {}
    last_job: dict[str, Any] = {}
    if repo_id is not None:
        row = conn.execute(
            "SELECT counts FROM index_jobs WHERE repository_id = ? "
            "AND status IN ('success','noop','failed') "
            "ORDER BY id DESC LIMIT 1",
            (repo_id,),
        ).fetchone()
        if row is not None:
            last_job = json.loads(row["counts"] or "{}")
    sem_keys = ("semantic_attempted", "semantic_enriched",
                "semantic_diagnostics")
    payload: dict[str, Any] = {
        "tool": "code-atlas",
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "root": str(target),
        "snapshot_id": snap.id if snap else None,
        "indexed": snap is not None,
        "resolvers": reports,
        "coverage": {
            "resolved": coverage.get("resolved", 0),
            "ambiguous": coverage.get("ambiguous", 0),
            "unresolved": coverage.get("unresolved", 0),
            "external": coverage.get("external", 0),
            "total": sum(coverage.values()),
        },
        "semantic": {k: last_job.get(k, 0) for k in sem_keys},
        "revision": {
            "commit_sha": snap.commit_sha if snap else None,
            "scip_index_sha": analyzer_versions.get("resolver:scip"),
        },
        "diagnostics": boot_errors,
        "_human": [],
    }
    for rep in reports:
        flags = "/".join(
            k for k in ("installed", "enabled", "used") if rep[k])
        payload["_human"].append(
            f"{rep['id']}: {flags or 'none'} edges={rep['edges']}")
    cov = payload["coverage"]
    payload["_human"].append(
        f"coverage: resolved={cov['resolved']} "
        f"ambiguous={cov['ambiguous']} "
        f"unresolved={cov['unresolved']} external={cov['external']}")
    for e in boot_errors:
        payload["_human"].append(f"diagnostic: {e}")
    return payload


def _resolver_usage(
    conn: sqlite3.Connection, snap: Any
) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    """Cobertura por status + uso por resolver no snapshot READY."""
    coverage: dict[str, int] = {}
    used: dict[str, dict[str, int]] = {}
    if snap is None or snap.id is None:
        return coverage, used
    for r in conn.execute(
        "SELECT resolver, resolution_status, COUNT(*) AS c "
        "FROM relations WHERE snapshot_id = ? "
        "GROUP BY resolver, resolution_status",
        (snap.id,),
    ):
        coverage[r["resolution_status"]] = (
            coverage.get(r["resolution_status"], 0) + r["c"])
        used.setdefault(r["resolver"], {}).setdefault(
            r["resolution_status"], 0)
        used[r["resolver"]][r["resolution_status"]] += r["c"]
    return coverage, used


def _resolver_reports(
    target: Path, used: dict[str, dict[str, int]]
) -> tuple[list[dict[str, Any]], list[str]]:
    """``describe_resolvers`` + marcação de uso real por resolver."""
    from code_atlas.resolvers.bootstrap import describe_resolvers

    table: Any = None
    cfg_path = config_file()
    if cfg_path.exists():
        from code_atlas.config import WorkspaceConfig

        try:
            table = WorkspaceConfig.load(cfg_path).resolvers
        except ConfigError:
            table = None
    reports, errors = describe_resolvers(table, target)
    for rep in reports:
        rid = str(rep["id"])
        by_status = dict(used.get(rid, {}))
        if rid == "syntactic-scope-v1":
            # arestas sintáticas carregam o parser-id como resolver
            # (ex.: tree-sitter-python) — tudo que não é resolver
            # externo pertence ao tier sintático baseline.
            for k, v in used.items():
                if k == rid:
                    continue
                if not k.startswith("scip") and not k.startswith("lsp:"):
                    for st, c in v.items():
                        by_status[st] = by_status.get(st, 0) + c
        rep["used"] = bool(by_status)
        rep["edges"] = sum(by_status.values())
        rep["by_status"] = by_status
    return reports, errors


def _search_context(
    args: argparse.Namespace,
) -> tuple[
    sqlite3.Connection | None,
    int | None,
    int | None,
    dict[str, Any] | None,
    str,
]:
    """Resolve (conn, repo_id, snapshot, repo_info) para read-only.

    Erros são impressos em stderr + JSON de erro; retorna tupla com
    ``None``s e mensagem quando indisponível — caller sai EXIT_ERROR.
    """
    root = resolve_root(args.root)
    conn = _open_db()
    search = StructuralSearch(conn)
    root_handle = str(root)
    repo_id = search.repo_for_root(root_handle)
    if repo_id is None:
        conn.close()
        return None, None, None, None, f"no indexed repository at {root_handle}"
    snap, err = search.resolve_snapshot(repo_id, args.snapshot)
    if err or snap is None or snap.id is None:
        conn.close()
        return None, None, None, None, err or "snapshot missing id"
    repo_info = search.repository_info(repo_id) or {"id": repo_id}
    return conn, repo_id, snap.id, repo_info, ""


def _emit_error(args: argparse.Namespace, msg: str) -> int:
    if args.json:
        print(json.dumps({"tool": "code-atlas", "version": __version__,
                          "error": msg, "schema_version": SCHEMA_VERSION}))
    else:
        print(f"error: {msg}", file=sys.stderr)
    return EXIT_ERROR


def _emit_page(args: argparse.Namespace, payload: dict[str, Any]) -> int:
    """--json: objeto único parseável. Humano: linhas pré-formatadas."""
    if args.json:
        payload.pop("_human", None)
        print(json.dumps(payload))
    else:
        for line in payload.pop("_human", []):
            print(line)
        if "total" in payload:
            print(
                f"-- total={payload['total']} "
                f"truncated={payload.get('truncated', False)}"
            )
    return EXIT_OK


def run_search(args: argparse.Namespace) -> int:
    conn, repo_id, snap_id, repo_info, err = _search_context(args)
    if err:
        return _emit_error(args, err)
    assert (
        conn is not None and repo_id is not None and snap_id is not None
    )
    strategy = getattr(args, "strategy", "structural") or "structural"
    if strategy != "structural":
        if repo_info is None:
            return _emit_error(args, "repository info unavailable")
        return _run_search_engine(args, conn, snap_id, repo_info, strategy)
    try:
        search = StructuralSearch(conn)
        filters = SearchFilters(
            language=args.language, kind=args.kind, path=args.path_filter,
        )
        limit = clamp_limit(args.limit)
        page = search.search_symbols(
            repo_id, snap_id, args.query, filters, limit, max(0, args.offset)
        )
        results = [dict(x) for x in page.items]
        payload: dict[str, Any] = {
            "tool": "code-atlas", "version": __version__,
            "schema_version": SCHEMA_VERSION, "command": "search",
            "query": args.query, "snapshot_id": snap_id,
            "repository": repo_info,
            "diagnostics": [],
            "filters": {"language": args.language, "kind": args.kind,
                        "path": args.path_filter},
            "limit": limit, "offset": args.offset,
            "total": page.total, "truncated": page.truncated,
            "results": results,
            "_human": [
                f"{r['symbol_id']:>5} {r['kind']:<9} {r['language']:<10} "
                f"{r['qualified_name']}" for r in results
            ],
        }
    finally:
        conn.close()
    return _emit_page(args, payload)


def _run_search_engine(
    args: argparse.Namespace,
    conn: sqlite3.Connection,
    snap_id: int,
    repo_info: dict[str, Any],
    strategy: str,
) -> int:
    """Estratégias do SearchEngine (lexical|graph|hybrid) com envelope v2."""
    from code_atlas.search import SearchEngine, SearchRequest
    from code_atlas.search.contracts import encode_cursor, query_fingerprint

    offset = max(0, args.offset)
    cursor = None
    if offset:
        fp = query_fingerprint(args.query, {
            "language": args.language, "kind": args.kind,
            "path": args.path_filter})
        cursor = encode_cursor(offset, fp)
    try:
        res = SearchEngine(conn).search(
            SearchRequest(
                query=args.query,
                strategy=strategy,
                language=args.language,
                kind=args.kind,
                path=args.path_filter,
                repository_root=str(resolve_root(args.root)),
                snapshot_id=snap_id,
                limit=clamp_limit(args.limit),
                cursor=cursor,
            )
        )
    finally:
        conn.close()
    items = [asdict(h) for h in res.items]
    payload: dict[str, Any] = res.to_dict()
    payload["tool"] = "code-atlas"
    payload["version"] = __version__
    payload["command"] = "search"
    payload["repository"] = repo_info
    payload["_human"] = [
        f"{h['qualified_name']:<40} {h['path']} "
        f"[{','.join(h['matched_fields']) or 'structural'} "
        f"score={h['score']:.4f}]" for h in items
    ]
    return _emit_page(args, payload)


def run_symbol(args: argparse.Namespace) -> int:
    conn, repo_id, snap_id, repo_info, err = _search_context(args)
    if err:
        return _emit_error(args, err)
    assert (
        conn is not None and repo_id is not None and snap_id is not None
    )
    try:
        search = StructuralSearch(conn)
        detail = search.symbol_detail(repo_id, snap_id, args.ident)
        if detail is None:
            return _emit_error(args, f"symbol {args.ident!r} not found")
        payload: dict[str, Any] = {
            "tool": "code-atlas", "version": __version__,
            "schema_version": SCHEMA_VERSION, "command": "symbol",
            "snapshot_id": snap_id, "result": detail,
            "repository": repo_info, "diagnostics": [],
            "_human": [
                f"{detail['kind']} {detail['qualified_name']} "
                f"({detail['language']})"
            ] + [
                f"  {o['path']}:{o['line']} [{o['parse_status']}]"
                for o in detail["occurrences"]
            ],
        }
    finally:
        conn.close()
    return _emit_page(args, payload)


def run_references(args: argparse.Namespace) -> int:
    conn, repo_id, snap_id, repo_info, err = _search_context(args)
    if err:
        return _emit_error(args, err)
    assert (
        conn is not None and repo_id is not None and snap_id is not None
    )
    try:
        search = StructuralSearch(conn)
        page, rerr = search.references(
            repo_id, snap_id, args.ident, args.relation_type,
            clamp_limit(args.limit), max(0, args.offset),
        )
        if page is None:
            return _emit_error(args, rerr)
        results = [dict(x) for x in page.items]
        payload: dict[str, Any] = {
            "tool": "code-atlas", "version": __version__,
            "schema_version": SCHEMA_VERSION, "command": "references",
            "symbol": args.ident, "snapshot_id": snap_id,
            "repository": repo_info, "diagnostics": [],
            "limit": clamp_limit(args.limit), "offset": args.offset,
            "total": page.total, "truncated": page.truncated,
            "results": results,
            "_human": [
                f"#{r['relation_id']} {r['type']} "
                f"{r['from']['symbol']}@{r['from']['path']}:{r['from']['line']} "
                f"-> {r['to']['symbol'] if r['to'] else r['to_unresolved']} "
                f"[{r['resolution_status']}]"
                for r in results
            ],
        }
    finally:
        conn.close()
    return _emit_page(args, payload)


def run_graph(args: argparse.Namespace) -> int:
    conn, repo_id, snap_id, repo_info, err = _search_context(args)
    if err:
        return _emit_error(args, err)
    assert (
        conn is not None and repo_id is not None and snap_id is not None
    )
    try:
        search = StructuralSearch(conn)
        filters = SearchFilters(
            language=args.language, kind=args.kind,
            path=args.path_filter, relation_type=args.relation_type,
        )
        dump = search.graph_dump(snap_id, filters, clamp_limit(args.limit))
        payload: dict[str, Any] = {
            "tool": "code-atlas", "version": __version__,
            "schema_version": SCHEMA_VERSION, "command": "graph",
            "snapshot_id": snap_id,
            "repository": repo_info, "diagnostics": [], "filters": {
                "language": args.language, "kind": args.kind,
                "path": args.path_filter, "relation_type": args.relation_type,
            },
            "limit": clamp_limit(args.limit),
            **dump,
            "_human": [
                f"nodes={dump['counts']['nodes']} edges={dump['counts']['edges']}",
            ],
        }
    finally:
        conn.close()
    return _emit_page(args, payload)


def run_index_plan(args: argparse.Namespace) -> int:
    """``index-plan`` (lf-0022): dry-run — plano JSON, zero writes no DB."""
    if args.path and ".." in Path(args.path).parts:
        print("error: '..' traversal is not allowed in PATH", file=sys.stderr)
        return EXIT_UNSAFE
    target = (
        Path(args.path).expanduser().resolve()
        if args.path
        else resolve_root(args.root)
    )
    if not target.is_dir():
        print(f"error: {target} is not a directory", file=sys.stderr)
        return EXIT_UNSAFE
    conn = _open_db()
    try:
        plan, diag = IndexCoordinator(conn).plan_dry_run(target)
    finally:
        conn.close()
    assert plan is not None  # dry-run sempre materializa um plano
    payload: dict[str, Any] = {
        "tool": "code-atlas", "version": __version__,
        "schema_version": SCHEMA_VERSION, "command": "index-plan",
        "root": str(target), "diagnostics": list(diag.values()),
        "plan": plan.to_dict(),
    }
    if args.json:
        print(json.dumps(payload))
    else:
        counts = plan.counts()
        print("index-plan (dry-run): " + ", ".join(
            f"{k}={v}" for k, v in sorted(counts.items())
        ))
        if diag:
            for d in diag.values():
                print(f"note: {d}")
        for f in plan.files:
            print(f"  {f.action:<22} {f.path}  ({f.reason})")
    return EXIT_OK


def run_definitions(args: argparse.Namespace) -> int:
    """``definitions`` (lf-0022): sites de definição do símbolo."""
    conn, repo_id, snap_id, repo_info, err = _search_context(args)
    if err:
        return _emit_error(args, err)
    assert (
        conn is not None and repo_id is not None and snap_id is not None
    )
    try:
        search = StructuralSearch(conn)
        out = search.definitions(repo_id, snap_id, args.ident)
        if out is None:
            return _emit_error(args, f"symbol {args.ident!r} not found")
        defs = out["definitions"]
        payload: dict[str, Any] = {
            "tool": "code-atlas", "version": __version__,
            "schema_version": SCHEMA_VERSION, "command": "definitions",
            "query": args.ident, "snapshot_id": snap_id,
            "repository": repo_info, "diagnostics": [],
            "symbol_id": out["symbol_id"],
            "qualified_name": out["qualified_name"],
            "total": len(defs), "truncated": False,
            "results": defs,
            "_human": [
                f"{out['qualified_name']} ({out['kind']})",
                *(
                    f"  {d['path']}:{d['line']}:{d['col']}"
                    f" [{d['parse_status']}]"
                    for d in defs
                ),
            ],
        }
    finally:
        conn.close()
    return _emit_page(args, payload)


def run_dependencies(args: argparse.Namespace) -> int:
    """``dependencies`` (lf-0022): arestas saíntes, BFS limitado."""
    conn, repo_id, snap_id, repo_info, err = _search_context(args)
    if err:
        return _emit_error(args, err)
    assert (
        conn is not None and repo_id is not None and snap_id is not None
    )
    depth = clamp_depth(args.depth)
    try:
        search = StructuralSearch(conn)
        out, derr = search.dependencies(
            repo_id, snap_id, args.ident, depth,
            clamp_limit(args.limit), max(0, args.offset),
        )
        if out is None:
            return _emit_error(args, derr)
        results = out["results"]
        payload: dict[str, Any] = {
            "tool": "code-atlas", "version": __version__,
            "schema_version": SCHEMA_VERSION, "command": "dependencies",
            "query": args.ident, "snapshot_id": snap_id,
            "repository": repo_info, "diagnostics": [],
            "symbol_id": out["symbol_id"],
            "qualified_name": out["qualified_name"],
            "depth": depth, "limit": clamp_limit(args.limit),
            "offset": max(0, args.offset),
            "total": out["total"], "truncated": out["truncated"],
            "visited_symbols": out["visited_symbols"],
            "results": results,
            "_human": [
                f"deps of {out['qualified_name']} "
                f"(depth={depth}, visited={out['visited_symbols']})",
                *(
                    f"  d{r['depth']} {r['type']} -> "
                    f"{r['to']['symbol'] if r['to'] else r['to_unresolved']}"
                    f" [{r['resolution_status']}]"
                    for r in results
                ),
            ],
        }
    finally:
        conn.close()
    return _emit_page(args, payload)


def clamp_depth(depth: int | None) -> int:
    """Sane default + teto duro de traversal (lf-0022)."""
    if depth is None:
        return 1
    return max(1, min(int(depth), MAX_DEPTH))


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
    p_index.add_argument(
        "--incremental", action="store_true", default=True,
        help="indexação incremental (default ativo; --full desativa)",
    )
    p_index.add_argument(
        "--scip", metavar="PATH", default=None,
        help="índice SCIP ad-hoc (sobrescreve [resolvers.scip].index_path)",
    )
    p_index.add_argument(
        "--resolver", metavar="NAME", action="append", default=None,
        help="restringe resolvers ativos (syntactic|scip|lsp|lsp:<nome>); "
             "repetível ou separado por vírgula",
    )
    p_index.add_argument("--json", action="store_true", help="saída em JSON")
    p_index.set_defaults(func=run_index)

    p_sem = sub.add_parser(
        "semantic-status",
        help="resolvers instalados/habilitados/usados + cobertura semântica",
    )
    p_sem.add_argument("path", nargs="?", metavar="PATH",
                       help="diretório do workspace")
    p_sem.add_argument("--root", metavar="PATH",
                       help="raiz alternativa (se PATH ausente)")
    p_sem.add_argument("--json", action="store_true", help="saída em JSON")
    p_sem.set_defaults(func=run_semantic_status)

    p_plan = sub.add_parser(
        "index-plan", help="dry-run: plano de indexação em JSON (zero writes)"
    )
    p_plan.add_argument("path", nargs="?", metavar="PATH",
                        help="diretório a planejar")
    p_plan.add_argument("--root", metavar="PATH",
                        help="raiz alternativa (se PATH ausente)")
    p_plan.add_argument("--json", action="store_true", help="saída em JSON")
    p_plan.set_defaults(func=run_index_plan)

    p_status = sub.add_parser("status", help="estado do índice (git, snapshot, migrações)")
    p_status.add_argument("--root", metavar="PATH", help="raiz do workspace (default: cwd)")
    p_status.add_argument("--json", action="store_true", help="saída em JSON")
    p_status.set_defaults(func=run_status)

    p_search = sub.add_parser(
        "search", help="busca estrutural por símbolo (substring)"
    )
    p_search.add_argument("query", metavar="QUERY", help="substring do qualified_name")
    p_search.add_argument(
        "--strategy", metavar="NAME", default="structural",
        help="structural|lexical|graph|hybrid (default structural)")
    _add_query_args(p_search, p_path=True)
    p_search.set_defaults(func=run_search)

    p_symbol = sub.add_parser("symbol", help="detalhe de um símbolo + ocorrências")
    p_symbol.add_argument("ident", metavar="SYMBOL", help="id numérico ou qualified_name")
    _add_query_args(p_symbol, filters=False)
    p_symbol.set_defaults(func=run_symbol)

    p_refs = sub.add_parser("references", help="relações que tocam um símbolo")
    p_refs.add_argument("ident", metavar="SYMBOL", help="id numérico ou qualified_name")
    p_refs.add_argument("--relation-type", metavar="TYPE", help="filtra por EdgeKind")
    _add_query_args(p_refs, filters=False)
    p_refs.set_defaults(func=run_references)

    p_graph = sub.add_parser("graph", help="dump de nós+arestas do snapshot (JSON)")
    p_graph.add_argument("--relation-type", metavar="TYPE", help="filtra arestas")
    _add_query_args(p_graph, p_path=True)
    p_graph.set_defaults(func=run_graph)

    p_defs = sub.add_parser(
        "definitions", help="sites de definição de um símbolo (path:span)"
    )
    p_defs.add_argument("ident", metavar="SYMBOL",
                        help="id numérico ou qualified_name")
    _add_query_args(p_defs, filters=False)
    p_defs.set_defaults(func=run_definitions)

    p_deps = sub.add_parser(
        "dependencies", help="arestas saíntes de um símbolo (BFS limitado)"
    )
    p_deps.add_argument("ident", metavar="SYMBOL",
                        help="id numérico ou qualified_name")
    p_deps.add_argument(
        "--depth", metavar="N", type=int, default=1,
        help=f"profundidade de traversal (default 1, teto {MAX_DEPTH})",
    )
    _add_query_args(p_deps, filters=False)
    p_deps.set_defaults(func=run_dependencies)

    return parser


def _add_query_args(
    p: argparse.ArgumentParser, *, filters: bool = True, p_path: bool = False
) -> None:
    """Args comuns dos comandos read-only de consulta."""
    p.add_argument("--root", metavar="PATH", help="raiz do workspace (default: cwd)")
    p.add_argument("--snapshot", metavar="ID", type=int,
                   help="snapshot alvo (default: READY mais recente)")
    p.add_argument("--limit", metavar="N", type=int,
                   help=f"máx. de resultados (default {DEFAULT_LIMIT}, teto {MAX_LIMIT})")
    p.add_argument("--offset", metavar="N", type=int, default=0,
                   help="deslocamento de paginação")
    if filters:
        p.add_argument("--language", metavar="LANG", help="filtra por linguagem")
        p.add_argument("--kind", metavar="KIND", help="filtra por NodeKind")
        if p_path:
            p.add_argument("--path", dest="path_filter", metavar="SUBSTR",
                           help="substring de relative_path")
    else:
        p.set_defaults(language=None, kind=None, path_filter=None)
    p.add_argument("--json", action="store_true", help="saída em JSON")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))
