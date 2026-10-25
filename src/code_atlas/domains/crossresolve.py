"""Resolução cross-domain (lf-0117, Cycle 7 Phase 9).

A camada que conecta entidades observadas por **adapters diferentes**
dentro do mesmo snapshot: a tabela que o SQL declara e o PySpark lê, o
``aws_glue_job`` que o Terraform provisiona e o script que o job executa,
o literal ``fetch("/v1/pets")`` e o ``GET /v1/pets`` do contrato OpenAPI.

Regra inegociável: **nunca casar por nome nu**. Um vínculo exige acordo
de identidade qualificada — ``catalog``/``namespace``/``environment``/
``root`` + ``qualified_name``, ou conta+região+recurso para ARNs AWS,
ou igualdade literal de ``method+path`` para consumidor↔endpoint. Nome
igual com qualificador divergente NÃO é resolvido — é exatamente o caso
que ``DomainIdentity`` existe para separar.

Desenho:

- opera sobre estado **persistido** do snapshot (``domain_entities`` +
  ``domain_entity_occurrences`` + ``domain_relations`` + símbolos) —
  nada é re-extraído de arquivo;
- ``plan`` é puro: devolve ``CrossResolutionPlan`` com *updates* (rows
  ``unresolved`` re-resolvidas in place) e *edges* novas — a gravação é
  ``Store.persist_cross_resolutions`` dentro da transação de publicação;
- isolamento por snapshot: todo candidato é ocorrência do **mesmo**
  snapshot; a auditoria ``cross_snapshot_domain_relations`` cobre as
  arestas novas como qualquer outra;
- ``inferred`` nunca vira ``resolved`` (invariante de ``DomainRelation``).

Estados honestos (spec):

- identidade qualificada plena → ``resolved``;
- acordo parcial (nome+ns, mas catálogo/env diverge) → ``ambiguous``
  com todos os candidatos preservados no ``to_unresolved``;
- lado exclusivamente externo (URL absoluta, ARN sem entidade local)
  → ``external``;
- nome nu sem qualificador → continua ``unresolved`` e vira
  ``CrossDiagnostic`` — nunca aresta.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from code_atlas.contracts.enums import ConfidenceClass, ResolutionStatus
from code_atlas.domains.api.common import is_absolute_url, url_path
from code_atlas.domains.identity import DomainIdentity
from code_atlas.domains.model import (
    DomainEntityKind,
    DomainRelation,
    DomainRelationKind,
)

if TYPE_CHECKING:
    # ``storage.store`` importa ``domains.model`` — manter o resolver
    # dentro do pacote domains exige quebrar o ciclo: Store só aparece
    # como anotação, nunca em runtime.
    from code_atlas.storage.store import Store

CROSS_RESOLVER_ID = "cross-domain"
CROSS_RESOLVER_VERSION = "0.1.0"

# Status que o resolver pode re-resolver: somente ``unresolved``.
# ``resolved`` nunca é tocado; ``ambiguous``/``external`` são decisões
# já declaradas pelo adapter — re-derivá-las encolheria a honestidade.
_RERESOLVABLE = frozenset({str(ResolutionStatus.UNRESOLVED)})

# Relações cujo alvo é nome qualificado de dados (tabela/visão/iceberg).
_DATA_RELATION_TYPES = frozenset(
    {
        str(DomainRelationKind.READS_FROM),
        str(DomainRelationKind.WRITES_TO),
        str(DomainRelationKind.TRANSFORMS),
        str(DomainRelationKind.DERIVES_FROM),
    }
)

# Atributos de entidade cujos valores podem ancorar um artefato do repo.
# Adapters registram observáveis como strings; um valor que é path
# relativo do repositório é evidência literal (path qualificado).
_PATH_ATTR_KEYS = frozenset(
    {
        "script_location", "source", "path", "file", "filename",
        "entry_point", "artifact", "artifacts_out", "artifacts_in",
        "definition_path", "target",
    }
)

_ABS_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_ARN_RE = re.compile(
    r"^arn:(?P<partition>[a-z0-9-]+):(?P<service>[a-z0-9-]+):"
    r"(?P<region>[a-z0-9-]*):(?P<account>[0-9]*):(?P<resource>.+)$"
)
_METHOD_NOTE_RE = re.compile(r"^method=([A-Z]+)$")
_METHOD_QNAME_RE = re.compile(r"^([A-Z]+)\s+(\S.*)$")
# Marcadores que adapters usam para "não é candidato estático" — nunca
# viram resolução nem diagnóstico (são honestidade, não pendência).
_SKIP_PREFIXES = ("dynamic:", "${dynamic}", "environment:")


class Agreement(StrEnum):
    """Grau de acordo entre uma referência e uma identidade de entidade."""

    FULL = "full"        # todos os componentes qualificados iguais
    PARTIAL = "partial"  # nome + algum qualificador, com divergência
    NAME_ONLY = "name"   # só o nome casa — nunca resolve
    NONE = "none"


@dataclass(frozen=True)
class _RefParts:
    """Parse posicional de um ``to_unresolved`` textual."""

    catalog: str = ""
    namespace: str = ""
    environment: str = ""
    root: str = ""
    name: str = ""


@dataclass(frozen=True)
class _Occ:
    """Ocorrência de entidade de domínio num snapshot (visão do resolver)."""

    occ_id: int
    entity_id: int
    identity: DomainIdentity
    kind: str
    attributes: Mapping[str, str]
    source_file_id: int | None
    source_path: str
    span_start: int | None


@dataclass(frozen=True)
class _RelRow:
    """Row de ``domain_relations`` do snapshot."""

    relation_id: int
    relation_type: str
    from_entity_occ_id: int | None
    from_symbol_occ_id: int | None
    to_entity_occ_id: int | None
    to_symbol_occ_id: int | None
    to_unresolved: str | None
    resolution_status: str
    confidence_class: str
    resolver: str
    evidence_ids: tuple[int, ...]


@dataclass(frozen=True)
class CrossUpdate:
    """Re-resolução in place de uma row ``domain_relations`` existente."""

    relation_id: int
    resolution_status: ResolutionStatus
    confidence_class: ConfidenceClass
    to_entity_occ_id: int | None = None
    to_symbol_occ_id: int | None = None
    to_unresolved: str | None = None
    evidence_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class CrossEdge:
    """Nova aresta cross-domain entre ocorrências do mesmo snapshot."""

    relation_type: str
    resolution_status: ResolutionStatus
    confidence_class: ConfidenceClass
    from_entity_occ_id: int | None = None
    from_symbol_occ_id: int | None = None
    to_entity_occ_id: int | None = None
    to_symbol_occ_id: int | None = None
    to_unresolved: str | None = None
    evidence_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class CrossDiagnostic:
    """Diagnóstico do resolver — candidato não-resolvido, nunca aresta."""

    code: str
    severity: str  # info | warning
    message: str


@dataclass(frozen=True)
class CrossResolutionPlan:
    """Saída determinística de ``CrossDomainResolver.plan``.

    ``update_rows()``/``insert_rows()`` produzem as tuplas primitivas que
    ``Store.persist_cross_resolutions`` grava — o store não conhece os
    dataclasses daqui (fronteira limpa, mesmo desenho de ``persist``).
    """

    updates: tuple[CrossUpdate, ...] = ()
    edges: tuple[CrossEdge, ...] = ()
    diagnostics: tuple[CrossDiagnostic, ...] = ()
    stats: Mapping[str, int] = field(default_factory=dict)

    def update_rows(self) -> list[tuple[object, ...]]:
        """(to_ent, to_sym, to_text, status, confidence, resolver, evi, rid)."""
        return [
            (
                u.to_entity_occ_id,
                u.to_symbol_occ_id,
                u.to_unresolved,
                str(u.resolution_status),
                str(u.confidence_class),
                CROSS_RESOLVER_ID,
                json.dumps(sorted(u.evidence_ids)),
                u.relation_id,
            )
            for u in self.updates
        ]

    def insert_rows(self, snapshot_id: int) -> list[tuple[object, ...]]:
        """Mesmo shape de ``Store.insert_domain_relations``."""
        return [
            (
                snapshot_id,
                e.from_entity_occ_id,
                e.from_symbol_occ_id,
                e.to_entity_occ_id,
                e.to_symbol_occ_id,
                e.to_unresolved,
                e.relation_type,
                str(e.resolution_status),
                str(e.confidence_class),
                CROSS_RESOLVER_ID,
                json.dumps(sorted(e.evidence_ids)),
            )
            for e in self.edges
        ]


# ---------------------------------------------------------------------
# parsing de texto não-resolvido
# ---------------------------------------------------------------------


def _name_parses(text: str) -> list[_RefParts]:
    """Todos os parses posicionais plausíveis de um nome pontuado.

    ``a.b.c`` rende parses por ponto de corte — ``(ns=a.b, name=c)``,
    ``(catalog=a, ns=b, name=c)``, ``(ns=a, name=b.c)`` etc. O conjunto
    é o que permite comparar texto heterogêneo (``db.t`` ANSI,
    ``module.vpc.aws_glue_job.etl`` Terraform) com identidades reais
    sem chutar a estrutura: cada parse é testado e o desfecho agrega
    os candidatos de todos eles — parses que só rendem nomes nus são
    descartados aqui, no nascimento.
    """
    t = text.strip()
    if not t or any(ch in t for ch in " /:\\\"'()[]{}<>*?"):
        return []
    parts = [p for p in t.split(".") if p != ""]
    if ".".join(parts) != t or not parts:
        return []
    if len(parts) == 1:
        return [_RefParts(name=parts[0])]
    out: list[_RefParts] = []
    seen: set[tuple[str, str, str]] = set()
    n = len(parts)
    for k in range(1, n):
        cand = (
            _RefParts(namespace=".".join(parts[:k]), name=".".join(parts[k:])),
            _RefParts(
                catalog=parts[0],
                namespace=".".join(parts[1:k]),
                name=".".join(parts[k:]),
            ),
        )
        for c in cand:
            key = (c.catalog, c.namespace, c.name)
            if key not in seen:
                seen.add(key)
                out.append(c)
    return out


def _repo_path(text: str) -> str | None:
    """``to_unresolved`` como path relativo do repo; ``None`` se não for."""
    t = text.strip()
    if t.startswith("local:"):
        t = t[len("local:"):]
    if not t or _ABS_SCHEME_RE.match(t) or t.startswith(
        ("arn:", "dynamic:", "module:", "data.", "/", "environment:")
    ):
        return None
    if t.startswith("./"):
        t = t[2:]
    if not t or t.startswith(".") or any(
        ch in t for ch in " \\'\"()[]{}<>*?"
    ):
        return None
    return t


def _arn_parts(text: str) -> tuple[str, str, str] | None:
    """ARN AWS → ``(account, region, resource_name)``; ``None`` se não ARN."""
    m = _ARN_RE.match(text.strip())
    if m is None:
        return None
    resource = m.group("resource")
    leaf = re.split(r"[/:]", resource)[-1]
    if not leaf:
        return None
    return m.group("account"), m.group("region"), leaf


def _agreement(ref: _RefParts, ident: DomainIdentity) -> Agreement:
    """Grau de acordo ref↔identidade — o coração da regra "nunca nome nu".

    ``FULL`` exige igualdade em catalog+namespace+environment+root **e**
    ao menos um qualificador não-vazio (acordo só de nome nu = NAME_ONLY,
    mesmo quando ambos os lados são igualmente não-qualificados).
    ``PARTIAL`` = nome igual, ≥1 qualificador não-vazio igual, ≥1
    qualificador divergente (catálogo/env/root distintos — o caso que
    identidade qualificada existe para separar).
    """
    if ref.name != ident.qualified_name:
        return Agreement.NONE
    diverged = (
        ref.catalog != ident.catalog
        or ref.namespace != ident.namespace
        or ref.environment != ident.environment
        or ref.root != ident.root
    )
    if not diverged:
        if ref.catalog or ref.namespace or ref.environment or ref.root:
            return Agreement.FULL
        return Agreement.NAME_ONLY
    partial = (
        (ref.catalog != "" and ref.catalog == ident.catalog)
        or (ref.namespace != "" and ref.namespace == ident.namespace)
    )
    return Agreement.PARTIAL if partial else Agreement.NAME_ONLY


def _pair_agreement(a: DomainIdentity, b: DomainIdentity) -> Agreement:
    """Acordo entidade↔entidade (mesma regra de ``_agreement``)."""
    ref = _RefParts(
        catalog=a.catalog,
        namespace=a.namespace,
        environment=a.environment,
        root=a.root,
        name=a.qualified_name,
    )
    return _agreement(ref, b)


def _ident_from_row(row: sqlite3.Row) -> DomainIdentity | None:
    """Reconstrói a identidade completa a partir da ``identity_key``.

    As colunas ``catalog``/``namespace``/``environment``/``qualified_name``
    cobrem a maior parte; ``root`` só existe dentro da chave canônica —
    por isso o parse da chave é a fonte única (coerente com a UNIQUE).
    """
    segs = DomainIdentity.parse_key(str(row["identity_key"]))
    if segs is None or len(segs) < 7:
        return None
    domain, repository, root, catalog, namespace, env, qname = segs[:7]
    return DomainIdentity(
        domain=domain, repository=repository, root=root, catalog=catalog,
        namespace=namespace, environment=env, qualified_name=qname,
    )


def _endpoint_path(occ: _Occ) -> str:
    """Path literal de um ``APIEndpoint`` — atributo ou ``"METHOD /p"``."""
    fp = occ.attributes.get("full_path", "")
    if fp:
        return fp
    m = _METHOD_QNAME_RE.match(occ.identity.qualified_name)
    return m.group(2).strip() if m else occ.identity.qualified_name


def _endpoint_method(occ: _Occ) -> str:
    m = _METHOD_QNAME_RE.match(occ.identity.qualified_name)
    declared = occ.attributes.get("method", "")
    if declared:
        return declared.upper()
    return m.group(1).upper() if m else ""


class CrossDomainResolver:
    """Resolver opt-in de vínculos entre adapters distintos (Phase 9).

    Stateless: ``plan(store, snapshot_id)`` lê o estado persistido do
    snapshot (incluindo rows não-commitadas da transação corrente —
    é assim que roda dentro do BEGIN de publicação) e devolve o plano
    determinístico. Nenhuma escrita acontece aqui.
    """

    resolver_id = CROSS_RESOLVER_ID
    resolver_version = CROSS_RESOLVER_VERSION

    # -- API pública ---------------------------------------------------

    def plan(self, store: Store, snapshot_id: int) -> CrossResolutionPlan:
        """Propõe updates/edges para ``snapshot_id`` — somente leitura.

        Determinístico: ordenação por ids/identity_keys; re-executável —
        rows já marcadas ``resolver='cross-domain'`` não são tocadas e
        arestas já existentes entram no dedup, então um segundo ``plan``
        sobre o mesmo estado converge para vazio.
        """
        occs, by_path = self._load_occurrences(store, snapshot_id)
        rels = self._load_relations(store, snapshot_id)
        evidence = self._load_evidence(store, snapshot_id)
        file_occs = self._load_file_occs(store, snapshot_id)
        ctx = _Ctx(
            occs=occs, by_path=by_path, evidence=evidence,
            file_occs=file_occs, linked=self._linked_pairs(rels),
        )
        updates = self._plan_updates(rels, ctx)
        # pares já ligados por updates entram no dedup — uma row
        # re-resolvida para B não pode também virar aresta A→B nova.
        for u in updates:
            rel = next(
                r for r in rels if r.relation_id == u.relation_id
            )
            if rel.from_entity_occ_id and u.to_entity_occ_id:
                ctx.linked.add(
                    frozenset(
                        (int(rel.from_entity_occ_id), u.to_entity_occ_id)
                    )
                )
        edges = self._plan_edges(ctx)
        stats = {
            "cross_considered": ctx.considered,
            "cross_resolved": sum(
                1 for u in updates
                if u.resolution_status is ResolutionStatus.RESOLVED
            ) + sum(
                1 for e in edges
                if e.resolution_status is ResolutionStatus.RESOLVED
            ),
            "cross_ambiguous": sum(
                1 for u in updates
                if u.resolution_status is ResolutionStatus.AMBIGUOUS
            ) + sum(
                1 for e in edges
                if e.resolution_status is ResolutionStatus.AMBIGUOUS
            ),
            "cross_external": sum(
                1 for u in updates
                if u.resolution_status is ResolutionStatus.EXTERNAL
            ),
            "cross_edges": len(edges),
            "cross_diagnostics": len(ctx.diagnostics),
            "cross_missing_evidence": ctx.missing_evidence,
        }
        return CrossResolutionPlan(
            updates=tuple(updates), edges=tuple(edges),
            diagnostics=tuple(ctx.diagnostics), stats=stats,
        )

    # -- carga do snapshot ----------------------------------------------

    def _load_occurrences(
        self, store: Store, snapshot_id: int
    ) -> tuple[dict[int, _Occ], dict[str, list[_Occ]]]:
        occs: dict[int, _Occ] = {}
        by_path: dict[str, list[_Occ]] = {}
        for row in store.domain_occurrences_snapshot(snapshot_id):
            ident = _ident_from_row(row)
            if ident is None:
                continue  # chave corrompida — nunca fabrica identidade
            try:
                attrs = json.loads(str(row["attributes"] or "{}"))
            except json.JSONDecodeError:
                attrs = {}
            occ = _Occ(
                occ_id=int(row["occ_id"]),
                entity_id=int(row["entity_id"]),
                identity=ident,
                kind=str(row["kind"]),
                attributes={str(k): str(v) for k, v in attrs.items()},
                source_file_id=(
                    None
                    if row["source_file_id"] is None
                    else int(row["source_file_id"])
                ),
                source_path=str(row["source_path"] or ""),
                span_start=(
                    None
                    if row["span_start"] is None
                    else int(row["span_start"])
                ),
            )
            occs[occ.occ_id] = occ
            if occ.source_path:
                by_path.setdefault(occ.source_path, []).append(occ)
        return occs, by_path

    def _load_relations(
        self, store: Store, snapshot_id: int
    ) -> list[_RelRow]:
        out: list[_RelRow] = []
        for row in store.domain_relations_snapshot(snapshot_id):
            try:
                ev = tuple(
                    int(x) for x in json.loads(str(row["evidence_ids"] or "[]"))
                )
            except json.JSONDecodeError:
                ev = ()
            out.append(
                _RelRow(
                    relation_id=int(row["id"]),
                    relation_type=str(row["type"]),
                    from_entity_occ_id=row["from_entity_occ_id"],
                    from_symbol_occ_id=row["from_symbol_occ_id"],
                    to_entity_occ_id=row["to_entity_occ_id"],
                    to_symbol_occ_id=row["to_symbol_occ_id"],
                    to_unresolved=row["to_unresolved"],
                    resolution_status=str(row["resolution_status"]),
                    confidence_class=str(row["confidence_class"]),
                    resolver=str(row["resolver"]),
                    evidence_ids=ev,
                )
            )
        return out

    def _load_evidence(
        self, store: Store, snapshot_id: int
    ) -> dict[int, sqlite3.Row]:
        return {
            int(r["id"]): r
            for r in store.evidence_snapshot(snapshot_id)
        }

    def _load_file_occs(
        self, store: Store, snapshot_id: int
    ) -> dict[str, list[int]]:
        """path relativo → ids de ocorrência do símbolo FILE."""
        out: dict[str, list[int]] = {}
        for path, occ_id in store.file_symbol_occurrences(snapshot_id):
            out.setdefault(path, []).append(occ_id)
        return out

    @staticmethod
    def _linked_pairs(rels: list[_RelRow]) -> set[frozenset[int]]:
        """Pares de ocorrências de entidade já ligados — dedup de arestas."""
        out: set[frozenset[int]] = set()
        for r in rels:
            if r.from_entity_occ_id and r.to_entity_occ_id:
                out.add(
                    frozenset(
                        (int(r.from_entity_occ_id), int(r.to_entity_occ_id))
                    )
                )
        return out

    # -- evidência ------------------------------------------------------

    def _occ_evidence(
        self, occ: _Occ, ctx: _Ctx
    ) -> tuple[int, ...]:
        """Ids de evidência do adapter que observou ``occ`` no snapshot.

        Filtro: mesmo ``snapshot_id`` + mesmo ``source_file_id`` +
        ``extractor='domain:<domínio>'``; quando a ocorrência tem span,
        evidências cujo span casa o início são preferidas (precisão sem
        inventar posição). Nada encontrado → ``()`` e o caller decide:
        aresta SEM evidência dos dois lados nunca é emitida.
        """
        if occ.source_file_id is None:
            return ()
        extractor = f"domain:{occ.identity.domain}"
        cands = [
            e for e in ctx.evidence.values()
            if e["source_file_id"] == occ.source_file_id
            and str(e["extractor"]) == extractor
        ]
        if not cands:
            return ()
        if occ.span_start is not None:
            precise = [
                e for e in cands
                if self._evidence_span_start(e) == occ.span_start
            ]
            if precise:
                return tuple(sorted(int(e["id"]) for e in precise))
        return tuple(sorted(int(e["id"]) for e in cands))

    @staticmethod
    def _evidence_span_start(row: sqlite3.Row) -> int | None:
        raw = row["span"]
        if raw is None:
            return None
        try:
            span = json.loads(str(raw))
        except json.JSONDecodeError:
            return None
        if not isinstance(span, list) or not span:
            return None
        return int(span[0])

    # -- re-resolução de rows unresolved (M1) ----------------------------

    def _plan_updates(
        self, rels: list[_RelRow], ctx: _Ctx
    ) -> list[CrossUpdate]:
        updates: list[CrossUpdate] = []
        for rel in sorted(rels, key=lambda r: r.relation_id):
            if rel.resolution_status not in _RERESOLVABLE:
                continue
            if rel.resolver == CROSS_RESOLVER_ID:
                continue  # idempotência
            if rel.to_entity_occ_id or rel.to_symbol_occ_id:
                continue
            text = (rel.to_unresolved or "").strip()
            if not text or text.startswith(_SKIP_PREFIXES):
                continue
            ctx.considered += 1
            upd = self._resolve_row(rel, text, ctx)
            if upd is not None:
                updates.append(upd)
        return updates

    def _resolve_row(
        self, rel: _RelRow, text: str, ctx: _Ctx
    ) -> CrossUpdate | None:
        """Despacha o texto não-resolvido para a regra adequada."""
        producer = self._producer_domain(rel.resolver)
        if is_absolute_url(text):
            # lado só-externo: URL absoluta nunca vira endpoint local.
            return self._mk_update(
                rel, ResolutionStatus.EXTERNAL,
                ConfidenceClass(rel.confidence_class),
                to_unresolved=text, ctx=ctx,
            )
        arn = _arn_parts(text)
        if arn is not None:
            return self._resolve_arn(rel, arn, producer, ctx)
        if rel.relation_type == str(DomainRelationKind.CALLS_ENDPOINT):
            return self._resolve_endpoint(rel, text, ctx)
        path = _repo_path(text)
        if path is not None and (
            "/" in path or path in ctx.file_occs or ctx.by_path.get(path)
        ):
            # path "definitivo": com separador, ou literalmente presente
            # no inventário. Nome nu sem "/" e fora do inventário cai
            # para o parse de nome qualificado → diagnóstico honesto.
            return self._resolve_repo_path(rel, path, producer, ctx)
        parses = _name_parses(text)
        if parses:
            return self._resolve_qualified(rel, text, parses, producer, ctx)
        ctx.diagnostic(
            "unqualified-target", "info",
            f"relation {rel.relation_id} target {text!r} carries no "
            "qualifier — never resolved by name alone",
        )
        return None

    @staticmethod
    def _producer_domain(resolver: str) -> str:
        """Domínio produtor da row — ``resolver='domain:<id>'`` padrão."""
        return resolver[len("domain:"):] if resolver.startswith("domain:") else ""

    def _mk_update(
        self,
        rel: _RelRow,
        status: ResolutionStatus,
        confidence: ConfidenceClass,
        ctx: _Ctx,
        *,
        to_entity_occ_id: int | None = None,
        to_symbol_occ_id: int | None = None,
        to_unresolved: str | None = None,
        extra_evidence: tuple[int, ...] = (),
    ) -> CrossUpdate:
        ev = sorted(set(rel.evidence_ids) | set(extra_evidence))
        return CrossUpdate(
            relation_id=rel.relation_id,
            resolution_status=status,
            confidence_class=confidence,
            to_entity_occ_id=to_entity_occ_id,
            to_symbol_occ_id=to_symbol_occ_id,
            to_unresolved=to_unresolved,
            evidence_ids=tuple(ev),
        )

    # -- regra: nome qualificado de dados ---------------------------------

    def _resolve_qualified(
        self,
        rel: _RelRow,
        text: str,
        parses: list[_RefParts],
        producer: str,
        ctx: _Ctx,
    ) -> CrossUpdate | None:
        """Casa parses com entidades de **outros** domínios do snapshot.

        FULL único → resolved. FULL múltiplo ou PARTIAL ≥1 → ambiguous
        (candidatos preservados no texto). NAME_ONLY → diagnóstico.
        """
        full, partial, bare = self._qualified_candidates(
            parses, producer, ctx
        )
        if len(full) == 1:
            occ = next(iter(full.values()))
            evi = self._occ_evidence(occ, ctx)
            if not evi:
                return self._missing_evidence(rel, occ, ctx)
            return self._mk_update(
                rel, ResolutionStatus.RESOLVED, ConfidenceClass.RESOLVED,
                to_entity_occ_id=occ.occ_id, extra_evidence=evi, ctx=ctx,
            )
        if len(full) > 1:
            return self._ambiguous(rel, text, sorted(full.values(),
                                                   key=lambda o: o.occ_id), ctx)
        if partial:
            return self._ambiguous(
                rel, text,
                sorted(partial.values(), key=lambda o: o.occ_id), ctx,
            )
        if bare:
            ctx.diagnostic(
                "unqualified-target", "info",
                f"relation {rel.relation_id} target {text!r} is a bare "
                "name — no qualifier, never resolved",
            )
        else:
            ctx.diagnostic(
                "no-cross-target", "info",
                f"relation {rel.relation_id} target {text!r} has no "
                "cross-domain entity candidate in this snapshot",
            )
        return None

    @staticmethod
    def _qualified_candidates(
        parses: list[_RefParts], producer: str, ctx: _Ctx
    ) -> tuple[dict[int, _Occ], dict[int, _Occ], bool]:
        """Scan de candidatos: ``(full, partial, bare)`` por agreement."""
        full: dict[int, _Occ] = {}
        partial: dict[int, _Occ] = {}
        bare = all(not (p.catalog or p.namespace) for p in parses)
        for p in parses:
            for occ in ctx.occs.values():
                if producer and occ.identity.domain == producer:
                    continue  # cross-domain: alvo precisa ser outro adapter
                ag = _agreement(p, occ.identity)
                if ag is Agreement.FULL:
                    full.setdefault(occ.occ_id, occ)
                elif ag is Agreement.PARTIAL:
                    partial.setdefault(occ.occ_id, occ)
        return full, partial, bare

    def _ambiguous(
        self, rel: _RelRow, text: str, cands: list[_Occ], ctx: _Ctx
    ) -> CrossUpdate:
        """Ambiguous honesto: todos os candidatos ficam no ``to_unresolved``."""
        disp = ", ".join(sorted(o.identity.display() for o in cands))
        return self._mk_update(
            rel, ResolutionStatus.AMBIGUOUS, ConfidenceClass.SYNTACTIC,
            to_unresolved=f"{text} [candidates: {disp}]", ctx=ctx,
        )

    def _missing_evidence(
        self, rel: _RelRow, occ: _Occ, ctx: _Ctx
    ) -> CrossUpdate | None:
        """Candidato sem evidência localizável → não resolve (evidence-first)."""
        ctx.missing_evidence += 1
        ctx.diagnostic(
            "missing-evidence", "warning",
            f"relation {rel.relation_id} candidate "
            f"{occ.identity.display()} has no locatable source evidence "
            "— not resolved",
        )
        return None

    # -- regra: consumidor ↔ endpoint (method+path literal) ---------------

    def _resolve_endpoint(
        self, rel: _RelRow, text: str, ctx: _Ctx
    ) -> CrossUpdate | None:
        """``CALLS_ENDPOINT`` — igualdade literal de method+path.

        Regra interna do domínio ``api`` (o adapter declara os
        candidatos mas não resolve — handoff explícito da Phase 9):
        vale para endpoints de qualquer domínio produtor, exige
        ``kind=api_endpoint``. URL absoluta já saiu como ``external``
        antes daqui; path é extraído de ``url_path`` (sem urllib).
        """
        path = url_path(text)
        declared = self._declared_method(rel, ctx)
        cands: dict[int, _Occ] = {}
        for occ in ctx.occs.values():
            if occ.kind != str(DomainEntityKind.API_ENDPOINT):
                continue
            if _endpoint_path(occ) != path:
                continue
            if declared and _endpoint_method(occ) not in (declared, "ANY"):
                continue
            cands[occ.occ_id] = occ
        if len(cands) == 1:
            occ = next(iter(cands.values()))
            evi = self._occ_evidence(occ, ctx)
            if not evi:
                return self._missing_evidence(rel, occ, ctx)
            return self._mk_update(
                rel, ResolutionStatus.RESOLVED, ConfidenceClass.RESOLVED,
                to_entity_occ_id=occ.occ_id, extra_evidence=evi, ctx=ctx,
            )
        if len(cands) > 1:
            return self._ambiguous(
                rel, text,
                sorted(cands.values(), key=lambda o: o.occ_id), ctx,
            )
        ctx.diagnostic(
            "endpoint-unmatched", "info",
            f"relation {rel.relation_id} call literal {text!r} matched "
            "no api_endpoint entity (method+path)",
        )
        return None

    @staticmethod
    def _declared_method(rel: _RelRow, ctx: _Ctx) -> str:
        """``method=X`` declarado em qualquer evidência da relação."""
        for eid in rel.evidence_ids:
            row = ctx.evidence.get(eid)
            if row is None:
                continue
            m = _METHOD_NOTE_RE.match(str(row["notes"] or ""))
            if m:
                return m.group(1)
        return ""

    # -- regra: ARN AWS (conta+região+recurso) ----------------------------

    def _resolve_arn(
        self,
        rel: _RelRow,
        arn: tuple[str, str, str],
        producer: str,
        ctx: _Ctx,
    ) -> CrossUpdate | None:
        """ARN → entidade com catálogo ``*:<conta>:<região>`` + nome.

        Sem entidade local que prove conta+região+recurso, o ARN é
        externo por definição — status sobe para ``external`` honesto.
        """
        account, region, name = arn
        suffix = f":{account}:{region}" if (account or region) else ""
        cands: dict[int, _Occ] = {}
        partial: dict[int, _Occ] = {}
        for occ in ctx.occs.values():
            if producer and occ.identity.domain == producer:
                continue
            cat = occ.identity.catalog
            if occ.identity.qualified_name != name:
                continue
            if suffix and cat.endswith(suffix):
                cands[occ.occ_id] = occ
            elif cat:
                partial[occ.occ_id] = occ
        if len(cands) == 1:
            occ = next(iter(cands.values()))
            evi = self._occ_evidence(occ, ctx)
            if not evi:
                return self._missing_evidence(rel, occ, ctx)
            return self._mk_update(
                rel, ResolutionStatus.RESOLVED, ConfidenceClass.RESOLVED,
                to_entity_occ_id=occ.occ_id, extra_evidence=evi, ctx=ctx,
            )
        if len(cands) > 1:
            return self._ambiguous(
                rel, rel.to_unresolved or "",
                sorted(cands.values(), key=lambda o: o.occ_id), ctx,
            )
        if partial:
            return self._ambiguous(
                rel, rel.to_unresolved or "",
                sorted(partial.values(), key=lambda o: o.occ_id), ctx,
            )
        return self._mk_update(
            rel, ResolutionStatus.EXTERNAL,
            ConfidenceClass(rel.confidence_class),
            to_unresolved=rel.to_unresolved or "", ctx=ctx,
        )

    # -- regra: path de artefato do repo ----------------------------------

    def _resolve_repo_path(
        self, rel: _RelRow, path: str, producer: str, ctx: _Ctx
    ) -> CrossUpdate | None:
        """Path literal inventariado → entidade naquele path ou símbolo FILE.

        Preferência: entidade de **outro domínio** observada naquele
        arquivo (alvo semântico) > ocorrência do símbolo ``file`` do
        próprio path (ponte domínio→código, lf-0117/0120).
        """
        ents = [
            o for o in ctx.by_path.get(path, ())
            if not producer or o.identity.domain != producer
        ]
        if len(ents) == 1:
            occ = ents[0]
            evi = self._occ_evidence(occ, ctx)
            if not evi:
                return self._missing_evidence(rel, occ, ctx)
            return self._mk_update(
                rel, ResolutionStatus.RESOLVED, ConfidenceClass.RESOLVED,
                to_entity_occ_id=occ.occ_id, extra_evidence=evi, ctx=ctx,
            )
        if len(ents) > 1:
            return self._ambiguous(rel, path, sorted(ents, key=lambda o: o.occ_id), ctx)
        occ_ids = ctx.file_occs.get(path, ())
        if len(occ_ids) == 1:
            return self._mk_update(
                rel, ResolutionStatus.RESOLVED, ConfidenceClass.RESOLVED,
                to_symbol_occ_id=occ_ids[0], ctx=ctx,
            )
        if len(occ_ids) > 1:
            return self._mk_update(
                rel, ResolutionStatus.AMBIGUOUS, ConfidenceClass.SYNTACTIC,
                to_unresolved=f"{path} [candidates: {len(occ_ids)} file "
                "occurrences]", ctx=ctx,
            )
        ctx.diagnostic(
            "no-cross-target", "info",
            f"relation {rel.relation_id} path target {path!r} not in "
            "this snapshot inventory",
        )
        return None

    # -- arestas novas entidade↔entidade (M2) ------------------------------

    def _plan_edges(self, ctx: _Ctx) -> list[CrossEdge]:
        """Pares de entidades de domínios distintos com identidade ligada.

        Dois vínculos reconhecidos:

        - **identidade qualificada plena** (mesmo ``qualified_name`` +
          qualificadores iguais) → ``REFERENCES`` resolved — mesma coisa
          lógica observada por dois adapters;
        - **path-anchor**: atributo-path de A == arquivo-fonte de B →
          aresta resolved (``PROVISIONS`` quando A é Terraform — o caso
          canônico ``aws_glue_job`` ↔ script; ``REFERENCES`` demais).

        Acordo parcial → aresta ``REFERENCES`` ambiguous com ambos os
        candidatos no texto. Nome nu → diagnóstico, nunca aresta.
        """
        proposed: set[frozenset[int]] = set(ctx.linked)
        edges = self._qualified_pair_edges(ctx, proposed)
        edges.extend(self._path_anchor_edges(ctx, proposed))
        edges.sort(
            key=lambda e: (
                e.from_entity_occ_id or 0,
                e.to_entity_occ_id or 0,
                e.relation_type,
            )
        )
        return edges

    def _qualified_pair_edges(
        self, ctx: _Ctx, proposed: set[frozenset[int]]
    ) -> list[CrossEdge]:
        """Mesmo ``qualified_name`` em domínios distintos → identidade."""
        by_qname: dict[str, list[_Occ]] = {}
        for occ in ctx.occs.values():
            by_qname.setdefault(occ.identity.qualified_name, []).append(occ)
        edges: list[CrossEdge] = []
        for qname in sorted(by_qname):
            group = sorted(by_qname[qname], key=lambda o: o.occ_id)
            for i, a in enumerate(group):
                for b in group[i + 1:]:
                    if a.identity.domain == b.identity.domain:
                        continue
                    pair = frozenset((a.occ_id, b.occ_id))
                    if pair in proposed:
                        continue
                    edge = self._pair_edge(a, b, qname, ctx)
                    if edge is not None:
                        edges.append(edge)
                        proposed.add(pair)
        return edges

    def _pair_edge(
        self, a: _Occ, b: _Occ, qname: str, ctx: _Ctx
    ) -> CrossEdge | None:
        """Um par cross-domain: FULL → aresta, PARTIAL → ambiguous,
        NAME_ONLY → diagnóstico (nunca aresta)."""
        ag = _pair_agreement(a.identity, b.identity)
        if ag is Agreement.FULL:
            return self._mk_edge(
                a, b, str(DomainRelationKind.REFERENCES),
                ResolutionStatus.RESOLVED, ctx,
            )
        if ag is Agreement.PARTIAL:
            return self._mk_ambiguous_edge(a, b, ctx)
        if ag is Agreement.NAME_ONLY:
            ctx.diagnostic(
                "name-only-collision", "info",
                f"{a.identity.display()} vs {b.identity.display()} "
                f"share bare name {qname!r} — no qualified agreement, "
                "no edge",
            )
        return None

    def _path_anchor_edges(
        self, ctx: _Ctx, proposed: set[frozenset[int]]
    ) -> list[CrossEdge]:
        """Atributo-path de A ancorado no repo — entidade↔entidade ou
        entidade→símbolo FILE.

        Preferência: entidade de outro domínio observada no arquivo do
        anchor (alvo semântico — ``aws_glue_job`` ↔ job que executa o
        script). Sem entidade, cai para o símbolo FILE do próprio path
        ("CI publica artefato presente no repo") — mas só se o arquivo
        tiver evidência-fonte localizável; sem ela, diagnóstico em vez
        de aresta.
        """
        out: list[CrossEdge] = []
        for a in sorted(ctx.occs.values(), key=lambda o: o.occ_id):
            for anchor in sorted(self._path_anchors(a)):
                if anchor == a.source_path:
                    continue  # auto-referência ao próprio artefato
                targets = [
                    o for o in ctx.by_path.get(anchor, ())
                    if o.identity.domain != a.identity.domain
                ]
                for b in sorted(targets, key=lambda o: o.occ_id):
                    pair = frozenset((a.occ_id, b.occ_id))
                    if pair in proposed:
                        continue
                    rtype = (
                        str(DomainRelationKind.PROVISIONS)
                        if a.identity.domain == "terraform"
                        else str(DomainRelationKind.REFERENCES)
                    )
                    edge = self._mk_edge(
                        a, b, rtype, ResolutionStatus.RESOLVED, ctx,
                    )
                    if edge is not None:
                        out.append(edge)
                        proposed.add(pair)
                if targets:
                    continue
                edge = self._file_anchor_edge(a, anchor, ctx)
                if edge is not None:
                    out.append(edge)
        return out

    def _file_anchor_edge(
        self, a: _Occ, anchor: str, ctx: _Ctx
    ) -> CrossEdge | None:
        """Anchor sem entidade de domínio → aresta para o símbolo FILE."""
        occ_ids = ctx.file_occs.get(anchor, ())
        if len(occ_ids) != 1:
            return None
        evi_a = self._occ_evidence(a, ctx)
        if not evi_a:
            ctx.missing_evidence += 1
            return None
        # evidência do lado arquivo: qualquer extractor — o fato-fonte é
        # a existência do arquivo no inventário do snapshot.
        evi_b = [
            int(e["id"]) for e in ctx.evidence.values()
            if str(e["path"]) == anchor
        ]
        if not evi_b:
            ctx.missing_evidence += 1
            ctx.diagnostic(
                "missing-evidence", "info",
                f"edge {a.identity.display()} → file {anchor!r} dropped: "
                "no evidence rows for the artifact file",
            )
            return None
        rel = DomainRelation(
            snapshot_id=0,
            relation_type=str(DomainRelationKind.REFERENCES),
            resolver=CROSS_RESOLVER_ID,
            confidence_class=ConfidenceClass.RESOLVED,
            resolution_status=ResolutionStatus.RESOLVED,
            from_entity_occ_id=a.occ_id, to_symbol_occ_id=occ_ids[0],
        )
        return self._edge_of(
            rel, tuple(sorted(set(evi_a) | set(evi_b)))
        )

    @staticmethod
    def _path_anchors(occ: _Occ) -> set[str]:
        """Valores-atributo que são paths relativos do repo."""
        out: set[str] = set()
        for key, value in occ.attributes.items():
            if key not in _PATH_ATTR_KEYS:
                continue
            for piece in value.split(","):
                p = piece.strip()
                if p.startswith("./"):
                    p = p[2:]
                if p and "/" in p and "://" not in p:
                    out.add(p)
        return out

    def _mk_edge(
        self,
        a: _Occ,
        b: _Occ,
        rtype: str,
        status: ResolutionStatus,
        ctx: _Ctx,
        *,
        to_unresolved: str | None = None,
    ) -> CrossEdge | None:
        """Monta aresta com evidência dos DOIS lados — ou nada.

        Direção determinística para ``REFERENCES`` de identidade
        qualificada: o ``from`` é o domínio lexicograficamente maior
        (o vínculo é o que importa; a direção não carrega semântica).
        Sem evidência localizável de um dos lados → diagnóstico e
        nenhuma aresta (evidence-first é inegociável).
        """
        if (
            rtype == str(DomainRelationKind.REFERENCES)
            and a.identity.domain < b.identity.domain
        ):
            a, b = b, a
        evi_a = self._occ_evidence(a, ctx)
        evi_b = self._occ_evidence(b, ctx)
        if not evi_a or not evi_b:
            ctx.missing_evidence += 1
            ctx.diagnostic(
                "missing-evidence", "warning",
                f"edge {a.identity.display()} → {b.identity.display()} "
                "dropped: source evidence missing on one side",
            )
            return None
        to_occ = b.occ_id if to_unresolved is None else None
        # DomainRelation é a fonte única: construir primeiro valida as
        # invariantes do modelo (endpoint obrigatório, inferred≠resolved)
        # e o CrossEdge deriva dos campos coercionados — zero drift.
        rel = DomainRelation(
            snapshot_id=0, relation_type=rtype, resolver=CROSS_RESOLVER_ID,
            confidence_class=(
                ConfidenceClass.RESOLVED
                if status is ResolutionStatus.RESOLVED
                else ConfidenceClass.SYNTACTIC
            ),
            resolution_status=status,
            from_entity_occ_id=a.occ_id, to_entity_occ_id=to_occ,
            to_unresolved=to_unresolved,
        )
        return self._edge_of(rel, tuple(sorted(set(evi_a) | set(evi_b))))

    def _mk_ambiguous_edge(self, a: _Occ, b: _Occ, ctx: _Ctx) -> CrossEdge:
        """Aresta ``ambiguous``: ambos os candidatos ficam no texto."""
        evi = sorted(
            set(self._occ_evidence(a, ctx)) | set(self._occ_evidence(b, ctx))
        )
        disp = " | ".join(
            sorted({a.identity.display(), b.identity.display()})
        )
        rel = DomainRelation(
            snapshot_id=0,
            relation_type=str(DomainRelationKind.REFERENCES),
            resolver=CROSS_RESOLVER_ID,
            confidence_class=ConfidenceClass.SYNTACTIC,
            resolution_status=ResolutionStatus.AMBIGUOUS,
            from_entity_occ_id=a.occ_id,
            to_unresolved=f"[ambiguous candidates: {disp}]",
        )
        return self._edge_of(rel, tuple(evi))

    @staticmethod
    def _edge_of(rel: DomainRelation, evi: tuple[int, ...]) -> CrossEdge:
        """``DomainRelation`` validada → ``CrossEdge`` (campos coercionados)."""
        return CrossEdge(
            relation_type=rel.relation_type,
            resolution_status=rel.resolution_status,
            confidence_class=rel.confidence_class,
            from_entity_occ_id=rel.from_entity_occ_id,
            from_symbol_occ_id=rel.from_symbol_occ_id,
            to_entity_occ_id=rel.to_entity_occ_id,
            to_symbol_occ_id=rel.to_symbol_occ_id,
            to_unresolved=rel.to_unresolved,
            evidence_ids=evi,
        )


@dataclass
class _Ctx:
    """Contexto mutável interno de um ``plan`` (não é API pública)."""

    occs: dict[int, _Occ]
    by_path: dict[str, list[_Occ]]
    evidence: dict[int, sqlite3.Row]
    file_occs: dict[str, list[int]]
    linked: set[frozenset[int]]
    diagnostics: list[CrossDiagnostic] = field(default_factory=list)
    considered: int = 0
    missing_evidence: int = 0

    def diagnostic(self, code: str, severity: str, message: str) -> None:
        self.diagnostics.append(
            CrossDiagnostic(code=code, severity=severity, message=message)
        )
