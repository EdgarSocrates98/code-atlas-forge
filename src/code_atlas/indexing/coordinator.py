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
from typing import TypeGuard

from code_atlas.contracts.enums import SnapshotStatus
from code_atlas.contracts.models import Snapshot
from code_atlas.discovery.models import ScanResult
from code_atlas.discovery.scanner import read_file_verified
from code_atlas.discovery.scanner import scan as default_scan
from code_atlas.graph.builder import GraphBuilder
from code_atlas.graph.validation import validate_graph
from code_atlas.indexing.change_detector import detect_changes
from code_atlas.indexing.dependency_planner import (
    IndexPlan,
    InvalidationPlanner,
    PlanAction,
)
from code_atlas.indexing.persist import persist_graph
from code_atlas.indexing.reuse import (
    copy_occurrences_and_evidence,
    copy_relations,
    reconstruct_universe,
)
from code_atlas.parsers.contracts import GraphBuildResult, ParseResult
from code_atlas.parsers.engine import ParseEngine
from code_atlas.resolvers.syntactic.resolver import ScopeUniverse
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
        self._planner = InvalidationPlanner()
        self.last_plan: IndexPlan | None = None  # plano do último index

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

    def index(
        self, root: Path, force: bool = False, incremental: bool = False
    ) -> IndexOutcome:
        """Escaneia `root` e publica snapshot READY (ou no-op) atomicamente.

        ``force=True`` ignora o atalho de no-op por digest idêntico.
        Um scan parcial/truncado nunca vira READY — é registrado como falha.

        ``incremental=True`` (lf-0019) difa o manifesto contra o último
        READY, produz um ``IndexPlan`` inspecionável e pula parse dos
        arquivos ``reuse`` — ocorrências/relações deles são copiadas do
        snapshot anterior com remap de FKs dentro da mesma transação
        atômica. ``force`` desativa o caminho incremental.
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
        if not force and self._is_unchanged(current, digest, versions):
            with self._conn:
                store.finish_index_job(
                    job_id, "noop", snapshot_id=current.id,
                    counts={"files": 0}, failures=(),
                )
            return self._noop_outcome(current.id, job_id)

        # Planejamento incremental ANTES de parsear (lf-0019): o plano é
        # produzido e inspecionável antes de qualquer trabalho. O plano é
        # amarrado ao snapshot `prior_snap` — se outro escritor publicar
        # antes do nosso lock, o copy-forward continua correto porque as
        # linhas copiadas vêm de `prior_snap` (não do current relido).
        self.last_plan = None
        plan: IndexPlan | None = None
        prior_id: int | None = None
        reused_paths: set[str] = set()
        parse_only: set[str] | None = None
        preserved = None
        missing_ctx: set[str] = set()
        if incremental and not force:
            (
                plan, prior_id, reused_paths, preserved,
                parse_only, missing_ctx,
            ) = self._plan_incremental(scan, repo_id, versions)

        # Fase de análise FORA do lock de escrita: parse + grafo + validação.
        results, file_status, read_failures = self._parse_files(
            root, scan, only=parse_only
        )
        graph = self._builder.build(
            Path(root).name, scan, results, preserved_universe=preserved
        )
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

            if not force and self._is_unchanged(current, digest, versions):
                store.finish_index_job(
                    job_id, "noop", snapshot_id=current.id,
                    counts={"files": 0}, failures=(),
                )
                self._conn.commit()
                return self._noop_outcome(current.id, job_id)

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
            pcounts = self._persist_with_reuse(
                repo_id=repo_id, snapshot_id=snapshot_id,
                file_ids=file_ids, graph=graph, file_status=file_status,
                plan=plan, prior_id=prior_id,
                reused_paths=reused_paths, missing_ctx=missing_ctx,
            )
            # evidência explícita para arquivos que falharam no parse —
            # nenhuma perda silenciosa vira READY
            pcounts["evidence"] += self._record_parse_failures(
                store, snapshot_id, scan, file_ids, read_failures
            )
            self._validate(snapshot_id)
            store.publish_snapshot(repo_id, snapshot_id)
            counts = {
                "files": len(rows),
                "files_parsed": len(results),
                "files_reused": (
                    len(reused_paths - missing_ctx) if plan is not None else 0
                ),
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

    def _plan_incremental(
        self, scan: ScanResult, repo_id: int, versions: dict[str, str]
    ) -> tuple[
        IndexPlan | None, int | None, set[str],
        ScopeUniverse | None, set[str] | None, set[str],
    ]:
        """Diff + plano de invalidação contra o último READY (lf-0019).

        Retorna ``(plan, prior_snapshot_id, reused_paths,
        preserved_universe, parse_only, missing_ctx)``; ``plan=None``
        quando não há snapshot anterior (a chamada inteira vira full
        index).
        """
        store = self._store
        prior_snap = store.latest_ready_snapshot(repo_id)
        if prior_snap is None or prior_snap.id is None:
            return None, None, set(), None, None, set()
        prior_id = prior_snap.id
        prior_inv = store.snapshot_inventory(prior_id)
        changeset = detect_changes(
            scan, prior_inv, dict(prior_snap.analyzer_versions), versions,
        )
        prior_status = {
            str(r["path"]): str(r["parse_status"]) for r in prior_inv
        }
        plan = self._planner.plan(
            changeset,
            store.module_import_edges(prior_id),
            prior_status,
        )
        self.last_plan = plan
        reused_paths = set(plan.paths_for(PlanAction.REUSE))
        preserved = reconstruct_universe(store, prior_id, reused_paths)
        # rede de segurança: arquivo "reuse" sem ctx persistido (sem
        # símbolo de módulo) → re-parse honesto
        covered = {ctx.path for ctx in preserved.nid_mod.values()}
        missing_ctx = reused_paths - covered
        parse_only = (
            set(plan.paths_for(PlanAction.REPARSE))
            | set(plan.paths_for(PlanAction.REVALIDATE_RELATIONS))
            | missing_ctx
        )
        return plan, prior_id, reused_paths, preserved, parse_only, missing_ctx

    def _persist_with_reuse(
        self,
        *,
        repo_id: int,
        snapshot_id: int,
        file_ids: dict[str, int],
        graph: GraphBuildResult,
        file_status: dict[str, str],
        plan: IndexPlan | None,
        prior_id: int | None,
        reused_paths: set[str],
        missing_ctx: set[str],
    ) -> dict[str, int]:
        """Copy-forward + persist na mesma transação (lf-0019).

        Ocorrências/evidências dos arquivos ``reuse`` são copiadas ANTES
        do ``persist_graph`` (que usa o mapa para amarrar arestas de
        arquivos parseados a módulos reutilizados); relations DEPOIS, já
        com todos os endpoints existentes no snapshot novo.
        """
        store = self._store
        external_occ: dict[str, int] = {}
        occ_map: dict[int, int] = {}
        evi_map: dict[int, int] = {}
        reused_fids: set[int] = set()
        if plan is not None and prior_id is not None:
            reused_fids = {
                file_ids[p]
                for p in reused_paths - missing_ctx
                if p in file_ids
            }
            external_occ, occ_map, evi_map = copy_occurrences_and_evidence(
                store, prior_id, snapshot_id, reused_fids
            )
        pcounts = persist_graph(
            store, repo_id, snapshot_id, file_ids, graph,
            fts5=fts5_available(self._conn), file_status=file_status,
            external_occ=external_occ or None,
        )
        if plan is not None and prior_id is not None:
            rel_stats = copy_relations(
                store, prior_id, snapshot_id,
                reused_fids, occ_map, evi_map,
            )
            pcounts["relations"] += rel_stats["relations_copied"]
            pcounts["edges_skipped"] += rel_stats["relations_dropped"]
        return pcounts

    @staticmethod
    def _record_parse_failures(
        store: Store,
        snapshot_id: int,
        scan: ScanResult,
        file_ids: dict[str, int],
        read_failures: list[str],
    ) -> int:
        """Evidência de parse-failure por arquivo; retorna quantas rows."""
        sha_by_path = {f.path: f.sha256 for f in scan.files}
        n = 0
        for fail in read_failures:
            path, _, code = fail.partition(":")
            fid = file_ids.get(path)
            if fid is not None:
                store.insert_evidence(
                    snapshot_id, fid, path, None,
                    sha_by_path.get(path) or "", "parse-engine",
                    ANALYZER_VERSION, notes=f"parse-failure:{code}",
                )
                n += 1
        return n

    @staticmethod
    def _noop_outcome(snapshot_id: int | None, job_id: int) -> IndexOutcome:
        return IndexOutcome(
            snapshot_id=snapshot_id, job_id=job_id, status="noop",
            files_indexed=0, noop=True,
        )

    @staticmethod
    def _is_unchanged(
        current: Snapshot | None,
        digest: str,
        versions: dict[str, str],
    ) -> TypeGuard[Snapshot]:
        """READY anterior com mesmo digest+versões → no-op (Blueprint §6)."""
        return (
            current is not None
            and current.worktree_digest == digest
            and dict(current.analyzer_versions) == versions
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
        self, root: Path, scan: ScanResult,
        only: set[str] | None = None,
    ) -> tuple[list[ParseResult], dict[str, str], list[str]]:
        """Lê e parseia arquivos suportados — fora da transação de escrita.

        Retorna (results, file_status, failures). ``file_status`` alimenta
        ``symbol_occurrences.parse_status``; ``failures`` entra no job.

        ``only`` (lf-0019): quando presente, só paths no conjunto são
        lidos/parseados — os demais vêm do copy-forward do snapshot
        anterior e sequer são abertos (o sha do manifesto já prova
        conteúdo idêntico).

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
            if only is not None and f.path not in only:
                continue
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
