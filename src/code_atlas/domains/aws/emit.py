"""Sink de extração do adapter ``domains.aws`` (lf-0113, Cycle 7 Ph.5).

Acumulador determinístico da saída de ``extract`` — mesmo desenho de
``domains.api.emit.Emit``/``domains.terraform`` ``_Builder``:

- fábrica de ``DomainIdentity`` com ``catalog`` (coordenadas AWS
  explícitas via ``common.aws_catalog`` — nunca por convenção);
- evidência ``domain:aws-data`` dedup por ``(path, span)``;
- entidades dedup por ``logical_key`` com **merge de evidência** (a
  mesma tabela ``db.t`` lida por dois jobs é a mesma entidade lógica);
- relações dedup por ``(tipo, origem, alvo)`` com ``DomainResolution``
  tipada — ``resolved`` só quando o alvo é entidade deste resultado,
  ``external`` para recursos fora do snapshot (ARNs, catálogos
  referenciados mas não declarados), ``unresolved`` para texto
  preservado/dinâmico;
- registro de catálogos declarados (``spark.sql.catalog.<n>``,
  ``catalog_id`` de Glue) resolvendo ``REFERENCES`` de catálogo ao fim
  do request — nomes não declarados viram ``external``, nunca
  fabricados.
"""

from __future__ import annotations

from dataclasses import dataclass

from code_atlas.contracts.enums import ConfidenceClass, ResolutionStatus
from code_atlas.domains.contracts import (
    DomainDiagnostic,
    DomainExtractionRequest,
    DomainExtractionResult,
    ExtractedDomainEntity,
    ExtractedDomainRelation,
)
from code_atlas.domains.evidence import domain_extractor_id, evidence_record
from code_atlas.domains.identity import DomainIdentity
from code_atlas.domains.model import DomainEntityKind
from code_atlas.domains.resolution import DomainResolution
from code_atlas.parsers.contracts import EvidenceRecord, SourceSpan

ADAPTER_DOMAIN = "aws-data"
RESOLVER_ID = domain_extractor_id(ADAPTER_DOMAIN)

_DIAG_CAP = 120  # diagnósticos por request — depois vira resumo


@dataclass(frozen=True)
class PendingCatalogRef:
    """Referência a catálogo nomeado — resolvida ao fim do request."""

    from_id: DomainIdentity
    catalog: str  # segmento ``catalog`` escrito no artefato
    path: str
    span: SourceSpan | None
    evi: int


