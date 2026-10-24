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

import contextlib
import dataclasses
import hashlib
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeGuard

from code_atlas.contracts.enums import SnapshotStatus
from code_atlas.contracts.models import Snapshot
from code_atlas.discovery.identity import identity_key
from code_atlas.discovery.models import ScanResult
from code_atlas.discovery.scanner import read_file_verified
from code_atlas.discovery.scanner import scan as default_scan
from code_atlas.graph.builder import GraphBuilder
from code_atlas.graph.validation import validate_graph
from code_atlas.indexing.cache import CacheKey, ParseResultCache
from code_atlas.indexing.change_detector import detect_changes
from code_atlas.indexing.dependency_planner import (
    IndexPlan,
    InvalidationPlanner,
    PlanAction,
    PlannedFile,
)
from code_atlas.indexing.persist import persist_graph
from code_atlas.indexing.reuse import cache_key_for, config_digest
from code_atlas.parsers.contracts import (
    GraphBuildResult,
    ParseDiagnostic,
    ParseResult,
)
from code_atlas.parsers.engine import ParseEngine
from code_atlas.resolvers.bootstrap import ResolverFactory
from code_atlas.resolvers.contracts import SemanticResolver
from code_atlas.resolvers.coordinator import ResolverCoordinator
from code_atlas.resolvers.enrichment import SemanticEnricher
from code_atlas.resolvers.registry import ResolverRegistry
from code_atlas.resolvers.syntactic.resolver import (
    RESOLVER_ID as _SYN_RESOLVER_ID,
)
from code_atlas.resolvers.syntactic.resolver import (
    RESOLVER_VERSION as _SYN_RESOLVER_VERSION,
)
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
        resolver_factories: Sequence[ResolverFactory] = (),
        resolver_versions: Mapping[str, str] | None = None,
    ) -> None:
        self._conn = conn
        self._store = Store(conn)
        self._scanner = scanner
        self._engine = engine or ParseEngine()
        self._builder = builder or GraphBuilder()
        self._planner = InvalidationPlanner()
        self._resolver_factories = tuple(resolver_factories)
        self._resolver_versions = dict(resolver_versions or {})
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
        # O SyntacticResolver é fixo no builder — sua versão define a
        # extração de relações e deve invalidar o índice (lf-0023).
        versions[f"resolver:{_SYN_RESOLVER_ID}"] = _SYN_RESOLVER_VERSION
        # Resolvers opt-in ativos mudam o resultado da resolução —
        # entrar/sair da config invalida relações (lf-0027).
        if self._resolver_factories:
            versions["resolver:external"] = "enabled/1"
        # identidade dos resolvers externos (lf-0030): índice SCIP novo
        # → sha muda → invalidação completa, conservadora e correta.
        versions.update(self._resolver_versions)
        return versions

    def _enrich_semantic(
        self, graph: GraphBuildResult
    ) -> tuple[GraphBuildResult, dict[str, int]]:
        """Etapa de enriquecimento semântico (lf-0027): resolvers opt-in
        (factories injetadas) re-resolvem arestas não-resolvidas via
        ``ResolverCoordinator`` por tier de evidência. Falha de resolver
        vira diagnóstico no grafo — o índice nunca para por resolver."""
        if not self._resolver_factories:
            return graph, {
                "semantic_attempted": 0,
                "semantic_enriched": 0,
                "semantic_diagnostics": 0,
            }
        registry = ResolverRegistry()
        resolvers: list[SemanticResolver] = []
        diags: list[ParseDiagnostic] = []
        for factory in self._resolver_factories:
            try:
                resolvers.append(factory(self._builder.universe))
            except Exception as exc:
                diags.append(
                    ParseDiagnostic(
                        code="semantic:bootstrap:resolver-error",
                        severity="warning",
                        message=f"resolver factory failed: {exc}",
                    )
                )
        for r in resolvers:
            registry.register(r)
        try:
            enriched, stats = SemanticEnricher(
                ResolverCoordinator(registry)
            ).enrich(graph)
        finally:
            # LSP e outros resolvers com lifecycle fecham ao fim do
            # enriquecimento — nenhum processo sobrevive ao index().
            for r in resolvers:
                closer = getattr(r, "close", None)
                if callable(closer):
                    with contextlib.suppress(Exception):
                        closer()
        if diags:
            enriched = dataclasses.replace(
                enriched,
                diagnostics=tuple(diags) + enriched.diagnostics,
            )
        return enriched, stats

    def index(
        self, root: Path, force: bool = False, incremental: bool = False
    ) -> IndexOutcome:
        """Escaneia `root` e publica snapshot READY (ou no-op) atomicamente.

        ``force=True`` ignora o atalho de no-op por digest idêntico.
        Um scan parcial/truncado nunca vira READY — é registrado como falha.

        ``incremental=True`` (lf-0019) difa o manifesto contra o último
        READY e produz um ``IndexPlan`` inspecionável. Arquivos ``REUSE``
        são servidos do cache versionado de ``ParseResult`` (lf-0020) —
        hit pula leitura e tree-sitter; miss cai para parse real.
        ``REPARSE``/``REVALIDATE`` sempre re-parseiam. O grafo é montado
        com resultados de todos os arquivos e persistido inteiro no
        snapshot novo (isolamento lógico). ``force`` desativa o caminho
        incremental.
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
                # lf-0021: identidade lógica (git remote/raiz/path) —
                # realocação do diretório mantém o mesmo repo_id.
                ikey = identity_key(scan.git, Path(root))
                repo_id = store.get_or_create_repository(
                    ikey, str(Path(root).resolve()))
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
        # produzido e inspecionável antes de qualquer trabalho. Com o
        # cache de ParseResult (lf-0020), ``REUSE``/``REVALIDATE`` tentam
        # o cache primeiro — miss cai para leitura+parse reais. O grafo
        # é construído com resultados de todos os arquivos e as arestas
        # são re-resolvidas sobre o universo novo — relações nunca são
        # reutilizadas prontas.
        self.last_plan = None
        plan: IndexPlan | None = None
        reparse_paths: set[str] = set()
        cache_paths: set[str] = set()
        if incremental and not force:
            plan, reparse_paths, cache_paths = self._plan_incremental(
                scan, repo_id, versions
            )

        cfg_digest = config_digest(self._engine)
        cache = ParseResultCache(self._conn, repo_id)

        # Fase de análise FORA do lock de escrita: parse + grafo + validação.
        results, file_status, read_failures, cache_puts, cache_hits = (
            self._parse_files(
                root, scan, cache, cfg_digest,
                reparse=reparse_paths, cache_candidates=cache_paths,
                incremental=plan is not None,
            )
        )
        graph = self._builder.build(Path(root).name, scan, results)
        graph, sem_stats = self._enrich_semantic(graph)
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
                dirty=scan.git.dirty,
            )
            file_ids = store.ensure_source_files(repo_id, scan.files)
            rows = [(file_ids[f.path], f) for f in scan.files]
            store.insert_inventory_rows(snapshot_id, rows)
            pcounts = persist_graph(
                store, repo_id, snapshot_id, file_ids, graph,
                fts5=fts5_available(self._conn), file_status=file_status,
            )
            # evidência explícita para arquivos que falharam no parse —
            # nenhuma perda silenciosa vira READY
            pcounts["evidence"] += self._record_parse_failures(
                store, snapshot_id, scan, file_ids, read_failures
            )
            self._validate(snapshot_id)
            store.publish_snapshot(repo_id, snapshot_id)
            pruned = self._apply_cache(
                cache, repo_id, snapshot_id, cache_puts, cache_hits
            )
            counts = {
                "files": len(rows),
                "files_parsed": len(results) - len(cache_hits),
                "files_reused": len(cache_hits),
                "cache_puts": len(cache_puts),
                "cache_pruned": pruned,
                "files_failed": sum(
                    1 for x in read_failures
                    if not x.endswith(":syntax-errors")
                ),
                "files_with_errors": sum(
                    1 for x in read_failures
                    if x.endswith(":syntax-errors")
                ),
                "secrets_skipped": scan.secret_count,
                **sem_stats,
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

    @staticmethod
    def _apply_cache(
        cache: ParseResultCache,
        repo_id: int,
        snapshot_id: int,
        puts: dict[str, tuple[CacheKey, ParseResult]],
        hits: dict[str, CacheKey],
    ) -> int:
        """Cache versionado (lf-0020) na transação de publicação.

        Registra resultados novos e marca os hits — rollback de build
        falho não deixa entradas órfãs. GC roda após o publish (o
        snapshot novo já conta como READY mais recente).
        """
        for key, res in puts.values():
            cache.put(key, res, snapshot_id=snapshot_id)
        for key in hits.values():
            cache.mark_used(key, snapshot_id)
        return cache.gc(repo_id)

    def plan_dry_run(
        self, root: Path
    ) -> tuple[IndexPlan | None, dict[str, str]]:
        """``index-plan`` (lf-0022): scan + diff + plano, **zero writes**.

        Retorna ``(plan, diagnostics)``. ``plan=None`` quando não há
        READY anterior — o caller renderiza o plano full implícito
        (todos os arquivos ``reparse``). Nunca cria repo/snapshot/job.
        """
        scan = self._scanner(Path(root))
        versions = self._analyzer_versions(scan)
        store = self._store
        ikey = identity_key(scan.git, Path(root))
        repo_id = store.repository_id_for(ikey)
        if repo_id is None:
            # compat: repo indexado por path antes do v5
            repo_id = store.repository_id_for(str(Path(root).resolve())
            )
        if repo_id is not None:
            plan, _, _ = self._plan_incremental(scan, repo_id, versions)
            if plan is not None:
                return plan, {}
        # Sem READY anterior (ou repo jamais indexado): o plano real
        # seria full — sintetiza tudo como reparse, marcado incremental.
        full = IndexPlan(
            files=tuple(
                PlannedFile(f.path, PlanAction.REPARSE,
                            "no prior READY snapshot — full build")
                for f in scan.files
            ),
            incremental=False,
        )
        return full, {"note": "no prior READY — dry-run rendered as full build"}

    def _plan_incremental(
        self, scan: ScanResult, repo_id: int, versions: dict[str, str]
    ) -> tuple[IndexPlan | None, set[str], set[str]]:
        """Diff + plano de invalidação contra o último READY (lf-0019).

        Retorna ``(plan, reparse_paths, cache_candidates)``; ``plan=None``
        quando não há snapshot anterior (a chamada inteira vira full
        index). Arquivos ``REPARSE`` + ``REVALIDATE`` são lidos/parseados
        direto; ``REUSE`` tenta o cache primeiro (lf-0020) e cai para
        parse real em miss — as relações são sempre re-resolvidas pelo
        builder sobre o universo novo.
        """
        store = self._store
        prior_snap = store.latest_ready_snapshot(repo_id)
        if prior_snap is None or prior_snap.id is None:
            return None, set(), set()
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
        reparse_paths = set(plan.paths_for(PlanAction.REPARSE)) | set(
            plan.paths_for(PlanAction.REVALIDATE_RELATIONS)
        )
        # Spec lf-0020: result file-level só é reutilizado quando o
        # conjunto de dependências está inalterado (REUSE). REVALIDATE
        # re-parseia — "reevaluate" = re-extração + re-resolução.
        cache_paths = set(plan.paths_for(PlanAction.REUSE))
        return plan, reparse_paths, cache_paths

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
        self,
        root: Path,
        scan: ScanResult,
        cache: ParseResultCache,
        cfg_digest: str,
        *,
        reparse: set[str],
        cache_candidates: set[str],
        incremental: bool,
    ) -> tuple[
        list[ParseResult], dict[str, str], list[str],
        dict[str, tuple[CacheKey, ParseResult]],
        dict[str, CacheKey],
    ]:
        """Lê e parseia arquivos suportados — fora da transação de escrita.

        Retorna ``(results, file_status, failures, puts, hits)``:
        ``puts`` = resultados novos para ``cache.put`` na transação de
        publicação; ``hits`` = chaves servidas do cache para
        ``mark_used``.

        lf-0020: ``cache_candidates`` (REUSE + REVALIDATE) tentam o
        cache primeiro — hit devolve o ``ParseResult`` sem ler nem
        parsear o arquivo; miss cai para o caminho normal (o sha do
        manifesto prova conteúdo idêntico → mesmo resultado). Parse
        real só acontece para ``reparse`` ou em modo full.

        Integridade scan→parse (lf-0015): os bytes parseados precisam ser
        exatamente os bytes hasheados pelo manifest — leitura verificada
        (``read_file_verified``) + comparação de sha256. Divergência vira
        falha de arquivo auditável, nunca READY com identidade errada.
        """
        root_real = Path(root).resolve()
        results: list[ParseResult] = []
        file_status: dict[str, str] = {}
        failures: list[str] = []
        puts: dict[str, tuple[CacheKey, ParseResult]] = {}
        hits: dict[str, CacheKey] = {}
        for f in scan.files:
            adapter = self._engine.registry.for_path(f.path)
            if adapter is None:
                continue  # extensão sem parser — não conta como falha
            key = cache_key_for(f, adapter, cfg_digest)
            if f.sha256 is None:
                failures.append(f"{f.path}:hash-unavailable")
                file_status[f.path] = "parse-failed"
                continue
            # lf-0020: candidato a cache → hit pula leitura e tree-sitter
            if (
                incremental
                and key is not None
                and f.path in cache_candidates
                and f.path not in reparse
            ):
                cached = cache.get(key)
                if cached is not None:
                    results.append(cached)
                    hits[f.path] = key
                    self._status_of(cached, f.path, file_status, failures)
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
            # só resultados suportados entram no cache — falhas são
            # re-tentadas a cada run (determinístico, honesto e barato)
            if key is not None and res.supported:
                puts[f.path] = (key, res)
            self._status_of(res, f.path, file_status, failures)
        return results, file_status, failures, puts, hits

    @staticmethod
    def _status_of(
        res: ParseResult,
        path: str,
        file_status: dict[str, str],
        failures: list[str],
    ) -> None:
        """Deriva parse_status + failure string de um ParseResult."""
        if not res.supported:
            failures.append(
                f"{path}:{res.diagnostics[0].code if res.diagnostics else 'parse'}"
            )
            file_status[path] = "parse-failed"
        elif res.has_error:
            failures.append(f"{path}:syntax-errors")
            file_status[path] = "parsed-with-errors"
        else:
            file_status[path] = "parsed"

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
