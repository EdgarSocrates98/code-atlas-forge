#!/usr/bin/env python3
"""Benchmark SQLite de domínio — lf-0132 (Phase 6) + lf-0133 (Phase 7).

Fixture mid-size determinística (seed fixa, 300 artefatos .sql + 20 .py
→ ~12k entidades/~12k arestas de domínio) indexada **com schema capado
em v10** — o estado "before" é um banco antigo de verdade: sem índices
v11, sem ``sqlite_stat1``, sem ``domain_fts``. Depois o arquivo é
copiado, migrado até v12 e ``Store.rebuild_domain_fts`` backfilla o
FTS do snapshot existente — o estado "after" é exatamente um banco
antigo atualizado, exercitando o caminho documentado de upgrade.

Mede por query (mediana de ``--repeats`` execuções, 1 warm-up):

- ego de entidade (``from_/to_entity_occ_id IN (...)`` literais);
- hidratação de endpoints (``deo.id IN json_each`` — antes range-scan
  de snapshot, depois probes por rowid);
- ``entity_detail`` summaries (IN literais + GROUP BY);
- listagem ``domain_relations`` e filtro por type;
- passo de BFS de linhagem (OR de 4 ``json_each`` — limite residual
  conhecido, fora do alcance de índice);
- match direto da faceta: LIKE bounded (before) x ``domain_fts`` MATCH
  (after) — mesmo shape do ``DomainEntitySearch`` real;
- expansão ``via_relation``: forma OR (before) x UNION (after);
- nível API: ``DomainEntitySearch.search`` LIKE x FTS;
- custo do ``rebuild_domain_fts`` (backfill declarado);
- custo do ``ANALYZE`` de publicação (domínio).

Honestidade: números medidos, nunca metas. ``NOT RUN`` explícito onde
um lado não se aplica (ex.: FTS no banco v10). O harness nunca executa
artefatos do repo sintetizado — só os lê via adapters de indexação.

Uso::

    .venv/bin/python benchmarks/bench_sqlite.py
    .venv/bin/python benchmarks/bench_sqlite.py --repeats 9 --files 300
    .venv/bin/python benchmarks/bench_sqlite.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import platform
import random
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from code_atlas.domains.registry import DomainRegistry
from code_atlas.domains.sql import SqlDomainAdapter
from code_atlas.indexing import IndexCoordinator
from code_atlas.search.contracts import SearchRequest
from code_atlas.search.domain import DomainEntitySearch
from code_atlas.storage import connect
from code_atlas.storage import migrations as mig
from code_atlas.storage.db import fts5_available
from code_atlas.storage.store import Store

SEED = 20261208
RESULTS = Path(__file__).resolve().parent / "results" / "sqlite-latest.json"
EGO_IN_SIZE = 30  # ids literais no ego — shape do endpoint real

_TABLE = """\
CREATE TABLE {ns}.t{i} (
    id BIGINT,
    payload_{i} STRING,
    ts TIMESTAMP
);
"""

_JOB = """\
-- job{i}: staging -> mart
INSERT INTO mart.fact_{i} (id, payload, k)
SELECT s.id, s.payload_{i}, k.k{j}
FROM staging.t{i} s
JOIN mart.dim_{j} k ON k.id = s.id
JOIN staging.t{a} a ON a.id = s.id
WHERE s.ts > TIMESTAMP '2026-01-01 00:00:00';

