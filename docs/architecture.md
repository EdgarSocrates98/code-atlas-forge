# Arquitetura — estado consolidado (as-built, `1.0.0rc1`)

> Este arquivo espelha o núcleo real hoje. Arquiteturas por ciclo:
> `docs/cycle-2-architecture.md` (núcleo C2),
> `docs/cycle-3-architecture.md` + `cycle-3.1-architecture.md`
> (incremental/resolvers), `docs/cycle-4-architecture.md`
> (search/context/MCP), `docs/cycle-5.1-final-architecture.md`
> (impact), `docs/cycle-6.1-architecture.md` (API/Explorer),
> `docs/cycle-7-architecture.md` (domínio/federação). Hardening do
> Cycle 8: `docs/cycle-8-*.md` + `docs/cycle-8-scorecard.md`.

Blueprint §3 define contextos delimitados em monorepo Python hexagonal.
Contrato base: local-first, zero telemetria, repositório indexado não
confiável, evidência primeiro, determinismo, isolamento de snapshot
entre repositórios.

## Implementado

```
src/code_atlas/
├── cli.py            argparse; única superfície de usuário
├── config.py         resolução XDG/APPDATA + CODE_ATLAS_*
├── env_override.py   env patched p/ subprocessos controlados
├── gitbin.py         gateway git (1 dos 2 subprocess autorizados)
├── contracts/        enums + dataclasses + ports Protocol (Blueprint §5)
├── discovery/        scanner Git-aware: models, gitignore fallback,
│                     walk/git, SECRET_PATTERNS, path confinado
├── parsers/          engine + registry + adaptadores tree-sitter
│                     (python.py, typescript.py, tsx.py) — ADR-015
├── graph/            GraphBuilder canônico, identity (sha256), validation
├── storage/          sqlite factory, migrações v1..v12 (fts, índices,
│                     domínio v9, federação v10, idx v11, domain_fts v12)
├── indexing/         IndexCoordinator + persist_graph + progress events
├── resolvers/        syntactic + opt-in SCIP/LSP (Cycle 3.1)
├── search/           StructuralSearch, lexical FTS5, graph, vector,
│                     hybrid RRF k=60, domain facet, federated
├── impact/           ImpactEngine: diff de snapshots, revision binding,
│                     blast misto código+domínio
├── context/          ContextEngine packs sob budget + DomainContextEngine
├── domains/          6 adapters opt-in (sql, pyspark, aws, api,
│                     terraform, cicd) + CrossDomainResolver — ADR-0010
├── federation/       workspaces multi-repo read-only, pins, view_id v2
├── eval/             harnesses de avaliação (goldens, ground truth,
│                     impact truth, pack value)
├── api/              HTTP /api/v1 loopback read-only + estático do Explorer
├── mcp.py            servidor MCP stdio JSON-RPC ndjson (15 tools)
└── observability/    logging estruturado, RedactionFilter, slow-guard
```

## Regra de dependência (hexagonal)

- `contracts`, `discovery.models`, `parsers.contracts` são puro domínio:
  não importam cli, storage concreto, MCP, web, UI, nem outras Forjas.
- `discovery` (perímetro de segurança) → `contracts`/`gitbin`.
- `parsers` → `contracts` + `parsers.contracts` (Tree-sitter só em
  `tree_sitter_runtime.py`).
- `graph` → `parsers.contracts` + `contracts` + `discovery.models`.
- `storage`/`indexing`/`search` → `contracts` + ports concretos.
- `domains` → `contracts`/`storage`; stdlib-only, ativação só via
  `DomainRegistry` (`index --domains …` é opt-in; default off preserva
  o índice byte a byte).
- `federation` → `storage`/`search`; read-only sobre snapshots
  READY/STALE — arestas federadas computadas, nunca persistidas.
- `impact`/`context` → `search`/`storage`/`domains`.
- `api`/`mcp`/`cli` → composição na borda; `api` roda em conexão
  `query_only` + bind loopback enforced.
- `eval` → CLI/pipeline real in-process; proibido `import os` direto
  (usa `env_override.patched_env` — sweep CI).
- `gitbin` e `resolvers/lsp/spawn.py` são os **únicos** pontos com
  `subprocess` — ambos autorizados (sweep em
  `tests/security/test_cycle8_adversarial.py`).

## Fluxo de indexação

```
cli.index → IndexCoordinator.index(root)
  ├─ scan(root) [git ls-files | os.walk+gitignore fallback; secrets skip]
  ├─ digest = manifest_sha256
  ├─ no-op fast-path: digest+analyzer_versions iguais → job noop
  ├─ ParseEngine: arquivo→adaptador→ParseResult (crash isolado/arquivo)
  ├─ domains opt-in: registry.extract por artefato → validate_extraction
  ├─ GraphBuilder.build → validate_graph (in-memory)
  ├─ job running (tx própria) → BEGIN IMMEDIATE
  ├─ BUILDING → source_files → file_inventory → persist_graph
  │    (symbols upsert + occurrences + relations + evidence)
  │    + persist_domain_result + CrossDomainResolver (opt-in, mesma tx)
  ├─ validate: foreign_key_check + cross-snapshot relations
  ├─ publish: ANALYZE domínio + READY←novo, STALE←antigos (mesma tx)
  │    → job success → commit
  └─ falha → rollback → snapshot FAILED + job failed (tx separada)
```

## Consulta

`cli.{search,symbol,references,graph,definitions,dependencies,context,
impact,domains,federation}` → engines sobre o snapshot READY — SQL
parametrizado, filtros, paginação, envelope `schema_version` estável.
Superfícies adicionais: API `/api/v1` (GET read-only), MCP stdio,
Explorer (`code_atlas explorer` serve API + `dist/` do Angular).
Semântica de status: `resolved`/`ambiguous`/`unresolved`/`external` —
nunca promovido silenciosamente. Ver `docs/structural-search.md`,
`docs/cycle-7-architecture.md`, `docs/cycle-8-*`.
