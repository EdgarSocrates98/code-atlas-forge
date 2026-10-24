# Quickstart — Cycle 2

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
{"tool": "code-atlas", "version": "0.1.0", "root": "...",
 "snapshot_id": 1, "status": "success", "files_indexed": 116,
 "secrets_skipped": 3, "warnings": [], "noop": false}
```

Re-indexar sem mudanças é **no-op determinístico**:
`{"status": "noop", "files_indexed": 0, ...}`.

## 5. Estado

```bash
$ code-atlas status
root:       /caminho/do/repo
schema:     v3
git:        main@8d1551b91e41 (dirty)
snapshot:   #1 READY — 116 files
```

## 6. Consultar o grafo (Cycle 2)

Após `index`, o snapshot READY é consultável — saída verbatim do fixture
`demo/` (4 arquivos → 13 símbolos, 16 arestas):

```bash
$ code-atlas search "main" --root demo --json
{"tool": "code-atlas", "version": "0.1.0", "schema_version": "1", "command": "search", "query": "main", "snapshot_id": 1, "filters": {"language": null, "kind": null, "path": null}, "limit": 50, "offset": 0, "total": 3, "truncated": false, "results": [{"symbol_id": 5, "qualified_name": "main", "language": "python", "kind": "module"}, {"symbol_id": 11, "qualified_name": "main.main", "language": "python", "kind": "function"}, {"symbol_id": 1, "qualified_name": "main.py", "language": "", "kind": "file"}]}

$ code-atlas symbol pkg.util.helper --root demo --json
{"tool": "code-atlas", "version": "0.1.0", ..., "result": {"symbol_id": 12, "qualified_name": "pkg.util.helper", "language": "python", "kind": "function", "occurrences": [{"occurrence_id": 12, "path": "pkg/util.py", "line": 5, "col": 0, "parse_status": "parsed"}]}}

$ code-atlas references pkg.util.helper --root demo --json
{"tool": "code-atlas", ..., "results": [{"type": "CALLS", "from": {"symbol": "pkg.core.Engine.run", ...}, "to": {"symbol": "pkg.util.helper", ...}, "resolution_status": "resolved", "confidence_class": "resolved"}, {"type": "DEFINES", ...}]}

$ code-atlas graph --root demo --json
{"tool": "code-atlas", ..., "counts": {"nodes": 13, "edges": 16}, "nodes": [...], "edges": [...]}
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
$ code-atlas definitions pkg.util.helper --root demo --json
{..., "results": [{"path": "pkg/util.py", "line": 5, "col": 0,
"parse_status": "parsed"}]}

$ code-atlas dependencies pkg.app.main --depth 2 --root demo --json
{..., "depth": 2, "visited_symbols": 4, "results": [{"depth": 1,
"type": "CALLS", "to": {"symbol": "pkg.mid.mid"}, ...}]}

$ code-atlas index-plan . --json   # dry-run, zero writes
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

## O que o Cycle 3 **não** faz

`impact`, `context`, `mcp`, `serve`, `ui` — não existem e não aparecem no
`--help`. Resolução syntactic não é type-checking (sem tipos inferidos,
overloads reais, barrel reexports) — `ambiguous`/`unresolved`/`external`
são fatos honestos. Resolvers SCIP/LSP sobem a cobertura, opt-in — ver
`docs/semantic-resolution.md`.

## Troubleshooting

`init` falha com "config already exists" → use `--force` ou aponte
`CODE_ATLAS_CONFIG_DIR` para outro lugar. `search` exit 1 → repo não
indexado ou snapshot FAILED. Mais em `docs/troubleshooting.md`.
