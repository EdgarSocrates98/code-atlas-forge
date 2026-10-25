"""Adapter de domínio ``pyspark`` — linhagem estática de DataFrames.

Implementação de ``factory/specs/inbox/lf-0112-cycle7-pyspark-adapter.md``
(Cycle 7, Phase 4). Análise ``ast`` stdlib pura — sem regex-parser,
sem execução de código, sem dependência de ``pyspark`` instalado.

Costuras públicas:

- ``PySparkAdapter`` — facade ``DomainAdapter``;
- ``extract(request, version, base)`` — driver por request;
- ``delegate_sql_literal`` — costura ``spark.sql(literal)`` → adapter
  ``sql`` (lf-0111).
"""

from code_atlas.domains.spark.adapter import (
    ADAPTER_VERSION,
    DOMAIN_ID,
    PySparkAdapter,
    satisfies_protocol,
)
from code_atlas.domains.spark.extract import extract
from code_atlas.domains.spark.sqllit import delegate_sql_literal

__all__ = [
    "ADAPTER_VERSION",
    "DOMAIN_ID",
    "PySparkAdapter",
    "delegate_sql_literal",
    "extract",
    "satisfies_protocol",
]
