# Cycle 7 — Evidência de superfícies (API · CLI · MCP)

> Inventário verificado contra o código em `src/code_atlas/` no branch
> `cycle-7-domain-intelligence-federation` (HEAD `7cde6d3`). Nenhum
> endpoint, comando ou tool é citado sem existir no wire. Convenção:
> parâmetros com teto registram o cap real do código.

## 1. API HTTP `/api/v1` — loopback, read-only, erros tipados

Todas as rotas `GET` são somente leitura sobre a conexão `query_only`;
`POST` existe só para `/context` e `/impact/analyze`. Escopo
`repository_id`/`snapshot_id` é resolvido por `_repo`/`_snapshot`
(READY-only; `snapshot_id` de outro repo → 404 `snapshot-not-found`;
não-READY → 409 `snapshot-not-ready`).

### 1.1 Rotas herdadas (inalteradas no ciclo 7)

| Método | Rota | Parâmetros | Handler |
|---|---|---|---|
| GET | `/health` | — | `_health` |
| GET | `/capabilities` | — | `_capabilities` |
| GET | `/repositories` | — | `_repositories` |
| GET | `/repositories/{id}/snapshots` | — | `_snapshots` |
| GET | `/graph/overview` | `repository_id`, `snapshot_id`, `limit` (def 500, cap 2000) | `_graph_overview` — emite `file_id` por item |
| GET | `/graph/neighbors` | `occurrence_id` (obrigatório), `direction=in|out|both`, `limit` (def 50) | `_graph_neighbors` — DTO completo (`relation_id`, `to_unresolved`, `confidence_class`, `resolver`, `evidence_ids`) |
| GET | `/files/{id}/occurrences` | `snapshot_id`, `limit` (def 200, cap 500), `offset` | `_file_occurrences` — `id:"occ:N"` string + `occurrence_id` numérico |
| GET | `/symbols/{occurrence_id}` | `repository_id`, `snapshot_id` | `_symbol` + `relations_summary` |
| GET | `/evidence/{id}` | `repository_id`, `snapshot_id` | `_evidence` + `referencing_relations` |
| GET | `/search` | `q` (obrig., ≤300), `strategy=lexical|structural|hybrid|vector|graph`, `snapshot_id`, `limit` (def 20, cap 100), `cursor`, `language`, `kind`, `path`, `repository_root`, `repository_id` | `_search` → `SearchEngine` |
| GET | `/index/status` | — | `_index_status` |
| POST | `/context` | body: `task`, `repository_root`, `snapshot_id`, `level`, `budget_tokens` (≤64000), `max_symbols` (≤200) | `_context` |
| POST | `/impact/analyze` | body: `repository_root`, `base_revision`, `target_revision`, `symbol`, `file`, `base_snapshot_id`, `target_snapshot_id`, `include_possible`, `known_only` | `_impact` |
| GET | `/{path}` (não-api) | — | assets `explorer/dist`, SPA fallback |

### 1.2 Busca — parâmetros novos do ciclo 7 (`_search`, lf-0119)

| Parâmetro | Efeito |
|---|---|
| `domain=<id>` | filtra a faceta de entidades de domínio (`de.domain = ?`, único valor — `max_items=1`); resultado em `domain_items`/`domain_total`, nunca fundido com símbolos |
| `workspace=<nome|id>` ou `federation=` | ativa `FederatedSearchEngine`: cada membro pesquisado no próprio `(repo, snapshot)` da view; merge posicional determinístico; items tagueados `repository_id`+`snapshot_id` |
| `pin=<repo>:<snap>[,…]` | overrides de pin da view (`_parse_pins` — formato estrito, senão `bad-param`) |
| `repository_id=<id>` | narrowing por membro no modo federado; 404 `repo-not-found` se inexistente |

### 1.3 Rotas de domínio (lf-0122 — GET, snapshot-scoped)