class Sink:
    """Acumulador da saída de ``AwsDataAdapter.extract``."""

    def __init__(self, request: DomainExtractionRequest, adapter_version: str) -> None:
        self._request = request
        self._version = adapter_version
        self.entities: dict[str, ExtractedDomainEntity] = {}
        self.relations: dict[tuple[str, str, str], ExtractedDomainRelation] = {}
        self.evidence: list[EvidenceRecord] = []
        self._evi_index: dict[tuple[str, tuple[int, ...] | None], int] = {}
        self.diagnostics: list[DomainDiagnostic] = []
        self._truncated_diags = 0
        self.stats: dict[str, int] = {}
        # catálogos declarados no request: segmento ``catalog`` → id
        self.catalogs: dict[str, DomainIdentity] = {}
        self.pending_cat_refs: list[PendingCatalogRef] = []
        # nome declarado → identidade (lookup p/ TRIGGERS→job etc.)
        self.by_name: dict[tuple[str, str], DomainIdentity] = {}

    # -- fábricas -----------------------------------------------------

    @property
    def repository(self) -> str:
        return self._request.repository

    @property
    def root(self) -> str:
        return self._request.root

    @property
    def environment(self) -> str:
        return self._request.environment

    @property
    def version(self) -> str:
        return self._version

    def ident(
        self,
        qualified_name: str,
        *,
        catalog: str = "",
        namespace: str = "",
    ) -> DomainIdentity:
        req = self._request
        return DomainIdentity(
            domain=ADAPTER_DOMAIN,
            repository=req.repository,
            qualified_name=qualified_name,
            root=req.root,
            catalog=catalog,
            namespace=namespace,
            environment=req.environment,
        )

    def evi(
        self,
        path: str,
        content_hash: str,
        span: SourceSpan | None = None,
        notes: str = "",
    ) -> int:
        """Evidência dedup por ``(path, span)`` → índice no envelope."""
        key = (
            path,
            None
            if span is None
            else (
                span.start_byte,
                span.end_byte,
                span.start_line,
                span.start_col,
                span.end_line,
                span.end_col,
            ),
        )
        idx = self._evi_index.get(key)
        if idx is not None:
            return idx
        idx = len(self.evidence)
        self.evidence.append(
            evidence_record(
                adapter_domain=ADAPTER_DOMAIN,
                adapter_version=self._version,
                path=path,
                content_hash=content_hash,
                span=span,
                notes=notes,
            )
        )
        self._evi_index[key] = idx
        return idx

    def diag(
        self,
        code: str,
        severity: str,
        message: str,
        path: str = "",
    ) -> None:
        if len(self.diagnostics) >= _DIAG_CAP:
            self._truncated_diags += 1
            return
        self.diagnostics.append(
            DomainDiagnostic(
                code=code,
                severity=severity,
                message=message,
                path=path,
                adapter_id=ADAPTER_DOMAIN,
            )
        )

    def bump(self, stat: str, n: int = 1) -> None:
        self.stats[stat] = self.stats.get(stat, 0) + n

    # -- entidades ------------------------------------------------------

    def entity(
        self,
        *,
        kind: DomainEntityKind,
        name: str,
        qualified_name: str,
        catalog: str = "",
        namespace: str = "",
        path: str = "",
        span: SourceSpan | None = None,
        content_hash: str = "",
        attributes: dict[str, str] | None = None,
        evidence: tuple[int, ...] = (),
    ) -> DomainIdentity:
        """Registra entidade dedup por ``logical_key``; merge de evidência.

        Em reemissão, evidência acumula e os primeiros atributos vencem
        (determinístico); kinds conflitantes para a mesma identidade
        viram ``conflicting-kind`` — nunca merge silencioso de tipo.
        """
        ident = self.ident(qualified_name, catalog=catalog, namespace=namespace)
        return self.entity_for(
            ident,
            kind=kind,
            name=name,
            path=path,
            span=span,
            content_hash=content_hash,
            attributes=attributes,
            evidence=evidence,
        )

    def entity_for(
        self,
        ident: DomainIdentity,
        *,
        kind: DomainEntityKind,
        name: str,
        path: str = "",
        span: SourceSpan | None = None,
        content_hash: str = "",
        attributes: dict[str, str] | None = None,
        evidence: tuple[int, ...] = (),
    ) -> DomainIdentity:
        """Mesma dedup de ``entity`` mas com identidade pronta."""
        key = ident.logical_key()
        prev = self.entities.get(key)
        if prev is not None:
            merged_ev = tuple(sorted({*prev.evidence, *evidence}))
            if prev.kind is not kind:
                self.diag(
                    "conflicting-kind",
                    "warning",
                    f"{ident.display()} emitted as both "
                    f"{prev.kind.value} and {kind.value} — first kept",
                    path or prev.path,
                )
            merged_attrs = dict(prev.attributes)
            merged_attrs.update(
                {k: v for k, v in (attributes or {}).items() if k not in merged_attrs}
            )
            self.entities[key] = ExtractedDomainEntity(
                identity=prev.identity,
                kind=prev.kind,
                name=prev.name,
                path=prev.path,
                span=prev.span,
                content_hash=prev.content_hash or content_hash,
                attributes=merged_attrs,
                evidence=merged_ev,
            )
            return ident
        self.entities[key] = ExtractedDomainEntity(
            identity=ident,
            kind=kind,
            name=name,
            path=path,
            span=span,
            content_hash=content_hash,
            attributes=attributes or {},
            evidence=evidence,
        )
        self.by_name.setdefault((kind.value, name), ident)
        self.bump(f"entities_{kind.value}")
        return ident

    # -- relações ------------------------------------------------------

    def resolution(
        self,
        status: ResolutionStatus,
        confidence: ConfidenceClass = ConfidenceClass.SYNTACTIC,
        *,
        target: DomainIdentity | None = None,
        unresolved_text: str = "",
    ) -> DomainResolution:
        return DomainResolution(
            status=status,
            confidence_class=confidence,
            resolver=RESOLVER_ID,
            target=target,
            unresolved_text=unresolved_text,
        )

    def rel(
        self,
        relation_type: str,
        path: str,
        from_identity: DomainIdentity,
        *,
        to_identity: DomainIdentity | None = None,
        to_unresolved: str = "",
        status: ResolutionStatus | None = None,
        confidence: ConfidenceClass = ConfidenceClass.SYNTACTIC,
        span: SourceSpan | None = None,
        evidence: tuple[int, ...] = (),
    ) -> None:
        """Relação com estado honesto; dedup por (tipo, origem, alvo).

        ``status=None`` → ``resolved`` quando ``to_identity`` é dado,
        ``unresolved`` para alvo textual. ``external`` explícito para
        alvos fora do snapshot (ARNs, catálogos não declarados).
        """
        if status is None:
            status = (
                ResolutionStatus.RESOLVED
                if to_identity is not None
                else ResolutionStatus.UNRESOLVED
            )
        to_key = to_identity.logical_key() if to_identity is not None else f"text:{to_unresolved}"
        key = (relation_type, from_identity.logical_key(), to_key)
        prev = self.relations.get(key)
        if prev is not None:
            merged_ev = tuple(sorted({*prev.evidence, *evidence}))
            self.relations[key] = ExtractedDomainRelation(
                relation_type=prev.relation_type,
                path=prev.path,
                from_identity=prev.from_identity,
                to_identity=prev.to_identity,
                to_unresolved=prev.to_unresolved,
                resolution=prev.resolution,
                span=prev.span,
                evidence=merged_ev,
            )
            return
        res = self.resolution(
            status,
            confidence,
            target=to_identity if status is not ResolutionStatus.UNRESOLVED else None,
            unresolved_text=(to_unresolved if status is not ResolutionStatus.RESOLVED else ""),
        )
        self.relations[key] = ExtractedDomainRelation(
            relation_type=relation_type,
            path=path,
            from_identity=from_identity,
            to_identity=to_identity,
            to_unresolved=(to_unresolved if status is not ResolutionStatus.RESOLVED else ""),
            resolution=res,
            span=span,
            evidence=evidence,
        )

    # -- catálogos -------------------------------------------------------

    def register_catalog(self, catalog_segment: str, ident: DomainIdentity) -> None:
        """Catálogo declarado (conf/catalog_id) → lookup para REFERENCES."""
        if catalog_segment:
            self.catalogs.setdefault(catalog_segment, ident)

    def catalog_ref(
        self,
        from_id: DomainIdentity,
        catalog_segment: str,
        path: str,
        span: SourceSpan | None,
        evi: int,
    ) -> None:
        """Adia ``REFERENCES`` a catálogo nomeado até ``finish()``.

        O catálogo pode ser declarado por outro artefato do request —
        resolução só é honesta com o conjunto completo.
        """
        if catalog_segment:
            self.pending_cat_refs.append(
                PendingCatalogRef(from_id, catalog_segment, path, span, evi)
            )

    # -- saída ----------------------------------------------------------

    def finish(self) -> DomainExtractionResult:
        """Resolve referências pendentes e monta o envelope final."""
        for ref in self.pending_cat_refs:
            target = self.catalogs.get(ref.catalog)
            if target is not None:
                self.rel(
                    "REFERENCES",
                    ref.path,
                    ref.from_id,
                    to_identity=target,
                    span=ref.span,
                    evidence=(ref.evi,),
                )
            else:
                self.rel(
                    "REFERENCES",
                    ref.path,
                    ref.from_id,
                    to_unresolved=f"catalog:{ref.catalog}",
                    status=ResolutionStatus.EXTERNAL,
                    span=ref.span,
                    evidence=(ref.evi,),
                )
        self.pending_cat_refs.clear()
        if self._truncated_diags:
            self.diagnostics.append(
                DomainDiagnostic(
                    code="diagnostics-truncated",
                    severity="info",
                    message=(
                        f"{self._truncated_diags} further diagnostics suppressed (cap {_DIAG_CAP})"
                    ),
                    adapter_id=ADAPTER_DOMAIN,
                )
            )
        stats = dict(self.stats)
        stats["entities"] = len(self.entities)
        stats["relations"] = len(self.relations)
        stats["diagnostics"] = len(self.diagnostics)
        return DomainExtractionResult(
            domain_id=ADAPTER_DOMAIN,
            adapter_version=self._version,
            entities=tuple(self.entities.values()),
            relations=tuple(self.relations.values()),
            evidence=tuple(self.evidence),
            diagnostics=tuple(self.diagnostics),
            stats=stats,
        )
