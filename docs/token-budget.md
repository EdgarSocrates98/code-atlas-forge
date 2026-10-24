# Token Budget (ADR-0027)

Reservas: 5% meta + 10% evidência + 5% diagnósticos; itens recebem o
restante (mín 32 tokens). Clamp `[64, 1_000_000]` — budget absurdo não
infla reservas (bug corrigido em lf-0045). `insufficient_budget=true`
reporta `suggested_minimum_budget` em vez de retornar pack vazio.