| Rota | Parâmetros | Semântica |
|---|---|---|
| `/domains` | `repository_id`, `snapshot_id` | inventário `domain`×`kind` com contagens `entities` (repo) e `occurrences` (snapshot) — 0 é resposta válida |
| `/domains/entities` | `domain=a,b` (≤8), `kind=a,b` (≤8), `q` (≤300, `LIKE ESCAPE '\'`), `limit`, `offset` | entidades com ocorrência no snapshot, paginado, `truncated`; inclui `attributes` (JSON), `first_occurrence_id`, `path`, `line`, `extraction_status` |
| `/domains/entities/{id}/neighbors` | `direction=in|out|both`, `limit` | ego da entidade em `domain_relations` por ocorrências; 404 `domain-entity-not-found` fora do repo ativo |
| `/domains/relations` | `type=a,b` (≤20), `limit`, `offset` | arestas do snapshot com endpoints hidratados (`entity`/`symbol`/`unresolved`/`stale`) + `evidence_ids`, `confidence_class`, `resolver` |
| `/domains/lineage` | `table` (obrig., ≤300), `direction=upstream|downstream|both`, `depth` (cap 8), `limit` (def 200, cap 500) | BFS sobre arestas de dados (`READS_FROM`/`DERIVES_FROM` upstream; `WRITES_TO`/`TRANSFORMS` downstream); `nodes` com `distance`+`lane`, `truncated` |

Endpoints de aresta carregam prova: `from`/`to` resolvidos para
`de:{entity_id}`/`occ:{occ_id}` com kind/domínio/path/line; alvo
`unresolved` declara o label; endpoint persistido fora do snapshot
resolvido marca `stale:true` — nunca confundido com resolved.

### 1.4 Rotas de federação (lf-0118/0119 — GET; mutação é CLI-only)

| Rota | Parâmetros | Semântica |
|---|---|---|
| `/federation/workspaces` | — | lista workspaces + contagem de membros |
| `/federation/workspaces/{id}` | — | workspace + membros (`repository_id`, `pinned_snapshot_id`, `root_handle`, `identity_key`) |
| `/federation/workspaces/{id}/view` | `pin=repo:snap[,…]` | view resolvida `{repo→snapshot}` + `view_id` (`fedview:sha256` dos pares ordenados, `None` incluído) + `resolution` por membro |
| `/federation/workspaces/{id}/entities/{logical_key}/occurrences` | `pin` | ocorrências da entidade por membro, cada uma ancorada no snapshot resolvido **daquele** membro |
| `/federation/workspaces/{id}/graph` | `pin`, `depth` (cap 8), `limit` (cap 5000) | BFS sobre `intra` (persistido) + `shared_entity` (computado, `resolver=federation-view`, `relation_id=null`); sementes = `shared_key` em ≥2 membros; `truncated` |
| `/federation/workspaces/{id}/search` | `q`, `strategy`, `domain`, `language`, `kind`, `path`, `limit`, `cursor`, `snapshot_id`, `pin`, `repository_id` | busca federada por workspace — mesmo envelope de `/search` + `view` |

Erros tipados de federação (`_FED_ERROR_MAP`): `workspace-not-found`
404, `federation-too-small` 409, `workspace-exists` 409,
`member-not-found` 404, `repo-not-found` 404, `snapshot-not-found`
404, `snapshot-not-ready` 409, `bad-pin` 400, `bad-key` 400.
`POST/PUT/DELETE /federation/*` → 404 (sem rota por desenho).

## 2. CLI `code-atlas` — superfície completa

