"""Adapter de domínio ``api`` (lf-0114, Cycle 7 Phase 6).

Extrai ``APIContract``/``APIService``/``APIEndpoint`` de specs
OpenAPI/Swagger e de rotas declaradas em código (FastAPI, Express),
mais relações ``CALLS_ENDPOINT`` de consumidores HTTP literais.

Fronteiras preservadas: nenhum import de ``tree_sitter`` (AST via
``ParseEngine``), nenhum ``urllib`` (split de URL local), nenhuma
dependência nova (parser YAML de subconjunto próprio, ``json`` stdlib),
nenhuma execução de código alvo nem acesso a rede.
"""

from code_atlas.domains.api.adapter import ADAPTER_VERSION, ApiContractAdapter
from code_atlas.domains.api.yaml_subset import (
    YamlDocument,
    YamlIssue,
    YamlValue,
    parse_yaml_subset,
)

__all__ = [
    "ADAPTER_VERSION",
    "ApiContractAdapter",
    "YamlDocument",
    "YamlIssue",
    "YamlValue",
    "parse_yaml_subset",
]
