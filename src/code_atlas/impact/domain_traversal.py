"""Travessia de domínio no Impact Engine (lf-0120, Cycle 7 Phase 12).

Responde a "se esta tabela/job/recurso mudar, quem é afetado?": BFS de
blast sobre o grafo **misto** — arestas de código (``relations``,
dependente em ``from``) mais arestas de domínio
(``domain_relations``, endpoints entidade-ou-símbolo) — tudo no
**mesmo snapshot** (isolamento por repositório+snapshot é
intransponível: endpoints de outro snapshot nunca são atravessados).

Semântica de direção — uma aresta só propaga mudança na direção do
dependente:

- **alvo dependido** (``from`` depende de ``to``): ``READS_FROM``,
  ``DERIVES_FROM``, ``DEPENDS_ON``, ``CALLS_ENDPOINT``,
  ``IMPLEMENTS_CONTRACT``, ``USES_RESOURCE``, ``ASSUMES_ROLE``,
  ``TESTS``, ``REFERENCES`` (não-identidade), ``IMPORTS``, ``CALLS``,
  ``IMPLEMENTS``, ``EXTENDS``, ``CONFIGURES``, ``CONTAINS`` — atravessa
  ``to → from`` (mudança no alvo impacta o consumidor/agregado);
- **acoplados** (``from`` produz/muta/declara ``to``): ``WRITES_TO``,
  ``TRANSFORMS``, ``PROVISIONS``, ``TRIGGERS``, ``DEPLOYS``,
  ``DEFINES`` — atravessa nos dois sentidos: o produto é afetado pelo
  produtor (``from → to``) e o produtor depende do estado do produto
  (``to → from`` — job que só *escreve* numa tabela quebra quando o
  schema dela muda);
- **identidade simétrica**: ``REFERENCES`` entidade↔entidade com mesmo
  ``root``/``catalog``/``namespace``/``environment``/``qualified_name``
  (o par cross-domain de ``crossresolve`` — "a direção não carrega
  semântica") atravessa nos dois sentidos: é a *mesma* coisa lógica
  observada por dois adapters.

Estados honestos (spec): ``resolution_status`` de cada hop vai para
``ImpactPath.resolution_statuses``; um hop não-``resolved`` rebaixa a
cadeia para ``possible`` — nunca promove. Alvos ``to_unresolved``
(``unresolved``/``ambiguous``/``external``) não têm endpoint: contam
como **incerteza** do nó (``uncertain_deps`` + ``unknown_coverage``
agregada), nunca como blast confirmado.

Seeds: ``resolve_seeds`` aceita ``identity_key`` ``dom:…``, row id de
``domain_entities`` (``entity:N``/``id:N``/``N``), ``display()``
(``pyspark:staging.orders``) ou nome qualificado (``staging.orders``,
``orders``). ``ImpactRequest.entity`` é o canal explícito;
``ImpactRequest.symbol`` casa as mesmas formas **sem diagnóstico de
ausência** — é a fiação mínima que permite a handlers existentes
(API ``_impact``, CLI ``impact --ident``, MCP ``atlas_impact``) aceitar
seeds de entidade sem mudança de assinatura: passem o campo ``symbol``
(ou ``ident``) com a ref de entidade. Para expor ``entity`` nas
superfícies basta repassar o parâmetro homônimo — o engine já o lê.
"""

from __future__ import annotations

import re
import sqlite3
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

from code_atlas.domains.identity import DomainIdentity
from code_atlas.impact.contracts import (
    EvidenceStrength,
    ImpactCandidate,
    ImpactDiagnostic,
    ImpactKind,
    ImpactLimits,
    ImpactPath,
)

# ``from`` depende de ``to`` → mudança em ``to`` impacta ``from``.
_DEPENDS_ON_TO: frozenset[str] = frozenset(
    {
        "READS_FROM", "DERIVES_FROM", "DEPENDS_ON", "CALLS_ENDPOINT",
        "IMPLEMENTS_CONTRACT", "USES_RESOURCE", "ASSUMES_ROLE",
        "REFERENCES", "TESTS", "IMPORTS", "CALLS", "IMPLEMENTS",
        "EXTENDS", "CONFIGURES", "CONTAINS",
    }
)

