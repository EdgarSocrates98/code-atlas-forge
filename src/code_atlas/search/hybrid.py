"""Fusão híbrida de candidatos — Reciprocal Rank Fusion (lf-0038 /
Cycle 4 Ph5).

Pipeline: lexical + structural + graph (vetorial quando existir) →
dedup por identidade canônica do símbolo (``qualified_name``)
preservando a participação e as ocorrências de cada estratégia →
score RRF → página.

- ``RRF_K = 60``: constante padrão da literatura (Cormack et al. 2009)
  — documentada; variantes só com evidência de ganho.
- Scores crus de backends distintos NUNCA são comparados — RRF opera
  sobre POSIÇÕES (rank), não sobre score.
- Sem provider vetorial, ``hybrid`` funciona sobre as 3 estratégias
  reais — degradação honesta, declarada no hit.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from code_atlas.search.contracts import SearchHit
from code_atlas.search.intent import QueryIntent

RRF_K = 60
RANKING_VERSION_HYBRID = "rrf-k60-v2"

# Fatores do rerank task-aware (lf-0050) — constantes documentadas,
# NUNCA arbitrárias: qualquer alteração exige benchmark em
# eval-report (comparação v1-vs-v2 no mesmo dataset).
BOOST_EXACT_QNAME = 1.5   # último segmento do qname == query (locate)
BOOST_DIRECTION = 1.25    # evidência de passo na direção do intent
DEMOTE_NO_EVIDENCE = 0.9  # hit sem nenhuma evidência explicável


def canonical_key(h: SearchHit) -> str:
    """Identidade canônica do símbolo (lf-0050).

    Não basta ``qualified_name``: identidades diferentes com o mesmo
    nome (linguagem/kind distintos) NÃO podem fundir. ``qname`` já
    carrega escopo (path/módulo); repo+snapshot são fixos por
    consulta (isolamento garantido a montante)."""
    return f"{h.language}:{h.kind}:{h.qualified_name}"


@dataclass(frozen=True)
class FusedCandidate:
    """Símbolo deduplicado com participação por estratégia."""

    symbol_key: str   # canonical_key composta (lang:kind:qname)
    qualified_name: str
    hits: tuple[SearchHit, ...]        # ocorrências por estratégia
    ranks: dict[str, int]              # strategy → melhor posição (1-based)
    rrf_score: float
    rerank_score: float = 0.0          # score final pós-rerank (0 = =rrf)
    rerank_reasons: tuple[str, ...] = ()


def fuse(
    rankings: dict[str, list[SearchHit]],
    *,
    k: int = RRF_K,
    weights: dict[str, float] | None = None,
    intent: QueryIntent | None = None,
    query: str = "",
) -> list[FusedCandidate]:
    """RRF sobre rankings de várias estratégias.

    ``rankings``: strategy → hits ordenados (posição = rank).
    Dedup por ``canonical_key`` preserva evidências: hits de todas as
    estratégias ficam em ``hits`` (nunca descartados); o primário é o
    de melhor rank (menor occurrence_id em empate — determinístico).
    ``intent``/``query`` ativam o rerank task-aware — sem eles o
    ranking é RRF puro (v1-compatível)."""
    w = weights or {}
    by_sym: dict[str, dict[str, int]] = {}
    hits_by_sym: dict[str, list[SearchHit]] = {}
    for strategy, hits in rankings.items():
        for pos, h in enumerate(hits, start=1):
            key = canonical_key(h)
            ranks = by_sym.setdefault(key, {})
            if pos < ranks.get(strategy, 1 << 30):
                ranks[strategy] = pos
            hits_by_sym.setdefault(key, []).append(h)
    out: list[FusedCandidate] = []
    for sym, ranks in by_sym.items():
        score = sum(
            w.get(s, 1.0) / (k + rank) for s, rank in ranks.items()
        )
        primaries = sorted(
            hits_by_sym[sym],
            key=lambda h: (min(ranks.values()), h.occurrence_id),
        )
        out.append(FusedCandidate(
            symbol_key=sym,
            qualified_name=primaries[0].qualified_name,
            hits=tuple(primaries),
            ranks=ranks,
            rrf_score=score,
        ))
    ranked = [_rerank(c, intent, query) for c in out]
    ranked.sort(key=lambda c: (
        -(c.rerank_score or c.rrf_score), c.symbol_key))
    return ranked


def _rerank(
    c: FusedCandidate, intent: QueryIntent | None, query: str,
) -> FusedCandidate:
    """Rerank task-aware — multiplicadores documentados, explicados
    em ``rerank_reasons``. Sem intent → RRF puro inalterado."""
    score = c.rrf_score
    reasons: list[str] = []
    primary = c.hits[0]
    if query and primary.qualified_name.split(".")[-1] == query:
        score *= BOOST_EXACT_QNAME
        reasons.append(f"exact-qname x{BOOST_EXACT_QNAME}")
    if intent is not None and intent.direction != "both":
        for h in c.hits:
            if any(f"dir={intent.direction}" in e for e in h.evidence):
                score *= BOOST_DIRECTION
                reasons.append(f"direction={intent.direction} "
                               f"x{BOOST_DIRECTION}")
                break
    if not any(h.evidence for h in c.hits):
        score *= DEMOTE_NO_EVIDENCE
        reasons.append(f"no-evidence x{DEMOTE_NO_EVIDENCE}")
    return replace(c, rerank_score=round(score, 9),
                   rerank_reasons=tuple(reasons))


def to_search_hit(c: FusedCandidate) -> SearchHit:
    """Projeta o candidato fundido para ``SearchHit`` explicável."""
    primary = c.hits[0]
    strategies = tuple(sorted(c.ranks))
    evidence = (
        *(f"{s}:rank={c.ranks[s]}" for s in strategies),
        f"rrf={c.rrf_score:.6f}",
        *(f"rerank={r}" for r in c.rerank_reasons),
    )
    return SearchHit(
        occurrence_id=primary.occurrence_id,
        qualified_name=c.qualified_name,
        kind=primary.kind,
        language=primary.language,
        path=primary.path,
        span=primary.span,
        score=round(c.rrf_score, 6),
        strategy="hybrid",
        matched_fields=strategies,
        evidence=evidence,
    )
