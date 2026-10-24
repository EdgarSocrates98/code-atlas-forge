# Cycle 2 — Evidence & Final Report

Data: 2026-10-24. Todos os comandos abaixo foram executados verbatim
nesta máquina; nenhuma saída é ilustrativa.

## Fluxo real demonstrado (verbatim)

Fixture: `demo/` com `main.py`, `pkg/__init__.py`, `pkg/core.py`
(`Engine(Base)` chamando `helper()` importado via `from pkg.util import
helper`) e `pkg/util.py` (`Base`, `helper`).

```bash
$ code-atlas init --root demo
config: /tmp/atlas-demo/cfg/config.toml
root: /tmp/atlas-demo/demo
data_dir: /tmp/atlas-demo/data
database: /tmp/atlas-demo/data/atlas.db
repository_id: 1

$ code-atlas index demo --json
{"tool": "code-atlas", "version": "0.1.0", "root": "/tmp/atlas-demo/demo", "snapshot_id": 1, "job_id": 1, "status": "success", "files_indexed": 4, "secrets_skipped": 0, "warnings": [], "noop": false, "error": "", "stats": {"files": 4, "files_parsed": 4, "files_failed": 0, "files_with_errors": 0, "secrets_skipped": 0, "evidence": 11, "symbols_new": 13, "occurrences": 13, "relations": 16, "edges_skipped": 5}}

$ code-atlas status --root demo --json
{"tool": "code-atlas", "version": "0.1.0", ..., "schema_version": 3, "repository_id": 1, "counts": {"source_files": 4, "snapshots": 1, "index_jobs": 1}, "snapshot": {"id": 1, "status": "READY", "worktree_digest": "9d1f6c930455bd06fd4bbbaf43a414b9e9084edbb5394e14167c43725b1a4689", "git_ref": "", "commit_sha": null, "parent_snapshot_id": null, "created_at": "2026-10-24T12:14:57.539Z", "files": 4}}

$ code-atlas search "main" --root demo --json
{"tool": "code-atlas", "version": "0.1.0", "schema_version": "1", "command": "search", "query": "main", "snapshot_id": 1, "filters": {"language": null, "kind": null, "path": null}, "limit": 50, "offset": 0, "total": 3, "truncated": false, "results": [{"symbol_id": 5, "qualified_name": "main", "language": "python", "kind": "module"}, {"symbol_id": 11, "qualified_name": "main.main", "language": "python", "kind": "function"}, {"symbol_id": 1, "qualified_name": "main.py", "language": "", "kind": "file"}]}

$ code-atlas symbol pkg.util.helper --root demo --json
{"tool": "code-atlas", "version": "0.1.0", "schema_version": "1", "command": "symbol", "snapshot_id": 1, "result": {"symbol_id": 12, "qualified_name": "pkg.util.helper", "language": "python", "kind": "function", "occurrences": [{"occurrence_id": 12, "path": "pkg/util.py", "line": 5, "col": 0, "parse_status": "parsed"}]}}

$ code-atlas references pkg.util.helper --root demo --json
{"tool": "code-atlas", "version": "0.1.0", "schema_version": "1", "command": "references", "symbol": "pkg.util.helper", "snapshot_id": 1, "limit": 50, "offset": 0, "total": 2, "truncated": false, "results": [{"relation_id": 3, "type": "CALLS", "from": {"symbol": "pkg.core.Engine.run", "path": "pkg/core.py", "line": 5}, "to": {"symbol": "pkg.util.helper", "path": "pkg/util.py", "line": 5}, "to_unresolved": "helper", "resolution_status": "resolved", "confidence_class": "resolved", "resolver": "tree-sitter-python", "evidence_ids": [11]}, {"relation_id": 11, "type": "DEFINES", "from": {"symbol": "pkg.util", "path": "pkg/util.py", "line": 1}, "to": {"symbol": "pkg.util.helper", "path": "pkg/util.py", "line": 5}, "to_unresolved": null, "resolution_status": "resolved", "confidence_class": "syntactic", "resolver": "tree-sitter-python", "evidence_ids": [5]}]}

$ code-atlas graph --root demo --json
{"tool": "code-atlas", ..., "counts": {"nodes": 13, "edges": 16},
 "nodes": [13 nós file/module/class/function/method],
 "edges": [16 arestas CALLS/CONTAINS/DEFINES/EXTENDS/IMPORTS]}
```

