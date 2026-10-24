# Graph Report - code-atlas-forge  (2026-10-24)

## Corpus Check
- 86 files · ~31,204 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 585 nodes · 964 edges · 51 communities (46 shown, 5 thin omitted)
- Extraction: 91% EXTRACTED · 9% INFERRED · 0% AMBIGUOUS · INFERRED: 88 edges (avg confidence: 0.87)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `8504c9f5`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Review: lf-0001 — Cycle 1 scaffold
- 2026/lf-0001.md
- lf-0001.review.claude.md
- scan
- scanner.py
- migrate
- gitignore.py
- Snapshot
- Store
- IndexCoordinator
- Relation
- Review: lf-0002 — Git-aware discovery scanner
- 2026/lf-0002.md
- contracts/models.py
- cli.py
- lf-0002.implement.claude.md
- lf-0002.review.claude.md
- test_logging.py
- test_sql_audit.py
- Review: lf-0003 — Domain contracts + SQLite storage + snapshot lifecycle
- 2026/lf-0003.md
- test_storage.py
- lf-0003.implement.claude.md
- lf-0003.review.claude.md
- test_commands.py
- test_cli.py
- test_flow.py
- ws
- ParserAdapter
- Review: lf-0004 — CLI vertical slice (doctor/init/status/index)
- 2026/lf-0004.md
- lf-0004.implement.claude.md
- lf-0004.review.claude.md
- test_hardening.py
- package.json
- readme.md
- ScanResult
- Code Atlas Forge
- Modelo de Ameaças — Code Atlas Forge (Cycle 1)
- ResolutionStatus
- bench
- Review: lf-0005 — Security hardening, observability, CI, benchmarks
- 2026/lf-0005.md
- Benchmarks do Code Atlas
- lf-0005.implement.claude.md
- lf-0005.review.claude.md
- .insert_inventory_rows
- CompletedProcess
- Connection

## God Nodes (most connected - your core abstractions)
1. `scan()` - 44 edges
2. `Store` - 28 edges
3. `Snapshot` - 18 edges
4. `paths()` - 16 edges
5. `_classify_and_hash()` - 14 edges
6. `migrate()` - 14 edges
7. `SnapshotStatus` - 13 edges
8. `probe_git()` - 11 edges
9. `_filter_dirs()` - 11 edges
10. `_walk_paths()` - 11 edges

## Surprising Connections (you probably didn't know these)
- `test_snapshot_status_coercion()` --calls--> `Snapshot`  [INFERRED]
  tests/contracts/test_models.py → src/code_atlas/contracts/models.py
- `test_snapshot_status_invalid_rejected()` --calls--> `Snapshot`  [INFERRED]
  tests/contracts/test_models.py → src/code_atlas/contracts/models.py
- `bench()` --calls--> `scan()`  [INFERRED]
  benchmarks/bench_scan.py → src/code_atlas/discovery/scanner.py
- `test_pathological_depth_capped()` --calls--> `scan()`  [INFERRED]
  tests/e2e/test_hardening.py → src/code_atlas/discovery/scanner.py
- `test_symlink_to_etc_warns_not_follows()` --calls--> `scan()`  [INFERRED]
  tests/e2e/test_hardening.py → src/code_atlas/discovery/scanner.py

## Import Cycles
- None detected.

## Communities (51 total, 5 thin omitted)

### Community 0 - "Review: lf-0001 — Cycle 1 scaffold"
Cohesion: 0.33
Nodes (5): Critérios de aceitação, Findings, Resultado, Review: lf-0001 — Cycle 1 scaffold, Verificação executada

### Community 1 - "2026/lf-0001.md"
Cohesion: 0.33
Nodes (5): Acceptance Criteria, Constraints, Context, Grill Gate, Review Notes

### Community 2 - "lf-0001.review.claude.md"
Cohesion: 0.40
Nodes (4): Acceptance Criteria, Constraints, Context, Review Notes

### Community 3 - "scan"
Cohesion: 0.14
Nodes (41): needs_git, Limites e comportamento do scanner., ScanOptions, Varre `root` e retorna um `ScanResult` determinístico. Ctrl-C ou timeout…, scan(), git(), nogit(), Path (+33 more)

