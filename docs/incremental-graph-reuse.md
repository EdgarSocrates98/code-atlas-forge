# Incremental Graph Reuse — análise comparativa (lf-0030 Phase 5)

Problema medido (lf-0023): incremental no corpus 5k economiza ~15-20%
porque **persist é O(snapshot)** — 110k linhas de
occurrences/relations são reescritas por snapshot mesmo quando 1
arquivo muda. O parse é economizado de verdade (cache lf-0020); o
custo residual é persistência.

## Modelos comparados

| modelo | economia de persist | canonicalidade | risco | custo de migração | GC |
|---|---|---|---|---|---|
| snapshots completos (atual) | nenhuma — O(snapshot) | ≡ full rebuild por construção | nulo | nenhum | trivial (`ON DELETE CASCADE`) |
| structural sharing de ocorrências | alta | exige prova de universo equivalente | alto: aresta semântica reutilizada sem revisão = review failure da spec | alto: identity física ≠ lógica ≠ ocorrência precisa de 3 níveis | refcount/GC complexo |
| content-addressed fragments | alta | forte (hash de conteúdo) | médio: fragmento captura só fatos locais — relações cross-file NÃO são fragmentáveis | alto | refcount por fragmento |
| manifests de fragmentos | média | forte | médio: manifest vira mais um artefato versionado | médio-alto | GC de manifest+fragmento |
| copy-on-write por linha | média | média | alto: compartilhar linha entre snapshots quebra isolamento | alto | dedup+refcount |
| materialização seletiva (delta) | alta | exige merge correto | alto: delta errado = snapshot inconsistente | médio | deltas acumulam |

## Decisão (ADR-0020)

**Manter snapshots completos** — structural sharing foi rejeitado:

1. Aresta semântica depende do **universo inteiro**, não do arquivo de
   origem — reutilizar ocorrências/relatenções compartilhadas exige
   provar equivalência de universo, que é exatamente o custo de
   reconstruir o grafo. A spec proíbe "reutilizar aresta semântica
   sem prova de universo equivalente" — a prova não é mais barata
   que o rebuild.
2. Isolamento de snapshot é a invariante que torna `incremental ≡
   full` trivialmente verdade hoje — quebrá-la por I/O introduz a
   classe de bug que a spec marca como review failure.
3. Compartilhamento real já existe onde é seguro: `symbols`
   (`logical_symbol_id` por repo), `source_files`, `parse_cache`.
4. A otimização honesta do gargalo real (round-trips) já foi feita
   em lf-0023 via batched INSERT…RETURNING + cache_size.

Alternativa segura implementada nesta spec (Phase 6): invalidação
mais precisa — índice SCIP novo (`resolver:scip=<sha16>`) entra na
identidade do índice e invalida relações conservadoramente.

GC: permanece cascade delete — nenhum compartilhamento novo, nenhum
refcount.
