# Análise de impacto interativa — Explorer

> Cycle 6 (lf-0090 Phase 10 · lf-0091). Tela `/impact`, implementada em
> `explorer/src/app/features/impact/`. Companheira de
> `docs/visual-diff.md` (tela `/revisions`, que compartilha o mesmo
> pipeline de resultado).

## 1. Fluxo: formulário → `POST /impact/analyze` → view-model

```
ImpactComponent (features/impact/impact.component.ts)
  │ formulário: repository_root (select de repositórios reais),
  │   base_revision, target_revision, symbol?, file?,
  │   include_possible (default true), known_only (default false)
  ▼ analyze()
perf.time('POST /impact/analyze', …)        perf-log.service.ts (latência
  │                                           fica registrada p/ /diagnostics)
  ▼
ApiService.analyzeImpact(params)            core/api/api.service.ts
  │ POST /api/v1/impact/analyze  (corpo JSON validado no handler)
  ▼
src/code_atlas/api/handlers.py::_impact
  │ valida: repository_root ≤1000 obrigatório; base/target_revision ≤200;
  │   symbol ≤300; file ≤1000; *_snapshot_id inteiros;
  │   include_possible/known_only booleanos
  ▼
src/code_atlas/impact/engine.py::ImpactEngine.analyze
  │ diff git → resolve par de snapshots (escopo do repo, READY-only)
  │ → revision binding → symbol diff → contratos → blast radius
  │ → test candidates → risk → ImpactResult + diagnostics[]
  ▼
impact.vm.ts::toImpactView(response)
  │ mapeia ApiImpactResponse → ImpactView (tudo pré-formatado pt-BR:
  │   labels de change kind, impact kind, consumer_state, compatibilidade,
  │   relationship, risk, agrupamentos por kind/hops)
  ▼
ImpactResultsComponent (features/impact/impact-results.component.ts)
  │ renderiza banners + seções; phase idle/loading/ready/error/offline
```

O componente de apresentação (`ImpactResultsComponent`) é o mesmo usado
pela tela `/revisions` — não existem dois renderizadores de resultado de
impacto com semânticas divergentes.

## 2. Banner de análise parcial — obrigatório, inviolável

A regra vem de `docs/explorer-ui-ux.md` §5.10: **qualquer** resultado com
diagnóstico de binding/cobertura deve ser apresentado como parcial, e é
estruturalmente impossível confundi-lo com um resultado verificado.

Implementação (`impact.vm.ts`):

- `BINDING_DIAGNOSTIC_CODES` — 15 códigos que tornam o resultado parcial:
  `repo-not-indexed`, `invalid-revision`, `git-error`,
  `revision-unresolved`, `revision-binding-mismatch`, `dirty-worktree`,
  `snapshot-for-revision-missing`, `content-unverifiable`,
  `content-mismatch`, `snapshot-missing`, `cross-repo-snapshot`,
  `snapshot-failed`, `snapshot-unavailable`, `partial-analysis`,
  `semantic-blocked`.
- `isPartialAnalysis(view)` — interseção não-vazia entre
  `view.diagnostics` e o conjunto acima.

Renderização (`impact-results.component.ts`):

- Banner `"⚠ Análise parcial"` com `role="alert"` e rampa **warning** —
  nunca estética de `verified`/verde. O texto afirma que os achados são
  indicativos, não prova.
- Banner separado `"Análise semântica bloqueada"` quando
  `isSemanticBlocked(view)` — derivado de diagnósticos bloqueantes
  (`semantic-blocked`, `snapshot-unavailable`, `repo-not-indexed`) ou de
  `unknown_coverage` contendo `semantic-blocked`/`no-snapshot`.
- Banner de truncamento quando `view.truncated`.

Do lado do engine (`src/code_atlas/impact/engine.py:120-137`):
`BindingState.MISMATCH` e `UNAVAILABLE` são hard-block — `analyze()`
retorna cedo com seções semânticas vazias e
`unknown_coverage=("semantic-blocked",)`; `PARTIAL` emite o diagnóstico
`partial-analysis` mas segue a análise (ver `revision_binding.py`).
Ou seja: o backend já degrada honestamente; a UI apenas **não esconde**.

