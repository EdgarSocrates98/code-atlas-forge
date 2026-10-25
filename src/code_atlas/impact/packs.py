"""Impact Context Pack (Phase 8) — seleção budget-aware dos achados.

Prioridades do prompt (estritas):
1. alteração observada → 2. contrato afetado → 3. consumidores diretos
→ 4. caminhos de evidência → 5. testes → 6. transitivos → 7. resto.
Conclusão crítica truncada é sempre declarada (``critical-omitted``).
"""

from __future__ import annotations

from dataclasses import dataclass

from code_atlas.context.tokenizer import HeuristicTokenizer
from code_atlas.impact.contracts import ImpactKind, ImpactResult

_P_CHANGE = 10
_P_CONTRACT = 20
_P_DIRECT = 30
_P_PATH = 40
_P_TEST = 50
_P_TRANSITIVE = 60
_P_MISC = 90


@dataclass(frozen=True)
class ImpactPackItem:
    item_id: str
    kind: str
    priority: int
    title: str
    body: str
    est_tokens: int


@dataclass(frozen=True)
class ImpactContextPack:
    schema_version: int
    budget_tokens: int
    tokens_used: int
    items: tuple[ImpactPackItem, ...]
    omitted: tuple[str, ...]
    diagnostics: tuple[str, ...]
    truncated: bool


def _items(res: ImpactResult, tok: HeuristicTokenizer) -> list[ImpactPackItem]:
    out: list[ImpactPackItem] = []

    def add(kind: str, prio: int, title: str, body: str,
            iid: str) -> None:
        est = int(tok.count(title + " " + body))
        out.append(ImpactPackItem(iid, kind, prio, title, body, est))

    for s in res.symbol_changes:
        add("change", _P_CHANGE,
            f"{s.change.value}: {s.qualified_name}",
            f"{s.old_signature or ''} -> {s.new_signature or ''} "
            f"{s.detail}",
            f"chg:{s.symbol_key}")
    for c in res.contract_changes:
        add("contract", _P_CONTRACT,
            f"{c.classification}: {c.symbol_key}",
            f"{c.compatibility} — {c.detail}",
            f"contract:{c.symbol_key}")
    direct = [i for i in res.impacts
              if i.impact == ImpactKind.DIRECT]
    for i in direct:
        body = (f"{i.kind} em {i.source_path or '?'}")
        add("consumer", _P_DIRECT, f"direct: {i.qualified_name}", body,
            f"imp:{i.symbol_key}")
        if i.path:
            # lf-0120: ``edge_refs`` é a proveniência por hop
            # ("rel:<id>" código / "dom:<id>" domínio) — os ids
            # numéricos de ``relation_ids`` cobrem só arestas de código.
            refs = i.path.edge_refs or tuple(
                f"rel:{r}" for r in i.path.relation_ids)
            add("path", _P_PATH,
                f"path {i.path.origin_symbol_key} -> "
                f"{i.path.affected_symbol_key}",
                f"hops={i.path.hops} edges={refs} "
                f"verified={i.path.fully_verified}",
                f"path:{i.symbol_key}")
    for i in res.impacts:
        if i.impact != ImpactKind.DIRECT:
            add("consumer", _P_TRANSITIVE,
                f"{i.impact.value}: {i.qualified_name}",
                i.source_path or "", f"imp:{i.symbol_key}")
    for t in res.test_candidates:
        add("test", _P_TEST, f"test: {t.test_file}",
            f"{t.relationship} — {t.reason}", f"test:{t.test_file}")
    risk_body = "; ".join(res.risk.reasons) or res.risk.category.value
    add("risk", _P_MISC, f"risk: {res.risk.category.value}",
        risk_body, "risk:summary")
    for u in res.unknown_coverage:
        add("unknown", _P_MISC, f"unknown: {u}", "", f"unk:{u}")
    out.sort(key=lambda i: (i.priority, i.item_id))
    return out


def build_pack(
    res: ImpactResult, budget_tokens: int,
) -> ImpactContextPack:
    """Encaixa itens por prioridade; excluídos vão para ``omitted``."""
    tok = HeuristicTokenizer()
    budget = max(0, min(int(budget_tokens), 1_000_000))
    items = _items(res, tok)
    selected: list[ImpactPackItem] = []
    omitted: list[str] = []
    used = 0
    for it in items:
        if used + it.est_tokens <= budget:
            selected.append(it)
            used += it.est_tokens
        else:
            omitted.append(it.item_id)
    diags: list[str] = []
    critical_omitted = [o for o in omitted
                        if o.startswith(("chg:", "contract:"))]
    if critical_omitted:
        diags.append(
            "critical-omitted: conclusão crítica truncada por budget "
            f"({len(critical_omitted)} item(ns))")
    if omitted:
        diags.append(f"pack-truncated: {len(omitted)} item(ns) omitidos")
    return ImpactContextPack(
        schema_version=1, budget_tokens=budget, tokens_used=used,
        items=tuple(selected), omitted=tuple(omitted),
        diagnostics=tuple(diags), truncated=bool(omitted))
