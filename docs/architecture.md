# Arquitetura — Cycle 2 (as-built)

> Cycle 3 entregue: resolvers + incremental + semantic CLI em
> `docs/cycle-3-architecture.md`. Este arquivo espelha o núcleo C2.

Blueprint §3 define contextos delimitados em monorepo Python hexagonal.
Visão do Cycle 2 em `docs/cycle-2-architecture.md`; este arquivo espelha
o estado real consolidado.

## Implementado

```
src/code_atlas/
├── cli.py            argparse; única superfície de usuário
├── config.py         resolução XDG/APPDATA + CODE_ATLAS_*
├── gitbin.py         ÚNICO módulo com subprocess (argv→git, sem shell)
├── contracts/        enums + dataclasses + ports Protocol (Blueprint §5)
├── discovery/        scanner Git-aware: models, gitignore fallback, walk/git
├── parsers/          engine + registry + adaptadores tree-sitter
│                     (python.py, typescript.py, tsx.py) — ADR-015
├── graph/            GraphBuilder canônico, identity (sha256), validation
├── storage/          sqlite factory, migrações v1+v2(fts)+v3(idx), Store
├── indexing/         IndexCoordinator + persist_graph (grafo→tabelas)
├── search/           StructuralSearch — queries SQL sobre o snapshot
└── observability/    logging estruturado + RedactionFilter
```

## Regra de dependência (hexagonal)

- `contracts`, `discovery.models`, `parsers.contracts` são puro domínio:
  não importam cli, storage concreto, MCP, web, UI, nem outras Forjas.
- `discovery` (perímetro de segurança) → `contracts`/`gitbin`.
- `parsers` → `contracts` + `parsers.contracts` (Tree-sitter só em
  `tree_sitter_runtime.py`).
- `graph` → `parsers.contracts` + `contracts` + `discovery.models`.
- `storage`/`indexing`/`search` → `contracts` + ports concretos.
- `cli` → tudo (composição na borda).
- `gitbin` ← qualquer um; é o único ponto com `subprocess`.

## Fluxo de indexação (Cycle 2)

```
cli.index → IndexCoordinator.index(root)
  ├─ scan(root) [git ls-files | os.walk+gitignore fallback]
  ├─ digest = manifest_sha256
  ├─ no-op fast-path: digest+analyzer_versions iguais → job noop
  ├─ ParseEngine: arquivo→adaptador→ParseResult (falha isolada/arquivo)
  ├─ GraphBuilder.build → validate_graph (in-memory)
  ├─ job running (tx própria) → BEGIN IMMEDIATE
  ├─ BUILDING → source_files → file_inventory → persist_graph
  │    (symbols upsert + occurrences + relations + evidence)
  ├─ validate: foreign_key_check + cross-snapshot relations
  ├─ publish: READY←novo, STALE←antigos (mesma tx) → job success → commit
  └─ falha → rollback → snapshot FAILED + job failed (tx separada)
```

## Consulta (Cycle 2)

`cli.{search,symbol,references,graph}` → `StructuralSearch` sobre o
snapshot READY — SQL parametrizado, filtros, paginação, `schema_version`
estável. Ver `docs/structural-search.md`.

## Futuro (não implementado — ver roadmap)

`resolvers` semânticos (SCIP/LSP), `impact`, `context` (packs),
`api`/`mcp`, `explorer` (Angular+Sigma), `federation`.
