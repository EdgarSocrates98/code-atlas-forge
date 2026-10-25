# Cycle 8 Phase 3 — Matriz de compatibilidade de dialetos de parser

Spec: `factory/specs/inbox/lf-0130-cycle8-parser-dialect.md`
Branch: `cycle-8-production-quality`

Audit + fixes de constructs de dialeto TypeScript/Python medidos contra
código real. Motivação: axios (`benchmarks/.corpus/realworld/axios`,
`aa54a5e1e0e50cd1a2561b854ae5490ff589d065`) falhava o snapshot inteiro
porque `import x = require('y')` emitia `IMPORTS` com `to_text` vazio —
`validate_graph` (corretamente, fail-closed) rejeitava e 503 arquivos
indexados morriam juntos.

## Invariantes novas (todas as linguagens)

1. **Nenhuma aresta `IMPORTS` sai do extractor com alvo vazio.** Se o
   caminho de módulo não é resolvível no ponto de extração, a aresta é
   descartada e um diagnóstico `import-source-missing` (warning) é
   emitido — nunca um `to_name`/`to_text` vazio.
2. **Guarda no `GraphBuilder`.** Mesmo que um adapter defeituoso emita
   `ExtractedRelation` sem alvo (`to_id=None` e `to_text` vazio), o
   builder descarta a aresta e emite diagnóstico `empty-edge-target` —
   o snapshot segue válido. A validação fail-closed em
   `validate_graph` permanece como última linha de defesa.
3. **Isolamento por arquivo no coordinator.** `IndexCoordinator`
   envolve `engine.parse` por arquivo: um crash interno do engine vira
   `engine-crash` / `parse-failed` para aquele arquivo; arquivos
   seguintes processam normalmente. `ParseEngine` já isolava crash de
   adapter (`adapter-crash`) e `parse_batch` já era fail-open — a
   camada do coordinator fecha o buraco restante (falha do próprio
   engine/registry).

## TypeScript (`typescript.py`, também herdado por `tsx.py`)

| Construct | Status | Detalhe |
|---|---|---|
| `import {a} from 'm'` / `import a` / `import * as ns` | extracted | já existia |
| `import type {T} from 'm'` | extracted | `source` no statement como import comum; **sem flag type-only** — `IMPORTS` idêntico a import de valor (decisão documentada: dependência existe para o grafo) |
| `import x = require('m')` | **fixed → extracted** | `source` mora em `import_require_clause`, não no statement (causa-raiz do P1 axios); binding `x → m` (módulo inteiro) |
| `import foo = Bar.Baz` (`import_alias`) | **fixed → extracted** | alias de **entidade**, não de módulo: binding `foo → Bar.Baz` + ref `import-alias`, **sem** aresta `IMPORTS` (correto — não há source de módulo) |
| `export {a}`, `export {a as b}` | extracted | refs `export` nos especificadores locais |
| `export {a} from 'm'` / `export type {T} from 'm'` | **fixed → extracted** | `IMPORTS → 'm'` + bindings `a → m.a`, `b → m.a` |
| `export * from 'm'` | **fixed → extracted** | `IMPORTS → 'm'` + binding `* → m.*` (`*` é filho anônimo na gramática) |
| `export * as ns from 'm'` | **fixed → extracted** | `IMPORTS → 'm'` + binding `ns → m.*` |
| `export = <expr>` (CJS/UMD) | **fixed → extracted (como referência)** | ref `export-assignment` com o nome da expressão; **sem** `IMPORTS` (não é dependência). Semântica de "módulo inteiro é o export" fica para o resolver |
| `export default function g(){}` / `class C{}` | **fixed → extracted** | símbolo mantém o **nome real** `g`/`C` + modifier `export` (antes caía para `default`) |
| `export default function(){}` / `class {}` | extracted | símbolo anônimo vira `default` + modifier `export` |
| `export declare class/function/const` | **fixed → extracted** | `ambient_declaration` é unwrapada preservando o flag `export` |
| `import('./m')` literal | **fixed → extracted** | `IMPORTS → './m'` — dependência concreta |
| `import(expr)` não-literal | unsupported-with-diagnostic | diagnóstico `dynamic-import-nonliteral` (info), **nenhuma** aresta inventada |
| `require('m')` (CJS) | **fixed → extracted** | `IMPORTS → 'm'` + binding do declarador (`const a = require('m')` → `a → m`; `require('m').default` → `a → m.default`) |
| `require(expr)` não-literal | degraded | sem prova estática → cai para `CALLS` normal (honesto, sem aresta inventada) |
| `namespace X {}` / `module X {}` | **fixed → extracted** | filhos com escopo `X`: `mod.X.member`; `export` dentro propaga |
| `declare namespace X {}` / `declare module 'x' {}` | **fixed → extracted** | `declare module 'ext' { export function g }` → `mod.ext.g` (signature-only) |
| `declare global {}` | extracted | body é statement_block — declarações internas extraídas no escopo do módulo |
| `declare const v: T` / `let x: T` sem valor | **fixed → extracted** | declarador sem inicializador vira símbolo `symbol` (antes era descartado) |
| `@Dec` / `@Dec(args)` em classe e método | **fixed → extracted** | nome do decorator em `symbol.decorators` + refs `decorator` com escopo correto; `pending` decorators nunca vazam para a declaração seguinte errada |
| generics `function f<T>(x: T): T` | degraded (documentado) | assinatura preservada em texto; parâmetros de tipo `T` não viram símbolos nem refs `type-ref` próprios |
| `.cts` / `.mts` / `.d.cts` / `.d.mts` | **fixed → extracted** | `module_name` resolve `index.d.cts` → `index`, `a/x.cts` → `a.x` (antes `.d.cts` vazava para o nome) |
| `abstract class`, `interface`, `enum`, `type alias` | extracted | já existia; methods `signature-only` em `.d.ts`/interfaces |
| `satisfies`/assertion/`as` expressions | degraded | expressão interna é visitada; o operador em si não gera símbolo (consistente com `as` casts) |
| JSX em `.tsx` | extracted | `tsx.py` herda todos os fixes — refs `jsx` de elementos |

