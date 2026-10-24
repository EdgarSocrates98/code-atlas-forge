"""Renderers versionados do ContextPack — JSON + Markdown (lf-0040)."""

from __future__ import annotations

import json
from typing import Any

from code_atlas.context.contracts import ContextPack

RENDERER_VERSION_JSON = "pack-json-v1"
RENDERER_VERSION_MD = "pack-markdown-v1"


def render_json(pack: ContextPack) -> str:
    """Objeto único parseável com renderer_version embutida."""
    d: dict[str, Any] = pack.to_dict()
    d["renderer_version"] = RENDERER_VERSION_JSON
    return json.dumps(d, sort_keys=True)


def render_markdown(pack: ContextPack) -> str:
    """Markdown determinístico — mesma ordem de itens do pack."""
    m = pack.manifest
    lines = [
        "<!-- renderer: pack-markdown-v1 -->",
        f"# Context Pack — snapshot {m.snapshot_id} ({m.level})",
        "",
        f"- budget: {pack.budget.available_items}/"
        f"{pack.budget.limit} tokens estimados ({m.estimator})",
        f"- selected: {m.selected} · omitted: {m.omitted} · "
        f"truncated: {str(m.truncated).lower()}",
        f"- freshness: {m.freshness}",
    ]
    if pack.diagnostics.codes:
        lines.append(
            f"- diagnostics: {', '.join(pack.diagnostics.codes)}")
    if pack.diagnostics.suggested_minimum_budget:
        lines.append(
            f"- suggested_minimum_budget: "
            f"{pack.diagnostics.suggested_minimum_budget}")
    lines.append("")
    for item in pack.items:
        lines.append(f"## {item.title}")
        lines.append(f"_{item.kind} · prio={item.priority} · "
                     f"~{item.est_tokens} tok_")
        lines.append("")
        lines.append("```")
        lines.append(item.body)
        lines.append("```")
        lines.append("")
    if pack.expansion_refs:
        lines.append("## Expandable refs")
        for r in pack.expansion_refs:
            lines.append(f"- `{r.ref_id}` ({r.kind}) — {r.reason}")
        lines.append("")
    return "\n".join(lines)
