"""``domains.aws`` — especialização AWS-data (lf-0113, Cycle 7 Phase 5).

Adapter **opcional**: registro explícito em ``domains.registry`` —
nenhum auto-load/auto-enable. Extração estática e evidence-first sobre
artefatos AWS-orientados:

- ``adapter`` — ``AwsDataAdapter`` (contrato ``DomainAdapter``);
- ``emit`` — ``Sink`` acumulador (identidades ``aws:data-catalog``,
  dedup, resoluções honestas);
- ``common`` — ARN/S3 URI/catálogo AWS/spans (puros, sem I/O);
- ``pyfiles`` — AST stdlib de scripts Glue/Spark (nunca executa);
- ``configs`` — shapes JSON/YAML (Glue job defs, EMR steps/cluster,
  triggers), ``spark-submit`` em ``.sh``, ``spark.*`` em ``.conf``;
- ``sqlbridge`` — reuso do parser/extractor ``domains.sql`` com
  re-marcação de identidades para ``aws-data``.
"""

from code_atlas.domains.aws.adapter import (
    ADAPTER_VERSION,
    DOMAIN_ID,
    AwsDataAdapter,
)

__all__ = [
    "ADAPTER_VERSION",
    "DOMAIN_ID",
    "AwsDataAdapter",
]