### Community 4 - "scanner.py"
Cohesion: 0.06
Nodes (50): GitIgnoreStack, GitInfo, ScanOptions, Discovery Git-aware: inventário de arquivos, fingerprints e manifest., _basename_match(), GitInfo, is_default_ignored(), is_secret() (+42 more)

### Community 5 - "migrate"
Cohesion: 0.09
Nodes (29): Exception, fixture, connect(), fts5_available(), Connection, Path, Fábrica de conexões SQLite (Blueprint ADR-006). Pragmas obrigatórios: WAL,…, Abre conexão configurada para o workspace Atlas. ``read_only=True`` usa URI… (+21 more)

### Community 6 - "gitignore.py"
Cohesion: 0.14
Nodes (11): GitIgnoreScope, GitIgnoreStack, Path, Parser mínimo de `.gitignore` (fallback sem git). Best-effort: suporta `*`,…, True se ignorado; o escopo mais profundo que decide vence., Converte um glob gitignore em regex sobre path relativo POSIX., Regras de um único `.gitignore`, aplicáveis a paths relativos a seu dir., True/False se decidido por este escopo; None se nenhuma regra casa. (+3 more)

### Community 7 - "Snapshot"
Cohesion: 0.12
Nodes (16): Protocol, Row, Ocorrência imutável de um estado do repositório., Snapshot, GraphStore, IndexCoordinator, Path, ScanResult (+8 more)

### Community 8 - "Store"
Cohesion: 0.09
Nodes (13): Repositório indexado; `id` independe do pathname local., Repository, Persistência SQLite (WAL, migrações, snapshots)., Connection, Acesso a dados do grafo (GraphStore parcial do Cycle 1). Somente queries…, Marca o novo READY e torna STALE os anteriores (mesma transação)., Lista violações FK pendentes (integrity gate antes do READY)., Relações cujos endpoints apontam fora do próprio snapshot. (+5 more)

### Community 9 - "IndexCoordinator"
Cohesion: 0.15
Nodes (13): ScanResult, _analyzer_versions(), IndexCoordinator, IndexOutcome, Connection, Path, IndexCoordinator: scan → BUILDING → validação → READY atômico (Blueprint §6).…, Integrity gate antes do READY; levanta para abortar a transação. (+5 more)

### Community 10 - "Relation"
Cohesion: 0.11
Nodes (15): _coerce(), Evidence, LogicalSymbol, Aresta tipada com proveniência obrigatória (Blueprint ADR-012)., Trecho observável que sustenta um fato (extrator + versão + hash)., Símbolo com identidade lógica estável por repositório., Relation, Testes dos contratos de domínio (vocabulário do Blueprint §5). (+7 more)

### Community 11 - "Review: lf-0002 — Git-aware discovery scanner"
Cohesion: 0.33
Nodes (5): Critérios, Findings, Resultado, Review: lf-0002 — Git-aware discovery scanner, Verificação

### Community 12 - "2026/lf-0002.md"
Cohesion: 0.33
Nodes (5): Acceptance Criteria, Constraints, Context, Grill Gate, Review Notes

### Community 13 - "contracts/models.py"
Cohesion: 0.19
Nodes (16): ConfidenceClass, EdgeKind, NodeKind, Vocabulário canônico do domínio (Blueprint §5). Valores devem casar exatamente…, Tipos de nó do grafo (Blueprint §5 "Tipos iniciais")., Vocabulário de arestas (Blueprint §5)., Classe de confiança da evidência — não é probabilidade (Blueprint §5)., Estados do ciclo de vida de um snapshot (Blueprint §5). (+8 more)

### Community 14 - "cli.py"
Cohesion: 0.10
Nodes (34): ArgumentParser, Connection, Namespace, build_parser(), Check, db_path(), _first_writable_ancestor(), main() (+26 more)

### Community 15 - "lf-0002.implement.claude.md"
Cohesion: 0.40
Nodes (4): Acceptance Criteria, Constraints, Context, Review Notes

### Community 16 - "lf-0002.review.claude.md"
Cohesion: 0.40
Nodes (4): Acceptance Criteria, Constraints, Context, Review Notes

