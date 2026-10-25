"""Catálogo **fechado** de classes de recurso Terraform (lf-0115).

O prompt Cycle 7 Phase 7 delimita as classes reconhecidas:
Glue, S3, IAM, EMR, Lake Formation, Lambda, API Gateway,
Step Functions, CloudWatch. Todo tipo fora do catálogo é extraído
como ``TERRAFORM_RESOURCE`` genérico com ``resource_class="other"``
— nada é descartado, nada é inventado.
"""

from __future__ import annotations

# resource_class → prefixos de tipo (lowercase). Catálogo delimitado
# pelo prompt: ampliar exige spec + ADR (mesmo regime do vocabulário).
_RESOURCE_CLASS_PREFIXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("glue", ("aws_glue_",)),
    ("s3", ("aws_s3_",)),
    ("iam", ("aws_iam_",)),
    ("emr", ("aws_emr_",)),
    ("lakeformation", ("aws_lakeformation_",)),
    ("lambda", ("aws_lambda_",)),
    ("apigateway", ("aws_api_gateway_", "aws_apigatewayv2_")),
    ("stepfunctions", ("aws_sfn_",)),
    ("cloudwatch", ("aws_cloudwatch_",)),
)

RESOURCE_CLASSES: frozenset[str] = frozenset(
    cls for cls, _ in _RESOURCE_CLASS_PREFIXES
) | {"other"}


def provider_of(resource_type: str) -> str:
    """Prefixo de provider do tipo (``aws_s3_bucket`` → ``aws``)."""
    head, sep, _ = resource_type.partition("_")
    return head if sep else resource_type


def resource_class(resource_type: str) -> str:
    """Classe declarada do tipo — ``other`` para fora do catálogo."""
    t = resource_type.lower()
    for cls, prefixes in _RESOURCE_CLASS_PREFIXES:
        if t.startswith(prefixes):
            return cls
    return "other"
