"""Adapters LSP opt-in (lf-0018).

- Nenhum servidor é instalado, baixado ou spawnado automaticamente.
- Ativação exclusivamente por configuração explícita do usuário
  (``config.toml [resolvers.lsp.servers.<name>]``) — wiring de CLI vem
  no lf-0022.
- Transporte: stdio apenas (sem TCP/socket). Todo request tem timeout
  obrigatório + ``$/cancelRequest``.
- ``spawn.py`` é o único site de subprocess (whitelist do audit).
"""

from code_atlas.resolvers.lsp.adapter import LspResolver
from code_atlas.resolvers.lsp.lifecycle import LspClient, LspError, make_client
from code_atlas.resolvers.lsp.spawn import LspServerSpec, SpawnError
from code_atlas.resolvers.lsp.transport import JsonRpcTransport, TransportError

__all__ = [
    "JsonRpcTransport",
    "LspClient",
    "LspError",
    "LspResolver",
    "LspServerSpec",
    "SpawnError",
    "TransportError",
    "make_client",
]
