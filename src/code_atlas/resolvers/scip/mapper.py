"""Correlação explícita SCIP ↔ Atlas (lf-0017).

Símbolos SCIP NÃO são ids internos do Atlas — este módulo faz a ponte:

- ``parse_symbol``: string scip → partes (scheme, package, descriptors).
  Gramática oficial (scip.proto):
    ``<scheme> ' ' <manager> ' ' <name> ' ' <version> ' ' <descriptors>``
    ou ``local <id>`` para símbolos confinados ao documento.
- ``symbol_to_qname``: descriptors → qname estilo Atlas
  (``ns.Class.method``), preservando sufixos de descriptor.
- ``ScipCorrelator``: resolve occurrence→(path → símbolo Atlas), usando os
  ``ScopeUniverse``/qnames montados pelo builder — associação por qname,
  nunca por id posicional.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from code_atlas.resolvers.syntactic.resolver import ScopeUniverse

# sufixo de descriptor → separador qname
_SUFFIX_SEP = {
    1: "/",   # Namespace
    2: "#",   # Type
    3: ".",   # Term
    4: ".",   # Method (com disambiguator)
    5: "[]",  # TypeParameter
    6: "()",  # Parameter
    7: ":",   # Meta
    8: ".",   # Local
    9: "!",   # Macro
}


@dataclass(frozen=True)
class Descriptor:
    name: str
    disambiguator: str
    suffix: int  # Descriptor.Suffix


@dataclass(frozen=True)
class ScipSymbol:
    """Símbolo SCIP decomposto — nunca usado como id Atlas direto."""

    scheme: str
    manager: str
    package_name: str
    package_version: str
    descriptors: tuple[Descriptor, ...]
    local_id: str = ""  # preenchido só para 'local N'

    @property
    def is_local(self) -> bool:
        return bool(self.local_id)

    @property
    def is_external(self) -> bool:
        """Símbolo de pacote externo (manager/pacote preenchidos e
        não-locais podem ser locais-do-repo também — externo de fato é
        decidido pelo correlator via presença no universo)."""
        return False


_SYMBOL_RE = re.compile(
    r"^(?P<scheme>\S+)\s+(?P<manager>\S+)\s+(?P<pkg>\S+)\s+"
    r"(?P<ver>\S+)\s+(?P<desc>.+)$"
)
_LOCAL_RE = re.compile(r"^local\s+(?P<id>\S+)$")

# um descriptor termina em: '/', '#', '.', ':', '!', '].', ').', '[]'
_DESC_TOKEN_RE = re.compile(
    r"(?P<name>`(?:[^`]|``)*`|[^\s/.#!:\[\]()`]+|`[^`]*`)"
    r"(?P<disambig>(?:\([^)]*\))?)"
    r"(?P<suffix>/|#|\.|:|!|\]|\))"
)


def parse_symbol(raw: str) -> ScipSymbol | None:
    """String scip → ScipSymbol; None quando malformado."""
    raw = raw.strip()
    if not raw:
        return None
    m = _LOCAL_RE.match(raw)
    if m:
        return ScipSymbol(
            scheme="", manager="", package_name="", package_version="",
            descriptors=(), local_id=m.group("id"),
        )
    m = _SYMBOL_RE.match(raw)
    if not m:
        return None
    descriptors = _parse_descriptors(m.group("desc"))
    if descriptors is None:
        return None
    return ScipSymbol(
        scheme=m.group("scheme"), manager=m.group("manager"),
        package_name=m.group("pkg"), package_version=m.group("ver"),
        descriptors=tuple(descriptors),
    )


def _parse_descriptors(text: str) -> list[Descriptor] | None:
    """Separa a cadeia de descriptors em tokens (name+disambig+suffix)."""
    out: list[Descriptor] = []
    i = 0
    n = len(text)
    while i < n:
        parsed = _read_name(text, i)
        if parsed is None:
            return None
        name, i = parsed
        disambig = ""
        if i < n and text[i] == "(":  # method: name(disambig).
            j = text.find(")", i)
            if j == -1:
                return None
            disambig = text[i + 1 : j]
            i = j  # consome ')' abaixo
        if i >= n:
            return None
        end = _read_suffix(text, i)
        if end is None:
            return None
        suffix, i = end
        if not name:
            return None
        clean = name[1:-1] if name.startswith("`") and name.endswith("`") \
            else name
        out.append(Descriptor(name=clean, disambiguator=disambig,
                              suffix=suffix))
    return out


def _read_name(text: str, i: int) -> tuple[str, int] | None:
    """Lê um name (identifier ou `escaped`) a partir de i → (name, next)."""
    n = len(text)
    if text[i] == "`":  # escaped identifier — `` = escape
        j = i + 1
        while j < n:
            if text[j] == "`":
                if j + 1 < n and text[j + 1] == "`":
                    j += 2
                    continue
                break
            j += 1
        if j >= n:
            return None  # backtick não fechado
        return text[i : j + 1], j + 1
    j = i
    while j < n and text[j] not in "/#.:(![`":
        j += 1
    return text[i:j], j


def _read_suffix(text: str, i: int) -> tuple[int, int] | None:
    """Lê o terminador de descriptor a partir de i → (suffix, next)."""
    ch = text[i]
    if ch == ")":  # fecha method descriptor (pode vir seguido de '.')
        i += 1
        if i < len(text) and text[i] == ".":
            i += 1
        return 4, i
    if ch == "[":
        j = text.find("]", i)
        if j == -1:
            return None
        return 5, j + 1  # ']' termina o descriptor type-param
    suffix = {"/": 1, "#": 2, ".": 3, ":": 7, "!": 9}.get(ch)
    if suffix is None:
        return None
    return suffix, i + 1


def symbol_to_qname(sym: ScipSymbol) -> str:
    """Converte descriptors scip → qname estilo Atlas (pkg.Class.method).

    Namespace e Type/Terms são concatenados com '.'; Method recebe '()';
    TypeParameter/Parameter são sufixados ao termo anterior.
    """
    if sym.is_local:
        return f"local:{sym.local_id}"
    parts: list[str] = []
    for d in sym.descriptors:
        if d.suffix in (5, 6):  # type-param / parameter — suffixo inline
            if parts:
                parts[-1] += f"[{d.name}]" if d.suffix == 5 else f"({d.name})"
            continue
        name = d.name
        if d.suffix == 4:  # method
            name = f"{name}({d.disambiguator})" if d.disambiguator else name
        parts.append(name)
    return ".".join(parts)


def symbol_parts(sym: ScipSymbol) -> tuple[str, tuple[str, ...]]:
    """Decompõe descriptors em (doc_path candidato, nomes de membros).

    Indexadores SCIP usam o path do documento como descriptor Namespace
    (ex.: `` `pkg/util.py`/helper(). ``). Membros = descriptors
    não-namespace, com disambiguator/parens removidos.
    """
    if sym.is_local:
        return "", ()
    ns_parts = [d.name for d in sym.descriptors if d.suffix == 1]
    members = tuple(
        d.name for d in sym.descriptors if d.suffix != 1
    )
    doc = "/".join(ns_parts)
    return doc, members


class ScipCorrelator:
    """Mapa occurrence→nó Atlas dentro de um ``ScopeUniverse``.

    Associação por (path do documento → módulo ctx) + (qname do símbolo →
    ``qname_nid``); nunca assume que o id scip é o id interno.
    """

    def __init__(self, universe: ScopeUniverse) -> None:
        self._u = universe

    def module_for_document(self, relative_path: str) -> str | None:
        """Document scip → nid do módulo Atlas com mesmo path."""
        return self._u.by_path.get(relative_path)

    def node_for_symbol(self, raw: str, path: str) -> str | None:
        """String scip → nid de símbolo no snapshot (ou None).

        Estratégia: (1) doc-path do namespace → ctx do módulo + membro
        final; (2) leaf global como fallback; (3) qname direto. Só
        retorna nid quando a identidade é única.
        """
        sym = parse_symbol(raw)
        if sym is None or sym.is_local:
            return None
        doc, members = symbol_parts(sym)
        hits = self._match(sym, doc, members, path)
        return hits[0] if len(hits) == 1 else None

    def _match(
        self,
        sym: ScipSymbol,
        doc: str,
        members: tuple[str, ...],
        path: str,
    ) -> list[str]:
        if doc:
            ctx = self._u.ctx_for_path(doc)
            if ctx is not None:
                if not members:
                    return [ctx.nid]
                hits = sorted(set(ctx.defines.get(members[-1], [])))
                if hits:
                    return hits
        if members:
            hits = sorted(set(self._u.leaf.get(members[-1], [])))
            if hits:
                return hits
        qname = symbol_to_qname(sym)
        return sorted(set(self._u.qname_nid.get(qname, [])))

    def candidates_for_symbol(self, raw: str) -> tuple[str, ...]:
        sym = parse_symbol(raw)
        if sym is None or sym.is_local:
            return ()
        doc, members = symbol_parts(sym)
        hits = self._match(sym, doc, members, "")
        return tuple(sorted(set(hits)))
