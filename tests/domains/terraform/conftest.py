"""Fixtures compartilhadas dos testes do adapter Terraform (lf-0115)."""

from __future__ import annotations

from pathlib import Path

import pytest

from code_atlas.domains.contracts import (
    DiscoveredArtifact,
    DomainExtractionRequest,
)
from code_atlas.domains.terraform import TerraformAdapter

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "terraform_infra"

# Layout da fixture: infra raiz + módulo local + tf.json + tfvars +
# diretório broken/ com HCL malformado (incluído só quando pedido).
GOOD_PATHS = (
    "main.tf",
    "iam.tf",
    "data.tf",
    "variables.tf",
    "backend.tf",
    "modules.tf",
    "queues.tf",
    "dev.tfvars",
    "extra.tf.json",
    "modules/network/main.tf",
    "modules/network/variables.tf",
)

BROKEN_PATHS = ("broken/broken.tf", "broken/stray.tf")

REPO = "git-remote:git@example/infra"


def make_request(
    paths: tuple[str, ...] = GOOD_PATHS,
    *,
    environment: str = "",
) -> DomainExtractionRequest:
    return DomainExtractionRequest(
        repository=REPO,
        artifacts=tuple(DiscoveredArtifact(path=p) for p in paths),
        environment=environment,
    )


@pytest.fixture()
def adapter() -> TerraformAdapter:
    return TerraformAdapter(base_path=FIXTURE)


@pytest.fixture()
def full_result(adapter: TerraformAdapter):
    return adapter.extract(make_request())
