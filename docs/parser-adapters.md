# Adaptadores de Parser — contratos e matriz de linguagens

Cycle 2 (lf-0008/lf-0009). Cada linguagem tem um **adaptador** que
encapsula a gramática Tree-sitter pinned e emite o contrato único
`ParseResult`. O restante do pipeline nunca toca objetos tree-sitter —
só os modelos de contrato.

## Matriz de linguagens suportadas

| Linguagem | Extensões | Adaptador | Gramática pinned | Extração |
|---|---|---|---|---|
| Python | `.py`, `.pyi` | `parsers/languages/python.py` (`tree-sitter-python`) | tree-sitter-python `0.23.6` | classes, funções, métodos, aninhados, decorators, params, docstrings, imports (absoluto/relativo/alias), calls, extends |
| TypeScript | `.ts`, `.mts`, `.cts`, `.d.ts` | `parsers/languages/typescript.py` (`tree-sitter-typescript`) | tree-sitter-typescript `0.23.2` | classes, interfaces, funções, métodos, arrow functions nomeadas, imports/exports com alias, extends/implements, type/enum decls |
| TSX | `.tsx` | `parsers/languages/tsx.py` (mesma gramática TS, dialeto tsx) | tree-sitter-typescript `0.23.2` | tudo do TS + referências JSX (`<Comp/>` → REFERENCES) |

Runtime: `tree-sitter==0.25.2` (ADR-015). Versões registradas por
arquivo em `snapshots.analyzer_versions` e por evidência em
`evidence.extractor_version` (`parser+grammar`).

## Contrato do adaptador

```python
class ParserAdapter(Protocol):
    language: str
    extensions: tuple[str, ...]
    capabilities: ParserCapability
    def parse(self, request: ParseRequest) -> ParseResult: ...
```

`ParseResult` carrega: `path`, `language`, `parser`, `parser_version`,
`grammar_version`, `content_hash`, `has_error`, contagens de nós de
erro/ausentes, `diagnostics` (com span), `symbols` (`ExtractedSymbol`),
`references` (`ExtractedReference`) e `relations` (`ExtractedRelation`).

### Semântica dos campos extraídos

- **`ExtractedSymbol`**: `kind` ∈ {module, class, interface, function,
  method, symbol}; `qualified_name` = `modulo.Escopo.nome`; `scope_path`
  preserva o caminho léxico; `params`, `signature`, `docstring`,
  `decorators`, `modifiers`, `bases`/`implements`.
- **`ExtractedReference`**: `(name, context, span, scope_path, target)`.
  Contextos: `import`, `import-member` (binding local → alvo completo,
  alimenta a resolução), `call`, `extends`, `decorator`, `type-ref`,
  `jsx`.
- **`ExtractedRelation`**: `(type, from_name, to_name, span)` — type ∈
  IMPORTS/CALLS/EXTENDS/IMPLEMENTS. `to_name` é o **texto** como escrito;
  a resolução é responsabilidade do graph builder, não do adaptador.

## Isolamento de falhas

- Parse failure é por arquivo: `has_error`, `diagnostics` e
  `parse_status` da ocorrência (`parsed` | `parsed-with-errors` |
  `parse-failed`). Um arquivo quebrado nunca aborta o snapshot.
- `parse_bytes` pode retornar `None` em timeout → resultado com
  diagnóstico `parse-timeout` (timeout configurável por arquivo).
- Arquivos sem adaptador (`supported=False`) viram nó `file` no grafo,
  sem módulo — o inventário nunca perde o arquivo.

## Regras de escopo sintático (Cycle 2)

- Funções anônimas não ganham identidade própria (só quando ligadas a um
  nome — declarator/property no TS).
- `import a.b` liga `a`; `import a.b as c` liga `c`; `from m import x as
  y` liga `y` — bindings registrados via `import-member`.
- Star-imports (`from m import *`) não emitem arestas IMPORTS —
  limitação conhecida, documentada.
- Comparação de nós tree-sitter **sempre por `.id`** — wrappers Python
  do mesmo nó não compartilham identidade (`is`/`is not` quebram).

## Como adicionar uma linguagem

1. Adicionar a gramática pinned em `pyproject.toml` (versão `==`).
2. Criar `parsers/languages/<lang>.py` implementando `ParserAdapter`.
3. Registrar em `ParserRegistry.default()`.
4. Adicionar fixtures golden em `tests/golden/corpus/<...>` com
   `expected.json` congelado — sem isso o adaptador não é aceito.