# ``from`` produz/muta/declara ``to`` — acoplamento nos dois sentidos:
# o produto é afetado por mudança no produtor (writer que muda altera o
# conteúdo da tabela) E o produtor depende do estado do produto (escrever
# numa tabela cujo schema mudou quebra; recurso drifted exige mexer no
# terraform que o declara). TRIGGERS só atravessa quando o alvo resolve
# (o cicd `workflow TRIGGERS evento` é sempre ``to_unresolved``).
_COUPLED: frozenset[str] = frozenset(
    {
        "WRITES_TO", "TRANSFORMS", "PROVISIONS", "TRIGGERS", "DEPLOYS",
        "DEFINES",
    }
)

_REFERENCES = "REFERENCES"

_IN_SQL = (
    "SELECT id, from_id, to_id, type, resolution_status"
    " FROM relations WHERE snapshot_id = ? AND to_id = ?"
    " ORDER BY id LIMIT ?"
)
_SYM_SQL = (
    "SELECT s.qualified_name, s.language, s.kind, o.id AS occ_id,"
    " o.snapshot_id AS snap, f.relative_path FROM symbol_occurrences o"
    " JOIN symbols s ON s.id = o.logical_symbol_id"
    " JOIN source_files f ON f.id = o.source_file_id"
    " WHERE o.id = ?"
)
# Todas as rows de domain_relations que tocam o nó (qualquer endpoint).
# Os JOINs trazem o snapshot de cada endpoint — prova local de que o
# vizinho vive no mesmo snapshot (guarda anti-federal do spec).
_DOM_SQL = (
    "SELECT dr.id, dr.type, dr.resolution_status,"
    " dr.from_entity_occ_id, dr.from_symbol_occ_id,"
    " dr.to_entity_occ_id, dr.to_symbol_occ_id, dr.to_unresolved,"
    " fe.snapshot_id AS fe_snap, fe_de.identity_key AS fe_key,"
    " fs.snapshot_id AS fs_snap,"
    " te.snapshot_id AS te_snap, te_de.identity_key AS te_key,"
    " ts.snapshot_id AS ts_snap"
    " FROM domain_relations dr"
    " LEFT JOIN domain_entity_occurrences fe"
    "        ON fe.id = dr.from_entity_occ_id"
    " LEFT JOIN domain_entities fe_de ON fe_de.id = fe.entity_id"
    " LEFT JOIN symbol_occurrences fs ON fs.id = dr.from_symbol_occ_id"
    " LEFT JOIN domain_entity_occurrences te"
    "        ON te.id = dr.to_entity_occ_id"
    " LEFT JOIN domain_entities te_de ON te_de.id = te.entity_id"
    " LEFT JOIN symbol_occurrences ts ON ts.id = dr.to_symbol_occ_id"
    " WHERE dr.snapshot_id = ? AND ("
    "   dr.from_entity_occ_id = ? OR dr.from_symbol_occ_id = ? OR"
    "   dr.to_entity_occ_id = ? OR dr.to_symbol_occ_id = ?)"
    " ORDER BY dr.id LIMIT ?"
)
_ENT_SQL = (
    "SELECT deo.id AS occ_id, deo.snapshot_id AS snap,"
    " de.identity_key, de.domain, de.kind, de.qualified_name,"
    " de.catalog, de.namespace, de.environment,"
    " sf.relative_path"
    " FROM domain_entity_occurrences deo"
    " JOIN domain_entities de ON de.id = deo.entity_id"
    " LEFT JOIN source_files sf ON sf.id = deo.source_file_id"
    " WHERE deo.id = ?"
)

_ID_REF_RE = re.compile(r"(?:entity|id):?(\d+)")


# Um nó do grafo misto: ("s", occ) = ocorrência de símbolo,
# ("e", occ) = ocorrência de entidade de domínio.
_Node = tuple[str, int]


