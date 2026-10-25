# Navegação de contexto global — `ExplorerContextService` + deep links

> Implementado no Cycle 6.1 (lf-0098). Resolve três gaps do ciclo 6:
> telas não compartilhavam seleção de repo/snapshot (L20), faltava
> deep-linking (L23) e o seletor do toolbar era somente-leitura (F12).
> Fonte: `explorer/src/app/core/state/explorer-context.service.ts`,
> `explorer/src/app/layout/shell/shell.component.{ts,html}`.

## Modelo de contexto

| Signal | Semântica |
|---|---|
| `repositories` | Lista real de `GET /repositories` (fases `idle/loading/ready/error/offline` com `repositoriesError`) |
| `activeRepository` | Seleção explícita; no bootstrap restaura o persistido (validado contra a lista) e cai explicitamente no primeiro repo quando stale |
| `snapshots` / `snapshotsPhase` | Snapshots do repo ativo (`GET /repositories/{id}/snapshots`), recarregados em background a cada troca |
| `activeSnapshot` | Pin **nullable** — `null` = "default do servidor" (latest READY). Nunca snapshot fabricado |
| `selectedSymbol` | Nó `occ:{id}` selecionado no grafo (compartilhado com inspector/links) |
| `notice` | Mensagem explícita quando estado persistido/deep-link não pôde ser honrado — string vazia = sem aviso; nunca mensagem adivinhada |

Regras de consistência verificadas no código:

- Trocar de repositório limpa `selectedSymbol` e o pin de snapshot
  (snapshot pertence a um repo só — pin cruzado é sempre inválido).
- `selectSnapshot(null)` volta ao default do servidor; `selectSnapshot`
  sem repo ativo é ignorado.
- Respostas de snapshot que chegam após troca de repo são descartadas
  (guard `activeRepository()?.id !== repo.id`).

## Persistência — mínima e honesta

`localStorage['atlas-explorer-context']` guarda **apenas**
`{repoIdentity: identity_key, snapshotId: number|null}` —
identificadores, não dados. Falhas de storage (modo privado/quota)
deixam o contexto em memória sem ruído. Na restauração:

- repo salvo sumiu do índice → `notice` "O repositório salvo não está
  mais indexado; usando o primeiro da lista." + contexto cai para
  `items[0]` declarado;
- lista vazia → `notice` similar e **nenhum** repo ativo;
- snapshot salvo não existe mais → `notice` "...não existe mais; usando
  o mais recente.";
- snapshot salvo existe mas não está READY → `notice` "...está
  FAILED/STALE; usando o mais recente.";
- falha ao validar snapshots → `notice` "Não foi possível validar o
  snapshot salvo..." (não alega que o snapshot sumiu).

## Deep links — `?repo=&snapshot=`

`ShellComponent` chama `ctx.syncFromQueryParams()` no bootstrap e a cada
`NavigationEnd` — o param é aplicado **onde quer que o usuário chegue**:

- `?repo=` casa por `root_handle`, `identity_key` **ou** id numérico —
  `resolveRepository(needle)`.
- `?snapshot=` deve ser inteiro positivo existente no repo ativo e
  READY; inválido/inexistente/não-READY → `notice` específico
  (`"?snapshot= inválido"`, "...não existe neste repositório.",
  "...está {status}; usando o mais recente.").
- `?repo=` irresolúvel → `notice` e `?snapshot=` é ignorado (sem repo,
  o param não significa nada).

Helpers para produzir links: `contextQueryParams()` (par atual) e
`deepLink(path, extra)` (par + extras) — usados no control center e nos
links de foco do grafo.

## Formulários que restauram de URL (além do contexto global)

| Tela | Params restaurados/escritos |
|---|---|
| `/search` | `q` (auto-executa), `strategy`, `language`, `kind`, `path`, `repo`→`repository_root`, `limit` — typing sincroniza a URL |
| `/impact` | `repo`, `base`, `target`, `base_snapshot`, `target_snapshot`, `symbol`, `file`, `possible=0`, `known=1` |
| `/graph` | `focus` = `occ:N` ou `N` (deep-link de símbolo) |
| `/repositories` | produz links `?repo=&snapshot=` |
| `/context` | produz links `?q=` para `/search` via expansion refs/chips |

## Selo do toolbar

`shell.component.html`: `<select>` funcional de repositório (estados
loading/idle/offline/error/vazio honestos), selo
`snapshot #N` quando há pin ou `snapshot: mais recente` quando default,
e faixa `role="status"` para `notice` com botão de dismiss.
Sidebar colapsável persiste em `localStorage['atlas-sidebar-collapsed']`;
breadcrumbs por segmento de URL (`/symbols/:id` → "ocorrência #N").

## Verificação

- Unit: `explorer-context.service.spec.ts` (persistência, restauração,
  notices, deep-link params), `shell.component.spec.ts`.
- E2E real: `flows2.spec.ts` testes **12** (`?repo=&snapshot=` resolve;
  repo bogus → notice), **13** (`/search?q=` auto-executa), **14**
  (typing sincroniza URL; links inspector/grafo navegam), **15**
  (`/symbols/:id` restaura contexto; id malformado degrada), **23**
  (sidebar colapsa e persiste após reload).