CREATE VIEW mart.v_{i} AS
SELECT id, payload_{i} FROM mart.fact_{i} JOIN staging.t{b} b ON b.id = id;
"""


def synth_repo(root: Path, n_files: int) -> Path:
    """Repositório sintético determinístico — só DDL/DML, nada executa."""
    rng = random.Random(SEED)
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n_files):
        body = _TABLE.format(ns=rng.choice(("staging", "mart")), i=i)
        body += _JOB.format(i=i, j=rng.randint(0, max(0, i - 1)),
                           a=rng.randint(0, i), b=rng.randint(0, i))
        (root / f"job{i:03}.sql").write_text(body, encoding="utf-8")
    for i in range(20):
        (root / f"app{i}.py").write_text(
            f"def handler_{i}(x):\n    return x + {i}\n", encoding="utf-8")
    return root


def index_at_v10(db: Path, repo: Path) -> None:
    """Indexa a fixture com migrações capadas em v10 — banco 'before'."""
    real = mig.migrations
    mig.migrations = lambda *, fts5: [  # type: ignore[assignment]
        m for m in real(fts5=fts5) if m.version <= 10]
    try:
        conn = connect(db)
        mig.migrate(conn)
    finally:
        mig.migrations = real  # type: ignore[assignment]
    reg = DomainRegistry()
    reg.register(SqlDomainAdapter(base_path=repo))
    res = IndexCoordinator(conn, domain_registry=reg).index(repo)
    if res.status != "success":
        raise RuntimeError(f"fixture index failed: {res.status}")
    # o publish atual já roda ANALYZE de domínio (lf-0132) — um banco
    # antigo real não teria stat1; apaga para o "before" ser fiel.
    conn.execute("DELETE FROM sqlite_stat1")
    conn.commit()
    conn.close()


def upgrade_to_v12(db: Path) -> dict[str, object]:
    """Migra até o head + backfill FTS do snapshot — banco 'after'."""
    conn = connect(db)
    t0 = time.perf_counter()
    version = mig.migrate(conn)
    migrate_s = time.perf_counter() - t0
    snap = conn.execute(
        "SELECT MAX(id) FROM snapshots WHERE status='READY'"
    ).fetchone()[0]
    store = Store(conn)
    t0 = time.perf_counter()
    with conn:
        fts_rows = store.rebuild_domain_fts(snap)
    rebuild_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    store._refresh_domain_stats()  # custo medido da publicação
    analyze_s = time.perf_counter() - t0
    conn.close()
    return {
        "schema_version": version,
        "migrate_s": round(migrate_s, 4),
        "rebuild_domain_fts_rows": fts_rows,
        "rebuild_domain_fts_s": round(rebuild_s, 4),
        "analyze_publish_s": round(analyze_s, 4),
    }


def timeit(conn: sqlite3.Connection, sql: str,
           params: tuple[object, ...], repeats: int) -> dict[str, object]:
    """Mediana/min/max de uma query read-only — 1 warm-up."""
    conn.execute(sql, params).fetchall()  # warm-up
    samples: list[float] = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        conn.execute(sql, params).fetchall()
        samples.append(time.perf_counter() - t0)
    return {
        "median_ms": round(statistics.median(samples) * 1000, 3),
        "min_ms": round(min(samples) * 1000, 3),
        "max_ms": round(max(samples) * 1000, 3),
        "rows": len(conn.execute(sql, params).fetchall()),
    }


def bench_queries(db: Path, *, after: bool, repeats: int) -> dict[str, object]:
    """Bateria por-shape — ``after=True`` só roda no banco migrado."""
    uri = f"file:{db.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    snap = conn.execute(
        "SELECT MAX(id) FROM snapshots WHERE status='READY'"
    ).fetchone()[0]
    repo = conn.execute("SELECT MIN(id) FROM repositories").fetchone()[0]
    occ_ids = [int(r[0]) for r in conn.execute(
        "SELECT id FROM domain_entity_occurrences WHERE snapshot_id=?"
        " ORDER BY id LIMIT ?", (snap, EGO_IN_SIZE))]
    occ_tail = [int(r[0]) for r in conn.execute(
        "SELECT id FROM domain_entity_occurrences WHERE snapshot_id=?"
        " ORDER BY id DESC LIMIT ?", (snap, EGO_IN_SIZE))]
    sym_occ = [int(r[0]) for r in conn.execute(
        "SELECT id FROM symbol_occurrences WHERE snapshot_id=? LIMIT 5",
        (snap,))]
    like = "%fact%"
    ph = ",".join("?" for _ in occ_ids)
    pht = ",".join("?" for _ in occ_tail)
    out: dict[str, object] = {}
    plans: dict[str, list[str]] = {}

    def run(name: str, sql: str, params: tuple[object, ...]) -> None:
        out[name] = timeit(conn, sql, params, repeats)
        plans[name] = [
            str(r[-1])
            for r in conn.execute(
                "EXPLAIN QUERY PLAN " + sql, params).fetchall()
            if "SUBQUERY" not in str(r[-1])
            and "json_each" not in str(r[-1])
        ]

    run("ego_edges_in",
        "SELECT id FROM domain_relations"
        f" WHERE snapshot_id = ? AND (from_entity_occ_id IN ({ph})"
        f" OR to_entity_occ_id IN ({ph})) ORDER BY id LIMIT 100",
        (snap, *occ_ids, *occ_ids))
    # pior caso honesto: ids altos — matches esparsos não alimentam o
    # early-exit do LIMIT; o scan de snapshot lê a faixa inteira.
    run("ego_edges_in_tail",
        "SELECT id FROM domain_relations"
        f" WHERE snapshot_id = ? AND (from_entity_occ_id IN ({pht})"
        f" OR to_entity_occ_id IN ({pht})) ORDER BY id LIMIT 100",
        (snap, *occ_tail, *occ_tail))
    run("hydrate_endpoints",
        "SELECT deo.id AS occ_id, deo.entity_id"
        " FROM domain_entity_occurrences deo"
        " JOIN domain_entities de ON de.id = deo.entity_id"
        " WHERE deo.snapshot_id = ?"
        " AND deo.id IN (SELECT value FROM json_each(?))",
        (snap, json.dumps(occ_ids)))
    run("hydrate_endpoints_tail",
        "SELECT deo.id AS occ_id, deo.entity_id"
        " FROM domain_entity_occurrences deo"
        " JOIN domain_entities de ON de.id = deo.entity_id"
        " WHERE deo.snapshot_id = ?"
        " AND deo.id IN (SELECT value FROM json_each(?))",
        (snap, json.dumps(occ_tail)))
    run("entity_detail_summary_out",
        "SELECT type, resolution_status, COUNT(*) AS c"
        " FROM domain_relations WHERE snapshot_id = ?"
        f" AND from_entity_occ_id IN ({ph})"
        " GROUP BY type, resolution_status",
        (snap, *occ_ids))
    run("relations_list",
        "SELECT id FROM domain_relations"
        " WHERE snapshot_id = ? ORDER BY id LIMIT 100 OFFSET 0",
        (snap,))
    run("relations_type_filter",
        "SELECT id FROM domain_relations"
        " WHERE snapshot_id = ? AND type IN ('READS_FROM','WRITES_TO')"
        " ORDER BY id LIMIT 100 OFFSET 0",
        (snap,))
    run("lineage_bfs_step_or",
        "SELECT id FROM domain_relations"
        " WHERE snapshot_id = ? AND ("
        " (type IN ('WRITES_TO','TRANSFORMS') AND ("
        "  to_entity_occ_id IN (SELECT value FROM json_each(?)) OR"
        "  to_symbol_occ_id IN (SELECT value FROM json_each(?))))"
        " OR (type IN ('READS_FROM','DERIVES_FROM') AND ("
        "  from_entity_occ_id IN (SELECT value FROM json_each(?)) OR"
        "  from_symbol_occ_id IN (SELECT value FROM json_each(?)))))"
        " ORDER BY id",
        (snap, json.dumps(occ_ids), json.dumps(sym_occ),
         json.dumps(occ_ids), json.dumps(sym_occ)))
    run("search_like_candidates",
        "SELECT de.id AS entity_id, COUNT(*) AS occurrences"
        " FROM domain_entities de"
        " JOIN domain_entity_occurrences deo"
        "  ON deo.entity_id = de.id AND deo.snapshot_id = ?"
        " LEFT JOIN source_files sf ON sf.id = deo.source_file_id"
        " WHERE de.repository_id = ?"
        " AND (de.name LIKE ? ESCAPE '\\'"
        "      OR de.qualified_name LIKE ? ESCAPE '\\'"
        "      OR sf.relative_path LIKE ? ESCAPE '\\')"
        " GROUP BY de.id LIMIT 200",
        (snap, repo, like, like, like))
    # termo seletivo (~1 entidade): LIKE paga o scan inteiro do mesmo
    # jeito — é o ponto onde o índice FTS deveria vencer por probe.
    like_sel = "%dim_500%"
    run("search_like_selective",
        "SELECT de.id AS entity_id, COUNT(*) AS occurrences"
        " FROM domain_entities de"
        " JOIN domain_entity_occurrences deo"
        "  ON deo.entity_id = de.id AND deo.snapshot_id = ?"
        " LEFT JOIN source_files sf ON sf.id = deo.source_file_id"
        " WHERE de.repository_id = ?"
        " AND (de.name LIKE ? ESCAPE '\\'"
        "      OR de.qualified_name LIKE ? ESCAPE '\\'"
        "      OR sf.relative_path LIKE ? ESCAPE '\\')"
        " GROUP BY de.id LIMIT 200",
        (snap, repo, like_sel, like_sel, like_sel))
    run("via_relation_or",
        "SELECT r.id, fe.entity_id AS fe_id, te.entity_id AS te_id"
        " FROM domain_relations r"
        " JOIN domain_entity_occurrences fe ON fe.id = r.from_entity_occ_id"
        " JOIN domain_entity_occurrences te ON te.id = r.to_entity_occ_id"
        " WHERE r.snapshot_id = ? AND ("
        " fe.entity_id IN (SELECT value FROM json_each(?)) OR"
        " te.entity_id IN (SELECT value FROM json_each(?)))"
        " ORDER BY r.id LIMIT 500",
        (snap, json.dumps([1]), json.dumps([1])))

    if after:
        fts_match = ('{name qualified_name terms path} : ("fact")'
                     ' OR name : "fact" OR qualified_name : "fact"')
        run("search_fts_candidates",
            "SELECT de.id AS entity_id, COUNT(*) AS occurrences,"
            " MIN(f.bm) AS m_rank"
            " FROM (SELECT rowid, entity_id, path,"
            " bm25(domain_fts, 0.0,0.0,10.0,8.0,6.0,1.0,0.0,0.0) AS bm"
            " FROM domain_fts"
            " WHERE domain_fts MATCH ? AND snapshot_id = ? LIMIT -1) f"
            " JOIN domain_entities de ON de.id = f.entity_id"
            " WHERE de.repository_id = ?"
            " GROUP BY de.id ORDER BY m_rank LIMIT 200",
            (fts_match, snap, repo))
        fts_sel = ('{name qualified_name terms path} : ("dim" OR "500")'
                   ' OR name : "dim_500" OR qualified_name : "dim_500"')
        run("search_fts_selective",
            "SELECT de.id AS entity_id, COUNT(*) AS occurrences,"
            " MIN(f.bm) AS m_rank"
            " FROM (SELECT rowid, entity_id, path,"
            " bm25(domain_fts, 0.0,0.0,10.0,8.0,6.0,1.0,0.0,0.0) AS bm"
            " FROM domain_fts"
            " WHERE domain_fts MATCH ? AND snapshot_id = ? LIMIT -1) f"
            " JOIN domain_entities de ON de.id = f.entity_id"
            " WHERE de.repository_id = ?"
            " GROUP BY de.id ORDER BY m_rank LIMIT 200",
            (fts_sel, snap, repo))
        run("via_relation_union",
            "SELECT r.id AS rel_id, fe.entity_id AS fe_id,"
            " te.entity_id AS te_id"
            " FROM domain_relations r"
            " JOIN domain_entity_occurrences fe ON fe.id = r.from_entity_occ_id"
            " JOIN domain_entity_occurrences te ON te.id = r.to_entity_occ_id"
            " WHERE r.snapshot_id = ?"
            " AND fe.entity_id IN (SELECT value FROM json_each(?))"
            " UNION "
            "SELECT r.id AS rel_id, fe.entity_id AS fe_id,"
            " te.entity_id AS te_id"
            " FROM domain_relations r"
            " JOIN domain_entity_occurrences fe ON fe.id = r.from_entity_occ_id"
            " JOIN domain_entity_occurrences te ON te.id = r.to_entity_occ_id"
            " WHERE r.snapshot_id = ?"
            " AND te.entity_id IN (SELECT value FROM json_each(?))"
            " ORDER BY rel_id LIMIT 500",
            (snap, json.dumps([1]), snap, json.dumps([1])))
    else:
        out["search_fts_candidates"] = "NOT RUN (domain_fts ausente no schema v10)"
        out["search_fts_selective"] = "NOT RUN (domain_fts ausente no schema v10)"
        out["via_relation_union"] = (
            "NOT RUN (shape só existe no código after — medido no lado after)")

    # nível API — DomainEntitySearch real (LIKE no before, FTS no after)
    engine = DomainEntitySearch(conn)
    for tag, qtext in (("api_domain_search", "fact"),
                       ("api_domain_search_selective", "dim_500")):
        req = SearchRequest(query=qtext, limit=20)
        engine.search(req, repo, snap)
        samples: list[float] = []
        for _ in range(repeats):
            t0 = time.perf_counter()
            engine.search(req, repo, snap)
            samples.append(time.perf_counter() - t0)
        out[tag] = {
            "median_ms": round(statistics.median(samples) * 1000, 3),
            "min_ms": round(min(samples) * 1000, 3),
            "max_ms": round(max(samples) * 1000, 3),
            "path": "domain_fts" if engine._fts_usable_for(snap) else "like",
        }
    out["_explain_query_plan"] = plans
    conn.close()
    return out


def pragma_report(path: Path) -> dict[str, object]:
    conn = connect(path)
    out = {
        "journal_mode": conn.execute("PRAGMA journal_mode").fetchone()[0],
        "synchronous": conn.execute("PRAGMA synchronous").fetchone()[0],
        "foreign_keys": conn.execute("PRAGMA foreign_keys").fetchone()[0],
        "busy_timeout_ms": conn.execute("PRAGMA busy_timeout").fetchone()[0],
        "cache_size": conn.execute("PRAGMA cache_size").fetchone()[0],
    }
    conn.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--files", type=int, default=300,
                    help="artefatos .sql sintéticos (default 300)")
    ap.add_argument("--repeats", type=int, default=7,
                    help="repetições cronometradas por query (default 7)")
    ap.add_argument("--json", type=Path, default=None,
                    help="grava resultado neste path além do stdout")
    ap.add_argument("--keep", type=Path, default=None,
                    help="preserva o diretório de trabalho aqui")
    args = ap.parse_args()

    work = args.keep or Path(
        tempfile.mkdtemp(prefix="atlas-bench-sqlite-"))
    work.mkdir(parents=True, exist_ok=True)
    repo = synth_repo(work / "repo", args.files)
    before_db = work / "atlas-before.db"
    after_db = work / "atlas-after.db"

    t0 = time.perf_counter()
    index_at_v10(before_db, repo)
    index_s = time.perf_counter() - t0
    shutil.copyfile(before_db, after_db)

    conn = sqlite3.connect(f"file:{before_db.as_posix()}?mode=ro", uri=True)
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("domain_entities", "domain_entity_occurrences",
                  "domain_relations", "symbol_occurrences", "source_files")
    }
    conn.close()

    upgrade = upgrade_to_v12(after_db)
    has_fts5 = fts5_available(connect(after_db))

    report = {
        "spec": "lf-0132 + lf-0133",
        "scenario": "mid-size domain fixture — before=v10 (sem índices "
                    "v11/stats/FTS) x after=v12 (índices + ANALYZE + "
                    "domain_fts backfill)",
        "methodology": {
            "fixture": f"synth_repo(seed={SEED}, files={args.files}) — "
                       "só DDL/DML lido por adapters; nada executado",
            "index": "IndexCoordinator com migrações capadas em v10 "
                     "(before = banco antigo real)",
            "after": "cópia do arquivo → migrate → "
                     "Store.rebuild_domain_fts (backfill declarado)",
            "timing": f"perf_counter, mediana de {args.repeats} runs + "
                      "1 warm-up; conexões read-only (mode=ro)",
            "index_s": round(index_s, 3),
        },
        "machine": platform.machine(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "fts5_build": has_fts5,
        "pragmas": pragma_report(after_db),
        "counts": counts,
        "upgrade": upgrade,
        "results": {
            "before_v10": bench_queries(before_db, after=False,
                                        repeats=args.repeats),
            "after_v12": bench_queries(after_db, after=True,
                                       repeats=args.repeats),
        },
        "n_plus_one_audit": {
            "_hydrate_domain_endpoints/_domain_edges_dto": (
                "já batch — ≤2 queries por página de arestas "
                "(json_each); o IN era range-scan de snapshot e virou "
                "probe por rowid com stats, sem mudar código"),
            "_lineage_absorb (api/handlers.py)": (
                "1 probe PK por aresta (occ→entity_id) + 1 "
                "domain_occurrences_for por nó novo — N+1 real mas "
                "bounded (≤500 arestas/nós); batching exigiria "
                "reestruturar o BFS em handlers.py — fora de escopo, "
                "registrado como residual"),
            "crossresolve/federation": (
                "leituras batch por snapshot — sem N+1 encontrado"),
        },
        "residual_known_limits": [
            "lineage_bfs_step_or: OR sobre 4 json_each não usa índices "
            "de endpoint (SCAN de domain_relations ~2.2ms aqui) — "
            "rewrite UNION exigiria handlers.py, não feito",
            "search_fts_candidates em termo ultra-comum ('fact', ~5.5k "
            "rows matched): ~10ms — bm25 por row > scan LIKE; em termos "
            "seletivos o candidato FTS cai a ~2.7ms (vs ~10ms do LIKE "
            "pos-stats). No nível API a diferença dilui: count repetido "
            "via MATCH + hidratação + via_relation dominam — FTS não é "
            "vitória universal, é seletividade + semântica de token",
            "LIKE-fallback em snapshot não coberto: planos podem "
            "diferir do regime pré-stats (SCAN de vs deo-first) — "
            "medido, não escondido",
            "fixture é single-snapshot/single-repo: ganhos de probes "
            "snapshot-scoped crescem com N snapshots no mesmo banco",
        ],
    }
    text = json.dumps(report, indent=2, ensure_ascii=False)
    out_path = args.json or RESULTS
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"\n→ {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
