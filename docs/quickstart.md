# Quickstart — Cycle 2

> **Atualizado no Cycle 8 (lf-0144):** o fixture `demo/` citado nas
> saídas originais não existe mais no repositório — os exemplos foram
> re-executados verbatim contra `tests/golden/corpus/py_core` (mesma
> estrutura: `main.py` + `pkg/{__init__,core,util}.py`) e as seções
> stale de status/comandos corrigidas. Evidência do Cycle 2 em
> `docs/cycle-2-evidence.md`.

Fluxo mínimo: init → doctor → index → status → search. Todos os comandos
foram executados verbatim (evidência completa em `docs/cycle-2-evidence.md`).

## 1. Instalar

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install .
```

## 2. Verificar ambiente

```bash
$ code-atlas doctor
python    ok    3.12.3
sqlite    ok    3.45.1
fts5      ok    available
git       ok    git version 2.43.0
config    ok    /home/edgar/.config/code-atlas/config.toml  missing (run `code-atlas init`)
data_dir  ok    /home/edgar/.local/share/code-atlas  writable via /home/edgar/.local/share
```

## 3. Inicializar workspace

```bash
$ code-atlas init --root /caminho/do/repo
config:  /home/edgar/.config/code-atlas/config.toml
root:    /caminho/do/repo
data:    /home/edgar/.local/share/code-atlas
```

## 4. Indexar

```bash
$ code-atlas index /caminho/do/repo --json
{"tool": "code-atlas", "version": "1.0.0rc1", "root": "...",
 "snapshot_id": 1, "status": "success", "files_indexed": 116,
 "secrets_skipped": 3, "warnings": [], "noop": false}
```

Re-indexar sem mudanças é **no-op determinístico**:
`{"status": "noop", "files_indexed": 0, ...}`.

## 5. Estado

```bash
$ code-atlas status --root tests/golden/corpus/py_core
root:       .../tests/golden/corpus/py_core
schema:     v12
git:        cycle-8-production-quality@d0460554af3b (dirty)
snapshot:   #1 READY — 4 files
```

## 6. Consultar o grafo (Cycle 2)

Após `index`, o snapshot READY é consultável — saída verbatim do fixture
`tests/golden/corpus/py_core` (4 arquivos → 16 símbolos, 22 arestas):

```bash
$ code-atlas search "main" --root tests/golden/corpus/py_core --json
{"tool": "code-atlas", "version": "1.0.0rc1", "schema_version": "2", "command": "search", "query": "main", "snapshot_id": 1, "filters": {"language": null, "kind": null, "path": null}, "limit": 50, "offset": 0, "total": 3, "truncated": false, "results": [{"symbol_id": 5, "qualified_name": "main", "language": "python", "kind": "module"}, {"symbol_id": 6, "qualified_name": "main.main", "language": "python", "kind": "function"}, {"symbol_id": 1, "qualified_name": "main.py", "language": "", "kind": "file"}]}

$ code-atlas symbol pkg.util.helper --root tests/golden/corpus/py_core --json
{"tool": "code-atlas", "version": "1.0.0rc1", ..., "result": {"symbol_id": 25, "qualified_name": "pkg.util.helper", "language": "python", "kind": "function", "occurrences": [{"occurrence_id": 23, "path": "pkg/util.py", "line": 5, "col": 0, "parse_status": "parsed"}]}}

$ code-atlas references pkg.util.helper --root tests/golden/corpus/py_core --json
{"tool": "code-atlas", ..., "results": [{"type": "CALLS", "from": {"symbol": "pkg.core.outer.inner", ...}, "to": {"symbol": "pkg.util.helper", ...}, "resolution_status": "resolved", "confidence_class": "resolved"}, {"type": "CALLS", "from": {"symbol": "pkg.core.Engine.run", ...}, "to": {"symbol": "pkg.util.helper", ...}, "resolution_status": "resolved"}]}

