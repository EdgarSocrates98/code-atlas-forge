# SCIP Revision Validation (lf-0028)

> Status: **implementado e integrado** — `resolvers/scip/provenance.py`
> (`IndexProvenance`, `assess_revision`), chamado pelo bootstrap antes
> de construir o `ScipResolver`.

## Contrato

Um índice SCIP é artefato externo: sem verificação de revisão ele pode
estar stale, de outro repo, ou malicioso. O Atlas trata os bytes como
untrusted e decide com 7 sinais **sem executar nada do repo**:

| Sinal | Efeito |
|---|---|
| `project_root` ↔ workspace | mismatch → `STATUS_REJECTED` (resolver nunca criado) |
| staleness vs worktree (paths/mtimes/digest) | → `STATUS_DEGRADED` |
| documentos referenciam paths fora do root | degrade |
| spans vs fim de arquivo | degrade |
| `position_encoding` desconhecido | degrade |
| metadata incompleta (tool_info, versão) | degrade |
| símbolos duplicados | degrade |

`rejected` → factory ausente + `ParseDiagnostic`; `degraded` →
resolver ativo mas `confidence_class` capado em `heuristic`;
`current` → resolver pleno. Sem assessment, o default é `degraded`
(nunca pleno não-verificado).

## Proveniência persistida

`IndexProvenance` (sha256 do índice, produtor@versão, identidade do
repo, revisão, digest do worktree, status, evidências) é serializável
e inspecionável; `semantic-status` expõe `revision` do snapshot via
`analyzer_versions["resolver:scip"]` = sha16 do índice que produziu o
READY (lf-0030: participa da identidade — índice novo invalida).

## Verificação

`tests/resolvers/test_provenance.py` (15): cada sinal, statuses,
decisão; `tests/resolvers/test_scip.py` (rebuild do resolver com
proveniência explícita); golden `test_scip_bad_fixtures_rejected`.
