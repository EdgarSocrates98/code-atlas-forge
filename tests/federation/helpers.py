"""Helpers/fixtures compartilhados dos testes de federação (lf-0118).

Dois repositórios reais indexados no MESMO banco — cada um com os
adapters ``sql`` + ``api`` habilitados — compartilhando:

- o contrato OpenAPI idêntico (``GET /orders`` sob base ``/v1``) →
  endpoint + service + contract com a mesma ``shared_key``;
- a tabela ``mart.orders`` (referenciada nos dois) → mesma
  ``shared_key``;
- ``other.orders`` só no repo B — mesmo nome, namespace distinto:
  NUNCA casa com ``mart.orders``;
- ``mart.v_orders`` só no repo A (view derivando de ``mart.orders``)
  → vizinho intra-snapshot para o BFS do grafo federado.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from code_atlas.domains.api import ApiContractAdapter
from code_atlas.domains.registry import DomainRegistry
from code_atlas.domains.sql import SqlDomainAdapter
from code_atlas.indexing import IndexCoordinator

OPENAPI_SPEC = """\
openapi: 3.0.0
info:
  title: Orders API
  version: 1.0.0
servers:
  - url: https://api.orders.example/v1
paths:
  /orders:
    get:
      operationId: listOrders
      responses:
        '200':
          description: all orders
"""

REPO_A_FILES = {
    "spec/openapi.yaml": OPENAPI_SPEC,
    "q.sql": (
        "CREATE VIEW mart.v_orders AS SELECT * FROM mart.orders;\n"
    ),
}

REPO_B_FILES = {
    "spec/openapi.yaml": OPENAPI_SPEC,
    "q.sql": "SELECT * FROM mart.orders;\nSELECT * FROM other.orders;\n",
    "app/client.py": (
        "import requests\n\n\n"
        "def go():\n"
        "    return requests.get(\"https://api.orders.example/v1/orders\")\n"
    ),
}


def index_repo(conn: sqlite3.Connection, root: Path) -> int:
    """Indexa ``root`` com domínio opt-in — retorna snapshot_id READY."""
    reg = DomainRegistry()
    reg.register(SqlDomainAdapter(base_path=root))
    reg.register(ApiContractAdapter(workspace_root=root))
    out = IndexCoordinator(conn, domain_registry=reg).index(root)
    assert out.status == "success" and out.snapshot_id is not None, out.error
    return out.snapshot_id


def write_repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root
