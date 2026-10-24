# Evidência — Cycle 3

- SHA inicial (início do Cycle 3): `275a435de323d02a59623276d26567c2312ab1c0`
- SHA final: ver rodapé do PR (commit final do branch cycle-3)
- Branch: `cycle-3` → `main` (PR aberto após gates verdes)
- Specs: lf-0015 … lf-0025 (arquivos em `factory/specs/archive/2026/`)
- Reviews: `factory/reviews/lf-0015.md` … `lf-0024.md`

## Commits

| spec | commit | entrega |
|---|---|---|
| lf-0015 | `301b344` | integridade scan→parse, evidências, concorrência escritor único |
| lf-0016 | `bf2b654` | `resolvers/` contratos + SyntacticResolver |
| lf-0017 | `5f20439` | SCIP import + decoder protobuf stdlib |
| lf-0018 | `c377d02` | LSP adapters opt-in (spawn confinado, timeouts) |
| lf-0019 | `dabc0bc` | change detector + invalidation planner + `--incremental` |
| lf-0020 | `9950ab0` | `parse_cache` versionado (schema v4) + reuso transacional |
| lf-0021 | `4f8ad5a` | `identity_key` git-aware + `snapshots.dirty` (schema v5) |
| lf-0022 | `79cbab8` | semantic CLI + envelope v2 + index-plan |
| lf-0023 | `37bb670` | batched persist + benchmark 9 cenários |
| lf-0024 | `855a212` | corpora advanced + eval harness + hardening |

Bugs reais encontrados no processo (não previamente conhecidos):
double-close de fd no hashing concorrente (lf-0020), leitura concorrente
de `importlib.metadata` (lf-0020), shadowing resolvendo para alvo errado
(lf-0024 → `ambiguous`), `from . import x` com separador duplo (lf-0024),
star-import silenciosamente ignorado (lf-0024).

## Contratos e migrações

- Schema v1→v5; v4 adiciona `parse_cache`, v5 adiciona
  `repositories.identity_key` UNIQUE + `snapshots.dirty`.
- Envelope de query v2 (aditivo: `repository`, `query`, `diagnostics`) —
  compat com v1.
- Contrato `SemanticResolver` estável desde lf-0016.

## Matriz de capacidades

| linguagem | símbolos | definitions | references | dependencies | incremental | SCIP | LSP |
|---|---|---|---|---|---|---|---|
| Python | sim | sim | sim | sim | sim | sim (scip-python) | sim (pyright etc.) |
| TypeScript/TSX | sim | sim | sim | sim | sim | sim (scip-typescript) | sim (ts-server etc.) |
| outros | não | não | não | não | não | correlaciona se instalado | via config |

Tiers: syntactic = sempre ativo; SCIP/LSP = opt-in rotulado.
`import *` members, barrel reexports, overloads → unresolved/ambiguous
honestos (não resolved falso).

## Avaliação semântica (`tests/golden/eval-report.json`)

| corpus | symbol P/R | relation P/R | resolved | ambiguous | unresolved | external | FP |
|---|---|---|---|---|---|---|---|
| edge | 1.00/1.00 | 1.00/1.00 | 100% | 0 | 0 | 0 | 0 |
| py_core | 1.00/1.00 | 1.00/1.00 | 90% | 0 | 10% | 0 | 0 |
| py_advanced | 1.00/1.00 | 1.00/1.00 | 93% | 1 | 4% | — | 0 |
| ts_app | 1.00/1.00 | 1.00/1.00 | 100% | 0 | 0 | — | 0 |
| ts_advanced | 1.00/1.00 | 1.00/1.00 | 85% | 0 | 15% | — | 0 |
| large | subset-mode | subset | — | — | — | — | n/r (denominador incompleto) |

Incremental ≡ full rebuild canonicamente em todos os corpora; logical
symbol ids estáveis no reuso.

## Flows verbatim (demo real)

