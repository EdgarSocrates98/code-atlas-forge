# Repository Control Center — `/repositories`

> Implementado no Cycle 6.1 (lf-0097). Substitui o `feature-placeholder`
> do ciclo 6 (achado L19/F2) — a tela agora lista dados reais do índice.
> Fonte: `explorer/src/app/features/repositories/{repositories.component.ts,
> repositories.vm.ts}`.

## O que a tela faz

1. **Lista real** de `GET /repositories` — `root_handle` + `identity_key`
   por repo, nunca placeholder.
2. **Cruzamento com `GET /index/status`** — por repo: `ready_snapshots`,
   `latest_ready` e contagens honestas de `file_inventory` (Arquivos),
   `symbol_occurrences` (Símbolos), `relations` (Relações). Repositório
   sem snapshot READY mostra `—`, não zero inventado.
3. **Detalhe do snapshot mais recente** via
   `GET /repositories/{id}/snapshots` (por repo, best-effort — falha de
   um repo degrada sua linha para `—` sem derrubar a tela):
   `#id`, `created_at`, `git_ref`, `commit_sha` curto.
4. **Seleção de contexto global** — o botão `Selecionar` grava o repo no
   `ExplorerContextService` e pinna o snapshot READY exibido; a linha
   ativa mostra o selo `contexto ativo` e o botão vira `Selecionado`
   (disabled). Todas as telas passam a consultar com
   `repository_id`/`snapshot_id` desse contexto.
5. **Deep links de ação** por card: `Abrir grafo` →
   `/graph?repo=<root_handle>&snapshot=<latestReadyId>`,
   `Pesquisar` → `/search?repo=<root_handle>`,
   `Diagnósticos` → `/diagnostics`, `Copiar caminho` (clipboard com
   feedback `role="status"` — sucesso e falha honestos).
6. **`Atualizar`** re-busca tudo; estados `loading`/`offline`/`error`
   via `atlas-state-block`; lista vazia → estado `empty` com a instrução
   real (`code-atlas index <caminho>` — o Explorer só lê o índice).

## Contratos consumidos

| Endpoint | Uso |
|---|---|
| `GET /api/v1/repositories` | `{items:[{id, identity_key, root_handle}], total}` |
| `GET /api/v1/index/status` | `{items:[{repository_id, root_handle, ready_snapshots, latest_ready, counts{file_inventory, symbol_occurrences, relations, evidence}}]}` |
| `GET /api/v1/repositories/{id}/snapshots` | `{items:[{id, status, commit_sha, git_ref, created_at}]}` — latest READY exibido |

`repositories.vm.ts` (`buildRepositoryRows`) faz o join determinístico
repo↔status↔snapshots; `shortSha` trunca `commit_sha` para exibição.

## Read-only por design

Nada nesta tela escreve: indexação é exclusiva da CLI
(`code-atlas index`). O botão `Selecionar` altera apenas o contexto
client-side (signal + persistência de `{identity_key, snapshotId}` em
`localStorage['atlas-explorer-context']`).

## Verificação

- Unit: `repositories.component.spec.ts` (render de linhas, seleção,
  estados).
- E2E real: `flows2.spec.ts` teste **11** — "repositories page shows real
  counts and the auto-selected context" (contra índice real da fixture,
  sem mocks).
- A11y: rota `/repositories` nos 24 scans axe — 0 violações
  (`docs/explorer-accessibility-report.md`).
