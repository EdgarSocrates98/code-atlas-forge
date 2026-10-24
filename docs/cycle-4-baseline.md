# Cycle 4 — Baseline Audit (lf-0035)

Baseline do `main` no início do Cycle 4 ("Hybrid Search, Context
Engineering, MCP & Agent Intelligence").

## Linha de partida

- **Merge-base com `main`**: `a9def8d` (squash do PR #5, Cycle 3.1) +
  `6ab1e4a` (archive/docs). Branch do ciclo: `cycle-4-hybrid-search-mcp`.
- **Suíte**: 375 testes verdes no início da spec; **378** após lf-0035
  (+3: confirm/conflict/untouched de precedência, +1 migração v6 —
  ajuste de pin de versão; +1 E2E real pyright).
- **Schema**: `schema_migrations` em v5 → **v6** (lf-0035: colunas de
  proveniência de resolvedor em `evidence`).
- **Gates**: ruff clean, mypy clean (65 arquivos), CI 5/5 verde no
  último run de main (run `40112990356`).

## Inventário relevante para o ciclo

| subsistema | estado no baseline |
|---|---|
| Pipeline index | scan → hash → parse → graph → **semantic enrich** → persist (transacional, snapshot READY) |
| Resolvers | syntactic baseline + SCIP (stdlib decoder, provenance/revision safety) + LSP (opt-in, lifecycle hardening) |
| Evidence | `resolution_status` + `confidence_class` + `resolver` por aresta; `evidence` agora carrega `resolver_id/version/tier/index_hash/validation_status` |
| Search | `StructuralSearch` (SQL parametrizado) + FTS5 `fts_symbols` (sem engine de retrieval dedicada) |
| Incremental | change detector + invalidation planner + parse cache + snapshots completos (ADR-0020) |
| CLI | index/search/references/definitions/dependencies/index-plan/semantic-status; envelope v2 |
| Evals | `tests/golden/eval-report.json` (cycle31), golden fixtures py/ts |

## Gaps que o Cycle 4 precisa fechar

- **Sem engine de retrieval unificada**: FTS5 existe como tabela mas sem
  pipeline de query/ranking/paginação próprio (Gate 2).
- **Sem graph retrieval**: não há travessia limitada/explicável nem
  ranking estrutural (Gate 3).
- **Sem hybrid/vector**: nenhuma fusão de candidatos (Gate 4/5).
- **Sem Context Packs**: nada de token budget, seleção prioritária ou
  expansão progressiva (Gates 6–8).
- **Sem MCP**: nenhum endpoint JSON-RPC (Gates 9–10).
- **Sem métricas de retrieval**: eval-report cobre resolução/index, não
  P@K/MRR/NDCG (Gate 11).
- **Sem suíte de prompt-injection** dedicada (Gate 12).

## Entregas do lf-0035 (Phase 0)

1. **Política de precedência de evidência determinística** —
   ver ADR-0021. Aresta `resolved` é verificável por tier estritamente
   superior: confirmação sobe a proveniência; divergência vira
   `ambiguous` + diagnóstico `semantic-conflict`, preservando o alvo
   original. Sem resolver superior configurado → custo zero.
2. **`EvidenceRecord` fortalecido** — `resolver_id`, `resolver_version`,
   `evidence_tier`, `index_hash`, `validation_status` (persistidos via
   migração v6; `""`/`0` quando a evidência vem do parser).
3. **`ResolverProvenance.index_hash`** — sha256 do índice externo
   fluindo do `IndexProvenance` do SCIP para cada evidência.
4. **LSP real opt-in** — `pyright-langserver@1.1.413` instalado via
   `npm install -g` (ambiente: node v22.23.2, nvm). Teste E2E em
   `tests/resolvers/test_lsp_real.py` roda com o binário presente e
   faz skip honesto (`NOT RUN`) quando ausente — instalação mínima e CI
   não dependem de Node.
