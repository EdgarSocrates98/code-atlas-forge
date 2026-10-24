# Evidência — Cycle 3.1 (Semantic Hardening)

- Branch: `cycle-3.1-semantic-hardening`
- SHA inicial (merge-base com main): `c74fc89` — primeiro commit do
  ciclo `e995b27` (lf-0026 baseline audit)
- SHA final da main: **registrado após merge** — ver rodapé.
- PR: ver rodapé.
- Specs: `factory/specs/archive/lf-0026` … `lf-0034` (cada uma com
  review em `factory/reviews/`).

## Entregas por spec

| spec | commit | conteúdo |
|---|---|---|
| lf-0026 | `e995b27` | baseline audit + gap register (`docs/cycle-3.1-baseline-audit.md`) |
| lf-0027 | `95ef2b4` | `SemanticEnricher` + bootstrap `[resolvers]` + `GraphEdge.source_path`; resolvers integrados ao pipeline; `resolver:external` na identidade |
| lf-0028 | `9afa5b8` | `IndexProvenance` + `assess_revision` (7 sinais); rejected→nunca resolve, degraded→heuristic |
| lf-0029 | `726971e` | LSP `documentSymbol`, `publishDiagnostics`, recovery (1 restart + 1 retry); doc real-vs-fixture |
| lf-0030 | `6bfc06d` | análise 6 modelos + ADR-0020 (snapshots completos; sharing rejeitado); `resolver:scip=<sha16>` invalida |
| lf-0031 | `250d625` | `stage_ms_*` instrumentation; `get_many` batch; builder O(1); `retain_snapshots` GC |
| lf-0032 | `d8ecc53` | `--scip`, `--resolver`, `semantic-status` (installed/enabled/used + coverage) |
| lf-0033 | `19056a0` | gaps semânticos + isolamento/consistência; eval-report `cycle31`; security review |
| lf-0034 | este | docs + homologação + PR + merge |

## Contratos (inalterados — aditivos apenas)

- Envelope `schema_version: "2"`; campos novos sempre aditivos.
- `relations.{resolver, resolution_status, confidence_class,
  evidence_ids}` — proveniência por aresta.
- `GraphEdge.source_path` (novo, interno) — módulo de origem p/
  `ResolutionRequest`.
- Identidade do índice: parsers + `resolver:` + `resolver:scip=<sha16>`.
- Snapshot: 1 READY por repo; publish atômico; consultas só leem READY.

## Migrations

Nenhuma nova — schema permanece **v5** (`identity_key` UNIQUE +
`snapshots.dirty`, do Cycle 3). Campos de resolver/estágio cabem no
schema existente por construção.

## Métricas semânticas

`tests/golden/eval-report.json` (gerado por
`tests/golden/gen_eval_report.py`, reproduzível):
- 6 corpora, P/R 1.00 em symbols e relations (modo `all`), **0 false
  positives**; resolved rates 0.85–1.00; `large` (subset) sem FP
  reportado (regra mantida).
- `cycle31`: stale-index rejection 4/4 corretos; invalidation
  correctness (incremental≡full, reuse estável); graph equivalence
  incremental≡cold; cache hit real + corrupt→miss; 0 relações
  cross-snapshot.

## Testes

- Total: **375 testes** (suite completa Cycles 1–3.1).
- Novos do ciclo: `test_semantic_enrichment.py` (15),
  `test_provenance.py` (15), `test_lsp` hardening (18),
  `test_lf0031_perf.py` (retenção/GC/timings), `test_semantic_ux.py`
  (36 CLI), `test_semantic_gaps.py` (5), `test_isolation.py` (7).
- NOT RUN declarado: E2E com servidor LSP real (ausente no ambiente —
  `eval-report.cycle31.not_run`).

## Benchmarks antes/depois (5k, 1 arquivo)

| | total | graph_build | persist |
|---|---|---|---|
| before | 5.48s | ~2.0–2.2s | ~1.4s |
| after | **4.55s** | **0.93–1.3s** | ~1.4s |

Claims: graph_build −50% (O(n²)→O(1)); total −17%; cache SQL
round-trips 2019→~6 (wall-clock neutro — desserialização domina;
claim honesto, não inflado). Detalhes:
`benchmarks/results/lf-0031-sqlite-perf.json` + `cycle-3.1-benchmarks.md`.

## Cobertura SCIP / LSP

- **SCIP**: decoder stdlib testado (`test_scip`, 21); proveniência
  (`test_provenance`, 15); enriquecimento E2E com índice sintético
  real (`test_semantic_enrichment`); rejeição de índice ruim (golden).
- **LSP**: lifecycle via `fake_lsp` (initialize/shutdown/timeout,
  documentSymbol, publishDiagnostics, restart+retry) — 18 testes.
  Servidor real: **NOT RUN** (nenhum pyright/ts-server instalado no
  ambiente — limitação declarada, não escondida).

## Gaps P0/P1/P2

- **P0/P1: nenhum aberto** (ver `docs/security-review-cycle31.md`).
- P2 declarados:
  - LSP real não homologado E2E (fixture cobre lifecycle);
  - persist permanece O(snapshot) — trade-off ADR-0020 assumido;
  - resolver sintático não infere receiver (unresolved honesto);
  - barrel exports TS: membros não promovidos (honesto);
  - dynamic imports sem aresta (honesto);
  - índice SCIP não é assinado — trust é do operador (declarado).

## Decisões