@dataclass(frozen=True)
class _Hit:
    """Nó atingido — forma unificada símbolo/entidade."""

    node: _Node
    key: str            # symbol_key (lang:kind:qname) ou identity_key
    qname: str          # qualified_name ou display() da entidade
    language: str       # linguagem ou domínio produtor
    kind: str           # kind de símbolo ou DomainEntityKind
    path: str           # path do artefato ("" quando sem arquivo)
    is_domain: bool


@dataclass(frozen=True)
class SeedResolution:
    """Seeds de entidade resolvidos contra um par de snapshots.

    ``*_origins`` mapeiam ``identity_key`` → id de ocorrência no
    respectivo snapshot — mesmo desenho dos ``origins`` de símbolo do
    blast de código.
    """

    target_origins: Mapping[str, int]
    base_origins: Mapping[str, int]
    diagnostics: tuple[ImpactDiagnostic, ...]


@dataclass(frozen=True)
class BlastOutcome:
    """Saída do blast misto — candidatos + métricas de incerteza."""

    candidates: tuple[ImpactCandidate, ...]
    truncated: bool
    # status → nº de alvos não-traversáveis nos nós expandidos
    uncertain: Mapping[str, int] = field(default_factory=dict)
    # nº de vizinhos pulados por viverem fora do snapshot analisado
    cross_snapshot: int = 0


def _sym_hit(conn: sqlite3.Connection, occ_id: int) -> _Hit | None:
    row = conn.execute(_SYM_SQL, (occ_id,)).fetchone()
    if row is None:
        return None
    key = f"{row['language']}:{row['kind']}:{row['qualified_name']}"
    return _Hit(
        ("s", int(row["occ_id"])), key, str(row["qualified_name"]),
        str(row["language"]), str(row["kind"]),
        str(row["relative_path"]), False)


def _display_of(row: sqlite3.Row) -> str:
    head = ".".join(
        p for p in (str(row["catalog"] or ""),
                    str(row["namespace"] or "")) if p)
    qn = (f"{head}.{row['qualified_name']}" if head
          else str(row["qualified_name"]))
    env = f"@{row['environment']}" if row["environment"] else ""
    return f"{row['domain']}:{qn}{env}"


def _ent_hit(conn: sqlite3.Connection, occ_id: int) -> _Hit | None:
    row = conn.execute(_ENT_SQL, (occ_id,)).fetchone()
    if row is None:
        return None
    return _Hit(
        ("e", int(row["occ_id"])), str(row["identity_key"]),
        _display_of(row), str(row["domain"]), str(row["kind"]),
        str(row["relative_path"] or ""), True)


def _hit_of(
    conn: sqlite3.Connection, node: _Node,
) -> _Hit | None:
    return (_ent_hit(conn, node[1]) if node[0] == "e"
            else _sym_hit(conn, node[1]))


def _identity_pair(fe_key: str | None, te_key: str | None) -> bool:
    """``REFERENCES`` simétrico: mesma coisa lógica em dois adapters.

    Compara os segmentos posicionais da ``identity_key`` a partir de
    ``root`` (``root/catalog/namespace/environment/qualified_name``) —
    o ``domain`` difere por construção (mesma chave completa = mesma
    entidade, impossível como par).
    """
    if not fe_key or not te_key or fe_key == te_key:
        return False
    a = DomainIdentity.parse_key(fe_key)
    b = DomainIdentity.parse_key(te_key)
    if a is None or b is None or len(a) != len(b):
        return False
    return a[1:] == b[1:]


def _endpoints(row: sqlite3.Row) -> list[tuple[_Node, int | None]]:
    """Todos os endpoints concretos da row: ``(nó, occ_snapshot)``."""
    out: list[tuple[_Node, int | None]] = []
    if row["from_entity_occ_id"] is not None:
        out.append(
            (("e", int(row["from_entity_occ_id"])), row["fe_snap"]))
    if row["from_symbol_occ_id"] is not None:
        out.append(
            (("s", int(row["from_symbol_occ_id"])), row["fs_snap"]))
    if row["to_entity_occ_id"] is not None:
        out.append(
            (("e", int(row["to_entity_occ_id"])), row["te_snap"]))
    if row["to_symbol_occ_id"] is not None:
        out.append(
            (("s", int(row["to_symbol_occ_id"])), row["ts_snap"]))
    return out


