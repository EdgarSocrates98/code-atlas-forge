"""``TerraformAdapter`` — adapter de domínio Terraform (lf-0115).

Implementa ``DomainAdapter`` sobre o núcleo de ``extract.py``:

- lê **somente** os artefatos do ``DomainExtractionRequest``,
  resolvidos contra ``base_path`` (raiz on-disk do recorte) —
  nunca lê disco fora do request, nunca abre rede, nunca executa
  ``terraform``/state/plan/apply;
- ``extract`` nunca levanta para conteúdo ruim — falhas viram
  ``DomainDiagnostic``;
- teto de confiança ``syntactic``: resolução de identidades é por
  chave canônica dentro do snapshot; o que é externo ao recorte sai
  como ``external``/``unresolved`` — nunca promovido.
"""

from __future__ import annotations

from pathlib import Path

from code_atlas.contracts.enums import ConfidenceClass
from code_atlas.domains.contracts import (
    ArtifactSupport,
    ConfidenceModel,
    DiscoveryRule,
    DomainDiagnostic,
    DomainExtractionRequest,
    DomainExtractionResult,
)
from code_atlas.domains.model import DomainEntityKind, DomainRelationKind
from code_atlas.domains.terraform.extract import DOMAIN_ID, extract

ADAPTER_VERSION = "0.1.0"


class TerraformAdapter:
    """Adapter ``terraform`` — recursos, módulos e IAM de ``.tf``/``.tf.json``.

    ``base_path``: raiz on-disk que resolve os paths relativos de
    ``DiscoveredArtifact``; ``None`` trata os paths como estão
    (absolutos ou relativos ao cwd do caller).
    """

    def __init__(self, base_path: str | Path | None = None) -> None:
        self._base = Path(base_path) if base_path is not None else None

    @property
    def domain_id(self) -> str:
        return DOMAIN_ID

    @property
    def adapter_version(self) -> str:
        return ADAPTER_VERSION

    def supported_artifacts(self) -> ArtifactSupport:
        return ArtifactSupport(
            extensions=frozenset({".tf", ".tfvars"}),
            globs=("*.tf.json", "*.tfvars.json"),
        )

    def entity_kinds(self) -> frozenset[DomainEntityKind]:
        return frozenset(
            {
                DomainEntityKind.TERRAFORM_RESOURCE,
                DomainEntityKind.TERRAFORM_MODULE,
                DomainEntityKind.IAM_ROLE,
            }
        )

    def relation_kinds(self) -> frozenset[str]:
        return frozenset(
            {
                str(DomainRelationKind.DEPENDS_ON),
                str(DomainRelationKind.PROVISIONS),
                str(DomainRelationKind.USES_RESOURCE),
                str(DomainRelationKind.ASSUMES_ROLE),
                str(DomainRelationKind.REFERENCES),
            }
        )

    def confidence_model(self) -> ConfidenceModel:
        # Extração estritamente evidence-first: sem inferência — o que
        # não está no snapshot sai unresolved/external, nunca inventado.
        return ConfidenceModel(
            ceiling=ConfidenceClass.SYNTACTIC,
            default=ConfidenceClass.SYNTACTIC,
            allows_inferred=False,
            notes="static HCL/JSON structural extraction only",
        )

    def discovery_rules(self) -> tuple[DiscoveryRule, ...]:
        return (
            DiscoveryRule(
                glob="*.tf", artifact_kind="tf-configuration",
                notes="HCL configuration files (any depth)",
            ),
            DiscoveryRule(
                glob="*.tf.json", artifact_kind="tf-json",
                notes="JSON-flavoured Terraform configuration",
            ),
            DiscoveryRule(
                glob="*.tfvars", artifact_kind="tf-vars",
                notes="variable assignments — secrets never surfaced",
            ),
            DiscoveryRule(
                glob="*.tfvars.json", artifact_kind="tf-vars-json",
                notes="JSON variable assignments",
            ),
        )

    def extract(
        self, request: DomainExtractionRequest
    ) -> DomainExtractionResult:
        try:
            return extract(request, self.adapter_version, self._base)
        except Exception as exc:  # última linha de defesa — nunca levanta
            return DomainExtractionResult(
                domain_id=self.domain_id,
                adapter_version=self.adapter_version,
                diagnostics=(
                    DomainDiagnostic(
                        code="adapter-failure",
                        severity="error",
                        message=f"terraform adapter failure: {exc!r}",
                        adapter_id=self.domain_id,
                    ),
                ),
            )
