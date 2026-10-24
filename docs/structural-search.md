# Structural Search — consultas sobre o grafo persistido

Cycle 2 (lf-0012). `StructuralSearch` (`search/structural.py`) responde
sobre **dados persistidos** — nunca re-parseia, nunca chama LLM. Toda
query é SQL parametrizado ancorado em `snapshot_id`; nada vaza entre
snapshots ou repositórios.

## Comandos

| Comando | O que retorna |
|---|---|
| `code-atlas search QUERY` | símbolos cujo `qualified_name` contém o texto (substring, case-insensitive) |
| `code-atlas symbol IDENT` | detalhe + ocorrências do símbolo (id numérico ou qualified_name exato) |
| `code-atlas references IDENT` | relações tocando o símbolo (de/para) |
| `code-atlas graph` | dump completo de nós+arestas do snapshot |

## Resolução de contexto

1. `--root` → repositório indexado (`repositories.root_handle`).
2. `--snapshot` explícito → exige READY daquele repositório;
   omitido → snapshot READY mais recente.
3. Snapshot ausente/FAILED ou repo nunca indexado → exit `1` com erro
   claro em `stderr` (ou `{"error": ...}` no JSON).

## Filtros

`--language`, `--kind`, `--path` (substring do path relativo) filtram
símbolos/nós; `--relation-type` filtra arestas em `references`/`graph`.
`--limit`/`--offset` paginam (default 50, máx 500); `total` vem de COUNT
paralelo e `truncated` = `offset + len(results) < total` — sempre
honesto. `limit 0` e offset além do fim retornam lista vazia, não erro.

## Formato JSON

Um objeto por comando, `schema_version` estável:

```json
{"tool":"code-atlas","version":"0.1.0","schema_version":"1",
 "command":"search","query":"...","snapshot_id":1,
 "filters":{"language":null,"kind":null,"path":null},
 "limit":50,"offset":0,"total":3,"truncated":false,
 "results":[{ "symbol_id":11,"qualified_name":"main.main",
              "language":"python","kind":"function" }]}
```

`symbol` retorna `result` com `occurrences`; `references` retorna itens
com `from`/`to` (símbolo+path+linha), `to_unresolved` textual,
`resolution_status`, `confidence_class`, `resolver` e `evidence_ids`;
`graph` retorna `nodes`, `edges` e `counts` determinísticos.

## O que é *sintático* vs *resolvido*

- **Parsing sintático:** fatos que a gramática prova (símbolo, span,
  texto do call). Sempre presentes quando o parse ocorre.
- **Referência sintática:** ocorrência de nome com contexto (`call`,
  `import`, `extends`) — o texto é real, o alvo pode não existir.
- **Referência resolvida:** o graph builder encontrou **exatamente um**
  alvo interno (binding de import + tabela de definidos) —
  `resolution_status="resolved"`, `confidence_class="resolved"`.
- **Inferência:** dedução a partir de tipos/valores — **não
  implementada**; reservada ao Cycle 3+.
- **Análise semântica profunda:** LSP/SCIP-level, fora de escopo — o
  Cycle 2 é explicitamente escopo sintático determinístico.

## Performance medida

Ver `docs/cycle-2-benchmarks.md`: p50 ≈ 12ms sobre 25k símbolos (corpus
5000 arquivos). FTS5 opcional existe (migração v2) como caminho de
upgrade — as queries atuais são `LIKE` parametrizado, suficientes na
escala do ciclo.
