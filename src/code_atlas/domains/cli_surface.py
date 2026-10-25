"""Superfície read-only de consulta de domínio (lf-0123, Cycle 7 Phase 15).

Consultas determinísticas sobre ``domain_entities`` /
``domain_entity_occurrences`` / ``domain_relations`` — camada
compartilhada entre ``cli.py`` e ``mcp.py`` para que os dois
transportes respondam a mesma verdade.

Regras inegociáveis (spec Cycle 7):

- **somente leitura**: nenhuma escrita, nenhum adapter é executado,
  nenhum artefato é relido — só o estado persistido do snapshot;
- **snapshot-scoped**: toda resposta ancora num snapshot resolvido pelo
  caller (isolamento entre repositórios preservado);
- **honestidade de ausência**: adapters são opt-in — zero rows de
  domínio é resposta vazia com nota, nunca erro;
- **bounded**: todo listing/traversal tem ``limit``/``depth`` com teto;
- **determinístico**: ordenação por colunas estáveis — mesma pergunta,
  mesma resposta.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from code_atlas.domains.contracts import DomainAdapter
from code_atlas.domains.registry import DomainRegistry

# Teto de profundidade do traversal de linhagem — mesmo limite duro do
# BFS de ``dependencies`` (lf-0022): bounded por construção.
MAX_LINEAGE_DEPTH = 8

# Relações que carregam fluxo de dados (leitura/escrita/derivação) —
# a linhagem nunca percorre DEPENDS_ON/REFERENCES/CONTAINS etc.
DATA_FLOW_TYPES: frozenset[str] = frozenset(
    {"READS_FROM", "WRITES_TO", "TRANSFORMS", "DERIVES_FROM"}
)

# Tipos cujo endpoint ``to`` é o produtor (fluxo ``to → from``).
_UPSTREAM_TARGET_TYPES: frozenset[str] = frozenset(
    {"READS_FROM", "DERIVES_FROM"}
)

NOTE_OPT_IN = (
    "domain adapters are opt-in: no domain rows means extraction was "
    "not enabled for this index (see `code-atlas index --domains`) — "
    "absence is not an error"
)


def _display_name(row: sqlite3.Row | dict[str, Any]) -> str:
    """``namespace.qualified_name`` quando há namespace — forma humana."""
    qname = str(row["qualified_name"])
    ns = str(row["namespace"] or "")
    return f"{ns}.{qname}" if ns else qname


# --- registro built-in -------------------------------------------------


def builtin_adapter_factories(
    root: Path | None = None,
) -> dict[str, Callable[[], DomainAdapter]]:
    """``domain_id → fábrica nullary`` dos adapters built-in.

    ``root`` vincula o adapter ao workspace indexado (``base_path`` /
    ``workspace_root``). Para listagem (``domains list``) qualquer path
    serve — os adapters só leem artefatos dentro de ``extract`` e a
    listagem nunca extrai.
    """
    from code_atlas.domains.api import ApiContractAdapter
    from code_atlas.domains.aws import AwsDataAdapter
    from code_atlas.domains.cicd import GitHubActionsAdapter
    from code_atlas.domains.spark import PySparkAdapter
    from code_atlas.domains.sql import SqlDomainAdapter
    from code_atlas.domains.terraform import TerraformAdapter

    base = root if root is not None else Path(".")
    return {
        "api": lambda: ApiContractAdapter(workspace_root=base),
        "aws-data": lambda: AwsDataAdapter(base_path=base),
        "cicd": lambda: GitHubActionsAdapter.for_workspace(base),
        "pyspark": lambda: PySparkAdapter(base_path=base),
        "sql": lambda: SqlDomainAdapter(base_path=base),
        "terraform": lambda: TerraformAdapter(base_path=base),
    }


def builtin_registry(root: Path | None = None) -> DomainRegistry:
    """Registry com todos os adapters built-in habilitados."""
    reg = DomainRegistry()
    for name in sorted(builtin_adapter_factories(root)):
        reg.register(builtin_adapter_factories(root)[name]())
    return reg


def select_domain_registry(
    spec: str, root: Path | None = None
) -> tuple[DomainRegistry | None, str]:
    """``--domains sql,pyspark``/``all`` → registry ou erro tipado.

    Ids desconhecidos são recusados (nunca flag que silenciosamente não
    faz nada — mesma disciplina de ``--resolver``). ``all`` expande o
    conjunto registrado; combinar ``all`` com ids explícitos é erro.
    """
    factories = builtin_adapter_factories(root)
    names = [n.strip() for n in spec.split(",") if n.strip()]
    if not names:
        return None, "--domains: empty selection"
    if "all" in names:
        if len(names) > 1:
            return None, "--domains: 'all' cannot be combined with ids"
        names = sorted(factories)
    unknown = [n for n in names if n not in factories]
    if unknown:
        return None, (
            f"--domains: unknown domain(s) "
            f"{', '.join(sorted(set(unknown)))} "
            f"(registered: {', '.join(sorted(factories))})"
        )
    reg = DomainRegistry()
    for n in dict.fromkeys(names):  # dedup preservando ordem
        reg.register(factories[n]())
    return reg, ""


def adapters_report(reg: DomainRegistry | None = None) -> list[dict[str, Any]]:
    """Inventário dos adapters registrados (``domains list``/``atlas_domains``)."""
    reg = reg or builtin_registry()
    out: list[dict[str, Any]] = []
    for a in sorted(
        reg.adapters(), key=lambda x: (x.domain_id, x.adapter_version)
    ):
        sup = a.supported_artifacts()
        cm = a.confidence_model()
        out.append(
            {
                "domain_id": a.domain_id,
                "adapter_version": a.adapter_version,
                "enabled": reg.is_enabled(a.domain_id),
                "versions": list(reg.versions(a.domain_id)),
                "artifact_support": {
                    "extensions": sorted(sup.extensions),
                    "globs": list(sup.globs),
                },
                "entity_kinds": sorted(str(k) for k in a.entity_kinds()),
                "relation_kinds": sorted(a.relation_kinds()),
                "confidence": {
                    "ceiling": str(cm.ceiling),
                    "default": str(cm.default),
                    "allows_inferred": cm.allows_inferred,
                },
            }
        )
    return out


# --- resolução de repositório/entidade ---------------------------------


def repo_for_ident(conn: sqlite3.Connection, ident: str) -> tuple[int | None, str]:
    """Deriva ``repository_id`` da própria referência de entidade.

    - id numérico → ``repository_id`` da row;
    - chave ``dom:…`` → segmento ``repository`` decodificado →
      ``repositories.identity_key``;
    - nome nu → único repo que contém a entidade; múltiplos → erro de
      ambiguidade honesto (o caller pede ``--repo``).

    ``(None, "")`` = não derivável — o caller cai no ``--root`` default.
    """
    ident = ident.strip()
    if not ident:
        return None, ""
    if ident.isdigit():
        row = conn.execute(
            "SELECT repository_id FROM domain_entities WHERE id = ?",
            (int(ident),),
        ).fetchone()
        return (int(row["repository_id"]) if row else None), ""
    if ident.startswith("dom:"):
        from code_atlas.domains.identity import DomainIdentity

        segs = DomainIdentity.parse_key(ident)
        if segs is not None and len(segs) >= 2:
            row = conn.execute(
                "SELECT id FROM repositories WHERE identity_key = ?",
                (segs[1],),
            ).fetchone()
            if row is not None:
                return int(row["id"]), ""
        return None, ""
    rows = conn.execute(
        "SELECT DISTINCT repository_id FROM domain_entities "
        "WHERE qualified_name = ? OR name = ? "
        "OR (namespace || '.' || qualified_name) = ? ORDER BY repository_id",
        (ident, ident, ident),
    ).fetchall()
    if len(rows) == 1:
        return int(rows[0]["repository_id"]), ""
    if len(rows) > 1:
        repos = ", ".join(str(int(r["repository_id"])) for r in rows)
        return None, (
            f"entity ref {ident!r} exists in repositories {repos} — "
            "pass --repo to disambiguate"
        )
    return None, ""


def find_entities(
    conn: sqlite3.Connection, repo_id: int | None, ident: str
) -> list[sqlite3.Row]:
    """Candidatos por id numérico, ``identity_key`` ou nome qualificado.

    Nome aceita ``qualified_name``, ``name`` e a forma humana
    ``namespace.qualified_name`` — nunca substring solta (lookup por
    substring fabricaria candidatos).
    """
    ident = ident.strip()
    conds: list[str] = []
    params: list[Any] = []
    if repo_id is not None:
        conds.append("repository_id = ?")
        params.append(repo_id)
    if ident.isdigit():
        conds.append("id = ?")
        params.append(int(ident))
    elif ident.startswith("dom:"):
        conds.append("identity_key = ?")
        params.append(ident)
    else:
        conds.append(
            "(qualified_name = ? OR name = ? "
            "OR (namespace || '.' || qualified_name) = ?)"
        )
        params.extend([ident, ident, ident])
    where = " AND ".join(conds) if conds else "1=1"
    entity_sql = (
        "SELECT * FROM domain_entities WHERE " + where + " ORDER BY id"
    )
    return conn.execute(entity_sql, params).fetchall()


def resolve_single_entity(
    conn: sqlite3.Connection, repo_id: int | None, ident: str
) -> tuple[sqlite3.Row | None, str]:
    """Exatamente um candidato — ausência e ambiguidade são honestas."""
    rows = find_entities(conn, repo_id, ident)
    if not rows:
        scope = "" if repo_id is None else f" in repository {repo_id}"
        return None, (
            f"domain entity not found: {ident!r}{scope} — {NOTE_OPT_IN}"
        )
    if len(rows) > 1:
        cand = ", ".join(
            f"#{int(r['id'])} {_display_name(r)}"
            f" (repo={int(r['repository_id'])})"
            for r in rows[:10]
        )
        return None, f"ambiguous entity ref {ident!r}: {cand}"
    return rows[0], ""


# --- listagem -----------------------------------------------------------


def list_entities(
    conn: sqlite3.Connection,
    repo_id: int,
    snapshot_id: int,
    *,
    domain: str = "",
    kind: str = "",
    q: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Entidades com ≥1 ocorrência no snapshot — listagem determinística."""
    from code_atlas.search.structural import _escape_like

    conds = ["deo.snapshot_id = ?", "de.repository_id = ?"]
    params: list[Any] = [snapshot_id, repo_id]
    if domain:
        conds.append("de.domain = ?")
        params.append(domain)
    if kind:
        conds.append("de.kind = ?")
        params.append(kind)
    if q:
        like = f"%{_escape_like(q)}%"
        conds.append(
            "(de.qualified_name LIKE ? ESCAPE '\\' "
            "OR de.name LIKE ? ESCAPE '\\' "
            "OR (de.namespace || '.' || de.qualified_name) "
            "LIKE ? ESCAPE '\\')"
        )
        params.extend([like, like, like])
    where = " AND ".join(conds)
    join = (
        "FROM domain_entity_occurrences deo "
        "JOIN domain_entities de ON de.id = deo.entity_id "
        "LEFT JOIN source_files sf ON sf.id = deo.source_file_id "
        f"WHERE {where}"
    )
    count_sql = "SELECT COUNT(DISTINCT de.id) AS c " + join
    total = int(
        conn.execute(count_sql, params).fetchone()["c"]
    )
    list_sql = (
        "SELECT de.id, de.domain, de.kind, de.qualified_name, de.name,"
        " de.catalog, de.namespace, de.environment, de.identity_key,"
        " COUNT(deo.id) AS occ_count,"
        " MIN(sf.relative_path) AS first_path "
        + join
        + " GROUP BY de.id"
        " ORDER BY de.domain, de.kind, de.qualified_name, de.id"
        " LIMIT ? OFFSET ?"
    )
    rows = conn.execute(
        list_sql,
        [*params, limit, offset],
    ).fetchall()
    items = [
        {
            "entity_id": int(r["id"]),
            "domain": r["domain"],
            "kind": r["kind"],
            "qualified_name": r["qualified_name"],
            "display_name": _display_name(r),
            "name": r["name"],
            "catalog": r["catalog"],
            "namespace": r["namespace"],
            "environment": r["environment"],
            "identity_key": r["identity_key"],
            "occurrences": int(r["occ_count"]),
            "path": r["first_path"],
        }
        for r in rows
    ]
    return {
        "total": total,
        "truncated": (offset + len(items)) < total,
        "items": items,
    }


