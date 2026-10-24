# Cache e Invalidação (lf-0019 + lf-0020)

## Cache versionado de ParseResult

`parse_cache` (schema v4): payload é `ParseResult` serializado em
**JSON canônico** (nunca pickle — artefato inspecionável). Chave de
validade `CacheKey`: `path + content_sha256 + language +
parser_version + grammar_version + extractor_version + config_digest`.

`config_digest` cobre adapter_ids, versões e limites efetivos do
engine — qualquer mudança de configuração real invalida.

Política:
- `hit` pula leitura e tree-sitter; `miss` cai para parse real —
  fallback correto, nunca inventa dados.
- `hit_count`/`last_used_snapshot_id` por entrada; GC por
  `KEEP_SNAPSHOTS=5` e cap `MAX_ENTRIES=50_000`.
- `put`/`mark_used`/`gc` dentro da transação do snapshot.

## Invalidação (planner)

`InvalidationPlanner` produz `IndexPlan` inspecionável:

| ação | quando |
|---|---|
| `reparse` | arquivo added/modified/renamed-target |
| `drop` | deletado/renomeado-fora |
| `revalidate-relations` | importador de módulo alterado, ou bump de `resolver:`/`parser:` de outra linguagem |
| `reuse` | inalterado e não-afetado → servido do cache |

Bumps conservadores: `parser:<lang>` re-parseia só aquela linguagem;
`scanner`/`scan_mode` marca tudo `modified`; `resolver:` revalida
relações de todos.

## Isolamento lógico

Arquivos reusados voltam ao `build` como `ParseResult` real — o
builder re-resolve **todas** as relações sobre o universo novo, e o
persist grava linhas novas no snapshot novo. Relações nunca são
copiadas prontas entre snapshots.
