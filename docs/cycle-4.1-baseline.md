# Cycle 4.1 — Baseline Audit (lf-0047)

SHA inicial: `713fe1d` (main pós-PR #6). CI: 5/5 verde no merge.

## Reprodução das métricas publicadas (Cycle 4)

`retrieval_eval.retrieval_block` re-executado → valores idênticos aos
publicados em `cycle-4-evidence.md`:

| estratégia | P@10 pub | R@10 pub |
|---|---|---|
| structural | 1.0 | 1.0 |
| lexical | 0.4167 | 1.0 |
| graph | 0.0 | 0.0 |
| hybrid | 0.0909 | 1.0 |

## Auditoria metodológica do avaliador (achados)

1. **Denominador P@K**: usa `tp/len(ranked)`, não `tp/K`. Igual quando
   retorna K hits; diverge com retornos parciais. → corrigir para tp/K.
2. **Agregação**: `statistics.median` sobre 8 tarefas — macro por
   mediana, sem média/micro. → reportar macro-mean + micro.
3. **FP**: conta hits fora do conjunto relevant — correto para o
   universo fechado do fixture, mas pune hit-lists maiores; declarar
   semântica `fp_hits` ≠ FPR (sem universo de não-relevantes anotado).
4. **NDCG**: binário apenas — sem relevância graduada; empates
   tratados posicionalmente (penalização implícita, honesta).
5. **MRR**: 1/rank do primeiro relevante — correto.
6. **Recall**: `tp/|relevant|` — correto; tarefas sem relevantes não
   tratadas (todas têm ≥1) — fix: excluir da agregação, contar em
   `no_relevant`.
7. **Vazamento**: judgments autorais contra o mesmo fixture de tuning
   — dataset único, sem held-out. Mitigação: corpus vendored novo em
   lf-0055 com judgments independentes.
8. **Símbolo vs ocorrência**: hits dedup por occurrence em
   lexical/structural e por símbolo em hybrid — unidades de avaliação
   inconsistentes entre estratégias. → normalizar para `symbol_key`
   na avaliação.
9. **Graph R=0 — causa raiz verificada**: não é filtro de arestas
   (BFS inclui ambiguous/unresolved, ranqueado). É que calls
   `x.method()` **nunca geram `to_id` navegável** — syntactic resolver
   não liga `svc` → `OrderService`, logo a aresta fica
   `external:`/unresolved sem ocorrência-alvo para BFS alcançar.
   Aresta existe como linha, mas não é traversável. Alimenta lf-0049.
10. **Evidence validity**: lexical/structural retornam
    `matched_fields` sem `evidence` → ev=0 honesto. Alimenta lf-0051.

## Veredito

Baseline reproduzível; números publicados são medidos de verdade —
mas a metodologia tem as fragilidades 1–8 acima. lf-0048 corrige o
avaliador; NUNCA usar as métricas antigas como verdade absoluta.
