"""Context packs semeados por entidade de domínio (lf-0121, Cycle 7 Ph13).

Mesma disciplina do ``ContextEngine``: seleção determinística sob
budget, nenhum corpo gerado por LLM — todo conteúdo vem de fatos
persistidos (``domain_entities``, ``domain_entity_occurrences``,
``domain_relations``, ``evidence``, símbolos). Nada é re-extraído de
arquivo, nenhum código/job/terraform é executado, nenhuma conexão
externa é aberta — e todo read é ancorado em ``snapshot_id`` +
``repository_id`` (isolamento por construção).

O pack é um ``ContextPack`` comum (envelope schema v2) com a seção
aditiva ``domain`` (schema próprio v1): cartão da entidade-semente,
vizinhos limitados de ``domain_relations`` (top-N por confiança),
contadores honestos por direção/status — incluindo alvos ``dynamic:``
e não-resolvidos —, amostras de alvos pendentes, o resumo determinístico
de ``domains.summarize`` e o contrato de re-consulta dos refs.

``expansion_refs`` carregam refs re-consultáveis: ``dom:<identity_key>``
(entidade de domínio) ou ``sym:<qualified_name>`` (símbolo de código —
a ponte ``*_symbol_occ_id``). ``resolve_domain_ref`` é a função que um
handler ``/domains/entities`` chamaria; até a rota existir (wiring
documentado em ``docs/cycle-7-domain-context-packs.md``, spec lf-0123)
o contrato está declarado em ``DomainPackSection.requery``.
"""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from code_atlas.context.contracts import (
    DOMAIN_PACK_SCHEMA_VERSION,
    PRIORITY_DEP,
    PRIORITY_DIRECT,
    PRIORITY_ESSENTIAL,
    Budget,
    ContextItem,
    ContextPack,
    DomainEntityCard,
    DomainNeighborRef,
    DomainPackPart,
    DomainPackRequest,
    DomainPackSection,
    ExpansionRef,
    PackEvidence,
)
from code_atlas.context.engine import ContextEngine
from code_atlas.context.tokenizer import TokenCounter
from code_atlas.contracts.enums import ConfidenceClass, ResolutionStatus
from code_atlas.domains.contracts import confidence_rank
from code_atlas.domains.summarize import LineageFact, summarize_lineage
from code_atlas.storage.store import Store

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from typing import Any

# Prefixos que adapters usam como marcador de honestidade ("o alvo é
# dinâmico/ambiente — não é candidato estático"). Espelha
# ``crossresolve._SKIP_PREFIXES`` — mantido local para não importar nome
# privado; a semântica é a mesma: conta à parte, nunca pendência.
DYNAMIC_TARGET_PREFIXES = ("dynamic:", "${dynamic}", "environment:")

MAX_NEIGHBORS_CAP = 100       # teto duro do top-N
_SAMPLE_CAP = 10              # amostras de alvos não-resolvidos/ambíguos
_EVIDENCE_PER_ITEM = 6        # cap de PackEvidence por vizinho

# Contrato de re-consulta — declarado DENTRO do pack (spec lf-0121:
# "exact re-query contract documented in the pack itself").
DOMAIN_REQUERY_CONTRACT = (
    "expansion_refs[].ref_id forms: 'dom:<identity_key>' resolves to a "
    "domain entity of this repository — re-query contract (route to be "
    "wired by lf-0123): GET /domains/entities"
    "?identity_key=<ref>&repository_root=<root>&snapshot_id=<sid>; "
    "engine-side backing function: "
    "code_atlas.context.domain.resolve_domain_ref"
    "(conn, repository_id, snapshot_id, ref). "
    "'sym:<qualified_name>' resolves to a code-graph symbol of this "
    "snapshot (structural search / ContextExpander, not /domains/entities)."
)

_RANKING_DOMAIN = (
    "seed card (essential) > vizinhos de domínio por confiança "
    "(resolved>syntactic>heuristic>inferred, desempate por status e ref); "
    "vizinhos além do top-N viram expansion_refs re-consultáveis")