$ code-atlas graph --root tests/golden/corpus/py_core --json
{"tool": "code-atlas", ..., "counts": {"nodes": 16, "edges": 22}, "nodes": [...], "edges": [...]}
```

Filtros disponíveis: `--language`, `--kind`, `--path`, `--snapshot`,
`--relation-type`, `--limit`/`--offset`. Detalhes e semântica
(sintático vs resolvido): `docs/structural-search.md`.

## 7. Consultas semânticas (Cycle 3, envelope v2)

Todo comando de consulta devolve envelope `{tool, version,
schema_version: "2", command, snapshot_id, repository{id, root_handle,
identity_key}, query, results, total, truncated, diagnostics}` —
campos aditivos, nunca quebra o v1.

```bash
$ code-atlas definitions pkg.util.helper --root tests/golden/corpus/py_core --json
{..., "results": [{"path": "pkg/util.py", "line": 5, "col": 0,
"parse_status": "parsed"}]}

$ code-atlas dependencies main.main --depth 2 --root tests/golden/corpus/py_core --json
{..., "depth": 2, "visited_symbols": 3, "results": [{"depth": 1,
"type": "CALLS", "to": null, "resolution_status": "unresolved"},
{"depth": 1, "type": "CALLS", "to": {"symbol": "pkg.core.Engine"},
"resolution_status": "resolved"}, {"depth": 2, "type": "EXTENDS",
"to": {"symbol": "pkg.util.Base"}, "resolution_status": "resolved"}]}

$ code-atlas index-plan tests/golden/corpus/py_core --json   # dry-run, zero writes
{..., "plan": {"incremental": true, "counts": {"reuse": 4},
"files": [{"path": "pkg/util.py", "action": "reuse", ...}]}}
```

- `references` é **incoming-only** (o símbolo é o alvo; default
  REFERENCES+CALLS, `--relation-type` filtra outro tipo).
- `dependencies`: traversal BFS limitado — `--depth N` (default 1,
  teto `MAX_DEPTH=8`), ciclo-safe, `truncated` honesto.
- `index`: incremental é o **default** — `--full` força rebuild
  completo; o payload inclui `plan` (counts de reparse/reuse).
- `index-plan`: dry-run que renderiza o `IndexPlan` sem tocar no DB.
- `ambiguous` nunca aparece como `resolved` — status sai verbatim.

## 8. Resolvers externos + status semântico (Cycle 3.1)

```bash
$ code-atlas index . --scip ./index.scip --json   # binding ad-hoc
$ code-atlas index . --resolver syntactic --json  # só o baseline
$ code-atlas index . --resolver scip --json       # syntactic + scip
$ code-atlas index . --resolver lsp:pyright --json
$ code-atlas semantic-status tests/golden/corpus/py_core --json
{..., "resolvers": [{"id": "syntactic-scope-v1", "installed": true,
"enabled": true, "used": true, "edges": 22,
"by_status": {"resolved": 20, "unresolved": 2}}],
"coverage": {"resolved": 20, "ambiguous": 0, "unresolved": 2,
"external": 0, "total": 22},
"semantic": {"semantic_attempted": 0, ...},
"revision": {"commit_sha": null, "scip_index_sha": null},
"diagnostics": []}
```

- `--scip PATH` sobrescreve `[resolvers.scip].index_path` só naquela
  invocação (proveniência/revisão do lf-0028 aplicada igual).
- `--resolver` restringe os resolvers ativos — repetível ou por
  vírgula; nome desconhecido é **erro tipado**, nunca flag silenciosa.
- `semantic-status` lê **só o snapshot READY persistido**: `installed`
  (índice carrega / binário no PATH), `enabled` (produziria resolver),
  `used` (aparece em `relations.resolver` — única fonte real de uso).
- Cobertura por status vem de `GROUP BY resolver, resolution_status` —
  sem estimativa.

## Além do Cycle 3

Os comandos `context`, `impact`, `mcp`, `api`, `explorer`, `domains` e
`federation` **existem hoje** — entregues nos Cycles 4–7 (ver
`docs/architecture.md` e `docs/roadmap.md`). `index` aceita
`--domains <lista>|all` + `--cross-domain` (opt-in) e `--verbose`
(progresso em stderr, stdout JSON intacto — Cycle 8).

Resolução syntactic não é type-checking (sem tipos inferidos,
overloads reais, barrel reexports) — `ambiguous`/`unresolved`/`external`
são fatos honestos. Resolvers SCIP/LSP sobem a cobertura, opt-in — ver
`docs/semantic-resolution.md`.

## Troubleshooting

`init` falha com "config already exists" → use `--force` ou aponte
`CODE_ATLAS_CONFIG_DIR` para outro lugar. `search` exit 1 → repo não
indexado ou snapshot FAILED. Mais em `docs/troubleshooting.md`.
