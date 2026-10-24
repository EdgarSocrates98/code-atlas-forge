# ADR-0027 — Política de Token Budget (lf-0040/0045)

## Decisão
Budget com reservas fixas: meta 5%, evidências 10%, diagnósticos 5%,
itens = restante (mín 32). Clamp `[64, 1_000_000]` no engine —
budget absurdo não infla reservas. `insufficient_budget` +
`suggested_minimum_budget` tipados. Estimador pluggable via
`TokenCounter` seam; default `heuristic-chars4` (chars/4).

## Por quê
- Reservas garantem espaço para evidência/diagnóstico — nunca pack
  sem explicação por falta de budget de envelope.
- Clamp adicionado no lf-0045 após teste adversarial expor reservas
  infláveis — mecanismo documentado, não escondido.
