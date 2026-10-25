"""Acesso a dados do grafo (GraphStore parcial do Cycle 1).

Somente queries parametrizadas. O Cycle 1 escreve inventário + snapshots;
``symbols``/``relations``/``evidence`` existem no schema mas não são
populados por parsing real ainda.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from code_atlas.contracts.enums import SnapshotStatus
from code_atlas.contracts.models import Repository, Snapshot
from code_atlas.discovery.models import SourceFile, WarningKind
from code_atlas.domains.model import DomainEntity


def _snapshot_from_row(row: sqlite3.Row) -> Snapshot:
    return Snapshot(
        id=int(row["id"]),
        repository_id=int(row["repository_id"]),
        git_ref=str(row["git_ref"]),
        commit_sha=row["commit_sha"],
        dirty=bool(row["dirty"]),
        worktree_digest=str(row["worktree_digest"]),
        analyzer_versions=json.loads(row["analyzer_versions"]),
        parent_snapshot_id=row["parent_snapshot_id"],
        status=SnapshotStatus(str(row["status"])),
        created_at=str(row["created_at"]),
    )


class Store:
    """Repositório de dados sobre uma conexão sqlite3 já configurada."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    @property
    def conn(self) -> sqlite3.Connection:
        """Conexão subjacente — para queries compostas (lf-0019 reuse)."""
        return self._conn

    # --- repositories -------------------------------------------------

    def get_or_create_repository(
        self, identity_key: str, root_handle: str = ""
    ) -> int:
        """Repo estável por **identidade lógica** (lf-0021), não por path.

        ``identity_key`` vem de ``discovery.identity`` (git remote →
        commit raiz → path). ``root_handle`` é só diagnóstico —
        atualizado a cada index para refletir a localização atual
        (realocação mantém identidade).

        Race-safe (lf-0015 + v5): UNIQUE em ``identity_key`` +
        ``INSERT OR IGNORE`` — dois indexadores concorrentes convergem
        para a mesma linha.
        """
        row = self._conn.execute(
            "SELECT id, root_handle FROM repositories WHERE identity_key = ?",
            (identity_key,),
        ).fetchone()
        if row is not None:
            if root_handle and row["root_handle"] != root_handle:
                self._conn.execute(
                    "UPDATE repositories SET root_handle = ? WHERE id = ?",
                    (root_handle, int(row["id"])),
                )
            return int(row["id"])
        self._conn.execute(
            "INSERT OR IGNORE INTO repositories (root_handle, identity_key) "
            "VALUES (?, ?)",
            (root_handle, identity_key),
        )
        row2 = self._conn.execute(
            "SELECT id FROM repositories WHERE identity_key = ?", (identity_key,)
        ).fetchone()
        assert row2 is not None  # UNIQUE + INSERT OR IGNORE garante a linha
        return int(row2["id"])

    def repository_id_for(self, key_or_handle: str) -> int | None:
        """Id do repo por ``identity_key`` **ou** ``root_handle``.

        Compat de lookup: busca por identity primeiro (repos novos) e
        cai para root_handle (repos pré-v5/sem git indexados por path).
        """
        row = self._conn.execute(
            "SELECT id FROM repositories WHERE identity_key = ?"
            " OR root_handle = ? ORDER BY id LIMIT 1",
            (key_or_handle, key_or_handle),
        ).fetchone()
        return int(row["id"]) if row else None

    def repository_by_id(self, repo_id: int) -> Repository | None:
        row = self._conn.execute(
            "SELECT * FROM repositories WHERE id = ?", (repo_id,)
        ).fetchone()
        if row is None:
            return None
        return Repository(
            id=int(row["id"]),
            root_handle=str(row["root_handle"]),
            origin=str(row["origin"]),
            git_provider=str(row["git_provider"]),
            default_branch=str(row["default_branch"]),
        )

    # --- snapshots ----------------------------------------------------

    def latest_ready_snapshot(self, repository_id: int) -> Snapshot | None:
        row = self._conn.execute(
            "SELECT * FROM snapshots WHERE repository_id = ? AND status = 'READY' "
            "ORDER BY id DESC LIMIT 1",
            (repository_id,),
        ).fetchone()
        return _snapshot_from_row(row) if row else None

    def snapshot_by_id(self, snapshot_id: int) -> Snapshot | None:
        row = self._conn.execute(
            "SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)
        ).fetchone()
        return _snapshot_from_row(row) if row else None

    def insert_snapshot(
        self,
        repository_id: int,
        worktree_digest: str,
        analyzer_versions: dict[str, str],
        parent_snapshot_id: int | None,
        git_ref: str = "",
        commit_sha: str | None = None,
        dirty: bool = False,
    ) -> int:
        """Cria snapshot em estado BUILDING.

        ``dirty`` (lf-0021): worktree com alterações não commitadas —
        snapshot é **vinculado ao estado atual**, nunca ao ``commit_sha``
        limpo (queries veem ``commit_sha`` do HEAD mas ``dirty=1`` marca
        que o conteúdo indexado diverge dele).
        """
        cur = self._conn.execute(
            "INSERT INTO snapshots (repository_id, git_ref, commit_sha, dirty, "
            "worktree_digest, analyzer_versions, parent_snapshot_id, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'BUILDING')",
            (
                repository_id,
                git_ref,
                commit_sha,
                int(dirty),
                worktree_digest,
                json.dumps(analyzer_versions, sort_keys=True),
                parent_snapshot_id,
            ),
        )
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def set_snapshot_status(
        self, snapshot_id: int, status: SnapshotStatus, error: str | None = None
    ) -> None:
        self._conn.execute(
            "UPDATE snapshots SET status = ?, error = COALESCE(?, error) WHERE id = ?",
            (str(status), error, snapshot_id),
        )

    def publish_snapshot(self, repository_id: int, snapshot_id: int) -> None:
        """Marca o novo READY e torna STALE os anteriores (mesma transação).

        lf-0132: também renova ``sqlite_stat1`` das tabelas de domínio —
        os índices ``(snapshot_id, endpoint)`` de v11 só vencem o
        range-scan de ``idx_domain_relations_snap`` quando o planner
        conhece a seletividade real. O ANALYZE roda dentro da tx de
        publicação: stats commitam atomicamente com os dados que
        descrevem. Custo é O(tabelas de domínio) por publicação —
        proporcional e medido em ``benchmarks/bench_sqlite.py``.
        ``PRAGMA optimize`` foi medido e não re-analisa tabelas
        crescidas de forma confiável — ANALYZE explícito é o honesto.
        """
        self._conn.execute(
            "UPDATE snapshots SET status = 'STALE' "
            "WHERE repository_id = ? AND status = 'READY' AND id != ?",
            (repository_id, snapshot_id),
        )
        self.set_snapshot_status(snapshot_id, SnapshotStatus.READY)
        self._refresh_domain_stats()

    def _refresh_domain_stats(self) -> None:
        """``ANALYZE`` escopo-domínio — stats fiéis às rows publicadas."""
        if not self._has_table("domain_relations"):
            return
        # SQL literal por tabela — auditoria AST exige execute() com
        # constante/nome, nunca expressão concatenada.
        self._conn.execute("ANALYZE domain_relations")
        self._conn.execute("ANALYZE domain_entity_occurrences")
        self._conn.execute("ANALYZE domain_entities")

    def gc_stale_snapshots(self, repository_id: int, keep: int) -> int:
        """Remove snapshots STALE além dos ``keep`` mais recentes.

        lf-0031 — política de GC explícita: READY nunca é tocado;
        FAILED também é preservado (trilha de auditoria de falhas).
        Filhos caem por ``ON DELETE CASCADE`` — a integridade referencial
        continua garantida pelo próprio schema. Chamado dentro da
        transação de publicação (atomicidade com o READY novo).
        Retorna quantos snapshots foram removidos.
        """
        if keep < 0:
            raise ValueError("keep must be >= 0")
        # ``LIMIT -1 OFFSET keep`` = todos os STALE exceto os ``keep``
        # mais novos — SQL constante (auditoria AST).
        # FKs sem cascade precisam ser desfeitas antes do delete:
        # ``index_jobs.snapshot_id`` (o job fica — só perde o vínculo)
        # e ``snapshots.parent_snapshot_id`` (linhagem dos mais novos).
        _doomed = (
            "SELECT id FROM snapshots"
            " WHERE repository_id = ? AND status = 'STALE'"
            " ORDER BY id DESC LIMIT -1 OFFSET ?"
        )
        _sql_jobs = (
            "UPDATE index_jobs SET snapshot_id = NULL"
            " WHERE snapshot_id IN (" + _doomed + ")"
        )
        _sql_parent = (
            "UPDATE snapshots SET parent_snapshot_id = NULL"
            " WHERE parent_snapshot_id IN (" + _doomed + ")"
        )
        _sql_del = "DELETE FROM snapshots WHERE id IN (" + _doomed + ")"
        _sql_doomed_ids = (
            "SELECT id FROM snapshots"
            " WHERE repository_id = ? AND status = 'STALE'"
            " ORDER BY id DESC LIMIT -1 OFFSET ?"
        )
        doomed_ids = [
            int(r["id"]) for r in self._conn.execute(
                _sql_doomed_ids, (repository_id, keep)
            )
        ]
        self._conn.execute(_sql_jobs, (repository_id, keep))
        self._conn.execute(_sql_parent, (repository_id, keep))
        if doomed_ids and self._has_table("fts_occurrences"):
            # filhos caem por CASCADE, mas FTS não é FK — delete manual
            self._delete_fts_occurrences(doomed_ids)
        if doomed_ids and self._has_table("domain_fts"):
            # lf-0133: mesmo cuidado — domain_fts é FTS standalone, não
            # participa do cascade de snapshots.
            self._delete_domain_fts(doomed_ids)
        cur = self._conn.execute(_sql_del, (repository_id, keep))
        return int(cur.rowcount if cur.rowcount else 0)

    def _has_table(self, name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','shadow')"
            " AND name = ?",
            (name,),
        ).fetchone()
        return row is not None

    # --- source files + inventory ------------------------------------

    def upsert_source_file(self, repository_id: int, f: SourceFile) -> int:
        """Dedup por (repository_id, relative_path) — chave de conteúdo estável."""
        row = self._conn.execute(
            "SELECT id FROM source_files WHERE repository_id = ? AND relative_path = ?",
            (repository_id, f.path),
        ).fetchone()
        if row:
            return int(row["id"])
        cur = self._conn.execute(
            "INSERT INTO source_files (repository_id, relative_path, language, "
            "sha256, size_bytes, ignored) VALUES (?, ?, ?, ?, ?, ?)",
            (repository_id, f.path, f.language, f.sha256, f.size_bytes, int(f.ignored)),
        )
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def ensure_source_files(
        self, repository_id: int, files: Iterable[SourceFile]
    ) -> dict[str, int]:
        """Dedup de ``source_files`` em duas fases — path → id.

        Mesmo padrão de ``ensure_symbols`` (lf-0023): um SELECT inicial,
        executemany ``INSERT OR IGNORE`` dos ausentes, SELECT final.
        Paridade com o upsert antigo: metadados da linha existente
        NÃO são atualizados (o inventário por snapshot carrega o
        sha256/size frescos em ``file_inventory``).
        """
        flist = list(files)
        existing = self._conn.execute(
            "SELECT id, relative_path FROM source_files"
            " WHERE repository_id = ?",
            (repository_id,),
        ).fetchall()
        ids = {r["relative_path"]: int(r["id"]) for r in existing}
        missing = [f for f in flist if f.path not in ids]
        if missing:
            self._conn.executemany(
                "INSERT OR IGNORE INTO source_files (repository_id,"
                " relative_path, language, sha256, size_bytes, ignored)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                [
                    (repository_id, f.path, f.language, f.sha256,
                     f.size_bytes, int(f.ignored))
                    for f in missing
                ],
            )
            refetch = self._conn.execute(
                "SELECT id, relative_path FROM source_files"
                " WHERE repository_id = ?",
                (repository_id,),
            ).fetchall()
            ids = {r["relative_path"]: int(r["id"]) for r in refetch}
        return ids

    def insert_inventory_rows(
        self, snapshot_id: int, rows: Iterable[tuple[int, SourceFile]]
    ) -> None:
        """Insere (snapshot, source_file) com atributos da ocorrência."""
        self._conn.executemany(
            "INSERT INTO file_inventory (snapshot_id, source_file_id, sha256, "
            "size_bytes, ignored) VALUES (?, ?, ?, ?, ?)",
            [(snapshot_id, fid, f.sha256, f.size_bytes, int(f.ignored)) for fid, f in rows],
        )

    def inventory_count(self, snapshot_id: int) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS c FROM file_inventory WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        return int(row["c"])

    def snapshot_inventory(self, snapshot_id: int) -> list[dict[str, object]]:
        """Inventário do snapshot: path, language, sha256, source_file_id,
        e pior parse_status observado nas ocorrências do arquivo."""
        rows = self._conn.execute(
            """
            SELECT sf.id AS source_file_id, sf.relative_path, sf.language,
                   fi.sha256,
                   COALESCE(
                     (SELECT MAX(CASE so.parse_status
                                   WHEN 'parse-failed' THEN 2
                                   WHEN 'parsed-with-errors' THEN 1
                                   ELSE 0 END)
                        FROM symbol_occurrences so
                       WHERE so.snapshot_id = fi.snapshot_id
                         AND so.source_file_id = fi.source_file_id),
                     -1) AS status_rank
              FROM file_inventory fi
              JOIN source_files sf ON sf.id = fi.source_file_id
             WHERE fi.snapshot_id = ?
             ORDER BY sf.relative_path
            """,
            (snapshot_id,),
        ).fetchall()
        rank = {0: "parsed", 1: "parsed-with-errors", 2: "parse-failed"}
        return [
            {
                "path": r["relative_path"],
                "language": r["language"],
                "sha256": r["sha256"],
                "source_file_id": r["source_file_id"],
                "parse_status": rank.get(int(r["status_rank"]), "unparsed"),
            }
            for r in rows
        ]

    def module_import_edges(
        self, snapshot_id: int
    ) -> list[tuple[str, str | None, str, str]]:
        """Arestas IMPORTS do snapshot mapeadas para paths.

        Retorna ``(importer_path, imported_path|None, to_unresolved,
        resolution_status)`` — insumo do planner de invalidação (lf-0019).
        """
        rows = self._conn.execute(
            """
            SELECT sf_from.relative_path AS importer,
                   sf_to.relative_path   AS imported,
                   r.to_unresolved       AS to_text,
                   r.resolution_status   AS status
              FROM relations r
              JOIN symbol_occurrences o_from ON o_from.id = r.from_id
              JOIN source_files sf_from ON sf_from.id = o_from.source_file_id
              LEFT JOIN symbol_occurrences o_to ON o_to.id = r.to_id
              LEFT JOIN source_files sf_to ON sf_to.id = o_to.source_file_id
             WHERE r.snapshot_id = ? AND r.type = 'IMPORTS'
             ORDER BY sf_from.relative_path
            """,
            (snapshot_id,),
        ).fetchall()
        return [
            (r["importer"], r["imported"], r["to_text"] or "", r["status"])
            for r in rows
        ]

    def snapshot_file_symbols(
        self, snapshot_id: int
    ) -> list[tuple[str, str, str, str, int]]:
        """Símbolos com ocorrência no snapshot, por arquivo.

        Retorna ``(path, kind, qualified_name, language, source_file_id)``
        — usado para reconstruir contextos de módulo reutilizados no
        índice incremental (lf-0019).
        """
        rows = self._conn.execute(
            """
            SELECT DISTINCT sf.relative_path AS path, s.kind, s.qualified_name,
                   s.language, so.source_file_id
              FROM symbol_occurrences so
              JOIN symbols s ON s.id = so.logical_symbol_id
              JOIN source_files sf ON sf.id = so.source_file_id
             WHERE so.snapshot_id = ?
             ORDER BY sf.relative_path, s.qualified_name
            """,
            (snapshot_id,),
        ).fetchall()
        return [
            (r["path"], r["kind"], r["qualified_name"], r["language"],
             r["source_file_id"])
            for r in rows
        ]

    def foreign_key_violations(self) -> list[str]:
        """Lista violações FK pendentes (integrity gate antes do READY)."""
        rows = self._conn.execute("PRAGMA foreign_key_check").fetchall()
        return [str(tuple(r)) for r in rows]

    def cross_snapshot_relations(self, snapshot_id: int) -> list[int]:
        """Relações cujos endpoints apontam fora do próprio snapshot.

        ``from_id`` é NOT NULL (JOIN); ``to_id`` pode ser NULL em alvos
        unresolved/external — LEFT JOIN + checagem só quando presente.
        """
        rows = self._conn.execute(
            """
            SELECT r.id FROM relations r
            JOIN symbol_occurrences o1 ON o1.id = r.from_id
            LEFT JOIN symbol_occurrences o2 ON o2.id = r.to_id
            WHERE r.snapshot_id = ?
              AND (o1.snapshot_id != ?
                   OR (r.to_id IS NOT NULL AND o2.snapshot_id != ?))
            """,
            (snapshot_id, snapshot_id, snapshot_id),
        ).fetchall()
        return [int(r["id"]) for r in rows]

    # --- symbols / occurrences / relations / evidence (Cycle 2) -------
    # --- persistência em lote (lf-0023, medido: ~145k execute/5k arquivos) --

    _BATCH = 400  # linhas por INSERT multi-row (abaixo do limite de vars)

    def ensure_symbols(
        self, repository_id: int, keys: list[tuple[str, str, str]]
    ) -> tuple[dict[tuple[str, str, str], int], set[tuple[str, str, str]]]:
        """Dedup de símbolos em duas fases — 145k+ round-trips → ~90.

        Fase 1: SELECT dos existentes numa query. Fase 2: executemany
        ``INSERT OR IGNORE`` só dos ausentes, seguido de SELECT final
        dos ids (``RETURNING`` não devolve ids de linhas ignoradas).
        ``keys``: ``(language, qualified_name, kind)`` dedupados pelo
        caller.

        Retorna ``(id_map, created)`` — ``created`` é o conjunto exato
        de chaves que passaram a existir nesta chamada (correto mesmo
        sob escritor concorrente: derivado do diff dos SELECTs).
        """
        uniq = sorted(set(keys))
        existing = self._conn.execute(
            "SELECT id, language, qualified_name, kind FROM symbols"
            " WHERE repository_id = ?",
            (repository_id,),
        ).fetchall()
        ids = {
            (r["language"], r["qualified_name"], r["kind"]): int(r["id"])
            for r in existing
        }
        pre = set(ids)
        missing = [k for k in uniq if k not in ids]
        if missing:
            self._conn.executemany(
                "INSERT OR IGNORE INTO symbols "
                "(repository_id, language, qualified_name, kind) "
                "VALUES (?, ?, ?, ?)",
                [(repository_id, *k) for k in missing],
            )
            refetch = self._conn.execute(
                "SELECT id, language, qualified_name, kind FROM symbols"
                " WHERE repository_id = ?",
                (repository_id,),
            ).fetchall()
            ids = {
                (r["language"], r["qualified_name"], r["kind"]): int(r["id"])
                for r in refetch
            }
        return ids, set(ids) - pre

    def insert_symbols_batch(
        self, rows: Iterable[tuple[int, str, str, str]]
    ) -> None:
        """FTS: inserts em lote — chamado só para símbolos novos."""
        self._conn.executemany(
            "INSERT INTO fts_symbols (rowid, qualified_name, language, kind) "
            "VALUES (?, ?, ?, ?)",
            list(rows),
        )

    def insert_fts_occurrences_batch(
        self, rows: Iterable[tuple[object, ...]]
    ) -> None:
        """lf-0036 — FTS por-ocorrência (snapshot-scoped): rowid = id da
        ocorrência; campos já vêm tokenizados do persist."""
        self._conn.executemany(
            "INSERT INTO fts_occurrences (rowid, snapshot_id, name, terms,"
            " signature, docstring, path, language, kind)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            list(rows),
        )

    def _delete_fts_occurrences(self, snapshot_ids: Iterable[int]) -> None:
        ids = list(snapshot_ids)
        if not ids:
            return
        # DELETE em FTS regular: placeholder único via json_each (SQL
        # constante — auditoria AST proíbe IN dinâmico).
        self._conn.execute(
            "DELETE FROM fts_occurrences WHERE snapshot_id IN"
            " (SELECT value FROM json_each(?))",
            (json.dumps(ids),),
        )

    def _delete_domain_fts(self, snapshot_ids: Iterable[int]) -> None:
        ids = list(snapshot_ids)
        if not ids:
            return
        self._conn.execute(
            "DELETE FROM domain_fts WHERE snapshot_id IN"
            " (SELECT value FROM json_each(?))",
            (json.dumps(ids),),
        )

    def _insert_returning(
        self, sql_prefix: str, rows: list[tuple[object, ...]], ncols: int
    ) -> list[int]:
        """Multi-row ``INSERT ... RETURNING id`` em chunks.

        SQLite devolve ids na ordem das VALUES — o mapa com as linhas
        de entrada é posicional e estável dentro da transação.
        """
        out: list[int] = []
        ph = ",".join("?" for _ in range(ncols))
        for i in range(0, len(rows), self._BATCH):
            chunk = rows[i : i + self._BATCH]
            stmt = sql_prefix + ",".join(
                "(" + ph + ")" for _ in chunk
            ) + " RETURNING id"
            cur = self._conn.execute(
                stmt, [v for row in chunk for v in row]
            )
            out.extend(int(r["id"]) for r in cur.fetchall())
        return out

    def insert_occurrences_batch(
        self, rows: list[tuple[object, ...]]
    ) -> list[int]:
        """Batch de ocorrências — retorna ids na ordem das linhas."""
        return self._insert_returning(
            "INSERT INTO symbol_occurrences (snapshot_id, logical_symbol_id,"
            " source_file_id, span_start, span_end, line, col, content_hash,"
            " parse_status) VALUES ",
            rows, 9,
        )

    def insert_evidence_batch(
        self, rows: list[tuple[object, ...]]
    ) -> list[int]:
        """Batch de evidências — retorna ids na ordem das linhas."""
        return self._insert_returning(
            "INSERT INTO evidence (snapshot_id, source_file_id, path, span,"
            " content_hash, extractor, extractor_version, notes,"
            " resolver_id, resolver_version, evidence_tier, index_hash,"
            " validation_status) VALUES ",
            rows, 13,
        )

    def insert_evidence(
        self,
        snapshot_id: int,
        source_file_id: int,
        path: str,
        span_json: str | None,
        content_hash: str,
        extractor: str,
        extractor_version: str,
        notes: str = "",
        resolver_id: str = "",
        resolver_version: str = "",
        evidence_tier: int = 0,
        index_hash: str = "",
        validation_status: str = "",
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO evidence (snapshot_id, source_file_id, path, span, "
            "content_hash, extractor, extractor_version, notes, "
            "resolver_id, resolver_version, evidence_tier, index_hash, "
            "validation_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snapshot_id, source_file_id, path, span_json, content_hash,
                extractor, extractor_version, notes, resolver_id,
                resolver_version, evidence_tier, index_hash,
                validation_status,
            ),
        )
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def insert_relations(self, rows: Iterable[tuple[object, ...]]) -> int:
        """Batch de relações — executemany (nenhum id precisa voltar)."""
        self._conn.executemany(
            "INSERT INTO relations (snapshot_id, from_id, to_id, to_unresolved, "
            "type, resolution_status, confidence_class, resolver, evidence_ids) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            list(rows),
        )
        return self._conn.total_changes  # para telemetria externa

    # --- domain entities / relations (lf-0110, schema v9) --------------

    def ensure_domain_entities(
        self, repository_id: int, entities: Iterable[DomainEntity]
    ) -> dict[str, int]:
        """Dedup de entidades de domínio — identity_key → row id.

        Mesma técnica de ``ensure_symbols``: SELECT inicial, ``INSERT OR
        IGNORE`` em lote dos ausentes, SELECT final. ``identity_key`` é
        a chave lógica canônica (``DomainIdentity.logical_key``) —
        catálogo/namespace/ambiente fazem parte da chave, então
        homônimos em escopos distintos não colidem.
        """
        items: dict[str, DomainEntity] = {}
        for e in entities:
            items[e.identity_key()] = e
        if not items:
            return {}
        existing = self._conn.execute(
            "SELECT id, identity_key FROM domain_entities"
            " WHERE repository_id = ?",
            (repository_id,),
        ).fetchall()
        ids = {r["identity_key"]: int(r["id"]) for r in existing}
        missing = [k for k in sorted(items) if k not in ids]
        if missing:
            self._conn.executemany(
                "INSERT OR IGNORE INTO domain_entities "
                "(repository_id, domain, kind, identity_key, qualified_name,"
                " name, catalog, namespace, environment, attributes)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        repository_id,
                        items[k].identity.domain,
                        str(items[k].kind),
                        k,
                        items[k].identity.qualified_name,
                        items[k].name,
                        items[k].identity.catalog,
                        items[k].identity.namespace,
                        items[k].identity.environment,
                        json.dumps(
                            dict(items[k].attributes), sort_keys=True
                        ),
                    )
                    for k in missing
                ],
            )
            refetch = self._conn.execute(
                "SELECT id, identity_key FROM domain_entities"
                " WHERE repository_id = ?",
                (repository_id,),
            ).fetchall()
            ids = {r["identity_key"]: int(r["id"]) for r in refetch}
        return ids

    def insert_domain_occurrences_batch(
        self, rows: list[tuple[object, ...]]
    ) -> list[int]:
        """Batch de ocorrências de domínio — retorna ids na ordem.

        ``rows``: (snapshot_id, entity_id, source_file_id, span_start,
        span_end, line, col, content_hash, attributes_json,
        extraction_status).
        """
        return self._insert_returning(
            "INSERT INTO domain_entity_occurrences (snapshot_id, entity_id,"
            " source_file_id, span_start, span_end, line, col, content_hash,"
            " attributes, extraction_status) VALUES ",
            rows, 10,
        )

    def insert_domain_relations(self, rows: Iterable[tuple[object, ...]]) -> int:
        """Batch de relações de domínio — executemany.

        ``rows``: (snapshot_id, from_entity_occ_id, from_symbol_occ_id,
        to_entity_occ_id, to_symbol_occ_id, to_unresolved, type,
        resolution_status, confidence_class, resolver, evidence_ids).
        """
        self._conn.executemany(
            "INSERT INTO domain_relations (snapshot_id, from_entity_occ_id,"
            " from_symbol_occ_id, to_entity_occ_id, to_symbol_occ_id,"
            " to_unresolved, type, resolution_status, confidence_class,"
            " resolver, evidence_ids) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            list(rows),
        )
        return self._conn.total_changes

    # --- domain_fts (lf-0133, schema v12) ---------------------------------

    def domain_fts_available(self) -> bool:
        """``domain_fts`` presente **e** build com FTS5 — gate de escrita.

        Bancos migrados por uma build com FTS5 podem ser abertos por uma
        sem o módulo: o INSERT na vtable falharia. A checagem dupla
        (tabela + probe de capacidade) é a condição honesta de escrita.
        """
        if not self._has_table("domain_fts"):
            return False
        from code_atlas.storage.db import fts5_available

        return fts5_available(self._conn)

    def insert_domain_fts_batch(
        self, rows: Iterable[tuple[object, ...]]
    ) -> None:
        """FTS de domínio em lote — rowid = id de ``domain_entity_occurrences``.

        ``rows``: (occ_id, snapshot_id, entity_id, name, qualified_name,
        terms, path, domain, kind) — campos já normalizados pelo caller
        (``terms`` sai de ``search.tokenizer.index_terms``).
        """
        self._conn.executemany(
            "INSERT INTO domain_fts (rowid, snapshot_id, entity_id, name,"
            " qualified_name, terms, path, domain, kind)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            list(rows),
        )

    def rebuild_domain_fts(self, snapshot_id: int) -> int:
        """Backfill in-place de ``domain_fts`` para um snapshot existente.

        lf-0133: snapshots persistidos antes da v12 não têm rows de FTS
        e a faceta cai no LIKE bounded (declarado). Este é o caminho de
        reconstrução — o equivalente programático de um ``code-atlas
        reindex``: lê o join ocorrência↔entidade↔arquivo e regrava o
        índice do snapshot com os mesmos campos do persist (``terms``
        pré-tokenizado em Python — não é expressível em SQL puro).
        Idempotente dentro da transação do caller (delete + insert do
        mesmo escopo). Retorna o número de rows regravadas; 0 quando a
        tabela não existe ou o snapshot não tem ocorrências de domínio.
        """
        if not self.domain_fts_available():
            return 0
        rows = self._conn.execute(
            "SELECT deo.id AS occ_id, deo.snapshot_id, deo.entity_id,"
            " de.name, de.qualified_name, de.domain, de.kind,"
            " sf.relative_path AS path"
            " FROM domain_entity_occurrences deo"
            " JOIN domain_entities de ON de.id = deo.entity_id"
            " LEFT JOIN source_files sf ON sf.id = deo.source_file_id"
            " WHERE deo.snapshot_id = ? ORDER BY deo.id",
            (snapshot_id,),
        ).fetchall()
        self._conn.execute(
            "DELETE FROM domain_fts WHERE snapshot_id = ?",
            (snapshot_id,),
        )
        if not rows:
            return 0
        # import tardio: storage não pode puxar code_atlas.search em
        # nível de módulo (search/__init__ → engine → domain → store).
        from code_atlas.search.tokenizer import index_terms

        fts_rows = [
            (
                int(r["occ_id"]), int(r["snapshot_id"]), int(r["entity_id"]),
                str(r["name"] or ""), str(r["qualified_name"]),
                index_terms(
                    str(r["name"] or ""), str(r["qualified_name"]),
                    str(r["path"] or ""),
                ),
                str(r["path"] or ""), str(r["domain"]), str(r["kind"]),
            )
            for r in rows
        ]
        self.insert_domain_fts_batch(fts_rows)
        return len(fts_rows)

    def snapshot_domain_entities(
        self, snapshot_id: int
    ) -> list[tuple[str, str, str, str, str | None]]:
        """Entidades com ocorrência no snapshot.

        Retorna ``(domain, kind, qualified_name, identity_key, path)`` —
        insumo de inspeção/testes e da resolução cross-domain (lf-0117).
        """
        rows = self._conn.execute(
            """
            SELECT de.domain, de.kind, de.qualified_name, de.identity_key,
                   sf.relative_path AS path
              FROM domain_entity_occurrences deo
              JOIN domain_entities de ON de.id = deo.entity_id
              LEFT JOIN source_files sf ON sf.id = deo.source_file_id
             WHERE deo.snapshot_id = ?
             ORDER BY de.domain, de.kind, de.qualified_name
            """,
            (snapshot_id,),
        ).fetchall()
        return [
            (r["domain"], r["kind"], r["qualified_name"], r["identity_key"],
             r["path"])
            for r in rows
        ]

    def domain_entity_id_for(
        self, repository_id: int, identity_key: str
    ) -> int | None:
        """Row id de ``domain_entities`` por chave lógica canônica."""
        row = self._conn.execute(
            "SELECT id FROM domain_entities"
            " WHERE repository_id = ? AND identity_key = ?",
            (repository_id, identity_key),
        ).fetchone()
        return int(row["id"]) if row else None

    def domain_occurrences_for(
        self, snapshot_id: int, entity_id: int
    ) -> list[int]:
        """Ids de ocorrências de uma entidade num snapshot."""
        rows = self._conn.execute(
            "SELECT id FROM domain_entity_occurrences"
            " WHERE snapshot_id = ? AND entity_id = ? ORDER BY id",
            (snapshot_id, entity_id),
        ).fetchall()
        return [int(r["id"]) for r in rows]

    def symbol_occurrences_for_name(
        self, snapshot_id: int, qualified_name: str
    ) -> list[int]:
        """Ocorrências de código num snapshot por qualified_name.

        Ponte código↔domínio (lf-0117/0120): endpoints ``*_symbol_occ_id``
        de ``domain_relations`` referenciam estas rows. Múltiplos matches
        (mesmo qname, kinds distintos) são retornados todos — o caller
        decide entre resolved único / ambiguous honesto.
        """
        rows = self._conn.execute(
            """
            SELECT so.id FROM symbol_occurrences so
              JOIN symbols s ON s.id = so.logical_symbol_id
             WHERE so.snapshot_id = ? AND s.qualified_name = ?
             ORDER BY so.id
            """,
            (snapshot_id, qualified_name),
        ).fetchall()
        return [int(r["id"]) for r in rows]

    def cross_snapshot_domain_relations(self, snapshot_id: int) -> list[int]:
        """Relações de domínio com endpoint fora do próprio snapshot.

        Espelha ``cross_snapshot_relations``: endpoints nullable recebem
        LEFT JOIN e só contam quando presentes.
        """
        rows = self._conn.execute(
            """
            SELECT r.id FROM domain_relations r
            LEFT JOIN domain_entity_occurrences fe
                   ON fe.id = r.from_entity_occ_id
            LEFT JOIN symbol_occurrences fs
                   ON fs.id = r.from_symbol_occ_id
            LEFT JOIN domain_entity_occurrences te
                   ON te.id = r.to_entity_occ_id
            LEFT JOIN symbol_occurrences ts
                   ON ts.id = r.to_symbol_occ_id
             WHERE r.snapshot_id = ?
               AND ((r.from_entity_occ_id IS NOT NULL AND fe.snapshot_id != ?)
                 OR (r.from_symbol_occ_id IS NOT NULL AND fs.snapshot_id != ?)
                 OR (r.to_entity_occ_id IS NOT NULL AND te.snapshot_id != ?)
                 OR (r.to_symbol_occ_id IS NOT NULL AND ts.snapshot_id != ?))
            """,
            (snapshot_id,) * 5,
        ).fetchall()
        return [int(r["id"]) for r in rows]

    # --- resolução cross-domain (lf-0117, Phase 9) ---------------------

    def domain_occurrences_snapshot(
        self, snapshot_id: int
    ) -> list[sqlite3.Row]:
        """Ocorrências de domínio do snapshot com a entidade lógica.

        Junta ``domain_entity_occurrences`` + ``domain_entities`` +
        path do arquivo-fonte — a visão completa que a resolução
        cross-domain precisa (identidade canônica vem da
        ``identity_key``; ``root`` só existe lá dentro).
        """
        return self._conn.execute(
            """
            SELECT deo.id AS occ_id, deo.entity_id, deo.source_file_id,
                   deo.span_start, deo.span_end, deo.extraction_status,
                   de.domain, de.kind, de.identity_key, de.qualified_name,
                   de.name, de.catalog, de.namespace, de.environment,
                   de.attributes,
                   sf.relative_path AS source_path
              FROM domain_entity_occurrences deo
              JOIN domain_entities de ON de.id = deo.entity_id
              LEFT JOIN source_files sf ON sf.id = deo.source_file_id
             WHERE deo.snapshot_id = ?
             ORDER BY deo.id
            """,
            (snapshot_id,),
        ).fetchall()

    def domain_relations_snapshot(
        self, snapshot_id: int
    ) -> list[sqlite3.Row]:
        """Todas as rows de ``domain_relations`` do snapshot (ordem de id).

        O resolver lê o conjunto inteiro — re-resolve só as
        ``unresolved``, mas precisa das demais para dedup de arestas
        novas e para idempotência (``resolver='cross-domain'``).
        """
        return self._conn.execute(
            """
            SELECT id, type, from_entity_occ_id, from_symbol_occ_id,
                   to_entity_occ_id, to_symbol_occ_id, to_unresolved,
                   resolution_status, confidence_class, resolver,
                   evidence_ids
              FROM domain_relations
             WHERE snapshot_id = ?
             ORDER BY id
            """,
            (snapshot_id,),
        ).fetchall()

    def evidence_snapshot(self, snapshot_id: int) -> list[sqlite3.Row]:
        """Rows de ``evidence`` do snapshot — insumo de proveniência."""
        return self._conn.execute(
            """
            SELECT id, source_file_id, path, span, extractor, notes
              FROM evidence
             WHERE snapshot_id = ?
             ORDER BY id
            """,
            (snapshot_id,),
        ).fetchall()

    def file_symbol_occurrences(
        self, snapshot_id: int
    ) -> list[tuple[str, int]]:
        """``(path, occ_id)`` de símbolos FILE com ocorrência no snapshot.

        Ponte domínio→código (lf-0117): ``qualified_name`` de nó FILE é
        o path relativo — alvo honesto para refs textuais que são paths
        inventariados (artefato de CI, script de job).
        """
        rows = self._conn.execute(
            """
            SELECT s.qualified_name AS path, so.id AS occ_id
              FROM symbol_occurrences so
              JOIN symbols s ON s.id = so.logical_symbol_id
             WHERE so.snapshot_id = ? AND s.kind = 'file'
             ORDER BY s.qualified_name, so.id
            """,
            (snapshot_id,),
        ).fetchall()
        return [(str(r["path"]), int(r["occ_id"])) for r in rows]

    def persist_cross_resolutions(
        self,
        snapshot_id: int,
        updates: Iterable[tuple[object, ...]],
        rows: Iterable[tuple[object, ...]],
    ) -> dict[str, int]:
        """Aplica o plano do resolver cross-domain — transação do caller.

        ``updates``: (to_entity_occ, to_symbol_occ, to_unresolved,
        resolution_status, confidence_class, resolver, evidence_ids_json,
        relation_id) — re-resolução in place de rows ``unresolved``;
        ``resolver`` marca a proveniência da decisão (``cross-domain``).
        O ``WHERE`` exige ``snapshot_id`` — isolamento por construção,
        nenhuma row de outro snapshot pode ser tocada.

        ``rows``: mesmo shape de ``insert_domain_relations`` — arestas
        novas entre ocorrências do snapshot. Retorna contagens.
        """
        upd = list(updates)
        ins = list(rows)
        self._conn.executemany(
            "UPDATE domain_relations"
            "   SET to_entity_occ_id = ?, to_symbol_occ_id = ?,"
            "       to_unresolved = ?, resolution_status = ?,"
            "       confidence_class = ?, resolver = ?, evidence_ids = ?"
            " WHERE id = ? AND snapshot_id = ?",
            [(*u, snapshot_id) for u in upd],
        )
        self._conn.executemany(
            "INSERT INTO domain_relations (snapshot_id, from_entity_occ_id,"
            " from_symbol_occ_id, to_entity_occ_id, to_symbol_occ_id,"
            " to_unresolved, type, resolution_status, confidence_class,"
            " resolver, evidence_ids) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ins,
        )
        return {"cross_updated": len(upd), "cross_inserted": len(ins)}

    # --- index jobs ---------------------------------------------------

    def insert_index_job(self, repository_id: int, manifest_hash: str) -> int:
        cur = self._conn.execute(
            "INSERT INTO index_jobs (repository_id, status, manifest_hash) "
            "VALUES (?, 'running', ?)",
            (repository_id, manifest_hash),
        )
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def finish_index_job(
        self,
        job_id: int,
        status: str,
        snapshot_id: int | None = None,
        counts: dict[str, int] | None = None,
        failures: tuple[str, ...] = (),
    ) -> None:
        self._conn.execute(
            "UPDATE index_jobs SET status = ?, snapshot_id = ?, counts = ?, "
            "failures = ?, finished_at = strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            "WHERE id = ?",
            (
                status,
                snapshot_id,
                json.dumps(counts or {}, sort_keys=True),
                json.dumps(list(failures)),
                job_id,
            ),
        )

    def last_warnings_digest(self, warnings: Iterable[WarningKind]) -> str:
        return json.dumps(sorted({str(w) for w in warnings}))
