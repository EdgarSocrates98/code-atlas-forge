# Cycle 6 — Revisão de UX por tela

> Checklist de aceite visual/UX do Explorer. Complementa
> `explorer-product-spec.md` (o quê/por quê) e `explorer-ui-ux.md`
> (como). Fonte: spec lf-0084.
>
> **Como usar:** ao fechar cada spec de implementação (lf-0087…lf-0093),
> rodar os checklists das telas tocadas + a seção transversal. Marcar
> ✅ aprovado · ⚠ aprovado com ressalva registrada · ❌ bloqueante.
>
> **Bloqueantes (qualquer ❌ = não aprova):**
> - dado simulado/mock apresentado como real;
> - estado de evidência comunicado só por cor;
> - `partial-analysis` indistinguível de resultado completo;
> - chamada de rede para host não-loopback ou qualquer telemetria;
> - ação exclusiva de mouse sem equivalente de teclado;
> - renderização além do orçamento (nós/arestas/linhas) sem declaração
>   de truncamento.

---

## 0. Checklist transversal (todas as telas)

### Dados e honestidade
- [ ] Todo dado visível veio de endpoint real de `/api/v1` (ou é claramente marcado como estado/placeholder)
- [ ] `snapshot_id` consultado está visível na tela ou no selo do toolbar
- [ ] `truncated=true` da API sempre vira aviso visível + ação de refinar
- [ ] `diagnostics[]`/`codes[]` de qualquer envelope aparecem na UI (nunca engolidos)
- [ ] Ausência de dado (`null`, `[]`) é distinguível de "dado indisponível" (erro/indisponibilidade)
- [ ] Erros tipados mapeiam para estados do catálogo (§5 do ui-ux), não para genérico

### Evidência e incerteza
- [ ] Toda aresta mostra `resolution_status` com glifo + rótulo pt-BR literal (§6.2)
- [ ] Todo finding mostra `strength` com glifo + rótulo
- [ ] `resolver` de origem acessível (tooltip ou inspector)
- [ ] `to_unresolved`/`external` renderizam como alvo-fantasma, não somem
- [ ] Nenhum `ambiguous`/`possible`/`unknown` visualmente promovido a verificado

### Acessibilidade
- [ ] Foco visível em todo elemento interativo (`--atlas-color-focus-ring`)
- [ ] Ordem de tabulação = ordem visual; `Esc` fecha e devolve foco
- [ ] `aria-live` para carregado/copiado; `role="alert"` para erros
- [ ] Alvos ≥24×24 px; texto ≥4.5:1 nos dois temas
- [ ] `prefers-reduced-motion`: sem animação não essencial (pan/zoom vira salto)
- [ ] Testado nos 3 temas (system claro / system escuro / forçado claro / forçado escuro)

### Local-first
- [ ] Zero requests fora de `127.0.0.1`/`::1` (DevTools Network aberto durante a revisão)
- [ ] Nenhuma fonte web, CDN, tracking pixel, beacon
- [ ] Nenhuma escrita além de `localStorage` de preferência

---

## 1. `/dashboard` — Painel

- [ ] Contagens por repo vindas de `GET /index/status` (`file_inventory`, `symbol_occurrences`, `relations`, `evidence`)
- [ ] `latest_ready` + `commit_sha`/`git_ref` do snapshot exibidos
- [ ] `GET /capabilities` refletido: `strategies` disponíveis, `vector` marcado indisponível quando `false`, `readonly: true` declarado
- [ ] Estado `no-index` quando `latest_ready=null` com instrução `code-atlas index`
- [ ] Estado `empty` quando `items: []` em `/repositories`
- [ ] Links de entrada para Grafo/Busca/Impacto/Diagnósticos
- [ ] `loading` com skeleton; `api-disconnected` com comando `code-atlas explorer`

## 2. `/repositories` — Repositórios

