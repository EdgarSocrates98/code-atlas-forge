# Diff visual de revisões — Explorer

> Cycle 6 (lf-0091 Phase 11). Tela `/revisions`, implementada em
> `explorer/src/app/features/revisions/revisions.component.ts`.
> Renderiza o diff **de símbolos** entre dois pontos do mesmo
> repositório — não é um diff linha-a-linha de código (ver §6).

## 1. Semântica da tela

O formulário monta um **par (base → alvo)** e chama `POST
/api/v1/impact/analyze` — o mesmo endpoint e o mesmo `ImpactEngine` da
tela `/impact`. Não existe um pipeline de diff separado: o que muda é
*como o par é escolhido*.

Três maneiras de indicar cada lado (combináveis):

| Entrada | O que envia | Comportamento |
|---|---|---|
| **Select de snapshot** | `base_snapshot_id` / `target_snapshot_id` | lista `GET /repositories/{id}/snapshots` — `#id · status · sha(12) · created_at`; todos os status aparecem (`READY`, `FAILED`, `STALE`), o backend rejeita os não-READY |
| **Expressão de revisão** (opcional) | `base_revision` / `target_revision` | sha ou expressão git (ex.: `HEAD~1`, `snapshot:N`); alvo vazio = worktree atual |
| **Ambos vazios** | nada | o backend resolve o par implicitamente: READY mais recente e seu antecessor |

`canCompare()` exige repositório + pelo menos um dos quatro caminhos
preenchido (`revisions.component.ts:327-335`).

Comportamento ao trocar de repositório (`onRepositoryChange`,
`revisions.component.ts:379-407`): a lista de snapshots e **ambas** as
seleções são zeradas antes do refetch — um snapshot do repo anterior
nunca contamina o novo contexto. Com ≥2 snapshots READY, o mais recente
vira alvo e o segundo vira base automaticamente; com um repo só no
índice, o repositório já vem pré-selecionado.

## 2. Estados de binding — o veredito vem antes dos números

O `revision_binding` do engine (`src/code_atlas/impact/revision_binding.py`)
classifica o vínculo de cada lado em:

| `BindingState` | Significado | Efeito |
|---|---|---|
| `exact` | revisão resolve ao commit do snapshot | verificado |
| `content_verified` | conteúdo conferido (hash/dirty) | verificado |
| `partial` | vínculo com ressalvas | diagnóstico `partial-analysis` → banner |
| `mismatch` | revisão diverge do snapshot | **hard-block** → `semantic-blocked` |
| `unavailable` | impossível verificar | **hard-block** → `semantic-blocked` |

Consequência no engine (`engine.py:120-137`): mismatch/unavailable
encerram a análise cedo — `symbol_changes`, `impacts`,
`contract_changes` e `test_candidates` voltam vazios e
`unknown_coverage=("semantic-blocked",)`. `partial` segue com o
diagnóstico. A UI **não pode** apresentar essas listas vazias como
"nada mudou" porque:

1. o cabeçalho de vínculo (`revisions.component.ts:161-173`) mostra
   `base <rev> (snapshot #N) → alvo <rev|(worktree)> (snapshot #M)`
   seguido de `— PARCIAL` (warning) ou `— verificado`;
2. o `ImpactResultsComponent` compartilhado desenha os banners de
   `partial-analysis` / `semantic-blocked` / truncamento
   (ver `docs/interactive-impact.md` §2);
3. o estado vazio ("Nenhuma diferença de símbolos…") só aparece quando
   `totalChanges == 0 && totalImpacts == 0 && totalTests == 0 &&
   diagnosticsTotal == 0 && !semanticBlocked`
   (`revisions.component.ts:337-350`) — um resultado bloqueado cai no
   banner de bloqueio, nunca no "vazio".

