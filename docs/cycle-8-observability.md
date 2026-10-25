# Cycle 8 Phase 15 — Observabilidade & Diagnósticos (lf-0141)

Observabilidade local-first para o operador: diagnóstico honesto do que
está indexado, timing de estágios do indexador, progresso de `index`
em stderr e um guard opt-in de queries lentas. **Sem telemetria, sem
egress, sem scans** — tudo é metadado persistido ou `stat()` de arquivo.

## 1. O que já existia (auditoria)

Antes desta fase o código já tinha — a fase só expôs/instrumentou:

| Superfície | Existia? | Onde |
|---|---|---|
| `GET /api/v1/health` | sim | `api/handlers.py` |
| `GET /api/v1/index/status` | sim | `api/handlers.py` (contagens por snapshot READY) |
| `GET /api/v1/capabilities` | sim | `api/handlers.py` (search: FTS5, vetorial, estratégias) |
| Envelope de erro tipado | sim | `ApiError` → `{"error": {code,message,details}}`; exceções genéricas viram `internal-error` **sem traceback no wire** |
| Logging local com redaction | sim | `observability/logging.py` — stderr, `CODE_ATLAS_LOG_FORMAT=json`, `CODE_ATLAS_LOG_LEVEL`, `RedactionFilter` (home paths, tokens, PEM) |
| `stage_ms_*` por estágio | sim | `indexing/coordinator.py` mede com `time.monotonic()` desde o Cycle 2 e persiste em `index_jobs.counts` — porém **inacessível** fora do JSON de `counts` |
| Timing de SQL nos handlers | parcial | `handle()` não media a requisição inteira; `search_scoped` não era medido |

Lacunas confirmadas pela auditoria: nenhum painel único do operador;
`stage_ms` enterrado em blob JSON; `index` silencioso até o fim;
nenhum guard de lentidão.

## 2. O que foi adicionado

### 2.1 `GET /api/v1/diagnostics` — painel do operador

Aditivo em `api/handlers.py` (um handler novo na tabela de rotas, GET-only;
POST devolve o 404 estruturado de sempre). Read-only — roda na conexão
`query_only` — e barato: metadados + `sqlite_master` + `stat()` de
arquivo. **Nunca** varre workspace nem re-executa adapter/resolver.

Forma da resposta (campos reais, servidor `1.0.0rc1`):

```json
{
  "status": "ok",
  "service": "code-atlas-api",
  "api_version": 1,
  "version": "1.0.0rc1",
  "readonly": true,
  "uptime_s": 0.107,
  "runtime": {"python": "3.12.3", "sqlite": "3.45.1"},
  "schema_version": 12,
  "database": {
    "path": "/abs/atlas.db", "size_bytes": 294912, "wal_bytes": 0,
    "journal_mode": "wal", "page_count": 72
  },
  "fts": {
    "fts5_build": true,
    "fts_symbols": true, "fts_occurrences": true, "domain_fts": true
  },
  "search": {"strategies": ["lexical","structural","graph","hybrid","vector"],
             "fts5": true, "vector": true, "detail": "…"},
  "repositories": [
    {
      "repository_id": 1, "identity_key": "path:/abs/repo",
      "root_handle": "/abs/repo",
      "snapshots": {"ready": 1, "failed": 0, "stale": 0, "building": 0},
      "latest_ready": 1, "latest_ready_created_at": "…",
      "counts": {"file_inventory": 1, "symbol_occurrences": 3,
                 "relations": 2, "evidence": 1,
                 "domain_entity_occurrences": 0, "domain_relations": 0},
      "analyzer_versions": {"parser:python": "…", "scan_mode": "no_git"},
      "last_job": {"id": 1, "status": "success",
                   "started_at": "…", "finished_at": "…",
                   "stage_ms": {"discovery": 6, "parse": 0, "…": 0}}
    }
  ],
  "federation": {"workspaces": 0},
  "adapters": {"domain": [ …inventário built-in… ], "error": ""},
  "observability": {"log_level": "WARNING", "log_format": "text",
                    "slow_ms": null}
}
```

Decisões honestas:

- **`fts.fts5_build`** é probed numa conexão `:memory:` descartável —
  a probe de FTS5 cria vtable temporária, o que falharia com
  `attempt to write a readonly database` na conexão da API.
  `fts_symbols`/`fts_occurrences`/`domain_fts` são **presença real**
  de tabela no banco (`sqlite_master`), não suposição.
- **`analyzer_versions`** vem do snapshot READY persistido — é a fonte
  autoritativa de quais parsers/resolvers produziram aquele índice,
  não o inventário do processo atual.
- **`adapters`** é inventário de metadados built-in
  (`domains.cli_surface.adapters_report`); falha de importação vira o
  campo `adapters.error` em vez de derrubar o endpoint.
- Bancos pré-v9/v10 podem não ter `domain_*`/`federation_workspaces` —
  presença é conferida em `sqlite_master` antes de contar; ausência
  rende `0`, não erro.
- `uptime_s` usa `server.started_at` (`time.monotonic()` na criação
  do `_AtlasHttpd`).

### 2.2 Structured index logging + progresso `--verbose`

`stage_ms` já era persistido (`index_jobs.counts` → `stage_ms_<stage>`)
— agora acessível via `repositories[].last_job.stage_ms` no endpoint.

`IndexCoordinator.index()` ganhou `progress: ProgressFn | None = None`
(default `None` preserva o comportamento anterior — zero custo).
Eventos emitidos quando o caller registra callback:

