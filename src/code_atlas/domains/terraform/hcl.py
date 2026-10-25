"""Parser HCL minimalista e seguro (lf-0115, Cycle 7 Phase 7).

Hand-rolled, stdlib only — **nunca** executa ``terraform``, nunca lê
state, nunca faz plan/apply. Cobre o subconjunto estrutural de HCL
necessário ao adapter:

- blocos ``resource``/``module``/``data``/``variable``/``locals``/
  ``output``/``provider``/``terraform`` com labels quoted;
- atributos ``name = expr`` com strings, heredocs (``<<EOF`` /
  ``<<-EOF``), listas/mapas multi-linha, chamadas de função e
  interpolações ``${...}``;
- comentários ``#``, ``//`` e ``/* */``;
- blocos aninhados arbitrários (``lifecycle {}``, ``statement {}``…).

Semântica de falha: **diagnóstico, nunca exceção** — conteúdo malformado
produz o que foi parseado (parcial) + ``HclDiagnostic`` com posição.
Spans seguem a convenção de ``SourceSpan``: byte offsets UTF-8, linha
1-based, coluna em bytes 0-based.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from code_atlas.parsers.contracts import SourceSpan

_IDENT_START = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"
)
_IDENT = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_0123456789-"
)


@dataclass(frozen=True)
class HclDiagnostic:
    """Problema estrutural do parser HCL — honesto, com posição."""

    code: str          # parse-error | unterminated-block | unterminated-string…
    message: str
    line: int = 0      # 1-based; 0 = desconhecida
    col: int = 0       # byte col 0-based


@dataclass(frozen=True)
class HclAttribute:
    """Atributo ``name = expr`` num corpo de bloco (ou top-level tfvars)."""

    name: str
    value: str          # texto bruto da expressão (não avaliado)
    span: SourceSpan    # span de ``name = expr``
    value_span: SourceSpan


@dataclass(frozen=True)
class HclBlock:
    """Bloco HCL ``type "label"… { body }`` com estrutura aninhada."""

    type: str
    labels: tuple[str, ...] = ()
    attributes: tuple[HclAttribute, ...] = ()
    children: tuple[HclBlock, ...] = ()
    body: str = ""      # texto bruto entre ``{`` e ``}`` (sem as chaves)
    body_start: int = 0  # byte offset do início de ``body``
    span: SourceSpan | None = None      # bloco inteiro
    header_span: SourceSpan | None = None  # ``type "l1" "l2"`` sem ``{``

    def attribute(self, name: str) -> HclAttribute | None:
        """Primeiro atributo top-level com ``name`` (ordem declarada)."""
        for a in self.attributes:
            if a.name == name:
                return a
        return None


@dataclass(frozen=True)
class HclFile:
    """Saída do parser: itens top-level + diagnósticos parciais."""

    blocks: tuple[HclBlock, ...] = ()
    attributes: tuple[HclAttribute, ...] = ()  # top-level (tfvars)
    diagnostics: tuple[HclDiagnostic, ...] = ()


class _ParseError(Exception):
    """Erro interno de parse — o caller resincroniza, nunca propaga."""


class _Parser:
    """Cursor sobre ``text`` com posição→(byte,line,col) pré-computada."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.n = len(text)
        self.pos = 0
        self.diags: list[HclDiagnostic] = []
        # offset de byte UTF-8 por índice de char (posições são chars;
        # SourceSpan exige bytes — converte uma vez, O(n)).
        self._byte: list[int] = [0] * (self.n + 1)
        off = 0
        for i, ch in enumerate(text):
            self._byte[i] = off
            off += len(ch.encode("utf-8"))
        self._byte[self.n] = off
        self._lines: list[int] = [0]
        for i, ch in enumerate(text):
            if ch == "\n":
                self._lines.append(i + 1)

    # --- posição ------------------------------------------------------

    def _lc(self, char_pos: int) -> tuple[int, int, int]:
        """``(byte_offset, line 1-based, col bytes 0-based)``."""
        cp = min(max(char_pos, 0), self.n)
        byte = self._byte[cp]
        li = bisect.bisect_right(self._lines, cp) - 1
        line_start = self._lines[li]
        return byte, li + 1, byte - self._byte[line_start]

    def span(self, start: int, end: int) -> SourceSpan:
        sb, sl, sc = self._lc(start)
        eb, el, ec = self._lc(end)
        return SourceSpan(
            start_byte=sb, end_byte=eb,
            start_line=sl, start_col=sc, end_line=el, end_col=ec,
        )

    def diag(self, code: str, message: str, pos: int) -> None:
        _, line, col = self._lc(pos)
        self.diags.append(
            HclDiagnostic(code=code, message=message, line=line, col=col)
        )

    # --- trivia -------------------------------------------------------

    def skip_trivia(self) -> None:
        """Consome whitespace e comentários ``#``/``//``/``/* */``."""
        while self.pos < self.n:
            c = self.text[self.pos]
            if c in " \t\r\n":
                self.pos += 1
                continue
            if c == "#":
                self._skip_line_comment()
                continue
            if c == "/" and self.pos + 1 < self.n:
                nxt = self.text[self.pos + 1]
                if nxt == "/":
                    self._skip_line_comment()
                    continue
                if nxt == "*":
                    end = self.text.find("*/", self.pos + 2)
                    if end < 0:
                        self.diag(
                            "unterminated-comment",
                            "block comment never closed", self.pos,
                        )
                        self.pos = self.n
                        return
                    self.pos = end + 2
                    continue
            return

    def _skip_line_comment(self) -> None:
        nl = self.text.find("\n", self.pos)
        self.pos = self.n if nl < 0 else nl + 1

    # --- strings / heredocs -------------------------------------------

    def _skip_string(self) -> None:
        """Consome uma string ``"…"`` a partir de ``pos`` (na aspa).

        Lida com escapes ``\\"`` e interpolações ``${…}`` balanceadas —
        strings dentro da interpolação são reconhecidas recursivamente.
        """
        start = self.pos
        self.pos += 1  # opening quote
        while self.pos < self.n:
            c = self.text[self.pos]
            if c == "\\":
                self.pos += 2
                continue
            if c == '"':
                self.pos += 1
                return
            if c == "$" and self.pos + 1 < self.n and self.text[self.pos + 1] == "{":
                self._skip_interpolation()
                continue
            self.pos += 1
        self.diag("unterminated-string", "string literal never closed", start)

    def _skip_interpolation(self) -> None:
        """Consome ``${…}`` — braces balanceados, strings internas ok."""
        start = self.pos
        self.pos += 2  # consume '${'
        depth = 1
        while self.pos < self.n and depth:
            c = self.text[self.pos]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == '"':
                self._skip_string()
                continue
            elif c == "#":
                self._skip_line_comment()
                continue
            self.pos += 1
        if depth:
            self.diag(
                "unterminated-interpolation",
                "${…} never closed", start,
            )

    def _heredoc_tag(self) -> tuple[str, int] | None:
        """Tag de ``<<TAG``/``<<-TAG``/``<<"TAG"`` → ``(tag, end)``."""
        p = self.pos
        if self.text.startswith("<<-", p):
            p += 3
        elif self.text.startswith("<<", p):
            p += 2
        else:
            return None
        q = p
        quoted = q < self.n and self.text[q] == '"'
        if quoted:
            q += 1
        tstart = q
        while q < self.n and self.text[q] in _IDENT:
            q += 1
        if q == tstart:
            return None  # '<<' sem tag — não é heredoc
        if quoted:
            if q < self.n and self.text[q] == '"':
                q += 1
            else:
                return None
        tag = self.text[tstart:q - (1 if quoted else 0)].strip()
        return (tag, q) if tag else None

    def _consume_heredoc_body(self, tag: str, line_start: int) -> None:
        """Avança linha a linha até o terminador ``tag``."""
        while line_start <= self.n:
            line_end = self.text.find("\n", line_start)
            if line_end < 0:
                line_end = self.n
            if self.text[line_start:line_end].strip() == tag:
                self.pos = line_end + 1 if line_end < self.n else self.n
                return
            if line_end >= self.n:
                break
            line_start = line_end + 1
        self.diag(
            "unterminated-heredoc",
            f"heredoc terminator {tag!r} not found", self.pos,
        )
        self.pos = self.n

    def _try_heredoc(self) -> bool:
        """Se ``pos`` inicia ``<<TAG``/``<<-TAG``, consome o heredoc."""
        res = self._heredoc_tag()
        if res is None:
            return False
        tag, q = res
        # consome até o fim da linha de abertura
        nl = self.text.find("\n", q)
        if nl < 0:
            self.diag(
                "unterminated-heredoc",
                f"heredoc {tag!r} opens at EOF", self.pos,
            )
            self.pos = self.n
            return True
        self._consume_heredoc_body(tag, nl + 1)
        return True

    # --- expressões -----------------------------------------------------

    def read_expression(self) -> tuple[str, int, int]:
        """Lê uma expressão até newline/``}`` em profundidade zero.

        Retorna ``(texto, start, end)`` em posições de char. Erros
        viram diagnósticos — a expressão termina no melhor ponto
        conhecido (parcial, nunca exceção).
        """
        start = self.pos
        depth = 0
        while self.pos < self.n:
            c = self.text[self.pos]
            if depth == 0 and c in ("\n", "}"):
                break  # newline ou '}' em depth 0 encerram a expressão
            if c == '"':
                self._skip_string()
                continue
            if c == "<" and self._try_heredoc():
                continue
            if self._is_comment_start():
                if depth == 0:
                    break  # comentário é trivia — encerra a expressão
                self._skip_comment()
                continue
            depth = self._expr_delimiter(c, depth)
            self.pos += 1
        if depth != 0:
            self.diag(
                "unbalanced-delimiter",
                f"expression ended with depth {depth}", start,
            )
        # whitespace final é trivia (ex.: antes de comentário) — não faz
        # parte do valor bruto.
        raw = self.text[start:self.pos].rstrip()
        return raw, start, start + len(raw)

    def _is_comment_start(self) -> bool:
        c = self.text[self.pos]
        if c == "#":
            return True
        return (
            c == "/"
            and self.pos + 1 < self.n
            and self.text[self.pos + 1] in "/*"
        )

    def _skip_comment(self) -> None:
        """``pos`` em início de comentário ``#``/``//``/``/* */``."""
        if (
            self.text[self.pos] == "/"
            and self.pos + 1 < self.n
            and self.text[self.pos + 1] == "*"
        ):
            end = self.text.find("*/", self.pos + 2)
            if end < 0:
                self.diag(
                    "unterminated-comment",
                    "block comment never closed", self.pos,
                )
                self.pos = self.n
                return
            self.pos = end + 2
            return
        self._skip_line_comment()

    def _expr_delimiter(self, c: str, depth: int) -> int:
        """Bracket ``([{``/``)]}`` → novo depth (diag em desequilíbrio)."""
        if c in "([{":
            return depth + 1
        if c in ")]}":
            if depth == 0:
                self.diag(
                    "unbalanced-delimiter",
                    f"unexpected {c!r} at depth 0", self.pos,
                )
                return depth
            return depth - 1
        return depth

    # --- tokens --------------------------------------------------------

    def _read_ident(self) -> tuple[str, int, int] | None:
        """Identificador ``[A-Za-z_][\\w-]*``; ``None`` sem consumir."""
        if self.pos >= self.n or self.text[self.pos] not in _IDENT_START:
            return None
        start = self.pos
        self.pos += 1
        while self.pos < self.n and self.text[self.pos] in _IDENT:
            self.pos += 1
        return self.text[start:self.pos], start, self.pos

    def _read_label(self) -> tuple[str, int, int] | None:
        """Label de bloco: string quoted ou identificador bare."""
        self.skip_trivia()
        if self.pos >= self.n:
            return None
        start = self.pos
        if self.text[self.pos] == '"':
            self._skip_string()
            raw = self.text[start:self.pos]
            # remove aspas externas sem interpretar escapes — labels
            # interpoladas são inválidas em HCL de qualquer forma
            return raw[1:-1], start, self.pos
        tok = self._read_ident()
        if tok is not None:
            return tok
        # label numérico (HCL antigo permite ``module 0 {…}``)
        dstart = self.pos
        while self.pos < self.n and self.text[self.pos].isdigit():
            self.pos += 1
        if self.pos > dstart:
            return self.text[dstart:self.pos], dstart, self.pos
        return None

    # --- corpo / bloco ---------------------------------------------------

    def _parse_body(self, body_char_start: int) -> tuple[
        tuple[HclAttribute, ...], tuple[HclBlock, ...], int
    ]:
        """Parse até o ``}`` de fechamento; retorna attrs, filhos, end."""
        attrs: list[HclAttribute] = []
        children: list[HclBlock] = []
        while True:
            self.skip_trivia()
            if self.pos >= self.n:
                self.diag(
                    "unterminated-block",
                    "block body reached EOF without '}'", self.pos,
                )
                return tuple(attrs), tuple(children), self.pos
            if self.text[self.pos] == "}":
                self.pos += 1
                return tuple(attrs), tuple(children), self.pos
            item = self._parse_item()
            if isinstance(item, HclAttribute):
                attrs.append(item)
            elif isinstance(item, HclBlock):
                children.append(item)
            # item None = resync já aplicado por _parse_item

    def _read_block_labels(
        self, name: str, item_start: int
    ) -> tuple[str, ...] | None:
        """Labels até ``{`` — ``None`` quando o header não fecha."""
        labels: list[str] = []
        while True:
            self.skip_trivia()
            if self.pos >= self.n:
                self.diag(
                    "unterminated-block",
                    f"block {name!r} header reached EOF", item_start,
                )
                return None
            if self.text[self.pos] == "{":
                return tuple(labels)
            lab = self._read_label()
            if lab is not None:
                labels.append(lab[0])
                continue
            self.diag(
                "parse-error",
                f"expected label or '{{' after {name!r}", self.pos,
            )
            # resync: pula até próximo '{' ou newline
            while self.pos < self.n and self.text[self.pos] not in "{\n":
                self.pos += 1
            if self.pos >= self.n or self.text[self.pos] == "\n":
                return None

    def _parse_item(self) -> HclAttribute | HclBlock | None:
        """Um item de corpo: atributo ``name = expr`` ou bloco aninhado."""
        item_start = self.pos
        tok = self._read_ident()
        if tok is None:
            self.diag(
                "parse-error",
                f"unexpected character {self.text[self.pos]!r}", self.pos,
            )
            # resync: avança até newline — progresso garantido
            self._skip_line_comment()
            if self.pos <= item_start:
                self.pos = item_start + 1
            return None
        name, name_start, _name_end = tok
        self.skip_trivia()
        if (
            self.pos < self.n
            and self.text[self.pos] == "="
            and (self.pos + 1 >= self.n or self.text[self.pos + 1] != "=")
        ):
            self.pos += 1
            self.skip_trivia()
            value, vstart, vend = self.read_expression()
            return HclAttribute(
                name=name, value=value,
                span=self.span(name_start, vend),
                value_span=self.span(vstart, vend),
            )
        labels = self._read_block_labels(name, item_start)
        if labels is None:
            return None
        header_end = self.pos
        self.pos += 1  # consume '{'
        body_char_start = self.pos
        attrs, children, body_end = self._parse_body(body_char_start)
        # ``body_end`` inclui o ``}`` de fechamento quando presente
        inner_end = (
            body_end - 1
            if body_end > body_char_start and self.text[body_end - 1] == "}"
            else body_end
        )
        return HclBlock(
            type=name, labels=labels,
            attributes=attrs, children=children,
            body=self.text[body_char_start:inner_end],
            body_start=self._byte[body_char_start],
            span=self.span(item_start, body_end),
            header_span=self.span(item_start, header_end),
        )

    def parse(self) -> HclFile:
        """Top-level: mistura de blocos e atributos (tfvars)."""
        blocks: list[HclBlock] = []
        attrs: list[HclAttribute] = []
        while True:
            self.skip_trivia()
            if self.pos >= self.n:
                break
            if self.text[self.pos] == "}":
                self.diag("stray-brace", "'}' without open block", self.pos)
                self.pos += 1
                continue
            item_start = self.pos
            item = self._parse_item()
            if isinstance(item, HclBlock):
                blocks.append(item)
            elif isinstance(item, HclAttribute):
                attrs.append(item)
            if self.pos <= item_start:  # guard anti-loop infinito
                self.pos = item_start + 1
        return HclFile(
            blocks=tuple(blocks), attributes=tuple(attrs),
            diagnostics=tuple(self.diags),
        )


def parse_hcl(text: str) -> HclFile:
    """Parse de texto HCL → ``HclFile``; nunca levanta exceção."""
    try:
        return _Parser(text).parse()
    except Exception as exc:  # última linha de defesa — diagnóstico honesto
        return HclFile(
            diagnostics=(
                HclDiagnostic(
                    code="parse-error",
                    message=f"internal parser failure: {exc!r}",
                ),
            )
        )
