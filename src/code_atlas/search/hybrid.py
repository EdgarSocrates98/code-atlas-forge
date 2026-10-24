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

from dataclasses import dataclass

from code_atlas.search.contracts import SearchHit

RRF_K = 60
RANKING_VERSION_HYBRID = "rrf-k60-v1"


@dataclass(frozen=True)
class FusedCandidate:
    """Símbolo deduplicado com participação por estratégia."""

    symbol_key: str                    # identidade canônica (qualified_name)
    hits: tuple[SearchHit, ...]        # ocorrências por estratégia
    ranks: dict[str, int]              # strategy → melhor posição (1-based)
    rrf_score: float


def fuse(
    rankings: dict[str, list[SearchHit]],
    *,
    k: int = RRF_K,
    weights: dict[str, float] | None = None,
) -> list[FusedCandidate]:
    """RRF sobre rankings de várias estratégias.

    ``rankings``: strategy → hits ordenados (posição = rank).
    Dedup por símbolo preserva evidências: hits de todas as estratégias
    ficam em ``hits`` (nunca descartados); o primário é o de melhor
    rank (menor occurrence_id em empate — determinístico).
    """
    w = weights or {}
    by_sym: dict[str, dict[str, int]] = {}
    hits_by_sym: dict[str, list[SearchHit]] = {}
    for strategy, hits in rankings.items():
        for pos, h in enumerate(hits, start=1):
            ranks = by_sym.setdefault(h.qualified_name, {})
            if pos < ranks.get(strategy, 1 << 30):
                ranks[strategy] = pos
            hits_by_sym.setdefault(h.qualified_name, []).append(h)
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
            hits=tuple(primaries),
            ranks=ranks,
            rrf_score=score,
        ))
    out.sort(key=lambda c: (-c.rrf_score, c.symbol_key))
    return out


def to_search_hit(c: FusedCandidate) -> SearchHit:
    """Projeta o candidato fundido para ``SearchHit`` explicável."""
    primary = c.hits[0]
    strategies = tuple(sorted(c.ranks))
    evidence = (
        *(f"{s}:rank={c.ranks[s]}" for s in strategies),
        f"rrf={c.rrf_score:.6f}",
    )
    return SearchHit(
        occurrence_id=primary.occurrence_id,
        qualified_name=c.symbol_key,
        kind=primary.kind,
        language=primary.language,
        path=primary.path,
        span=primary.span,
        score=round(c.rrf_score, 6),
        strategy="hybrid",
        matched_fields=strategies,
        evidence=evidence,
    )
