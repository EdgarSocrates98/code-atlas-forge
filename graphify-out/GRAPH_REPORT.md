# Graph Report - code-atlas-forge  (2026-10-24)

## Corpus Check
- 46 files · ~17,510 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 160 nodes · 336 edges · 17 communities
- Extraction: 88% EXTRACTED · 12% INFERRED · 0% AMBIGUOUS · INFERRED: 42 edges (avg confidence: 0.89)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `ed443009`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- Review: lf-0001 — Cycle 1 scaffold
- 2026/lf-0001.md
- lf-0001.review.claude.md
- scan
- models.py
- scanner.py
- GitIgnoreScope
- _filter_dirs
- ScanOptions
- git
- GitIgnoreStack
- Review: lf-0002 — Git-aware discovery scanner
- 2026/lf-0002.md
- __init__.py
- ScanResult
- lf-0002.implement.claude.md
- lf-0002.review.claude.md

## God Nodes (most connected - your core abstractions)
1. `scan()` - 42 edges
2. `ScanOptions` - 17 edges
3. `paths()` - 16 edges
4. `_classify_and_hash()` - 15 edges
5. `_Budget` - 13 edges
6. `_filter_dirs()` - 12 edges
7. `_walk_paths()` - 12 edges
8. `ScanWarning` - 11 edges
9. `WarningKind` - 10 edges
10. `GitIgnoreStack` - 8 edges

## Surprising Connections (you probably didn't know these)
- `test_circular_symlink_no_loop()` --calls--> `ScanOptions`  [EXTRACTED]
  tests/discovery/test_scanner.py → src/code_atlas/discovery/models.py
- `test_git_uncommitted_and_untracked()` --calls--> `ScanOptions`  [EXTRACTED]
  tests/discovery/test_scanner.py → src/code_atlas/discovery/models.py
- `test_max_depth_limit()` --calls--> `ScanOptions`  [EXTRACTED]
  tests/discovery/test_scanner.py → src/code_atlas/discovery/models.py
- `test_max_files_limit()` --calls--> `ScanOptions`  [EXTRACTED]
  tests/discovery/test_scanner.py → src/code_atlas/discovery/models.py
- `test_oversize_file_no_hash()` --calls--> `ScanOptions`  [EXTRACTED]
  tests/discovery/test_scanner.py → src/code_atlas/discovery/models.py

## Import Cycles
- None detected.

## Communities (17 total, 0 thin omitted)

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
Cohesion: 0.19
Nodes (33): needs_git, Varre `root` e retorna um `ScanResult` determinístico. Ctrl-C ou timeout…, scan(), paths(), Path, Testes do scanner Git-aware (lf-0002)., test_binary_and_malformed_utf8(), test_circular_symlink_no_loop() (+25 more)

### Community 4 - "models.py"
Cohesion: 0.18
Nodes (15): _basename_match(), is_default_ignored(), is_secret(), language_for(), Modelos do resultado de scan (Cycle 1). Sem persistência: o scanner retorna…, Linguagem preliminar pela extensão; `unknown`/`other` quando ausente., True se o basename do path casa padrões de segredo., True se qualquer segmento/basename casa os ignores padrão do Atlas. (+7 more)

### Community 5 - "scanner.py"
Cohesion: 0.23
Nodes (12): CompletedProcess, GitInfo, Estado git detectado na raiz., _git(), _git_lines(), _git_paths(), probe_git(), Scanner Git-aware de inventário (Blueprint §4.1 / ADR-014). Perímetro de… (+4 more)

### Community 6 - "GitIgnoreScope"
Cohesion: 0.20
Nodes (7): GitIgnoreScope, Path, Converte um glob gitignore em regex sobre path relativo POSIX., Regras de um único `.gitignore`, aplicáveis a paths relativos a seu dir., True/False se decidido por este escopo; None se nenhuma regra casa., _Rule, _translate()