- [ ] Lista de `GET /repositories` (`root_handle`, `id`) — `identity_key` disponível no detalhe
- [ ] Por repo: `ready_snapshots`, `latest_ready`, contagens (de `/index/status`)
- [ ] Seleção grava repo ativo e reflete no seletor do toolbar (todas as telas passam a consultar com `repository_id`/`repository_root` dele)
- [ ] Persistência da seleção entre rotas na sessão; ausência de seleção → `no-repo-selected`
- [ ] **Item na navegação lateral presente** (hoje ausente em `NAV_ITEMS`)
- [ ] Estados: `loading`, `api-disconnected`, `empty`

## 3. `/graph` — Grafo

- [ ] Nível macro vem de `GET /graph/overview` (agregado por arquivo: `path`, `symbols`, `language`) — nunca grafo integral
- [ ] `truncated`/`total_files` exibidos quando limitado (`too-many-nodes`)
- [ ] Vizinhança via `GET /graph/neighbors` (`occurrence_id`, `direction`, `limit` ≤200)
- [ ] Interações: click=selecionar · **dblclick=expandir** · botão direito=menu (pin/isolar/expand in/out/abrir símbolo/copiar qname/copiar evidência)
- [ ] Pin (`📌` + `aria-pressed`), Isolate com banner "N nós ocultos — desfazer"
- [ ] Zoom ∈ [0.1×, 4×] com indicador; pan limitado ao bounding box; **câmera não desaparece** (gate C6)
- [ ] Reset de câmera (`0` / botão); legenda sempre acessível (`?`)
- [ ] Equivalente de teclado: setas entre nós, `Enter` expande, lista "vizinhos de X" com mesma informação
- [ ] ≤500 nós simultâneos; ao exceder → `too-many-nodes`
- [ ] Nó sem arestas → "Sem relações registradas neste snapshot" (≠ erro ≠ loading)
- [ ] Badges de `resolution_status` nas arestas; tipo de aresta (`CALLS`/`IMPORTS`/…) distinguível por estilo + legenda

## 4. `/search` — Busca

- [ ] `q` obrigatório (≤300) — validação antes do request
- [ ] Filtros `strategy`/`language`/`kind`/`path` respeitam `GET /capabilities` (`vector` desabilitado quando indisponível, com razão)
- [ ] Hits mostram `qualified_name`, `kind`, `language`, `path`, `score`, `strategy`, `matched_fields`
- [ ] `evidence` (`occ:{id}`) copiável por hit
- [ ] Paginação por `next_cursor` explícita ("Carregar mais"); `truncated` declarado
- [ ] `diagnostics` da busca exibidos (ex.: `vector-unavailable-readonly`, cursor inválido)
- [ ] Estados: `no-results` com sugestões reais, `loading` sem flicker, `api-disconnected`, `no-index` quando sem snapshot
- [ ] Click no hit → `/symbols/{occurrence_id}`; URL reflete `q`+filtros (deep-link)

## 5. `/symbols/:id` — Símbolo

- [ ] `:id` é `occurrence_id` numérico; aceita `occ:N` com strip; inválido → `bad-param` tratado
- [ ] Detalhe de `GET /symbols/{id}`: `qname`, `kind`, `language`, `path`, `span`, `parse_status`, `relations_summary` (type × status × n)
- [ ] `parse_status` `parsed-with-errors`/`parse-failed` rotulado (nunca escondido)
- [ ] Vizinhança `in`/`out`/`both` navegável (lista + atalho para grafo focado)
- [ ] Evidência: `GET /evidence/{id}` → `extractor`, `extractor_version`, `path`, `span`, `content_hash`, `notes`, `referencing_relations`
- [ ] `evidence-missing` (404) com explicação + alternativa; `symbol-not-found` sugere outro snapshot
- [ ] Copiar `qname` e `occ:{id}` com confirmação `aria-live`
- [ ] Deep-link `/symbols/:id` restaura contexto (repo+snapshot)

## 6. `/impact` — Impacto

