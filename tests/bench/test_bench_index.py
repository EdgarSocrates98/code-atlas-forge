"""Smoke test do benchmark de indexação (lf-0013).

Corpus pequeno (30 arquivos) — valida que o harness mede todas as fases
e produz o artefato JSON completo, sem custo do benchmark real.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

BENCH = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "bench_index.py"
)
spec = importlib.util.spec_from_file_location("bench_index", BENCH)
assert spec is not None and spec.loader is not None
bench_index = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench_index)


def test_bench_smoke_measures_all_phases(tmp_path: Path) -> None:
    result = bench_index.bench_size(tmp_path, 30)
    for key in (
        "total_s", "discovery_s", "parse_s", "graph_s",
        "persist_residual_s", "noop_reindex_s", "db_size_mb",
        "peak_tracemalloc_mb", "peak_rss_mb",
    ):
        assert key in result, key
        # peak_rss_mb é None no Windows (sem `resource`); demais >= 0
        if result[key] is not None:
            assert result[key] >= 0  # type: ignore[operator]
    assert result["files"] == 30
    # mods + __init__.py dos pacotes gerados — parsed >= mods pedidos
    assert result["files_parsed"] >= 30
    assert result["symbols"] > 0 and result["relations"] > 0
    assert result["noop_status"] == "noop"
    lat = result["query_latency_s"]
    assert lat["n"] == 60 and lat["p95"] >= lat["p50"]  # type: ignore[index]


@pytest.mark.parametrize("n", [1, 5])
def test_synth_corpus_deterministic(tmp_path: Path, n: int) -> None:
    a = bench_index.synth_indexable_corpus(tmp_path / "a", n, seed=7)
    b = bench_index.synth_indexable_corpus(tmp_path / "b", n, seed=7)
    pa = sorted(p.relative_to(a) for p in a.rglob("*.py"))
    pb = sorted(p.relative_to(b) for p in b.rglob("*.py"))
    assert pa == pb
    for rel in pa:
        assert (a / rel).read_bytes() == (b / rel).read_bytes()


def test_synth_corpus_seed_changes_content(tmp_path: Path) -> None:
    a = bench_index.synth_indexable_corpus(tmp_path / "a", 5, seed=1)
    b = bench_index.synth_indexable_corpus(tmp_path / "b", 5, seed=2)
    ha = sorted(p.name for p in a.rglob("*.py"))
    hb = sorted(p.name for p in b.rglob("*.py"))
    assert ha != hb or any(
        (a / p.relative_to(b)).read_bytes() != p.read_bytes()
        for p in b.rglob("*.py")
    )
