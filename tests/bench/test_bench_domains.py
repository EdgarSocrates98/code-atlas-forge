"""Smoke test do benchmark de domínio (lf-0125, Cycle 7 Ph.17).

Valida que o harness mede cada adapter sobre sua fixture, gera o caso
de escala sintética e produz o JSON completo — sem custo do bench real.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

BENCH = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "bench_domains.py"
)
spec = importlib.util.spec_from_file_location("bench_domains", BENCH)
assert spec is not None and spec.loader is not None
bench_domains = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench_domains)


@pytest.mark.parametrize(
    "domain",
    ["sql", "pyspark", "aws-data", "api", "terraform", "cicd"],
)
def test_adapter_fixture_bench_smoke(domain: str) -> None:
    fixture = bench_domains.FIXTURE_ADAPTERS[domain][0]
    result = bench_domains.bench_adapter(domain, fixture, repeats=2)
    assert result["artifacts"] > 0
    assert result["median_ms"] >= 0 and result["p95_ms"] >= result["min_ms"]  # type: ignore[operator]
    assert result["repeats"] == 2


def test_synth_sql_tree_deterministic(tmp_path: Path) -> None:
    a = bench_domains.synth_sql_tree(tmp_path / "a", 8, seed=7)
    b = bench_domains.synth_sql_tree(tmp_path / "b", 8, seed=7)
    pa = sorted(p.relative_to(a) for p in a.rglob("*.sql"))
    pb = sorted(p.relative_to(b) for p in b.rglob("*.sql"))
    assert pa == pb and len(pa) == 8
    for rel in pa:
        assert (a / rel).read_bytes() == (b / rel).read_bytes()


def test_sql_scale_and_index_toggle(tmp_path: Path) -> None:
    scale = bench_domains.bench_sql_scale(6, repeats=2, tmp=tmp_path)
    assert scale["artifacts"] == 6
    assert scale["entities"] > 0 and scale["relations"] > 0

    idx = bench_domains.bench_index_domains(repeats=1, tmp=tmp_path)
    off, on = idx["off"], idx["on"]
    assert off["files"] > 0 and on["files"] == off["files"]  # type: ignore[index]
    assert off["domain_adapters"] == 0 and off["domain_entities"] == 0  # type: ignore[index]
    assert on["domain_adapters"] > 0 and on["domain_entities"] > 0  # type: ignore[index]
    # números medidos, sem meta — apenas sanidade de formato
    assert on["median_ms"] >= 0 and "delta_median_ms" in idx  # type: ignore[operator]
