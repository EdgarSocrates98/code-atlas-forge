"""Análise de contratos públicos por assinatura (Phase 4).

NÃO é type-checker: compara a *forma* da assinatura persistida
(``fts_occurrences.signature``). Classificações determinísticas;
"potentially incompatible" ≠ "quebra comprovada" — a quebra exige
consumidor incompatível demonstrado separadamente (fora do escopo).
"""

from __future__ import annotations

import re

from code_atlas.impact.contracts import (
    ContractChange,
    EvidenceStrength,
    SymbolChange,
    SymbolChangeKind,
)

_PARAM_RE = re.compile(r"\((.*)\)")
_OPT_RE = re.compile(r"[=?]|\w+\s*[:=]")


def _params(sig: str) -> list[str]:
    m = _PARAM_RE.search(sig)
    if not m:
        return []
    return [p.strip() for p in m.group(1).split(",") if p.strip()
            and p.strip() not in ("self", "cls")]


def _required(params: list[str]) -> list[str]:
    """Obrigatórios: sem ``=``, sem ``?`` (TS), não *args/**kwargs."""
    out = []
    for p in params:
        if p.startswith(("*", "/")) or p.startswith("..."):
            continue
        name = p.split(":")[0].split("=")[0].strip().rstrip("?")
        if "=" not in p and not p.rstrip().endswith("?"):
            out.append(name)
    return out


def classify_change(
    sc: SymbolChange,
) -> ContractChange | None:
    """Classifica uma SymbolChange c/ assinaturas; None se sem contrato."""
    old, new = sc.old_signature or "", sc.new_signature or ""
    if sc.change == SymbolChangeKind.REMOVED:
        return ContractChange(
            symbol_key=sc.symbol_key,
            classification="public-symbol-removed",
            compatibility="potential-risk",
            old_signature=old or None, new_signature=None,
            strength=sc.strength,
            detail="símbolo removido do grafo alvo")
    if sc.change == SymbolChangeKind.ADDED:
        return None  # adição não quebra contrato existente
    if sc.change != SymbolChangeKind.SIGNATURE_CHANGED:
        return None

    po, pn = _params(old), _params(new)
    ro, rn = set(_required(po)), set(_required(pn))
    cls, compat, detail = "signature-changed", "indeterminate", ""

    added_req = sorted(rn - ro)
    removed_req = sorted(ro - rn)
    if added_req:
        cls, compat = "required-parameter-added", "potential-risk"
        detail = f"novos obrigatórios: {', '.join(added_req)}"
    elif removed_req:
        cls, compat = "required-parameter-removed", "likely-compatible"
        detail = ("remoção de parâmetro obrigatório é compatível "
                  "para chamadores que o passavam por nome")
    elif len(pn) > len(po):
        cls, compat = "optional-parameter-added", "likely-compatible"
        detail = "apenas parâmetros opcionais adicionados"
    elif len(pn) < len(po):
        cls, compat = "parameter-removed", "potential-risk"
        detail = "parâmetro removido pode quebrar chamadas posicionais"
    elif po != pn:
        cls, compat = "parameter-renamed", "potential-risk"
        detail = "ordem/nome de parâmetros diverge"
    else:
        cls, compat = "no-contract-impact", "no-contract-impact"
        detail = "mesma forma de parâmetros"
    if old.lstrip().startswith("async") != new.lstrip().startswith("async"):
        cls, compat = "async-changed", "potential-risk"
        detail = "mudança sync↔async"
    return ContractChange(
        symbol_key=sc.symbol_key, classification=cls,
        compatibility=compat, old_signature=old or None,
        new_signature=new or None,
        strength=(EvidenceStrength.VERIFIED
                  if compat != "indeterminate"
                  else EvidenceStrength.POSSIBLE),
        detail=detail)


def analyze(
    changes: tuple[SymbolChange, ...],
) -> tuple[ContractChange, ...]:
    out = [c for c in (classify_change(s) for s in changes) if c]
    return tuple(out)
