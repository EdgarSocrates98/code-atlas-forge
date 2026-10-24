# Graph Report - code-atlas-forge  (2026-10-24)

## Corpus Check
- 74 files · ~27,117 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 479 nodes · 851 edges · 39 communities (36 shown, 3 thin omitted)
- Extraction: 89% EXTRACTED · 11% INFERRED · 0% AMBIGUOUS · INFERRED: 90 edges (avg confidence: 0.89)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `853e9a94`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Review: lf-0001 — Cycle 1 scaffold
- 2026/lf-0001.md
- lf-0001.review.claude.md
- scan
- scanner.py
- test_storage.py
- gitignore.py
- ports.py
- Store
- IndexCoordinator
- Relation
- Review: lf-0002 — Git-aware discovery scanner
- 2026/lf-0002.md
- contracts/models.py
- cli.py
- lf-0002.implement.claude.md
- lf-0002.review.claude.md
- LogicalSymbol
- test_sql_audit.py
- Review: lf-0003 — Domain contracts + SQLite storage + snapshot lifecycle
- 2026/lf-0003.md
- Snapshot
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
- .scan
- package.json
- readme.md
- ScanResult

## God Nodes (most connected - your core abstractions)
1. `scan()` - 42 edges
2. `Store` - 28 edges
3. `Snapshot` - 18 edges
4. `ScanOptions` - 17 edges
5. `paths()` - 16 edges
6. `_classify_and_hash()` - 15 edges
7. `migrate()` - 14 edges
8. `SnapshotStatus` - 13 edges
9. `_Budget` - 13 edges
10. `_filter_dirs()` - 12 edges

## Surprising Connections (you probably didn't know these)
- `test_snapshot_status_coercion()` --calls--> `Snapshot`  [INFERRED]
  tests/contracts/test_models.py → src/code_atlas/contracts/models.py
- `test_snapshot_status_invalid_rejected()` --calls--> `Snapshot`  [INFERRED]
  tests/contracts/test_models.py → src/code_atlas/contracts/models.py
- `test_evidence_requires_provenance_fields()` --calls--> `Evidence`  [INFERRED]
  tests/contracts/test_models.py → src/code_atlas/contracts/models.py
- `test_relation_accepts_unresolved_target()` --calls--> `Relation`  [INFERRED]
  tests/contracts/test_models.py → src/code_atlas/contracts/models.py
- `test_relation_requires_resolver_provenance()` --calls--> `Relation`  [INFERRED]
  tests/contracts/test_models.py → src/code_atlas/contracts/models.py

## Import Cycles
- None detected.

## Communities (39 total, 3 thin omitted)

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
Nodes (40): needs_git, Varre `root` e retorna um `ScanResult` determinístico. Ctrl-C ou timeout…, scan(), git(), nogit(), Path, Fixtures compartilhadas dos testes de discovery., Repositório git básico com 1 commit e 2 arquivos. (+32 more)

### Community 4 - "scanner.py"
Cohesion: 0.07
Nodes (54): CompletedProcess, GitIgnoreStack, True se ignorado; o escopo mais profundo que decide vence., Pilha de escopos `.gitignore`; a decisão mais profunda e tardia vence., Discovery Git-aware: inventário de arquivos, fingerprints e manifest., _basename_match(), GitInfo, is_default_ignored() (+46 more)

### Community 5 - "test_storage.py"
Cohesion: 0.10
Nodes (38): Exception, connect(), fts5_available(), Connection, Path, Fábrica de conexões SQLite (Blueprint ADR-006). Pragmas obrigatórios: WAL,…, Abre conexão configurada para o workspace Atlas. ``read_only=True`` usa URI…, True se a build do sqlite suporta FTS5. (+30 more)

### Community 6 - "gitignore.py"
Cohesion: 0.19
Nodes (8): GitIgnoreScope, Path, Parser mínimo de `.gitignore` (fallback sem git). Best-effort: suporta `*`,…, Converte um glob gitignore em regex sobre path relativo POSIX., Regras de um único `.gitignore`, aplicáveis a paths relativos a seu dir., True/False se decidido por este escopo; None se nenhuma regra casa., _Rule, _translate()

### Community 7 - "ports.py"
Cohesion: 0.16
Nodes (14): Protocol, Status de resolução de um alvo de relação (Blueprint §5)., ResolutionStatus, Ocorrência de um símbolo num snapshot (span/hash são da ocorrência)., SymbolOccurrence, IndexCoordinator, Ports hexagonais do domínio (Blueprint §3). Interfaces ``Protocol`` — apenas…, Produz o inventário de arquivos de uma raiz (implementado: discovery). (+6 more)

### Community 8 - "Store"
Cohesion: 0.08
Nodes (12): SourceFile, Connection, Marca o novo READY e torna STALE os anteriores (mesma transação)., Dedup por (repository_id, relative_path) — chave de conteúdo estável., Insere (snapshot, source_file) com atributos da ocorrência., Lista violações FK pendentes (integrity gate antes do READY)., Relações cujos endpoints apontam fora do próprio snapshot., Repositório de dados sobre uma conexão sqlite3 já configurada. (+4 more)

