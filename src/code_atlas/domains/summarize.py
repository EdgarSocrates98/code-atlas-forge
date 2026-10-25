"""Resumo textual determinístico de lineage de domínio (lf-0121, Ph13).

Facts only — nenhuma frase vem de LLM: a saída é montada por template
fixo sobre ``LineageFact`` (relation_type x direção x status), com
ordenação estável e cap de listagem explícito. Exemplo::

    data_table cat1.analytics.orders: read by 2 (daily_load, etl);
    writes to 1 (reporting.orders); 1 ambiguous; 1 dynamic

O módulo é puro (sem sqlite/IO) para ficar testável e reusável — o
builder de context pack traduz rows persistidas em fatos.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

SUMMARIZE_VERSION = 1
_LIST_CAP = 8

# Verbos relativos à SEED, por relation_type:
# ``(forma outgoing — "seed VERBO alvo", forma incoming — "seed VERBO")``.
# Tipos fora do mapa caem no fallback determinístico ``TYPE →`` /
# ``TYPE ←`` — nunca uma paráfrase inventada.
_VERBS: Mapping[str, tuple[str, str]] = {
    "READS_FROM": ("reads", "read by"),
    "WRITES_TO": ("writes to", "written by"),
    "TRANSFORMS": ("transforms", "transformed by"),
    "DERIVES_FROM": ("derives from", "derived by"),
    "DEPENDS_ON": ("depends on", "depended on by"),
    "CALLS_ENDPOINT": ("calls", "called by"),
    "IMPLEMENTS_CONTRACT": ("implements", "implemented by"),
    "PROVISIONS": ("provisions", "provisioned by"),
    "USES_RESOURCE": ("uses", "used by"),
    "ASSUMES_ROLE": ("assumes", "assumed by"),
    "TRIGGERS": ("triggers", "triggered by"),
    "DEPLOYS": ("deploys", "deployed by"),
    "TESTS": ("tests", "tested by"),
    "REFERENCES": ("references", "referenced by"),
    # Vocabulário estrutural compartilhado (EdgeKind) — aparece em
    # domain_relations (CONTAINS módulo→recurso, IMPORTS de módulo tf…).
    "CONTAINS": ("contains", "contained by"),
    "DEFINES": ("defines", "defined by"),
    "IMPORTS": ("imports", "imported by"),
    "CALLS": ("calls", "called by"),
    "IMPLEMENTS": ("implements", "implemented by"),
    "EXTENDS": ("extends", "extended by"),
    "CONFIGURES": ("configures", "configured by"),
}


@dataclass(frozen=True)
class LineageFact:
    """Uma aresta de ``domain_relations`` vista da entidade-semente.

    ``label``: nome estável do vizinho (``qualified_name``) para alvos
    resolvidos/externos, ou o ``to_unresolved`` textual para pendentes
    — o texto é o registro honesto, nunca uma paráfrase.
    ``dynamic``: alvo marcado como dinâmico pelo adapter
    (``dynamic:``/``${dynamic}``/``environment:``) — honestidade, não
    pendência; contado à parte de ``unresolved``.
    """

    relation_type: str
    direction: str          # "incoming" | "outgoing"
    label: str
    resolution_status: str  # resolved|ambiguous|unresolved|external
    neighbor_kind: str = "" # entity kind do vizinho ("" = alvo textual)
    dynamic: bool = False


def _verb(relation_type: str, direction: str) -> str:
    pair = _VERBS.get(relation_type)
    if pair is None:
        arrow = "\u2190" if direction == "incoming" else "\u2192"
        return f"{relation_type} {arrow}"
    return pair[0] if direction == "outgoing" else pair[1]


def summarize_lineage(
    subject: str, facts: Iterable[LineageFact]
) -> str:
    """``subject``: rótulo da seed (ex.: ``"data_table a.b.orders"``).

    Saída: ``"<subject>: <frases>"`` — grupos ``resolved``/``external``
    por (relation_type, direção) com listas capadas (alvos externos
    marcados ``(external)``); contagens honestas de ``ambiguous``,
    ``unresolved`` e ``dynamic`` ao final. Sem fatos, declara a
    ausência — nunca uma frase genérica otimista.
    """
    resolved: dict[tuple[str, str], list[str]] = {}
    n_amb = n_unres = n_dyn = 0
    for f in facts:
        if f.dynamic:
            n_dyn += 1
            continue
        if f.resolution_status == "resolved":
            resolved.setdefault((f.relation_type, f.direction), []).append(
                f.label
            )
        elif f.resolution_status == "ambiguous":
            n_amb += 1
        elif f.resolution_status == "external":
            # alvo conhecido fora do índice — listável mas não vizinho local
            resolved.setdefault(
                (f.relation_type, f.direction), []).append(
                    f"{f.label} (external)")
        else:
            n_unres += 1
    phrases: list[str] = []
    for key in sorted(resolved):
        labels = sorted(resolved[key])
        shown = labels[:_LIST_CAP]
        more = f", +{len(labels) - _LIST_CAP} more" if len(labels) > _LIST_CAP else ""
        phrases.append(
            f"{_verb(*key)} {len(labels)} ({', '.join(shown)}{more})"
        )
    if n_amb:
        phrases.append(f"{n_amb} ambiguous")
    if n_unres:
        phrases.append(f"{n_unres} unresolved")
    if n_dyn:
        phrases.append(f"{n_dyn} dynamic")
    body = "; ".join(phrases) if phrases else "no persisted domain relations"
    return f"{subject}: {body}"