- ADR-0020: snapshots completos; structural sharing rejeitado com
  justificativa (`docs/graph-fragment-storage.md`,
  `docs/incremental-graph-reuse.md`).
- `retain_snapshots` opt-in, default ilimitado; `VACUUM` rejeitado
  como operação automática (`docs/storage-lifecycle.md`).
- PRAGMAs inalterados — nenhuma durabilidade enfraquecida.
- Enriquecimento só promove com alvo persistível — cross-snapshot
  impossível por construção.

## Homologação 1–20 (saídas reais)

| # | item | resultado |
|---|---|---|
| 1 | ruff | `ruff check src tests` → All checks passed |
| 2 | mypy strict | `mypy src` → no issues (65 arquivos) |
| 3 | pytest | `pytest -q` → 375 passed |
| 4 | golden | `pytest tests/golden` → 27 passed |
| 5 | SCIP reais | `pytest tests/resolvers` → 68 passed (scip 21 + provenance 15 + lsp 18 + resolvers 14) |
| 6 | LSP | fixture `fake_lsp` + lifecycle — 18 testes; server real NOT RUN (declarado) |
| 7 | snapshots | `test_integrity` + `test_isolation` (STALE invisível, cross-snapshot=0) |
| 8 | concorrência | `test_concurrent_indexers_single_ready`, `test_lost_write_lock` — verde |
| 9 | cache | `test_cache` (hit/miss/version bump/blob corrompido) — verde |
| 10 | invalidation | `test_incremental` + `test_git_intelligence` (transitivo, rename, branch) — verde |
| 11 | benchmarks | `bench_index --sizes 100` → total 0.2245s, p50 query 0.74ms, rss 32.6MB |
| 12 | wheel | `pip wheel` + install em venv limpo → CLI funcional (`--help` lista 12 comandos) |
| 13 | Linux CI | ver rodapé (run id) |
| 14 | Windows CI | ver rodapé (run id) |
| 15 | licenças | projeto Apache-2.0; deps `tree-sitter*` MIT; `pip-audit --strict` → 0 vulns |
| 16 | segurança | `docs/security-review-cycle31.md` — matriz 18 prioridades, 0 P0/P1 |
| 17 | regressões C1–C3 | suíte completa verde (375 testes cobrem todos os ciclos) |
| 18 | full vs incremental | `test_consistency_cold_incremental_cache_rebuild` + verbatim abaixo |
| 19 | revisão arquitetural | `docs/cycle-3.1-architecture.md` (diff vs Cycle 3) |
| 20 | inspeção de evidências | este documento + reviews por spec em `factory/reviews/` |

Verbatim (repo 4 arquivos, `CODE_ATLAS_*` isolados):

```
index .        → success, 4 files, 11 relations, 4 parsed
index .        → noop
(muta 1 file) index . → success, parsed 3, reused 1
index-plan .   → {"incremental": true, "counts": {"reuse": 4}, ...}
semantic-status → syntactic-scope-v1: installed/enabled/used=true;
                  coverage resolved 11/11
```

## Gates G1–G12

| gate | status | evidência |
|---|---|---|
| G1 resolvers conectados ao pipeline | PASS | `SemanticEnricher` no coordinator (`_enrich_semantic`); test_semantic_enrichment 15 |
| G2 SCIP validado por revisão | PASS | `assess_revision` no bootstrap; test_provenance 15 |
| G3 LSP opcional funcional e isolado | PASS | opt-in default-off; lifecycle fake_lsp; real NOT RUN declarado |
| G4 grafo persistido enriquecido | PASS | `relations.resolver ∈ {syntactic-scope-v1, scip, lsp:*}`; stats semantic_* no payload |
| G5 evidence integrity | PASS | evidence_ids por aresta; FK check pré-publish |
| G6 snapshot isolation | PASS | `cross_snapshot_relations`=0; STALE invisível (test_isolation) |
| G7 incremental correctness | PASS | incremental≡cold canônico; invalidação por resolver:scip=sha |
| G8 performance medida | PASS | benchmarks/results/lf-0031 (antes/depois mesma metodologia) |
| G9 CLI semântico real | PASS | `--scip`/`--resolver`/`semantic-status` verbatim acima |
| G10 E2E + golden | PASS | 27 golden + suíte completa 375 |
| G11 segurança/portabilidade | PASS | security-review-cycle31 (18 itens); CI Linux+Windows |
| G12 docs + ADRs | PASS | docs criados/atualizados; ADR-0020; roadmap/README |

## Limitações (sem linguagem promocional)

- Implementado: enrichment no pipeline, revisão SCIP, LSP lifecycle,
  instrumentação, GC opt-in, CLI semântico, hardening de segurança.
- Integrado: SCIP/LSP via bootstrap do config do operador.
- Opcional: `[resolvers]` inteiro é opt-in; `retain_snapshots`.
- Experimental: —
- Não suportado: receiver-type inference, barrel member promotion,
  dynamic import edges, structural sharing, LSP real não homologado.
- Planejado (Cycle 4): hybrid search, context packs, MCP, UI.

## Recomendação Cycle 4

**Pronto.** O contrato que o Cycle 4 precisa existe e é estável:
envelope v2 aditivo, tiers de evidência por aresta, `semantic-status`
para diagnóstico, index-plan para dry-run, snapshots isolados. Nenhum
P0/P1 aberto. O gargalo conhecido (persist O(snapshot)) não bloqueia
context packs/MCP — afeta só reindex de corpora grandes.

---

_Rodapé preenchido após merge: PR, run ids de CI, SHA final da main._