- [ ] Formulário cobre `repository_root`, `base_revision`, `target_revision`, `symbol`/`file`, `include_possible`, `known_only` (limites do backend: 1000/200/200/300/1000 chars)
- [ ] **Binding verificado antes dos números:** diagnósticos `partial-analysis`, `revision-binding-mismatch`, `dirty-worktree`, `semantic-blocked`, `snapshot-*`, `revision-*` → estados §5.10/§5.11
- [ ] `change_set.files` com `kind` (`added`/`modified`/`deleted`/`renamed`/`copied`/`binary`/`unavailable`) + `similarity` em renomes + `dirty_worktree`/`commit_ids`
- [ ] `symbol_changes` com `change` (9 kinds) + `strength` + `old/new_signature` + `old_path→new_path`
- [ ] `contract_changes` com `classification` + `compatibility` (4 valores) + `strength`
- [ ] `impacts` com `impact` (direct/transitive/possible/unknown) + `strength` + `path{hops, intermediate_keys, resolution_statuses, fully_verified}` + `consumer_state` (`still_present`/`also_removed`/`unresolved_in_target`)
- [ ] `test_candidates` com `relationship` + `revision: base|target` + `reason`; lista vazia → frase "ausência não é evidência de segurança"
- [ ] `risk.category` (5 valores) **ao lado de** `risk.uncertainty` + `reasons`; `unknown_coverage[]` visível
- [ ] `truncated` declarado; export JSON local do `ImpactResult`
- [ ] **Teste de 3 segundos:** resultado com `partial-analysis` não parece verificado (revisão cega)

## 7. `/revisions` — Revisões

- [ ] Lista de `GET /repositories/{id}/snapshots` com `id`, `status`, `commit_sha`, `git_ref`, `created_at` (ordenado `id DESC`, ≤100)
- [ ] `status` rotulado: BUILDING/READY/FAILED/STALE — FAILED/STALE visíveis, não escondidos
- [ ] Marcação base+alvo → envia para `/impact` pré-preenchido (`base_snapshot_id`/`target_snapshot_id` ou revisions)
- [ ] Seleção de snapshot consultável para as demais telas (override do `latest_ready`)
- [ ] **Item na navegação lateral presente** (hoje ausente)
- [ ] Estados: `empty` (sem snapshots), `snapshot-unavailable` para não-READY, `loading`, `api-disconnected`

## 8. `/context` — Context Packs

- [ ] Formulário: `task` obrigatório (≤2000), `level` orientation/focus/deep, `budget_tokens` ≤64000, `max_symbols` ≤200
- [ ] `manifest` renderizado antes dos itens: `level`, `estimator` (`heuristic-chars4`), `tokens_estimated` (declarado como estimativa), `selected`, `omitted`, `truncated`, `freshness`, `ranking_explanation`, `tokens_payload`
- [ ] `items` ordenados por `priority` (0–6) com `kind`, `title`, `est_tokens`, `path`/`symbol_key`, `evidence`, flag `expandable`
- [ ] `budget` mostra `limit` − reservas = `available_items`
- [ ] `unresolved_refs[]` e `dependencies[]` (PackEvidence `kind`+`ref`) visíveis
- [ ] `diagnostics`: `insufficient_budget` + `suggested_minimum_budget` com re-run 1-clique; `mandatory_context_omitted` → alerta estrutural "pack incompleto"
- [ ] `expansion_refs` acionáveis (novo `POST /context` level+1) **[alvo-C6]**
- [ ] Copiar pack JSON/markdown; `task` vazia → `bad-param` tratado
- [ ] Nota honesta: tokens são estimados — UI nunca diz "exatos"

## 9. `/diagnostics` — Diagnósticos

