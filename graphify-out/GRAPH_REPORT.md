# Graph Report - code-atlas-forge  (2026-10-24)

## Corpus Check
- 62 files · ~23,631 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 366 nodes · 691 edges · 25 communities
- Extraction: 88% EXTRACTED · 12% INFERRED · 0% AMBIGUOUS · INFERRED: 80 edges (avg confidence: 0.89)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `8d1551b9`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Review: lf-0001 — Cycle 1 scaffold
- 2026/lf-0001.md
- lf-0001.review.claude.md
- scan
- scanner.py
- test_storage.py
- GitIgnoreStack
- ports.py
- Store
- coordinator.py
- Relation
- Review: lf-0002 — Git-aware discovery scanner
- 2026/lf-0002.md
- contracts/models.py
- storage/conftest.py
- lf-0002.implement.claude.md
- lf-0002.review.claude.md
- LogicalSymbol
- test_sql_audit.py
- Review: lf-0003 — Domain contracts + SQLite storage + snapshot lifecycle
- 2026/lf-0003.md
- Snapshot
- lf-0003.implement.claude.md
- lf-0003.review.claude.md
- .insert_inventory_rows

## God Nodes (most connected - your core abstractions)
1. `scan()` - 42 edges
2. `Store` - 28 edges
3. `Snapshot` - 18 edges
4. `ScanOptions` - 17 edges
5. `paths()` - 16 edges
6. `SnapshotStatus` - 15 edges
7. `_classify_and_hash()` - 15 edges
8. `migrate()` - 13 edges
9. `_Budget` - 13 edges
10. `_filter_dirs()` - 12 edges

## Surprising Connections (you probably didn't know these)
- `test_snapshot_status_coercion()` --calls--> `Snapshot`  [INFERRED]
  tests/contracts/test_models.py → src/code_atlas/contracts/models.py
- `test_snapshot_status_invalid_rejected()` --calls--> `Snapshot`  [INFERRED]
  tests/contracts/test_models.py → src/code_atlas/contracts/models.py
- `test_changed_inventory_new_snapshot()` --uses--> `SnapshotStatus`  [INFERRED]
  tests/storage/test_storage.py → src/code_atlas/contracts/enums.py
- `test_failed_index_preserves_ready()` --uses--> `SnapshotStatus`  [INFERRED]
  tests/storage/test_storage.py → src/code_atlas/contracts/enums.py
- `test_snapshot_lifecycle_happy_path()` --uses--> `SnapshotStatus`  [INFERRED]
  tests/storage/test_storage.py → src/code_atlas/contracts/enums.py

## Import Cycles
- None detected.

## Communities (25 total, 0 thin omitted)

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
Nodes (41): fixture, needs_git, Varre `root` e retorna um `ScanResult` determinístico. Ctrl-C ou timeout…, scan(), git(), nogit(), Path, Fixtures compartilhadas dos testes de discovery. (+33 more)

### Community 4 - "scanner.py"
Cohesion: 0.08
Nodes (51): CompletedProcess, Discovery Git-aware: inventário de arquivos, fingerprints e manifest., _basename_match(), GitInfo, is_default_ignored(), is_secret(), language_for(), Modelos do resultado de scan (Cycle 1). Sem persistência: o scanner retorna… (+43 more)

### Community 5 - "test_storage.py"
Cohesion: 0.10
Nodes (38): Exception, connect(), fts5_available(), Connection, Path, Fábrica de conexões SQLite (Blueprint ADR-006). Pragmas obrigatórios: WAL,…, Abre conexão configurada para o workspace Atlas. ``read_only=True`` usa URI…, True se a build do sqlite suporta FTS5. (+30 more)

### Community 6 - "GitIgnoreStack"
Cohesion: 0.14
Nodes (11): GitIgnoreScope, GitIgnoreStack, Path, Parser mínimo de `.gitignore` (fallback sem git). Best-effort: suporta `*`,…, True se ignorado; o escopo mais profundo que decide vence., Converte um glob gitignore em regex sobre path relativo POSIX., Regras de um único `.gitignore`, aplicáveis a paths relativos a seu dir., True/False se decidido por este escopo; None se nenhuma regra casa. (+3 more)

### Community 7 - "ports.py"
Cohesion: 0.08
Nodes (22): Protocol, ScanOptions, Status de resolução de um alvo de relação (Blueprint §5)., ResolutionStatus, Ocorrência de um símbolo num snapshot (span/hash são da ocorrência)., SymbolOccurrence, GraphStore, IndexCoordinator (+14 more)

