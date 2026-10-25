"""Fixtures dos testes do adapter ``domains.spark`` (lf-0112, Phase 4)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from code_atlas.domains.contracts import (
    DiscoveredArtifact,
    DomainExtractionRequest,
    DomainExtractionResult,
)
from code_atlas.domains.spark import PySparkAdapter

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "pyspark_repo"
REPO = "git-remote:git@example/pyspark-repo"

ALL_PATHS = (
    "broken_job.py",
    "dynamic_job.py",
    "etl_orders.py",
    "notebook_implicit.py",
    "plain_utils.py",
    "sql_job.py",
    "views_job.py",
)


def make_request(
    paths: tuple[str, ...] = ALL_PATHS,
    *,
    repository: str = REPO,
    environment: str = "",
) -> DomainExtractionRequest:
    """Request sobre a fixture real — mesmo desenho dos outros domínios."""
    return DomainExtractionRequest(
        repository=repository,
        artifacts=tuple(DiscoveredArtifact(path=p) for p in paths),
        environment=environment,
    )


@pytest.fixture()
def adapter() -> PySparkAdapter:
    return PySparkAdapter(base_path=FIXTURE)


@pytest.fixture()
def full_result(adapter: PySparkAdapter) -> DomainExtractionResult:
    return adapter.extract(make_request())


@pytest.fixture()
def extract(adapter: PySparkAdapter):
    """``extract([rel paths])`` → resultado real sobre ``pyspark_repo``."""

    def _run(
        paths: list[str] | tuple[str, ...] = ALL_PATHS,
        *,
        repository: str = REPO,
        environment: str = "",
    ) -> DomainExtractionResult:
        arts = tuple(
            DiscoveredArtifact(
                path=p,
                sha256=hashlib.sha256(
                    (FIXTURE / p).read_bytes()
                ).hexdigest(),
            )
            for p in paths
        )
        return adapter.extract(
            DomainExtractionRequest(
                repository=repository,
                artifacts=arts,
                environment=environment,
            )
        )

    return _run


@pytest.fixture()
def extract_inline(tmp_path: Path):
    """``extract_inline(src, name)`` → resultado sobre fonte inline."""

    def _run(
        source: str,
        *,
        name: str = "job.py",
        repository: str = "repo:inline",
    ) -> DomainExtractionResult:
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(source, encoding="utf-8")
        ad = PySparkAdapter(base_path=tmp_path)
        return ad.extract(
            DomainExtractionRequest(
                repository=repository,
                artifacts=(DiscoveredArtifact(path=name),),
            )
        )

    return _run


@pytest.fixture()
def rels():
    """``rels(result, type)`` → pares ``(origem, alvo)`` display."""

    def _pairs(res, rtype: str) -> set[tuple[str, str]]:
        out: set[tuple[str, str]] = set()
        for r in res.relations:
            if r.relation_type != rtype:
                continue
            frm = (
                r.from_identity.display()
                if r.from_identity else r.from_symbol or "?"
            )
            to = (
                r.to_identity.display()
                if r.to_identity
                else r.to_unresolved or r.to_symbol
            )
            out.add((frm, to))
        return out

    return _pairs


@pytest.fixture()
def diag_codes():
    """``diag_codes(result)`` → códigos de diagnóstico em ordem."""

    def _codes(res) -> list[str]:
        return [d.code for d in res.diagnostics]

    return _codes