# --- detalhe --------------------------------------------------------------


def entity_detail(
    conn: sqlite3.Connection, entity: sqlite3.Row, snapshot_id: int
) -> dict[str, Any]:
    """Cartão da entidade: identidade, ocorrências, relações, evidência."""
    entity_id = int(entity["id"])
    occ_rows = conn.execute(
        "SELECT deo.id, deo.line, deo.col, deo.extraction_status,"
        " sf.relative_path AS path"
        " FROM domain_entity_occurrences deo"
        " LEFT JOIN source_files sf ON sf.id = deo.source_file_id"
        " WHERE deo.entity_id = ? AND deo.snapshot_id = ?"
        " ORDER BY deo.id",
        (entity_id, snapshot_id),
    ).fetchall()
    occ_ids = [int(r["id"]) for r in occ_rows]
    occurrences = [
        {
            "occurrence_id": int(r["id"]),
            "path": r["path"],
            "line": r["line"],
            "col": r["col"],
            "extraction_status": r["extraction_status"],
        }
        for r in occ_rows
    ]

    summary: dict[tuple[str, str, str], int] = {}
    evidence_count = 0
    if occ_ids:
        ph = ",".join("?" for _ in occ_ids)
        for direction, col in (
            ("out", "from_entity_occ_id"),
            ("in", "to_entity_occ_id"),
        ):
            rel_sql = (
                "SELECT type, resolution_status, COUNT(*) AS c"
                " FROM domain_relations"
                f" WHERE snapshot_id = ? AND {col} IN ({ph})"
                " GROUP BY type, resolution_status"
                " ORDER BY type, resolution_status"
            )
            rows = conn.execute(
                rel_sql,
                [snapshot_id, *occ_ids],
            ).fetchall()
            for r in rows:
                key = (direction, r["type"], r["resolution_status"])
                summary[key] = summary.get(key, 0) + int(r["c"])
        ev_sql = (
            "SELECT COUNT(DISTINCT j.value) AS c"
            " FROM domain_relations r, json_each(r.evidence_ids) j"
            f" WHERE r.snapshot_id = ? AND (r.from_entity_occ_id IN ({ph})"
            f" OR r.to_entity_occ_id IN ({ph}))"
        )
        evidence_count = int(
            conn.execute(
                ev_sql,
                [snapshot_id, *occ_ids, *occ_ids],
            ).fetchone()["c"]
        )
    relations = [
        {"direction": d, "type": t, "status": s, "count": c}
        for (d, t, s), c in sorted(summary.items())
    ]
    try:
        attributes = json.loads(entity["attributes"] or "{}")
    except (TypeError, json.JSONDecodeError):
        attributes = {}
    return {
        "entity_id": entity_id,
        "repository_id": int(entity["repository_id"]),
        "identity": {
            "domain": entity["domain"],
            "kind": entity["kind"],
            "qualified_name": entity["qualified_name"],
            "display_name": _display_name(entity),
            "name": entity["name"],
            "catalog": entity["catalog"],
            "namespace": entity["namespace"],
            "environment": entity["environment"],
            "identity_key": entity["identity_key"],
        },
        "attributes": attributes,
        "snapshot_id": snapshot_id,
        "occurrences": occurrences,
        "relations_summary": relations,
        "evidence_count": evidence_count,
    }


