"""Contrato de proveniência + revision safety do índice SCIP (lf-0028).

``project_root`` bater não basta: um índice pode ser de outro repo com
o mesmo path, estar velho (arquivos modificados/removidos depois da
indexação) ou simplesmente incompleto. ``IndexProvenance`` é o modelo
interno — não presume que o .scip contenha todos os campos; o que não
pode ser provado fica explicitamente marcado.

``validation_status``:
- ``current``: todas as verificações passaram.
- ``degraded``: índice utilizável, mas evidência de revisão
  insuficiente — relações derivadas NUNCA são promovidas como
  comprovadas (confidence capado em ``heuristic`` no resolver).
- ``rejected``: incompatível — o resolver não produz resolução
  nenhuma (índice incompatível nunca contamina snapshot READY).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from code_atlas.resolvers.scip.importer import ScipDocument, ScipIndex
from code_atlas.resolvers.scip.validation import project_root_matches

# position_encoding válido no mapper: UTF-32 (3) ou não-declarado (0
# — protobuf enum zero/default); outros encodings tornam spans
# não-confiáveis → degraded (o mapper não re-decodifica).
TRUSTED_ENCODINGS = frozenset({0, 3})

STATUS_CURRENT = "current"
STATUS_DEGRADED = "degraded"
STATUS_REJECTED = "rejected"


@dataclass(frozen=True)
class IndexProvenance:
    """Proveniência de um índice externo — inspecionável e serializável."""

    index_format: str = "scip"
    index_sha256: str = ""          # sha256 dos bytes do arquivo
    producer_name: str = ""         # metadata.tool_info.name
    producer_version: str = ""      # metadata.tool_info.version
    repository_identity: str = ""   # metadata.project_root (URI)
    source_revision: str = ""       # revisão do repo indexado, se provável
    worktree_digest: str = ""       # digest dos arquivos indexados vistos
    indexed_at: str = ""            # mtime do arquivo .scip (evidência host)
    validation_status: str = STATUS_REJECTED
    validation_evidence: tuple[str, ...] = ()

    def summary(self) -> str:
        """Resumo one-line para evidence notes / diagnósticos."""
        return (
            f"scip-provenance status={self.validation_status} "
            f"sha256={self.index_sha256[:12]} "
            f"producer={self.producer_name}@{self.producer_version} "
            f"revision={self.source_revision or 'unknown'}"
        )


@dataclass(frozen=True)
class RevisionAssessment:
    """Resultado do assessment: proveniência + erros + avisos."""

    provenance: IndexProvenance
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def assess_revision(
    index: ScipIndex,
    index_path: Path,
    workspace_root: Path,
    *,
    raw_bytes: bytes | None = None,
) -> RevisionAssessment:
    """Verifica o índice contra o worktree atual.

    Sinais sem executar nada do repo: identidade (``project_root`` ↔
    workspace), staleness, paths, spans vs fim de arquivo,
    ``position_encoding``, completude de metadata, símbolos duplicados.
    """
    if raw_bytes is None:
        try:
            raw_bytes = index_path.read_bytes()
        except OSError as exc:
            return RevisionAssessment(
                IndexProvenance(validation_status=STATUS_REJECTED),
                errors=(f"cannot read index for provenance: {exc}",),
            )
    sha = hashlib.sha256(raw_bytes).hexdigest()
    index_mtime, indexed_at = _index_stamp(index_path)
    base = dict(
        index_sha256=sha,
        producer_name=index.tool_name,
        producer_version=index.tool_version,
        repository_identity=index.project_root,
        indexed_at=indexed_at,
    )
    if not project_root_matches(index, workspace_root):
        return RevisionAssessment(
            IndexProvenance(
                **base,
                validation_status=STATUS_REJECTED,
                validation_evidence=("project_root mismatch",),
            ),
            errors=(
                f"index project_root {index.project_root!r} does not "
                f"identify workspace {workspace_root}",
            ),
        )
    degraded, digest = _check_docs(
        index, Path(workspace_root).resolve(), index_mtime)
    degraded += _check_completeness(index, index_mtime)
    status = STATUS_DEGRADED if degraded else STATUS_CURRENT
    return RevisionAssessment(
        IndexProvenance(
            **base,
            source_revision="",  # scip metadata não prova commit
            worktree_digest=digest,
            validation_status=status,
            validation_evidence=(
                "project_root matches workspace", *degraded),
        ),
        warnings=tuple(degraded)
        + (() if index_mtime else ("index mtime unavailable",)),
    )


def _index_stamp(index_path: Path) -> tuple[float, str]:
    try:
        mtime = index_path.stat().st_mtime
    except OSError:
        return 0.0, ""
    return mtime, datetime.fromtimestamp(
        mtime, tz=UTC).isoformat()


def _check_docs(
    index: ScipIndex, root: Path, index_mtime: float
) -> tuple[list[str], str]:
    """staleness + paths + spans + encoding + símbolos duplicados."""
    degraded: list[str] = []
    defined_in: dict[str, str] = {}
    digest_parts: list[str] = []
    for doc in index.documents:
        p = doc.relative_path
        if "\\" in p or "//" in p or p.startswith("./"):
            degraded.append(f"non-normalized path: {p!r}")
        if doc.position_encoding not in TRUSTED_ENCODINGS:
            degraded.append(f"doc {p}: position_encoding="
                            f"{doc.position_encoding} untrusted")
        for s in doc.symbols:
            prev = defined_in.get(s.symbol)
            if prev is not None and prev != p:
                degraded.append(
                    f"symbol {s.symbol!r} defined in {prev} and {p}")
            else:
                defined_in[s.symbol] = p
        degraded += _check_doc_file(doc, root / p, index_mtime,
                                    digest_parts)
    digest = hashlib.sha256(
        "\n".join(sorted(digest_parts)).encode()).hexdigest()
    return degraded, digest


def _check_doc_file(
    doc: ScipDocument, target: Path, index_mtime: float,
    digest_parts: list[str],
) -> list[str]:
    """staleness e spans de um documento contra o arquivo atual."""
    p = doc.relative_path
    if not target.is_file():
        digest_parts.append(f"{p}:absent")
        return [f"indexed file missing: {p}"]
    try:
        fst = target.stat()
    except OSError:
        return [f"cannot stat {p}"]
    digest_parts.append(f"{p}:{fst.st_size}")
    out: list[str] = []
    if index_mtime and fst.st_mtime > index_mtime:
        out.append(f"file newer than index: {p}")
    if doc.occurrences:
        try:
            n_lines = len(
                target.read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeDecodeError):
            n_lines = -1
        if n_lines >= 0:
            max_line = max(
                (o.range.start_line if o.range else 0)
                for o in doc.occurrences)
            if max_line > n_lines:
                out.append(f"doc {p}: occurrence span line {max_line} "
                           f"beyond file end {n_lines}")
    return out


def _check_completeness(index: ScipIndex, index_mtime: float) -> list[str]:
    """índice válido ≠ índice completo."""
    out: list[str] = []
    if not index.tool_version:
        out.append("metadata.tool_info.version missing")
    if not index.documents:
        out.append("index has no documents")
    if not index_mtime:
        out.append("index timestamp unknown")
    return out
