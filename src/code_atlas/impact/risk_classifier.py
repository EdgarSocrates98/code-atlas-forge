"""Classificação determinística de risco (Phase 6).

Regras explicáveis — cada incremento gera uma ``reason``. Risco e
incerteza são eixos separados: relatório pode ser ``low`` com
``uncertainty=high`` — nunca conclusão tranquilizadora.
``critical`` ≠ falha garantida.
"""

from __future__ import annotations

from code_atlas.impact.contracts import (
    ContractChange,
    ImpactCandidate,
    ImpactKind,
    RiskAssessment,
    RiskCategory,
    SymbolChange,
    SymbolChangeKind,
)


def assess(
    symbol_changes: tuple[SymbolChange, ...],
    contract_changes: tuple[ContractChange, ...],
    impacts: tuple[ImpactCandidate, ...],
    unknown_coverage: tuple[str, ...],
) -> RiskAssessment:
    reasons: list[str] = []
    score = 0

    risks = [c for c in contract_changes
             if c.compatibility == "potential-risk"]
    if risks:
        score += 3
        reasons.append(
            f"{len(risks)} contrato(s) potencialmente incompatíveis: "
            + ", ".join(c.classification for c in risks[:5]))
    removed = sum(1 for s in symbol_changes
                  if s.change == SymbolChangeKind.REMOVED)
    if removed:
        score += 2
        reasons.append(f"{removed} símbolo(s) removido(s)")

    direct = [i for i in impacts if i.impact == ImpactKind.DIRECT]
    transitive = [i for i in impacts
                  if i.impact == ImpactKind.TRANSITIVE]
    possible = [i for i in impacts if i.impact == ImpactKind.POSSIBLE]
    if len(direct) >= 10:
        score += 2
        reasons.append(f"{len(direct)} consumidores diretos")
    elif direct:
        score += 1
        reasons.append(f"{len(direct)} consumidor(es) direto(s)")
    if transitive:
        score += 1
        reasons.append(f"{len(transitive)} impacto(s) transitivo(s)")

    unknown_edges = len(possible)
    uncertainty = "low"
    if unknown_coverage or unknown_edges > len(direct):
        uncertainty = "high"
        reasons.append(
            "alta incerteza: áreas sem cobertura ou cadeias "
            "com arestas não-resolvidas")
    elif possible:
        uncertainty = "moderate"
        reasons.append(f"{len(possible)} impacto(s) apenas possíveis")

    cat = _category(score, bool(symbol_changes))
    if cat == RiskCategory.UNKNOWN:
        reasons.append("sem mudanças observadas — risco indeterminado")
    return RiskAssessment(category=cat, uncertainty=uncertainty,
                          reasons=tuple(reasons))


def _category(score: int, has_changes: bool) -> RiskCategory:
    if score >= 6:
        return RiskCategory.CRITICAL
    if score >= 4:
        return RiskCategory.HIGH
    if score >= 2:
        return RiskCategory.MODERATE
    return RiskCategory.LOW if has_changes else RiskCategory.UNKNOWN