# --- linhagem ---------------------------------------------------------------


def _entity_label_map(
    conn: sqlite3.Connection, entity_ids: set[int]
) -> dict[int, sqlite3.Row]:
    if not entity_ids:
        return {}
    ph = ",".join("?" for _ in entity_ids)
    label_sql = (
        "SELECT id, domain, kind, qualified_name, name, catalog,"
        " namespace, environment FROM domain_entities"
        f" WHERE id IN ({ph})"
    )
    rows = conn.execute(label_sql, sorted(entity_ids)).fetchall()
    return {int(r["id"]): r for r in rows}


def _node_key(
    entity_id: int | None, symbol: str | None, unresolved: str | None
) -> str:
    """Chave do nó no traversal — entidade, símbolo ou alvo honesto."""
    if entity_id is not None:
        return f"e:{entity_id}"
    if symbol:
        return f"symbol:{symbol}"
    if unresolved:
        return f"unresolved:{unresolved}"
    return "missing:endpoint"  # endpoint órfão — nunca inventado


def _flow_edges(
    conn: sqlite3.Connection, snapshot_id: int
) -> tuple[list[dict[str, Any]], set[int]]:
    """Arestas de fluxo normalizadas ``producer → consumer`` do snapshot.

    ``READS_FROM``/``DERIVES_FROM`` fluem ``to → from`` (a origem lê ou
    deriva do alvo); ``WRITES_TO``/``TRANSFORMS`` fluem ``from → to``
    (a origem escreve/muta o alvo). Endpoints de símbolo e alvos
    ``unresolved`` viram nós-folha declarados — nunca atravessados.
    """
    ph = ",".join("?" for _ in DATA_FLOW_TYPES)
    edges_sql = (
        "SELECT r.id, r.type, r.resolution_status, r.resolver,"
        " r.from_entity_occ_id, r.to_entity_occ_id, r.to_unresolved,"
        " fe.entity_id AS from_entity_id, te.entity_id AS to_entity_id,"
        " fs.qualified_name AS from_symbol,"
        " ts.qualified_name AS to_symbol"
        " FROM domain_relations r"
        " LEFT JOIN domain_entity_occurrences fe"
        "   ON fe.id = r.from_entity_occ_id"
        " LEFT JOIN domain_entity_occurrences te"
        "   ON te.id = r.to_entity_occ_id"
        " LEFT JOIN symbol_occurrences fso"
        "   ON fso.id = r.from_symbol_occ_id"
        " LEFT JOIN symbols fs ON fs.id = fso.logical_symbol_id"
        " LEFT JOIN symbol_occurrences tso"
        "   ON tso.id = r.to_symbol_occ_id"
        " LEFT JOIN symbols ts ON ts.id = tso.logical_symbol_id"
        f" WHERE r.snapshot_id = ? AND r.type IN ({ph})"
        " ORDER BY r.id"
    )
    rows = conn.execute(
        edges_sql,
        [snapshot_id, *sorted(DATA_FLOW_TYPES)],
    ).fetchall()
    edges: list[dict[str, Any]] = []
    entity_ids: set[int] = set()
    for r in rows:
        fe = r["from_entity_id"]
        te = r["to_entity_id"]
        frm = _node_key(
            int(fe) if fe is not None else None,
            r["from_symbol"],
            None,
        )
        to = _node_key(
            int(te) if te is not None else None,
            r["to_symbol"],
            r["to_unresolved"],
        )
        if fe is not None:
            entity_ids.add(int(fe))
        if te is not None:
            entity_ids.add(int(te))
        # normalização do fluxo: upstream (producer) → downstream (consumer)
        if r["type"] in _UPSTREAM_TARGET_TYPES:
            u, v = to, frm  # alvo alimenta a origem
            ue, ve = te, fe
        else:
            u, v = frm, to
            ue, ve = fe, te
        edges.append(
            {
                "u": u,
                "v": v,
                "u_entity": int(ue) if ue is not None else None,
                "v_entity": int(ve) if ve is not None else None,
                "relation_id": int(r["id"]),
                "type": r["type"],
                "resolution_status": r["resolution_status"],
                "resolver": r["resolver"],
            }
        )
    return edges, entity_ids


