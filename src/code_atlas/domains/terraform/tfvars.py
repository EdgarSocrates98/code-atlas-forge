"""Leitura estrutural de ``.tfvars`` / ``.tfvars.json`` (lf-0115).

tfvars é HCL restrito a atribuições top-level — reusa ``parse_hcl``
sem nenhum caminho de execução. ``.tfvars.json`` é JSON puro.

Segurança: valores cuja chave casa o padrão de segredos nunca são
devolvidos — o caller recebe o marcador ``[REDACTED]``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from code_atlas.domains.terraform.hcl import HclDiagnostic, parse_hcl
from code_atlas.domains.terraform.refs import (
    REDACTED,
    is_sensitive_name,
    looks_like_secret_value,
    sanitize_value,
)


@dataclass(frozen=True)
class TfvarsAssignment:
    """Atribuição ``name = value`` de um arquivo tfvars."""

    name: str
    value: str          # texto bruto (sanitizado/redigido pelo caller via flags)
    redacted: bool      # True quando a chave é sensível — value == "[REDACTED]"
    line: int = 0


@dataclass(frozen=True)
class TfvarsFile:
    assignments: tuple[TfvarsAssignment, ...] = ()
    diagnostics: tuple[HclDiagnostic, ...] = ()


def _unquote(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v.startswith('"') and v.endswith('"'):
        return v[1:-1]
    return v


def _assignment(name: str, raw: str, line: int) -> TfvarsAssignment:
    if is_sensitive_name(name):
        return TfvarsAssignment(name, REDACTED, True, line)
    # valor com formato de credencial sob chave benigna — mesma redação
    # que chave sensível; nunca entra em env-hints nem atributos (F5).
    if looks_like_secret_value(_unquote(raw)):
        return TfvarsAssignment(name, REDACTED, True, line)
    return TfvarsAssignment(name, sanitize_value(_unquote(raw)), False, line)


def parse_tfvars(text: str) -> TfvarsFile:
    """Parse de ``*.tfvars`` (HCL assignments) — nunca levanta."""
    hcl = parse_hcl(text)
    out = [
        _assignment(a.name, a.value, a.span.start_line)
        for a in hcl.attributes
    ]
    diags = list(hcl.diagnostics)
    for b in hcl.blocks:  # tfvars não tem blocos — honesto, não crash
        diags.append(
            HclDiagnostic(
                code="unexpected-block",
                message=f"block {b.type!r} in tfvars file",
                line=(b.header_span.start_line if b.header_span else 0),
            )
        )
    return TfvarsFile(assignments=tuple(out), diagnostics=tuple(diags))


def parse_tfvars_json(text: str) -> TfvarsFile:
    """Parse de ``*.tfvars.json`` — objeto JSON chave→valor."""
    try:
        data = json.loads(text)
    except (ValueError, json.JSONDecodeError) as exc:
        return TfvarsFile(
            diagnostics=(
                HclDiagnostic("json-parse-error", str(exc)),
            )
        )
    if not isinstance(data, dict):
        return TfvarsFile(
            diagnostics=(
                HclDiagnostic(
                    "json-parse-error", "tfvars.json root must be an object"
                ),
            )
        )
    out = [
        _assignment(str(k), json.dumps(v, ensure_ascii=False), 0)
        for k, v in data.items()
    ]
    return TfvarsFile(assignments=tuple(out))
