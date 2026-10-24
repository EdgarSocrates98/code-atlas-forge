"""Golden fixtures do Cycle 2 (lf-0013): ground truth congelada em
expected.json commitado — grammar drift vira diff de teste.

Cobre: python (nested, aliases, herança, decorators, unresolved calls),
typescript/tsx (interface, implements, relative imports, jsx refs),
invalid syntax (parsed-with-errors), unicode, empty, repeated names,
no-git (walk mode — os corpora não têm .git), large file, renames.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from eval_harness import CORPUS_DIR, evaluate, snapshot_sets

from code_atlas.indexing import IndexCoordinator
from code_atlas.storage import connect, migrate

CORPORA = sorted(p for p in CORPUS_DIR.iterdir() if p.is_dir())


def _report_json(rep) -> str:
    return json.dumps(
        {
            "corpus": rep.name,
            "symbols": rep.symbols,
            "relations": rep.relations,
            "missing_symbols": rep.missing_symbols,
            "unexpected_symbols": rep.unexpected_symbols,
            "missing_relations": rep.missing_relations,
            "unexpected_relations": rep.unexpected_relations,
            "count_mismatches": rep.count_mismatches,
            "file_status_mismatches": rep.file_status_mismatches,
        },
        indent=2, ensure_ascii=False, sort_keys=True,
    )


@pytest.mark.parametrize("corpus", CORPORA, ids=[c.name for c in CORPORA])
def test_golden_corpus_matches_expected(
    corpus: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rep = evaluate(corpus, tmp_path / f"{corpus.name}.db")
    print(_report_json(rep))  # métricas visíveis em `pytest -s`/falha

    assert not rep.missing_symbols, f"{rep.name}: symbols ausentes"
    assert not rep.unexpected_symbols, f"{rep.name}: symbols inesperados"
    assert not rep.missing_relations, f"{rep.name}: relations ausentes"
    assert not rep.unexpected_relations, f"{rep.name}: relations inesperadas"
    assert not rep.count_mismatches, rep.count_mismatches
    assert not rep.file_status_mismatches, rep.file_status_mismatches

    # qualidade real: P/R perfeito no modo all; recall=1 no subset
    if rep.symbols.get("mode") != "subset":
        assert rep.symbols["precision"] == 1.0
        assert rep.symbols["recall"] == 1.0
    else:
        assert rep.symbols["recall"] == 1.0


def test_metrics_report_resolved_and_syntactic_buckets(
    tmp_path: Path,
) -> None:
    rep = evaluate(CORPUS_DIR / "py_core", tmp_path / "m.db")
    rel = rep.relations
    assert "resolved" in rel and "syntactic" in rel
    assert rel["resolved"]["tp"] > 0 and rel["syntactic"]["tp"] > 0
    # buckets não se misturam: soma dos tps = tp geral
    assert (
        rel["resolved"]["tp"] + rel["syntactic"]["tp"] == rel["all"]["tp"]
    )


def test_determinism_two_independent_runs(tmp_path: Path) -> None:
    """Dois índices independentes do corpus → serialização idêntica."""
    corpus = CORPUS_DIR / "py_core"
    a = snapshot_sets(tmp_path / "a.db", corpus)
    b = snapshot_sets(tmp_path / "b.db", corpus)
    assert a[:3] == b[:3]


def test_rename_rescan_new_snapshot(tmp_path: Path) -> None:
    """Rename de arquivo entre runs: novo snapshot, path novo, símbolo
    lógico muda de qname (módulo novo) — sem vazamento entre snapshots."""
    repo = tmp_path / "renamed"
    repo.mkdir()
    shutil.copytree(CORPUS_DIR / "py_core", repo, dirs_exist_ok=True)

    conn = connect(tmp_path / "r.db")
    migrate(conn)
    coord = IndexCoordinator(conn)
    first = coord.index(repo)
    assert first.status == "success"

    (repo / "pkg" / "util.py").rename(repo / "pkg" / "util2.py")
    second = coord.index(repo)
    assert second.status == "success"
    assert second.snapshot_id != first.snapshot_id

    q2 = {r["q"] for r in conn.execute(
        "SELECT s.qualified_name q FROM symbols s "
        "JOIN symbol_occurrences o ON o.logical_symbol_id=s.id "
        "WHERE o.snapshot_id=?", (second.snapshot_id,))}
    assert "pkg.util2.helper" in q2
    # snapshot novo não carrega ocorrência do path antigo
    paths = {r["p"] for r in conn.execute(
        "SELECT DISTINCT f.relative_path p FROM symbol_occurrences o "
        "JOIN source_files f ON f.id=o.source_file_id "
        "WHERE o.snapshot_id=?", (second.snapshot_id,))}
    assert "pkg/util2.py" in paths and "pkg/util.py" not in paths
    conn.close()


def test_corpus_has_no_git_dir() -> None:
    """Corpora são walk-mode por construção: nenhum .git commitado."""
    for corpus in CORPORA:
        assert not (corpus / ".git").exists(), corpus