### Community 9 - "IndexCoordinator"
Cohesion: 0.10
Nodes (21): fixture, ScanResult, _analyzer_versions(), IndexCoordinator, IndexOutcome, Connection, Path, IndexCoordinator: scan → BUILDING → validação → READY atômico (Blueprint §6).… (+13 more)

### Community 10 - "Relation"
Cohesion: 0.24
Nodes (8): Aresta tipada com proveniência obrigatória (Blueprint ADR-012)., Relation, Testes dos contratos de domínio (vocabulário do Blueprint §5)., test_relation_accepts_unresolved_target(), test_relation_requires_resolver_provenance(), test_relation_requires_target(), test_snapshot_status_coercion(), test_snapshot_status_invalid_rejected()

### Community 11 - "Review: lf-0002 — Git-aware discovery scanner"
Cohesion: 0.33
Nodes (5): Critérios, Findings, Resultado, Review: lf-0002 — Git-aware discovery scanner, Verificação

### Community 12 - "2026/lf-0002.md"
Cohesion: 0.33
Nodes (5): Acceptance Criteria, Constraints, Context, Grill Gate, Review Notes

### Community 13 - "contracts/models.py"
Cohesion: 0.14
Nodes (19): ConfidenceClass, EdgeKind, NodeKind, Vocabulário canônico do domínio (Blueprint §5). Valores devem casar exatamente…, Tipos de nó do grafo (Blueprint §5 "Tipos iniciais")., Vocabulário de arestas (Blueprint §5)., Classe de confiança da evidência — não é probabilidade (Blueprint §5)., Contratos de domínio do Code Atlas (modelos, enums, ports). O domínio não… (+11 more)

### Community 14 - "cli.py"
Cohesion: 0.15
Nodes (24): ArgumentParser, Namespace, build_parser(), Check, db_path(), _first_writable_ancestor(), main(), _open_db() (+16 more)

### Community 15 - "lf-0002.implement.claude.md"
Cohesion: 0.40
Nodes (4): Acceptance Criteria, Constraints, Context, Review Notes

### Community 16 - "lf-0002.review.claude.md"
Cohesion: 0.40
Nodes (4): Acceptance Criteria, Constraints, Context, Review Notes

### Community 17 - "LogicalSymbol"
Cohesion: 0.29
Nodes (4): _coerce(), LogicalSymbol, Símbolo com identidade lógica estável por repositório., test_logical_vs_occurrence_identity()

### Community 18 - "test_sql_audit.py"
Cohesion: 0.40
Nodes (4): Call, _first_arg_dynamic(), Auditoria estática: nenhuma query construída por concatenação/f-string.…, test_no_dynamic_sql_in_execute_calls()

### Community 19 - "Review: lf-0003 — Domain contracts + SQLite storage + snapshot lifecycle"
Cohesion: 0.33
Nodes (5): Critérios, Findings (resolvidos em implementação), Resultado, Review: lf-0003 — Domain contracts + SQLite storage + snapshot lifecycle, Verificação

### Community 20 - "2026/lf-0003.md"
Cohesion: 0.33
Nodes (5): Acceptance Criteria, Constraints, Context, Grill Gate, Review Notes

### Community 21 - "Snapshot"
Cohesion: 0.22
Nodes (9): Row, Estados do ciclo de vida de um snapshot (Blueprint §5)., SnapshotStatus, Ocorrência imutável de um estado do repositório., Snapshot, GraphStore, Persistência do grafo tipado (implementado parcialmente: storage)., Acesso a dados do grafo (GraphStore parcial do Cycle 1). Somente queries… (+1 more)

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

### Community 33 - ".scan"
Cohesion: 0.40
Nodes (3): ScanOptions, Path, ScanResult

## Knowledge Gaps
- **67 isolated node(s):** `Context`, `Acceptance Criteria`, `Constraints`, `Review Notes`, `Context` (+62 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **3 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `run_status()` connect `cli.py` to `Store`, `scanner.py`, `test_storage.py`?**
  _High betweenness centrality (0.135) - this node is a cross-community bridge._
- **Why does `Store` connect `Store` to `test_storage.py`, `IndexCoordinator`, `contracts/models.py`, `cli.py`, `Snapshot`?**
  _High betweenness centrality (0.133) - this node is a cross-community bridge._
- **Why does `probe_git()` connect `scanner.py` to `scan`, `cli.py`?**
  _High betweenness centrality (0.121) - this node is a cross-community bridge._
- **Are the 29 inferred relationships involving `scan()` (e.g. with `ScanMode` and `WarningKind`) actually correct?**
  _`scan()` has 29 INFERRED edges - model-reasoned connections that need verification._
- **Are the 9 inferred relationships involving `Store` (e.g. with `run_init()` and `run_status()`) actually correct?**
  _`Store` has 9 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `Snapshot` (e.g. with `SnapshotStatus` and `GraphStore`) actually correct?**
  _`Snapshot` has 6 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `ScanOptions` (e.g. with `_Budget` and `_classify_and_hash()`) actually correct?**
  _`ScanOptions` has 5 INFERRED edges - model-reasoned connections that need verification._