"""Tokenização de identificadores para o índice lexical (lf-0036).

FTS5 via ``sqlite3`` não permite tokenizer customizado (API C-only) —
a segmentação de identificadores é feita em Python na ingestão e na
query: ``processCustomerOrders`` indexa como
``process customer orders`` na coluna ``terms`` (além do termo cru na
coluna ``name``), recuperável por fragmento E por forma exata.
"""

from __future__ import annotations

import re

_SEGMENTS = re.compile(r"[^0-9A-Za-z]+")
_WORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+")


def split_identifier(text: str) -> list[str]:
    """Divide um identificador/caminho em tokens minúsculos.

    snake_case, camelCase, PascalCase, qualified.names, paths,
    dígitos e siglas (``HTTPServer`` → ``http server``).
    """
    out: list[str] = []
    for seg in _SEGMENTS.split(text):
        if not seg:
            continue
        out.extend(m.group(0).lower() for m in _WORD.finditer(seg))
    return out


def index_terms(*parts: str) -> str:
    """Texto da coluna ``terms``: união ordenada dos tokens de todos os
    campos (name + qualified_name + module)."""
    toks: set[str] = set()
    for p in parts:
        toks.update(split_identifier(p))
    return " ".join(sorted(toks))


def fts_escape(term: str) -> str:
    """Escapa um termo para uso dentro de ``"..."`` no MATCH do FTS5."""
    return '"' + term.replace('"', '""') + '"'
