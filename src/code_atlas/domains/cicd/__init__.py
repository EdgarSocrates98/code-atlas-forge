"""Domínio CI/CD (lf-0116, Cycle 7 Phase 8).

GitHub Actions é a plataforma primária; demais plataformas (GitLab CI,
Azure Pipelines, Jenkins, CircleCI) são declared-optional — ver
``OPTIONAL_PLATFORMS``. O parser YAML é um subconjunto stdlib-only
(``yamlmini``) que reporta diagnósticos para construções fora do
suporte em vez de fabricar valores.
"""

from code_atlas.domains.cicd.adapter import (
    ADAPTER_VERSION,
    DOMAIN_ID,
    OPTIONAL_PLATFORMS,
    PLATFORM,
    GitHubActionsAdapter,
)
from code_atlas.domains.cicd.classify import classify_step
from code_atlas.domains.cicd.workflow import (
    JobObs,
    StepObs,
    TriggerObs,
    WorkflowObs,
    parse_workflow,
    sanitize,
)
from code_atlas.domains.cicd.yamlmini import (
    MiniYamlResult,
    YamlDiagnostic,
    YNode,
    parse,
)

__all__ = [
    "ADAPTER_VERSION",
    "DOMAIN_ID",
    "OPTIONAL_PLATFORMS",
    "PLATFORM",
    "GitHubActionsAdapter",
    "JobObs",
    "MiniYamlResult",
    "StepObs",
    "TriggerObs",
    "WorkflowObs",
    "YNode",
    "YamlDiagnostic",
    "classify_step",
    "parse",
    "parse_workflow",
    "sanitize",
]
