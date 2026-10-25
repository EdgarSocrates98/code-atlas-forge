"""Federação de domínios (Cycle 7): adapters, identidade canônica,
modelo unificado de entidades/relações e registro explícito.

Camada separada do grafo de símbolos: ``domains`` observa artefatos
não-código (SQL, Terraform, OpenAPI, CI/CD) com o mesmo rigor de
evidência e snapshots do restante do Atlas (ADR-0010).
"""

from code_atlas.domains.contracts import (
    DOMAIN_CONTRACT_VERSION,
    ArtifactSupport,
    ConfidenceModel,
    DiscoveredArtifact,
    DiscoveryRule,
    DomainAdapter,
    DomainDiagnostic,
    DomainExtractionRequest,
    DomainExtractionResult,
    ExtractedDomainEntity,
    ExtractedDomainRelation,
    confidence_rank,
)
from code_atlas.domains.evidence import (
    DOMAIN_EXTRACTOR_PREFIX,
    DomainEvidence,
    domain_extractor_id,
    evidence_record,
)
from code_atlas.domains.identity import DomainIdentity
from code_atlas.domains.model import (
    DOMAIN_RELATION_TYPES,
    DomainEntity,
    DomainEntityKind,
    DomainEntityOccurrence,
    DomainRelation,
    DomainRelationKind,
)
from code_atlas.domains.registry import (
    ENTRYPOINT_GROUP,
    AdapterDisabledError,
    AdapterRegistryError,
    DomainRegistry,
    DuplicateAdapterError,
    UnknownDomainError,
    default_registry,
)
from code_atlas.domains.resolution import DomainResolution
from code_atlas.domains.validation import validate_extraction

__all__ = [
    "DOMAIN_CONTRACT_VERSION",
    "DOMAIN_EXTRACTOR_PREFIX",
    "DOMAIN_RELATION_TYPES",
    "ENTRYPOINT_GROUP",
    "AdapterDisabledError",
    "AdapterRegistryError",
    "ArtifactSupport",
    "ConfidenceModel",
    "DiscoveredArtifact",
    "DiscoveryRule",
    "DomainAdapter",
    "DomainDiagnostic",
    "DomainEntity",
    "DomainEntityKind",
    "DomainEntityOccurrence",
    "DomainEvidence",
    "DomainExtractionRequest",
    "DomainExtractionResult",
    "DomainIdentity",
    "DomainRegistry",
    "DomainRelation",
    "DomainRelationKind",
    "DomainResolution",
    "DuplicateAdapterError",
    "ExtractedDomainEntity",
    "ExtractedDomainRelation",
    "UnknownDomainError",
    "confidence_rank",
    "default_registry",
    "domain_extractor_id",
    "evidence_record",
    "validate_extraction",
]
