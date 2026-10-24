"""lf-0020 Phase 5: cache versionado de ParseResult + builds transacionais.

Cobre: hit pula tree-sitter (contagem real), invalidação por conteúdo/
versão de parser/config, blob corrompido → miss, isolamento de
snapshot, GC por retenção, rollback de build falho, e auditoria
estática do codec (sem pickle/marshal/eval).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from code_atlas.indexing import IndexCoordinator
from code_atlas.indexing.cache import CacheKey, ParseResultCache
from code_atlas.indexing.reuse import cache_key_for, config_digest
from code_atlas.parsers.engine import ParseEngine
from code_atlas.storage import connect, migrate
from code_atlas.storage.store import Store


@pytest.fixture()
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "atlas.db")
    migrate(conn)
    yield conn
    conn.close()


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "r"
    (r / "pkg").mkdir(parents=True)
    (r / "pkg" / "__init__.py").write_text("")
    (r / "pkg" / "util.py").write_text("def helper():\n    return 1\n")
    (r / "pkg" / "app.py").write_text(
        "from pkg.util import helper\n\n\ndef main():\n    return helper()\n"
    )
    (r / "solo.py").write_text("def isolated():\n    return 0\n")
    return r


def _repo_id(db: sqlite3.Connection, repo: Path) -> int:
    rid = Store(db).repository_id_for(str(repo.resolve()))
    assert rid is not None
    return rid


def _cache(db: sqlite3.Connection, repo: Path) -> ParseResultCache:
    return ParseResultCache(db, _repo_id(db, repo))


class _CountingEngine(ParseEngine):
    """Instrumenta parse real — prova que tree-sitter não é chamado."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def parse(self, rel_path: str, content: bytes):  # type: ignore[override]
        self.calls.append(rel_path)
        return super().parse(rel_path, content)


def test_cache_hit_skips_tree_sitter(db, repo: Path) -> None:
    eng = _CountingEngine()
    out0 = IndexCoordinator(db, engine=eng).index(repo)
    assert out0.status == "success"
    assert len(eng.calls) == 4  # todos parseados na primeira

    # muda só util.py → reparse dele + revalidate de app; reuse vem do cache
    (repo / "pkg" / "util.py").write_text("def helper():\n    return 2\n")
    eng2 = _CountingEngine()
    coord = IndexCoordinator(db, engine=eng2)
    out = coord.index(repo, incremental=True)
    assert out.status == "success"
    # só reparse+revalidate tocam o engine; solo/__init__ vieram do cache
    assert sorted(eng2.calls) == ["pkg/app.py", "pkg/util.py"]
    assert out.stats["files_reused"] == 2
    # identidade: ocorrências existem no snapshot novo (linhas NOVAS)
    snap = out.snapshot_id
    occs = db.execute(
        "SELECT COUNT(*) AS c FROM symbol_occurrences WHERE snapshot_id=?",
        (snap,),
    ).fetchone()["c"]
    assert occs > 0


def test_content_change_invalidates_cache(db, repo: Path) -> None:
    IndexCoordinator(db).index(repo)
    (repo / "solo.py").write_text("def isolated():\n    return 99\n")
    eng = _CountingEngine()
    out = IndexCoordinator(db, engine=eng).index(repo, incremental=True)
    assert out.status == "success"
    assert "solo.py" in eng.calls  # conteúdo novo → parse real


def test_parser_version_bump_invalidates(db, repo: Path) -> None:
    IndexCoordinator(db).index(repo)
    cache = _cache(db, repo)
    key = _first_key(db, repo)
    assert key is not None
    # bump de parser_version → a entrada antiga não responde mais
    bumped = CacheKey(
        path=key.path, content_sha256=key.content_sha256,
        language=key.language, parser_version="9.9.9",
        grammar_version=key.grammar_version,
        extractor_version=key.extractor_version,
        config_digest=key.config_digest,
    )
    assert cache.get(bumped) is None


def test_config_change_invalidates(db, repo: Path) -> None:
    IndexCoordinator(db).index(repo)
    cache = _cache(db, repo)
    key = _first_key(db, repo)
    assert key is not None
    other_cfg = CacheKey(
        path=key.path, content_sha256=key.content_sha256,
        language=key.language, parser_version=key.parser_version,
        grammar_version=key.grammar_version,
        extractor_version=key.extractor_version,
        config_digest="x" * 64,
    )
    assert cache.get(other_cfg) is None


def test_corrupted_blob_is_miss_not_crash(db, repo: Path) -> None:
    IndexCoordinator(db).index(repo)
    db.execute(
        "UPDATE parse_result_cache SET payload = ?"
        " WHERE repository_id = ?",
        ("corrupted-not-json", _repo_id(db, repo)),
    )
    db.commit()
    cache = _cache(db, repo)
    key = _first_key(db, repo)
    assert key is not None
    assert cache.get(key) is None  # checksum diverge → miss honesto
    # blob com checksum forjado mas formato inválido → miss também
    import hashlib

    bad = json.dumps({"format": 999})
    db.execute(
        "UPDATE parse_result_cache SET payload=?, payload_sha256=?"
        " WHERE repository_id=?",
        (bad, hashlib.sha256(bad.encode()).hexdigest(),
         _repo_id(db, repo)),
    )
    db.commit()
    assert cache.get(key) is None


def test_incremental_equals_full_with_cache(db, repo: Path) -> None:
    IndexCoordinator(db).index(repo)
    (repo / "solo.py").write_text("def isolated():\n    return 7\n")
    inc = IndexCoordinator(db).index(repo, incremental=True)
    assert inc.status == "success"
    c1 = _canonical(db, inc.snapshot_id)
    full = IndexCoordinator(db).index(repo, force=True)
    assert full.status == "success"
    assert c1 == _canonical(db, full.snapshot_id)


