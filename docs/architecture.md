# Arquitetura — Cycle 1 (as-built)

Blueprint §3 define contextos delimitados em monorepo Python hexagonal.
Estado real hoje:

## Implementado

```
src/code_atlas/
├── cli.py            argparse; única superfície de usuário
├── config.py         resolução XDG/APPDATA + CODE_ATLAS_*
├── gitbin.py         ÚNICO módulo com subprocess (argv→git, sem shell)
├── contracts/        enums + dataclasses + ports Protocol (Blueprint §5)
├── discovery/        scanner Git-aware: models, gitignore fallback, walk/git modes
├── storage/          sqlite factory, migrações v1+v2(fts), Store (queries)
├── indexing/         IndexCoordinator: scan→BUILDING→validate→READY atômico
└── observability/    logging estruturado + RedactionFilter
```

## Regra de dependência (hexagonal)

- `contracts`, `discovery.models` são puro domínio: não importam cli, storage
  concreto, MCP, web, UI, nem outras Forjas.
- `discovery` (perímetro de segurança) → `contracts`/`gitbin`.
- `storage`/`indexing` → `contracts` + `discovery` (ports concretos).
- `cli` → tudo (composição na borda).
- `gitbin` ← qualquer um; é o único ponto com `subprocess`.

## Fluxo de indexação

```
cli.index → IndexCoordinator.index(root)
  ├─ scan(root) [git ls-files | os.walk+gitignore fallback]
  ├─ digest = manifest_sha256
  ├─ job running (tx própria) → BEGIN IMMEDIATE
  ├─ no-op? digest+analyzers iguais ao READY atual → job noop
  ├─ BUILDING → upsert source_files (dedup repo+path) → file_inventory
  ├─ validate: foreign_key_check + cross-snapshot relations
  ├─ publish: READY←novo, STALE←antigos (mesma tx) → job success → commit
  └─ falha → rollback → snapshot FAILED + job failed (tx separada)
```

## Futuro (não implementado — ver roadmap)

`parsers` (Tree-sitter), `resolvers` (SCIP/LSP), `graph` (typed graph),
`search` (FTS+vector), `impact`, `context` (packs), `api`/`mcp`, `explorer`
(Angular+Sigma), `federation`.