| Comando | Novo no ciclo 7 | Notas |
|---|---|---|---|
| `init`, `doctor`, `status`, `index-plan`, `semantic-status` | — | inalterados |
| `index <path>` | **flags novas** | `--domains <ids\|all>` (opt-in, default OFF: `sql,pyspark,aws-data,terraform,cicd,api`) e `--cross-domain` (requer `--domains`) |
| `search`, `symbol`, `references`, `definitions`, `dependencies`, `graph` | — | consultas do snapshot READY |
| `context`, `context-expand` | — | packs v2 + expansão |
| `impact diff|worktree|symbol|tests|contract|report` | seed de entidade | `symbol`/`--ident` aceita ref de entidade de domínio (`dom:` key, row id, display, qname) — semeado silenciosamente quando casa (`cycle-7-cross-domain-impact.md`) |
| `mcp serve|doctor|manifest` | — | servidor stdio |
| `api --host --port` | — | loopback-only |
| `explorer [--open]` | — | API + dist Angular |
| `federation create <name>` | **novo** | cria workspace |
| `federation add <ws> <repo>` | **novo** | membro por repo id / identity_key / root_handle / path (resolve via `repositories` — nunca escaneia) |
| `federation pin <ws> <repo> <snap\|latest>` | **novo** | pin READY/STALE do mesmo repo; `latest` desfaz (unpin) |
| `federation view <ws> [--pin repo:snap,…] [--json]` | **novo** | view resolvida |
| `domains list` | **novo** | adapters registrados/habilitados/versões (read-only; listar não extrai) |
| `domains entities [--repo --domain --kind --q --limit --offset --snapshot]` | **novo** | entidades de domínio do snapshot |
| `domains entity <key>` | **novo** | cartão: `dom:` key | row id | qualified_name | `ns.qname` |
| `domains lineage <key> [--direction up\|down\|both --depth ≤8]` | **novo** | linhagem bounded de dados |

`domains` e `federation` compartilham a camada
`src/code_atlas/domains/cli_surface.py` com o MCP — mesma verdade nos
dois transportes.

## 3. MCP — `code-atlas mcp` (15 tools, read-only)

Tools herdadas: `atlas_search`, `atlas_symbol`, `atlas_references`,
`atlas_dependencies`, `atlas_context`, `atlas_evidence`,
`atlas_status`, `atlas_expand`, `atlas_impact`, `atlas_diff`,
`atlas_test_impact`.

Novas no ciclo 7 (`mcp.py`):

| Tool | Parâmetros | Âncora |
|---|---|---|
| `atlas_domains` | — (sem args) | **global** (`_GLOBAL_TOOLS`) — lista adapters registrados; não extrai |
| `atlas_domain_entity` | `key` (identity_key `dom:…` \| id \| qualified_name), `root` | repo/snapshot |
| `atlas_domain_lineage` | `entity` (key \| id \| qname), `direction=up\|down\|both`, `depth` ≤8, `limit` ≤500, `root` | repo/snapshot |
| `atlas_federation_view` | `workspace` (nome ou id), `pins` (`{repository_id: snapshot_id}`) | **global** — view resolvida |

Resources e prompts inalterados (`atlas://repository`,
`atlas://snapshot`, `atlas://capabilities`, `atlas://graph`,
`atlas://context`, `atlas://symbols`).

## 4. Índice — flags e efeito no snapshot

- `index --domains` registra os adapters no `DomainRegistry` injetado
  no `IndexCoordinator`; versões entram em `analyzer_versions` como
  `domain:{id}` → mudança de conjunto derrota no-op de digest.
- `index --cross-domain` injeta `CrossDomainResolver` — roda após os
  adapters dentro da mesma `BEGIN IMMEDIATE`; stats
  `cross_considered/resolved/ambiguous/edges` em `IndexOutcome.stats`.
- Sem `--domains`: comportamento pré-Cycle-7 byte a byte (zero linhas
  de domínio, zero custo).

## 5. Onde cada superfície está coberta por teste

| Superfície | Suite |
|---|---|---|
| Rotas `/domains*` | `tests/api/test_domain_api.py` |
| Rotas `/federation*` | `tests/api/test_federation_api.py` |
| Busca federada/domínio | `tests/api/test_federated_search.py`, `tests/search/` |
| Engine de federação + CLI | `tests/federation/` (`test_federation.py`, `test_federation_cli.py`) |
| Tools MCP de domínio | `tests/mcp/test_domain_tools.py` |
| Resolução cross-domain | `tests/domains/test_crossresolve.py` (14 casos) |
| Impacto de domínio | `tests/impact/test_domain_impact.py` (8 casos) |
| Context packs de domínio | `tests/context/test_domain_packs.py` (10 casos) |
| Segurança de domínio | `tests/security/test_domain_security.py` |
| Eval por domínio | `tests/evaluation/test_domain_eval.py` + `evaluation/run_eval.py --check` |
| Views Explorer | specs em `explorer/src/app/features/domains/` + `ng test` (219) |
