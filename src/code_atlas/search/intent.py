"""Classificação determinística de intenção de consulta (lf-0049).

Sem LLM — heurística por palavras-chave configurável. Produz o tipo
de tarefa e a direção de travessia preferida no grafo:
- "find-consumers" → incoming (quem chama/importa o símbolo)
- "explain-dependencies" → outgoing (o que o símbolo usa)
- demais → both (exploração simétrica)

A classificação é deliberadamente conservadora: na dúvida, ``both``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# padrões → (task_type, direction). Ordem importa: primeiro match vence.
_RULES: tuple[tuple[str, str, str], ...] = (
    (r"\b(who calls|callers?|consumers?|referenc|usado por|"
     r"quem chama|incoming)\b", "find-consumers", "incoming"),
    (r"\b(depend\w*|import\w*|callee\w*|outgoing|delega\w*|"
     r"o que .{0,20}usa)\b", "explain-dependencies", "outgoing"),
    (r"\b(flow|fluxo|architect|arquitet|component|pipeline|"
     r"camadas?)\b", "understand-component", "both"),
    (r"\b(test|tests?|spec|coverage)\b", "find-tests", "incoming"),
    (r"\b(bug|falha|fail|erro|crash|debug|defeito)\b",
     "investigate-failure", "incoming"),
)


@dataclass(frozen=True)
class QueryIntent:
    """Intenção classificada — determinística, explicável."""

    task_type: str           # find-consumers|explain-dependencies|...
    direction: str           # incoming|outgoing|both
    matched_rule: str        # padrão que casou (ou "" = default)


def classify(query: str) -> QueryIntent:
    """Classifica a consulta. Sem match → locate/both (default seguro)."""
    q = query.lower()
    for pattern, task, direction in _RULES:
        if re.search(pattern, q):
            return QueryIntent(task_type=task, direction=direction,
                               matched_rule=pattern)
    return QueryIntent(task_type="locate", direction="both",
                       matched_rule="")