def _from_side(row: sqlite3.Row, node: _Node) -> bool:
    kind, occ = node
    return (
        kind == "e" and row["from_entity_occ_id"] == occ
    ) or (
        kind == "s" and row["from_symbol_occ_id"] == occ
    )


def _to_side(row: sqlite3.Row, node: _Node) -> bool:
    kind, occ = node
    return (
        kind == "e" and row["to_entity_occ_id"] == occ
    ) or (
        kind == "s" and row["to_symbol_occ_id"] == occ
    )


def _dom_params(node: _Node, snapshot_id: int, cap: int) -> tuple[object, ...]:
    ent = node[1] if node[0] == "e" else None
    sym = node[1] if node[0] == "s" else None
    return (snapshot_id, ent, sym, ent, sym, cap)


# Um item de trabalho do BFS: nó, origem do caminho e acumuladores
# (relation_ids, intermediate_keys, statuses, edge_refs) + profundidade.
_Work = tuple[
    _Node, str, tuple[int, ...], tuple[str, ...],
    tuple[str, ...], tuple[str, ...], int,
]


@dataclass
class _Ctx:
    """Estado mutável de um blast — passado aos expansores."""

    snapshot_id: int
    limits: ImpactLimits
    reason_suffix: str
    origin_keys: set[str]
    queue: deque[_Work] = field(default_factory=deque)
    visited: set[tuple[_Node, str]] = field(default_factory=set)
    out: list[ImpactCandidate] = field(default_factory=list)
    node_deps: dict[_Node, set[str]] = field(default_factory=dict)
    uncertain: dict[str, int] = field(default_factory=dict)
    cross_snap: int = 0


def _emit(
    ctx: _Ctx, hit: _Hit, origin_key: str,
    rel_ids: tuple[int, ...], mids: tuple[str, ...],
    statuses: tuple[str, ...], refs: tuple[str, ...],
    depth: int, *, rel_id: int | None, edge_ref: str,
    rtype: str, status: str, via: str,
) -> None:
    """Registra candidato + enfileira continuação do caminho."""
    new_rels = (*rel_ids, rel_id) if rel_id is not None else rel_ids
    new_mid = (*mids, hit.key)
    new_st = (*statuses, status)
    new_rf = (*refs, edge_ref)
    verified = all(s == "resolved" for s in new_st)
    hops = len(new_rf)
    kind = (ImpactKind.DIRECT if hops == 1 and verified
            else ImpactKind.TRANSITIVE if verified
            else ImpactKind.POSSIBLE)
    strength = (EvidenceStrength.VERIFIED if verified
                else EvidenceStrength.POSSIBLE)
    path = ImpactPath(
        origin_symbol_key=origin_key,
        affected_symbol_key=hit.key, hops=hops,
        relation_ids=new_rels,
        intermediate_keys=new_mid,
        resolution_statuses=new_st,
        fully_verified=verified,
        reason=f"{rtype} via {via}{ctx.reason_suffix}",
        edge_refs=new_rf)
    ctx.out.append(ImpactCandidate(
        symbol_key=hit.key, qualified_name=hit.qname,
        language=hit.language, kind=hit.kind, impact=kind,
        strength=strength, path=path,
        source_path=hit.path or None,
        occurrence_id=None if hit.is_domain else hit.node[1],
        node_type="domain" if hit.is_domain else "symbol",
        domain_occurrence_id=hit.node[1] if hit.is_domain else None))
    if hops < ctx.limits.max_depth:
        ctx.queue.append((hit.node, origin_key, new_rels,
                          new_mid, new_st, new_rf, depth + 1))


