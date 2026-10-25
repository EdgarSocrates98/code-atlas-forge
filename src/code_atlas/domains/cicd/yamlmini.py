"""Subconjunto mínimo e honesto de YAML (stdlib-only) — lf-0116.

Workflows GitHub Actions usam um subconjunto limitado de YAML: mapas de
bloco, sequências, escalares plain/single/double, comentários, escalares
de bloco ``|``/``>`` e coleções flow de linha única. Este parser cobre
**apenas** esse subconjunto — nunca executa, nunca importa ``yaml``
(dependência externa proibida: adapters são stdlib por contrato).

Construções fora do subconjunto NÃO são fabricadas nem ignoradas em
silêncio: cada uma vira ``YamlDiagnostic`` posicional — anchors ``&``,
aliases ``*``, merge keys ``<<:``, tags ``!``, múltiplos documentos,
coleções flow multiline, tabs em indentação, chaves duplicadas.

Sem resolução implícita YAML 1.1: ``on:`` continua a *string* ``"on"``
(o bug clássico de ``yaml.safe_load`` que lê ``on`` como ``True`` não
existe aqui — chaves e escalares nunca são re-interpretados como bool).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from code_atlas.parsers.contracts import SourceSpan

YNodeKind = Literal["map", "seq", "scalar", "null"]

_DOC_MARKERS = frozenset({"---", "..."})
# cabeçalho de escalar de bloco: "|" ou ">" + chomping opcional + indicador
_BLOCK_HEADER = re.compile(r"^([|>])([+-]?)(\d*)\s*(#.*)?$")
# expressões GitHub ${{ ... }} — sanitizadas em atributos, nunca avaliadas
_ANCHOR_TOKEN = re.compile(r"^&[^\s,[\]{}]+")
_ALIAS_TOKEN = re.compile(r"^\*[^\s,[\]{}]+")
_TAG_TOKEN = re.compile(r"^!(?:[^\s!]+!)?[^\s,[\]{}]*")

_ESCAPES = {
    "0": "\0", "a": "\a", "b": "\b", "t": "\t", "n": "\n",
    "v": "\v", "f": "\f", "r": "\r", "e": "\x1b", '"': '"',
    "\\": "\\", "/": "/", "_": "\xa0", "N": "\x85",
}


@dataclass(frozen=True)
class YamlDiagnostic:
    """Problema estrutural do subconjunto YAML — posicional e honesto."""

    code: str        # ex.: "anchor-unsupported", "expected-key", "tab-indent"
    severity: str    # "error" | "warning" | "info"
    message: str
    line: int = 0    # 1-based; 0 = arquivo inteiro
    col: int = 0     # 0-based (char)


@dataclass(frozen=True)
class YNode:
    """Nó da árvore YAML do subconjunto.

    ``value`` só vale para ``kind == "scalar"`` (texto já decodificado
    de quotes; plain scalars ficam como texto — sem coerção de tipo).
    """

    kind: YNodeKind
    span: SourceSpan
    value: str = ""
    style: str = ""   # plain|single|double|literal|folded|flow — auditoria
    pairs: tuple[tuple[YNode, YNode], ...] = ()
    items: tuple[YNode, ...] = ()

    # --- navegação -----------------------------------------------------

    def get(self, key: str) -> YNode | None:
        """Último ``key`` do mapa (semântica de override do YAML).

        Chaves duplicadas são reportadas como diagnóstico pelo parser;
        aqui o override tardio vence, como ``yaml.safe_load``.
        """
        found: YNode | None = None
        for k, v in self.pairs:
            if k.kind == "scalar" and k.value == key:
                found = v
        return found

    def keys(self) -> tuple[str, ...]:
        """Chaves escalares do mapa, na ordem declarada."""
        return tuple(
            k.value for k, _ in self.pairs if k.kind == "scalar"
        )

    def scalar_text(self) -> str | None:
        """Texto do escalar; ``None`` quando o nó não é escalar."""
        return self.value if self.kind == "scalar" else None

    def scalar_list(self) -> tuple[str, ...]:
        """Lista de textos: seq de escalares, escalar único ou vazio.

        Uniformiza ``needs: build`` e ``needs: [a, b]`` sem inventar
        valores — itens não-escalares são omitidos.
        """
        if self.kind == "scalar":
            return (self.value,) if self.value else ()
        if self.kind == "seq":
            return tuple(
                it.value for it in self.items if it.kind == "scalar"
            )
        return ()


@dataclass
class _Line:
    """Linha física com posição — base de todos os spans."""

    num: int          # 1-based
    byte0: int        # byte offset do início da linha no documento
    raw: str          # conteúdo sem newline
    indent: int       # colunas de espaço à esquerda (tab conta 1)
    body: str         # raw[indent:]


def _is_blank_or_comment(line: _Line) -> bool:
    body = line.body.strip()
    return not body or body.startswith("#")


def _cut_comment(text: str) -> str:
    """Remove trailing comment de escalar plain: `` #`` inicia comentário."""
    for i, ch in enumerate(text):
        if ch == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i]
    return text


