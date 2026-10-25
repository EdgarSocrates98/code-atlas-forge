"""Validação de saída de adapters de domínio (lf-0110).

Espelha ``graph.validation``: retorna lista de violações (vazia = ok),
nunca levanta. Checagens:

- ``result.domain_id``/``adapter_version`` casam com o adapter;
- kinds de entidade/relação ⊆ vocabulário declarado pelo adapter;
- identidades das entidades têm ``domain`` == adapter e repositório ==
  request;
- confiança declarada respeita o ``ConfidenceModel`` (teto + inferência);
- índices de evidência em faixa; invariantes de ``DomainResolution``
  (inferred nunca resolved — reforço: o dataclass já garante, aqui
  reporta-se como violação, não exceção).
"""

from __future__ import annotations

from code_atlas.contracts.enums import ConfidenceClass, ResolutionStatus
from code_atlas.domains.contracts import (
    DOMAIN_CONTRACT_VERSION,
    DomainAdapter,
    DomainExtractionRequest,
    DomainExtractionResult,
)
from code_atlas.domains.model import DOMAIN_RELATION_TYPES


def _check_envelope(
    adapter: DomainAdapter, result: DomainExtractionResult
) -> list[str]:
    errors: list[str] = []
    if result.schema_version != DOMAIN_CONTRACT_VERSION:
        errors.append(f"schema_version mismatch: {result.schema_version}")
    if result.domain_id != adapter.domain_id:
        errors.append(
            f"result.domain_id {result.domain_id!r} != adapter "
            f"{adapter.domain_id!r}"
        )
    if result.adapter_version != adapter.adapter_version:
        errors.append(
            f"result.adapter_version {result.adapter_version!r} != adapter "
            f"{adapter.adapter_version!r}"
        )
    return errors


def _check_entities(
    adapter: DomainAdapter,
    request: DomainExtractionRequest,
    result: DomainExtractionResult,
) -> list[str]:
    errors: list[str] = []
    declared = adapter.entity_kinds()
    evi_len = len(result.evidence)
    identities: set[str] = set()
    for e in result.entities:
        key = e.identity.canonical_key()
        if key in identities:
            errors.append(f"duplicate entity identity: {e.identity.display()}")
        identities.add(key)
        if e.kind not in declared:
            errors.append(f"undeclared entity kind: {e.kind}")
        if e.identity.domain != adapter.domain_id:
            errors.append(
                f"entity domain {e.identity.domain!r} != adapter "
                f"{adapter.domain_id!r}"
            )
        if e.identity.repository != request.repository:
            errors.append(
                f"entity repository {e.identity.repository!r} != request "
                f"{request.repository!r}"
            )
        for i in e.evidence:
            if not 0 <= i < evi_len:
                errors.append(f"entity evidence index out of range: {i}")
    return errors


def _check_relations(
    adapter: DomainAdapter, result: DomainExtractionResult
) -> list[str]:
    errors: list[str] = []
    declared = adapter.relation_kinds()
    confidence = adapter.confidence_model()
    evi_len = len(result.evidence)
    for r in result.relations:
        if r.relation_type not in DOMAIN_RELATION_TYPES:
            errors.append(f"invalid relation_type: {r.relation_type!r}")
        elif r.relation_type not in declared:
            errors.append(f"undeclared relation kind: {r.relation_type!r}")
        if r.to_identity is None and not r.to_symbol and not r.to_unresolved:
            errors.append("relation without target")
        res = r.resolution
        if res is not None:
            if (
                res.confidence_class is ConfidenceClass.INFERRED
                and res.status is ResolutionStatus.RESOLVED
            ):
                errors.append("inferred confidence reported resolved")
            if not confidence.admits(res.confidence_class):
                errors.append(
                    f"confidence {res.confidence_class} exceeds adapter "
                    f"ceiling {confidence.ceiling}"
                )
        for i in r.evidence:
            if not 0 <= i < evi_len:
                errors.append(f"relation evidence index out of range: {i}")
    return errors


def validate_extraction(
    adapter: DomainAdapter,
    request: DomainExtractionRequest,
    result: DomainExtractionResult,
) -> list[str]:
    """Violações do resultado contra o contrato declarado do adapter."""
    errors = _check_envelope(adapter, result)
    errors.extend(_check_entities(adapter, request, result))
    errors.extend(_check_relations(adapter, result))
    return errors