- `"stage"`: `stage=<nome>`, `elapsed_ms=<int>` — um por estágio
  (`discovery`, `hashing`, `invalidation`, `parse`, `graph_build`,
  `semantic`, `domains`, `validation`, `persist_rows`, `fk_check`,
  `publish_gc`, `persist_publish`), mesmos nomes das chaves persistidas;
- `"parse-progress"`: `parsed=<n>`, `total=<n>`, `files_per_sec=<f>` —
  a cada 64 arquivos e uma vez no final;
- `"noop"`: `snapshot_id=<id>` — quando o scan não acha mudança.

`code-atlas index --verbose` liga o callback e imprime linhas
`[index] <evento> k=v …` **em stderr** — stdout continua JSON puro
parseável (verificado em `tests/cli/test_verbose.py`).

### 2.3 Slow-query guard — `CODE_ATLAS_SLOW_MS`

Opt-in por env, teto em milissegundos (aceita fracionário, ex. `0.5`).
Ausente/inválido/`<=0` = desligado, custo de um `os.environ.get` por
chamada. Implementado em `observability/logging.py`:

- `slow_ms()` — parse do teto;
- `log_if_slow(logger, label, t0, detail)` — emite `DEBUG` quando
  `monotonic()-t0` excede o teto;
- `timed(label)` — decorator que mede em `try/finally` (loga também
  o caminho de exceção).

Fronteiras instrumentadas (sem reescrever middleware):

- `api.handlers.handle()` — a requisição inteira, com a rota em `detail`;
- `SearchEngine.search_scoped` — `@timed("search")` cobre lexical,
  estrutural, grafo, híbrida e vetorial.

As linhas saem pelo logger local existente — mesmos redaction filter e
formato (`text`/`json`). Exemplo (`CODE_ATLAS_SLOW_MS=50`):

```
DEBUG code_atlas.api: slow request elapsed_ms=87.3 threshold_ms=50 /diagnostics
```

### 2.4 Honestidade de erro — verificada, não mudada

O envelope tipado já cobria os casos pedidos; a fase adicionou testes
que travam o comportamento:

- parâmetro mal-formado (`repo=abc`, `snapshot=-1`, `pin=1:2:3`) →
  **400** `{"error": {"code": "bad-param", …}}`;
- snapshot inexistente em `/revisions`, `/search`, `/graph` →
  **404** `snapshot-not-found` (não 500, não traceback);
- exceção não-mapeada → **500** `internal-error` com mensagem
  truncada, sem stack no wire;
- `POST /api/v1/diagnostics` → **404** `route-not-found` (GET-only).

## 3. NOT RUN — declarado

- **Metrics export / Prometheus / OTLP**: fora de escopo por design —
  local-first não tem egress. `NOT RUN`.
- **Distributed tracing (OpenTelemetry)**: `NOT RUN` — mesma razão.
- **Scan de conteúdo indexado pelo endpoint**: `NOT RUN` e
  intencionalmente não implementado — `/diagnostics` lê só metadados.
- **Timing por-query individual de SQL**: `NOT RUN` — o guard mede a
  fronteira (request/search), não cada `execute()`; granularidade
  interna ficaria para uma fase de profiling dedicada.
- **Benchmark de latência do `/diagnostics`**: `NOT RUN` — medido só
  como teste funcional (resposta < 1 s em banco de fixture); não há
  número de produção porque este ambiente é local-first.
- **Alteração de golden datasets**: nenhuma.
- **Execução de código/jobs de repositórios indexados**: nenhuma.

## 4. Verificação (números medidos neste ambiente)

| Check | Resultado |
|---|---|
| `pytest tests/api tests/indexing tests/search tests/cli tests/observability tests/storage` | **399 passed**, 1 skipped (25.3 s) |
| `pytest` (suíte completa) | **1510 passed**, 1 skipped (77.7 s) |
| `ruff check` (arquivos tocados) | All checks passed |
| `mypy` (full, 167 fontes) | **Success: no issues found** |

O SQL-audit (`test_no_dynamic_sql_in_execute_calls`) reprovou uma
versão inicial do endpoint que interpolava `{tbl}` num `execute()` —
corrigido para o mapa estático `_DIAG_COUNT_QUERIES` (mesmo padrão de
`_COUNT_QUERIES`); o audit agora passa.

## 5. Arquivos

| Arquivo | Mudança |
|---|---|
| `src/code_atlas/observability/logging.py` | `ENV_SLOW_MS`, `slow_ms()`, `log_if_slow()`, `timed()` |
| `src/code_atlas/observability/__init__.py` | re-exports |
| `src/code_atlas/api/server.py` | `started_at` para uptime |
| `src/code_atlas/api/handlers.py` | `_diagnostics` + rota `/diagnostics` + `log_if_slow` em `handle()` |
| `src/code_atlas/search/engine.py` | `@timed("search")` em `search_scoped` |
| `src/code_atlas/indexing/coordinator.py` | `ProgressFn`, `_emit_progress`, `_files_per_sec`, `_parse_tick`, `_parse_tick_final`, eventos por estágio |
| `src/code_atlas/cli.py` | `index --verbose` → `_index_progress` em stderr |
| `tests/api/test_diagnostics.py` | novo — endpoint, honestidade de erro, GET-only |
| `tests/observability/test_slow_guard.py` | novo — teto on/off, DEBUG, caminho de exceção |
| `tests/indexing/test_progress.py` | novo — eventos stage/parse-progress/noop |
| `tests/cli/test_verbose.py` | novo — stderr progress + stdout JSON |
