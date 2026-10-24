# ADR-0028 — Evidência de Retrieval (lf-0035/0038/0044)

## Decisão
Todo hit carrega `matched_fields` + `evidence` (origem estratégia,
rank por estratégia, `rrf=...`, resolver/tier quando semântico).
`evidence_validity` medida no eval; gaps honestos reportados:
lexical/structural hoje populam `matched_fields` mas deixam
`evidence` vazio — registrado como hardening futuro, não maquiado.

## Por quê
- "Ranking preserva evidências" (aceite do ciclo) exige trilha por
  hit — RRF sem evidência seria caixa-preta.
- Métrica medida (eval-report cycle4) em vez de claim.
