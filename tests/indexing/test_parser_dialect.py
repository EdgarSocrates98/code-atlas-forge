"""lf-0130 — regressão end-to-end do fix de dialeto TS (Cycle 8 Ph.3).

Mini-repo com os constructs do bug P1 do axios (``import x =
require('y')`` + ``export =``): antes do fix, uma IMPORTS com alvo
vazio derrubava o snapshot INTEIRO no validate_graph. Também cobre:
um arquivo que emite relação degenerada nunca falha os demais
(fail-open por arquivo) e a resolução de imports relativos em
``__init__.py``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from code_atlas.graph.builder import GraphBuilder
from code_atlas.graph.validation import validate_graph
from code_atlas.indexing import IndexCoordinator
from code_atlas.parsers.contracts import (
    ExtractedRelation,
    ParseResult,
    SourceSpan,
)
from code_atlas.storage import connect, migrate


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    yield conn
    conn.close()


def _relations(db: sqlite3.Connection, snapshot_id: int) -> list[dict]:
    return [
        dict(r)
        for r in db.execute(
            "SELECT r.type t, sf.qualified_name fq, "
            "st.qualified_name tq, r.to_unresolved tu, "
            "r.resolution_status rs FROM relations r "
            "JOIN symbol_occurrences o1 ON o1.id=r.from_id "
            "JOIN symbols sf ON sf.id=o1.logical_symbol_id "
            "LEFT JOIN symbol_occurrences o2 ON o2.id=r.to_id "
            "LEFT JOIN symbols st ON st.id=o2.logical_symbol_id "
            "WHERE r.snapshot_id=?",
            (snapshot_id,),
        )
    ]


def test_import_equals_require_and_export_equals_index(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """Fixture do bug P1: `import =` + `export =` — o snapshot inteiro
    publica READY e as arestas IMPORTS saem com alvo real."""
    repo = tmp_path / "axioslike"
    repo.mkdir()
    (repo / "index.d.cts").write_text(
        "declare const axios: number;\nexport = axios;\n"
    )
    (repo / "use.ts").write_text("import axios = require('axios');\n")
    (repo / "mod.ts").write_text("export function f(): number {\n  return 1;\n}\n")

    out = IndexCoordinator(db).index(repo)
    assert out.status == "success", out.error

    rels = _relations(db, out.snapshot_id)
    imports = [r for r in rels if r["t"] == "IMPORTS"]
    assert any(
        r["fq"] == "use" and r["tu"] == "axios" for r in imports
    )
    # nenhuma aresta sem alvo textual persistida
    assert all(r["tq"] is not None or r["tu"] for r in rels)


def test_degenerate_relation_drops_edge_not_snapshot(tmp_path: Path) -> None:
    """Fail-open no builder: um ParseResult com IMPORTS vazio (adapter
    defeituoso) vira diagnóstico — o grafo segue válido e as demais
    arestas persistem (a falha não pode mais matar o snapshot)."""
    from code_atlas.discovery.scanner import scan
    from code_atlas.parsers import ParseEngine

    repo = tmp_path / "r"
    repo.mkdir()
    (repo / "ok.ts").write_text("export function ok() {}\n")
    s = scan(repo)
    res = ParseEngine().parse(
        "ok.ts", (repo / "ok.ts").read_bytes())
    bad = ParseResult(
        path="bad.ts",
        language="typescript",
        parser="fake",
        parser_version="0",
        grammar_version="0",
        content_hash="x",
        symbols=res.symbols[:1],  # módulo
        relations=(
            ExtractedRelation(
                "IMPORTS", "bad", "",
                SourceSpan(0, 0, 1, 0, 1, 0),
            ),
        ),
    )
    g = GraphBuilder().build("r", s, [res, bad])
    assert validate_graph(g) == []
    assert any(d.code == "empty-edge-target" for d in g.diagnostics)
    assert not any(
        e.type == "IMPORTS" and not e.to_text and e.to_id is None
        for e in g.edges
    )


def test_init_py_relative_import_resolves(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """`from .api import x` dentro de `pkg/__init__.py` — `.` é o
    próprio pacote: IMPORTS resolve para ``pkg.api`` (antes ficava
    unresolved — bug auditado no requests real)."""
    repo = tmp_path / "pyre"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "__init__.py").write_text(
        "from .api import helper\nfrom . import util\n"
    )
    (repo / "pkg" / "api.py").write_text("def helper(): return 1\n")
    (repo / "pkg" / "util.py").write_text("def u(): return 2\n")

    out = IndexCoordinator(db).index(repo)
    assert out.status == "success", out.error
    rels = _relations(db, out.snapshot_id)
    imports = [r for r in rels if r["t"] == "IMPORTS"]
    assert any(
        r["fq"] == "pkg" and r["tq"] == "pkg.api"
        and r["rs"] == "resolved"
        for r in imports
    ), imports
    assert any(
        r["fq"] == "pkg" and r["tq"] == "pkg.util"
        and r["rs"] == "resolved"
        for r in imports
    ), imports


def test_one_bad_file_does_not_abort_others(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """Arquivo com sintaxe quebrada → parse-failed auditável; os demais
    arquivos publicam símbolos e arestas normalmente."""
    repo = tmp_path / "mixed"
    repo.mkdir()
    (repo / "bad.ts").write_text("import { from ;\nexport function x( {\n")
    (repo / "ok1.ts").write_text(
        "import {f} from './ok2';\nexport function a() { return f(); }\n"
    )
    (repo / "ok2.ts").write_text("export function f(): number {\n  return 1;\n}\n")

    out = IndexCoordinator(db).index(repo)
    assert out.status == "success", out.error
    statuses = {
        r["p"]: r["s"]
        for r in db.execute(
            "SELECT f.relative_path p, o.parse_status s "
            "FROM symbol_occurrences o "
            "JOIN source_files f ON f.id=o.source_file_id "
            "WHERE o.snapshot_id=?",
            (out.snapshot_id,),
        )
    }
    assert statuses["ok1.ts"] == "parsed"
    assert statuses["ok2.ts"] == "parsed"
    assert statuses["bad.ts"] in ("parsed-with-errors", "parse-failed")