- [ ] Agrega: `GET /index/status` + `GET /capabilities` + `lastError` do `GlobalErrorHandler` **[real]**
- [ ] Diagnósticos de engine coletados na sessão (busca/impacto/contexto) com código + mensagem + origem
- [ ] `vector-unavailable-readonly` explicado ("DB read-only por design — canal vetorial desativado"), não como falha
- [ ] Saúde de parsing: razão `symbol_occurrences`/`file_inventory`, `parse_status` por ocorrência linkável
- [ ] Nenhum "tudo certo" quando há snapshot FAILED/STALE recente — estado listado
- [ ] Cada diagnóstico clicável → tela de origem quando aplicável

## 10. `/settings` — Configurações

- [ ] Tema system/light/dark funcional **[real]** (`localStorage atlas-theme`, sem `data-theme` quando system)
- [ ] `GET /capabilities` → `api_version`, `readonly` exibidos
- [ ] Nota local-first/sem telemetria presente **[real]**
- [ ] Qualquer preferência nova persiste só localmente; nada é enviado
- [ ] Sem controles que prometam o que a API não faz (nenhum "salvar remoto")

---

## 11. Achados já registrados contra o scaffold atual

> Review do estado real (branch `cycle-6-angular-explorer`, commit
> `dac0e4e`). Itens abaixo são **fatos verificados no código** — viram
> entradas de backlog, não acusações:

| # | Achado | Onde | Ação proposta |
|---|---|---|---|
| F1 | Rotas `/repositories` e `/revisions` ausentes do `NAV_ITEMS` | `shell.component.ts` | incluir no menu (§1.2) |
| F2 | `ApiService` chama endpoints inexistentes (`/repositories/{id}/snapshot`, `/symbols/{id}/relations`, `/revisions/{id}/impact`, `/symbols/{id}/context-pack`) | `api.service.ts` | reescrever contra `explorer-api.md` em lf-0090/91 |
| F3 | Modelo `SnapshotStatus` minúsculo vs. backend `READY`/`FAILED`/`STALE`/`BUILDING` | `api.models.ts` × `enums.py` | corrigir modelo TS |
| F4 | `risk` no modelo TS = `'low'|'medium'|'high'|'unknown'` vs. `RiskAssessment{category: low|moderate|high|critical|unknown, uncertainty}` | `api.models.ts` × `impact/contracts.py` | corrigir modelo TS |
| F5 | `ResolutionStatus` TS = `verified|supported|possible|unresolved|external` vs. arestas `resolved|ambiguous|unresolved|external` | `api.models.ts` × `enums.py` | separar os dois vocabulários (§6.1) |
| F6 | `ContextPack` TS (`id/title/sections`) ≠ envelope real (`manifest/budget/items/…`) | `api.models.ts` × `context/contracts.py` | reescrever modelo em lf-0091 |
| F7 | `Repository` TS sem `identity_key` (API emite) | `api.models.ts` × `handlers.py` | ampliar modelo |
| F8 | `EvidenceStrength.unknown`/risco `unknown` sem slot de cor | `tokens.css` | rampa neutra + marcador `?` em lf-0085 |
| F9 | Probe de `/search` no placeholder usa `q=""` → `bad-param` garantido quando API está de pé (só detecta offline) | `search.component.ts` | probe real de `/health` ou tela inicial sem chamada |
| F10 | Badge de evidência ainda não existe (contrato de tokens apenas) | `explorer-design-system.md` gap declarado | `evidence-badge` em lf-0085 |
| F11 | Contraste AA não auditado por ferramenta | `explorer-design-system.md` gap declarado | axe/Lighthouse em lf-0093 |
| F12 | Seletor de repo no toolbar é somente-leitura | `shell.component.html` | seletor funcional (§1.2) |

---

## 12. Registro de revisão (template)

```
Tela:            /_________
Spec de impl.:   lf-____
Revisor:         __________  Data: ____
Ambiente:        branch ______ · code-atlas explorer (porta ____)
Checklist:       ✅ __  ⚠ __  ❌ __
Bloqueantes encontrados: ____
Ressalvas registradas:   ____
Evidência (prints/notas): ____
Veredito:        APROVADO / APROVADO COM RESSALVAS / REPROVADO
```