### Community 8 - "Store"
Cohesion: 0.09
Nodes (12): Repositório indexado; `id` independe do pathname local., Repository, Connection, Acesso a dados do grafo (GraphStore parcial do Cycle 1). Somente queries…, Marca o novo READY e torna STALE os anteriores (mesma transação)., Lista violações FK pendentes (integrity gate antes do READY)., Relações cujos endpoints apontam fora do próprio snapshot., Repositório de dados sobre uma conexão sqlite3 já configurada. (+4 more)

### Community 9 - "coordinator.py"
Cohesion: 0.15
Nodes (13): _analyzer_versions(), IndexCoordinator, IndexOutcome, Connection, Path, ScanResult, IndexCoordinator: scan → BUILDING → validação → READY atômico (Blueprint §6).…, Integrity gate antes do READY; levanta para abortar a transação. (+5 more)

### Community 10 - "Relation"
Cohesion: 0.16
Nodes (11): Evidence, Aresta tipada com proveniência obrigatória (Blueprint ADR-012)., Trecho observável que sustenta um fato (extrator + versão + hash)., Relation, Testes dos contratos de domínio (vocabulário do Blueprint §5)., test_evidence_requires_provenance_fields(), test_relation_accepts_unresolved_target(), test_relation_requires_resolver_provenance() (+3 more)

### Community 11 - "Review: lf-0002 — Git-aware discovery scanner"
Cohesion: 0.33
Nodes (5): Critérios, Findings, Resultado, Review: lf-0002 — Git-aware discovery scanner, Verificação

### Community 12 - "2026/lf-0002.md"
Cohesion: 0.33
Nodes (5): Acceptance Criteria, Constraints, Context, Grill Gate, Review Notes

### Community 13 - "contracts/models.py"
Cohesion: 0.19
Nodes (16): ConfidenceClass, EdgeKind, NodeKind, Vocabulário canônico do domínio (Blueprint §5). Valores devem casar exatamente…, Tipos de nó do grafo (Blueprint §5 "Tipos iniciais")., Vocabulário de arestas (Blueprint §5)., Classe de confiança da evidência — não é probabilidade (Blueprint §5)., Estados do ciclo de vida de um snapshot (Blueprint §5). (+8 more)

### Community 14 - "storage/conftest.py"
Cohesion: 0.32
Nodes (7): coordinator(), corpus(), db(), Connection, IndexCoordinator, Path, Fixtures de storage/indexing: DB migrado em tmp_path.

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
Cohesion: 0.47
Nodes (4): Row, Ocorrência imutável de um estado do repositório., Snapshot, _snapshot_from_row()

### Community 22 - "lf-0003.implement.claude.md"
Cohesion: 0.40
Nodes (4): Acceptance Criteria, Constraints, Context, Review Notes

### Community 23 - "lf-0003.review.claude.md"
Cohesion: 0.40
Nodes (4): Acceptance Criteria, Constraints, Context, Review Notes

### Community 24 - ".insert_inventory_rows"
Cohesion: 0.40
Nodes (3): SourceFile, Dedup por (repository_id, relative_path) — chave de conteúdo estável., Insere (snapshot, source_file) com atributos da ocorrência.

## Knowledge Gaps
- **47 isolated node(s):** `Context`, `Acceptance Criteria`, `Constraints`, `Review Notes`, `Context` (+42 more)
  These have ≤1 connection - possible missing edges or undocumented components.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `SnapshotStatus` connect `contracts/models.py` to `Store`, `coordinator.py`, `test_storage.py`, `Snapshot`?**
  _High betweenness centrality (0.198) - this node is a cross-community bridge._
- **Why does `WarningKind` connect `scanner.py` to `scan`, `contracts/models.py`?**
  _High betweenness centrality (0.172) - this node is a cross-community bridge._
- **Why does `Store` connect `Store` to `test_storage.py`, `coordinator.py`, `contracts/models.py`, `Snapshot`, `.insert_inventory_rows`?**
  _High betweenness centrality (0.146) - this node is a cross-community bridge._
- **Are the 29 inferred relationships involving `scan()` (e.g. with `ScanMode` and `WarningKind`) actually correct?**
  _`scan()` has 29 INFERRED edges - model-reasoned connections that need verification._
- **Are the 7 inferred relationships involving `Store` (e.g. with `IndexCoordinator` and `SnapshotStatus`) actually correct?**
  _`Store` has 7 INFERRED edges - model-reasoned connections that need verification._
- **Are the 6 inferred relationships involving `Snapshot` (e.g. with `SnapshotStatus` and `GraphStore`) actually correct?**
  _`Snapshot` has 6 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `ScanOptions` (e.g. with `_Budget` and `_classify_and_hash()`) actually correct?**
  _`ScanOptions` has 5 INFERRED edges - model-reasoned connections that need verification._