def _lineage_bfs(
    seed_key: str,
    up_adj: dict[str, list[dict[str, Any]]],
    down_adj: dict[str, list[dict[str, Any]]],
    *,
    direction: str,
    depth: int,
    limit: int,
    label: Callable[[str], str],
) -> tuple[dict[str, int], dict[str, str], list[dict[str, Any]], bool]:
    """BFS bounded sobre adjacências de fluxo — ciclo-safe e ordenado.

    Retorna ``(node_depth, node_via, edges, truncated)``. Só nós de
    entidade (``e:<id>``) propagam — folhas ``symbol:``/``unresolved:``/
    ``missing:`` são terminais declarados, nunca atravessados.
    """
    node_depth: dict[str, int] = {seed_key: 0}
    node_via: dict[str, str] = {seed_key: "seed"}
    out_edges: list[dict[str, Any]] = []
    truncated = False
    # a direção do seed é o próprio ``direction`` — ``up`` nunca expande
    # downstream no primeiro hop nem nos seguintes (via propaga).
    frontier = [(seed_key, direction)]
    for d in range(1, depth + 1):
        candidates = _bfs_candidates(frontier, up_adj, down_adj, label)
        next_frontier, hit = _bfs_visit(
            candidates, node_depth, node_via, out_edges,
            limit=limit, label=label, depth_now=d)
        truncated = truncated or hit
        frontier = next_frontier
        if truncated:
            break
    return node_depth, node_via, out_edges, truncated