(graph truncado acima por legibilidade; output completo reproduzível
rodando o fixture — nós/arestas batem com `docs/graph-schema.md`.)

## Homologation — Phase 10 checklist

| # | Item | Resultado | Evidência |
|---|---|---|---|
| 1 | Suíte local completa | **PASS** | `pytest -q` → 182 passed in 4.18s |
| 2 | Ruff + mypy strict | **PASS** | `ruff check src tests` all green; `mypy src` → 38 files, 0 issues |
| 3 | Instalação limpa por wheel | **PASS** | `pip wheel .` → `/tmp/atlas-wheel/code_atlas_forge-0.1.0-*.whl`; venv limpa instala e `code-atlas --version`/`doctor`/`index`/`search` funcionam |
| 4 | CLI E2E | **PASS** | fluxo verbatim acima: init→index→status→search→symbol→references→graph |
| 5 | Banco e migrations | **PASS** | v1+v2+v3 sequenciais, checksum anti-drift, transacionais (BEGIN explícito); `test_migrations` green |
| 6 | Segurança / ausência de egress | **PASS** | `grep` em `src/`: zero `urllib`/`requests`/`socket`/`httpx`; auditoria AST `test_no_network`/`test_no_dynamic_sql` green; subprocess só em `gitbin.py` |
| 7 | Determinismo de grafos | **PASS** | `test_deterministic_ids_across_fresh_dbs` + `test_determinism_two_independent_runs` (golden) — serialização idêntica entre DBs frescos |
| 8 | CI Linux/Windows | **LIMITAÇÃO DECLARADA** | workflow `.github/workflows/ci.yml` matrix ubuntu+windows × py3.12/3.13 existe e dispara no push; execução Windows **não observável localmente** — verificável em GitHub Actions após push da branch |
| 9 | Ausência de deps das Forjas | **PASS** | zero imports `forge*` em `src/`; deps runtime = apenas tree-sitter pinned |
| 10 | Compatibilidade Blueprint | **PASS** | vocabulário enums = Blueprint §5 verbatim; schema §6 + v3 índices; ADR-015 emenda zero-dep conscientemente |
| 11 | Testes Cycle-1 preservados | **PASS** | 115 testes Cycle 1 continuam verdes dentro dos 182 (nenhum removido; dois testes de `--help` atualizados para aceitar os 4 comandos agora reais — mudança documentada) |
| 12 | Revisão adversarial dos parsers | **PASS** | bug real encontrado e corrigido: `c is not mod` em `_handle_import` falhava entre wrappers Node → `module_name` era emitido como import (`pkg.util.pkg.util` fantasma); fix `c.id != mod.id`, coberto pelo corpus golden. Auditors restantes: nenhum outro `is`/`is not` em comparação de nós |
| 13 | Relatório final PASS/FAIL/NOT RUN | **PASS** | esta tabela + seção abaixo |
| 14 | PR com evidências + merge pós-gates | **EM CURSO** | branch `cycle-2`, PR aberto nesta data |

## Relatório final (Phase 10)

- **SHA inicial** (merge-base main): `f54392a`
- **SHA final** (HEAD cycle-2): `414ef16` (antes do merge)
- **Commits do ciclo**: 11 (lf-0007→lf-0013 + archives + docs desta spec)
- **Arquitetura**: ver `docs/cycle-2-architecture.md`
- **Arquivos criados**: `parsers/` (5 módulos), `graph/` (4),
  `search/` (2), `indexing/persist.py`, `tests/{parsers,graph,search,
  golden,bench}/`, `benchmarks/bench_index.py`, `docs/{cycle-2-*,
  parser-adapters,graph-schema,structural-search}.md`, ADR-015.