def _expand_code(
    conn: sqlite3.Connection, ctx: _Ctx, node: _Node,
    origin_key: str, rel_ids: tuple[int, ...], mids: tuple[str, ...],
    statuses: tuple[str, ...], refs: tuple[str, ...], depth: int,
) -> bool:
    """Arestas ``relations`` — consumidores (to→from). ``True`` = corte."""
    for r in conn.execute(
            _IN_SQL, (ctx.snapshot_id, node[1],
                      ctx.limits.max_nodes)).fetchall():
        consumer = int(r["from_id"])
        if (("s", consumer), origin_key) in ctx.visited:
            continue
        hit = _sym_hit(conn, consumer)
        if hit is None or hit.key in ctx.origin_keys:
            continue
        _emit(ctx, hit, origin_key, rel_ids, mids, statuses, refs,
              depth, rel_id=int(r["id"]), edge_ref=f"rel:{r['id']}",
              rtype=str(r["type"]),
              status=str(r["resolution_status"]),
              via=f"relation {r['id']}")
        if len(ctx.out) >= ctx.limits.max_paths:
            return True
    return False


def _dom_neighbors(
    ctx: _Ctx, r: sqlite3.Row, rtype: str, node: _Node,
    from_hit: bool, to_hit: bool,
) -> list[tuple[_Node, int | None]]:
    """Vizinhos de uma aresta de domínio conforme a direção do tipo."""
    if rtype == _REFERENCES and _identity_pair(
            str(r["fe_key"] or ""), str(r["te_key"] or "")):
        # identidade simétrica — atravessa para o outro lado.
        return [(n, snap) for n, snap in _endpoints(r) if n != node]
    if rtype in _DEPENDS_ON_TO and to_hit:
        return [(n, snap) for n, snap in _endpoints(r)
                if _from_side(r, n)]
    if rtype in _COUPLED:
        return [(n, snap) for n, snap in _endpoints(r) if n != node]
    return []


def _expand_domain(
    conn: sqlite3.Connection, ctx: _Ctx, node: _Node,
    origin_key: str, rel_ids: tuple[int, ...], mids: tuple[str, ...],
    statuses: tuple[str, ...], refs: tuple[str, ...], depth: int,
) -> bool:
    """Arestas ``domain_relations`` — ``True`` = corte por max_paths."""
    node_unc = ctx.node_deps.setdefault(node, set())
    for r in conn.execute(
            _DOM_SQL, _dom_params(node, ctx.snapshot_id,
                                  ctx.limits.max_nodes)).fetchall():
        rtype = str(r["type"])
        status = str(r["resolution_status"])
        from_hit = _from_side(r, node)
        to_hit = _to_side(r, node)

        # Alvo não-traversável: row sem endpoint concreto em `to`. É
        # dependência/produto não localizado — incerteza do nó, nunca hop.
        if (from_hit and r["to_entity_occ_id"] is None
                and r["to_symbol_occ_id"] is None
                and r["to_unresolved"]):
            dep = f"{rtype}→{str(r['to_unresolved'])[:80]} ({status})"
            if dep not in node_unc:
                node_unc.add(dep)
                ctx.uncertain[status] = ctx.uncertain.get(status, 0) + 1

        for nnode, nsnap in _dom_neighbors(
                ctx, r, rtype, node, from_hit, to_hit):
            if nnode == node or (nnode, origin_key) in ctx.visited:
                continue
            if nsnap is not None and nsnap != ctx.snapshot_id:
                # guarda de federação: alvo em outro snapshot nunca é
                # atravessado — conta como cobertura desconhecida.
                ctx.cross_snap += 1
                continue
            hit = _hit_of(conn, nnode)
            if hit is None or hit.key in ctx.origin_keys:
                continue
            _emit(ctx, hit, origin_key, rel_ids, mids, statuses, refs,
                  depth, rel_id=None, edge_ref=f"dom:{r['id']}",
                  rtype=rtype, status=status,
                  via=f"domain_relation {r['id']}")
            if len(ctx.out) >= ctx.limits.max_paths:
                return True
    return False