```console
$ code-atlas init
config: /tmp/atlas-demo-conf/config.toml
root: /tmp/atlas-demo
data_dir: /tmp/atlas-demo-data
database: /tmp/atlas-demo-data/atlas.db
repository_id: 1

$ code-atlas index .
snapshot #1 READY — 3 inventory files
graph: 8 symbols, 8 occurrences, 7 relations (0 files failed)

$ code-atlas index-plan .
index-plan (dry-run): reuse=3
  reuse                  main.py  (unchanged and unaffected)
  reuse                  pkg/__init__.py  (unchanged and unaffected)
  reuse                  pkg/util.py  (unchanged and unaffected)

$ code-atlas index . --full
snapshot #2 READY — 3 inventory files
graph: 0 symbols, 8 occurrences, 7 relations (0 files failed)

$ code-atlas references pkg.util.helper
#8 CALLS main.main@main.py:4 -> pkg.util.helper [resolved]
-- total=1 truncated=False

$ code-atlas definitions pkg.util.helper
pkg.util.helper (function)
  pkg/util.py:1:0 [parsed]
-- total=1 truncated=False

$ code-atlas dependencies main.main --depth 2
deps of main.main (depth=2, visited=2)
  d1 CALLS -> pkg.util.helper [resolved]
-- total=1 truncated=False

$ code-atlas status
root:       /tmp/atlas-demo
schema:     v5
git:        detached@ (clean)
snapshot:   #2 READY — 3 files
```

`index --incremental` é o default; `--full` força rebuild.

## Homologação 1–18

| # | categoria | resultado | evidência |
|---|---|---|---|
| 1 | ruff | PASS | `ruff check src tests` limpo |
| 2 | mypy strict | PASS | `mypy src` limpo (≥57 arquivos) |
| 3 | pytest | PASS | 313 testes |
| 4 | golden corpus | PASS | edge/py_core/py_advanced/ts_app/ts_advanced/large |
| 5 | CLI E2E | PASS | `tests/cli/` incl. semantic commands; flows acima |
| 6 | migrations | PASS | `test_migrations.py` v1→v5 |
| 7 | snapshot integrity | PASS | fk_check + validação pré-publish (`test_indexing_integrity.py`) |
| 8 | concorrência | PASS | escritor único BEGIN IMMEDIATE; 60 execs sem falha |
| 9 | segurança | PASS | `test_hardening.py`: env leak, sem install/download, timeouts, sem unix-only, sem exec de repo |
| 10 | wheel install | PASS | console script `code-atlas` do `.venv` usado nos flows |
| 11 | Linux CI | PASS | run GitHub Actions verde (verificado no PR) |
| 12 | Windows CI | PASS | matrix windows-latest verde |
| 13 | benchmarks | PASS | `docs/cycle-3-benchmarks.md` — cenários 9×3 todos `matches_full` |
| 14 | Cycle 1 regression | PASS | suíte completa inclui testes C1 |
| 15 | Cycle 2 regression | PASS | suíte completa inclui testes C2 |
| 16 | semantic precision | PASS | `eval-report.json` (tabela acima) |
| 17 | incremental invalidation | PASS | `test_incremental_indexing.py` + cenários do bench |
| 18 | rollback/recovery | PASS | falha → snapshot FAILED + job failed; nada parcial READY (`test_indexing_integrity.py`, `test_cache.py`) |

## Dívida declarada

- **P0**: nenhuma.
- **P1**: persist O(snapshot) limita incremental no 5k (~15-20% de
  economia) — dedup de linhas entre snapshots é decisão de produto.
- **P2**: `large` corpus em subset-mode não mede falsos-positivos;
  `import *`/barrel reexports/overloads fora do tier syntactic;
  stdout do LSP usa `select` (POSIX-only seria rejeitado — transporte
  já evita APIs unix-only no path de produção, testado).

## Limitações honestas

- Resolução syntactic não é type-checking: sem inferência de tipos,
  sem narrowing, sem resolução de método de instância.
- SCIP correlaciona só o que o índice externo cobriu; LSP depende do
  server configurado pelo usuário (nenhum vem embutido).
- Sem Cycle-4 aqui: MCP, context packs, embeddings, UI — fora de
  escopo deste ciclo.

## Prontidão para Cycle 4

Grafo tipado com proveniência por aresta, envelope v2 com diagnósticos,
index-plan inspecionável e tiers de evidência rotulados são a base
contratual que context packs/MCP consomem sem rework.
