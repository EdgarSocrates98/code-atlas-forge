# Security & Portability Review — Cycle 3.1 (lf-0033)

> Escopo: a superfície nova dos Cycles 3/3.1 — resolvers externos
> (índice SCIP, servidores LSP), cache de parse, snapshots/GC,
> enriquecimento semântico. O modelo de ameaças base está em
> `threat-model.md` (Cycle 1) e continua válido.

## Fronteiras de confiança (fatos verificados no código)

- `config.toml` (com `[resolvers]`, `lsp.servers.*.cmd`, `scip.index_path`)
  vive no **config dir do operador** (`config_dir()` →
  `$CODE_ATLAS_CONFIG_DIR` ou `~/.config/code-atlas`) — nunca dentro do
  repo indexado. Um repo hostil **não** escolhe binários nem índices.
- `--scip PATH` é argumento do operador; `--resolver` só filtra
  resolvers já configurados.
- O indexer nunca executa, importa ou instala conteúdo do repo
  (`test_resolvers_do_not_execute_repo_content`,
  `test_no_auto_download_or_install_in_src`).

## Matriz: prioridade → mecanismo → teste → veredito

| Prioridade | Mecanismo | Teste | Veredito |
|---|---|---|---|
| symlink escape | resolvido contra `root.resolve()`, nunca atravessado | `test_symlink_to_etc_warns_not_follows`, `test_symlink_git_mode` | coberto |
| TOCTOU | `os.open` + `fstat` verificado; digest re-verificado no parse | `test_content_mismatch_between_scan_and_parse`, `test_symlink_swap_between_scan_and_parse` | coberto |
| protobuf malicioso | wire-reader próprio, caps (`varint ≤10B`, `max_bytes`), validação estrutural | `test_scip_bad_fixtures_rejected`, `test_scip`, `test_provenance` | coberto |
| índices grandes | `MAX_INDEX_BYTES` antes de decodificar | `test_scip_bad_fixtures_rejected` (oversize) | coberto |
| integer overflow | varint >10 bytes rejeitado; valores extremos não crasham | `test_scip_varint_overflow_rejected`, `test_scip_huge_field_values_bounded` | coberto (lf-0033) |
| path traversal | `index` rejeita `..` e raiz do FS; `db` confinado ao data dir | `test_index_rejects_traversal`, `test_index_rejects_filesystem_root`, `test_db_path_confined_to_data_dir` | coberto |
| LSP arbitrário | server vem do config do **operador** (não do repo); spawn sem shell, argv fixo | `test_subprocess_confined_to_gitbin` (único site de subprocess é git), `test_lsp_absent_typed_error_not_crash` | coberto — trust declarado |
| env leakage | env do spawn LSP filtra/redata segredos | `test_lsp_spawn_env_redacts_secrets` | coberto |
| process injection | subprocess confinado a `gitbin.py` + LSP opt-in | `test_subprocess_confined_to_gitbin`, `test_no_network_or_shell_exec_in_src` | coberto |
| segredos em logs | `RedactionFilter` + scanner pula arquivos-segredo | `tests/observability/`, `test_hardening` | coberto |
| SQLite corruption | publicação atômica; arquivo não-SQLite → `DatabaseError` tipado | `test_corrupt_db_file_detected` (lf-0033), `test_migration_tx`, `test_failed_incremental_keeps_prior_ready` | coberto |
| cache poisoning | payload JSON validado por sha + chave completa; blob ruim → miss | `test_corrupted_blob_is_miss_not_crash`, `test_no_pickle_marshal_eval_in_codec`, `test_get_many_*` | coberto |
| cross-repo/snapshot contamination | arestas por `snapshot_id`; `cross_snapshot_relations` auditado | `test_identical_repos_no_cross_contamination`, `test_relations_scoped_to_own_snapshot` (lf-0033) | coberto |
| snapshot isolation | consultas só leem READY; STALE invisível | `test_stale_snapshot_invisible_to_queries` (lf-0033) | coberto |
| resource exhaustion | `max_files/max_file_size/max_depth/timeout`; caps SCIP | `test_pathological_depth_capped`, `test_zero_byte_and_oversize_files`, `test_lsp_timeouts_bounded` | coberto |
| untrusted repo instructions | nada do repo é executado (ver acima) | `test_resolvers_do_not_execute_repo_content` | coberto |
| concurrent writers | `BEGIN IMMEDIATE` + recheck; losers → FAILED tipado | `test_concurrent_indexers_single_ready`, `test_lost_write_lock_marks_failed_not_crash` | coberto |
| interrupted write | transação única; falha → FAILED, READY anterior intacto | `test_begin_lock_denied_fails_with_failed_snapshot`, `test_failed_incremental_keeps_prior_ready` | coberto |
| fragment/snapshot GC | `retain_snapshots` opt-in, default ilimitado; READY/FAILED nunca deletados | `tests/indexing/test_lf0031_perf.py` | coberto (lf-0031) |

## Portabilidade (Linux + Windows)

- Sem APIs unix-only em `src/` (`test_no_unix_only_apis_in_src`); bugs
  reais do Windows corrigidos no Cycle 3 (select em pipe, close com
  read pendente, URI de drive `file:///C:`).
- CI roda ubuntu × windows × py3.12/3.13 (ver PR #4 evidência).

## P0/P1 abertos

**Nenhum.** Itens declarados (não-bugs, limitações de trust aceitas):

- Servidor LSP real é binário escolhido pelo operador — executá-lo é o
  *propósito* do recurso opt-in; o repo indexado não pode selecioná-lo.
  E2E com server real: **NOT RUN** — nenhum pyright/ts-server no
  ambiente (declarado em eval-report `cycle31.not_run`); lifecycle é
  testado contra `tests/resolvers/fake_lsp` + rust-analyzer ausente.
- Índice SCIP é fornecido pelo operador; proveniência/revisão é
  avaliada (`lf-0028`) mas um índice *assinado* pelo produtor está fora
  de escopo — declared, plano: trust de índice fica para ciclo futuro
  se resolvers remotos forem adicionados.

## Regressões

Suíte completa (Cycle 1–3.1) verde — ver evidência do spec.
