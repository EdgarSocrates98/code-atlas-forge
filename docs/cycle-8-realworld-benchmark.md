# Cycle 8 — Real-world benchmark (Phase 1)

Spec: `factory/specs/inbox/lf-0128-cycle8-realworld-benchmark.md`
Baseline: `405a1ad` · Runner: `benchmarks/realworld/run_benchmark.py`
Resultado completo (máquina): `benchmarks/results/realworld-latest.json`
Run: `2026-10-25T14:21:44Z` · wall total do suite: **13.637s**

## Ambiente

Linux 7.0.0-38-generic x86_64 · 16 cores · Python 3.12.3 · code-atlas 0.1.0
Index via subprocesso real `code-atlas index <repo> --full --json` com
`CODE_ATLAS_DATA_DIR`/`CODE_ATLAS_CONFIG_DIR` isolados (baseline
syntactic-only, sem resolvers opt-in) e `PYTHONHASHSEED=0`.
API via `python -u -m code_atlas api --port 0` real; 3 warmups + 25 requests
por endpoint, latência client-side.

## Corpus — 5 repos OSS reais (clone `--depth 1`, SHA-pinado)

| id | SHA (verificado `sha_match=true`) | arquivos | bytes | forma |
|---|---|---|---|---|
| jaffle_shop | `fd7bfacae4f497ff044a6a0275268676bf1b64c3` | 22 | 636K | dbt/SQL |
| terraform-aws-s3-bucket | `9afa4fc8c8f1e0b3fd913474c1bef8d0c454b235` | 147 | 1.8M | Terraform |
| requests | `611c6162cbc4ac2020a2f91c7cfa4f3abf9bbb60` | 131 | 8.5M | Python lib |
| click | `2247b35ea1c47c727d7a06e51fa280e12a863ff6` | 181 | 12M | Python lib |
| axios | `aa54a5e1e0e50cd1a2561b854ae5490ff589d065` | 509 | 19.4M | TS/JS lib |

## Resultados de `index` (verbatim de `realworld-latest.json`)

### Wall time + contagens (db_counts = verdade do SQLite, não o payload)

| repo | modo | wall_s | status | files | secrets_skipped | occurrences | relations | domain_entities | domain_relations |
|---|---|---|---|---|---|---|---|---|---|
| jaffle_shop | default | 0.243 | success | 19 | 0 | 19 | 0 | 0 | 0 |
| jaffle_shop | `--domains all` | 0.309 | success | 19 | 0 | 19 | 0 | 2 | 6 |
| terraform-s3 | default | 0.277 | success | 144 | 0 | 144 | 0 | 0 | 0 |
| terraform-s3 | `--domains all` | 0.528 | success | 144 | 0 | 144 | 0 | 186 | 371 |
| requests | default | 0.756 | success | 121 | 7 | 945 | 4061 | 0 | 0 |
| requests | `--domains all` | 1.456 | success | 121 | 7 | 945 | 4061 | 21 | 179 |
| click | default | 1.490 | success | 178 | 0 | 2294 | 8338 | 0 | 0 |
| click | `--domains all` | 3.706 | success | 178 | 0 | 2294 | 8338 | 16 | 54 |
| axios | default | 0.459 | **failed** | 503 | 3 | — | — | — | — |
| axios | `--domains all` | 0.932 | **failed** | 503 | 3 | — | — | — | — |

### Stage breakdown (`stats.stage_ms_*` do coordinator)

| repo | modo | discovery | hashing | parse | graph_build | domains | persist_rows | persist_publish |
|---|---|---|---|---|---|---|---|---|
| jaffle_shop | default | 40 | 1 | 0 | 0 | 0 | 1 | 1 |
| jaffle_shop | domains | 34 | 1 | 0 | 0 | 8 | 2 | 2 |
| terraform-s3 | default | 60 | 12 | 0 | 1 | 0 | 6 | 7 |
| terraform-s3 | domains | 54 | 12 | 0 | 1 | 166 | 40 | 42 |
| requests | default | 53 | 13 | 264 | 36 | 0 | 119 | 147 |
| requests | domains | 55 | 13 | 273 | 36 | 624 | 140 | 168 |
| click | default | 73 | 16 | 676 | 96 | 0 | 254 | 309 |
| click | domains | 66 | 19 | 867 | 115 | 1814 | 319 | 381 |
| axios | ambos | — | — | — | — | — | — | — |

(`stage_ms` ausente no axios: falha antes de stats — `stats: {}` no payload.)

### Delta dos adapters de domínio (`--domains all` − default)

| repo | Δwall_s | domain_entities | domain_relations | nota |
|---|---|---|---|---|
| jaffle_shop | +0.066 | 2 | 6 | adapters: sql/aws-data extraíram do .sql/.yml |
| terraform-s3 | +0.251 | 186 | 371 | terraform adapter dominante |
| requests | +0.700 | 21 | 179 | cicd (21 ent.) + api 123 rel. sem entidade própria |
| click | +2.216 | 16 | 54 | domains stage sozinho: 1814ms — **o maior custo do run** |
| axios | +0.473 | — | — | falha idêntica nos dois modos (bug abaixo) |

## Latência da API real (`/api/v1`, 25 req após 3 warmups)

