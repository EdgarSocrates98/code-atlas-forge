"""Helpers compartilhados do adapter ``domains.api`` (lf-0114).

Utilitários puros de texto/sintaxe: extração de lista de argumentos a
partir de uma posição de call-site (span do callee vindo do Tree-sitter
via ``ParseResult``), classificação de literais de string (Python/TS),
split de URL **sem** ``urllib`` (barrado fora de ``code_atlas.api`` pela
auditoria de rede lf-0086), nomes de módulo derivados de path e spans de
linha. Nenhuma I/O, nenhuma importação de ``tree_sitter``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from code_atlas.parsers.contracts import SourceSpan

_ARGS_MAX = 16_384
_ARGS_MAX_ITEMS = 64

PY_MODULE_SUFFIXES = (".py", ".pyi")
TS_MODULE_SUFFIXES = (".d.ts", ".tsx", ".ts", ".jsx", ".mjs", ".cjs", ".js")


# -- módulos / nomes ----------------------------------------------------


def module_name(path: str, suffixes: tuple[str, ...]) -> str:
    """``app/routers.py`` → ``app.routers``; ``web/x.d.ts`` → ``web.x``."""
    p = path
    for suf in suffixes:
        if p.endswith(suf):
            p = p[: -len(suf)]
            break
    parts = [seg for seg in p.split("/") if seg]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or path


def slugify(text: str) -> str:
    """Slug determinístico para nomes de serviço (``Pet Store`` → ``pet-store``)."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:64]


def join_paths(prefix: str, path: str) -> str:
    """Junta prefixo + path de endpoint sem duplicar ``/``."""
    if not prefix:
        return path or "/"
    if not path:
        return prefix
    return prefix.rstrip("/") + "/" + path.lstrip("/")


# -- offsets / spans ------------------------------------------------------


def line_offsets(text: str) -> list[int]:
    """Offset de **char** do início de cada linha (1-based pelo índice)."""
    offs = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            offs.append(i + 1)
    return offs


def byte_offsets(text: str) -> list[int]:
    """Offset de **byte UTF-8** do início de cada linha (alinhado a SourceSpan)."""
    offs = [0]
    total = 0
    for raw in text.splitlines(keepends=True):
        total += len(raw.encode("utf-8"))
        offs.append(total)
    return offs


def line_span(text: str, byte_offs: list[int], lineno: int) -> SourceSpan | None:
    """``SourceSpan`` da linha inteira (sem o ``\\n``); None se fora de faixa."""
    if lineno < 1 or lineno > len(byte_offs):
        return None
    start = byte_offs[lineno - 1]
    lines = text.splitlines()
    if lineno > len(lines):
        return None
    raw = lines[lineno - 1]
    end = start + len(raw.encode("utf-8"))
    ncols = len(raw.encode("utf-8"))
    return SourceSpan(
        start_byte=start, end_byte=end,
        start_line=lineno, start_col=0,
        end_line=lineno, end_col=ncols,
    )


def byte_to_char(text: str, byte_off: int) -> int:
    """Converte offset de byte UTF-8 em offset de char ``str``.

    Necessário porque spans do Tree-sitter são byte offsets e o fatiamento
    de argumentos opera sobre ``str`` decodificado.
    """
    return len(text.encode("utf-8")[:byte_off].decode("utf-8", "replace"))


# -- argumentos de call-site -------------------------------------------------


def _quote_step(ch: str, quote: str) -> tuple[bool, str]:
    """Passo de scan dentro de aspas → ``(escapou?, quote_atual)``."""
    if ch == "\\":
        return True, quote
    if ch == quote:
        return False, ""
    return False, quote


def scan_args(text: str, open_paren: int) -> str | None:
    """Conteúdo interno de ``(...)`` começando em ``open_paren``.

    Balanceado e ciente de aspas (``'`` ``"`` `` ` ``) com escapes —
    não é um parser de expressões: cobre o caso comum de call-sites de
    rota/cliente. Limitado a ``_ARGS_MAX`` chars; None se desbalanceado
    ou além do limite.
    """
    if open_paren >= len(text) or text[open_paren] != "(":
        return None
    depth = 0
    quote = ""
    esc = False
    i = open_paren
    while i < len(text) and i - open_paren <= _ARGS_MAX:
        ch = text[i]
        if esc:
            esc = False
        elif quote:
            esc, quote = _quote_step(ch, quote)
        elif ch in "'\"`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1:i]
        i += 1
    return None