### Community 7 - "_filter_dirs"
Cohesion: 0.25
Nodes (11): _depth(), _filter_dirs(), _hash_file(), Path, True se `path` é symlink cujo alvo resolve fora de `root_real`., Prune de diretórios: symlinks de dir, ignores e profundidade., Gera (abs_path, rel_posix, ignored) via os.walk + ignores best-effort., SHA-256 por streaming; None em erro de leitura. (+3 more)

### Community 8 - "ScanOptions"
Cohesion: 0.31
Nodes (6): Limites e comportamento do scanner., Warning tipado; `path` é relativo POSIX quando aplicável., ScanOptions, ScanWarning, _Budget, Rastreia limites, warnings e deadline do scan.

### Community 9 - "git"
Cohesion: 0.39
Nodes (7): fixture, git(), nogit(), Path, Fixtures compartilhadas dos testes de discovery., Repositório git básico com 1 commit e 2 arquivos., repo()

### Community 10 - "GitIgnoreStack"
Cohesion: 0.29
Nodes (4): GitIgnoreStack, Parser mínimo de `.gitignore` (fallback sem git). Best-effort: suporta `*`,…, True se ignorado; o escopo mais profundo que decide vence., Pilha de escopos `.gitignore`; a decisão mais profunda e tardia vence.

### Community 11 - "Review: lf-0002 — Git-aware discovery scanner"
Cohesion: 0.33
Nodes (5): Critérios, Findings, Resultado, Review: lf-0002 — Git-aware discovery scanner, Verificação

### Community 12 - "2026/lf-0002.md"
Cohesion: 0.33
Nodes (5): Acceptance Criteria, Constraints, Context, Grill Gate, Review Notes

### Community 13 - "__init__.py"
Cohesion: 0.40
Nodes (5): Discovery Git-aware: inventário de arquivos, fingerprints e manifest., Categoria tipada de warning do scan., ScanMode, WarningKind, StrEnum

### Community 14 - "ScanResult"
Cohesion: 0.40
Nodes (4): Fingerprint do manifest canônico., Resultado completo de um scan de diretório., Serialização canônica e determinística do inventário. Não contém timestamps,…, ScanResult

### Community 15 - "lf-0002.implement.claude.md"
Cohesion: 0.40
Nodes (4): Acceptance Criteria, Constraints, Context, Review Notes

### Community 16 - "lf-0002.review.claude.md"
Cohesion: 0.40
Nodes (4): Acceptance Criteria, Constraints, Context, Review Notes

## Knowledge Gaps
- **30 isolated node(s):** `Context`, `Acceptance Criteria`, `Constraints`, `Review Notes`, `Context` (+25 more)
  These have ≤1 connection - possible missing edges or undocumented components.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `scan()` connect `scan` to `models.py`, `scanner.py`, `_filter_dirs`, `ScanOptions`, `__init__.py`, `ScanResult`?**
  _High betweenness centrality (0.192) - this node is a cross-community bridge._
- **Why does `GitIgnoreStack` connect `GitIgnoreStack` to `scanner.py`, `GitIgnoreScope`, `_filter_dirs`?**
  _High betweenness centrality (0.059) - this node is a cross-community bridge._
- **Why does `ScanResult` connect `ScanResult` to `scanner.py`, `scan`, `models.py`, `__init__.py`?**
  _High betweenness centrality (0.046) - this node is a cross-community bridge._
- **Are the 29 inferred relationships involving `scan()` (e.g. with `ScanMode` and `WarningKind`) actually correct?**
  _`scan()` has 29 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `ScanOptions` (e.g. with `_Budget` and `_classify_and_hash()`) actually correct?**
  _`ScanOptions` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Are the 2 inferred relationships involving `_classify_and_hash()` (e.g. with `ScanOptions` and `WarningKind`) actually correct?**
  _`_classify_and_hash()` has 2 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Context`, `Acceptance Criteria`, `Constraints` to the rest of the system?**
  _30 weakly-connected nodes found - possible documentation gaps or missing edges._