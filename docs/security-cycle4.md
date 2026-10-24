# Security Review — Cycle 4 (lf-0045)

Conteúdo indexado é hostil por padrão: comentários maliciosos, docs
adversariais, segredos, pseudo-system-messages, paths manipulados,
arquivos enormes, SQL malicioso, inputs MCP hostis. Vereditos abaixo
são sobre **testes reais** — cada linha aponta para o teste que a
comprova. Cobertura herdada do Cycle 3.1 em `security-review-cycle31.md`
e `tests/e2e/test_hardening.py` não é repetida aqui.

## Matriz item → mecanismo → teste → veredito

| item | mecanismo | teste | veredito |
|---|---|---|---|
| Prompt injection em código/doc indexado | conteúdo é `body`/evidência; nunca entra em `manifest`/`diagnostics` (campos gerados pelo engine) | `test_hostile_content_stays_data` | OK |
| Pseudo-system-messages em arquivos | nenhum campo do pack deriva de conteúdo indexado além de `body`/`title` citados | mesmo teste | OK |
| SQL injection via query/tool | queries parametrizadas; SQL dinâmico vetado por auditoria AST (`test_static_sql_audit` do ciclo 3.1) | `test_sql_injection_queries_are_inert` | OK |
| Path traversal em query | query é string FTS/qname — nunca usada como path | `test_sql_injection_queries_are_inert` (cobre `../..`) | OK |
| Traversal no index / workspace | confinamento herdado (rejects `..`, raiz FS, symlinks externos) | `test_index_rejects_traversal`, `test_symlink_to_etc_warns_not_follows` | OK |
| Segredos em código | conteúdo de código é **dado** — retornado verbatim (proposital); segredos em *env de processos* são redigidos | `test_lsp_spawn_env_redacts_secrets` | OK (escopo documentado) |
| Execução de código indexado | nenhum `exec`/`eval`/import do repo; resolvers nunca executam | `test_resolvers_do_not_execute_repo_content`, `test_no_network_or_shell_exec_in_src` | OK |
| Egress | zero `socket`/`urllib`/`requests` em src | `test_no_network_or_shell_exec_in_src` | OK |
| Limites de resposta | `limit` clamped a ≤200 em engine+MCP; budget clamped a ≤1M tokens | `test_limits_enforced` | OK — **bug real corrigido nesta spec** (`budget_tokens` ilimitado) |
| Limites de grafo | `TraversalLimits` bounded; truncamento tipado | `test_graph_retrieval` (lf-0037) | OK |
| N+1 / memória | `get_many` batchado (lf-0031); nenhum grafo inteiro em memória (BFS limitado por `max_results`) | `test_lf0031_perf`, BFS limits | OK |
| MCP inputs hostis | validação por tool; erros tipados (`-32602`); nomes fora do registry → erro | `test_mcp_hostile_inputs` | OK |
| MCP escrita via tools | registry só tem leitura; nenhum handler emite DML | `test_mcp_no_write_tools` (snapshot count inalterado) | OK |
| MCP config global | `mcp manifest` **emite** JSON; instrução vai a stderr; zero escrita fora do workspace | `test_mcp_manifest_does_not_write_host_config` | OK |
| Índice vetorial corrompido | cache por conteúdo+modelo; dimensão validada; provider ausente → diagnóstico honesto | `tests/search/vector/` (lf-0039) | OK |
| Arquivos enormes / depth | caps herdados (0-byte, oversize, profundidade) | `test_zero_byte_and_oversize_files`, `test_pathological_depth_capped` | OK |
| Dependência Forge | zero imports de `forge*`/`loop_factory` em src | `test_no_forge_imports_in_src` | OK |
| Rebuild de embeddings | cache por content-hash — só rebuilda se conteúdo/modelo mudar | `tests/search/vector/` | OK |

## Performance (benchmarks/results/lf-0045-perf-cycle4.json, n=30/célula)

| cenário | p50 ms | p95 ms |
|---|---|---|
| small · lexical | 0.18 | 0.20 |
| small · structural | 0.15 | 0.16 |
| small · graph | 0.34 | 0.49 |
| small · hybrid | 1.52 | 1.59 |
| small · pack-focus / pack-deep | 0.30 / 0.51 | 0.34 / 0.52 |
| small · mcp-search (ndjson E2E) | 1.13 | 1.22 |
| medium (corpus large) · lexical | 0.08 | 0.09 |
| medium · hybrid | 6.99 | 7.79 |
| medium · pack-deep | 0.23 | 0.25 |
| medium · mcp-search | 0.97 | 1.02 |

Sem regressão crítica vs baselines do Cycle 3.1; hybrid é a célula mais
cara (soma dos backends) — comportamento esperado e medido, não
inferido.

## Veredito

**0 P0/P1 abertos.** Achado corrigido nesta spec: `budget_tokens` sem
clamp no `ContextEngine` (agora ≤ 1 000 000). Riscos residuais
documentados: evidências `matched_fields` vs `evidence` nos hits
lexical/structural (hardening futuro, não bloqueante).