# Status da seção — a palavra honesta do build.
_STATUS_OK = "ok"
_STATUS_ABSENT = "absent"
_STATUS_NOT_FOUND = "seed-not-found"
_STATUS_AMBIGUOUS = "seed-ambiguous"
_STATUS_NOT_IN_SNAP = "seed-not-in-snapshot"

_STATUS_RANK = {
    str(ResolutionStatus.RESOLVED): 0,
    str(ResolutionStatus.EXTERNAL): 1,
    str(ResolutionStatus.AMBIGUOUS): 2,
    str(ResolutionStatus.UNRESOLVED): 3,
}


def _is_dynamic(text: str) -> bool:
    return text.strip().startswith(DYNAMIC_TARGET_PREFIXES)


def _entity_row(
    conn: sqlite3.Connection, repo_id: int, entity_id: int
) -> dict[str, Any] | None:
    """Row de ``domain_entities`` do PRÓPRIO repositório — isolamento."""
    row = conn.execute(
        "SELECT id, domain, kind, identity_key, qualified_name, name,"
        " catalog, namespace, environment, attributes"
        " FROM domain_entities WHERE id = ? AND repository_id = ?",
        (entity_id, repo_id),
    ).fetchone()
    return dict(row) if row is not None else None


def _card(
    entity: Mapping[str, Any],
    occs: Sequence[Mapping[str, Any]],
    repository_id: int,
    snapshot_id: int,
) -> DomainEntityCard:
    """Cartão da entidade ancorado na primeira ocorrência do snapshot."""
    first = occs[0]
    return DomainEntityCard(
        entity_id=int(entity["id"]),
        occurrence_id=int(first["occ_id"]),
        occurrence_count=len(occs),
        domain=str(entity["domain"]),
        kind=str(entity["kind"]),
        name=str(entity["name"]),
        qualified_name=str(entity["qualified_name"]),
        identity_key=str(entity["identity_key"]),
        repository_id=repository_id,
        snapshot_id=snapshot_id,
        catalog=str(entity["catalog"]),
        namespace=str(entity["namespace"]),
        environment=str(entity["environment"]),
        source_path=str(first["source_path"] or ""),
        extraction_status=str(first["extraction_status"]),
    )


def resolve_domain_ref(
    conn: sqlite3.Connection,
    repository_id: int,
    snapshot_id: int,
    ref: str,
) -> DomainEntityCard | None:
    """Resolve um ref de expansão para o cartão da entidade — a função
    que um handler ``GET /domains/entities`` chamaria.

    Formas: ``dom:<identity_key>`` (chave lógica canônica) ou
    ``entity:<row_id>``. ``None`` para ref malformado, entidade de outro
    repositório ou entidade sem ocorrência neste snapshot — nunca
    fabrica identidade nem atravessa isolamento.
    """
    store = Store(conn)
    entity_id: int | None = None
    ref = ref.strip()
    if ref.startswith("dom:"):
        entity_id = store.domain_entity_id_for(repository_id, ref)
    elif ref.startswith("entity:") and ref[len("entity:"):].isdigit():
        entity_id = int(ref[len("entity:"):])
    elif ref.isdigit():
        entity_id = int(ref)
    if entity_id is None:
        return None
    entity = _entity_row(conn, repository_id, entity_id)
    if entity is None:
        return None
    occs = [
        dict(r) for r in store.domain_occurrences_snapshot(snapshot_id)
        if int(r["entity_id"]) == entity_id
    ]
    if not occs:
        return None
    return _card(entity, occs, repository_id, snapshot_id)


@dataclass(frozen=True)
class _SeedHit:
    """Desfecho da resolução da seed dentro do snapshot."""

    status: str
    entity: Mapping[str, Any] | None = None
    occs: tuple[Mapping[str, Any], ...] = ()
    candidates: tuple[str, ...] = ()


