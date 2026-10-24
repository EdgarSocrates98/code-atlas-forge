"""Validação de índices SCIP contra o workspace Atlas (lf-0017).

Impede associações falsas: um índice produzido para outro repositório ou
outra revisão NUNCA é correlacionado silenciosamente — a rejeição é
explícita e diagnosticada.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from code_atlas.resolvers.scip.importer import ScipIndex


def _pct_decode(s: str) -> str:
    """Percent-decode mínimo para paths de file:// (stdlib pura — urllib
    é banido pelo audit de rede do projeto)."""
    out = bytearray()
    i = 0
    raw = s.encode("utf-8", errors="replace")
    while i < len(raw):
        if raw[i:i + 1] == b"%" and i + 2 < len(raw) + 1 and \
                i + 2 < len(raw) + 1:
            try:
                out.append(int(raw[i + 1:i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out.append(raw[i])
        i += 1
    return out.decode("utf-8", errors="replace")

# limites defensivos — índices externos são untrusted input
MAX_INDEX_BYTES = 256 * 1024 * 1024      # 256 MiB
MAX_DOCUMENTS = 200_000
MAX_OCCURRENCES_PER_DOC = 500_000


@dataclass(frozen=True)
class ScipValidationResult:
    ok: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def validate_index(index: ScipIndex, raw_size: int) -> ScipValidationResult:
    """Sanidade estrutural do índice decodificado."""
    errors: list[str] = []
    warnings: list[str] = []
    if raw_size > MAX_INDEX_BYTES:
        errors.append(f"index too large: {raw_size} > {MAX_INDEX_BYTES}")
    if len(index.documents) > MAX_DOCUMENTS:
        errors.append(
            f"too many documents: {len(index.documents)} > {MAX_DOCUMENTS}")
    seen_paths: set[str] = set()
    for doc in index.documents:
        p = doc.relative_path
        if not p or p.startswith("/") or ".." in p.split("/"):
            errors.append(f"bad document path: {p!r}")
        if p in seen_paths:
            warnings.append(f"duplicate document path: {p}")
        seen_paths.add(p)
        if len(doc.occurrences) > MAX_OCCURRENCES_PER_DOC:
            errors.append(f"doc {p}: occurrences > {MAX_OCCURRENCES_PER_DOC}")
        for o in doc.occurrences:
            if not o.symbol:
                warnings.append(f"doc {p}: occurrence with empty symbol")
                break
    if not index.tool_name:
        warnings.append("metadata.tool_info.name empty")
    return ScipValidationResult(ok=not errors, errors=tuple(errors),
                                warnings=tuple(warnings))


def project_root_matches(index: ScipIndex, workspace_root: Path) -> bool:
    """O ``project_root`` do índice precisa apontar para a raiz indexada.

    ``project_root`` é uma URI (file://...) — comparamos o caminho
    decodificado com o caminho real do workspace.
    """
    if not index.project_root:
        return False
    uri = index.project_root
    if uri.startswith("file://"):
        path = _pct_decode(uri[len("file://"):])
        # strip host vazio/localhost (file:///x → /x; file://host/x)
        if not path.startswith("/"):
            path = "/" + path.split("/", 1)[-1]
    else:
        path = uri
    try:
        return Path(path).resolve() == Path(workspace_root).resolve()
    except OSError:
        return False