## 3. Risco ≠ incerteza — eixos separados

`RiskAssessment` (`src/code_atlas/impact/contracts.py:236` e redondezas)
carrega **dois campos independentes**:

| Eixo | Valores | Significado |
|---|---|---|
| `category` | `low · moderate · high · critical · unknown` | tamanho do risco estimado |
| `uncertainty` | `low · moderate · high` | confiança na estimativa |

A UI renderiza os dois lado a lado na seção "Risco"
(`impact-results.component.ts`): "Categoria" com rampa de cor
low→critical e "Incerteza (eixo separado)" com rótulo próprio — junto de
`formula_version` e a lista `reasons`. Um `category=low` com
`uncertainty=high` não pode ser lido como "tudo certo": a incerteza está
sempre visível ao lado da categoria, nunca fundida nela.

## 4. Tags de revisão

`impact.vm.ts::revisionTagLabel` mapeia o campo `revision` dos itens do
resultado (em especial `test_candidates`):

| Valor cru | Rótulo exibido |
|---|---|
| `base` | revisão base |
| `target` | revisão alvo |
| qualquer outro/ausente | sem vínculo de revisão |

Cada teste candidato renderiza: tag de revisão + `relationship`
(direto/transitivo/heurístico/desconhecido) + `strength` + `reason`. A
seção se chama "Candidatos de teste" — a UI nunca os apresenta como
cobertura garantida, e a lista vazia exibe texto explícito ("ausência de
candidatos não é evidência de segurança", conforme §5.6 do spec de UX).

## 5. Seções renderizadas (ImpactResultsComponent)

1. **Resumo** — repositório, `base_revision → target_revision`,
   `base_snapshot_id`/`target_snapshot_id`, contagens do `change_set`,
   flag `dirty_worktree`.
2. **Risco** — categoria + incerteza + `formula_version` + `reasons`.
3. **Símbolos alterados** — agrupados por `SymbolChangeKind`
   (adicionado/removido/renomeado/movido/assinatura/corpo/doc alterado/
   sem mudança semântica/desconhecido), com `strength`, assinaturas
   antiga→nova e caminho antigo→novo.
4. **Contratos públicos** — `contract_changes` com `compatibility`
   (risco potencial / provavelmente compatível / sem impacto de
   contrato / indeterminado).
5. **Raio de impacto** — `impacts` agrupados por `path.hops`
   (`-1` → "Sem cadeia conhecida"), cada um com `impact` (direto/
   transitivo/possível/desconhecido), `strength`, `consumer_state` e a
   cadeia de propagação quando presente.
6. **Candidatos de teste** — ver §4.
7. **Cobertura desconhecida** — `unknown_coverage` listado literalmente.
8. **Diagnósticos** — `code` + `message` de cada diagnóstico do engine.

## 6. O que a tela **não** faz (declarado)

- **Não exporta JSON.** O spec (`explorer-product-spec.md`, jornada C10)
  prevê cópia do `ImpactResult`; o componente atual não tem botão de
  export — apenas `/context` exporta. Registrado em
  `docs/cycle-6-limitations.md`.
- **Não tem deep-linking de parâmetros** — `?base=…&target=…` na URL não
  restaura o formulário (seam `[alvo-C6]` de `explorer-ui-ux.md` §1.2).
- **Não mostra histórico** — não existe `GET /impact/{id}` no backend;
  cada análise é síncrona e efêmera (declarado em `docs/explorer-api.md`).

## 7. Referências

- UI: `explorer/src/app/features/impact/{impact.component.ts,
  impact.vm.ts, impact-results.component.ts}`.
- API: `src/code_atlas/api/handlers.py::_impact`.
- Engine: `src/code_atlas/impact/{engine.py, revision_binding.py,
  contracts.py, risk_classifier.py, report.py}`.
- Spec: `docs/explorer-ui-ux.md` §5.10/§5.11/§6.2;
  `docs/explorer-product-spec.md` jornada C.
- Testes: `explorer/src/app/features/impact/impact.component.spec.ts`,
  `tests/api/test_explorer_api.py` (rota + payload).
