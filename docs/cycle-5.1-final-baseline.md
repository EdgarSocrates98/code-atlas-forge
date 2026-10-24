# Cycle 5.1 FINAL — Baseline (Phase 0)

Baseline `6c0b93d`. Achados confirmados/reprovados:

| # | achado | sev | estado |
|---|--------|-----|--------|
| 1 | `_bind_revisions` só registrava diagnostic — MISMATCH não bloqueava análise semântica | P0 | **FIX** `ImpactRevisionPlan` + `semantic-blocked` |
| 2 | `CONTENT_VERIFIED` retornado sem comparação de conteúdo | P0 | **FIX** `_content_verify`: manifest-sha256 snapshot vs scan atual, só HEAD limpo |
| 3 | Consumidores históricos de removidos sem reconciliação | P1 | **FIX** `consumer_state` aditivo |
| 4 | `visited` global descartava caminhos de origens distintas | P1 | **FIX** chave `(occ, origem)` |
| 5 | TestSelector só via snapshot alvo — testes históricos perdidos | P1 | **FIX** `base_snapshot_id` + campo `revision` |
| 6 | Snapshot FAILED/cross-repo | P0 | já correto (lf-0071) |
| 7 | Rename ambíguo por hash | P1 | já correto (lf-0073) |
