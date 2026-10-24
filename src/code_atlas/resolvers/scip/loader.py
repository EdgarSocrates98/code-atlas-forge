"""Carregamento opt-in de índices SCIP do disco (lf-0017).

Política do indexer externo:
- Entrada é **somente um caminho de arquivo** — o Atlas nunca executa
  o indexer nem qualquer comando.
- Cap de tamanho configurável (default ``MAX_INDEX_BYTES``).
- Conteúdo é tratado como untrusted: decodificação via wire-reader
  próprio + validação estrutural + binding de revisão.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from code_atlas.resolvers.scip.decoder import DecodeError
from code_atlas.resolvers.scip.importer import ScipIndex, parse_index
from code_atlas.resolvers.scip.validation import (
    MAX_INDEX_BYTES,
    project_root_matches,
    validate_index,
)


@dataclass(frozen=True)
class LoadOutcome:
    """Resultado honesto do load — nunca levanta por conteúdo ruim."""

    index: ScipIndex | None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.index is not None and not self.errors


def load_index(
    path: Path,
    *,
    workspace_root: Path | None = None,
    max_bytes: int = MAX_INDEX_BYTES,
    allow_revision_mismatch: bool = False,
) -> LoadOutcome:
    """Lê e valida um arquivo ``.scip``.

    - Arquivo inexistente/ilegível, oversize ou bytes malformados →
      ``errors`` populadas (nunca raise para input externo).
    - ``workspace_root`` informado ativa o binding de revisão:
      ``project_root`` divergente rejeita a associação salvo
      ``allow_revision_mismatch=True`` (opt-in explícito do usuário).
    """
    try:
        size = path.stat().st_size
    except OSError as exc:
        return LoadOutcome(None, errors=(f"cannot stat {path}: {exc}",))
    if size > max_bytes:
        return LoadOutcome(
            None, errors=(f"index too large: {size} > {max_bytes}",))
    try:
        data = path.read_bytes()
    except OSError as exc:
        return LoadOutcome(None, errors=(f"cannot read {path}: {exc}",))
    try:
        index = parse_index(data)
    except DecodeError as exc:
        return LoadOutcome(None, errors=(f"decode error: {exc}",))

    res = validate_index(index, size)
    if not res.ok:
        return LoadOutcome(None, errors=res.errors, warnings=res.warnings)

    errors: list[str] = []
    warnings = list(res.warnings)
    if workspace_root is not None and not project_root_matches(
        index, workspace_root
    ):
        msg = (
            f"project_root {index.project_root!r} does not match "
            f"workspace {workspace_root}"
        )
        if allow_revision_mismatch:
            warnings.append(f"{msg} (allowed by flag)")
        else:
            errors.append(msg)
    return LoadOutcome(
        index if not errors else None,
        errors=tuple(errors),
        warnings=tuple(warnings),
    )
