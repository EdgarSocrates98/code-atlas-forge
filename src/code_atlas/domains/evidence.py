"""Evidência de fatos de domínio (lf-0110, Cycle 7).

Nenhum tipo novo: evidência de domínio **é** ``EvidenceRecord`` — a mesma
tabela ``evidence``, o mesmo envelope (extractor + versão + content_hash +
span). O que muda é a convenção de identificação do produtor:

- ``extractor`` = ``domain:<domain_id>`` (ex.: ``domain:sql``);
- ``extractor_version`` = ``adapter_version`` do adapter;
- ``resolver_id``/``resolver_version``/``evidence_tier`` preenchidos só
  quando a evidência veio de um resolvedor (mesma semântica de lf-0035);
- ``content_hash`` = sha256 do artefato observado — casa 1:1 com o
  arquivo do inventário (evidence-first: nenhum fato sem trecho real).
"""

from __future__ import annotations

from code_atlas.parsers.contracts import EvidenceRecord, SourceSpan

# Alias documentado — evidência de domínio reutiliza o envelope canônico.
DomainEvidence = EvidenceRecord

DOMAIN_EXTRACTOR_PREFIX = "domain:"


def domain_extractor_id(domain_id: str) -> str:
    """``extractor`` canônico de um adapter de domínio."""
    return f"{DOMAIN_EXTRACTOR_PREFIX}{domain_id}"


def evidence_record(
    *,
    adapter_domain: str,
    adapter_version: str,
    path: str,
    content_hash: str,
    span: SourceSpan | None = None,
    notes: str = "",
    resolver_id: str = "",
    resolver_version: str = "",
    evidence_tier: int = 0,
    index_hash: str = "",
    validation_status: str = "",
) -> EvidenceRecord:
    """Fábrica tipada de ``EvidenceRecord`` com convenção de domínio."""
    return EvidenceRecord(
        path=path,
        span=span,
        content_hash=content_hash,
        extractor=domain_extractor_id(adapter_domain),
        extractor_version=adapter_version,
        notes=notes,
        resolver_id=resolver_id,
        resolver_version=resolver_version,
        evidence_tier=evidence_tier,
        index_hash=index_hash,
        validation_status=validation_status,
    )
