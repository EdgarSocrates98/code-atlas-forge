"""Parser YAML de subconjunto delimitado — zero dependências (lf-0114).

O repositório proíbe PyYAML (deps de runtime = pilha Tree-sitter, ADR).
Este parser cobre apenas o subconjunto usado por specs OpenAPI/Swagger e
futuros artefatos CI/CD (lf-0116):

- mapeamentos por indentação (``key: value`` / ``key:`` + bloco aninhado);
- sequências (``- item``), incluindo itens-mapa inline (``- k: v``);
- escalares: aspas simples/duplas, inteiros, floats, ``true``/``false``,
  ``null``/``~``;
- flow single-line ``[a, b]`` / ``{k: v}`` (profundidade limitada);
- escalares de bloco ``|``/``>`` com chomping ``-``/``+``;
- comentários ``#`` e marcadores de documento ``---``/``...`` (primeiro
  documento apenas — multi-documento vira diagnóstico ``info``).

Fora do subconjunto — sempre diagnosticado, nunca silencioso:

- âncoras ``&x``, aliases ``*x``, merge keys ``<<:``, tags ``!``/``!!``;
- escalares plain multi-linha e flow multi-linha;
- indentação com TAB; sequência ao mesmo nível da chave (``key:`` +
  ``- item`` sem indentação extra) — estilo raro fora de specs;
- chaves complexas ``? key``.

O parser nunca levanta exceção por conteúdo ruim: problemas viram
``YamlIssue`` e o parse continua em melhor esforço (extração parcial
honesta — spec lf-0114).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

YamlScalar = str | int | float | bool | None
YamlValue = YamlScalar | list["YamlValue"] | dict[str, "YamlValue"]

_MAX_DEPTH = 40
_MAX_FLOW_LEN = 16_384
_BLOCK_HEADER = re.compile(r"^[|>][+-]?\d*$")
_INT = re.compile(r"^[-+]?\d+$")
_FLOAT = re.compile(r"^[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?$")


@dataclass(frozen=True)
class YamlIssue:
    """Problema de parse YAML — diagnóstico honesto, nunca exceção."""

    code: str        # ex.: "yaml-anchor-unsupported"
    severity: str    # "info" | "warning" | "error"
    message: str
    line: int = 0    # 1-based; 0 = não localizado


@dataclass(frozen=True)
class YamlDocument:
    """Resultado do parse: valor + issues + mapa key-path → linha.

    ``key_lines`` indexa a linha (1-based) onde cada chave de mapeamento
    foi declarada. O caminho é a tupla de chaves normalizadas (strings);
    itens de sequência aparecem como índice string (``"0"``, ``"1"``…).
    """

    value: YamlValue
    issues: tuple[YamlIssue, ...]
    key_lines: Mapping[tuple[str, ...], int]


@dataclass
class _Line:
    no: int           # 1-based
    indent: int
    content: str      # comentário removido, rstripped (sem indent)
    ignored: bool     # blank/comment-only/consumida por block scalar


class _FlowError(Exception):
    """Falha interna de parse flow — convertida em YamlIssue."""


class _Parser:
    def __init__(self, text: str) -> None:
        self.raw = text.splitlines()
        self.lines: list[_Line] = []
        self.issues: list[YamlIssue] = []
        self.key_lines: dict[tuple[str, ...], int] = {}
        self._tab_reported = False
        self._build_lines()

    # -- infra ---------------------------------------------------------

    def _issue(self, code: str, severity: str, message: str, line: int = 0) -> None:
        self.issues.append(YamlIssue(code, severity, message, line))

    def _strip_comment(self, s: str) -> str:
        """Corta ``#`` iniciando comentário (pos 0 ou após espaço), fora de aspas."""
        in_s = in_d = False
        j = 0
        while j < len(s):
            ch = s[j]
            if in_d:
                if ch == "\\":
                    j += 2  # próximo char é escapado
                    continue
                if ch == '"':
                    in_d = False
            elif in_s:
                if ch == "'":
                    in_s = False
            elif ch == '"':
                in_d = True
            elif ch == "'":
                in_s = True
            elif ch == "#" and (j == 0 or s[j - 1] in " \t"):
                return s[:j]
            j += 1
        return s

    def _build_lines(self) -> None:
        for no, raw in enumerate(self.raw, 1):
            ws = raw[: len(raw) - len(raw.lstrip())]
            if "\t" in ws and not self._tab_reported:
                self._issue(
                    "tab-indent", "error",
                    "TAB in leading whitespace — line ignored", no,
                )
                self._tab_reported = True
            if "\t" in ws:
                self.lines.append(_Line(no, 0, "", ignored=True))
                continue
            indent = len(ws)
            content = self._strip_comment(raw[indent:]).rstrip()
            self.lines.append(_Line(no, indent, content, ignored=not content))

    @property
    def _n(self) -> int:
        return len(self.lines)

    def _eff(self, i: int) -> int:
        """Próximo índice de linha efetiva (não-ignorada) >= i."""
        while i < self._n and self.lines[i].ignored:
            i += 1
        return i

    @staticmethod
    def _is_doc_marker(content: str) -> bool:
        return content == "---" or content == "..." or content.startswith("--- ")

    @staticmethod
    def _is_seq_item(content: str) -> bool:
        return content == "-" or content.startswith("- ")

    # -- entrada ---------------------------------------------------------

    def parse(self) -> YamlDocument:
        i = self._eff(0)
        if i < self._n and self.lines[i].content == "---":
            i = self._eff(i + 1)
        if i >= self._n:
            value: YamlValue = None
        else:
            value, i = self._parse_node(i, self.lines[i].indent, (), 0)
        j = self._eff(i)
        if j < self._n:
            if self._is_doc_marker(self.lines[j].content):
                self._issue(
                    "multi-document", "info",
                    "extra YAML documents ignored — first document parsed",
                    self.lines[j].no,
                )
            else:
                self._issue(
                    "trailing-content", "warning",
                    "unparsed trailing content", self.lines[j].no,
                )
        return YamlDocument(
            value=value, issues=tuple(self.issues),
            key_lines=dict(self.key_lines),
        )

    # -- dispatch --------------------------------------------------------

    def _parse_node(
        self, i: int, indent: int, path: tuple[str, ...], depth: int
    ) -> tuple[YamlValue, int]:
        if depth > _MAX_DEPTH:
            self._issue("max-depth", "error", "nesting depth exceeded", 0)
            return None, i
        i = self._eff(i)
        if i >= self._n:
            return None, i
        li = self.lines[i]
        if li.indent < indent or self._is_doc_marker(li.content):
            return None, i
        if self._is_seq_item(li.content):
            return self._parse_seq(i, li.indent, path, depth)
        if self._split_key(li.content) is not None:
            return self._parse_map(i, li.indent, path, depth)
        # escalar de linha única
        val = self._scalar_or_tagged(li.content, li.no)
        j = self._eff(i + 1)
        if j < self._n and self.lines[j].indent > li.indent:
            self._issue(
                "multiline-plain-scalar", "warning",
                "continuation of plain scalar ignored",
                self.lines[j].no,
            )
            while j < self._n and self.lines[j].indent > li.indent:
                self.lines[j].ignored = True
                j = self._eff(j + 1)
            return val, j
        return val, i + 1

    # -- mapeamento ------------------------------------------------------

    @staticmethod
    def _is_key_sep(content: str, j: int) -> bool:
        return content[j] == ":" and (
            j + 1 == len(content) or content[j + 1] in " \t"
        )

    def _split_key(self, content: str) -> tuple[str, str] | None:
        """``key: rest`` no primeiro ``:`` seguido de espaço/EOL, depth 0."""
        quote = ""
        esc = False
        depth = 0
        for j, ch in enumerate(content):
            if esc:
                esc = False
                continue
            if quote:
                esc, quote = _quote_step(ch, quote)
                continue
            if ch in "'\"":
                quote = ch
            elif ch in "[{":
                depth += 1
            elif ch in "]}":
                depth = max(0, depth - 1)
            elif self._is_key_sep(content, j):
                return content[:j].strip(), content[j + 1:].strip()
        return None

    def _norm_key(self, raw: str, line: int) -> str:
        if raw.startswith("? "):
            self._issue(
                "complex-key-unsupported", "warning",
                "explicit complex key unsupported", line,
            )
            raw = raw[2:].strip()
        if raw[:1] in ("&", "!"):
            self._issue(
                "yaml-tag-unsupported", "warning",
                f"key tag/anchor ignored: {raw.split()[0]!r}", line,
            )
            parts = raw.split(None, 1)
            raw = parts[1] if len(parts) > 1 else ""
        if raw[:1] in ("'", '"'):
            val, _ = self._quoted(raw, line)
            return val if val else raw.strip("'\"")
        return raw.strip()

    def _parse_map(
        self, i: int, indent: int, path: tuple[str, ...], depth: int
    ) -> tuple[dict[str, YamlValue], int]:
        out: dict[str, YamlValue] = {}
        while True:
            i = self._eff(i)
            if i >= self._n:
                break
            li = self.lines[i]
            if self._is_doc_marker(li.content):
                break
            if li.indent < indent:
                break
            if li.indent > indent:
                self._issue(
                    "unexpected-indent", "warning",
                    "orphan indented line skipped", li.no,
                )
                i += 1
                continue
            if self._is_seq_item(li.content):
                break  # volta ao nível da sequência
            kv = self._split_key(li.content)
            if kv is None:
                self._issue(
                    "not-a-mapping", "error",
                    f"line is not a mapping entry: {li.content[:60]!r}", li.no,
                )
                i += 1
                continue
            key = self._norm_key(kv[0], li.no)
            self.key_lines.setdefault((*path, key), li.no)
            if key == "<<":
                self._issue(
                    "yaml-merge-key-unsupported", "warning",
                    "merge key '<<' parsed as literal key", li.no,
                )
            if key in out:
                self._issue(
                    "duplicate-key", "warning",
                    f"duplicate key {key!r} — first value kept", li.no,
                )
            value, i = self._value(kv[1], i, li, (*path, key), depth)
            out.setdefault(key, value)
        return out, i

    # -- sequência ---------------------------------------------------------

    def _parse_seq(
        self, i: int, indent: int, path: tuple[str, ...], depth: int
    ) -> tuple[list[YamlValue], int]:
        items: list[YamlValue] = []
        idx = 0
        while True:
            i = self._eff(i)
            if i >= self._n:
                break
            li = self.lines[i]
            if self._is_doc_marker(li.content):
                break
            if li.indent < indent:
                break
            if li.indent > indent:
                self._issue(
                    "unexpected-indent", "warning",
                    "orphan indented line skipped", li.no,
                )
                i += 1
                continue
            if not self._is_seq_item(li.content):
                break
            after = li.content[1:]
            stripped = after.lstrip(" ")
            item_col = li.indent + 1 + (len(after) - len(stripped))
            key_path = (*path, str(idx))
            if not stripped:
                j = self._eff(i + 1)
                if (
                    j < self._n
                    and self.lines[j].indent > li.indent
                    and not self._is_doc_marker(self.lines[j].content)
                ):
                    v, i = self._parse_node(
                        j, self.lines[j].indent, key_path, depth + 1
                    )
                else:
                    v, i = None, i + 1
                items.append(v)
            elif self._split_key(stripped) is not None:
                # item-mapa inline: reescreve a linha como entrada de mapa
                # em ``item_col`` e delega ao parser de mapa.
                li.indent = item_col
                li.content = stripped
                v, i = self._parse_map(i, item_col, key_path, depth + 1)
                items.append(v)
            else:
                v, i = self._value(stripped, i, li, key_path, depth)
                items.append(v)
            idx += 1
        return items, i

    # -- valores -----------------------------------------------------------

    def _value(
        self, rest: str, i: int, li: _Line, path: tuple[str, ...], depth: int
    ) -> tuple[YamlValue, int]:
        """Valor de um par ``key: rest`` ou item ``- rest``."""
        if not rest:
            j = self._eff(i + 1)
            if (
                j < self._n
                and self.lines[j].indent > li.indent
                and not self._is_doc_marker(self.lines[j].content)
            ):
                return self._parse_node(
                    j, self.lines[j].indent, path, depth + 1
                )
            return None, i + 1
        if _BLOCK_HEADER.match(rest):
            return self._block_scalar(i, li, rest)
        if rest[0] in "[{":
            return self._flow(rest, li.no), i + 1
        return self._scalar_or_tagged(rest, li.no), i + 1

    def _scalar_or_tagged(self, s: str, line: int) -> YamlValue:
        while s[:1] in ("&", "!"):
            tok, _, rem = s.partition(" ")
            self._issue(
                "yaml-anchor-unsupported" if s[0] == "&"
                else "yaml-tag-unsupported",
                "warning",
                f"{tok!r} ignored — value kept without anchor/tag semantics",
                line,
            )
            s = rem.strip()
            if not s:
                return None
        if s.startswith("*"):
            self._issue(
                "yaml-alias-unsupported", "warning",
                f"alias {s.split()[0]!r} unresolved — value null", line,
            )
            return None
        if s[:1] in "[{":
            return self._flow(s, line)
        if s[:1] in ("|", ">"):
            # header de block scalar fora de posição (ex.: após `-` inline)
            self._issue(
                "misplaced-block-scalar", "warning",
                "block scalar header in unexpected position", line,
            )
            return None
        if s[:1] in ("'", '"'):
            val, end = self._quoted(s, line)
            if s[end:].strip():
                self._issue(
                    "trailing-junk", "warning",
                    f"trailing content after quoted scalar: {s[end:][:40]!r}",
                    line,
                )
            return val
        return self._plain(s)

    def _quoted(self, s: str, line: int) -> tuple[str, int]:
        """Escalar entre aspas; devolve (valor, índice pós-fechamento)."""
        if s[0] == "'":
            return self._single_quoted(s, line)
        return self._double_quoted(s, line)

    def _single_quoted(self, s: str, line: int) -> tuple[str, int]:
        out: list[str] = []
        j = 1
        while j < len(s):
            if s[j] == "'":
                if j + 1 < len(s) and s[j + 1] == "'":
                    out.append("'")
                    j += 2
                    continue
                return "".join(out), j + 1
            out.append(s[j])
            j += 1
        self._issue(
            "unterminated-string", "error",
            "unterminated single-quoted scalar", line,
        )
        return "".join(out), len(s)

    def _dq_escape(self, s: str, j: int) -> tuple[str, int]:
        """Escape ``\\x``/``\\u``/simples em posição j — (char, new j)."""
        esc = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\", "0": "\0"}
        nxt = s[j + 1]
        for mark, size in (("x", 4), ("u", 6)):
            if nxt == mark and j + size - 1 < len(s):
                try:
                    return chr(int(s[j + 2:j + size], 16)), j + size
                except ValueError:
                    break
        return esc.get(nxt, nxt), j + 2

    def _double_quoted(self, s: str, line: int) -> tuple[str, int]:
        out: list[str] = []
        j = 1
        while j < len(s):
            ch = s[j]
            if ch == "\\" and j + 1 < len(s):
                piece, j = self._dq_escape(s, j)
                out.append(piece)
                continue
            if ch == '"':
                return "".join(out), j + 1
            out.append(ch)
            j += 1
        self._issue(
            "unterminated-string", "error",
            "unterminated double-quoted scalar", line,
        )
        return "".join(out), len(s)

    def _plain(self, s: str) -> YamlScalar:
        v = s.strip()
        low = v.lower()
        if low in ("", "~", "null"):
            return None
        if low == "true":
            return True
        if low == "false":
            return False
        if _INT.match(v):
            try:
                return int(v)
            except ValueError:
                return v
        if _FLOAT.match(v):
            try:
                return float(v)
            except ValueError:
                return v
        return v

    # -- block scalar -----------------------------------------------------

    def _block_scalar(
        self, i: int, li: _Line, header: str
    ) -> tuple[str, int]:
        chunk, j = self._collect_scalar_lines(i, li.indent)
        while chunk and chunk[-1] == "":
            chunk.pop()
        value = _fold_lines(chunk) if header[0] == ">" else "\n".join(chunk)
        if "-" not in header and value:
            value += "\n"
        return value, j

    def _collect_scalar_lines(
        self, i: int, parent_indent: int
    ) -> tuple[list[str], int]:
        """Linhas do block scalar (indent > parent); marca-as ignoradas."""
        j = i + 1
        block_indent: int | None = None
        chunk: list[str] = []
        while j < self._n:
            raw = self.raw[j]
            if not raw.strip():
                chunk.append("")
                j += 1
                continue
            ind = len(raw) - len(raw.lstrip(" "))
            if ind <= parent_indent:
                break
            if block_indent is None:
                block_indent = ind
            if ind < block_indent:
                break
            chunk.append(raw[block_indent:])
            self.lines[j].ignored = True
            j += 1
        return chunk, j

    # -- flow --------------------------------------------------------------

    def _flow(self, s: str, line: int) -> YamlValue:
        if len(s) > _MAX_FLOW_LEN:
            self._issue(
                "flow-too-large", "error",
                f"flow value exceeds {_MAX_FLOW_LEN} chars", line,
            )
            return None
        try:
            val, pos = self._flow_at(s, 0, 0, line)
        except _FlowError as exc:
            self._issue("flow-parse-error", "error", str(exc), line)
            return None
        if s[pos:].strip():
            self._issue(
                "trailing-junk", "warning",
                f"trailing content after flow value: {s[pos:][:40]!r}", line,
            )
        return val

    def _flow_at(
        self, s: str, i: int, depth: int, line: int
    ) -> tuple[YamlValue, int]:
        if depth > 8:
            raise _FlowError("flow nesting depth exceeded")
        i = self._flow_ws(s, i)
        if i >= len(s):
            raise _FlowError("unterminated flow value")
        ch = s[i]
        if ch == "[":
            return self._flow_seq(s, i + 1, depth, line)
        if ch == "{":
            return self._flow_map(s, i + 1, depth, line)
        if ch in ("'", '"'):
            val, used = self._quoted(s[i:], line)
            return val, i + used
        if ch == "*":
            self._issue(
                "yaml-alias-unsupported", "warning",
                "alias in flow position — value null", line,
            )
            i += 1
            while i < len(s) and s[i] not in ",]}":
                i += 1
            return None, i
        # escalar plain em flow: até , ] } no mesmo nível
        j = i
        while j < len(s) and s[j] not in ",]}":
            j += 1
        return self._plain(s[i:j]), j

    @staticmethod
    def _flow_ws(s: str, i: int) -> int:
        while i < len(s) and s[i] in " \t":
            i += 1
        return i

    def _flow_seq(
        self, s: str, i: int, depth: int, line: int
    ) -> tuple[list[YamlValue], int]:
        out: list[YamlValue] = []
        i = self._flow_ws(s, i)
        if i < len(s) and s[i] == "]":
            return out, i + 1
        while True:
            v, i = self._flow_at(s, i, depth + 1, line)
            out.append(v)
            i = self._flow_ws(s, i)
            if i >= len(s):
                raise _FlowError("unterminated flow sequence")
            if s[i] == "]":
                return out, i + 1
            if s[i] != ",":
                raise _FlowError(f"expected ',' in flow seq, got {s[i]!r}")
            i = self._flow_ws(s, i + 1)
            if i < len(s) and s[i] == "]":
                return out, i + 1

    def _flow_map(
        self, s: str, i: int, depth: int, line: int
    ) -> tuple[dict[str, YamlValue], int]:
        out: dict[str, YamlValue] = {}
        i = self._flow_ws(s, i)
        if i < len(s) and s[i] == "}":
            return out, i + 1
        while True:
            i = self._flow_ws(s, i)
            if i < len(s) and s[i] in ("'", '"'):
                key_v, used = self._quoted(s[i:], line)
                key = str(key_v)
                i += used
            else:
                j = i
                while j < len(s) and s[j] not in ":,}":
                    j += 1
                key = s[i:j].strip()
                i = j
            i = self._flow_ws(s, i)
            if i >= len(s) or s[i] != ":":
                raise _FlowError("expected ':' in flow map")
            v, i = self._flow_at(s, i + 1, depth + 1, line)
            out[key] = v
            i = self._flow_ws(s, i)
            if i >= len(s):
                raise _FlowError("unterminated flow map")
            if s[i] == "}":
                return out, i + 1
            if s[i] != ",":
                raise _FlowError(f"expected ',' in flow map, got {s[i]!r}")
            i = self._flow_ws(s, i + 1)
            if i < len(s) and s[i] == "}":
                return out, i + 1


def _quote_step(ch: str, quote: str) -> tuple[bool, str]:
    """Passo de scan dentro de aspas → ``(escapou?, quote_atual)``."""
    if ch == "\\":
        return True, quote
    if ch == quote:
        return False, ""
    return False, quote


def _fold_lines(chunk: list[str]) -> str:
    """Folding ``>``: linhas consecutivas viram frase, blanks separam."""
    paras: list[str] = []
    cur: list[str] = []
    for line in chunk:
        if line == "":
            if cur:
                paras.append(" ".join(cur))
                cur = []
            paras.append("")
        else:
            cur.append(line.strip())
    if cur:
        paras.append(" ".join(cur))
    return "\n".join(paras)


def parse_yaml_subset(text: str) -> YamlDocument:
    """Parseia o subconjunto YAML delimitado; nunca levanta."""
    return _Parser(text).parse()