### Community 17 - "test_logging.py"
Cohesion: 0.10
Nodes (26): CaptureFixture, Logger, LogRecord, Logging estruturado e redaction (sem telemetria, sem egress)., get_logger(), JsonFormatter, Logging estruturado com redaction (Blueprint §9, ADR-014). Sem telemetria, sem…, Aplica as regras de mascaramento a uma string. (+18 more)

### Community 18 - "test_sql_audit.py"
Cohesion: 0.40
Nodes (4): Call, _first_arg_dynamic(), Auditoria estática: nenhuma query construída por concatenação/f-string.…, test_no_dynamic_sql_in_execute_calls()

### Community 19 - "Review: lf-0003 — Domain contracts + SQLite storage + snapshot lifecycle"
Cohesion: 0.33
Nodes (5): Critérios, Findings (resolvidos em implementação), Resultado, Review: lf-0003 — Domain contracts + SQLite storage + snapshot lifecycle, Verificação

### Community 20 - "2026/lf-0003.md"
Cohesion: 0.33
Nodes (5): Acceptance Criteria, Constraints, Context, Grill Gate, Review Notes

### Community 21 - "test_storage.py"
Cohesion: 0.29
Nodes (16): Connection, IndexCoordinator, Path, Testes de storage: migrações, pragmas, snapshot lifecycle, concorrência., _tables(), test_atomic_publish_single_commit(), test_changed_inventory_new_snapshot(), test_concurrent_readers_during_write() (+8 more)

### Community 22 - "lf-0003.implement.claude.md"
Cohesion: 0.40
Nodes (4): Acceptance Criteria, Constraints, Context, Review Notes

### Community 23 - "lf-0003.review.claude.md"
Cohesion: 0.40
Nodes (4): Acceptance Criteria, Constraints, Context, Review Notes

### Community 24 - "test_commands.py"
Cohesion: 0.22
Nodes (17): CaptureFixture, Path, Testes do vertical slice do CLI (lf-0004)., test_each_subcommand_help(), test_exit_code_documented_in_help(), test_help_clean_of_future_verbs(), test_index_and_status_flow(), test_index_empty_dir() (+9 more)

### Community 25 - "test_cli.py"
Cohesion: 0.24
Nodes (12): CaptureFixture, MonkeyPatch, Path, Testes do vertical slice mínimo do CLI (lf-0001)., test_doctor_human_output_ok(), test_doctor_json(), test_help_lists_only_real_commands(), test_init_creates_config() (+4 more)

### Community 26 - "test_flow.py"
Cohesion: 0.22
Nodes (12): env(), CaptureFixture, fixture, MonkeyPatch, Path, Fluxo ponta a ponta: init → index → status → doctor (lf-0004)., Workspace isolado com repo git real a partir do fixture., `src/` não importa socket/urllib/requests — operação 100% offline. (+4 more)

### Community 27 - "ws"
Cohesion: 0.28
Nodes (8): fixture, MonkeyPatch, Path, Fixtures de teste do CLI: ambiente XDG isolado por teste., Isola config/data dirs para não tocar o workspace real do usuário., Cópia do fixture commitado + arquivos ignorados/segredo criados em runtime., sample(), ws()

### Community 28 - "ParserAdapter"
Cohesion: 0.29
Nodes (3): ParserAdapter, Adaptador AST (Tree-sitter, Cycle 2) com capability matrix declarada., Ex.: ``symbols``, ``imports``, ``calls``, ``docstrings``.

### Community 29 - "Review: lf-0004 — CLI vertical slice (doctor/init/status/index)"
Cohesion: 0.33
Nodes (5): Critérios, Findings, Resultado, Review: lf-0004 — CLI vertical slice (doctor/init/status/index), Verificação

### Community 30 - "2026/lf-0004.md"
Cohesion: 0.33
Nodes (5): Acceptance Criteria, Constraints, Context, Grill Gate, Review Notes

### Community 31 - "lf-0004.implement.claude.md"
Cohesion: 0.40
Nodes (4): Acceptance Criteria, Constraints, Context, Review Notes

### Community 32 - "lf-0004.review.claude.md"
Cohesion: 0.40
Nodes (4): Acceptance Criteria, Constraints, Context, Review Notes

