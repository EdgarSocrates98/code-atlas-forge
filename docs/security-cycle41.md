# Security Cycle 4.1

Novos vetores + mitigações (tests/e2e/test_adversarial_cycle41.py):

| risco | mitigação | teste |
|---|---|---|
| exceção de tool derruba server | `except` → `-32603`, processo vivo | `test_tool_exception_never_kills_server` |
| evidência fabricada p/ símbolo ausente | refs==[] evidência escopada | `test_evidence_never_fabricated_for_missing_symbol` |
| ref cross-snapshot/repo | validação contra dono real do snapshot | `test_cross_snapshot_ref_rejected` |
| payload serializado > budget | enforce + diag explícito | `test_payload_budget_declares_overflow` |
| fanout não-ilimitado de refs | LIMIT 25 + snapshot pinado | `test_evidence_refs_bounded` |

Herdados do Cycle 4 (regressão mantida): prompt injection como dado,
SQLi inerte, limites de limit/depth/budget, sem write tools, sem
execução de conteúdo indexado, sem rede por padrão.

**0 P0/P1 abertos.**
