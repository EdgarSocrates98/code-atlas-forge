"""Fixtures do adapter ``domains.api`` (lf-0114)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from code_atlas.domains.api import ApiContractAdapter
from code_atlas.domains.contracts import (
    DiscoveredArtifact,
    DomainExtractionRequest,
    DomainExtractionResult,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "api_repo"
REPO = "git-remote:git@example/api-repo"


@pytest.fixture()
def adapter() -> ApiContractAdapter:
    return ApiContractAdapter(workspace_root=FIXTURES)


@pytest.fixture()
def extract(adapter: ApiContractAdapter):
    """``extract([rel paths])`` → resultado real sobre ``api_repo``."""

    def _run(
        paths: list[str],
        *,
        repository: str = REPO,
        with_hash: bool = True,
    ) -> DomainExtractionResult:
        arts = []
        for p in paths:
            raw = (FIXTURES / p).read_bytes()
            arts.append(
                DiscoveredArtifact(
                    path=p,
                    sha256=hashlib.sha256(raw).hexdigest()
                    if with_hash else "",
                )
            )
        return adapter.extract(
            DomainExtractionRequest(repository=repository, artifacts=tuple(arts))
        )

    return _run
