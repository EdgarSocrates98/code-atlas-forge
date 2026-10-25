#!/usr/bin/env python3
"""Benchmark dos adapters de domínio (Cycle 7 Ph.17, lf-0125).

Mede o custo real de ``DomainAdapter.extract`` sobre as fixtures
versionadas do repo — ``sql_warehouse``, ``pyspark_repo``, ``aws_repo``,
``api_repo``, ``terraform_infra``, ``cicd_repo`` — mais:

- um caso de **escala sintética**: N arquivos ``.sql`` gerados por
  helper determinístico (seed fixo) e extraídos pelo ``SqlDomainAdapter``;
- o **delta de indexação** com ``domain_registry`` ligado/desligado sobre
  uma fixture pequena (``crossdomain_ws``): mesmo repo, bancos frescos,
  ``IndexCoordinator`` idêntico — só o registry opt-in muda.

Honestidade: números são medidos, não metas. Fixtures são pequenas por
desenho (ground truth de testes) — o caso sintético existe justamente
para observar custo por artefato fora do regime de fixture. ``extract``
releu os artefatos do request dentro do workspace; nada é executado.

Uso::

    .venv/bin/python benchmarks/bench_domains.py
    .venv/bin/python benchmarks/bench_domains.py --repeats 9 --scale-files 200
    .venv/bin/python benchmarks/bench_domains.py --json out.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import statistics
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from code_atlas.domains.api import ApiContractAdapter
from code_atlas.domains.aws import AwsDataAdapter
from code_atlas.domains.cicd import GitHubActionsAdapter
from code_atlas.domains.cli_surface import builtin_registry
from code_atlas.domains.contracts import (
    DiscoveredArtifact,
    DomainAdapter,
    DomainExtractionRequest,
    DomainExtractionResult,
)
from code_atlas.domains.spark import PySparkAdapter
from code_atlas.domains.sql import SqlDomainAdapter
from code_atlas.domains.terraform import TerraformAdapter
from code_atlas.indexing import IndexCoordinator
from code_atlas.storage import connect, migrate

SEED = 20261024
REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures"
RESULTS = REPO_ROOT / "benchmarks" / "results" / "domains-latest.json"

# domain_id → (fixture, fábrica vinculada ao diretório da fixture)
FIXTURE_ADAPTERS: dict[
    str, tuple[str, Callable[[Path], DomainAdapter]]
] = {
    "sql": ("sql_warehouse", lambda root: SqlDomainAdapter(base_path=root)),
    "pyspark": ("pyspark_repo", lambda root: PySparkAdapter(base_path=root)),
    "aws-data": ("aws_repo", lambda root: AwsDataAdapter(base_path=root)),
    "api": ("api_repo", lambda root: ApiContractAdapter(workspace_root=root)),
    "terraform": (
        "terraform_infra", lambda root: TerraformAdapter(base_path=root)
    ),
    "cicd": (
        "cicd_repo", lambda root: GitHubActionsAdapter.for_workspace(root)
    ),
}


def fixture_artifacts(
    adapter: DomainAdapter, root: Path
) -> tuple[DiscoveredArtifact, ...]:
    """Todos os arquivos da fixture que ``supported_artifacts`` aceita.

    Varredura real do diretório (não lista hardcoded) — entram inclusive
    os artefatos propositalmente quebrados das fixtures, cujo custo de
    diagnóstico faz parte da extração honesta.
    """
    support = adapter.supported_artifacts()
    arts: list[DiscoveredArtifact] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.is_symlink():
            continue
        rel = p.relative_to(root).as_posix()
        if not support.matches(rel):
            continue
        arts.append(
            DiscoveredArtifact(
                path=rel,
                sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
            )
        )
    return tuple(arts)


def _request(
    artifacts: tuple[DiscoveredArtifact, ...], repository: str
) -> DomainExtractionRequest:
    return DomainExtractionRequest(
        repository=repository, artifacts=artifacts, snapshot="bench"
    )


def _time_extract(
    adapter: DomainAdapter,
    request: DomainExtractionRequest,
    repeats: int,
) -> tuple[list[float], DomainExtractionResult]:
    """1 warmup + ``repeats`` medições; retorna (segundos, último resultado)."""
    adapter.extract(request)  # warmup — caches de FS/singletons
    times: list[float] = []
    result = adapter.extract(request)
    for _ in range(repeats):
        t = time.monotonic()
        result = adapter.extract(request)
        times.append(time.monotonic() - t)
    return times, result


def _stats(times_s: list[float]) -> dict[str, float]:
    ms = [t * 1000 for t in times_s]
    return {
        "median_ms": round(statistics.median(ms), 3),
        "min_ms": round(min(ms), 3),
        "max_ms": round(max(ms), 3),
        "p95_ms": round(
            sorted(ms)[min(len(ms) - 1, int(len(ms) * 0.95))], 3
        ),
        "repeats": len(ms),
    }


def _result_counts(res: DomainExtractionResult) -> dict[str, int]:
    return {
        "entities": len(res.entities),
        "relations": len(res.relations),
        "evidence": len(res.evidence),
        "diagnostics": len(res.diagnostics),
    }


def bench_adapter(
    domain_id: str, fixture_name: str, repeats: int
) -> dict[str, object]:
    """``extract`` completo sobre a fixture versionada do domínio."""
    root = FIXTURES / fixture_name
    factory = FIXTURE_ADAPTERS[domain_id][1]
    adapter = factory(root)
    artifacts = fixture_artifacts(adapter, root)
    request = _request(artifacts, f"git-remote:bench/{fixture_name}")
    times, result = _time_extract(adapter, request, repeats)
    out: dict[str, object] = {
        "domain": domain_id,
        "adapter_version": adapter.adapter_version,
        "fixture": fixture_name,
        "artifacts": len(artifacts),
        "artifact_bytes": sum(
            (root / a.path).stat().st_size for a in artifacts
        ),
        **_stats(times),
        **_result_counts(result),
    }
    if artifacts:
        out["ms_per_artifact_median"] = round(
            out["median_ms"] / len(artifacts), 3  # type: ignore[operator]
        )
    return out


# --- escala sintética ----------------------------------------------------

_SQL_TABLE = """\
CREATE TABLE {ns}.t{i} (
    id BIGINT,
    payload_{i} STRING,
    ts TIMESTAMP
);
"""

_SQL_JOB = """\
-- job{i}: staging → mart (linhagem sintética determinística)
INSERT INTO mart.fact_{i} (id, payload, k)
SELECT s.id, s.payload_{i}, k.k{i}
FROM staging.t{i} s
JOIN mart.dim_{k} k ON k.id = s.id
WHERE s.ts > TIMESTAMP '2026-01-01 00:00:00';