def test_failed_incremental_keeps_prior_ready(db, repo: Path) -> None:
    out0 = IndexCoordinator(db).index(repo)
    assert out0.status == "success"
    store = Store(db)
    prior = store.latest_ready_snapshot(_repo_id(db, repo))
    assert prior is not None
    # build que explode no meio da transação → rollback + READY intacto
    coord = IndexCoordinator(db)

    def _boom(*a: object, **k: object) -> None:
        raise RuntimeError("boom-mid-build")

    coord._store.insert_inventory_rows = _boom  # type: ignore[method-assign]
    (repo / "solo.py").write_text("def isolated():\n    return 8\n")
    out = coord.index(repo, incremental=True)
    assert out.status == "failed"
    after = store.latest_ready_snapshot(_repo_id(db, repo))
    assert after is not None and after.id == prior.id


def test_gc_respects_retention_and_live_rows(db, repo: Path) -> None:
    IndexCoordinator(db).index(repo)
    cache = _cache(db, repo)
    total = db.execute(
        "SELECT COUNT(*) AS c FROM parse_result_cache"
    ).fetchone()["c"]
    assert total > 0
    # todas as entradas referenciadas pelo último snapshot → GC não tira
    removed = cache.gc(_repo_id(db, repo))
    assert removed == 0
    assert db.execute(
        "SELECT COUNT(*) AS c FROM parse_result_cache"
    ).fetchone()["c"] == total
    # snapshot continua íntegro — GC nunca toca linhas de snapshot
    occs = db.execute(
        "SELECT COUNT(*) AS c FROM symbol_occurrences"
    ).fetchone()["c"]
    assert occs > 0
    # entries mais velhas que KEEP_SNAPSHOTS saem: simular last_used velho
    db.execute(
        "UPDATE parse_result_cache SET last_used_snapshot_id = 0"
    )
    db.commit()
    removed = cache.gc(_repo_id(db, repo))
    assert removed == total


def test_no_pickle_marshal_eval_in_codec() -> None:
    """Auditoria estática: load path nunca usa deserializador perigoso."""
    import re

    src = Path(
        __file__
    ).parents[2] / "src" / "code_atlas" / "indexing" / "cache.py"
    text = src.read_text()
    for banned in (
        r"import\s+pickle", r"import\s+marshal", r"pickle\.loads",
        r"marshal\.loads", r"\beval\s*\(", r"\bexec\s*\(",
        r"__import__",
    ):
        assert not re.search(banned, text), banned


def _first_key(db: sqlite3.Connection, repo: Path) -> CacheKey | None:
    row = db.execute(
        "SELECT path, content_sha256, language, parser_version,"
        " grammar_version, extractor_version, config_digest"
        " FROM parse_result_cache WHERE repository_id = ? LIMIT 1",
        (_repo_id(db, repo),),
    ).fetchone()
    if row is None:
        return None
    return CacheKey(
        path=row["path"], content_sha256=row["content_sha256"],
        language=row["language"], parser_version=row["parser_version"],
        grammar_version=row["grammar_version"],
        extractor_version=row["extractor_version"],
        config_digest=row["config_digest"],
    )


def _canonical(db: sqlite3.Connection, snapshot_id: int) -> dict[str, set]:
    occs = db.execute(
        """
        SELECT s.kind, s.qualified_name, s.language, sf.relative_path,
               o.parse_status, o.content_hash
          FROM symbol_occurrences o
          JOIN symbols s ON s.id = o.logical_symbol_id
          JOIN source_files sf ON sf.id = o.source_file_id
         WHERE o.snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchall()
    nodes = {
        (r["kind"], r["qualified_name"], r["language"],
         r["relative_path"], r["parse_status"], r["content_hash"])
        for r in occs
    }
    rels = db.execute(
        """
        SELECT r.type, r.resolution_status, r.confidence_class,
               sf_from.relative_path AS fp,
               s_from.qualified_name AS fq,
               sf_to.relative_path AS tp,
               s_to.qualified_name AS tq,
               r.to_unresolved
          FROM relations r
          JOIN symbol_occurrences o_from ON o_from.id = r.from_id
          JOIN source_files sf_from ON sf_from.id = o_from.source_file_id
          JOIN symbols s_from ON s_from.id = o_from.logical_symbol_id
          LEFT JOIN symbol_occurrences o_to ON o_to.id = r.to_id
          LEFT JOIN source_files sf_to ON sf_to.id = o_to.source_file_id
          LEFT JOIN symbols s_to ON s_to.id = o_to.logical_symbol_id
         WHERE r.snapshot_id = ?
        """,
        (snapshot_id,),
    ).fetchall()
    edges = {
        (r["type"], r["fp"], r["fq"], r["tp"], r["tq"],
         r["to_unresolved"] or "", r["resolution_status"],
         r["confidence_class"])
        for r in rels
    }
    return {"nodes": nodes, "edges": edges}


def test_config_digest_changes_with_limits() -> None:
    e1 = ParseEngine()
    from code_atlas.parsers.engine import ParseLimits

    e2 = ParseEngine(limits=ParseLimits(timeout_us=1))
    assert config_digest(e1) != config_digest(e2)


def test_cache_key_from_scan_file(db, repo: Path) -> None:
    IndexCoordinator(db).index(repo)
    eng = ParseEngine()
    from code_atlas.discovery import scan

    res = scan(repo)
    f = next(x for x in res.files if x.path == "solo.py")
    adapter = eng.registry.for_path("solo.py")
    assert adapter is not None
    key = cache_key_for(f, adapter, config_digest(eng))
    assert key is not None and key.language == "python"
    assert _cache(db, repo).get(key) is not None
