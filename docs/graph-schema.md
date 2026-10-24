# Graph Schema — grafo canônico persistido

Cycle 2 (lf-0010/lf-0011). O grafo é derivado deterministicamente de
`ScanResult` + `ParseResult`s pelo `GraphBuilder` e projetado nas
tabelas `symbols`, `symbol_occurrences`, `relations` e `evidence`.

## Nós

| Kind | Origem | Identidade |
|---|---|---|
| `repository` | raiz indexada | nome do diretório raiz |
| `package` | diretório intermediário | path relativo |
| `file` | arquivo do inventário | path relativo |
| `module` | arquivo parseado | `(linguagem, path)` |
| `class` / `interface` / `function` / `method` / `symbol` | símbolo extraído | `(linguagem, kind, qualified_name)` |

IDs são `sha256` hex truncado sobre a identidade lógica — **nunca**
linha/coluna (mudam a cada edição). A mesma identidade lógica gera o
mesmo ID em qualquer máquina/DB.

### Persistência

Nem todo nó vira linha em `symbols`: `repository` e `package` são
deriváveis dos paths e **não** persistem. Kinds persistidos recebem uma
`symbol_occurrence` por snapshot (span + `content_hash` +
`parse_status`). É por isso que `search`/`graph` retornam nós `file` e
`module` junto com símbolos de código.

## Arestas

| Tipo | Sentido | Exemplo |
|---|---|---|
| `CONTAINS` | estrutural: file→module (repo/package deriváveis) | `pkg/core.py → pkg.core` |
| `DEFINES` | símbolo definido pelo pai léxico | `pkg.core → pkg.core.Engine` |
| `IMPORTS` | módulo importa módulo (membro no texto) | `main → pkg.core` |
| `CALLS` | escopo chama alvo | `Engine.run → pkg.util.helper` |
| `EXTENDS` | herança de classe | `Engine → pkg.util.Base` |
| `IMPLEMENTS` | classe implementa interface (TS) | `Api → Svc` |
| `REFERENCES` | decorator/type-ref/jsx | `Widget → Api` |

`CONFIGURES`/`TESTS` existem no vocabulário (Blueprint §5) mas **não têm
produtor** ainda — nunca aparecem em resultados reais.

## Resolução (escopo sintático, Cycle 2)

Resolução é determinística e local: bindings de import (`import-member`
→ alvo completo), tabela de definidos por módulo, longest-prefix para
módulos Python, resolução de path relativo TS (`./x`, `../x`,
extensão/`/index`). Semântica profunda (tipos, overloads reais,
fluxo de dados) é Cycle 3 — aqui `resolved` significa "exatamente um
alvo no snapshot".

### Status de resolução

| `resolution_status` | Significado |
|---|---|
| `resolved` | exatamente um alvo interno encontrado |
| `unresolved` | texto preservado em `to_unresolved`, sem alvo inventado |
| `ambiguous` | múltiplos candidatos — recusado, texto preservado |
| `external` | stdlib/site-packages — fora do grafo interno por política |

### Confidence class

| `confidence_class` | Significado |
|---|---|
| `resolved` | alvo interno único confirmado |
| `syntactic` | fato sintático real, alvo não resolvido |
| `heuristic` / `inferred` | reservados (Cycle 3+) — nunca emitidos hoje |

## Proveniência

Toda aresta persiste `resolver` (ex.: `tree-sitter-python`,
`syntactic-scope-v1`), `evidence_ids` → `evidence` (path, span,
content_hash, extractor+versão) e opcionalmente `to_unresolved` com o
texto original — um fato nunca perde sua origem textual.

## Determinismo

- Ordenação de saída: nós por `node_id`, arestas por
  `(tipo, from, to, to_text, span.start_byte)`.
- Dedup: `(tipo, from, to, to_text, span)` — arestas idênticas são
  emitidas uma vez.
- `test_deterministic_ids_across_fresh_dbs` prova ids/linhas/spans
  idênticos entre bancos frescos; `test_golden` congela o conjunto
  completo por corpus.

## Validação antes do READY

`validate_graph` (in-memory) + `PRAGMA foreign_key_check` + checagem de
relações entre snapshots: nenhum snapshot vira READY com FK quebrada,
aresta de endpoint ausente ou ciclo de CONTAINS. Falha → snapshot FAILED,
READY anterior preservado.