CREATE VIEW mart.v_{i} AS
SELECT id, payload_{i} FROM mart.fact_{i};
"""


def synth_sql_tree(root: Path, n_files: int, seed: int = SEED) -> Path:
    """``n_files`` ``.sql`` determinísticos — DDL + DML com linhagem real."""
    rng = random.Random(seed)
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n_files):
        body = _SQL_TABLE.format(ns=rng.choice(("staging", "mart")), i=i)
        body += _SQL_JOB.format(i=i, k=rng.randint(0, max(0, i - 1)))
        (root / f"job{i:03}.sql").write_text(body, encoding="utf-8")
    return root


def bench_sql_scale(n_files: int, repeats: int, tmp: Path) -> dict[str, object]:
    root = synth_sql_tree(tmp / f"sql-scale-{n_files}", n_files)
    adapter = SqlDomainAdapter(base_path=root)
    artifacts = fixture_artifacts(adapter, root)
    request = _request(artifacts, "git-remote:bench/sql-scale")
    times, result = _time_extract(adapter, request, repeats)
    return {
        "domain": "sql",
        "adapter_version": adapter.adapter_version,
        "case": "synthetic-scale",
        "artifacts": len(artifacts),
        "artifact_bytes": sum(
            (root / a.path).stat().st_size for a in artifacts
        ),
        **_stats(times),
        **_result_counts(result),
        "ms_per_artifact_median": round(
            statistics.median([t * 1000 for t in times]) / n_files, 3
        ),
    }


# --- index com/sem domínio -----------------------------------------------


def bench_index_domains(repeats: int, tmp: Path) -> dict[str, object]:
    """Mesmo repo indexado com ``domain_registry`` on/off.

    Banco fresco por repetição (snapshot novo a cada run — nunca no-op).
    Reporta total e o estágio ``domains`` isolado via ``stage_ms_*`` do
    job stats, além das contagens ``domain_*`` persistidas.
    """
    root = FIXTURES / "crossdomain_ws"
    out: dict[str, object] = {"fixture": "crossdomain_ws"}
    for label, with_domains in (("off", False), ("on", True)):
        totals: list[float] = []
        last_stats: dict[str, int] = {}
        for i in range(repeats):
            conn = connect(tmp / f"idx-{label}-{i}.db")
            migrate(conn)
            reg = builtin_registry(root) if with_domains else None
            coord = IndexCoordinator(conn, domain_registry=reg)
            t = time.monotonic()
            outcome = coord.index(root)
            totals.append(time.monotonic() - t)
            conn.close()
            if outcome.status != "success":
                raise RuntimeError(
                    f"index {label} failed: {outcome.error}"
                )
            last_stats = dict(outcome.stats)
        out[label] = {
            **_stats(totals),
            "stage_ms_domains": last_stats.get("stage_ms_domains", 0),
            "domain_entities": sum(
                v for k, v in last_stats.items()
                if k.startswith("domain_") and k.endswith("_entities")
            ),
            "domain_relations": sum(
                v for k, v in last_stats.items()
                if k.startswith("domain_") and k.endswith("_relations")
            ),
            "domain_adapters": last_stats.get("domain_adapters", 0),
            "files": last_stats.get("files", 0),
        }
    on = out["on"]["median_ms"]  # type: ignore[index]
    off = out["off"]["median_ms"]  # type: ignore[index]
    out["delta_median_ms"] = round(on - off, 3)
    return out


def main() -> int:
    p = argparse.ArgumentParser(
        description="Benchmark dos adapters de domínio (Cycle 7)"
    )
    p.add_argument("--repeats", type=int, default=7)
    p.add_argument("--scale-files", type=int, default=50,
                   help="arquivos .sql do caso de escala sintética")
    p.add_argument("--json", type=Path, default=RESULTS,
                   help="destino do resultado (default: results/domains-latest.json)")
    args = p.parse_args()

    report: dict[str, object] = {
        "tool": "code-atlas bench_domains",
        "seed": SEED,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "note": (
            "números medidos, não metas — fixtures são pequenas por "
            "desenho; escala sintética observa custo por artefato"
        ),
        "adapters": [],
        "synthetic_scale": {},
        "index_domains": {},
    }
    for domain_id, (fixture_name, _) in sorted(FIXTURE_ADAPTERS.items()):
        report["adapters"].append(  # type: ignore[union-attr]
            bench_adapter(domain_id, fixture_name, args.repeats)
        )
    with tempfile.TemporaryDirectory(prefix="atlas-bench-dom-") as td:
        tmp = Path(td)
        report["synthetic_scale"] = bench_sql_scale(
            args.scale_files, args.repeats, tmp
        )
        report["index_domains"] = bench_index_domains(args.repeats, tmp)

    text = json.dumps(report, indent=2, sort_keys=True)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(text + "\n", encoding="utf-8")
    print(f"written: {args.json}")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