def _finish(ctx: _Ctx, truncated: bool) -> BlastOutcome:
    """Anexa ``uncertain_deps`` por candidato — pós-BFS.

    As dependências não-traversáveis de um nó só são vistas quando ele
    é expandido, o que pode ocorrer depois de o candidato já ter sido
    emitido: o anexo é sempre um pós-passo, nunca no emit.
    """
    deps = {n: tuple(sorted(s)) for n, s in ctx.node_deps.items() if s}
    done: list[ImpactCandidate] = []
    for c in ctx.out:
        node: _Node | None = None
        if c.node_type == "domain":
            if c.domain_occurrence_id is not None:
                node = ("e", c.domain_occurrence_id)
        elif c.occurrence_id is not None:
            node = ("s", c.occurrence_id)
        done.append(
            replace(c, uncertain_deps=deps.get(node, ())
                    if node is not None else ()))
    return BlastOutcome(tuple(done), truncated, ctx.uncertain,
                        ctx.cross_snap)


def blast(
    conn: sqlite3.Connection, snapshot_id: int,
    *,
    sym_origins: Mapping[str, int],
    ent_origins: Mapping[str, int],
    limits: ImpactLimits,
    reason_suffix: str = "",
) -> BlastOutcome:
    """BFS de blast sobre grafo misto; retorna ``BlastOutcome``.

    Mesma disciplina de ``blast_radius.blast``: visita por
    ``(nó, origem)`` preservando caminhos alternativos, origens nunca
    entram como candidatos, ``fully_verified`` exige todos os hops
    ``resolved``. Adições: nós entidade (``("e", occ)``), direção por
    tipo de relação de domínio, alvos ``to_unresolved`` contados como
    incerteza (``uncertain`` + ``uncertain_deps`` por candidato) e
    endpoints fora do snapshot pulados (``cross_snapshot``).
    """
    conn.row_factory = sqlite3.Row
    ctx = _Ctx(snapshot_id, limits, reason_suffix,
               set(sym_origins) | set(ent_origins))
    for key, occ in sym_origins.items():
        ctx.queue.append((("s", occ), key, (), (), (), (), 0))
    for key, occ in ent_origins.items():
        ctx.queue.append((("e", occ), key, (), (), (), (), 0))

    while ctx.queue:
        node, origin_key, rel_ids, mids, statuses, refs, depth = \
            ctx.queue.popleft()
        if (node, origin_key) in ctx.visited \
                or depth > ctx.limits.max_depth:
            continue
        ctx.visited.add((node, origin_key))
        if node[0] == "s" and _expand_code(
                conn, ctx, node, origin_key, rel_ids, mids,
                statuses, refs, depth):
            return _finish(ctx, True)
        if _expand_domain(conn, ctx, node, origin_key, rel_ids,
                          mids, statuses, refs, depth):
            return _finish(ctx, True)

    return _finish(ctx, False)


# ---------------------------------------------------------------------
# resolução de seeds de entidade
# ---------------------------------------------------------------------


def _display_forms(row: sqlite3.Row) -> set[str]:
    """Formas casáveis de uma row ``domain_entities``."""
    qn = str(row["qualified_name"])
    ns = str(row["namespace"] or "")
    cat = str(row["catalog"] or "")
    env = str(row["environment"] or "")
    forms = {qn, str(row["identity_key"]), _display_of(row)}
    if ns:
        forms.add(f"{ns}.{qn}")
    if cat or ns:
        forms.add(".".join(p for p in (cat, ns, qn) if p))
    if env:
        forms.update(f + f"@{env}" for f in tuple(forms))
    return forms


