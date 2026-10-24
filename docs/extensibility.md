# Extensibilidade — ports & adapters

O domínio expõe `Protocol`s em `code_atlas.contracts.ports`
(`src/code_atlas/contracts/ports.py`). Adapters concretos plugam na borda
sem o domínio importá-los (regra hexagonal).

## Ports definidos

| Port | Assinatura essencial | Estado Cycle 2 |
|---|---|---|
| `RepositoryScanner` | `scan(root, options) -> ScanResult` | **implementado** — `discovery.scan` |
| `ParserAdapter` | `language`, `extensions`, `parse(request) -> ParseResult` | **implementado** — `parsers/languages/{python,typescript,tsx}.py` (Tree-sitter pinned, ADR-015) |
| `SymbolResolver` | `resolve(occurrence) -> ResolutionStatus` | **implementado** — `resolvers/` (syntactic → scip → lsp, ADR-016) |
| `GraphStore` | `latest_ready_snapshot`, `snapshot_by_id` | **parcial** — `storage.Store` |
| `SearchPort` | `search(query, limit)` | **implementado** — `search.structural.StructuralSearch` (SQL, não FTS) |
| `IndexCoordinator` | `index(root) -> Snapshot` | **implementado** — `indexing.IndexCoordinator` |

## Regras para novos adapters

1. Viver fora de `contracts/` (ex.: `parsers/languages/<lang>.py`).
2. Declarar capability matrix real — nunca prometer capacidade ausente.
3. Fallbacks devem ser marcados (ADR-004): um resolver degradado reporta
   `confidence_class`/`resolution_status` honestos, não finge `resolved`.
4. Nenhum adapter pode executar código do repo indexado (ADR-014).
5. Dependências novas exigem justificativa — runtime hoje é stdlib +
   Tree-sitter pinned (ADR-015 emendou a postura zero-dep do Cycle 1).
6. Adapters de parser novos exigem fixture golden em
   `tests/golden/corpus/` com `expected.json` congelado — sem ele, o
   adaptador não é aceito.

## Exemplo real — adaptador implementado (Cycle 2)

```python
class PythonAdapter:
    language = "python"
    extensions = (".py", ".pyi")
    capabilities = ParserCapability(decorators=True)

    @property
    def adapter_id(self): return "tree-sitter-python"
    def parse(self, request: ParseRequest) -> ParseResult: ...
```

Contrato completo + matriz de linguagens: `docs/parser-adapters.md`.
Adapters que só existem como nome no contrato **não** devem ser criados —
spec do grill gate proíbe implementações placebo.
