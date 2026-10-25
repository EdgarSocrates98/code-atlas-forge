"""Fixtures dos testes do adapter ``domains.aws`` (lf-0113, Phase 5)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from code_atlas.domains.aws import AwsDataAdapter
from code_atlas.domains.contracts import (
    DiscoveredArtifact,
    DomainExtractionRequest,
    DomainExtractionResult,
)

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "aws_repo"
REPO = "git-remote:git@example/aws-analytics"

GLUE_ETL = "glue/jobs/orders_etl.py"
ICEBERG = "glue/jobs/iceberg_upsert.py"
ATHENA_RUNNER = "glue/jobs/athena_runner.py"
CROSS_ACCOUNT = "glue/jobs/cross_account.py"
BROKEN = "glue/jobs/broken.py"
ATHENA_SQL = "athena/queries/daily_report.sql"
EMR_STEPS = "emr/steps.json"
EMR_CLUSTER = "emr/cluster.yaml"
EMR_BAD = "emr/bad.json"
JOB_DEF = "jobs/nightly-etl.json"
TRIGGER = "jobs/trigger.json"
SUBMIT_SH = "scripts/submit.sh"
SPARK_CONF = "conf/spark-defaults.conf"
NOTES = "docs/notes.txt"

ALL_PATHS = (
    GLUE_ETL,
    ICEBERG,
    ATHENA_RUNNER,
    CROSS_ACCOUNT,
    BROKEN,
    ATHENA_SQL,
    EMR_STEPS,
    EMR_CLUSTER,
    EMR_BAD,
    JOB_DEF,
    TRIGGER,
    SUBMIT_SH,
    SPARK_CONF,
    NOTES,
)


def make_request(
    paths: tuple[str, ...] = ALL_PATHS,
    *,
    repository: str = REPO,
    environment: str = "",
) -> DomainExtractionRequest:
    return DomainExtractionRequest(
        repository=repository,
        artifacts=tuple(DiscoveredArtifact(path=p) for p in paths),
        environment=environment,
    )


@pytest.fixture()
def adapter() -> AwsDataAdapter:
    return AwsDataAdapter(base_path=FIXTURE)


@pytest.fixture()
def full_result(adapter: AwsDataAdapter) -> DomainExtractionResult:
    return adapter.extract(make_request())


@pytest.fixture()
def extract(adapter: AwsDataAdapter):
    """``extract([rel paths])`` → resultado real sobre ``aws_repo``."""

    def _run(
        paths: list[str] | tuple[str, ...],
        *,
        repository: str = REPO,
        environment: str = "",
    ) -> DomainExtractionResult:
        arts = tuple(
            DiscoveredArtifact(
                path=p,
                sha256=hashlib.sha256((FIXTURE / p).read_bytes()).hexdigest(),
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
def rels():
    """``rels(result, type)`` → conjunto ``(origem, alvo, status)``."""

    def _pairs(fx: DomainExtractionResult, rtype: str) -> set[tuple[str, str, str]]:
        out: set[tuple[str, str, str]] = set()
        for r in fx.relations:
            if r.relation_type != rtype:
                continue
            frm = r.from_identity.display() if r.from_identity else r.from_symbol or "?"
            to = r.to_identity.display() if r.to_identity else r.to_unresolved or r.to_symbol
            status = r.resolution.status.value if r.resolution else ""
            out.add((frm, to, status))
        return out

    return _pairs


@pytest.fixture()
def ents():
    """``ents(result, kind)`` → entidades por kind value."""

    def _by_kind(fx: DomainExtractionResult, kind: str):
        return [e for e in fx.entities if e.kind.value == kind]

    return _by_kind


@pytest.fixture()
def diag_codes():
    """``diag_codes(result)`` → códigos de diagnóstico em ordem."""

    def _codes(fx: DomainExtractionResult) -> list[str]:
        return [d.code for d in fx.diagnostics]

    return _codes
