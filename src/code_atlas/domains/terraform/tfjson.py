"""Leitura estrutural de ``.tf.json`` (lf-0115).

Terraform aceita configuração em JSON: ``{"resource": {"aws_x":
{"name": {…attrs…}}}}``. Este leitor converte o objeto em
``HclBlock`` equivalentes — ``body`` é a serialização JSON do objeto
do bloco e os ``value_span`` dos atributos são offsets **dentro do
body** (body_start=0), mantendo a classificação de referências
(``depends_on`` explícito, atributo de documento) consistente no
mesmo espaço de coordenadas.

Spans de entidade/evidência: o JSON stdlib não expõe offsets —
aproximamos pelo token ``"<name>"`` no texto real do arquivo
(``_key_span``); evidência aponta para conteúdo do inventário, com
precisão declarada como aproximada.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from code_atlas.domains.terraform.hcl import HclAttribute, HclBlock, HclDiagnostic
from code_atlas.parsers.contracts import SourceSpan

_ZERO = SourceSpan(0, 0, 0, 0, 0, 0)


@dataclass(frozen=True)
class TfJsonFile:
    blocks: tuple[HclBlock, ...] = ()
    diagnostics: tuple[HclDiagnostic, ...] = ()


def _key_span(text: str, key: str) -> SourceSpan | None:
    """Span aproximado do token ``"<key>"`` no texto do arquivo."""
    needle = json.dumps(key, ensure_ascii=False)
    idx = text.find(needle)
    if idx < 0:
        return None
    start_b = len(text[:idx].encode("utf-8"))
    end_b = start_b + len(needle.encode("utf-8"))
    line = text.count("\n", 0, idx) + 1
    line_start = text.rfind("\n", 0, idx) + 1
    col = len(text[line_start:idx].encode("utf-8"))
    return SourceSpan(
        start_byte=start_b, end_byte=end_b,
        start_line=line, start_col=col,
        end_line=line, end_col=col + (end_b - start_b),
    )


def _body_span(start: int, end: int) -> SourceSpan:
    """Span em coordenadas do corpo serializado (linha 1 fictícia)."""
    return SourceSpan(
        start_byte=start, end_byte=end,
        start_line=1, start_col=start, end_line=1, end_col=end,
    )


def _attrs_of_body(body_text: str, obj: object) -> tuple[HclAttribute, ...]:
    """``{k: v}`` → atributos; ``value_span`` em offsets do ``body``.

    A região do valor vai do fim do token ``"k"`` até o início da
    próxima chave irmã (ordem preservada pelo ``json``) — aproximação
    contendo o valor serializado inteiro.
    """
    if not isinstance(obj, dict):
        return ()
    out: list[HclAttribute] = []
    positions: list[tuple[str, int]] = []
    cursor = 0
    for k in obj:
        token = json.dumps(str(k), ensure_ascii=False)
        idx = body_text.find(token, cursor)
        if idx < 0:
            idx = body_text.find(token)
        if idx >= 0:
            positions.append((str(k), idx))
            cursor = idx + len(token)
        else:
            positions.append((str(k), -1))
    for i, (key, idx) in enumerate(positions):
        raw = json.dumps(obj[key], ensure_ascii=False)
        if idx < 0:
            span = _ZERO
        else:
            vstart = idx + len(json.dumps(str(k), ensure_ascii=False)) + 1
            vend = (
                positions[i + 1][1]
                if i + 1 < len(positions) and positions[i + 1][1] > vstart
                else len(body_text)
            )
            span = _body_span(vstart, vend)
        out.append(
            HclAttribute(name=key, value=raw, span=span, value_span=span)
        )
    return tuple(out)


def _block(
    text: str,
    btype: str,
    labels: tuple[str, ...],
    name_key: str,
    body: object,
) -> HclBlock:
    body_text = json.dumps(body, ensure_ascii=False)
    span = _key_span(text, name_key)
    return HclBlock(
        type=btype, labels=labels,
        attributes=_attrs_of_body(body_text, body),
        body=body_text, body_start=0,
        span=span, header_span=span,
    )


def _section_blocks(
    text: str, section: str, content: dict[str, object]
) -> list[HclBlock]:
    """Uma seção top-level → blocos ``HclBlock`` equivalentes."""
    out: list[HclBlock] = []
    if section in ("resource", "data"):
        # {"resource": {"aws_x": {"name": {…}}}}
        for rtype, named in content.items():
            if not isinstance(named, dict):
                continue
            for name, body in named.items():
                out.append(
                    _block(
                        text, section,
                        (str(rtype), str(name)), str(name), body,
                    )
                )
    elif section in ("module", "variable", "output", "provider"):
        for name, body in content.items():
            out.append(_block(text, section, (str(name),), str(name), body))
    else:
        # locals / terraform / seções desconhecidas — bloco anônimo
        out.append(_block(text, section, (), section, content))
    return out


def parse_tfjson(text: str) -> TfJsonFile:
    """Parse estrutural de ``*.tf.json`` — nunca levanta."""
    try:
        data = json.loads(text)
    except (ValueError, json.JSONDecodeError) as exc:
        return TfJsonFile(
            diagnostics=(HclDiagnostic("json-parse-error", str(exc)),)
        )
    if not isinstance(data, dict):
        return TfJsonFile(
            diagnostics=(
                HclDiagnostic(
                    "json-parse-error", "tf.json root must be an object"
                ),
            )
        )
    blocks: list[HclBlock] = []
    for section, content in data.items():
        if isinstance(content, dict):
            blocks.extend(_section_blocks(text, str(section), content))
    return TfJsonFile(blocks=tuple(blocks))
