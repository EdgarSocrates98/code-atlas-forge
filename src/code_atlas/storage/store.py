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
        """Marca o novo READY e torna STALE os anteriores (mesma transação)."""
        self._conn.execute(
            "UPDATE snapshots SET status = 'STALE' "
            "WHERE repository_id = ? AND status = 'READY' AND id != ?",
            (repository_id, snapshot_id),
        )
        self.set_snapshot_status(snapshot_id, SnapshotStatus.READY)

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
            " content_hash, extractor, extractor_version, notes) VALUES ",
            rows, 8,
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
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO evidence (snapshot_id, source_file_id, path, span, "
            "content_hash, extractor, extractor_version, notes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                snapshot_id, source_file_id, path, span_json, content_hash,
                extractor, extractor_version, notes,
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