| repo | endpoint | status | p50_ms | p95_ms | max_ms | itens |
|---|---|---|---|---|---|---|
| jaffle_shop | /graph/overview | 200 | 2.353 | 2.804 | 3.137 | — |
| jaffle_shop | /search?q=customer | 200 | 3.916 | 5.074 | 5.202 | items=3, domain_items=2 |
| jaffle_shop | /graph/neighbors | 200 | 2.389 | 3.019 | 3.086 | edges=0 |
| terraform-s3 | /graph/overview | 200 | 3.033 | 3.558 | 3.596 | — |
| terraform-s3 | /search?q=bucket | 200 | 8.214 | 9.280 | 9.577 | items=20, domain_items=20 |
| terraform-s3 | /graph/neighbors | 200 | 2.617 | 3.009 | 3.020 | edges=0 |
| requests | /graph/overview | 200 | 3.544 | 4.100 | 4.551 | — |
| requests | /search?q=session | 200 | 18.166 | 19.488 | 20.280 | items=20, domain_items=0 |
| requests | /graph/neighbors | 200 | 4.207 | 4.620 | 4.637 | edges=50 |
| click | /graph/overview | 200 | 4.871 | 5.429 | 5.583 | — |
| click | /search?q=command | 200 | 23.847 | 29.113 | 30.876 | items=20, domain_items=0 |
| click | /graph/neighbors | 200 | 5.093 | 5.798 | 5.819 | edges=50 |
| axios | /graph/overview | **409** | 2.419 | 2.984 | 3.025 | snapshot-not-ready (index falhou) |
| axios | /search?q=request | 200 | 2.345 | 3.080 | 3.438 | items=0, domain_items=0 |
| axios | /graph/neighbors | skipped | — | — | — | sem symbol_occurrences |

## Bug real encontrado (comportamento medido, não corrigido)

**P1 — `index` falha o snapshot inteiro em TS `import x = require('y')`.**

axios (repo top-10 GitHub) falha deterministicamente nos dois modos,
exit_code=1:

```
graph integrity violated: edge without target text: IMPORTS from module:typescript:tests/module/cjs/tests/helpers/cjs-added-types.ts; edge without target text: IMPORTS from module:typescript:tests/module/cjs/tests/helpers/cjs-fetch-types.ts; edge without target text: IMPORTS from module:typescript:tests/module/cjs/tests/helpers/cjs-is-cancel-typing.ts
```

Causa raiz (verificada com tree-sitter real):

- `import axios = require('axios')` vira
  `import_statement > import_require_clause > string` — o field `source`
  mora **no clause**, não no statement.
- `TypeScriptAdapter._handle_import` (`typescript.py:265-280`) lê
  `node.child_by_field_name("source")` do `import_statement` → `None` →
  `module_path = ""` → `ExtractedRelation("IMPORTS", module, "")`.
- `validate_graph` (`validation.py:61-62`) rejeita edge com `to_id=None` e
  `to_text` vazio → snapshot FAILED inteiro (fail-closed correto do gate;
  o defeito é o parser emitir a aresta vazia em vez do path ou dropar).
- Bonus: `_import_names` também não cobre `import_require_clause` → o
  binding `axios` nem é registrado.

Reproduz mínimo: qualquer `.ts` com `import x = require('y')` — 4 arquivos
helper do axios derrubam a indexação de 503 arquivos.

## Achados honestos

- **Cobertura de linguagem é real e limitada**: `.js` (225 arquivos do
  axios) e `.tf`/`.sql` fora dos adapters não geram símbolos — ocorrencias
  são os nós-módulo por arquivo (19/144 nos repos sem linguagem parseável).
  Isso é design, não defeito — mas agora está medido.
- **`domains` stage é o custo dominante com `--domains all`** em repo com
  muito .py (click: 1814ms ≈ 49% do wall 3.706s; requests: 624ms ≈ 43%).
  pyspark+api+sql varrem cada .py — superfície de otimização futura.
- **`secrets_skipped` funciona de verdade**: requests=7, axios=3 —
  certs/chaves de teste dos próprios repos excluídos pelo SECRET_PATTERNS.
- **Search híbrido acha também faceta de domínio**: terraform "bucket"
  retornou items=20 + domain_items=20; jaffle "customer" items=3 +
  domain_items=2 — a faceta (lf-0119) responde em produção.
- **Neighbors em repo sem linguagem parseável** retorna 200 com `edges=0`
  honestamente; axios devolve 409 correto (`snapshot-not-ready`) no
  overview — a API degrada certo quando não há READY.
- `files_indexed` < `corpus.files` nos casos esperados: secrets + ignores
  padrão (`.git`, etc.) — ex: axios 509→503+3, requests 131→121+7.
- Wall total do suite (5 repos, 2 modos cada + API): 13.637s — bounded.

## Como reproduzir

```bash
bash benchmarks/realworld/clone_corpus.sh
.venv/bin/python benchmarks/realworld/run_benchmark.py
# resultado: benchmarks/results/realworld-latest.json
```

## NOT RUN (honesto)

- `.js`/`.cjs` do axios: fora dos parsers (apenas .ts entra) — medido como
  inventário, não como grafo.
- `parse_result_cache` warm/incremental: o bench mede só cold `--full`
  (data dir fresco por modo).
- Resolvers opt-in (scip/lsp): config isolado → syntactic-only por design.
- Repositórios >509 arquivos: corpus é small por spec; scaling já coberto
  por `bench_index.py` (100/1k/5k sintético).
