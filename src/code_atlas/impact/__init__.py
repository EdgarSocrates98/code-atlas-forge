"""Impact Engine — análise determinística de impacto de mudanças (Cycle 5).

Fatos vêm de snapshots persistidos + Git subprocess read-only.
Conectividade de grafo indica dependência, nunca certeza de quebra.
"""

from code_atlas.impact.contracts import (
    ANALYZER_VERSION,
    IMPACT_SCHEMA_VERSION,
    ChangeKind,
    ChangeSet,
    ContractChange,
    EvidenceStrength,
    FileChange,
    FindingKind,
    ImpactCandidate,
    ImpactDiagnostic,
    ImpactKind,
    ImpactLimits,
    ImpactPath,
    ImpactReport,
    ImpactRequest,
    ImpactResult,
    RiskAssessment,
    RiskCategory,
    SymbolChange,
    TestCandidate,
)

__all__ = [
    "ANALYZER_VERSION", "IMPACT_SCHEMA_VERSION",
    "ChangeKind", "ChangeSet", "ContractChange", "EvidenceStrength",
    "FileChange", "FindingKind", "ImpactCandidate", "ImpactDiagnostic",
    "ImpactKind", "ImpactLimits", "ImpactPath", "ImpactReport",
    "ImpactRequest", "ImpactResult", "RiskAssessment", "RiskCategory",
    "SymbolChange", "TestCandidate",
]