## Python (`python.py` + resolver)

| Construct | Status | Detalhe |
|---|---|---|
| `__init__.py`: `from .api import x` | **fixed → resolved** | resolver tratava `.` como relativo ao arquivo `pkg/__init__` (módulo `pkg`) em vez do pacote — `from .api import x` ficava unresolved; agora `pkg → pkg.api` resolve (`test_init_py_relative_import_resolves`) |
| `__init__.py`: `from . import util` | **fixed → resolved** | `pkg → pkg.util` resolve |
| `from . import x` em módulo comum | extracted+resolved | já existia |
| `from m import *` | extracted | `IMPORTS → 'm'` + binding `* → m`; re-export downstream depende do resolver (estrela não lista membros — degraded por natureza, documentado) |
| `if TYPE_CHECKING:` imports | extracted | walk desce no `if_statement` — `IMPORTS` normal; **sem flag type-only** (indistinguível de import runtime — documentado) |
| `from ..x import y` (relativo multinível) | extracted+resolved | `to_name` textual `..x.y`; resolver normaliza os pontos |
| walrus `:=`, comprehensions | n/a-consistente | walrus/compr targets não viram símbolos — igual a assignment comum: o extractor não emite símbolos de variáveis locais em nenhum lugar (não é gap do walrus em si) |
| re-export em `__init__.py` (`from .x import *` público) | extracted | depende do binding `*` no resolver — símbolos reexportados não são duplicados como símbolos locais (correto) |

## Diagnósticos novos (códigos)

| Código | Severidade | Onde | Significado |
|---|---|---|---|
| `import-source-missing` | warning | extractor TS/TSX | caminho de módulo não extraível → aresta `IMPORTS` descartada (nunca emitida vazia) |
| `dynamic-import-nonliteral` | info | extractor TS/TSX | `import(expr)` não-literal → sem dependência estática |
| `empty-edge-target` | warning | `GraphBuilder` | adapter emitiu relação sem alvo → aresta descartada no builder |
| `engine-crash` (failure tag) | — | coordinator | `engine.parse` lançou exceção interna → `parse-failed` para o arquivo |

## Verificação — axios real

Comando (verbatim):

```
.venv/bin/python -m code_atlas index benchmarks/.corpus/realworld/axios --full --json
```

Antes do fix (baseline medido): `status: failed`, `files_indexed: 503`,
`error: graph integrity violated: edge without target text: IMPORTS
from module:typescript:tests/module/cjs/tests/helpers/cjs-added-types.ts; ...`.

Depois do fix (verbatim, snapshot 18):

```json
{"tool": "code-atlas", "version": "0.1.0", "schema_version": "2",
 "root": ".../benchmarks/.corpus/realworld/axios", "snapshot_id": 18,
 "job_id": 21, "status": "success", "files_indexed": 503,
 "secrets_skipped": 3, "warnings": [], "noop": false, "error": "",
 "stats": {"files": 503, "files_parsed": 32, "files_reused": 0,
  "cache_puts": 32, "files_failed": 0, "files_with_errors": 0,
  "secrets_skipped": 3, "evidence": 1665, "symbols_new": 868,
  "occurrences": 869, "relations": 1721, "edges_skipped": 588,
  "domain_adapters": 0, "domain_failures": 0},
 "diagnostics": [], "cross_domain": false}
```

- 503 arquivos inventariados; 32 com adapter (`.ts`/`.cts`/`.d.ts` —
  os demais são `.js` e assets, **sem adapter JavaScript por design**);
  0 falhas, 0 syntax-errors, 3 secrets skipped.
- Snapshot DB: `status READY`, 60 arestas `IMPORTS` — 59 unresolved com
  texto honesto (`axios`, `vitest`, … `resolution_status='external'`)
  e 1 resolvida (`docs..vitepress.config → docs..vitepress.search`).
  **Zero arestas com alvo vazio** no snapshot inteiro.
- `edges_skipped=588` é a semântica pré-existente de endpoints sem
  âncora persistida (alvos fora do snapshot) — não arestas descartadas
  pelo fix.
