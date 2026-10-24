# Extensibilidade — ports & adapters

O domínio expõe `Protocol`s em `code_atlas.contracts.ports`
(`src/code_atlas/contracts/ports.py`). Adapters concretos plugam na borda
sem o domínio importá-los (regra hexagonal).

## Ports definidos

| Port | Assinatura essencial | Estado Cycle 1 |
|---|---|---|
| `RepositoryScanner` | `scan(root, options) -> ScanResult` | **implementado** — `discovery.scan` |
| `ParserAdapter` | `name`, `version`, `languages()`, `capabilities()` | contrato — Tree-sitter no Cycle 2 |
| `SymbolResolver` | `resolve(occurrence) -> ResolutionStatus` | contrato — SCIP/LSP no Cycle 3 |
| `GraphStore` | `latest_ready_snapshot`, `snapshot_by_id` | **parcial** — `storage.Store` |
| `SearchPort` | `search(query, limit)` | contrato — FTS no Cycle 4 |
| `IndexCoordinator` | `index(root) -> Snapshot` | **implementado** — `indexing.IndexCoordinator` |

## Regras para novos adapters

1. Viver fora de `contracts/` (ex.: `parsers/tree_sitter/`, Cycle 2).
2. Declarar capability matrix real — nunca prometer capacidade ausente.
3. Fallbacks devem ser marcados (ADR-004): um resolver degradado reporta
   `confidence_class`/`resolution_status` honestos, não finge `resolved`.
4. Nenhum adapter pode executar código do repo indexado (ADR-014).
5. Dependências novas exigem justificativa — runtime hoje é stdlib-only.

## Exemplo de contrato (Cycle 2 preview — não implementado)

```python
class TreeSitterPython:  # implements ParserAdapter
    name = "tree-sitter-python"
    version = "0.x"
    def languages(self): return frozenset({"python"})
    def capabilities(self): return frozenset({"symbols", "imports"})
```

Adapters que só existem como nome no contrato **não** devem ser criados —
spec do grill gate proíbe implementações placebo.