- **Contratos adicionados**: `parsers/contracts.py` (ParseResult,
  ExtractedSymbol com params/docstring/decorators/modifiers,
  ExtractedReference com target import-member, ExtractedRelation,
  GraphNode/GraphEdge/GraphBuildResult, ParseDiagnostic, SourceSpan,
  EvidenceRecord, ParseLimits).
- **Migrations**: v3 (idx_relations_snapshot,
  idx_occurrences_snapshot_symbol, idx_evidence_snapshot). v1/v2 intactas.
- **Testes**: 182 total — 115 Cycle-1 + 67 Cycle-2 (parsers 12,
  graph 9, persist 7, search 7, golden 8, bench 3, + ajustes). Falhas: 0.
- **Cobertura por subsistema**: parsers (unit+fixtures), graph builder
  (ground truth), persistência (FK/rollback/determinismo), search/CLI
  (E2E), golden (4 corpora), bench (smoke).
- **Métricas parse/grafo**: demo 4 arquivos → 13 símbolos, 16 arestas,
  11 evidências; corpus golden grande → 242 símbolos/261 relações
  num único arquivo de 682 linhas.
- **Benchmarks**: `docs/cycle-2-benchmarks.md` — 5000 arquivos em
  26.5s total, p95 de query 13ms, DB 33.7MB.
- **Gates**: tabela acima.
- **Débitos**: **P0** nenhum. **P1** nenhum aberto (todos os P1 do
  Cycle 1 sanados em lf-0007: symlink escape, migration não-atômica,
  CI Windows glob). **P2**: persist domina o tempo de indexação
  (~77% em 5k arquivos) — batch/índices diferidos; star-imports não
  emitem IMPORTS; resolução semântica adiada a Cycle 3 por design.
- **Limitações conhecidas**: escopo sintático apenas; `ambiguous`
  reservado mas raramente emitido; TS `import type` / barrel files
  não especializados; CI Windows não observável localmente.
- **Cycle-3 readiness**: grafo persistido com status/confidence
  separados e `to_unresolved` textual preservado — resolução semântica
  pode iterar `unresolved`/`ambiguous` sem re-parse; analyzer_versions
  já distinguem gramática para invalidação seletiva.

## Ciclo completo (resumo executado)

- **Tree-sitter integrado de verdade**: runtime 0.25.2, gramáticas
  py 0.23.6 / ts 0.23.2 pinned — **sim**.
- **Python + TypeScript + TSX analisados**: **sim** (matriz em
  `parser-adapters.md`).
- **Símbolos reais extraídos**: **sim** — módulos, classes, interfaces,
  funções, métodos, aninhados, com params/assinatura/docstring.
- **Grafos reais produzidos**: **sim** — CONTAINS/DEFINES/IMPORTS/
  CALLS/EXTENDS/IMPLEMENTS/REFERENCES.
- **Relações com proveniência**: **sim** — resolver + evidence_ids →
  evidence(path, span, hash, extractor+versão).
- **Unresolved/ambiguous preservados**: **sim** — `to_unresolved`
  textual, nunca alvo inventado (`missing_fn`, `Engine().run` no demo).
- **Snapshots consistentes**: **sim** — publish atômico, FK check,
  falha preserva READY anterior.
- **Busca estrutural funcionando**: **sim** — verbatim acima.
- **CLI JSON**: **sim** — schema_version estável.
- **Testes e benchmarks documentados**: **sim**.
- **P1 do Cycle 1 resolvidos**: **sim** (lf-0007).
- **CI validado ou limitação declarada**: limitação declarada (Windows).