def _bfs_candidates(
    frontier: list[tuple[str, str]],
    up_adj: dict[str, list[dict[str, Any]]],
    down_adj: dict[str, list[dict[str, Any]]],
    label: Callable[[str], str],
) -> list[tuple[str, dict[str, Any], str]]:
    """Vizinhos do nível — ordenação determinística por (label, rel_id)."""
    candidates: list[tuple[str, dict[str, Any], str]] = []
    for node, dirn in frontier:
        if dirn in ("up", "both"):
            for e in up_adj.get(node, ()):
                candidates.append((e["u"], e, "up"))
        if dirn in ("down", "both"):
            for e in down_adj.get(node, ()):
                candidates.append((e["v"], e, "down"))
    candidates.sort(key=lambda c: (label(c[0]), c[1]["relation_id"]))
    return candidates


def _bfs_visit(
    candidates: list[tuple[str, dict[str, Any], str]],
    node_depth: dict[str, int],
    node_via: dict[str, str],
    out_edges: list[dict[str, Any]],
    *,
    limit: int,
    label: Callable[[str], str],
    depth_now: int,
) -> tuple[list[tuple[str, str]], bool]:
    """Visita um nível: registra nós/arestas novos, propaga entidades."""
    next_frontier: list[tuple[str, str]] = []
    truncated = False
    for nkey, edge, via in candidates:
        if len(node_depth) >= limit and nkey not in node_depth:
            truncated = True
            continue
        first = nkey not in node_depth
        if first:
            node_depth[nkey] = depth_now
            node_via[nkey] = via
        # aresta reportada quando o par (rel, nó-extremo) é novo
        if nkey.startswith("e:") or first:
            entry = {
                "from": label(edge["u"]),
                "to": label(edge["v"]),
                "from_entity_id": edge["u_entity"],
                "to_entity_id": edge["v_entity"],
                "type": edge["type"],
                "resolution_status": edge["resolution_status"],
                "resolver": edge["resolver"],
                "relation_id": edge["relation_id"],
                "depth": depth_now,
                "via": via,
            }
            if entry not in out_edges:
                out_edges.append(entry)
        if first and nkey.startswith("e:"):
            next_frontier.append((nkey, via))
    return next_frontier, truncated


