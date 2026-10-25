"""Driver de extração por request do domínio ``pyspark`` (lf-0112).

Ordem determinística: artefatos ordenados por ``path``; cada ``.py`` é
lido uma vez (utf-8, bytes decodificados) e percorrido pelo walker
AST. Falhas de leitura/decodificação viram diagnóstico; exceção
inesperada do walker vira ``adapter-crash`` (error) — o adapter nunca
propaga exceção para o coordinator.

Isolamento: só lê os paths declarados em ``request.artifacts`` sob
``base_path``; não escreve no disco, não executa código, não abre
conexões.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from code_atlas.discovery.scanner import (
    artifact_path_confined,
    read_file_verified,
)
from code_atlas.domains.contracts import (
    DomainExtractionRequest,
    DomainExtractionResult,
)
from code_atlas.domains.spark.emit import Sink
from code_atlas.domains.spark.walk import _Walker

_MAX_BYTES = 2 * 1024 * 1024  # 2 MiB — acima disso: diagnóstico, skip


def _read_artifact(
    base_path: Path, rel_path: str, sink: Sink
) -> tuple[str, str] | None:
    """Lê o artefato → ``(texto, sha256-bytes)``; ``None`` + diag em falha."""
    base = base_path.resolve()
    # containment por pathlib — ``startswith`` em string aceitaria irmãos
    # com prefixo comum (``/repo-evil`` sob ``/repo``); absoluto e ``..``
    # no path do request são igualmente hostis (lf-c8 F7).
    p = artifact_path_confined(rel_path, base)
    if p is None:
        sink.diag(
            "artifact-outside-root", "warning",
            f"{rel_path}: artifact path escapes repository root",
            rel_path,
        )
        return None
    raw = read_file_verified(p, base)
    if raw is None:
        sink.diag(
            "read-error", "error",
            "cannot read artifact: unsafe or vanished", rel_path,
        )
        return None
    if len(raw) > _MAX_BYTES:
        sink.diag(
            "artifact-too-large", "info",
            f"skipping {rel_path}: {len(raw)} bytes > {_MAX_BYTES}",
            rel_path,
        )
        return None
    sha = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        sink.diag(
            "decode-error", "error",
            f"artifact is not valid utf-8: {exc}", rel_path,
        )
        return None
    return text, sha


def extract(
    request: DomainExtractionRequest,
    adapter_version: str,
    base_path: Path,
) -> DomainExtractionResult:
    """Percorre ``request.artifacts`` (somente ``.py``) e extrai linhagem."""
    sink = Sink(request, adapter_version)
    for art in sorted(request.artifacts, key=lambda a: a.path):
        if not art.path.lower().endswith(".py"):
            sink.diag(
                "unsupported-artifact", "info",
                f"{art.path}: adapter extracts only Python (.py) "
                f"artifacts — {art.artifact_kind or 'unknown'} skipped",
                art.path,
            )
            sink.bump("files_skipped")
            continue
        got = _read_artifact(base_path, art.path, sink)
        if got is None:
            sink.bump("files_unreadable")
            continue
        text, sha = got
        sink.bump("files_seen")
        try:
            _Walker(sink, art.path, sha, text).run()
        except Exception as exc:  # honestidade: crash vira diagnóstico
            sink.diag(
                "adapter-crash", "error",
                f"{type(exc).__name__} while walking {art.path}: {exc}",
                art.path,
            )
            sink.bump("parse_errors")
    sink.finish()
    return sink.result()
