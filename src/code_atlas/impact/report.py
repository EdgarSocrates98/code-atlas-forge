"""Impact Report — renderers JSON + Markdown + CLI (Phase 9).

Ordenação determinística, truncamento explícito, nada fictício.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import asdict

from code_atlas.impact.contracts import ImpactResult


def _jsonable(obj: object) -> object:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, (tuple, list)):
        return [_jsonable(v) for v in obj]
    return obj


def to_json(res: ImpactResult) -> str:
    """Envelope JSON versionado (schema_version incluso no result)."""
    return json.dumps(_jsonable(res), indent=2,
                      sort_keys=True, default=str)


def _fmt_sig(c: object) -> str:
    return getattr(c, "new_signature", None) or getattr(
        c, "old_signature", None) or ""


def to_markdown(res: ImpactResult) -> str:
    """Formato do Phase 9: seções fixas, ordenadas, auditáveis."""
    lines = [
        "# Impact Report", "",
        f"- **Repository**: `{res.repository}`",
        f"- **Base Revision**: `{res.base_revision}`",
        f"- **Target Revision**: `{res.target_revision}`",
        f"- **Snapshots**: {res.base_snapshot_id} → "
        f"{res.target_snapshot_id}",
        f"- **Risk**: **{res.risk.category.value}** "
        f"(uncertainty: {res.risk.uncertainty})",
        "",
        "## Change Summary", ""]
    if res.change_set:
        lines.append(f"- {len(res.change_set.files)} arquivo(s) "
                     f"alterado(s) via {res.change_set.source}")
        for f in res.change_set.files[:20]:
            lines.append(f"  - `{f.kind.value}` "
                         f"{f.new_path or f.old_path}")
    lines += _symbols_section(res)
    lines += _impacts_sections(res)
    lines += _tests_section(res)
    lines += _risk_section(res)
    lines += _limitations_section(res)
    lines += ["", f"_analyzer: {res.analyzer_version} "
                  f"· schema v{res.schema_version}_", ""]
    return "\n".join(lines)


def _symbols_section(res: ImpactResult) -> list[str]:
    lines = ["", "## Modified Symbols", ""]
    for s in res.symbol_changes:
        lines.append(f"- `{s.change.value}` **{s.qualified_name}** "
                     f"({s.kind}, {s.strength.value})")
    if res.contract_changes:
        lines += ["", "## Contract Changes", ""]
        for c in res.contract_changes:
            lines.append(
                f"- **{c.classification}** `{c.symbol_key}` — "
                f"{c.compatibility}")
    return lines


def _impacts_sections(res: ImpactResult) -> list[str]:
    lines: list[str] = []
    for title, kinds in (
            ("Direct Impact", ("direct",)),
            ("Transitive Impact", ("transitive",)),
            ("Possible Impact", ("possible", "unknown"))):
        group = [i for i in res.impacts
                 if i.impact.value in kinds]
        lines += ["", f"## {title}", ""]
        for i in group[:30]:
            hops = i.path.hops if i.path else 0
            # lf-0120: nós de domínio marcados — qualified_name já
            # traz ``dominio:ns.qname``; uncertain_deps é incerteza,
            # nunca silenciada.
            tag = "domain " if i.node_type == "domain" else ""
            deps = (f", {len(i.uncertain_deps)} dep(s) incerta(s)"
                    if i.uncertain_deps else "")
            lines.append(
                f"- **{i.qualified_name}** ({tag}{i.kind}) — "
                f"{hops} hop(s), {i.strength.value}{deps}")
    return lines


def _tests_section(res: ImpactResult) -> list[str]:
    lines = ["", "## Test Recommendations", ""]
    for t in res.test_candidates[:20]:
        lines.append(f"- `{t.test_file}` — {t.relationship}: "
                     f"{t.reason}")
    return lines


def _risk_section(res: ImpactResult) -> list[str]:
    return ["", "## Risk Assessment", "",
            *[f"- {r}" for r in res.risk.reasons]]


def _limitations_section(res: ImpactResult) -> list[str]:
    lines = ["", "## Limitations", ""]
    for u in res.unknown_coverage:
        lines.append(f"- {u}")
    for d in res.diagnostics:
        lines.append(f"- diagnostic `{d.code}`: {d.message}")
    if res.truncated:
        # lf-0151: cada bound que cortou trabalho é nomeado — um
        # "TRUNCATED" sem razão seria de novo silêncio invertido.
        why = ", ".join(res.truncation_reasons) or "limites"
        lines.append(
            f"- **TRUNCATED**: limites de análise atingidos ({why})")
    return lines


def to_pr_comment(res: ImpactResult) -> str:
    """Phase 12 (opt-in): corpo de comentário Markdown para PR.

    Gerado localmente — NUNCA publica. A integração de providers
    (GitHub/GitLab) é seam futura; sem token/rede, reporta-se NOT RUN.
    """
    header = ("> Code Atlas Impact Report (impact-v1) — gerado "
              "localmente; publicação opt-in\n\n")
    return header + to_markdown(res)