Diagnósticos de binding que o usuário pode ver (definidos em
`impact.vm.ts::BINDING_DIAGNOSTIC_CODES`): `invalid-revision`,
`revision-unresolved`, `revision-binding-mismatch`, `dirty-worktree`,
`snapshot-for-revision-missing`, `content-unverifiable`,
`content-mismatch`, `snapshot-missing`, `cross-repo-snapshot`,
`snapshot-failed`, `snapshot-unavailable`, `git-error` —
cada um vira linha na seção **Diagnósticos** com `code` + `message`
reais do backend.

## 3. Sem mistura entre revisões nem entre repositórios

Garantias verificadas no código:

- **Escopo por repositório no backend.** `handlers._snapshot`
  (`src/code_atlas/api/handlers.py:66-86`) rejeita `snapshot_id` de
  outro repositório com `snapshot-not-found` 404 e qualquer snapshot
  não-READY com `snapshot-not-ready` 409. O resolver do engine também é
  restrito ao `repository_id` do `repository_root` enviado
  (`engine.py:114` — "SEMPRE escopados ao repositório do root").
- **Par único por análise.** `ImpactEngine.analyze` computa o diff de
  símbolos sobre exatamente `base_sid → target_sid`
  (`symbol_diff.diff_snapshots`, `engine.py:140-142`) e o blast radius a
  partir desses dois snapshots — não há campo que admita uma terceira
  revisão nem agregação entre análises.
- **Reset do cliente ao trocar de repo** — ver §1.
- **Revisão livre é validada contra o snapshot**, não confiada: o
  binding pode devolver `revision-binding-mismatch` /
  `content-mismatch` / `content-unverifiable` e o resultado inteiro
  passa a carregar o rótulo PARCIAL (ou bloqueio semântico).

## 4. Pipeline idêntico ao `/impact`

`revisions.component.ts` importa `ImpactResultsComponent` e
`toImpactView` diretamente de `features/impact/` (linhas 19–20). Ou
seja: banners, seções, rótulos de risco/incerteza, tags de revisão e a
seção de diagnósticos são literalmente o mesmo código — impossível a
tela de revisões "achar" um resultado verificado que a tela de impacto
consideraria parcial.

## 5. Interação com o estado `semantic-blocked`

Quando o binding falha feio, o resultado mostra:

- o banner "Análise semântica bloqueada" (rampa danger);
- `change_set` e metadados do par (o que o git diff conseguiu ler);
- seções semânticas rotuladas como indisponíveis pela ausência de
  conteúdo + `unknown_coverage` listando `semantic-blocked`;
- os diagnósticos exatos que causaram o bloqueio.

Conforme `explorer-ui-ux.md` §5.11: seções bloqueadas renderizam como
**indisponíveis**, nunca como vazias legítimas.

## 6. Limites honestos

- **Não é diff textual.** A tela difere símbolos/contratos/relações —
  não mostra hunks `+/-` de linhas. A "visual diff" do produto é o
  agrupamento por `SymbolChangeKind` com assinaturas e caminhos
  antiga→nova.
- **Sem histórico nem compartilhamento por URL.** Cada comparação é
  síncrona e efêmera; `?base=&target=` não restaura estado (seam
  `[alvo-C6]`).
- **Snapshot FAILED aparece no select mas é rejeitado pelo backend**
  (`snapshot-failed`/409) — a opção é visível de propósito (o usuário
  precisa saber que o snapshot existe), mas a análise recusa e o
  diagnóstico explica.

## 7. Referências

- UI: `explorer/src/app/features/revisions/revisions.component.ts` +
  `../impact/{impact.vm.ts, impact-results.component.ts}`.
- Backend: `src/code_atlas/api/handlers.py::_impact`,
  `src/code_atlas/impact/{engine.py, revision_binding.py,
  snapshot_resolver.py, symbol_diff.py}`.
- Spec: `docs/explorer-ui-ux.md` §5.10/§5.11;
  `docs/explorer-product-spec.md` jornada C;
  `docs/cycle-5.1-impact-integrity.md` (origem dos estados de binding).
- Testes: `explorer/src/app/features/revisions/revisions.component.spec.ts`.
