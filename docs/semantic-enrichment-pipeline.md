# Semantic Enrichment Pipeline (lf-0027)

> Status: **implementado e integrado** — estágio entre `graph.build` e
> `persist` no `IndexCoordinator`. Fonte:
> `src/code_atlas/resolvers/enrichment.py` (`SemanticEnricher`).

## Motivação

No Cycle 3 os resolvers SCIP/LSP existiam como componentes testados mas
**nunca invocados** pelo `index()` — era código isolado. O Cycle 3.1
conecta o subsistema ao pipeline sem mudar o contrato do grafo.

## Estágio `SemanticEnricher`

Entrada: `GraphBuildResult` (baseline syntactic já aplicado pelo
builder). Saída: mesmo build com algumas arestas promovidas + stats
(`semantic_attempted`, `semantic_enriched`, `semantic_diagnostics`).

Para cada aresta não-`resolved` de tipo elegível
(`CALLS→call`, `IMPORTS→import`, `EXTENDS/IMPLEMENTS→type-ref`,
`REFERENCES→reference`) o enriquecedor consulta o
`ResolverCoordinator` na ordem de `EvidenceTier` (scip → lsp), com
`ResolutionRequest` que carrega `module` = `source_path` da aresta
(`GraphEdge.source_path`, campo adicionado neste ciclo).

## Regras invioláveis (ver docstring do módulo)

1. Aresta `resolved` nunca é reaberta/reescrita.
2. Resultado não-`resolved` preserva a aresta original verbatim.
3. `target_id` precisa ser nó persistível **deste** build — vínculo
   cross-snapshot é impossível por construção (checado por
   `persistable` + `store.cross_snapshot_relations`).
4. Primeira evidência `resolved` no tier mais alto vence; alternativas
   coexistem como diagnósticos (não são projetadas).
5. Exceção de resolver → `ParseDiagnostic`, nunca crash.

## Bootstrap (`resolvers/bootstrap.py`)

`bootstrap_resolvers(table)` monta as factories a partir de
`[resolvers]` do `config.toml` do operador:

- `scip`: `index_path` → `load_index` (cap `MAX_INDEX_BYTES`) +
  `assess_revision` (lf-0028). `rejected` → factory ausente +
  diagnóstico; `degraded`/`current` → `ScipResolver` com proveniência.
- `lsp.servers.<name>`: `LspServerSpec` (cmd, languages, enabled) —
  spawn só ocorre `enabled=True`, lazy no primeiro uso.
- erros de bootstrap → `ParseDiagnostic` no payload do index
  (`stats.semantic_diagnostics`), nunca abortam a indexação.

`describe_resolvers(table, workspace)` (lf-0032) devolve o relatório
read-only usado por `semantic-status`: `installed` (artefato
carrega/`shutil.which`), `enabled`, proveniência/resumo — sem spawnar
servers.

## CLI

`index --scip PATH` sobrescreve `index_path` na invocação
(proveniência intacta — `resolver:scip=<sha16>` segue na identidade);
`index --resolver NAME` restringe resolvers ativos (`syntactic`,
`scip`, `lsp`, `lsp:<nome>`); nome desconhecido = erro tipado.
`semantic-status` lê só o snapshot READY persistido: uso real vem de
`GROUP BY resolver, resolution_status` em `relations`; arestas
sintáticas com parser-id agregam no tier `syntactic-scope-v1`.

## Proveniência por aresta

Cada aresta guarda `resolver`, `resolution_status`,
`confidence_class`, `evidence_ids` — `semantic-status` e o eval-report
separam métricas por resolver/tier sem misturar classes de evidência.
