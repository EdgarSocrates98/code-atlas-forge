"""Estado de resolução de alvos de relações de domínio (lf-0110).

Reutiliza o vocabulário ``ResolutionStatus`` (resolved / ambiguous /
unresolved / external) e ``ConfidenceClass`` — nenhum valor novo.

Invariantes (spec Cycle 7 — "estados honestos de resolução"):

- ``resolved`` exige ``target`` (identidade canônica do alvo);
- ``ambiguous`` exige ≥2 candidatos — nunca "resolved por sorte";
- ``external`` pode carregar ``target`` (identidade conhecida, fora do
  snapshot — ex.: ARN de recurso real, tabela de outro repo federado);
- ``inferred`` **nunca** é promovida a ``resolved`` — evidência inferida
  no máximo sustenta ``ambiguous``/``external``/``unresolved``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from code_atlas.contracts.enums import ConfidenceClass, ResolutionStatus
from code_atlas.contracts.models import _coerce
from code_atlas.domains.identity import DomainIdentity


@dataclass(frozen=True)
class DomainResolution:
    """Desfecho tipado da resolução de um alvo de relação de domínio."""

    status: ResolutionStatus
    confidence_class: ConfidenceClass
    resolver: str                       # id do adapter/resolver produtor
    target: DomainIdentity | None = None
    candidates: tuple[DomainIdentity, ...] = field(default_factory=tuple)
    unresolved_text: str = ""           # alvo textual preservado

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "status", _coerce(ResolutionStatus, self.status, "status")
        )
        object.__setattr__(
            self,
            "confidence_class",
            _coerce(ConfidenceClass, self.confidence_class, "confidence_class"),
        )
        if not self.resolver:
            raise ValueError("domain resolution requires resolver provenance")
        if self.status is ResolutionStatus.RESOLVED and self.target is None:
            raise ValueError("resolved status requires target identity")
        if (
            self.status is ResolutionStatus.AMBIGUOUS
            and len(self.candidates) < 2
        ):
            raise ValueError("ambiguous status requires >=2 candidates")
        if (
            self.status in (ResolutionStatus.UNRESOLVED,)
            and self.target is not None
        ):
            raise ValueError("unresolved status cannot carry a target")
        if (
            self.confidence_class is ConfidenceClass.INFERRED
            and self.status is ResolutionStatus.RESOLVED
        ):
            raise ValueError(
                "inferred evidence can never be reported resolved"
            )
