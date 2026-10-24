"""Estimador de tokens (lf-0040).

Heurístico por default — ``estimate`` NUNCA é contagem exata (declarado
em ``tokens_estimated=True`` e no id do estimador). Seam
``TokenCounter`` permite adapter real opt-in sem mudar o engine.
"""

from __future__ import annotations

from typing import Protocol


class TokenCounter(Protocol):
    """Conta/estima tokens de um texto."""

    counter_id: str

    def count(self, text: str) -> int: ...


class HeuristicTokenizer:
    """chars/token fixo (default 4.0) — estimativa declarada, honesta.

    Não é tokenizer de nenhum modelo: superestima texto ASCII denso,
    subestima identificadores longos — erro uniforme e documentado.
    """

    counter_id = "heuristic-chars4"

    def __init__(self, chars_per_token: float = 4.0) -> None:
        if chars_per_token <= 0:
            raise ValueError("chars_per_token deve ser > 0")
        self._cpt = chars_per_token
        self.counter_id = f"heuristic-chars{chars_per_token:g}"

    def count(self, text: str) -> int:
        return max(1, int(len(text) / self._cpt + 0.5))