def _lineage_nodes(
    node_depth: dict[str, int],
    node_via: dict[str, str],
    labels: dict[int, sqlite3.Row],
    label: Callable[[str], str],
) -> list[dict[str, Any]]:
    """Nós do traversal ordenados por (depth, label) — determinístico."""
    nodes: list[dict[str, Any]] = []
    for k in sorted(node_depth, key=lambda k: (node_depth[k], label(k))):
        node: dict[str, Any] = {
            "ref": label(k),
            "entity_id": int(k[2:]) if k.startswith("e:") else None,
            "synthetic": (
                None if k.startswith("e:")
                else k.split(":", 1)[0]  # symbol|unresolved|missing
            ),
            "depth": node_depth[k],
            "via": node_via[k],
            "kind": None,
            "domain": None,
        }
        if k.startswith("e:") and int(k[2:]) in labels:
            row = labels[int(k[2:])]
            node["kind"] = row["kind"]
            node["domain"] = row["domain"]
        nodes.append(node)
    return nodes


def lineage(
    conn: sqlite3.Connection,
    entity: sqlite3.Row,
    snapshot_id: int,
    *,
    direction: str = "both",
    depth: int = 2,
    limit: int = 500,
) -> dict[str, Any]:
    """Traversal BFS limitado de fluxo de dados a partir da entidade.

    ``direction``: ``up`` (o que alimenta a entidade — produtores),
    ``down`` (o que a entidade alimenta — consumidores) ou ``both``.
    Ciclo-safe (visited) e bounded (``depth`` + ``limit``); nós-folha
    ``symbol:``/``unresolved:``/``missing:`` são declarados, nunca
    atravessados.
    """
    direction = direction if direction in ("up", "down", "both") else "both"
    depth = max(1, min(int(depth), MAX_LINEAGE_DEPTH))
    limit = max(1, int(limit))
    edges, entity_ids = _flow_edges(conn, snapshot_id)
    seed = int(entity["id"])
    entity_ids.add(seed)
    labels = _entity_label_map(conn, entity_ids)

    def label(key: str) -> str:
        if key.startswith("e:"):
            row = labels.get(int(key[2:]))
            return _display_name(row) if row is not None else key
        return key

    up_adj: dict[str, list[dict[str, Any]]] = {}
    down_adj: dict[str, list[dict[str, Any]]] = {}
    for e in edges:
        up_adj.setdefault(e["v"], []).append(e)   # u alimenta v
        down_adj.setdefault(e["u"], []).append(e)

    node_depth, node_via, out_edges, truncated = _lineage_bfs(
        f"e:{seed}", up_adj, down_adj,
        direction=direction, depth=depth, limit=limit, label=label)
    nodes = _lineage_nodes(node_depth, node_via, labels, label)
    out_edges.sort(key=lambda e: (e["depth"], e["relation_id"], e["via"]))
    return {
        "entity": {
            "entity_id": seed,
            "qualified_name": entity["qualified_name"],
            "display_name": _display_name(entity),
            "kind": entity["kind"],
            "domain": entity["domain"],
            "identity_key": entity["identity_key"],
        },
        "snapshot_id": snapshot_id,
        "direction": direction,
        "depth": depth,
        "limit": limit,
        "truncated": truncated,
        "nodes": nodes,
        "edges": out_edges,
        "upstream": [
            n for n in nodes if n["via"] in ("up",)
        ],
        "downstream": [
            n for n in nodes if n["via"] in ("down",)
        ],
    }
