# ADR-002: Monorepo modular Python

**Status:** decidida e implementada (Cycle 1)

## Contexto

O Blueprint define um monorepo Python modular com contextos delimitados
(`contracts`, `discovery`, `storage`, `indexing`, `observability`, futuros
`parsers`, `resolvers`, `graph`, `search`, `impact`, `context`, `api`, `mcp`,
`explorer`).

## Decisão

`src/` layout, pacote único `code_atlas` com subpacotes por contexto,
fronteiras hexagonais (domínio não importa borda), entry point `code-atlas`.
Zero dependências de runtime no Cycle 1.

## Consequências

- Subpacotes futuros nascem dentro de `src/code_atlas/` respeitando as
  regras de dependência já testadas.
- `pip install -e .[dev]` cobre todo o workflow de desenvolvimento.