def args_after(text: str, pos: int) -> str | None:
    """Argumentos logo após ``pos`` (fim do callee): pula espaço, exige ``(``."""
    i = pos
    while i < len(text) and text[i] in " \t":
        i += 1
    if i < len(text) and text[i] == "(":
        return scan_args(text, i)
    return None


def iter_args(args: str) -> Iterator[str]:
    """Itera argumentos top-level — ciente de aspas e profundidade."""
    depth = 0
    quote = ""
    esc = False
    emitted = False
    cur: list[str] = []
    for ch in args:
        if esc:
            cur.append(ch)
            esc = False
            continue
        if quote:
            cur.append(ch)
            esc, quote = _quote_step(ch, quote)
            continue
        if ch in "'\"`":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            emitted = True
            yield "".join(cur).strip()
            cur = []
            continue
        cur.append(ch)
    tail = "".join(cur).strip()
    if tail or emitted:
        yield tail


def split_args(args: str) -> list[str]:
    """Split top-level por vírgula — limitado a ``_ARGS_MAX_ITEMS``."""
    out: list[str] = []
    for a in iter_args(args):
        out.append(a)
        if len(out) >= _ARGS_MAX_ITEMS:
            break
    return out


def kwarg(args: list[str], name: str) -> str | None:
    """Valor textual de ``name=...`` numa lista de argumentos."""
    prefix = f"{name}="
    for a in args:
        a = a.strip()
        if a.startswith(prefix):
            return a[len(prefix):].strip()
    return None


# -- literais de string -----------------------------------------------------


@dataclass(frozen=True)
class StrLiteral:
    """Literal de string classificado."""

    value: str
    dynamic: bool  # f-string com ``{}`` ou template ``${}`` — nunca resolve


_PY_STR = re.compile(r"^([rRbBfFuU]{0,3})('''|\"\"\"|'|\")", re.S)


def _quoted_end(tok: str, start: int, quote: str) -> int:
    """Índice do primeiro ``quote`` não-escapado >= start; -1 se ausente."""
    i = start
    while i < len(tok):
        if tok[i] == "\\":
            i += 2
            continue
        if tok.startswith(quote, i):
            return i
        i += 1
    return -1


def parse_str_literal(tok: str, *, lang: str) -> StrLiteral | None:
    """Classifica um token como literal de string.

    ``lang`` = ``"py"`` (prefixos r/b/f/u, aspas simples/duplas/triplas)
    ou ``"ts"`` (aspas simples/duplas/backtick). Devolve ``StrLiteral`` —
    ``dynamic=True`` quando o literal carrega interpolação (``f".."`` /
    template ``${}``); None quando o token não é literal puro —
    identificador, expressão, concatenação (``"a" + "b"`` não é literal
    estático e nunca deve ser resolvido como URL).
    """
    tok = tok.strip()
    if not tok:
        return None
    if lang == "py":
        m = _PY_STR.match(tok)
        if not m:
            return None
        prefix, quote = m.group(1).lower(), m.group(2)
        if "b" in prefix:
            return None  # bytes literal — não é URL
        close = _quoted_end(tok, m.end(), quote)
        if close < 0 or tok[close + len(quote):].strip():
            return None  # sem fechamento, ou concatenação após o literal
        body = tok[m.end():close]
        dynamic = "f" in prefix and ("{" in body)
        return StrLiteral(value=body, dynamic=dynamic)
    # ts
    q = tok[0]
    if q in ("'", '"', "`"):
        close = _quoted_end(tok, 1, q)
        if close < 0 or tok[close + 1:].strip():
            return None
        body = tok[1:close]
        return StrLiteral(value=body, dynamic=(q == "`" and "${" in body))
    return None


# -- URLs (sem urllib — fronteira lf-0086) ----------------------------------


_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.-]*)://(.*)$")


def url_path(url: str) -> str:
    """Componente de path de uma URL absoluta/relativa.

    Split mínimo por string — ``urllib.parse`` é proibido fora de
    ``code_atlas.api`` (auditoria lf-0086). URLs com ``{var}`` (templates
    de servidor OpenAPI) passam intactas.
    """
    s = url.strip()
    m = _SCHEME_RE.match(s)
    if m:
        rest = m.group(2)
        slash = rest.find("/")
        path = rest[slash:] if slash >= 0 else "/"
    else:
        path = s
    for sep in ("?", "#"):
        cut = path.find(sep)
        if cut >= 0:
            path = path[:cut]
    return path or "/"


def is_absolute_url(url: str) -> bool:
    """True para ``scheme://`` absoluta (http/https/ws)."""
    m = _SCHEME_RE.match(url.strip())
    return bool(m and m.group(1).lower() in ("http", "https", "ws", "wss"))
