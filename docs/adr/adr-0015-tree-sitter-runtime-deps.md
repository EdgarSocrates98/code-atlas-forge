# ADR-015: Tree-sitter como dependência de runtime pinned

**Status:** decidida e implementada (Cycle 2, lf-0008)

## Contexto

O Cycle 1 fixou uma postura de *zero dependências de runtime* (stdlib only) —
ver ADR-001. O Cycle 2 exige parsing real: extração determinística de símbolos,
referências e relações em Python, TypeScript e TSX. As alternativas:

- **`ast` (stdlib)** cobre apenas Python — TypeScript/TSX exigiriam um parser
  próprio (inviável e não-determinístico para o escopo).
- **LSP/SCIP externos** violam local-first (servidores, processos, rede).
- **Tree-sitter via wheels PyPI**: gramáticas compiladas, parsing puramente
  local, determinístico, com versionamento público e checksums verificáveis.

## Decisão

Dependências de runtime **pinned exatamente** em `pyproject.toml`:

```
tree-sitter==0.25.2
tree-sitter-python==0.23.6
tree-sitter-typescript==0.23.2
```

- Versões exatas (`==`), nunca ranges — o grafo é função das gramáticas;
  bump de versão exige revisão consciente e roda o corpus golden.
- `analyzer_versions` do snapshot registra `parser:<lang>` =
  `adapter/parser+grammar` — mudança de gramática **derrota o no-op**
  de reindex (testado em `test_extractor_version_change_defeats_noop`).
- Nenhuma dependência é opcional: sem Tree-sitter o pacote instala mas
  `index` não parseia — por isso são deps obrigatórias, não extras.

## Consequências

- A postura "zero deps" do ADR-001 é **emendada**, não revogada: continua
  proibido depender de serviço externo, rede, Docker, SaaS, embeddings ou
  banco separado. Dependências de *código* pinned, offline e auditáveis
  são admitidas quando indispensáveis à função central.
- `pip-audit` cobre as três deps no CI (zero vulnerabilidades na data do
  ciclo).
- Novos adaptadores seguem o mesmo padrão: gramática pinned +
  `ParserAdapter` + entrada na matriz de linguagens.