class _Parser:
    """Descida recursiva por indentação sobre a tabela de linhas."""

    def __init__(self, text: str) -> None:
        self.lines: list[_Line] = []
        self.diags: list[YamlDiagnostic] = []
        self._doc_markers_seen = 0
        pos = 0
        for n, rl in enumerate(text.split("\n"), 1):
            body = rl[:-1] if rl.endswith("\r") else rl
            ws_len = len(body) - len(body.lstrip(" \t"))
            ws = body[:ws_len]
            if "\t" in ws:
                self._diag(
                    "tab-indent", "error",
                    "tab in indentation is unsupported YAML subset", n, 0,
                )
            self.lines.append(
                _Line(n, pos, body, ws_len, body[ws_len:])
            )
            pos += len(rl.encode("utf-8")) + 1
        self.n = len(self.lines)

    # --- diagnósticos / spans ------------------------------------------

    def _diag(
        self, code: str, severity: str, message: str,
        line: int = 0, col: int = 0,
    ) -> None:
        self.diags.append(
            YamlDiagnostic(code, severity, message, line, col)
        )

    def _span(
        self, l1: int, c1: int, l2: int, c2: int
    ) -> SourceSpan:
        """Span em bytes: ``l*`` são índices 0-based de linha."""
        a = self.lines[l1]
        b = self.lines[l2]
        return SourceSpan(
            start_byte=a.byte0 + len(a.raw[:c1].encode("utf-8")),
            end_byte=b.byte0 + len(b.raw[:c2].encode("utf-8")),
            start_line=l1 + 1,
            start_col=len(a.raw[:c1].encode("utf-8")),
            end_line=l2 + 1,
            end_col=len(b.raw[:c2].encode("utf-8")),
        )

    def _eol_span(self, line_idx: int) -> SourceSpan:
        ln = self.lines[line_idx]
        col = len(ln.raw)
        return self._span(line_idx, col, line_idx, col)

    def _sig(self, i: int) -> int | None:
        """Próxima linha significativa (não-branca, não-comentário)."""
        while i < self.n:
            if not _is_blank_or_comment(self.lines[i]):
                return i
            i += 1
        return None

    # --- entrada ----------------------------------------------------------

    def parse_document(self) -> YNode | None:
        """Documento único; marcadores ``---``/``...`` tolerados."""
        i = self._sig(0)
        while i is not None and self.lines[i].body.strip() == "---":
            self._doc_markers_seen += 1
            i = self._sig(i + 1)
        if i is not None and self.lines[i].body.lstrip().startswith("%"):
            while i is not None and self.lines[i].body.lstrip().startswith("%"):
                self._diag(
                    "directive-unsupported", "warning",
                    "YAML directive ignored", self.lines[i].num,
                )
                i = self._sig(i + 1)
            while i is not None and self.lines[i].body.strip() == "---":
                i = self._sig(i + 1)
        if i is None:
            return None
        node, i2 = self.parse_block(i, self.lines[i].indent)
        # conteúdo remanescente: docs extras ou lixo estrutural
        j = self._sig(i2)
        in_extra_doc = False
        while j is not None:
            stripped = self.lines[j].body.strip()
            if stripped in _DOC_MARKERS:
                if stripped == "---":
                    self._diag(
                        "multi-document-unsupported", "warning",
                        "additional YAML document ignored",
                        self.lines[j].num,
                    )
                    in_extra_doc = True
                else:
                    in_extra_doc = False
            elif not in_extra_doc:
                self._diag(
                    "unparsed-trailing-content", "error",
                    "line could not be attached to the document tree",
                    self.lines[j].num, self.lines[j].indent,
                )
            j = self._sig(j + 1)
        return node

    # --- núcleo -----------------------------------------------------------

    def parse_block(self, i: int, min_indent: int) -> tuple[YNode | None, int]:
        """Nó de bloco começando em ``i``; ``None`` se nada se aplica."""
        j = self._sig(i)
        if j is None:
            return None, i
        ln = self.lines[j]
        if ln.indent < min_indent:
            return None, i
        if ln.body.strip() in _DOC_MARKERS:
            return None, i
        if self._is_seq_item(ln.body):
            return self._parse_seq(j, ln.indent)
        if self._split_key(ln.body) is not None:
            return self._parse_map(j, ln.indent)
        return self._parse_scalar(j, ln.indent)

    @staticmethod
    def _is_seq_item(body: str) -> bool:
        return body == "-" or body.startswith("- ")

    def _split_key(self, body: str) -> int | None:
        """Posição do ``:`` separador de ``key:``; ``None`` se não-mapa.

        Separador YAML: ``:`` seguido de espaço ou fim-de-linha, fora de
        quotes e de coleções flow — ``http://x`` nunca é chave.
        """
        if not body:
            return None
        if body[0] in "'\"":
            return self._quoted_key_pos(body)
        return self._plain_key_pos(body)

    def _quoted_key_pos(self, body: str) -> int | None:
        end = self._quoted_end(body, 0)
        if end is None:
            return None
        rest = body[end:]
        if rest.startswith(":") and (len(rest) == 1 or rest[1] == " "):
            return end
        return None

    @staticmethod
    def _is_key_colon(body: str, k: int) -> bool:
        return (
            body[k] == ":"
            and (k + 1 == len(body) or body[k + 1] == " ")
        )

    def _plain_key_pos(self, body: str) -> int | None:
        depth = 0
        for k, ch in enumerate(body):
            if ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
            elif ch == "#" and k > 0 and body[k - 1] == " ":
                return None  # comentário antes de qualquer ':' → escalar
            elif depth == 0 and self._is_key_colon(body, k):
                return k
        return None

    def _parse_map(self, i: int, indent: int) -> tuple[YNode, int]:
        pairs: list[tuple[YNode, YNode]] = []
        first = i
        seen: dict[str, int] = {}
        while True:
            j = self._sig(i)
            if j is None:
                i = self.n
                break
            ln = self.lines[j]
            if ln.indent < indent or ln.body.strip() in _DOC_MARKERS:
                i = j
                break
            if ln.indent > indent:
                self._diag(
                    "unexpected-indent", "error",
                    "line indented deeper than enclosing map",
                    ln.num, ln.indent,
                )
                i = j + 1
                continue
            if self._is_seq_item(ln.body):
                i = j
                break
            pair, i = self._parse_map_pair(j, indent, seen)
            if pair is not None:
                pairs.append(pair)
        return (
            YNode(kind="map", pairs=tuple(pairs),
                  span=self._join_span(first, pairs)),
            i,
        )

    def _parse_map_pair(
        self, j: int, indent: int, seen: dict[str, int]
    ) -> tuple[tuple[YNode, YNode] | None, int]:
        """Uma entrada ``key: value`` (inline ou bloco) na linha ``j``."""
        ln = self.lines[j]
        kpos = self._split_key(ln.body)
        if kpos is None:
            self._diag(
                "expected-key", "error",
                "line inside mapping has no 'key:' separator",
                ln.num, ln.indent,
            )
            return None, j + 1
        raw_key = ln.body[:kpos].strip()
        key = self._decode_key(raw_key, j)
        if key in seen:
            self._diag(
                "duplicate-key", "warning",
                f"duplicate mapping key {key!r}: later entry wins",
                ln.num, ln.indent,
            )
        seen[key] = j
        if key == "<<":
            self._diag(
                "merge-key-unsupported", "warning",
                "merge key '<<' is outside the supported subset",
                ln.num, ln.indent,
            )
        key_node = YNode(
            kind="scalar", value=key, style="key",
            span=self._span(j, ln.indent, j, ln.indent + len(raw_key)),
        )
        rest = ln.body[kpos + 1:]
        stripped = rest.strip()
        if not stripped:
            child, i = self._map_value_block(j, indent)
            return (key_node, child), i
        child, i = self._parse_inline(
            stripped, j,
            ln.indent + kpos + 1 + len(rest) - len(rest.lstrip()),
            indent, j + 1,
        )
        return (key_node, child), i

    def _map_value_block(
        self, j: int, indent: int
    ) -> tuple[YNode, int]:
        """Valor de ``key:`` vazio: bloco mais fundo ou seq mesmo indent."""
        j2 = self._sig(j + 1)
        if (
            j2 is not None
            and self.lines[j2].indent == indent
            and self._is_seq_item(self.lines[j2].body)
        ):
            return self._parse_seq(j2, indent)
        child, i = self.parse_block(j + 1, indent + 1)
        return (
            child if child is not None else self._null_at(j)
        ), i

    def _join_span(
        self, first_line: int, pairs: list[tuple[YNode, YNode]]
    ) -> SourceSpan:
        first = self.lines[first_line]
        if pairs:
            last = pairs[-1][1].span
            return SourceSpan(
                start_byte=first.byte0 + len(
                    first.raw[: first.indent].encode("utf-8")
                ),
                end_byte=last.end_byte,
                start_line=first_line + 1,
                start_col=first.indent,
                end_line=last.end_line,
                end_col=last.end_col,
            )
        return self._eol_span(first_line)

    def _null_at(self, line_idx: int) -> YNode:
        return YNode(kind="null", span=self._eol_span(line_idx))

    def _parse_seq(self, i: int, indent: int) -> tuple[YNode, int]:
        items: list[YNode] = []
        first = i
        while True:
            j = self._sig(i)
            if j is None:
                i = self.n
                break
            ln = self.lines[j]
            if (
                ln.indent < indent
                or ln.body.strip() in _DOC_MARKERS
                or not self._is_seq_item(ln.body)
            ):
                i = j
                break
            if ln.indent > indent:
                self._diag(
                    "unexpected-indent", "error",
                    "sequence item indented deeper than sequence",
                    ln.num, ln.indent,
                )
                i = j + 1
                continue
            body = ln.body
            after = body[1:].lstrip(" ")
            if not after:
                child, i = self.parse_block(j + 1, indent + 1)
                items.append(child if child is not None else self._null_at(j))
                continue
            # linha virtual: conteúdo após '- ' funciona como bloco
            # próprio cuja indentação é a coluna real do conteúdo.
            vcol = ln.indent + (len(body) - len(after))
            virt = _Line(ln.num, ln.byte0, ln.raw, vcol, ln.raw[vcol:])
            self.lines[j] = virt
            try:
                child, i = self.parse_block(j, vcol)
            finally:
                self.lines[j] = ln
            if i == j:
                # parse_block recusou a linha virtual (ex.: marcador de
                # documento como item) — avançar para nunca ciclar.
                i = j + 1
            items.append(
                child if child is not None else self._null_at(j)
            )
        end = items[-1].span if items else self._eol_span(first)
        start = self.lines[first]
        return (
            YNode(
                kind="seq", items=tuple(items),
                span=SourceSpan(
                    start_byte=start.byte0 + start.indent,
                    end_byte=end.end_byte,
                    start_line=first + 1,
                    start_col=start.indent,
                    end_line=end.end_line,
                    end_col=end.end_col,
                ),
            ),
            i,
        )

    def _parse_scalar(self, i: int, indent: int) -> tuple[YNode, int]:
        """Escalar de linha única; continuações mais fundas viram fold."""
        ln = self.lines[i]
        body = ln.body
        node, _ = self._inline_scalar(body, i, ln.indent)
        # continuações plain: linhas mais indentadas dobram com ' '
        consumed = i + 1
        extra: list[str] = []
        while True:
            j = self._sig(consumed)
            if j is None:
                consumed = self.n
                break
            cont = self.lines[j]
            if cont.indent <= indent or cont.body.strip() in _DOC_MARKERS:
                consumed = j
                break
            if node.style in ("single", "double"):
                break  # quoted multi-line fora do subset
            if self._is_seq_item(cont.body) or self._split_key(cont.body):
                break  # estrutura — não é continuação
            extra.append(_cut_comment(cont.body).strip())
            consumed = j + 1
        if extra:
            node = YNode(
                kind="scalar",
                value=" ".join([node.value, *extra]),
                style="plain-folded",
                span=SourceSpan(
                    start_byte=node.span.start_byte,
                    end_byte=self._eol_span(consumed - 1).end_byte,
                    start_line=node.span.start_line,
                    start_col=node.span.start_col,
                    end_line=consumed,
                    end_col=len(self.lines[consumed - 1].raw),
                ),
            )
        return node, consumed

    # --- valores inline ------------------------------------------------

    def _parse_inline(
        self, text: str, line_idx: int, col: int,
        parent_indent: int, next_i: int,
    ) -> tuple[YNode, int]:
        """Valor na mesma linha do ``key:``/``- `` — escalar, flow ou bloco."""
        head = text[0]
        if head in "|>":
            m = _BLOCK_HEADER.match(text)
            if m is None:
                self._diag(
                    "block-scalar-header", "error",
                    f"unsupported block scalar header {text!r}",
                    self.lines[line_idx].num, col,
                )
                return self._plain_node(text, line_idx, col), next_i
            style = "literal" if m.group(1) == "|" else "folded"
            return self._block_scalar(
                parent_indent, next_i, style, m.group(2), line_idx, col,
            )
        if head == "&":
            self._diag(
                "anchor-unsupported", "warning",
                f"anchor {text.split()[0]!r} outside subset; value kept",
                self.lines[line_idx].num, col,
            )
            m = _ANCHOR_TOKEN.match(text)
            rest = text[m.end():].lstrip() if m else ""
            if not rest:
                child, i2 = self.parse_block(next_i, parent_indent + 1)
                return (
                    child if child is not None else self._null_at(line_idx)
                ), i2
            return self._plain_node(rest, line_idx, col), next_i
        if head == "*":
            self._diag(
                "alias-unsupported", "error",
                f"alias {text.split()[0]!r} cannot be resolved — no "
                "anchor table; value reported as null",
                self.lines[line_idx].num, col,
            )
            return self._null_at(line_idx), next_i
        if head == "!":
            self._diag(
                "tag-unsupported", "warning",
                f"tag {text.split()[0]!r} outside subset; value kept",
                self.lines[line_idx].num, col,
            )
            m = _TAG_TOKEN.match(text)
            rest = text[m.end():].lstrip() if m else ""
            if not rest:
                child, i2 = self.parse_block(next_i, parent_indent + 1)
                return (
                    child if child is not None else self._null_at(line_idx)
                ), i2
            return self._plain_node(rest, line_idx, col), next_i
        if head in "[{":
            return self._parse_flow(text, line_idx, col), next_i
        node, _ = self._inline_scalar(text, line_idx, col)
        return node, next_i

    def _plain_node(self, text: str, line_idx: int, col: int) -> YNode:
        value = _cut_comment(text).strip()
        return YNode(
            kind="scalar", value=value, style="plain",
            span=self._span(line_idx, col, line_idx, col + len(text)),
        )

    def _inline_scalar(
        self, text: str, line_idx: int, col: int
    ) -> tuple[YNode, int]:
        """Escalar inline: quoted ou plain com comment-cut."""
        if text and text[0] in "'\"":
            value, end = self._decode_quoted(text, 0, line_idx, col)
            trailing = text[end:].strip()
            if trailing and not trailing.startswith("#"):
                self._diag(
                    "scalar-trailing-content", "warning",
                    "content after closing quote ignored",
                    self.lines[line_idx].num, col + end,
                )
            return (
                YNode(
                    kind="scalar", value=value,
                    style="single" if text[0] == "'" else "double",
                    span=self._span(line_idx, col, line_idx, col + end),
                ),
                end,
            )
        value = _cut_comment(text).strip()
        return (
            YNode(
                kind="scalar", value=value, style="plain",
                span=self._span(
                    line_idx, col, line_idx, col + len(text)
                ),
            ),
            len(text),
        )

    # --- coleções flow --------------------------------------------------

    def _parse_flow(self, text: str, line_idx: int, col: int) -> YNode:
        node, pos = self._flow_node(text, 0, line_idx, col)
        if node is None:
            return self._null_at(line_idx)
        trailing = text[pos:].strip()
        if trailing and not trailing.startswith("#"):
            self._diag(
                "flow-trailing-content", "error",
                "content after flow collection ignored",
                self.lines[line_idx].num, col + pos,
            )
        return YNode(
            kind=node.kind, value=node.value, style="flow",
            pairs=node.pairs, items=node.items,
            span=self._span(line_idx, col, line_idx, col + pos),
        )

    def _flow_node(
        self, s: str, pos: int, line_idx: int, col: int
    ) -> tuple[YNode | None, int]:
        pos = self._flow_ws(s, pos)
        if pos >= len(s):
            self._diag(
                "flow-unexpected-end", "error",
                "flow collection ended unexpectedly",
                self.lines[line_idx].num, col + pos,
            )
            return None, pos
        ch = s[pos]
        if ch == "[":
            return self._flow_seq(s, pos, line_idx, col)
        if ch == "{":
            return self._flow_map(s, pos, line_idx, col)
        if ch in "'\"":
            value, end = self._decode_quoted(s, pos, line_idx, col)
            return (
                YNode(kind="scalar", value=value,
                      style="single" if s[pos] == "'" else "double",
                      span=self._span(
                          line_idx, col + pos, line_idx, col + end)),
                end,
            )
        # escalar plain dentro de flow: até , ] } — ou ':' seguido de
        # separador/espaço (é o separador de chave do flow map)
        start = pos
        while pos < len(s):
            ch = s[pos]
            if ch in ",]}":
                break
            if (
                ch == ":"
                and (pos + 1 == len(s) or s[pos + 1] in " ,]}")
            ):
                break
            pos += 1
        raw = s[start:pos].strip()
        return (
            YNode(kind="scalar", value=raw, style="flow",
                  span=self._span(
                      line_idx, col + start, line_idx, col + pos)),
            pos,
        )

    def _flow_ws(self, s: str, pos: int) -> int:
        while pos < len(s) and s[pos] in " \t":
            pos += 1
        return pos

    def _flow_seq(
        self, s: str, pos: int, line_idx: int, col: int
    ) -> tuple[YNode | None, int]:
        items: list[YNode] = []
        start = pos
        pos += 1
        while True:
            pos = self._flow_ws(s, pos)
            if pos >= len(s):
                self._diag(
                    "flow-unclosed", "error",
                    "flow sequence missing ']'", self.lines[line_idx].num,
                    col + start,
                )
                return (
                    YNode(kind="seq", items=tuple(items),
                          span=self._span(
                              line_idx, col + start, line_idx, col + pos)),
                    pos,
                )
            if s[pos] == "]":
                return (
                    YNode(kind="seq", items=tuple(items),
                          span=self._span(
                              line_idx, col + start, line_idx, col + pos + 1)),
                    pos + 1,
                )
            if s[pos] == ",":
                pos += 1
                continue
            item, pos = self._flow_node(s, pos, line_idx, col)
            if item is None:
                return (
                    YNode(kind="seq", items=tuple(items),
                          span=self._span(
                              line_idx, col + start, line_idx, col + pos)),
                    pos,
                )
            items.append(item)

    def _flow_map(
        self, s: str, pos: int, line_idx: int, col: int
    ) -> tuple[YNode | None, int]:
        pairs: list[tuple[YNode, YNode]] = []
        start = pos
        pos += 1
        while True:
            pos = self._flow_ws(s, pos)
            if pos >= len(s):
                self._diag(
                    "flow-unclosed", "error",
                    "flow mapping missing '}'", self.lines[line_idx].num,
                    col + start,
                )
                return (
                    YNode(kind="map", pairs=tuple(pairs),
                          span=self._span(
                              line_idx, col + start, line_idx, col + pos)),
                    pos,
                )
            if s[pos] == "}":
                return (
                    YNode(kind="map", pairs=tuple(pairs),
                          span=self._span(
                              line_idx, col + start, line_idx, col + pos + 1)),
                    pos + 1,
                )
            if s[pos] == ",":
                pos += 1
                continue
            # chave flow: escalar simples até ':'
            key_node, pos = self._flow_node(s, pos, line_idx, col)
            if key_node is None:
                break
            pos = self._flow_ws(s, pos)
            if pos < len(s) and s[pos] == ":":
                pos += 1
                pos = self._flow_ws(s, pos)
                if pos < len(s) and s[pos] in ",}":
                    val = self._null_at(line_idx)
                else:
                    child, pos = self._flow_node(s, pos, line_idx, col)
                    val = child if child is not None else self._null_at(line_idx)
            else:
                val = self._null_at(line_idx)
            pairs.append((key_node, val))
        return (
            YNode(kind="map", pairs=tuple(pairs),
                  span=self._span(
                      line_idx, col + start, line_idx, col + pos)),
            pos,
        )

    # --- escalares de bloco ---------------------------------------------

    def _block_scalar(
        self, parent_indent: int, i: int, style: str,
        chomp: str, header_line: int, header_col: int,
    ) -> tuple[YNode, int]:
        """``|``/``>`` com chomping; consome linhas mais indentadas.

        Linhas em branco pertencem ao escalar; comentários dentro dele
        são CONTEÚDO (não são stripados) — ex.: ``run: | # comenta``.
        """
        vals, j = self._block_scalar_lines(parent_indent, i)
        value = self._block_scalar_text(vals, style, chomp)
        end_line = vals[-1][0] if vals else header_line
        return (
            YNode(
                kind="scalar", value=value, style=style,
                span=self._span(
                    header_line, header_col,
                    end_line, len(self.lines[end_line].raw),
                ),
            ),
            j,
        )

    def _block_scalar_lines(
        self, parent_indent: int, i: int
    ) -> tuple[list[tuple[int, str, bool]], int]:
        """Coleta linhas do escalar: (índice, texto, more-indented)."""
        vals: list[tuple[int, str, bool]] = []
        content_indent = -1
        j = i
        while j < self.n:
            ln = self.lines[j]
            if not ln.body.strip():
                vals.append((j, "", False))
                j += 1
                continue
            if ln.indent <= parent_indent:
                break
            if content_indent < 0:
                content_indent = ln.indent
            if ln.indent < content_indent:
                self._diag(
                    "block-scalar-indent", "warning",
                    "line less indented than block scalar content",
                    ln.num, ln.indent,
                )
            keep = min(ln.indent, content_indent)
            vals.append((j, ln.raw[keep:], ln.indent > content_indent))
            j += 1
        # trailing blanks além do último conteúdo não geram linhas extras
        while vals and vals[-1][1] == "":
            vals.pop()
        return vals, j

    @staticmethod
    def _block_scalar_text(
        vals: list[tuple[int, str, bool]], style: str, chomp: str
    ) -> str:
        if style == "literal":
            text = "\n".join(v for _, v, _ in vals)
        else:
            parts: list[str] = []
            for k, (_, v, more) in enumerate(vals):
                if k > 0:
                    prev_blank = vals[k - 1][1] == ""
                    prev_more = vals[k - 1][2]
                    if v == "" or prev_blank or more or prev_more:
                        parts.append("\n")
                    else:
                        parts.append(" ")
                parts.append(v)
            text = "".join(parts)
        core = text.rstrip("\n")
        if chomp == "-":
            return core
        if chomp == "+":
            return text if text.endswith("\n") or not text else text + "\n"
        return core + ("\n" if core else "")

    # --- quotes -----------------------------------------------------------

    def _quoted_end(self, s: str, pos: int) -> int | None:
        """Posição após a aspa de fechamento; ``None`` se não fecha."""
        q = s[pos]
        pos += 1
        while pos < len(s):
            ch = s[pos]
            if ch == q:
                if q == "'" and pos + 1 < len(s) and s[pos + 1] == "'":
                    pos += 2
                    continue
                return pos + 1
            if q == '"' and ch == "\\":
                pos += 2
                continue
            pos += 1
        return None

    def _decode_quoted(
        self, s: str, pos: int, line_idx: int, col: int
    ) -> tuple[str, int]:
        q = s[pos]
        out: list[str] = []
        pos += 1
        while pos < len(s):
            ch = s[pos]
            if ch == q:
                if q == "'" and pos + 1 < len(s) and s[pos + 1] == "'":
                    out.append("'")
                    pos += 2
                    continue
                return "".join(out), pos + 1
            if q == '"' and ch == "\\" and pos + 1 < len(s):
                esc = s[pos + 1]
                if esc in _ESCAPES:
                    out.append(_ESCAPES[esc])
                else:
                    self._diag(
                        "unknown-escape", "warning",
                        f"unknown escape \\{esc} kept literally",
                        self.lines[line_idx].num, col + pos,
                    )
                    out.append(ch + esc)
                pos += 2
                continue
            out.append(ch)
            pos += 1
        self._diag(
            "unterminated-quote", "error",
            "quoted scalar never closes", self.lines[line_idx].num, col,
        )
        return "".join(out), pos

    def _decode_key(self, raw: str, line_idx: int) -> str:
        if raw and raw[0] in "'\"":
            value, _ = self._decode_quoted(
                raw, 0, line_idx, self.lines[line_idx].indent
            )
            return value
        return raw


@dataclass(frozen=True)
class MiniYamlResult:
    """Resultado honesto: árvore + diagnósticos, nunca exceção."""

    root: YNode | None
    diagnostics: tuple[YamlDiagnostic, ...] = field(
        default_factory=tuple
    )


def parse(text: str) -> MiniYamlResult:
    """Parse do subconjunto YAML; falhas viram diagnósticos."""
    parser = _Parser(text)
    try:
        root = parser.parse_document()
    except Exception as exc:  # robustez: bug vira diagnóstico, nunca crash
        parser._diag(
            "parser-internal-error", "error",
            f"internal parser failure: {exc}",
        )
        return MiniYamlResult(None, tuple(parser.diags))
    return MiniYamlResult(root, tuple(parser.diags))