@dataclass
class _Neighbor:
    """Arestas deduplicadas por vizinho (entidade ou símbolo)."""

    ref: str                        # dom:… | sym:<qname>
    kind: str                       # "entity" | "symbol"
    entity_kind: str
    display: str
    source_path: str = ""
    directions: set[str] = field(default_factory=set)
    relation_types: set[str] = field(default_factory=set)
    best_rank: int = -1             # confidence_rank máx.
    best_status_rank: int = 99      # _STATUS_RANK da aresta mais forte
    best_status: str = ""
    best_confidence: str = ""
    first_relation_id: int = 1 << 62
    edge_count: int = 0
    evidence_ids: set[int] = field(default_factory=set)
    occ_paths: set[str] = field(default_factory=set)

    def direction(self) -> str:
        if self.directions == {"incoming"}:
            return "incoming"
        if self.directions == {"outgoing"}:
            return "outgoing"
        return "both"

    def touch(
        self, *, direction: str, relation_type: str, relation_id: int,
        status: str, confidence: str, evidence: Iterable[int],
    ) -> None:
        self.directions.add(direction)
        self.relation_types.add(relation_type)
        self.edge_count += 1
        self.first_relation_id = min(self.first_relation_id, relation_id)
        self.evidence_ids.update(int(e) for e in evidence)
        rank = confidence_rank(ConfidenceClass(confidence))
        srank = _STATUS_RANK.get(status, 9)
        if (rank, -srank) > (self.best_rank, -self.best_status_rank):
            self.best_rank = rank
            self.best_status_rank = srank
            self.best_status = status
            self.best_confidence = confidence

    def order_key(self) -> tuple[int, int, int, str]:
        """Top-N por confiança: rank desc → status melhor → id estável."""
        return (-self.best_rank, self.best_status_rank,
                self.first_relation_id, self.ref)

    def ref_view(self, item_id: str) -> DomainNeighborRef:
        return DomainNeighborRef(
            item_id=item_id, ref=self.ref, kind=self.kind,
            entity_kind=self.entity_kind, display=self.display,
            direction=self.direction(),
            relation_types=tuple(sorted(self.relation_types)),
            resolution_status=self.best_status,
            confidence_class=self.best_confidence,
            edge_count=self.edge_count,
            evidence_ids=tuple(sorted(self.evidence_ids)))


def _new_neighbor(
    ref: str, occ_by_identity: Mapping[str, Mapping[str, Any]],
) -> _Neighbor | None:
    """Cria o vizinho a partir do ref — ``None`` quando a ocorrência
    não está no snapshot (nunca fabrica identidade)."""
    if ref.startswith("dom:"):
        occ = occ_by_identity.get(ref)
        if occ is None:
            return None
        return _Neighbor(
            ref=ref, kind="entity", entity_kind=str(occ["kind"]),
            display=str(occ["qualified_name"]),
            source_path=str(occ["source_path"] or ""))
    if ref.startswith("sym:") and ref[len("sym:"):]:
        return _Neighbor(ref=ref, kind="symbol", entity_kind="symbol",
                         display=ref[len("sym:"):])
    return None