def resolve_entity_refs(
    conn: sqlite3.Connection, repository_id: int, ref: str,
) -> tuple[list[sqlite3.Row], str]:
    """Resolve uma ref de entidade → ``(rows de domain_entities, como)``.

    Formas: ``dom:…`` (identity_key), ``entity:N``/``id:N``/``N`` (row
    id), ``display()``, ``catalog.ns.qname``/``ns.qname``/``qname``.
    Múltiplos matches são retornados todos — o caller decide seedar
    todos (multiplicidade honesta) e diagnosticar.
    """
    conn.row_factory = sqlite3.Row
    ref = ref.strip()
    if not ref:
        return [], "empty"
    if ref.startswith("dom:"):
        row = conn.execute(
            "SELECT * FROM domain_entities"
            " WHERE repository_id = ? AND identity_key = ?",
            (repository_id, ref)).fetchone()
        return ([row] if row else []), "identity-key"
    m = _ID_REF_RE.fullmatch(ref)
    if m:
        row = conn.execute(
            "SELECT * FROM domain_entities"
            " WHERE id = ? AND repository_id = ?",
            (int(m.group(1)), repository_id)).fetchone()
        return ([row] if row else []), "row-id"
    guesses = sorted(
        g for g in {ref,
                    ref.split(":")[-1].split("@")[0].split(".")[-1]}
        if g)
    hits: list[sqlite3.Row] = []
    seen: set[int] = set()
    for row in conn.execute(
            "SELECT * FROM domain_entities WHERE repository_id = ?"
            " AND (qualified_name = ? OR qualified_name = ?)"
            " ORDER BY id",
            (repository_id,
             guesses[0] if guesses else "",
             guesses[-1] if guesses else "")).fetchall():
        if ref in _display_forms(row) and int(row["id"]) not in seen:
            seen.add(int(row["id"]))
            hits.append(row)
    return (hits, "display") if hits else ([], "none")


def resolve_seeds(
    conn: sqlite3.Connection, repository_id: int,
    refs: tuple[tuple[str, bool], ...],
    base_sid: int, target_sid: int,
) -> SeedResolution:
    """Resolve refs de entidade → ocorrências-semente nos dois snapshots.

    ``refs``: ``(ref, explícita)`` — refs explícitas (``req.entity``)
    geram diagnóstico quando não casam; refs de ``req.symbol`` falham
    em silêncio (o campo tem outro papel primário). Uma entidade só vira
    seed no snapshot onde tem ocorrência — sem ocorrência no par
    analisado, ``entity-seed-no-occurrence`` honesto.
    """
    target: dict[str, int] = {}
    base: dict[str, int] = {}
    diags: list[ImpactDiagnostic] = []
    seen_refs: set[str] = set()
    for ref, explicit in refs:
        if ref in seen_refs:
            continue
        seen_refs.add(ref)
        rows, how = resolve_entity_refs(conn, repository_id, ref)
        if not rows:
            if explicit:
                diags.append(ImpactDiagnostic(
                    "entity-seed-not-found",
                    f"entidade '{ref}' não encontrada no repositório"))
            continue
        if len(rows) > 1:
            diags.append(ImpactDiagnostic(
                "entity-seed-multi",
                f"'{ref}' casa {len(rows)} entidades ({how}) — todas "
                "viram seed (multiplicidade honesta)"))
        for row in rows:
            eid = int(row["id"])
            ikey = str(row["identity_key"])
            for sid, sink in ((target_sid, target), (base_sid, base)):
                occ = conn.execute(
                    "SELECT id FROM domain_entity_occurrences"
                    " WHERE snapshot_id = ? AND entity_id = ?"
                    " ORDER BY id LIMIT 1",
                    (sid, eid)).fetchone()
                if occ is not None and ikey not in sink:
                    sink[ikey] = int(occ["id"])
            if ikey not in target and ikey not in base and explicit:
                diags.append(ImpactDiagnostic(
                    "entity-seed-no-occurrence",
                    f"entidade '{ref}' ({ikey}) não tem ocorrência nos "
                    "snapshots analisados"))
    return SeedResolution(target, base, tuple(diags))


def domain_state(
    conn: sqlite3.Connection, snapshot_id: int,
    analyzer_versions: Mapping[str, str] | None,
) -> str:
    """Estado dos dados de domínio no snapshot.

    ``present``: há ocorrências de domínio. ``empty``: adapters rodaram
    (``domain:*`` em ``analyzer_versions``) mas nada foi extraído.
    ``adapters-disabled``: nenhum adapter habilitado no index — a
    ausência de dados de domínio é configuração, não falha.
    """
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM domain_entity_occurrences"
        " WHERE snapshot_id = ?", (snapshot_id,)).fetchone()
    if int(row["c"]):
        return "present"
    enabled = any(
        str(k).startswith("domain:") for k in (analyzer_versions or {}))
    return "empty" if enabled else "adapters-disabled"
