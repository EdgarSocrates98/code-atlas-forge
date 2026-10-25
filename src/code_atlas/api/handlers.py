"""Handlers da API v1 — reutilizam os engines de domínio, nunca SQL solto.

Cada handler abre uma conexão read-only por request (ciclo de vida real,
mesmo padrão do MCP) e retorna ``dict`` serializável. Erros viram
``ApiError`` tipados — nunca traceback ao cliente.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, unquote, urlparse

from code_atlas.api.server import ApiError
from code_atlas.federation import (
    Federation,
    FederationError,
    InvalidKeyError,
    InvalidPinError,
    MemberNotFoundError,
    RepositoryNotFoundError,
    SnapshotNotFoundError,
    SnapshotNotReadyError,
    WorkspaceExistsError,
    WorkspaceNotFoundError,
    WorkspaceSizeError,
)
from code_atlas.storage.store import Store

if TYPE_CHECKING:
    from http.server import BaseHTTPRequestHandler

    from code_atlas.api.server import _AtlasHttpd

_MAX_LIMIT = 200
_QNAME = re.compile(r"^[a-zA-Z0-9_:.<>$/\-]{1,300}$")


def _qs(handler: BaseHTTPRequestHandler) -> dict[str, str]:
    raw = parse_qs(urlparse(handler.path).query)
    return {k: v[0] for k, v in raw.items() if v}


def _int(params: dict[str, str], key: str, default: int = 0,
         cap: int = _MAX_LIMIT) -> int:
    try:
        v = int(params.get(key, "") or default)
    except ValueError as exc:
        raise ApiError("bad-param", f"parâmetro {key} não é inteiro") from exc
    return max(0, min(v, cap))


def _repo(conn: Any, params: dict[str, str]) -> int:
    """Resolve repository_id — escopo obrigatório (Cycle 5.1)."""
    store = Store(conn)
    rid = params.get("repository_id", "")
    if rid:
        try:
            rid_int = int(rid)
        except ValueError as exc:
            raise ApiError("bad-param", "repository_id inválido") from exc
        if store.repository_by_id(rid_int) is None:
            raise ApiError("repo-not-found", "repositório inexistente", 404)
        return rid_int
    repos = [r for r in _all_repos(store)]
    if not repos:
        raise ApiError("no-repository", "nenhum repositório indexado", 404)
    return repos[0]


def _all_repos(store: Store) -> list[int]:
    rows = store.conn.execute(
        "SELECT id FROM repositories ORDER BY id").fetchall()
    return [int(r["id"]) for r in rows]


def _snapshot(conn: Any, repo_id: int, params: dict[str, str]) -> int:
    store = Store(conn)
    sid = params.get("snapshot_id", "")
    if sid:
        try:
            snap = store.snapshot_by_id(int(sid))
        except ValueError as exc:
            raise ApiError("bad-param", "snapshot_id inválido") from exc
        if snap is None or snap.repository_id != repo_id:
            raise ApiError("snapshot-not-found",
                           "snapshot inexistente no repositório", 404)
        if snap.status != "READY":
            raise ApiError("snapshot-not-ready",
                           f"snapshot {snap.id} está {snap.status}", 409)
        assert snap.id is not None
        return snap.id
    snap = store.latest_ready_snapshot(repo_id)
    if snap is None:
        raise ApiError("snapshot-not-ready", "nenhum snapshot READY", 409)
    assert snap.id is not None
    return snap.id


_NODE_SELECT = (
    "SELECT o.id AS occurrence_id, o.logical_symbol_id AS symbol_id, "
    "s.qualified_name AS qname, s.kind AS kind, s.language AS language, "
    "f.relative_path AS path, o.span_start, o.span_end, o.parse_status "
    "FROM symbol_occurrences o "
    "JOIN symbols s ON s.id = o.logical_symbol_id "
    "JOIN source_files f ON f.id = o.source_file_id ")


def _row_to_node(row: Any) -> dict[str, Any]:
    return {
        "id": f"occ:{row['occurrence_id']}",
        "occurrence_id": row["occurrence_id"],
        "symbol_id": row["symbol_id"],
        "qname": row["qname"],
        "kind": row["kind"],
        "language": row["language"],
        "path": row["path"],
        "span": {"start": row["span_start"], "end": row["span_end"]},
        "parse_status": row["parse_status"],
    }


_NODES_IN_QUERY = (
    _NODE_SELECT +
    "WHERE o.snapshot_id = ? AND o.id IN (SELECT value FROM json_each(?))")
_NODE_BY_ID_QUERY = _NODE_SELECT + "WHERE o.snapshot_id = ? AND o.id = ?"


_COUNT_QUERIES = {
    "file_inventory":
        "SELECT COUNT(*) AS n FROM file_inventory WHERE snapshot_id = ?",
    "symbol_occurrences":
        "SELECT COUNT(*) AS n FROM symbol_occurrences WHERE snapshot_id = ?",
    "relations":
        "SELECT COUNT(*) AS n FROM relations WHERE snapshot_id = ?",
    "evidence":
        "SELECT COUNT(*) AS n FROM evidence WHERE snapshot_id = ?",
}


# --- handlers -----------------------------------------------------------


def _health(server: _AtlasHttpd, conn: Any, _p: dict[str, str],
            _b: dict[str, Any] | None) -> dict[str, Any]:
    return {"status": "ok", "service": "code-atlas-api", "version": 1}


def _capabilities(server: _AtlasHttpd, conn: Any, _p: dict[str, str],
                  _b: dict[str, Any] | None) -> dict[str, Any]:
    from code_atlas.search.engine import SearchEngine
    caps = dataclasses.asdict(SearchEngine(conn).capabilities())
    return {"search": caps, "api_version": 1, "readonly": True}


def _repositories(server: _AtlasHttpd, conn: Any, _p: dict[str, str],
                  _b: dict[str, Any] | None) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT id, identity_key, root_handle FROM repositories ORDER BY id"
    ).fetchall()
    items = [{"id": r["id"], "identity_key": r["identity_key"],
              "root_handle": r["root_handle"]} for r in rows]
    return {"items": items, "total": len(items)}


def _snapshots(server: _AtlasHttpd, conn: Any, repo_id: str,
               _b: dict[str, Any] | None) -> dict[str, Any]:
    try:
        rid = int(repo_id)
    except ValueError as exc:
        raise ApiError("bad-param", "repository_id inválido") from exc
    rows = conn.execute(
        "SELECT id, status, commit_sha, git_ref, created_at FROM snapshots "
        "WHERE repository_id = ? ORDER BY id DESC LIMIT 100", (rid,)).fetchall()
    return {"items": [dict(r) for r in rows]}


def _graph_overview(server: _AtlasHttpd, conn: Any, p: dict[str, str],
                    _b: dict[str, Any] | None) -> dict[str, Any]:
    """Visão macro: nós agregados por arquivo — nunca o grafo inteiro."""
    rid = _repo(conn, p)
    sid = _snapshot(conn, rid, p)
    limit = _int(p, "limit", 500, cap=2000)
    rows = conn.execute(
        "SELECT f.id AS file_id, f.relative_path AS path, "
        "COUNT(*) AS symbols, MIN(s.language) AS language "
        "FROM symbol_occurrences o "
        "JOIN symbols s ON s.id = o.logical_symbol_id "
        "JOIN source_files f ON f.id = o.source_file_id "
        "WHERE o.snapshot_id = ? "
        "GROUP BY f.id ORDER BY symbols DESC LIMIT ?",
        (sid, limit)).fetchall()
    total = conn.execute(
        "SELECT COUNT(DISTINCT source_file_id) AS n FROM symbol_occurrences "
        "WHERE snapshot_id = ?", (sid,)).fetchone()["n"]
    return {"snapshot_id": sid, "repository_id": rid,
            "items": [dict(r) for r in rows], "total_files": total,
            "truncated": len(rows) < total}


def _row_to_edge(row: Any, snapshot_id: int) -> dict[str, Any]:
    """DTO completo de aresta — prova da relação, nunca só os endpoints."""
    return {
        "id": row["id"],
        "relation_id": row["id"],
        "snapshot_id": snapshot_id,
        "from_id": row["from_id"],
        "to_id": row["to_id"],
        "to_unresolved": row["to_unresolved"],
        "type": row["type"],
        "resolution_status": row["resolution_status"],
        "confidence_class": row["confidence_class"],
        "resolver": row["resolver"],
        "evidence_ids": json.loads(row["evidence_ids"] or "[]"),
    }


def _graph_neighbors(server: _AtlasHttpd, conn: Any, p: dict[str, str],
                     _b: dict[str, Any] | None) -> dict[str, Any]:
    rid = _repo(conn, p)
    sid = _snapshot(conn, rid, p)
    direction = p.get("direction", "out")
    if direction not in ("in", "out", "both"):
        raise ApiError("bad-param", "direction deve ser in|out|both")
    limit = _int(p, "limit", 50)
    occ_id = _int(p, "occurrence_id", 0, cap=10**9)
    if not occ_id:
        raise ApiError("bad-param", "occurrence_id obrigatório")
    edges = conn.execute(
        "SELECT id, from_id, to_id, to_unresolved, type, resolution_status, "
        "confidence_class, resolver, evidence_ids "
        "FROM relations WHERE snapshot_id = ? AND ("
        "  (? = 'out' AND from_id = ?) "
        "  OR (? = 'in' AND to_id = ?) "
        "  OR (? = 'both' AND (from_id = ? OR to_id = ?))"
        ") ORDER BY id LIMIT ?",
        (sid, direction, occ_id, direction, occ_id,
         direction, occ_id, occ_id, limit)).fetchall()
    occ_ids = {e["from_id"] for e in edges} | {e["to_id"] for e in edges}
    occ_ids.discard(0)
    occ_ids.discard(None)
    nodes = []
    if occ_ids:
        nodes = conn.execute(
            _NODES_IN_QUERY,
            (sid, json.dumps(sorted(occ_ids)))).fetchall()
    return {"snapshot_id": sid,
            "edges": [_row_to_edge(e, sid) for e in edges],
            "nodes": [_row_to_node(n) for n in nodes],
            "truncated": len(edges) == limit}


def _file_occurrences(server: _AtlasHttpd, conn: Any, file_id: str,
                      p: dict[str, str]) -> dict[str, Any]:
    """Ocorrências de símbolo de um arquivo — escopo snapshot do repo dele."""
    try:
        fid = int(file_id)
    except ValueError as exc:
        raise ApiError("bad-param", "file_id inválido") from exc
    frow = conn.execute(
        "SELECT id, repository_id, relative_path, language FROM source_files "
        "WHERE id = ?", (fid,)).fetchone()
    if frow is None:
        raise ApiError("file-not-found", "arquivo inexistente", 404)
    rid = int(frow["repository_id"])
    sid = _snapshot(conn, rid, p)
    limit = _int(p, "limit", 200, cap=500)
    offset = _int(p, "offset", 0, cap=10**9)
    rows = conn.execute(
        "SELECT o.id AS occurrence_id, o.logical_symbol_id AS symbol_id, "
        "s.qualified_name AS qname, s.kind AS kind, o.span_start, o.span_end, "
        "o.line, o.col, o.content_hash, o.parse_status "
        "FROM symbol_occurrences o "
        "JOIN symbols s ON s.id = o.logical_symbol_id "
        "WHERE o.snapshot_id = ? AND o.source_file_id = ? "
        "ORDER BY o.line, o.col, o.id LIMIT ? OFFSET ?",
        (sid, fid, limit, offset)).fetchall()
    total = conn.execute(
        "SELECT COUNT(*) AS n FROM symbol_occurrences "
        "WHERE snapshot_id = ? AND source_file_id = ?",
        (sid, fid)).fetchone()["n"]
    items = [{
        "id": f"occ:{r['occurrence_id']}",
        "occurrence_id": r["occurrence_id"],
        "symbol_id": r["symbol_id"],
        "qualified_name": r["qname"],
        "kind": r["kind"],
        "span_start": r["span_start"],
        "span_end": r["span_end"],
        "line": r["line"],
        "col": r["col"],
        "content_hash": r["content_hash"],
        "parse_status": r["parse_status"],
    } for r in rows]
    return {"file_id": fid, "repository_id": rid, "snapshot_id": sid,
            "path": frow["relative_path"], "language": frow["language"],
            "items": items, "total": total, "limit": limit, "offset": offset,
            "truncated": offset + len(items) < total}


def _symbol(server: _AtlasHttpd, conn: Any, sym_id: str,
            p: dict[str, str]) -> dict[str, Any]:
    rid = _repo(conn, p)
    sid = _snapshot(conn, rid, p)
    try:
        occ_id = int(sym_id)
    except ValueError as exc:
        raise ApiError("bad-param", "id de símbolo deve ser occurrence_id") from exc
    row = conn.execute(_NODE_BY_ID_QUERY, (sid, occ_id)).fetchone()
    if row is None:
        raise ApiError("symbol-not-found", "símbolo inexistente", 404)
    counts = conn.execute(
        "SELECT type, resolution_status, COUNT(*) AS n FROM relations "
        "WHERE snapshot_id = ? AND (from_id = ? OR to_id = ?) GROUP BY 1,2",
        (sid, occ_id, occ_id)).fetchall()
    out = _row_to_node(row)
    out["relations_summary"] = [dict(c) for c in counts]
    return out


def _search_strategy(p: dict[str, str]) -> str:
    """Estratégia pedida — default hybrid, valor desconhecido → hybrid."""
    s = p.get("strategy", "hybrid")
    return s if s in (
        "lexical", "structural", "hybrid", "vector", "graph") else "hybrid"


def _search_repo_filter(conn: Any, p: dict[str, str]) -> int | None:
    """``repository_id`` opcional validado — narrowing por repositório."""
    rid = p.get("repository_id", "")
    if not rid:
        return None
    try:
        repo_id = int(rid)
    except ValueError as exc:
        raise ApiError("bad-param", "repository_id inválido") from exc
    if Store(conn).repository_by_id(repo_id) is None:
        raise ApiError("repo-not-found", "repositório inexistente", 404)
    return repo_id


def _search(server: _AtlasHttpd, conn: Any, p: dict[str, str],
            _b: dict[str, Any] | None) -> dict[str, Any]:
    """Busca de símbolos + faceta de domínio (lf-0119).

    ``workspace=``/``federation=`` ativa a busca federada (mesmo
    envelope da família search + identidade da view); ``repository_id``
    seleciona/narrow o repo; ``domain`` filtra a faceta de domínio;
    ``pin=repo:snap[,…]`` aplica overrides da view.
    """
    from code_atlas.search.contracts import SearchRequest
    from code_atlas.search.engine import SearchEngine
    q = p.get("q", "")
    if not q or len(q) > 300:
        raise ApiError("bad-param", "q obrigatório (≤300 chars)")
    store = Store(conn)
    workspace = p.get("workspace") or p.get("federation") or ""
    repo_filter = _search_repo_filter(conn, p)
    doms = _csv_filter(p.get("domain", ""), "domain", max_items=1)
    req = SearchRequest(
        query=q,
        strategy=_search_strategy(p),
        snapshot_id=_int(p, "snapshot_id", 0, cap=10**9) or None,
        limit=_int(p, "limit", 20, cap=100) or 20,
        cursor=p.get("cursor") or None,
        language=p.get("language") or None,
        kind=p.get("kind") or None,
        path=p.get("path") or None,
        domain=doms[0] if doms else None)
    if workspace:
        from code_atlas.search.federated import FederatedSearchEngine
        pins = _parse_pins(p)
        return _fed_call(
            lambda: FederatedSearchEngine(conn).search(
                req, workspace, pins, repository_id=repo_filter
            ).to_dict())
    # repo escolhido: repository_id (explícito) > repository_root > 1º repo
    root = p.get("repository_root", "")
    if repo_filter is not None:
        repo = store.repository_by_id(repo_filter)
        assert repo is not None  # validado em _search_repo_filter
        root = repo.root_handle
    elif not root:
        first = conn.execute(
            "SELECT root_handle FROM repositories ORDER BY id LIMIT 1"
        ).fetchone()
        root = str(first["root_handle"]) if first else ""
    elif store.repository_id_for(root) is None:
        raise ApiError("repo-not-indexed", "repositório não indexado", 404)
    res = SearchEngine(conn).search(
        dataclasses.replace(req, repository_root=root))
    return dataclasses.asdict(res)


def _context(server: _AtlasHttpd, conn: Any, _p: dict[str, str],
             body: dict[str, Any] | None) -> dict[str, Any]:
    from code_atlas.context.contracts import ContextRequest
    from code_atlas.context.engine import ContextEngine
    body = body or {}
    task = str(body.get("task", ""))[:2000]
    if not task:
        raise ApiError("bad-param", "task obrigatória")
    pack = ContextEngine(conn).build(ContextRequest(
        task=task,
        repository_root=str(body.get("repository_root", ""))[:1000],
        snapshot_id=body.get("snapshot_id"),
        level=body.get("level", "focus"),
        budget_tokens=min(int(body.get("budget_tokens", 4000)), 64000),
        max_symbols=min(int(body.get("max_symbols", 50)), 200)))
    return pack.to_dict()


def _impact(server: _AtlasHttpd, conn: Any, _p: dict[str, str],
            body: dict[str, Any] | None) -> dict[str, Any]:
    from code_atlas.impact.contracts import ImpactRequest
    from code_atlas.impact.engine import ImpactEngine
    body = body or {}
    root = str(body.get("repository_root", ""))
    if not root or len(root) > 1000:
        raise ApiError("bad-param", "repository_root obrigatório")
    res = ImpactEngine(conn).analyze(ImpactRequest(
        repository_root=root,
        base_revision=str(body.get("base_revision", "HEAD"))[:200],
        target_revision=str(body.get("target_revision", ""))[:200],
        symbol=str(body.get("symbol"))[:300] if body.get("symbol") else None,
        file=str(body.get("file"))[:1000] if body.get("file") else None,
        base_snapshot_id=body.get("base_snapshot_id"),
        target_snapshot_id=body.get("target_snapshot_id"),
        include_possible=bool(body.get("include_possible", True)),
        known_only=bool(body.get("known_only", False))))
    return dataclasses.asdict(res)


def _evidence(server: _AtlasHttpd, conn: Any, ev_id: str,
              p: dict[str, str]) -> dict[str, Any]:
    rid = _repo(conn, p)
    sid = _snapshot(conn, rid, p)
    try:
        occ_id = int(ev_id)
    except ValueError as exc:
        raise ApiError("bad-param", "id de evidência inválido") from exc
    row = conn.execute(
        "SELECT id, extractor, extractor_version, notes, path, span, "
        "content_hash FROM evidence WHERE snapshot_id = ? AND id = ?",
        (sid, occ_id)).fetchone()
    if row is None:
        raise ApiError("evidence-not-found", "evidência inexistente", 404)
    refs = conn.execute(
        "SELECT id, from_id, to_id, type, resolution_status FROM relations "
        "WHERE snapshot_id = ? AND evidence_ids LIKE ? LIMIT 100",
        (sid, f"%{occ_id}%")).fetchall()
    return {"snapshot_id": sid, "evidence": dict(row),
            "referencing_relations": [dict(r) for r in refs]}


def _index_status(server: _AtlasHttpd, conn: Any, _p: dict[str, str],
                  _b: dict[str, Any] | None) -> dict[str, Any]:
    repos = conn.execute(
        "SELECT r.id, r.root_handle, "
        "(SELECT COUNT(*) FROM snapshots s WHERE s.repository_id = r.id "
        " AND s.status = 'READY') AS ready, "
        "(SELECT MAX(id) FROM snapshots s WHERE s.repository_id = r.id "
        " AND s.status = 'READY') AS latest_ready "
        "FROM repositories r ORDER BY r.id").fetchall()
    items = []
    for r in repos:
        sid = r["latest_ready"]
        counts = {}
        if sid:
            for tbl, sql in _COUNT_QUERIES.items():
                counts[tbl] = conn.execute(sql, (sid,)).fetchone()["n"]
        items.append({"repository_id": r["id"], "root_handle": r["root_handle"],
                      "ready_snapshots": r["ready"], "latest_ready": sid,
                      "counts": counts})
    return {"items": items}


# --- federação multi-repo (lf-0118, Phase 10 — somente leitura) -------
#
# Views/lookups/graphs são GET sobre conexão ``query_only``; criar/
# editar workspaces é superfície exclusiva da CLI — POST de federação
# não existe por desenho nesta fase.

_FED_ERROR_MAP: tuple[tuple[type[FederationError], str, int], ...] = (
    (WorkspaceNotFoundError, "workspace-not-found", 404),
    (WorkspaceSizeError, "federation-too-small", 409),
    (WorkspaceExistsError, "workspace-exists", 409),
    (MemberNotFoundError, "member-not-found", 404),
    (RepositoryNotFoundError, "repo-not-found", 404),
    (SnapshotNotFoundError, "snapshot-not-found", 404),
    (SnapshotNotReadyError, "snapshot-not-ready", 409),
    (InvalidPinError, "bad-pin", 400),
    (InvalidKeyError, "bad-key", 400),
)


def _fed_call[T](fn: Callable[[], T]) -> T:
    """Mapeia ``FederationError`` tipadas para ``ApiError``."""
    try:
        return fn()
    except FederationError as exc:
        for cls, code, status in _FED_ERROR_MAP:
            if isinstance(exc, cls):
                raise ApiError(code, str(exc)[:300], status) from exc
        raise ApiError("federation-error", str(exc)[:300]) from exc


def _parse_pins(p: dict[str, str]) -> dict[int, int]:
    """``?pin=<repo>:<snap>[,<repo>:<snap>…]`` — overrides da view."""
    raw = p.get("pin", "")
    out: dict[int, int] = {}
    if not raw.strip():
        return out
    for part in raw.split(","):
        seg = part.strip()
        if not seg:
            continue
        repo, sep, snap = seg.partition(":")
        if not sep or not repo.isdigit() or not snap.isdigit():
            raise ApiError(
                "bad-param",
                "pin deve ser repo:snapshot[,repo:snapshot…]",
            )
        out[int(repo)] = int(snap)
    return out


def _fed_workspaces(server: _AtlasHttpd, conn: Any, _p: dict[str, str],
                    _b: dict[str, Any] | None) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT w.id, w.name, w.created_at, "
        "(SELECT COUNT(*) FROM federation_members m "
        " WHERE m.workspace_id = w.id) AS members "
        "FROM federation_workspaces w ORDER BY w.name").fetchall()
    return {"items": [dict(r) for r in rows], "total": len(rows)}


def _fed_workspace(server: _AtlasHttpd, conn: Any, ws_id: str,
                   _p: dict[str, str]) -> dict[str, Any]:
    fed = Federation(conn)
    ws = _fed_call(lambda: fed.get_workspace(int(ws_id)))
    rows = conn.execute(
        "SELECT m.repository_id, m.pinned_snapshot_id, m.added_at, "
        "r.root_handle, r.identity_key "
        "FROM federation_members m "
        "JOIN repositories r ON r.id = m.repository_id "
        "WHERE m.workspace_id = ? ORDER BY m.repository_id",
        (ws.id,)).fetchall()
    return {"id": ws.id, "name": ws.name, "created_at": ws.created_at,
            "members": [dict(r) for r in rows]}


def _fed_view(server: _AtlasHttpd, conn: Any, ws_id: str,
              p: dict[str, str]) -> dict[str, Any]:
    fed = Federation(conn)
    pins = _parse_pins(p)
    return _fed_call(
        lambda: fed.resolve_view(fed.get_workspace(int(ws_id)), pins)
        .to_dict()
    )


def _fed_entity_occurrences(server: _AtlasHttpd, conn: Any, ws_id: str,
                            key: str, p: dict[str, str]) -> dict[str, Any]:
    fed = Federation(conn)
    pins = _parse_pins(p)
    logical_key = unquote(key)[:2000]
    return _fed_call(
        lambda: fed.entity_occurrences(
            fed.get_workspace(int(ws_id)), logical_key, pins
        )
    )


def _fed_graph(server: _AtlasHttpd, conn: Any, ws_id: str,
               p: dict[str, str]) -> dict[str, Any]:
    fed = Federation(conn)
    pins = _parse_pins(p)
    depth = _int(p, "depth", 1, cap=8)
    limit = _int(p, "limit", 500, cap=5000)
    return _fed_call(
        lambda: fed.graph(
            fed.get_workspace(int(ws_id)), depth=depth, limit=limit,
            pins=pins,
        ).to_dict()
    )


def _fed_search(server: _AtlasHttpd, conn: Any, ws_id: str,
                p: dict[str, str]) -> dict[str, Any]:
    """Busca federada num workspace (lf-0119).

    Cada membro é pesquisado no PRÓPRIO escopo ``(repo, snapshot)``
    resolvido pela view — merge posicional determinístico, items
    tagueados ``repository_id``+``snapshot_id``, homônimos cross-repo
    nunca colapsam. ``pin=`` e ``snapshot_id`` ajustam a view;
    ``repository_id`` restringe a um membro.
    """
    from code_atlas.search.contracts import SearchRequest
    from code_atlas.search.federated import FederatedSearchEngine
    q = p.get("q", "")
    if not q or len(q) > 300:
        raise ApiError("bad-param", "q obrigatório (≤300 chars)")
    repo_filter = _search_repo_filter(conn, p)
    doms = _csv_filter(p.get("domain", ""), "domain", max_items=1)
    req = SearchRequest(
        query=q,
        strategy=_search_strategy(p),
        snapshot_id=_int(p, "snapshot_id", 0, cap=10**9) or None,
        limit=_int(p, "limit", 20, cap=100) or 20,
        cursor=p.get("cursor") or None,
        language=p.get("language") or None,
        kind=p.get("kind") or None,
        path=p.get("path") or None,
        domain=doms[0] if doms else None)
    pins = _parse_pins(p)
    return _fed_call(
        lambda: FederatedSearchEngine(conn).search(
            req, int(ws_id), pins, repository_id=repo_filter
        ).to_dict())


# --- domínios (lf-0122, Phase 14 — somente leitura) --------------------
#
# Superfície read-only sobre ``domain_entities`` /
# ``domain_entity_occurrences`` / ``domain_relations`` (schema v9). Todo
# endpoint resolve ``repository_id`` + ``snapshot_id`` pelo mesmo caminho
# dos demais handlers — isolamento de snapshot preservado, nada é
# agregado entre snapshots ou repositórios.

_DOMAIN_PARAM = re.compile(r"^[a-zA-Z0-9_.\-]{1,100}$")
_LINEAGE_KINDS = (
    "data_table", "data_view", "iceberg_table", "data_column",
    "data_transformation",
)
# Tipos que carregam fluxo de dados — a travessia upstream/downstream do
# lineage só segue este vocabulário (READS_FROM/WRITES_TO/TRANSFORMS/
# DERIVES_FROM), nunca arestas estruturais.
_LINEAGE_READ = ("READS_FROM", "DERIVES_FROM")
_LINEAGE_WRITE = ("WRITES_TO", "TRANSFORMS")
_LINEAGE_MAX_NODES = 300


def _csv_filter(raw: str, param: str, max_items: int = 8) -> list[str]:
    """Filtro ``a,b,c`` validado — lista vazia significa "sem filtro"."""
    out: list[str] = []
    for part in (raw or "").split(","):
        value = part.strip()
        if not value:
            continue
        if not _DOMAIN_PARAM.fullmatch(value):
            raise ApiError(
                "bad-param", f"parâmetro {param} inválido: {value!r}")
        out.append(value)
        if len(out) > max_items:
            raise ApiError(
                "bad-param",
                f"parâmetro {param} aceita no máximo {max_items} valores",
            )
    return out


def _in_clause(values: list[str]) -> str:
    return ", ".join("?" for _ in values)


def _domain_entity_dto(row: Any) -> dict[str, Any]:
    try:
        attrs = json.loads(row["attributes"] or "{}")
    except json.JSONDecodeError:
        attrs = {}  # atributo corrompido nunca derruba a leitura
    return {
        "id": row["id"],
        "domain": row["domain"],
        "kind": row["kind"],
        "qualified_name": row["qualified_name"],
        "name": row["name"],
        "catalog": row["catalog"],
        "namespace": row["namespace"],
        "environment": row["environment"],
        "attributes": attrs,
        "occurrences": row["occurrences"],
        "first_occurrence_id": row["first_occurrence_id"],
        "path": row["path"],
        "line": row["line"],
        "extraction_status": row["extraction_status"],
    }


def _domains(server: _AtlasHttpd, conn: Any, p: dict[str, str],
             _b: dict[str, Any] | None) -> dict[str, Any]:
    """Inventário de domínios: kinds presentes + contagens honestas.

    ``entities`` conta as entidades lógicas do repositório (estáveis
    entre snapshots); ``occurrences`` conta as observadas no snapshot
    resolvido — 0 é resposta válida, nunca omitida.
    """
    rid = _repo(conn, p)
    sid = _snapshot(conn, rid, p)
    rows = conn.execute(
        "SELECT de.domain, de.kind, COUNT(DISTINCT de.id) AS entities, "
        "COUNT(deo.id) AS occurrences "
        "FROM domain_entities de "
        "LEFT JOIN domain_entity_occurrences deo "
        "  ON deo.entity_id = de.id AND deo.snapshot_id = ? "
        "WHERE de.repository_id = ? "
        "GROUP BY de.domain, de.kind ORDER BY de.domain, de.kind",
        (sid, rid)).fetchall()
    domains: dict[str, dict[str, Any]] = {}
    for r in rows:
        dom = domains.setdefault(r["domain"], {"domain": r["domain"],
                                               "kinds": []})
        dom["kinds"].append({"kind": r["kind"],
                             "entities": r["entities"],
                             "occurrences": r["occurrences"]})
    items = [domains[d] for d in sorted(domains)]
    return {"repository_id": rid, "snapshot_id": sid,
            "items": items, "total_domains": len(items)}


def _domain_entities(server: _AtlasHttpd, conn: Any, p: dict[str, str],
                     _b: dict[str, Any] | None) -> dict[str, Any]:
    """Entidades de domínio com ocorrência no snapshot — paginado."""
    rid = _repo(conn, p)
    sid = _snapshot(conn, rid, p)
    doms = _csv_filter(p.get("domain", ""), "domain")
    kinds = _csv_filter(p.get("kind", ""), "kind")
    q = p.get("q", "")[:300].strip()
    limit = _int(p, "limit", 100)
    offset = _int(p, "offset", 0, cap=10**9)

    where = ["de.repository_id = ?"]
    args: list[Any] = [sid, rid]
    if doms:
        where.append(f"de.domain IN ({_in_clause(doms)})")
        args += doms
    if kinds:
        where.append(f"de.kind IN ({_in_clause(kinds)})")
        args += kinds
    if q:
        like = ("%" + q.replace("\\", "\\\\").replace("%", "\\%")
                .replace("_", "\\_") + "%")
        where.append("(de.qualified_name LIKE ? ESCAPE '\\' "
                     "OR de.name LIKE ? ESCAPE '\\')")
        args += [like, like]
    where_sql = " AND ".join(where)

    # SQLite garante que colunas "bare" num GROUP BY com MIN() vêm da row
    # do mínimo — path/line/extraction_status descrevem a PRIMEIRA
    # ocorrência da entidade no snapshot, de forma determinística.
    entities_sql = (
        "SELECT de.id, de.domain, de.kind, de.qualified_name, de.name, "
        "de.catalog, de.namespace, de.environment, de.attributes, "
        "COUNT(*) AS occurrences, MIN(deo.id) AS first_occurrence_id, "
        "sf.relative_path AS path, deo.line AS line, "
        "deo.extraction_status AS extraction_status "
        "FROM domain_entities de "
        "JOIN domain_entity_occurrences deo "
        "  ON deo.entity_id = de.id AND deo.snapshot_id = ? "
        "LEFT JOIN source_files sf ON sf.id = deo.source_file_id "
        f"WHERE {where_sql} "
        "GROUP BY de.id "
        "ORDER BY de.domain, de.kind, de.qualified_name, de.id "
        "LIMIT ? OFFSET ?")
    rows = conn.execute(entities_sql, (*args, limit, offset)).fetchall()
    count_sql = (
        "SELECT COUNT(DISTINCT de.id) AS n "
        "FROM domain_entities de "
        "JOIN domain_entity_occurrences deo "
        "  ON deo.entity_id = de.id AND deo.snapshot_id = ? "
        f"WHERE {where_sql}")
    total = conn.execute(count_sql, args).fetchone()["n"]
    return {"repository_id": rid, "snapshot_id": sid,
            "items": [_domain_entity_dto(r) for r in rows],
            "total": total, "limit": limit, "offset": offset,
            "truncated": offset + len(rows) < total}


_DOMAIN_EDGE_SELECT = (
    "SELECT id, snapshot_id, from_entity_occ_id, from_symbol_occ_id, "
    "to_entity_occ_id, to_symbol_occ_id, to_unresolved, type, "
    "resolution_status, confidence_class, resolver, evidence_ids "
    "FROM domain_relations ")

_DOMAIN_OCC_SELECT = (
    "SELECT deo.id AS occ_id, deo.entity_id, deo.extraction_status, "
    "deo.line, de.domain, de.kind AS entity_kind, de.qualified_name, "
    "de.name, sf.relative_path AS path "
    "FROM domain_entity_occurrences deo "
    "JOIN domain_entities de ON de.id = deo.entity_id "
    "LEFT JOIN source_files sf ON sf.id = deo.source_file_id ")


def _hydrate_domain_endpoints(
    conn: Any, sid: int, rows: list[Any]
) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]]]:
    """Resolve os endpoints de um lote de arestas — 2 queries no máximo."""
    ent_ids = sorted(
        {r["from_entity_occ_id"] for r in rows}
        | {r["to_entity_occ_id"] for r in rows} - {None})
    sym_ids = sorted(
        {r["from_symbol_occ_id"] for r in rows}
        | {r["to_symbol_occ_id"] for r in rows} - {None})
    ents: dict[int, dict[str, Any]] = {}
    if ent_ids:
        occ_by_ids_sql = (
            _DOMAIN_OCC_SELECT +
            "WHERE deo.snapshot_id = ? "
            "AND deo.id IN (SELECT value FROM json_each(?))")
        for r in conn.execute(
            occ_by_ids_sql, (sid, json.dumps(ent_ids))).fetchall():
            occ = int(r["occ_id"])
            eid = int(r["entity_id"])
            ents[occ] = {
                "ref": f"de:{eid}",
                "endpoint": "entity",
                "occurrence_id": occ,
                "entity_id": eid,
                "domain": r["domain"],
                "entity_kind": r["entity_kind"],
                "qualified_name": r["qualified_name"],
                "name": r["name"],
                "path": r["path"],
                "line": r["line"],
                "extraction_status": r["extraction_status"],
            }
    syms: dict[int, dict[str, Any]] = {}
    if sym_ids:
        for r in conn.execute(
            _NODES_IN_QUERY, (sid, json.dumps(sym_ids))).fetchall():
            occ = int(r["occurrence_id"])
            node = _row_to_node(r)
            syms[occ] = {
                "ref": node["id"],          # occ:{id}
                "endpoint": "symbol",
                "occurrence_id": occ,
                "qualified_name": node["qname"],
                "kind": node["kind"],
                "language": node["language"],
                "path": node["path"],
            }
    return ents, syms


def _domain_endpoint(
    row: Any, side: str,
    ents: dict[int, dict[str, Any]],
    syms: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """Endpoint de uma aresta — entity | symbol | unresolved | stale.

    ``stale`` declara endpoint persistido fora do snapshot resolvido
    (aresta cross-snapshot auditável) — nunca confundido com resolved.
    """
    eid = row[f"{side}_entity_occ_id"]
    if eid is not None:
        found = ents.get(int(eid))
        if found is not None:
            return found
        return {"ref": f"de_occ:{eid}", "endpoint": "entity",
                "occurrence_id": int(eid), "stale": True}
    oid = row[f"{side}_symbol_occ_id"]
    if oid is not None:
        found = syms.get(int(oid))
        if found is not None:
            return found
        return {"ref": f"occ:{oid}", "endpoint": "symbol",
                "occurrence_id": int(oid), "stale": True}
    if side == "to" and row["to_unresolved"]:
        label = str(row["to_unresolved"])
        return {"ref": f"unresolved:{label}", "endpoint": "unresolved",
                "label": label}
    return {"ref": f"{side}:none", "endpoint": "unknown"}


def _domain_edge_dto(
    row: Any,
    ents: dict[int, dict[str, Any]],
    syms: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    """DTO completo da aresta de domínio — prova, nunca só endpoints."""
    return {
        "id": row["id"],
        "relation_id": row["id"],
        "snapshot_id": row["snapshot_id"],
        "type": row["type"],
        "resolution_status": row["resolution_status"],
        "confidence_class": row["confidence_class"],
        "resolver": row["resolver"],
        "evidence_ids": json.loads(row["evidence_ids"] or "[]"),
        "from": _domain_endpoint(row, "from", ents, syms),
        "to": _domain_endpoint(row, "to", ents, syms),
    }


def _domain_edges_dto(conn: Any, sid: int, rows: list[Any]) -> list[dict[str, Any]]:
    ents, syms = _hydrate_domain_endpoints(conn, sid, rows)
    return [_domain_edge_dto(r, ents, syms) for r in rows]


def _domain_relations(server: _AtlasHttpd, conn: Any, p: dict[str, str],
                      _b: dict[str, Any] | None) -> dict[str, Any]:
    """Arestas de domínio do snapshot — filtro por ``type=a,b``."""
    rid = _repo(conn, p)
    sid = _snapshot(conn, rid, p)
    types = _csv_filter(p.get("type", ""), "type", max_items=20)
    limit = _int(p, "limit", 100)
    offset = _int(p, "offset", 0, cap=10**9)
    where = "WHERE snapshot_id = ?"
    args: list[Any] = [sid]
    if types:
        where += f" AND type IN ({_in_clause(types)})"
        args += types
    edges_sql = _DOMAIN_EDGE_SELECT + where + " ORDER BY id LIMIT ? OFFSET ?"
    rows = conn.execute(edges_sql, (*args, limit, offset)).fetchall()
    count_sql = f"SELECT COUNT(*) AS n FROM domain_relations {where}"
    total = conn.execute(count_sql, args).fetchone()["n"]
    return {"repository_id": rid, "snapshot_id": sid,
            "edges": _domain_edges_dto(conn, sid, rows),
            "total": total, "limit": limit, "offset": offset,
            "truncated": offset + len(rows) < total}


def _domain_entity_neighbors(server: _AtlasHttpd, conn: Any,
                             entity_id: str, p: dict[str, str],
                             ) -> dict[str, Any]:
    """Ego de uma entidade em ``domain_relations`` (por ocorrências)."""
    try:
        eid = int(entity_id)
    except ValueError as exc:
        raise ApiError("bad-param", "id de entidade inválido") from exc
    ent = conn.execute(
        "SELECT id, repository_id, domain, kind, identity_key, "
        "qualified_name, name, catalog, namespace, environment, "
        "attributes FROM domain_entities WHERE id = ?",
        (eid,)).fetchone()
    if ent is None:
        raise ApiError("domain-entity-not-found", "entidade inexistente", 404)
    rid = _repo(conn, p)
    if rid != int(ent["repository_id"]):
        raise ApiError("domain-entity-not-found",
                       "entidade fora do repositório ativo", 404)
    sid = _snapshot(conn, rid, p)
    direction = p.get("direction", "both")
    if direction not in ("in", "out", "both"):
        raise ApiError("bad-param", "direction deve ser in|out|both")
    limit = _int(p, "limit", 100)
    occ_ids = Store(conn).domain_occurrences_for(sid, eid)
    edges: list[Any] = []
    if occ_ids:
        marks = _in_clause([str(o) for o in occ_ids])
        conds: list[str] = []
        args: list[Any] = [sid]
        if direction in ("out", "both"):
            conds.append(f"from_entity_occ_id IN ({marks})")
            args += occ_ids
        if direction in ("in", "both"):
            conds.append(f"to_entity_occ_id IN ({marks})")
            args += occ_ids
        ego_sql = (
            _DOMAIN_EDGE_SELECT +
            f"WHERE snapshot_id = ? AND ({' OR '.join(conds)}) "
            "ORDER BY id LIMIT ?")
        edges = conn.execute(ego_sql, (*args, limit)).fetchall()
    entity_dto = {
        "id": eid, "domain": ent["domain"], "kind": ent["kind"],
        "qualified_name": ent["qualified_name"], "name": ent["name"],
        "catalog": ent["catalog"], "namespace": ent["namespace"],
        "environment": ent["environment"],
        "attributes": json.loads(ent["attributes"] or "{}"),
        "occurrences": len(occ_ids),
    }
    return {"repository_id": rid, "snapshot_id": sid,
            "entity": entity_dto,
            "edges": _domain_edges_dto(conn, sid, edges),
            "truncated": len(edges) == limit}


def _lineage_root(conn: Any, rid: int, table: str) -> Any:
    """Resolve ``?table=`` — id numérico ou qualified_name/name exato."""
    if table.isdigit():
        row = conn.execute(
            "SELECT * FROM domain_entities WHERE id = ? AND repository_id = ?",
            (int(table), rid)).fetchone()
        if row is None:
            raise ApiError("lineage-target-not-found",
                           f"entidade {table} inexistente no repositório", 404)
        return row
    root_sql = (
        "SELECT * FROM domain_entities WHERE repository_id = ? "
        f"AND kind IN ({_in_clause(list(_LINEAGE_KINDS))}) "
        "AND (qualified_name = ? OR name = ?) ORDER BY id")
    rows = conn.execute(
        root_sql, (rid, *_LINEAGE_KINDS, table, table)).fetchall()
    if not rows:
        raise ApiError(
            "lineage-target-not-found",
            f"tabela {table!r} não encontrada — use o id da entidade "
            "ou o qualified_name exato", 404)
    if len(rows) > 1:
        cands = ", ".join(
            f"#{r['id']} {r['qualified_name']}" for r in rows[:5])
        raise ApiError(
            "lineage-target-ambiguous",
            f"{table!r} é ambíguo ({len(rows)} entidades: {cands}) — "
            "refaça com o id da entidade", 409)
    return rows[0]


def _lineage_step(
    conn: Any, sid: int, ent_occs: list[int], sym_occs: list[int],
    upstream: bool,
) -> list[Any]:
    """Um passo de BFS — regras de direção por tipo de aresta.

    upstream (contra o fluxo): ``N READS_FROM X``/``N DERIVES_FROM X``
    → X; ``tr WRITES_TO N``/``tr TRANSFORMS N`` → tr. Downstream é o
    espelho exato. Nada além do vocabulário de dados é atravessado.
    """
    if not ent_occs and not sym_occs:
        return []
    e_json = json.dumps(ent_occs)
    s_json = json.dumps(sym_occs)
    if upstream:
        cond = ("(type IN ('WRITES_TO','TRANSFORMS') AND ("
                "to_entity_occ_id IN (SELECT value FROM json_each(?)) OR "
                "to_symbol_occ_id IN (SELECT value FROM json_each(?)))) "
                "OR (type IN ('READS_FROM','DERIVES_FROM') AND ("
                "from_entity_occ_id IN (SELECT value FROM json_each(?)) OR "
                "from_symbol_occ_id IN (SELECT value FROM json_each(?))))")
    else:
        cond = ("(type IN ('WRITES_TO','TRANSFORMS') AND ("
                "from_entity_occ_id IN (SELECT value FROM json_each(?)) OR "
                "from_symbol_occ_id IN (SELECT value FROM json_each(?)))) "
                "OR (type IN ('READS_FROM','DERIVES_FROM') AND ("
                "to_entity_occ_id IN (SELECT value FROM json_each(?)) OR "
                "to_symbol_occ_id IN (SELECT value FROM json_each(?))))")
    step_sql = (
        _DOMAIN_EDGE_SELECT +
        f"WHERE snapshot_id = ? AND ({cond}) ORDER BY id")
    rows: list[Any] = conn.execute(
        step_sql, (sid, e_json, s_json, e_json, s_json)).fetchall()
    return rows


def _lineage_neighbor(row: Any, upstream: bool) -> tuple[str, Any, str]:
    """Endpoint vizinho de um passo — ``(ref, id, side)``.

    ``side`` diz qual endpoint da aresta é o vizinho: 'from' quando a
    fronteira tocou o ``to``, 'to' quando tocou o ``from``.
    """
    if upstream:
        if row["type"] in _LINEAGE_WRITE:
            return ("entity", row["from_entity_occ_id"], "from") \
                if row["from_entity_occ_id"] is not None else (
                    "symbol", row["from_symbol_occ_id"], "from")
        return ("entity", row["to_entity_occ_id"], "to") \
            if row["to_entity_occ_id"] is not None else (
                "symbol", row["to_symbol_occ_id"], "to")
    if row["type"] in _LINEAGE_WRITE:
        return ("entity", row["to_entity_occ_id"], "to") \
            if row["to_entity_occ_id"] is not None else (
                "symbol", row["to_symbol_occ_id"], "to")
    return ("entity", row["from_entity_occ_id"], "from") \
        if row["from_entity_occ_id"] is not None else (
            "symbol", row["from_symbol_occ_id"], "from")


def _lineage_absorb(
    conn: Any, store: Store, sid: int, row: Any, upstream: bool,
    dist: int, lane: str,
    nodes: dict[str, dict[str, Any]], visited: set[str],
    queue: list[tuple[str, list[int], list[int], int, str]],
) -> bool:
    """Absorve o vizinho de uma aresta — False quando o cap de nós bate."""
    kind, nid, _side = _lineage_neighbor(row, upstream)
    if nid is None:
        # alvo textual não resolvido — entra como nó honesto, nunca expande
        nref = f"unresolved:{row['id']}"
        nodes.setdefault(nref, {
            "ref": nref, "endpoint": "unresolved",
            "label": str(row["to_unresolved"] or ""),
            "distance": dist + 1, "lane": lane})
        return True
    eid: int | None = None
    if kind == "entity":
        # dedup por ENTIDADE — uma entidade pode ter várias ocorrências;
        # o ref público é ``de:{entity_id}``.
        erow = conn.execute(
            "SELECT entity_id FROM domain_entity_occurrences WHERE id = ?",
            (nid,)).fetchone()
        if erow is None:
            return True  # endpoint fora do snapshot — aresta já consta
        eid = int(erow["entity_id"])
        nref = f"de:{eid}"
    else:
        nref = f"occ:{nid}"
    if nref in visited:
        return True
    visited.add(nref)
    if len(visited) > _LINEAGE_MAX_NODES:
        return False
    nodes[nref] = {"ref": nref, "occurrence_id": int(nid),
                   "entity_id": eid, "distance": dist + 1, "lane": lane}
    if kind == "entity" and eid is not None:
        queue.append((nref, store.domain_occurrences_for(sid, eid), [],
                      dist + 1, lane))
    else:
        queue.append((nref, [], [int(nid)], dist + 1, lane))
    return True


def _lineage_bfs(
    conn: Any, sid: int, root: Any, direction: str, depth: int,
    limit: int,
) -> tuple[list[Any], dict[str, dict[str, Any]], bool]:
    """BFS bounded — retorna ``(edge_rows, nodes, truncated)``."""
    store = Store(conn)
    root_ref = f"de:{root['id']}"
    edges_by_id: dict[int, Any] = {}
    nodes: dict[str, dict[str, Any]] = {}
    visited: set[str] = {root_ref}
    truncated = False
    queue: list[tuple[str, list[int], list[int], int, str]] = [
        (root_ref, store.domain_occurrences_for(sid, int(root["id"])),
         [], 0, "root"),
    ]
    while queue and not truncated:
        _ref, ent_occs, sym_occs, dist, _lane = queue.pop(0)
        if dist >= depth:
            continue
        for upstream, lane in ((True, "upstream"), (False, "downstream")):
            if direction != "both" and direction != lane:
                continue
            for row in _lineage_step(
                    conn, sid, ent_occs, sym_occs, upstream):
                if len(edges_by_id) >= limit:
                    truncated = True
                    break
                edges_by_id.setdefault(int(row["id"]), row)
                if not _lineage_absorb(conn, store, sid, row, upstream,
                                       dist, lane, nodes, visited, queue):
                    truncated = True
                    break
            if truncated:
                break
    return ([edges_by_id[k] for k in sorted(edges_by_id)],
            nodes, truncated)


def _domain_lineage(server: _AtlasHttpd, conn: Any, p: dict[str, str],
                    _b: dict[str, Any] | None) -> dict[str, Any]:
    """Linhagem bounded: BFS upstream/downstream sobre arestas de dados."""
    rid = _repo(conn, p)
    sid = _snapshot(conn, rid, p)
    table = p.get("table", "").strip()
    if not table or len(table) > 300:
        raise ApiError("bad-param", "table obrigatório (≤300 chars)")
    direction = p.get("direction", "both")
    if direction not in ("upstream", "downstream", "both"):
        raise ApiError("bad-param",
                       "direction deve ser upstream|downstream|both")
    depth = _int(p, "depth", 4, cap=8)
    limit = _int(p, "limit", 200, cap=500) or 200

    root = _lineage_root(conn, rid, table)
    root_ref = f"de:{root['id']}"
    edge_rows, nodes, truncated = _lineage_bfs(
        conn, sid, root, direction, depth, limit)
    edge_dtos = _domain_edges_dto(conn, sid, edge_rows)

    # enriquece nós entity/symbol com os descritores já resolvidos
    endpoint_by_ref: dict[str, dict[str, Any]] = {}
    for dto in edge_dtos:
        for side in ("from", "to"):
            ep = dto[side]
            if ep["endpoint"] == "entity" and ep.get("entity_id") is not None:
                endpoint_by_ref.setdefault(f"de:{ep['entity_id']}", ep)
            elif (ep["endpoint"] == "symbol"
                  and ep.get("occurrence_id") is not None):
                endpoint_by_ref.setdefault(f"occ:{ep['occurrence_id']}", ep)
    out_nodes: list[dict[str, Any]] = []
    for nref, meta in nodes.items():
        merged = dict(endpoint_by_ref.get(nref, {}))
        merged.update({k: v for k, v in meta.items() if k not in merged
                       or merged.get(k) is None})
        merged["distance"] = meta["distance"]
        merged["lane"] = meta["lane"]
        out_nodes.append(merged)
    lane_rank = {"root": 0, "upstream": 1, "downstream": 2}
    out_nodes.sort(key=lambda n: (
        lane_rank.get(str(n.get("lane")), 9), int(n.get("distance", 0)),
        str(n.get("qualified_name") or n.get("label") or n.get("ref"))))

    return {
        "repository_id": rid, "snapshot_id": sid,
        "root": {
            "ref": root_ref, "endpoint": "entity",
            "entity_id": int(root["id"]), "domain": root["domain"],
            "entity_kind": root["kind"],
            "qualified_name": root["qualified_name"],
            "name": root["name"], "distance": 0, "lane": "root",
        },
        "direction": direction, "depth": depth,
        "nodes": out_nodes, "edges": edge_dtos,
        "truncated": truncated,
    }


_NO_MATCH = object()  # sentinela: rota não é do grupo parametrizado


def _route_get_detail(server: _AtlasHttpd, conn: Any, route: str,
                      p: dict[str, str], body: Any) -> Any:
    """Despacha rotas GET ``/<recurso>/<id>`` — ``_NO_MATCH`` fora do escopo."""
    m = re.fullmatch(r"/repositories/(\d+)/snapshots", route)
    if m:
        return _snapshots(server, conn, m.group(1), body)
    m = re.fullmatch(r"/symbols/(\d+)", route)
    if m:
        return _symbol(server, conn, m.group(1), p)
    m = re.fullmatch(r"/evidence/(\d+)", route)
    if m:
        return _evidence(server, conn, m.group(1), p)
    m = re.fullmatch(r"/files/(\d+)/occurrences", route)
    if m:
        return _file_occurrences(server, conn, m.group(1), p)
    return _NO_MATCH


def _route_domains(server: _AtlasHttpd, conn: Any, route: str,
                   p: dict[str, str]) -> Any:
    """Despacha rotas GET de domínio — ``_NO_MATCH`` fora do escopo."""
    m = re.fullmatch(r"/domains/entities/(\d+)/neighbors", route)
    if m:
        return _domain_entity_neighbors(server, conn, m.group(1), p)
    return _NO_MATCH


def _route_federation(server: _AtlasHttpd, conn: Any, route: str,
                      p: dict[str, str]) -> Any:
    """Despacha rotas GET de federação — ``_NO_MATCH`` fora do escopo."""
    if route == "/federation/workspaces":
        return _fed_workspaces(server, conn, p, None)
    m = re.fullmatch(r"/federation/workspaces/(\d+)/view", route)
    if m:
        return _fed_view(server, conn, m.group(1), p)
    m = re.fullmatch(
        r"/federation/workspaces/(\d+)/entities/([^/]+)/occurrences",
        route)
    if m:
        return _fed_entity_occurrences(
            server, conn, m.group(1), m.group(2), p)
    m = re.fullmatch(r"/federation/workspaces/(\d+)/graph", route)
    if m:
        return _fed_graph(server, conn, m.group(1), p)
    m = re.fullmatch(r"/federation/workspaces/(\d+)/search", route)
    if m:
        return _fed_search(server, conn, m.group(1), p)
    m = re.fullmatch(r"/federation/workspaces/(\d+)", route)
    if m:
        return _fed_workspace(server, conn, m.group(1), p)
    return _NO_MATCH


def handle(server: _AtlasHttpd, method: str, route: str,
           handler: BaseHTTPRequestHandler) -> Any:
    conn = server.connect()
    try:
        p = _qs(handler)
        body = server_body(handler) if method == "POST" else None
        if method == "GET":
            detail = _route_get_detail(server, conn, route, p, body)
            if detail is not _NO_MATCH:
                return detail
            # federação (lf-0118) — rotas GET read-only
            fed_result = _route_federation(server, conn, route, p)
            if fed_result is not _NO_MATCH:
                return fed_result
            # domínios (lf-0122) — rotas GET read-only
            dom_result = _route_domains(server, conn, route, p)
            if dom_result is not _NO_MATCH:
                return dom_result
            simple = {
                "/health": _health, "/capabilities": _capabilities,
                "/repositories": _repositories,
                "/graph/overview": _graph_overview,
                "/graph/neighbors": _graph_neighbors,
                "/search": _search, "/index/status": _index_status,
                "/domains": _domains,
                "/domains/entities": _domain_entities,
                "/domains/relations": _domain_relations,
                "/domains/lineage": _domain_lineage,
            }
            fn = simple.get(route)
            if fn:
                return fn(server, conn, p, body)
        elif method == "POST":
            if route == "/context":
                return _context(server, conn, p, body)
            if route == "/impact/analyze":
                return _impact(server, conn, p, body)
        raise ApiError("not-found", f"{method} {route} inexistente", 404)
    finally:
        conn.close()


def server_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    from code_atlas.api.server import _content_length
    length = _content_length(handler.headers.get("Content-Length"))
    import json
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ApiError("bad-json", "corpo não é JSON válido") from exc
    if not isinstance(data, dict):
        raise ApiError("bad-json", "corpo deve ser objeto JSON")
    return data
