"""Sink de extração do adapter ``domains.api`` (lf-0114).

Acumula entidades, relações, evidência e diagnósticos com dedup por
``logical_key``/``(path, span)`` e monta o ``DomainExtractionResult``
final. Centraliza também:

- a fábrica de ``DomainIdentity`` (domain/repository/root/environment
  vêm do request — o adapter só escolhe ``qualified_name``/``namespace``);
- o índice ``path → endpoints`` usado para **candidatos** de
  ``CALLS_ENDPOINT`` (a resolução literal-URL↔contrato é da Phase 9 —
  aqui só registramos candidatos, nunca resolvemos).
"""

from __future__ import annotations

from code_atlas.contracts.enums import ConfidenceClass, ResolutionStatus
from code_atlas.domains.contracts import (
    DomainDiagnostic,
    DomainExtractionRequest,
    DomainExtractionResult,
    ExtractedDomainEntity,
    ExtractedDomainRelation,
)
from code_atlas.domains.evidence import evidence_record
from code_atlas.domains.identity import DomainIdentity
from code_atlas.domains.model import DomainEntityKind
from code_atlas.domains.resolution import DomainResolution
from code_atlas.parsers.contracts import EvidenceRecord, SourceSpan

ADAPTER_DOMAIN = "api"
RESOLVER_ID = "domain:api"


class Emit:
    """Acumulador determinístico da saída de ``extract``."""

    def __init__(
        self, request: DomainExtractionRequest, adapter_version: str
    ) -> None:
        self._request = request
        self._version = adapter_version
        self.entities: list[ExtractedDomainEntity] = []
        self.relations: list[ExtractedDomainRelation] = []
        self.evidence: list[EvidenceRecord] = []
        self.diagnostics: list[DomainDiagnostic] = []
        self.stats: dict[str, int] = {}
        self._evi_index: dict[tuple[str, int], int] = {}
        self._entity_keys: set[str] = set()
        self.endpoint_paths: dict[str, list[DomainIdentity]] = {}

    # -- fábricas ----------------------------------------------------

    def ident(self, qualified_name: str, namespace: str = "") -> DomainIdentity:
        req = self._request
        return DomainIdentity(
            domain=ADAPTER_DOMAIN,
            repository=req.repository,
            qualified_name=qualified_name,
            root=req.root,
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
        """Evidência dedup por ``(path, start_byte)`` → índice no envelope."""
        key = (path, span.start_byte if span else -1)
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
        self.diagnostics.append(
            DomainDiagnostic(
                code=code, severity=severity, message=message,
                path=path, adapter_id=ADAPTER_DOMAIN,
            )
        )

    def bump(self, stat: str, n: int = 1) -> None:
        self.stats[stat] = self.stats.get(stat, 0) + n

    # -- entidades / relações -----------------------------------------

    def entity(
        self,
        *,
        kind: DomainEntityKind,
        name: str,
        qualified_name: str,
        namespace: str = "",
        path: str = "",
        span: SourceSpan | None = None,
        content_hash: str = "",
        attributes: dict[str, str] | None = None,
        evidence: tuple[int, ...] = (),
    ) -> DomainIdentity:
        """Registra entidade dedup por ``logical_key``; devolve a identidade."""
        ident = self.ident(qualified_name, namespace)
        key = ident.logical_key()
        if key in self._entity_keys:
            self.diag(
                "duplicate-entity", "warning",
                f"entity {ident.display()} already emitted — first kept",
                path,
            )
            return ident
        self._entity_keys.add(key)
        self.entities.append(
            ExtractedDomainEntity(
                identity=ident,
                kind=kind,
                name=name,
                path=path,
                span=span,
                content_hash=content_hash,
                attributes=attributes or {},
                evidence=evidence,
            )
        )
        return ident

    def endpoint(
        self,
        *,
        name: str,
        qualified_name: str,
        namespace: str,
        full_path: str,
        path: str = "",
        span: SourceSpan | None = None,
        content_hash: str = "",
        attributes: dict[str, str] | None = None,
        evidence: tuple[int, ...] = (),
    ) -> DomainIdentity:
        """``APIEndpoint`` + indexação de ``full_path`` para candidatos."""
        ident = self.entity(
            kind=DomainEntityKind.API_ENDPOINT,
            name=name,
            qualified_name=qualified_name,
            namespace=namespace,
            path=path,
            span=span,
            content_hash=content_hash,
            attributes=attributes,
            evidence=evidence,
        )
        self.endpoint_paths.setdefault(full_path, []).append(ident)
        return ident

    def resolution(
        self,
        status: ResolutionStatus,
        confidence: ConfidenceClass,
        *,
        target: DomainIdentity | None = None,
        candidates: tuple[DomainIdentity, ...] = (),
        unresolved_text: str = "",
    ) -> DomainResolution:
        return DomainResolution(
            status=status,
            confidence_class=confidence,
            resolver=RESOLVER_ID,
            target=target,
            candidates=candidates,
            unresolved_text=unresolved_text,
        )

    def relation(
        self,
        *,
        relation_type: str,
        path: str,
        from_identity: DomainIdentity | None = None,
        from_symbol: str = "",
        to_identity: DomainIdentity | None = None,
        to_symbol: str = "",
        to_unresolved: str = "",
        resolution: DomainResolution | None = None,
        span: SourceSpan | None = None,
        evidence: tuple[int, ...] = (),
    ) -> None:
        self.relations.append(
            ExtractedDomainRelation(
                relation_type=relation_type,
                path=path,
                from_identity=from_identity,
                from_symbol=from_symbol,
                to_identity=to_identity,
                to_symbol=to_symbol,
                to_unresolved=to_unresolved,
                resolution=resolution,
                span=span,
                evidence=evidence,
            )
        )

    def result(self) -> DomainExtractionResult:
        return DomainExtractionResult(
            domain_id=ADAPTER_DOMAIN,
            adapter_version=self._version,
            entities=tuple(self.entities),
            relations=tuple(self.relations),
            evidence=tuple(self.evidence),
            diagnostics=tuple(self.diagnostics),
            stats=dict(self.stats),
        )