### Community 33 - "test_hardening.py"
Cohesion: 0.29
Nodes (9): MonkeyPatch, Path, Hardening: confinamento de subprocess, traversal, DoS básico (lf-0005)., test_db_path_confined_to_data_dir(), test_index_rejects_filesystem_root(), test_index_rejects_traversal(), test_pathological_depth_capped(), test_symlink_to_etc_warns_not_follows() (+1 more)

### Community 39 - "Code Atlas Forge"
Cohesion: 0.22
Nodes (8): Code Atlas Forge, Desenvolvimento, Estrutura do repositório, Instalação, Licença, Limitações do Cycle 1, Princípios, Uso rápido

### Community 40 - "Modelo de Ameaças — Code Atlas Forge (Cycle 1)"
Cohesion: 0.25
Nodes (7): Ativos, Entradas controladas por atacante, Fronteiras de confiança, Mitigações implementadas (verificadas por teste), Modelo de Ameaças — Code Atlas Forge (Cycle 1), Riscos residuais (documentados, aceitos no Cycle 1), Verificações relacionadas

### Community 41 - "ResolutionStatus"
Cohesion: 0.32
Nodes (6): Status de resolução de um alvo de relação (Blueprint §5)., ResolutionStatus, Ocorrência de um símbolo num snapshot (span/hash são da ocorrência)., SymbolOccurrence, Resolve ocorrências para alvos canônicos (SCIP/LSP, Cycle 3)., SymbolResolver

### Community 42 - "bench"
Cohesion: 0.60
Nodes (5): bench(), main(), Path, Gera corpus determinístico: mesmos parâmetros → mesma árvore., synth_corpus()

### Community 43 - "Review: lf-0005 — Security hardening, observability, CI, benchmarks"
Cohesion: 0.33
Nodes (5): Critérios, Findings, Resultado, Review: lf-0005 — Security hardening, observability, CI, benchmarks, Verificação

### Community 44 - "2026/lf-0005.md"
Cohesion: 0.33
Nodes (5): Acceptance Criteria, Constraints, Context, Grill Gate, Review Notes

### Community 45 - "Benchmarks do Code Atlas"
Cohesion: 0.40
Nodes (4): Benchmarks do Code Atlas, Regras, Saída de referência (Cycle 1), Uso

### Community 46 - "lf-0005.implement.claude.md"
Cohesion: 0.40
Nodes (4): Acceptance Criteria, Constraints, Context, Review Notes

### Community 47 - "lf-0005.review.claude.md"
Cohesion: 0.40
Nodes (4): Acceptance Criteria, Constraints, Context, Review Notes

### Community 48 - ".insert_inventory_rows"
Cohesion: 0.40
Nodes (3): SourceFile, Dedup por (repository_id, relative_path) — chave de conteúdo estável., Insere (snapshot, source_file) com atributos da ocorrência.

## Knowledge Gaps
- **100 isolated node(s):** `Princípios`, `Instalação`, `Uso rápido`, `Limitações do Cycle 1`, `Estrutura do repositório` (+95 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **5 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `scan()` connect `scan` to `IndexCoordinator`, `bench`, `scanner.py`, `test_hardening.py`?**
  _High betweenness centrality (0.110) - this node is a cross-community bridge._
- **Why does `Store` connect `Store` to `Snapshot`, `IndexCoordinator`, `contracts/models.py`, `cli.py`, `.insert_inventory_rows`, `test_storage.py`?**
  _High betweenness centrality (0.101) - this node is a cross-community bridge._
- **Why does `probe_git()` connect `scanner.py` to `scan`, `cli.py`?**
  _High betweenness centrality (0.060) - this node is a cross-community bridge._
- **Are the 32 inferred relationships involving `scan()` (e.g. with `bench()` and `ScanWarning`) actually correct?**
  _`scan()` has 32 INFERRED edges - model-reasoned connections that need verification._
- **Are the 9 inferred relationships involving `Store` (e.g. with `run_init()` and `run_status()`) actually correct?**
  _`Store` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `Snapshot` (e.g. with `SnapshotStatus` and `GraphStore`) actually correct?**
  _`Snapshot` has 6 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Princípios`, `Instalação`, `Uso rápido` to the rest of the system?**
  _100 weakly-connected nodes found - possible documentation gaps or missing edges._