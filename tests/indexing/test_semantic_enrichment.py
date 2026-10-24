"""lf-0027 — integração real dos resolvers semânticos no pipeline.

Arestas que o syntactic deixa ``unresolved`` são re-tentadas via
``ResolverCoordinator`` (factories opt-in). Verifica:

- promoção: unresolved → resolved com proveniência do resolver;
- preservação: ambiguous/unresolved/external nunca degradados;
- resiliência: exceção de resolver ou factory vira diagnóstico —
  o índice nunca falha por resolver;
- determinismo: mesmo input + mesmo resolver → mesmo grafo;
- bootstrap: ``[resolvers]`` do config constrói factories SCIP/LSP
  apenas quando explicitamente habilitados.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path

import pytest

from code_atlas.config import WorkspaceConfig
from code_atlas.graph.identity import symbol_node_id
from code_atlas.indexing import IndexCoordinator
from code_atlas.resolvers.bootstrap import resolver_factories
from code_atlas.resolvers.capabilities import ResolverCapability
from code_atlas.resolvers.contracts import (
    ResolutionRequest,
    ResolutionResult,
    ResolvedReference,
    ResolverProvenance,
)
from code_atlas.storage import connect, migrate

_REPO_FILES = {
    "pkg/__init__.py": "",
    "pkg/util.py": "def helper():\n    return 1\n",
    # `helper` não é importado nem definido localmente → syntactic
    # deixa unresolved; `mystery` ninguém resolve.
    "pkg/engine.py": (
        "def run():\n"
        "    a = helper()\n"
        "    b = mystery()\n"
        "    return a + b\n"
    ),
}

_HELPER_NID = symbol_node_id("python", "function", "pkg.util.helper")


def _repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    for rel, content in _REPO_FILES.items():
        p = r / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return r


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    yield conn
    conn.close()


def _rels(db: sqlite3.Connection) -> list[sqlite3.Row]:
    db.row_factory = sqlite3.Row
    return db.execute(
        """
        SELECT type, to_unresolved, resolution_status,
               confidence_class, resolver
        FROM relations
        """
    ).fetchall()


class _FakeResolver:
    """Resolver determinístico de teste: promove `helper` para o nid
    real de ``pkg.util.helper``; demais nomes ficam como vieram."""

    resolver_id = "fake-semantic"
    resolver_version = "0.0.1"

    def capabilities(self) -> ResolverCapability:
        return ResolverCapability(
            resolver_id=self.resolver_id,
            tier=1,  # SEMANTIC_INDEX — acima do syntactic
            languages=frozenset({"python"}),
            relation_types=frozenset({"CALLS"}),
        )

    def resolve(self, request: ResolutionRequest) -> ResolutionResult:
        ref = None
        if request.name == "helper":
            ref = ResolvedReference(
                status="resolved",
                confidence_class="resolved",
                provenance=ResolverProvenance(
                    resolver_id=self.resolver_id,
                    resolver_version=self.resolver_version,
                    evidence_tier=1,
                    detail="fake deterministic hit",
                ),
                target_id=_HELPER_NID,
                target_text="pkg.util.helper",
            )
        elif request.name == "mystery":
            ref = ResolvedReference(
                status="unresolved",
                confidence_class="heuristic",
                provenance=ResolverProvenance(
                    resolver_id=self.resolver_id,
                    resolver_version=self.resolver_version,
                    evidence_tier=1,
                ),
            )
        return ResolutionResult(request=request, reference=ref)


_RUN_NID = symbol_node_id("python", "function", "pkg.engine.run")


class _ConflictingResolver(_FakeResolver):
    """lf-0035: resolve `helper` para um ALVO DISTINTO persistível
    (``pkg.engine.run``) — exercita o caminho de conflito."""

    resolver_id = "conflicting"

    def resolve(self, request: ResolutionRequest) -> ResolutionResult:
        result = super().resolve(request)
        if result.reference is not None:
            ref = dataclasses.replace(
                result.reference, target_id=_RUN_NID
            )
            return ResolutionResult(request=request, reference=ref)
        return result


class _ExplodingResolver(_FakeResolver):
    resolver_id = "exploding"

    def resolve(self, request: ResolutionRequest) -> ResolutionResult:
        raise RuntimeError("boom")


def _index(
    db: sqlite3.Connection, repo: Path, factories: tuple = ()
) -> object:
    return IndexCoordinator(
        db, resolver_factories=factories
    ).index(repo)


# -- comportamento sem resolvers (regressão) ---------------------------------


def test_no_factories_zero_semantic_stats(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    out = _index(db, _repo(tmp_path))
    assert out.status == "success"
    assert out.stats["semantic_attempted"] == 0
    assert out.stats["semantic_enriched"] == 0
    rows = _rels(db)
    calls = {r["to_unresolved"]: r for r in rows if r["type"] == "CALLS"}
    assert calls["helper"]["resolution_status"] == "unresolved"
    assert calls["helper"]["resolver"] != "fake-semantic"


# -- promoção ----------------------------------------------------------------


def test_unresolved_edge_promoted_by_resolver(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    out = _index(
        db, _repo(tmp_path), factories=(lambda u: _FakeResolver(),)
    )
    assert out.status == "success"
    assert out.stats["semantic_enriched"] >= 1
    rows = _rels(db)
    calls = {r["to_unresolved"]: r for r in rows if r["type"] == "CALLS"}
    promoted = calls["helper"]
    assert promoted["resolution_status"] == "resolved"
    assert promoted["confidence_class"] == "resolved"
    assert promoted["resolver"] == "fake-semantic"


def test_promoted_edge_targets_persisted_node(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    _index(db, _repo(tmp_path), factories=(lambda u: _FakeResolver(),))
    db.row_factory = sqlite3.Row
    row = db.execute(
        """
        SELECT s.qualified_name
        FROM relations r
        JOIN symbol_occurrences o ON o.id = r.to_id
        JOIN symbols s ON s.id = o.logical_symbol_id
        WHERE r.type = 'CALLS' AND r.to_unresolved = 'helper'
        """
    ).fetchone()
    assert row is not None
    assert row["qualified_name"] == "pkg.util.helper"


# -- preservação de honestidade ----------------------------------------------


def test_unresolved_result_preserves_edge(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    _index(db, _repo(tmp_path), factories=(lambda u: _FakeResolver(),))
    rows = _rels(db)
    calls = {r["to_unresolved"]: r for r in rows if r["type"] == "CALLS"}
    mystery = calls["mystery"]
    assert mystery["resolution_status"] == "unresolved"
    # resolver externo tentou mas não promoveu — aresta fica honesta
    assert mystery["to_unresolved"] == "mystery"


def test_resolved_edge_confirmed_by_higher_tier(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """lf-0035: `helper` resolvido pelo syntactic é VERIFICADO pelo
    resolver de tier 1; mesmo alvo → proveniência sobe, aresta segue
    resolved e evidência ``confirmed`` é persistida."""
    repo = tmp_path / "repo"
    for rel, content in _REPO_FILES.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    (repo / "pkg" / "engine.py").write_text(
        "from pkg.util import helper\n\n"
        "def run():\n"
        "    return helper()\n"
    )
    _index(db, repo, factories=(lambda u: _FakeResolver(),))
    rows = _rels(db)
    calls = [r for r in rows if r["type"] == "CALLS"]
    assert calls, "expected CALLS edges"
    for r in calls:
        # lf-0035: tier superior CONFIRMA o mesmo alvo → proveniência
        # sobe para o resolver de tier mais alto; aresta segue resolved.
        assert r["resolver"] == "fake-semantic"
        assert r["resolution_status"] == "resolved"
    confirmed = db.execute(
        "SELECT COUNT(*) FROM evidence WHERE validation_status='confirmed'"
    ).fetchone()[0]
    assert confirmed >= 1


def test_conflict_with_higher_tier_becomes_ambiguous(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """lf-0035: resolver de tier superior diverge → aresta vira
    ``ambiguous``, alvo original preservado (nunca sobrescrito)."""
    repo = tmp_path / "repo"
    for rel, content in _REPO_FILES.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    (repo / "pkg" / "engine.py").write_text(
        "from pkg.util import helper\n\n"
        "def run():\n"
        "    return helper()\n"
    )
    out = _index(db, repo, factories=(lambda u: _ConflictingResolver(),))
    assert out.stats["semantic_conflicts"] >= 1
    row = db.execute(
        "SELECT r.resolution_status, s.qualified_name "
        "FROM relations r "
        "JOIN symbol_occurrences o ON o.id = r.to_id "
        "JOIN symbols s ON s.id = o.logical_symbol_id "
        "WHERE r.type='CALLS'"
    ).fetchone()
    assert row is not None
    assert row["resolution_status"] == "ambiguous"
    # to_id preserva a ocorrência de pkg.util.helper (alvo original)
    assert "helper" in row["qualified_name"]
    helper_ev = db.execute(
        "SELECT COUNT(*) FROM evidence WHERE validation_status='conflict'"
    ).fetchone()[0]
    assert helper_ev >= 1


def test_resolved_edge_untouched_without_higher_tier(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    """Sem resolver de tier superior configurado, arestas resolved
    passam pelo pipeline sem custo nem alteração (lf-0035)."""
    repo = tmp_path / "repo"
    for rel, content in _REPO_FILES.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    (repo / "pkg" / "engine.py").write_text(
        "from pkg.util import helper\n\n"
        "def run():\n"
        "    return helper()\n"
    )
    out = _index(db, repo)
    assert out.stats.get("semantic_verified", 0) == 0
    calls = [r for r in _rels(db) if r["type"] == "CALLS"]
    assert calls and all(
        r["resolution_status"] == "resolved" for r in calls
    )


# -- resiliência ---------------------------------------------------------------


def test_resolver_exception_becomes_diagnostic(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    out = _index(
        db, _repo(tmp_path), factories=(lambda u: _ExplodingResolver(),)
    )
    assert out.status == "success"
    assert out.stats["semantic_diagnostics"] >= 1
    rows = _rels(db)
    calls = {r["to_unresolved"]: r for r in rows if r["type"] == "CALLS"}
    assert calls["helper"]["resolution_status"] == "unresolved"


def test_factory_exception_is_diagnostic_not_crash(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    def bad_factory(universe: object) -> object:
        raise RuntimeError("scip exploded")

    out = _index(db, _repo(tmp_path), factories=(bad_factory,))
    assert out.status == "success"


# -- determinismo --------------------------------------------------------------


def test_enrichment_deterministic(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    repo = _repo(tmp_path)
    f = (lambda u: _FakeResolver(),)
    _index(db, repo, factories=f)
    snap1 = db.execute(
        "SELECT id FROM snapshots WHERE status='READY' ORDER BY id DESC"
    ).fetchone()[0]
    canon1 = db.execute(
        "SELECT type, to_unresolved, resolution_status, confidence_class,"
        " resolver FROM relations ORDER BY 1,2"
    ).fetchall()
    out2 = _index(db, repo, factories=f)
    # noop: manifest+analyzers idênticos → nada re-publicado
    assert out2.noop
    canon2 = db.execute(
        "SELECT type, to_unresolved, resolution_status, confidence_class,"
        " resolver FROM relations ORDER BY 1,2"
    ).fetchall()
    assert canon1 == canon2
    assert snap1 is not None


# -- bootstrap de config --------------------------------------------------------


def test_config_resolvers_table_parsed(tmp_path: Path) -> None:
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[workspace]\nroot = "."\n\n'
        '[resolvers.scip]\nindex_path = "index.scip"\n'
    )
    loaded = WorkspaceConfig.load(cfg)
    assert loaded.resolvers is not None
    assert loaded.resolvers["scip"]["index_path"] == "index.scip"


def test_config_resolvers_rejects_non_table(tmp_path: Path) -> None:
    from code_atlas.config import ConfigError

    cfg = tmp_path / "config.toml"
    cfg.write_text('resolvers = "oops"\n\n[workspace]\nroot = "."\n')
    with pytest.raises(ConfigError):
        WorkspaceConfig.load(cfg)


def test_bootstrap_no_table_no_factories(tmp_path: Path) -> None:
    factories, errors = resolver_factories(None, tmp_path)
    assert factories == [] and errors == []


def test_bootstrap_scip_missing_file_is_error(tmp_path: Path) -> None:
    factories, errors = resolver_factories(
        {"scip": {"index_path": "index.scip"}}, tmp_path
    )
    assert factories == []
    assert errors and "index.scip" in errors[0]


def test_bootstrap_lsp_disabled_by_default(tmp_path: Path) -> None:
    factories, errors = resolver_factories(
        {
            "lsp": {
                "servers": {
                    "pyright": {
                        "command": ["pyright-langserver", "--stdio"],
                        "languages": ["python"],
                        # enabled ausente → default False → ignorado
                    }
                }
            }
        },
        tmp_path,
    )
    assert factories == [] and errors == []


def test_bootstrap_lsp_bad_spec_is_error(tmp_path: Path) -> None:
    factories, errors = resolver_factories(
        {"lsp": {"servers": {"bad": {"languages": ["python"]}}}},
        tmp_path,
    )
    assert factories == []
    assert errors and "command" in errors[0]


# -- end-to-end SCIP --------------------------------------------------------------


def test_scip_enrichment_end_to_end(tmp_path: Path) -> None:
    """Índice SCIP real (fixture) promove o call `helper` — evidência
    tier=1 (semantic index) gravada na relação persistida."""
    import sys

    sys.path.insert(0, str(Path(__file__).parent.parent / "resolvers"))
    from scip_fixture import (
        document,
        index_bytes,
        occurrence,
        slr,
        symbol_info,
    )

    from code_atlas.resolvers.scip.importer import (
        ROLE_DEFINITION,
    )

    repo = _repo(tmp_path)
    pkg = "scip-python . repo 0.1.0"
    sym_helper = f"{pkg} `pkg/util.py`/helper()."
    doc_util = document(
        "pkg/util.py",
        occs=[occurrence(sym_helper, slr(0, 4, 10), roles=ROLE_DEFINITION)],
        syms=[symbol_info(sym_helper, kind=17)],
    )
    doc_engine = document(
        "pkg/engine.py",
        occs=[
            # call site helper() — linha 2 do arquivo (0-based: 1)
            occurrence(sym_helper, slr(1, 8, 14), roles=0),
        ],
    )
    scip_file = repo / "index.scip"
    scip_file.write_bytes(
        index_bytes(repo.as_uri(), [doc_util, doc_engine])
    )
    factories, errors = resolver_factories(
        {"scip": {"index_path": "index.scip"}}, repo
    )
    assert not errors and len(factories) == 1

    db = connect(tmp_path / "atlas.db")
    migrate(db)
    try:
        out = IndexCoordinator(
            db, resolver_factories=tuple(factories)
        ).index(repo)
        assert out.status == "success"
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT to_unresolved, resolution_status, resolver"
            " FROM relations WHERE type='CALLS'"
        ).fetchall()
    finally:
        db.close()
    calls = {r["to_unresolved"]: r for r in rows}
    assert calls["helper"]["resolution_status"] == "resolved"
    assert calls["helper"]["resolver"] == "scip"


# -- lf-0030: índice SCIP novo invalida relações -------------------------------


def test_scip_index_change_invalidates_index_identity(
    tmp_path: Path,
) -> None:
    """resolver_versions entra em analyzer_versions — scip atualizado
    muda a identidade do índice (conservador: invalida tudo)."""
    repo = _repo(tmp_path)
    scip_file = repo / "index.scip"
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent.parent / "resolvers"))
    from scip_fixture import document, index_bytes
    scip_file.write_bytes(
        index_bytes(repo.as_uri(), [document("pkg/util.py")]))
    versions: dict = {}
    factories, _errors = resolver_factories(
        {"scip": {"index_path": "index.scip"}}, repo, versions)
    assert "resolver:scip" in versions

    db = connect(tmp_path / "atlas.db")
    migrate(db)
    try:
        coord = IndexCoordinator(
            db, resolver_factories=tuple(factories),
            resolver_versions=versions)
        out1 = coord.index(repo)
        assert out1.status == "success"
        # scip regenerado (conteúdo diferente → sha diferente)
        scip_file.write_bytes(
            index_bytes(repo.as_uri(),
                        [document("pkg/util.py"),
                         document("pkg/engine.py")]))
        versions2: dict = {}
        factories2, _ = resolver_factories(
            {"scip": {"index_path": "index.scip"}}, repo, versions2)
        assert versions2["resolver:scip"] != versions["resolver:scip"]
        coord2 = IndexCoordinator(
            db, resolver_factories=tuple(factories2),
            resolver_versions=versions2)
        out2 = coord2.index(repo)
        # identidade mudou → NÃO é noop
        assert not out2.noop
        assert out2.status == "success"
    finally:
        db.close()
