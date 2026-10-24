# Arquitetura — Cycle 2 (as-built)

Extensão do Cycle 1 (`architecture.md`): o pipeline agora vai além do
inventário — parseia, constrói grafo e persiste tudo dentro do ciclo de
vida de snapshots.

## Pipeline real

```
scan (discovery)                     fora do lock de escrita
  └─ ParseEngine → adapters TS        por arquivo; falha isolada
       └─ GraphBuilder                grafo canônico em memória
            └─ validate_graph         integridade in-memory
BEGIN IMMEDIATE
  └─ file_inventory + persist_graph   symbols/occurrences/relations/evidence
  └─ foreign_key_check + cross-snapshot
  └─ publish READY                    atômico
COMMIT
```

- **Semântica:** resolução por escopo sintático local (bindings de
  import + definidos por módulo + path relativo TS). Nada semântico/
  inferencial — Cycle 3.
- **No-op:** digest do worktree **e** `analyzer_versions` (scanner +
  parser/grammar por linguagem) precisam ser idênticos — mudança de
  gramática força reindex.
- **Falhas:** parse falho = `parsed-with-errors`/`parse-failed` +
  evidência `parse-failure:*`; falha transacional = snapshot FAILED,
  READY anterior intacto.

## Mapa de módulos (novo no Cycle 2)

```
src/code_atlas/
├── parsers/
│   ├── contracts.py       ParseResult/ExtractedSymbol/Reference/Relation,
│   │                      GraphNode/GraphEdge/GraphBuildResult (domínio)
│   ├── tree_sitter_runtime.py  LanguageSpec, parse_bytes, iteração de nós
│   ├── registry.py        extensão→adaptador; default() py/ts/tsx
│   ├── engine.py          ParseEngine: ParseLimits, timeout, batch
│   └── languages/         python.py, typescript.py, tsx.py
├── graph/
│   ├── identity.py        node_id = sha256(linguagem|kind|qname|path)
│   ├── builder.py         GraphBuilder: nós, arestas, resolução
│   ├── validation.py      validate_graph (integridade pré-persist)
│   └── queries.py         helpers de consulta ao build result
├── search/
│   └── structural.py      StructuralSearch sobre o Store (SQL só)
└── indexing/
    └── persist.py         persist_graph: GraphBuildResult→tabelas
```

## Dependências de runtime (ADR-015)

`tree-sitter==0.25.2`, `tree-sitter-python==0.23.6`,
`tree-sitter-typescript==0.23.2` — exatos, auditados por pip-audit no
CI, registrados por arquivo em `evidence.extractor_version`.

## O que continua fora (intencional)

- Resolução semântica/profunda, tipos inferidos, overloads reais.
- FTS5 é suportado (v2) mas a busca usa `LIKE` parametrizado.
- MCP, UI Explorer, impact-analysis, context packs — Cycle 3+.
