"""IndexCoordinator: scan → parse → graph → BUILDING → validação → READY.

Pipeline completo do Cycle 2 (Blueprint §6): Discovery → Manifest →
Parser Registry → Símbolos → Graph Builder → Validação → READY.

Regras:

- Inventário idêntico (mesmo ``worktree_digest`` + mesmas versões de
  analisadores **incluindo parser/grammar**) é **no-op** — mudança de
  versão de extrator derrota o no-op por design.
- Parsing e construção do grafo acontecem **fora** do ``BEGIN
  IMMEDIATE`` — lock de escrita fica curto.
- Falha dentro da transação faz rollback completo; o snapshot é marcado
  ``FAILED`` em transação separada para diagnóstico e o ``READY`` anterior
  permanece intacto.
- Falha de parse por arquivo vira ``files_failed`` + failures no job e
  evidence com notes — o snapshot pode ser READY com perda explícita e
  auditável. Falha fatal (engine/grafo inválido) → snapshot FAILED.
- Escritor único via ``BEGIN IMMEDIATE``; leitores concorrentes sob WAL.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from code_atlas.contracts.enums import SnapshotStatus
from code_atlas.discovery.models import ScanResult
from code_atlas.discovery.scanner import read_file_verified
from code_atlas.discovery.scanner import scan as default_scan
from code_atlas.graph.builder import GraphBuilder
from code_atlas.graph.validation import validate_graph
from code_atlas.indexing.persist import persist_graph
from code_atlas.parsers.contracts import ParseResult
from code_atlas.parsers.engine import ParseEngine
from code_atlas.storage.db import fts5_available
from code_atlas.storage.store import Store

ANALYZER_NAME = "code-atlas"
ANALYZER_VERSION = "0.1.0"


@dataclass(frozen=True)
class IndexOutcome:
    """Resultado de uma tentativa de indexação."""

    snapshot_id: int | None
    job_id: int | None  # None quando nem o job pôde ser registrado
    status: str  # success | failed | noop
    files_indexed: int
    noop: bool = False
    error: str = ""
    secrets_skipped: int = 0
    warnings: tuple[str, ...] = ()
    stats: dict[str, int] = field(default_factory=dict)


class IndexCoordinator:
    """Coordena indexação sobre uma conexão sqlite3 migrada."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        scanner: Callable[[Path], ScanResult] = default_scan,
        engine: ParseEngine | None = None,
        builder: GraphBuilder | None = None,
    ) -> None:
        self._conn = conn
        self._store = Store(conn)
        self._scanner = scanner
        self._engine = engine or ParseEngine()
        self._builder = builder or GraphBuilder()

    def _analyzer_versions(self, scan: ScanResult) -> dict[str, str]:
        """Versões que compõem a identidade do índice.

        Inclui parser+grammar por linguagem registrada — manifest
        idêntico com extrator novo **não** é no-op (lf-0011).
        """
        versions = {
            "scanner": f"{ANALYZER_NAME}-scanner/{ANALYZER_VERSION}",
            "scan_mode": str(scan.mode),
        }
        reg = self._engine.registry
        for lang in reg.languages:
            adapter = reg.for_language(lang)
            if adapter is not None:
                versions[f"parser:{lang}"] = (
                    f"{adapter.adapter_id}/{adapter.parser_version}"
                    f"+{adapter.grammar_version}"
                )
        return versions

    def index(self, root: Path, force: bool = False) -> IndexOutcome:
        """Escaneia `root` e publica snapshot READY (ou no-op) atomicamente.

        ``force=True`` ignora o atalho de no-op por digest idêntico.
        Um scan parcial/truncado nunca vira READY — é registrado como falha.
        """
        scan = self._scanner(Path(root))
        digest = scan.manifest_sha256()
        versions = self._analyzer_versions(scan)
        store = self._store

        # Job registrado em transação própria para sobreviver a rollback —
        # falhas precisam ficar inspecionáveis. Sob lock de escrita preso,
        # até esta escrita mínima falha → outcome failed honesto (lf-0015).
        try:
            with self._conn:
                repo_id = store.get_or_create_repository(
                    str(Path(root).resolve()))
                job_id = store.insert_index_job(repo_id, digest)
        except sqlite3.OperationalError as exc:
            return IndexOutcome(
                snapshot_id=None, job_id=None, status="failed",
                files_indexed=len(scan.files), error=str(exc),
                secrets_skipped=scan.secret_count,
            )

        if scan.partial or scan.truncated:
            error = "scan incomplete (partial or truncated; not publishing READY)"
            snap_id = self._mark_failed(repo_id, job_id, error)
            return IndexOutcome(
                snapshot_id=snap_id, job_id=job_id, status="failed",
                files_indexed=len(scan.files), error=error,
                secrets_skipped=scan.secret_count,
                warnings=tuple(str(w.kind) for w in scan.warnings),
            )

        # Fast-path noop ANTES de parsear (Blueprint §6: no-op não refaz
        # parse). A checagem se repete dentro do lock para cobrir corrida
        # com outro escritor.
        current = store.latest_ready_snapshot(repo_id)
        if (
            not force
            and current is not None
            and current.worktree_digest == digest
            and dict(current.analyzer_versions) == versions
        ):
            with self._conn:
                store.finish_index_job(
                    job_id, "noop", snapshot_id=current.id,
                    counts={"files": 0}, failures=(),
                )
            return IndexOutcome(
                snapshot_id=current.id, job_id=job_id, status="noop",
                files_indexed=0, noop=True,
            )

        # Fase de análise FORA do lock de escrita: parse + grafo + validação.
        results, file_status, read_failures = self._parse_files(root, scan)
        graph = self._builder.build(Path(root).name, scan, results)
        g_errors = validate_graph(graph)
        if g_errors:
            # invariante violada = bug/violação interna → FAILED, READY salvo
            error = f"graph integrity violated: {'; '.join(g_errors[:3])}"
            snap_id = self._mark_failed(repo_id, job_id, error)
            return IndexOutcome(
                snapshot_id=snap_id, job_id=job_id, status="failed",
                files_indexed=len(scan.files), error=error,
                secrets_skipped=scan.secret_count,
            )

        # Escritor único: BEGIN IMMEDIATE serializa com outros escritores.
        # busy_timeout do connect é 5s — persistências grandes podem
        # segurar o lock mais que isso; retry limitado dá ao perdedor uma
        # chance honesta antes de virar FAILED (lf-0015).
        begin_err = self._acquire_write_lock()
        if begin_err is not None:
            snap_id = self._mark_failed(repo_id, job_id, str(begin_err))
            return IndexOutcome(
                snapshot_id=snap_id, job_id=job_id, status="failed",
                files_indexed=len(scan.files), error=str(begin_err),
                secrets_skipped=scan.secret_count,
            )
        try:
            current = store.latest_ready_snapshot(repo_id)

            if (
                not force
                and current is not None
                and current.worktree_digest == digest
                and dict(current.analyzer_versions) == versions
            ):
                store.finish_index_job(
                    job_id, "noop", snapshot_id=current.id,
                    counts={"files": 0}, failures=(),
                )
                self._conn.commit()
                return IndexOutcome(
                    snapshot_id=current.id, job_id=job_id, status="noop",
                    files_indexed=0, noop=True,
                )

            snapshot_id = store.insert_snapshot(
                repo_id,
                worktree_digest=digest,
                analyzer_versions=versions,
                parent_snapshot_id=current.id if current else None,
                git_ref=scan.git.branch,
                commit_sha=scan.git.head_sha or None,
            )
            rows = [(store.upsert_source_file(repo_id, f), f) for f in scan.files]
            store.insert_inventory_rows(snapshot_id, rows)
            file_ids = {f.path: fid for fid, f in rows}
            pcounts = persist_graph(
                store, repo_id, snapshot_id, file_ids, graph,
                fts5=fts5_available(self._conn), file_status=file_status,
            )
            # evidência explícita para arquivos que falharam no parse —
            # nenhuma perda silenciosa vira READY
            sha_by_path = {f.path: f.sha256 for f in scan.files}
            for fail in read_failures:
                path, _, code = fail.partition(":")
                fid = file_ids.get(path)
                if fid is not None:
                    store.insert_evidence(
                        snapshot_id, fid, path, None,
                        sha_by_path.get(path) or "", "parse-engine",
                        ANALYZER_VERSION, notes=f"parse-failure:{code}",
                    )
                    pcounts["evidence"] += 1
            self._validate(snapshot_id)
            store.publish_snapshot(repo_id, snapshot_id)
            counts = {
                "files": len(rows),
                "files_parsed": len(results),
                "files_failed": sum(
                    1 for x in read_failures
                    if not x.endswith(":syntax-errors")
                ),
                "files_with_errors": sum(
                    1 for x in read_failures
                    if x.endswith(":syntax-errors")
                ),
                "secrets_skipped": scan.secret_count,
                **pcounts,
            }
            failures = (
                tuple(str(w.kind) for w in scan.warnings)
                + tuple(read_failures)
            )
            store.finish_index_job(
                job_id, "success", snapshot_id=snapshot_id,
                counts=counts, failures=failures,
            )
            self._conn.commit()
            return IndexOutcome(
                snapshot_id=snapshot_id, job_id=job_id, status="success",
                files_indexed=len(rows), secrets_skipped=scan.secret_count,
                warnings=tuple(str(w.kind) for w in scan.warnings),
                stats=counts,
            )
        except Exception as exc:
            self._conn.rollback()
            failed_snap = self._mark_failed(repo_id=repo_id, job_id=job_id, error=str(exc))
            return IndexOutcome(
                snapshot_id=failed_snap, job_id=job_id, status="failed",
                files_indexed=0, error=str(exc),
            )

    _BEGIN_ATTEMPTS = 4
    _BEGIN_BACKOFF_S = 0.05

    def _acquire_write_lock(self) -> sqlite3.OperationalError | None:
        """BEGIN IMMEDIATE com backoff; retorna o erro final ou None."""
        last: sqlite3.OperationalError | None = None
        for attempt in range(self._BEGIN_ATTEMPTS):
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                return None
            except sqlite3.OperationalError as exc:
                last = exc
                time.sleep(self._BEGIN_BACKOFF_S * (attempt + 1))
        return last

    def _parse_files(
        self, root: Path, scan: ScanResult
    ) -> tuple[list[ParseResult], dict[str, str], list[str]]:
        """Lê e parseia arquivos suportados — fora da transação de escrita.

        Retorna (results, file_status, failures). ``file_status`` alimenta
        ``symbol_occurrences.parse_status``; ``failures`` entra no job.

        Integridade scan→parse (lf-0015): os bytes parseados precisam ser
        exatamente os bytes hasheados pelo manifest — leitura verificada
        (``read_file_verified``) + comparação de sha256. Divergência vira
        falha de arquivo auditável, nunca READY com identidade errada.
        """
        root_real = Path(root).resolve()
        results: list[ParseResult] = []
        file_status: dict[str, str] = {}
        failures: list[str] = []
        for f in scan.files:
            if self._engine.registry.for_path(f.path) is None:
                continue  # extensão sem parser — não conta como falha
            if f.sha256 is None:
                failures.append(f"{f.path}:hash-unavailable")
                file_status[f.path] = "parse-failed"
                continue
            data = read_file_verified(Path(root) / f.path, root_real)
            if data is None:
                failures.append(f"{f.path}:read-error")
                file_status[f.path] = "parse-failed"
                continue
            if hashlib.sha256(data).hexdigest() != f.sha256:
                failures.append(f"{f.path}:content-mismatch")
                file_status[f.path] = "parse-failed"
                continue
            res = self._engine.parse(f.path, data)
            results.append(res)
            if not res.supported:
                failures.append(
                    f"{f.path}:{res.diagnostics[0].code if res.diagnostics else 'parse'}"
                )
                file_status[f.path] = "parse-failed"
            elif res.has_error:
                failures.append(f"{f.path}:syntax-errors")
                file_status[f.path] = "parsed-with-errors"
            else:
                file_status[f.path] = "parsed"
        return results, file_status, failures

    def _validate(self, snapshot_id: int) -> None:
        """Integrity gate antes do READY; levanta para abortar a transação."""
        violations = self._store.foreign_key_violations()
        if violations:
            raise sqlite3.IntegrityError(
                f"FK violations in snapshot {snapshot_id}: {violations[:5]}"
            )
        cross = self._store.cross_snapshot_relations(snapshot_id)
        if cross:
            raise sqlite3.IntegrityError(
                f"cross-snapshot relations {cross[:5]} in snapshot {snapshot_id}"
            )

    def _mark_failed(self, repo_id: int, job_id: int, error: str) -> int:
        """Insere snapshot FAILED para diagnóstico (transação separada).

        O BUILDING abortado já sofreu rollback; registramos um FAILED novo
        para que falhas fiquem inspecionáveis sem tocar no READY anterior.
        """
        with self._conn:
            snap_id = self._store.insert_snapshot(
                repo_id,
                worktree_digest="",
                analyzer_versions={},
                parent_snapshot_id=None,
            )
            self._store.set_snapshot_status(snap_id, SnapshotStatus.FAILED, error[:500])
            self._store.finish_index_job(
                job_id, "failed", snapshot_id=snap_id, failures=(error[:500],)
            )
        return snap_id
