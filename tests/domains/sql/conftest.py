"""Fixtures dos testes do adapter ``domains.sql`` (lf-0111, Phase 3)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from code_atlas.domains.contracts import (
    DiscoveredArtifact,
    DomainExtractionRequest,
    DomainExtractionResult,
)
from code_atlas.domains.sql import SqlDomainAdapter
from code_atlas.domains.sql.extract import extract_parsed
from code_atlas.domains.sql.parser import parse_file

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "sql_warehouse"
REPO = "git-remote:git@example/warehouse"

PIPELINE_PATHS = (
    "landing/01_landing.sql",
    "staging/02_staging.sql",
    "mart/03_mart.sql",
)

BROKEN_PATH = "broken/99_broken.sql"


def make_request(
    paths: tuple[str, ...] = PIPELINE_PATHS,
    *,
    environment: str = "",
) -> DomainExtractionRequest:
    """Request sobre a fixture real — mesmo desenho dos outros domínios."""
    return DomainExtractionRequest(
        repository=REPO,
        artifacts=tuple(DiscoveredArtifact(path=p) for p in paths),
        environment=environment,
    )


@pytest.fixture()
def adapter() -> SqlDomainAdapter:
    return SqlDomainAdapter(base_path=FIXTURE)


@pytest.fixture()
def full_result(adapter: SqlDomainAdapter) -> DomainExtractionResult:
    return adapter.extract(make_request())


@pytest.fixture()
def extract(adapter: SqlDomainAdapter):
    """``extract([rel paths])`` → resultado real sobre ``sql_warehouse``."""

    def _run(
        paths: list[str] | tuple[str, ...] = PIPELINE_PATHS,
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
def inline():
    """``inline(sql)`` → FileExtraction de SQL inline (cenário pontual)."""

    def _run(
        text: str, *, path: str = "inline.sql", repository: str = "repo"
    ):
        return extract_parsed(
            parse_file(text),
            repository=repository,
            root="",
            environment="",
            path=path,
            sha=hashlib.sha256(text.encode()).hexdigest(),
            adapter_version="0.0.0-test",
        )

    return _run


@pytest.fixture()
def rels():
    """``rels(fx|result, type)`` → pares ``(origem, alvo)`` display."""

    def _pairs(fx, rtype: str) -> set[tuple[str, str]]:
        out: set[tuple[str, str]] = set()
        for r in fx.relations:
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
    """``diag_codes(fx|result)`` → códigos de diagnóstico em ordem."""

    def _codes(fx) -> list[str]:
        return [d.code for d in fx.diagnostics]

    return _codes
