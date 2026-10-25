"""Adapter de domínio SQL (lf-0111, Cycle 7 Phase 3).

Linhagem estática ANSI/Athena(Trino)/Spark em stdlib puro — tokenizer
(``tokens``) + parser recursivo-descendente (``parser``) + extração para
o grafo unificado de domínio (``extract``). Nenhuma execução de SQL,
nenhuma conexão externa, nenhuma dependência nova.

Estados honestos: ``SELECT *`` limita linhagem de coluna declaradamente;
aliases/expressões não-resolvíveis viram ``ambiguous``/``unresolved`` —
nunca ``resolved``; ``${}``/``{{}}``/``EXEC`` são ``dynamic`` —
diagnosticados, nunca fabricados.
"""

from code_atlas.domains.sql.adapter import (
    ADAPTER_VERSION,
    DOMAIN_ID,
    SqlDomainAdapter,
    looks_like_sql,
)
from code_atlas.domains.sql.parser import ParsedFile, detect_dialect, parse_file

__all__ = [
    "ADAPTER_VERSION",
    "DOMAIN_ID",
    "ParsedFile",
    "SqlDomainAdapter",
    "detect_dialect",
    "looks_like_sql",
    "parse_file",
]
