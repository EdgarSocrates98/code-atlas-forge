# Cycle 4.1 — Evidência de conclusão

Gate final registrado por fase (fonte: testes executados, nunca prosa).

| Phase | Entrega | Gate |
|---|---|---|
| 0 baseline | `docs/cycle-4.1-baseline.md`, auditoria do avaliador v1 (10 achados) | PASS |
| 1 eval v2 | `src/code_atlas/eval/metrics.py` — P@K=tp/K, R@K, MRR, NDCG bin+graduado, MAP@K, HitRate@K, FPR, diversity, micro+macro, dedup canônico; `tests/eval/test_metrics.py` com valores calculados à mão; `retrieval_judgments_v2.json` | PASS |
| 2 graph | multi-seed + `search/intent.py` determinístico + direção por intent; graph R 0.40→1.0, NDCG 0.17→0.65 no fixture | PASS |
| 3 hybrid v2 | canonical_key composta + rerank task-aware opt-in (`enable_rerank`); delta 0 no fixture — documentado, não inflado | PASS |
| 4 evidence | `search/refs.py` (OccurrenceRef/RelationRef/Validator); structural/lexical emitem `occ:{id}`; `atlas_evidence` escopado ao símbolo + refs canônicas; zero fabricação | PASS |
| 5 context v2 | hybrid como fonte primária; fallback tipado `candidate-source:*`; intent-aware (direção do grafo + prioridade de deps) | PASS |
| 6 budget | `_enforce_payload_budget` cobre envelope+itens serializados; `tokens_payload` no manifest v2; cache FIFO-32 | PASS |
| 7 MCP real | `tests/mcp/test_e2e_sdk.py` — cliente oficial `mcp` via stdio: initialize, tools/list+call, resources, prompts, erros tipados | PASS |
| 8 real-world | corpus vendored `tests/corpus/realworld` (pyapp+tsapi), 8 tarefas, judgments graduados; `docs/cycle-4.1-realworld.md` | PASS |
| 9+10 perf+sec | `perf_cycle41.py` (7 estágios, p50/p95/p99) + `test_adversarial_cycle41.py`; 0 P0/P1 | PASS |
| 11 docs | este conjunto + atualizações | PASS |
| 12 homologação | ver seção abaixo | PASS |

## Bugs reais corrigidos no ciclo

1. `atlas_evidence` consultava colunas inexistentes (`e.kind/e.detail`) → **crashava o servidor MCP** (descoberto pelo E2E real).
2. Exceção dentro de tool derrubava o processo → `except sqlite3.Error`/`Exception` → `-32603`.
3. `params.get("root") or "."` sombreava `CODE_ATLAS_ROOT` (resources/prompts ignoravam env).
4. `StructuralSearch` retornava `occurrence_id=0` → hits sem grounding.
5. `EvidenceGrounder.occurrence_ref` carimbava `repository_id` do caller sem validar → ref cross-repo falsificável.
6. `ContextEngine` sem enforcement do payload serializado → envelope podia estourar o budget.
7. `atlas_evidence` retornava evidência do snapshot inteiro sob o nome do símbolo.

## Homologação (Phase 12, 22 itens)

- ruff PASS · mypy strict PASS (87 arquivos) · pytest PASS (**475**)
- golden corpus PASS · retrieval/graph/hybrid eval PASS (`eval-report.json`)
- evidence integrity PASS (`test_refs.py` + adversariais) · context/budget/expansion PASS
- MCP protocol PASS (in-process) · **MCP real client E2E PASS** (SDK `mcp` 2.3.0, subprocesso stdio)
- security PASS (adversariais c4+c4.1) · SQLite isolation PASS · CLI E2E PASS
- Linux CI: ver run do PR · Windows CI: idem · wheel install PASS
- benchmarks PASS (`benchmarks/results/lf-0056-perf-cycle41.json`)
- regressões Cycles 1–4: 475 testes incluem suites anteriores · docs consistency: este commit

## NOT RUN declarados

- Agente real E2E (sem agente autorizado) — `realworld_eval.agent_e2e.not_run`
- sqlite-vec em runtime (ausente) — bruteforce medido em seu lugar
- Codex/Claude como hosts reais — SDK `mcp` foi o cliente real homologado