class DomainContextEngine(ContextEngine):
    """``ContextEngine`` semeado por entidade de domínio.

    Reusa o maquinário de budget/seleção do engine base (``_budget``,
    ``_fit``, ``_enforce_payload_budget``) — a seção ``domain`` entra no
    envelope via ``domain_factory`` e é contada no payload estimado.
    """

    def __init__(
        self, conn: sqlite3.Connection, counter: TokenCounter | None = None
    ) -> None:
        super().__init__(conn, counter)
        self._dstore = Store(conn)

    # -------------------------------------------------------- público
    def build_domain(self, req: DomainPackRequest) -> ContextPack:
        diags: list[str] = []
        repo_id = self._structural.repo_for_root(req.repository_root)
        if repo_id is None:
            return self._absent(
                req, f"repositório não indexado: {req.repository_root}")
        snap, err = self._structural.resolve_snapshot(
            repo_id, req.snapshot_id)
        if snap is None or snap.id is None:
            return self._absent(req, err or "nenhum snapshot READY")
        sid = int(snap.id or 0)
        budget = self._budget(req)
        max_n = max(0, min(req.max_neighbors, MAX_NEIGHBORS_CAP))
        ck = ("domain", sid, req.seed, req.level, budget.limit, max_n)
        cached = self._pack_cache.get(ck)
        if cached is not None:
            return cached

        occs = self._dstore.domain_occurrences_snapshot(sid)
        if not occs:
            pack = self._assemble(
                req, sid, budget, card=None, neighbors=(), total=0,
                counts=self._zero_counts(), unresolved=(), ambiguous=(),
                diags=[*diags, "domain-absent",
                       "snapshot sem ocorrências de domínio — nenhum "
                       "adapter de domínio rodou ou snapshot anterior "
                       "à v9"],
                status=_STATUS_ABSENT, snap_status=str(snap.status))
            self._cache_put(ck, pack)
            return pack

        occ_by_entity: dict[int, list[dict[str, Any]]] = {}
        occ_map: dict[int, dict[str, Any]] = {}
        occ_by_identity: dict[str, dict[str, Any]] = {}
        for row in occs:
            o = dict(row)
            occ_map[int(o["occ_id"])] = o
            occ_by_entity.setdefault(int(o["entity_id"]), []).append(o)
            occ_by_identity.setdefault(str(o["identity_key"]), o)

        hit = self._resolve_seed(repo_id, req.seed, occ_by_entity)
        if hit.status != _STATUS_OK or hit.entity is None:
            diags.append(f"domain-{hit.status}")
            pack = self._assemble(
                req, sid, budget, card=None, neighbors=(), total=0,
                counts=self._zero_counts(), unresolved=(), ambiguous=(),
                diags=diags, status=hit.status,
                candidates=hit.candidates,
                snap_status=str(snap.status))
            self._cache_put(ck, pack)
            return pack

        entity = hit.entity
        seed_occ_ids = {int(o["occ_id"]) for o in hit.occs}
        seed_ref = str(entity["identity_key"])  # já carrega "dom:"
        card = _card(entity, list(hit.occs), repo_id, sid)

        edges, counts, unresolved, ambiguous = self._seed_edges(
            sid, seed_occ_ids, diags)
        sym_names = self._symbol_names({
            int(r[c]) for r in edges
            for c in ("from_symbol_occ_id", "to_symbol_occ_id")
            if r[c] is not None})
        neighbors = self._neighbor_views(
            edges, seed_occ_ids, seed_ref, occ_map, occ_by_identity,
            sym_names)
        ordered = sorted(neighbors.values(), key=_Neighbor.order_key)
        shown, beyond = ordered[:max_n], ordered[max_n:]
        if beyond:
            diags.append(
                f"domain-neighbors-capped:{len(shown)}/{len(ordered)}")
        facts = self._lineage_facts(
            edges, seed_occ_ids, seed_ref, str(entity["qualified_name"]),
            occ_map, sym_names, neighbors)
        pack = self._assemble(
            req, sid, budget, card=card,
            neighbors=tuple(shown), total=len(ordered),
            counts=counts, unresolved=unresolved, ambiguous=ambiguous,
            diags=diags, status=_STATUS_OK,
            beyond=tuple(n.ref for n in beyond),
            summary=summarize_lineage(self._subject(entity), facts),
            snap_status=str(snap.status))
        self._cache_put(ck, pack)
        return pack

    def _cache_put(
        self, ck: tuple[object, ...], pack: ContextPack
    ) -> None:
        if len(self._pack_cache) >= 32:
            self._pack_cache.pop(next(iter(self._pack_cache)))
        self._pack_cache[ck] = pack

    # -------------------------------------------------------- seed
    def _resolve_seed(
        self, repo_id: int, seed: str,
        occ_by_entity: Mapping[int, Sequence[Mapping[str, Any]]],
    ) -> _SeedHit:
        """Formas: ``dom:``/``entity:<id>``/dígitos/``qualified_name``."""
        s = seed.strip()
        if not s:
            return _SeedHit(status=_STATUS_NOT_FOUND)
        entity: dict[str, Any] | None = None
        if s.startswith("dom:"):
            eid = self._dstore.domain_entity_id_for(repo_id, s)
            if eid is not None:
                entity = _entity_row(self._conn, repo_id, eid)
        elif s.startswith("entity:") and s[len("entity:"):].isdigit():
            entity = _entity_row(
                self._conn, repo_id, int(s[len("entity:"):]))
        elif s.isdigit():
            entity = _entity_row(self._conn, repo_id, int(s))
        else:
            rows = self._conn.execute(
                "SELECT id, domain, kind, identity_key, qualified_name,"
                " name, catalog, namespace, environment, attributes"
                " FROM domain_entities"
                " WHERE repository_id = ? AND qualified_name = ?"
                " ORDER BY identity_key",
                (repo_id, s),
            ).fetchall()
            if len(rows) > 1:
                return _SeedHit(
                    status=_STATUS_AMBIGUOUS,
                    candidates=tuple(str(r["identity_key"]) for r in rows))
            if rows:
                entity = dict(rows[0])
        if entity is None:
            return _SeedHit(status=_STATUS_NOT_FOUND)
        occs = occ_by_entity.get(int(entity["id"]), ())
        if not occs:
            return _SeedHit(status=_STATUS_NOT_IN_SNAP, entity=entity)
        return _SeedHit(status=_STATUS_OK, entity=entity, occs=tuple(occs))

    # -------------------------------------------------------- arestas
    def _seed_edges(
        self, sid: int, seed_occ_ids: set[int], diags: list[str]
    ) -> tuple[
        list[dict[str, Any]], dict[str, int],
        tuple[str, ...], tuple[str, ...],
    ]:
        """Arestas do snapshot que tocam a seed + contadores honestos.

        ``unresolved`` conta só pendências reais — alvos ``dynamic:``
        (marcador de adapter) contam em ``dynamic`` à parte."""
        counts = self._zero_counts()
        edges: list[dict[str, Any]] = []
        unresolved: list[str] = []
        ambiguous: list[str] = []
        for r in self._dstore.domain_relations_snapshot(sid):
            fe, te = r["from_entity_occ_id"], r["to_entity_occ_id"]
            out = fe is not None and int(fe) in seed_occ_ids
            inc = te is not None and int(te) in seed_occ_ids
            if not out and not inc:
                continue
            edges.append(dict(r))
            if out:
                counts["outgoing"] += 1
            if inc:
                counts["incoming"] += 1
            self._count_status(
                counts, str(r["resolution_status"]),
                str(r["to_unresolved"] or ""), unresolved, ambiguous)
        for key in ("resolved", "ambiguous", "unresolved", "external",
                    "dynamic"):
            if counts[key]:
                diags.append(f"domain-{key}:{counts[key]}")
        return edges, counts, tuple(unresolved), tuple(ambiguous)

    @staticmethod
    def _count_status(
        counts: dict[str, int], status: str, target: str,
        unresolved: list[str], ambiguous: list[str],
    ) -> None:
        """Contador por status; ``dynamic:`` conta à parte de
        ``unresolved`` (marcador de adapter, não pendência)."""
        if status == str(ResolutionStatus.UNRESOLVED):
            if _is_dynamic(target):
                counts["dynamic"] += 1
            else:
                counts["unresolved"] += 1
                if target and len(unresolved) < _SAMPLE_CAP:
                    unresolved.append(target)
        elif status == str(ResolutionStatus.AMBIGUOUS):
            counts["ambiguous"] += 1
            if target and len(ambiguous) < _SAMPLE_CAP:
                ambiguous.append(target)
        else:
            counts[status] = counts.get(status, 0) + 1

    @staticmethod
    def _zero_counts() -> dict[str, int]:
        return {"incoming": 0, "outgoing": 0, "resolved": 0,
                "ambiguous": 0, "unresolved": 0, "external": 0,
                "dynamic": 0}

    # -------------------------------------------------------- vizinhos
    def _neighbor_views(
        self, edges: list[dict[str, Any]], seed_occ_ids: set[int],
        seed_ref: str,
        occ_map: Mapping[int, Mapping[str, Any]],
        occ_by_identity: Mapping[str, Mapping[str, Any]],
        sym_names: Mapping[int, str],
    ) -> dict[str, _Neighbor]:
        """Agrupa arestas por vizinho — entidade (``dom:``) ou símbolo
        de código (``sym:``). Arestas para ``to_unresolved`` não têm
        vizinho (são contagens/alvos pendentes, nunca itens); arestas
        seed↔seed contam mas não viram vizinho (uma entidade não é
        vizinha de si mesma)."""
        neighbors: dict[str, _Neighbor] = {}
        for r in edges:
            fe, te = r["from_entity_occ_id"], r["to_entity_occ_id"]
            out = fe is not None and int(fe) in seed_occ_ids
            inc = te is not None and int(te) in seed_occ_ids
            rtype = str(r["type"])
            status = str(r["resolution_status"])
            conf = str(r["confidence_class"])
            try:
                evidence = tuple(
                    int(e) for e in json.loads(str(r["evidence_ids"])))
            except (ValueError, TypeError):
                evidence = ()
            rel_id = int(r["id"])
            sides: list[tuple[str, str]] = []
            if out:
                sides.append(("outgoing", self._other_ref(
                    r["to_entity_occ_id"], r["to_symbol_occ_id"],
                    occ_map, sym_names)))
            if inc:
                sides.append(("incoming", self._other_ref(
                    r["from_entity_occ_id"], r["from_symbol_occ_id"],
                    occ_map, sym_names)))
            for direction, other in sides:
                if not other or other == seed_ref:
                    continue
                nb = neighbors.get(other)
                if nb is None:
                    nb = _new_neighbor(other, occ_by_identity)
                    if nb is None:
                        continue  # occ fora do snapshot — nunca fabrica
                    neighbors[other] = nb
                nb.touch(direction=direction, relation_type=rtype,
                         relation_id=rel_id, status=status,
                         confidence=conf, evidence=evidence)
        return neighbors

    @staticmethod
    def _other_ref(
        entity_occ: object, symbol_occ: object,
        occ_map: Mapping[int, Mapping[str, Any]],
        sym_names: Mapping[int, str],
    ) -> str:
        """Ref re-consultável do outro endpoint ("" quando não há)."""
        if isinstance(entity_occ, int):
            occ = occ_map.get(entity_occ)
            if occ is not None:
                return str(occ["identity_key"])  # já carrega "dom:"
        if isinstance(symbol_occ, int):
            qn = sym_names.get(symbol_occ, "")
            if qn:
                return f"sym:{qn}"
        return ""

    def _symbol_names(self, occ_ids: set[int]) -> dict[int, str]:
        """``occ_id`` → ``qualified_name`` — batched, sem N+1."""
        if not occ_ids:
            return {}
        rows = self._conn.execute(
            "SELECT so.id, s.qualified_name FROM symbol_occurrences so"
            " JOIN symbols s ON s.id = so.logical_symbol_id"
            " WHERE so.id IN (SELECT value FROM json_each(?))",
            (json.dumps(sorted(occ_ids)),),
        ).fetchall()
        return {int(r["id"]): str(r["qualified_name"]) for r in rows}

    # -------------------------------------------------------- fatos/items
    def _lineage_facts(
        self, edges: list[dict[str, Any]], seed_occ_ids: set[int],
        seed_ref: str, seed_display: str,
        occ_map: Mapping[int, Mapping[str, Any]],
        sym_names: Mapping[int, str],
        neighbors: Mapping[str, _Neighbor],
    ) -> list[LineageFact]:
        """Rows → ``LineageFact`` para o resumo determinístico."""
        facts: list[LineageFact] = []
        for r in edges:
            fe, te = r["from_entity_occ_id"], r["to_entity_occ_id"]
            out = fe is not None and int(fe) in seed_occ_ids
            inc = te is not None and int(te) in seed_occ_ids
            rtype = str(r["type"])
            status = str(r["resolution_status"])
            target = str(r["to_unresolved"] or "")
            dynamic = _is_dynamic(target)
            if out:
                other = self._other_ref(
                    r["to_entity_occ_id"], r["to_symbol_occ_id"],
                    occ_map, sym_names)
                nb = neighbors.get(other)
                if other == seed_ref:
                    label = seed_display
                elif nb is not None:
                    label = nb.display
                else:
                    label = target or "(no target)"
                facts.append(LineageFact(
                    relation_type=rtype, direction="outgoing",
                    label=label, resolution_status=status,
                    neighbor_kind=nb.entity_kind if nb else "",
                    dynamic=dynamic))
            if inc:
                other = self._other_ref(
                    r["from_entity_occ_id"], r["from_symbol_occ_id"],
                    occ_map, sym_names)
                nb = neighbors.get(other)
                if other == seed_ref:
                    label = seed_display
                elif nb is not None:
                    label = nb.display
                else:
                    label = "(unresolved)"
                facts.append(LineageFact(
                    relation_type=rtype, direction="incoming",
                    label=label, resolution_status=status,
                    neighbor_kind=nb.entity_kind if nb else "",
                    dynamic=dynamic))
        return facts

    @staticmethod
    def _subject(entity: Mapping[str, Any]) -> str:
        """Rótulo da seed no resumo — espelha ``DomainIdentity.display``."""
        head = ".".join(p for p in (
            str(entity["catalog"]), str(entity["namespace"])) if p)
        qn = str(entity["qualified_name"])
        disp = f"{head}.{qn}" if head else qn
        env = str(entity["environment"])
        if env:
            disp = f"{disp}@{env}"
        return f"{entity['kind']} {disp}"

    def _items(
        self, card: DomainEntityCard,
        neighbors: tuple[_Neighbor, ...],
        evidence_map: Mapping[int, str],
    ) -> list[ContextItem]:
        body = "\n".join(
            ln for ln in (
                f"kind: {card.kind}",
                f"domain: {card.domain}",
                f"identity: {card.identity_key}",
                f"qualified_name: {card.qualified_name}",
                f"catalog: {card.catalog}" if card.catalog else "",
                f"namespace: {card.namespace}" if card.namespace else "",
                f"environment: @{card.environment}"
                if card.environment else "",
                f"source: {card.source_path}" if card.source_path else "",
                f"occurrences: {card.occurrence_count}",
            ) if ln)
        items = [ContextItem(
            item_id=f"dcard:{card.identity_key}", kind="domain-entity",
            priority=PRIORITY_ESSENTIAL, title=card.qualified_name,
            body=body, est_tokens=0, path=card.source_path,
            evidence=(PackEvidence(kind="domain-entity",
                                   ref=card.identity_key),
                      PackEvidence(kind="domain-occurrence",
                                   ref=str(card.occurrence_id))),
            expandable=False)]
        for nb in neighbors:
            ev = [PackEvidence(kind="domain-relation",
                               ref=str(nb.first_relation_id))]
            for eid in sorted(nb.evidence_ids)[:_EVIDENCE_PER_ITEM]:
                ev.append(PackEvidence(kind="evidence", ref=str(eid),
                                       detail=evidence_map.get(eid, "")))
            rel_desc = ", ".join(
                f"{t} ({nb.direction()})"
                for t in sorted(nb.relation_types))
            nbody = "\n".join((
                f"{nb.entity_kind} {nb.display}",
                f"ref: {nb.ref}",
                f"relations: {rel_desc} — {nb.best_status}/"
                f"{nb.best_confidence} x{nb.edge_count}",
            ))
            items.append(ContextItem(
                item_id=nb.ref, kind="domain-neighbor",
                priority=PRIORITY_DEP, title=nb.display, body=nbody,
                est_tokens=0,
                symbol_key=(nb.display if nb.kind == "symbol" else ""),
                path=nb.source_path,
                evidence=tuple(ev), expandable=True))
        return items

    def _evidence_paths(self, ids: set[int]) -> dict[int, str]:
        if not ids:
            return {}
        rows = self._conn.execute(
            "SELECT id, path FROM evidence"
            " WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(sorted(ids)),),
        ).fetchall()
        return {int(r["id"]): str(r["path"]) for r in rows}

    # -------------------------------------------------------- assemble
    def _assemble(
        self, req: DomainPackRequest, sid: int, budget: Budget, *,
        card: DomainEntityCard | None, neighbors: tuple[_Neighbor, ...],
        total: int, counts: Mapping[str, int],
        unresolved: tuple[str, ...], ambiguous: tuple[str, ...],
        diags: list[str], status: str, snap_status: str,
        candidates: tuple[str, ...] = (),
        beyond: tuple[str, ...] = (),
        summary: str = "",
    ) -> ContextPack:
        """Empilha itens + fábrica de seção no maquinário de budget."""
        ev_map = self._evidence_paths({
            e for nb in neighbors for e in nb.evidence_ids})
        items = (self._items(card, neighbors, ev_map)
                 if card is not None else [])
        items = self._dedup(self._estimate(items))
        items.sort(key=lambda i: (i.priority, i.item_id))
        selected, omitted = self._fit(items, budget.available_items)
        essentials = sum(
            i.est_tokens for i in items if i.priority <= PRIORITY_DIRECT)
        insufficient = len(selected) < len(
            [i for i in items if i.priority <= PRIORITY_DIRECT])

        neighbor_by_ref = {nb.ref: nb for nb in neighbors}

        def factory(sel: tuple[ContextItem, ...]) -> DomainPackPart:
            chosen = [
                neighbor_by_ref[i.item_id].ref_view(i.item_id)
                for i in sel if i.item_id in neighbor_by_ref]
            section = DomainPackSection(
                schema_version=DOMAIN_PACK_SCHEMA_VERSION,
                status=status, entity=card,
                neighbors=tuple(chosen),
                neighbors_total=total,
                neighbors_omitted=total - len(chosen),
                counts=dict(counts),
                unresolved_targets=unresolved,
                ambiguous_targets=ambiguous,
                summary=summary,
                requery=DOMAIN_REQUERY_CONTRACT,
                candidates=candidates)
            return DomainPackPart(
                section=section,
                extra_expansion_refs=tuple(
                    ExpansionRef(
                        ref_id=ref, kind="domain-neighbor",
                        reason="além do top-N de vizinhos — "
                               "re-consultável via ref")
                    for ref in beyond),
                extra_unresolved=(*unresolved, *ambiguous),
                ranking_explanation=_RANKING_DOMAIN)

        return self._enforce_payload_budget(
            req, sid, budget, selected, omitted, essentials,
            insufficient, diags, snap_status, domain_factory=factory)

    def _absent(
        self, req: DomainPackRequest, msg: str
    ) -> ContextPack:
        """Falha de resolução (repo/snapshot) — pack vazio honesto."""
        base = self._empty(req, msg)
        return dataclasses.replace(
            base,
            domain=DomainPackSection(
                schema_version=DOMAIN_PACK_SCHEMA_VERSION,
                status=_STATUS_ABSENT, entity=None, neighbors=(),
                neighbors_total=0, neighbors_omitted=0,
                counts=self._zero_counts(), unresolved_targets=(),
                ambiguous_targets=(), summary="", candidates=(),
                requery=DOMAIN_REQUERY_CONTRACT))
