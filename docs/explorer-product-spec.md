# Code Atlas Explorer — Especificação de Produto (Cycle 6)

> Spec **lf-0084** · documentação apenas (nenhuma mudança de código).
> Fontes de verdade usadas: `README.md`, `docs/explorer-api.md`,
> `src/code_atlas/api/handlers.py`, `src/code_atlas/api/server.py`,
> `src/code_atlas/cli.py` (`code-atlas explorer`, `code-atlas api serve`),
> `src/code_atlas/impact/contracts.py`, `src/code_atlas/context/contracts.py`,
> `src/code_atlas/search/contracts.py`, `src/code_atlas/contracts/enums.py`,
> `src/code_atlas/mcp.py`, `explorer/src/app/**`,
> `docs/explorer-frontend-architecture.md`, `docs/explorer-design-system.md`,
> `docs/cycle-5.1-impact-integrity.md`, `docs/cycle-6-baseline.md`.
>
> Convenção de marcação deste documento:
> - **[real]** — existe e está verificado no código/contrato hoje.
> - **[alvo-C6]** — especificado aqui; a ser construído nas specs lf-0087…lf-0093.
> - **[roadmap]** — fora do escopo do Cycle 6; listado como seam deliberado.

---

## 0. Status e escopo

O Code Atlas Explorer é a interface visual **local-first** do Code Atlas.
Hoje (branch `cycle-6-angular-explorer`) existem: a API HTTP local
`/api/v1` completa e verificada (lf-0086), a fundação Angular 18 com 10
rotas lazy e shell acessível (lf-0083), e **todas as telas de feature
ainda são placeholders** (`feature-placeholder`) que apenas sondam a API.
Este documento especifica **o que as telas devem se tornar**, usando
somente dados que a API real já fornece.

Não-escopo deste documento: nenhuma decisão de produto nova é inventada.
Onde o backend não fornece o dado, o item é marcado **[roadmap]** — nunca
apresentado como se existisse.

## 1. O produto em uma página

**O que é:** um workbench visual para inspecionar o grafo tipado,
verificar evidências e medir impacto de mudanças em repositórios indexados
pelo Code Atlas — tudo servido por `code-atlas explorer` em
`http://127.0.0.1:8741/` (API em `/api/v1`, bind exclusivamente loopback).

**O que não é:** não é um editor, não é um IDE, não é um serviço
compartilhado, não é um coletor de dados. A API é read-only
(`PRAGMA query_only`, `mode=ro`) e o Explorer **nunca** escreve no banco,
no repositório indexado ou em qualquer lugar fora do perfil do navegador
(`localStorage` para preferência de tema, apenas).

**Princípios do produto (derivados do README + contratos):**

1. **Local-first, offline-first.** Nenhuma chamada a host não-loopback;
   nenhum CDN, fonte web ou asset externo em runtime.
2. **Sem telemetria — por ausência, não por opção.** Não existe código de
   telemetria; este spec proíbe introduzi-lo (analytics, crash reporters,
   contadores de uso, fingerprinting).
3. **Evidência primeiro.** Toda relação exibida carrega
   `resolution_status` + `resolver`; todo achado de impacto carrega
   `strength`. A UI nunca esconde esses campos.
4. **Incerteza honesta.** Estados `unresolved`, `ambiguous`, `external`,
   `possible`, `unknown` são fatos exibidos como tais — nunca promovidos
   visualmente, nunca preenchidos por heurística, nunca ocultados por
   default.
5. **Análise parcial nunca parece verificada.** Qualquer resultado com
   diagnóstico `partial-analysis`, binding `partial`/`mismatch` ou
   `truncated=true` é apresentado com rótulo explícito e estruturalmente
   distinto de um resultado completo (ver `explorer-ui-ux.md` §5.10).
6. **Renderização limitada (bounded).** A API já é bounded
   (`/graph/overview` agrega por arquivo com `limit` ≤ 2000;
   `/graph/neighbors` limit ≤ 200). A UI obedece orçamentos próprios e
   declara truncamento — nunca renderiza o grafo inteiro.
7. **Determinismo visível.** Snapshot sempre identificado (`snapshot_id`,
   `commit_sha`, `git_ref`); a mesma consulta sobre o mesmo snapshot
   mostra o mesmo resultado.

## 2. Estado atual vs. alvo do ciclo

| Rota | Título de rota | Estado atual **[real]** | Alvo Cycle 6 **[alvo-C6]** | Spec implementadora |
|---|---|---|---|---|
| `/dashboard` | Painel | placeholder + probe `GET /repositories` | visão do repo: snapshot READY, contagens de `/index/status`, capabilities | lf-0087/92 |
| `/repositories` | Repositórios | placeholder + probe | lista de `/repositories` + `/index/status`, seletor de repo ativo | lf-0087 |
| `/graph` | Grafo | placeholder + probe | overview agregado por arquivo + canvas de vizinhança bounded | lf-0088/89 |
| `/search` | Busca | placeholder + probe `q=""` (falha `bad-param`) | busca híbrida com filtros, cursor, diagnósticos | lf-0090 |
| `/symbols/:id` | Símbolo | placeholder estático | detalhe de ocorrência + resumo de relações + evidência | lf-0089/90 |
| `/impact` | Impacto | placeholder (sem probe) | formulário ImpactRequest → resultado completo do engine | lf-0090/91 |
| `/revisions` | Revisões | placeholder estático | snapshots do repo (`/repositories/{id}/snapshots`) + seleção base/alvo | lf-0091 |
| `/context` | Context Packs | placeholder (sem probe) | construtor de `POST /context` + inspetor de pack | lf-0091 |
| `/diagnostics` | Diagnósticos | placeholder + banner `lastError` | central: diagnósticos de engine + saúde de índice + último erro | lf-0092 |
| `/settings` | Configurações | **funcional** — temas system/light/dark | + preferências de densidade/movimento **[alvo-C6]** | lf-0085/93 |

> **Achado de IA (registrado para revisão):** `/repositories` e
> `/revisions` existem como rotas mas **não estão no menu lateral**
> (`NAV_ITEMS` em `shell.component.ts` tem 7 itens). O alvo C6 inclui
> ambas na navegação — ver `explorer-ui-ux.md` §1.

## 3. Personas

### P1 — Desenvolvedor(a) de feature ("a Dev")
- **Contexto:** vai tocar código que não conhece; tem o repo indexado
  localmente e o Explorer aberto ao lado do editor.
- **Objetivo:** entender onde uma função/símbolo vive, quem a chama e o
  que pode quebrar ao modificá-la.
- **Telas primárias:** `/search`, `/symbols/:id`, `/graph`.
- **Dados-chave:** `SearchHit` (qname, path, span, `evidence`),
  `relations_summary` por tipo/status, vizinhos `in`/`out`.
- **Sucesso:** acha a definição e os chamadores verificados em < 2 min
  sem rodar `grep` cego.
- **Frustração a evitar:** grafo que trava com repo grande → exige
  bounded rendering e estado `too-many-nodes`.

### P2 — Tech lead ("a Lead")
- **Contexto:** prepara sprint/refinamento; precisa dimensionar uma
  mudança arquitetural proposta.
- **Objetivo:** medir blast radius e risco antes de aprovar escopo.
- **Telas primárias:** `/impact`, `/revisions`, `/dashboard`.
- **Dados-chave:** `ImpactResult.symbol_changes`, `impacts` (com
  `ImpactKind` + `strength` + `path.hops`), `risk.category`,
  `risk.uncertainty`, `unknown_coverage`.
- **Sucesso:** responde "o que quebra se eu mudar X?" com lista
  auditável e cadeia de propagação (`ImpactPath`), não com palpite.
- **Frustração a evitar:** número de risco sem explicação → a UI exibe
  `risk.reasons` e separa `category` de `uncertainty`.

### P3 — Arquiteto(a)
- **Contexto:** revisa estrutura de módulos, dependências entre camadas,
  fronteiras do sistema.
- **Objetivo:** ver o grafo macro (arquivos × símbolos) e mergulhar em
  dependências suspeitas.
- **Telas primárias:** `/graph` (overview), `/symbols/:id`, `/diagnostics`.
- **Dados-chave:** `/graph/overview` (arquivos ordenados por nº de
  símbolos, `language`), arestas por `type` (`IMPORTS`, `CALLS`,
  `EXTENDS`…) e `resolver` (syntactic/scip/lsp).
- **Sucesso:** identifica dependência cruzada indevida com a aresta e a
  evidência que a sustenta.
- **Frustração a evitar:** aresta que parece certeza mas é `ambiguous` →
  evidência visual obrigatória (badge com marcador + rótulo, nunca só cor).

### P4 — Revisor(a) de PR
- **Contexto:** recebeu um PR; rodou `code-atlas index` nos dois pontos
  (ou usa worktree) e quer revisar por impacto, não por diff linha-a-linha.
- **Objetivo:** lista de mudanças de símbolo → risco de contrato → testes
  que deveriam rodar.
- **Telas primárias:** `/revisions`, `/impact`, `/symbols/:id`.
- **Dados-chave:** `change_set.files` (kinds `added`/`modified`/`deleted`/
  `renamed`/`copied`/`binary`/`unavailable`), `symbol_changes` (com
  `SymbolChangeKind` + `strength`), `contract_changes` (com
  `compatibility`), `test_candidates` (com `relationship` +
  `revision: base|target`), `diagnostics` do binding.
- **Sucesso:** revisão cobre 100% dos `contract_changes` de
  `compatibility=potential-risk` e os testes `direct` listados — e a
  revisão **declara** quando a análise foi parcial.
- **Frustração a evitar:** confiar em análise rodada sobre snapshot
  divergente → estado `partial-analysis`/`revision-binding-mismatch`
  sempre visível (MANDATORY).

### P5 — Engenheiro(a) de dados / plataforma ("Data/Platform")
- **Contexto:** mantém pipelines e repos poliglotas (Python/SQL/dbt/Terraform
  — adapters de domínio são Cycle 7, **[roadmap]**).
- **Objetivo hoje (C6):** auditar cobertura do índice: quantos arquivos
  entraram só no inventário, quantos com `parsed-with-errors`,
  `parse-failed`, `unsupported-language`.
- **Telas primárias:** `/diagnostics`, `/dashboard`, `/repositories`.
- **Dados-chave:** `/index/status` (contagens `file_inventory`,
  `symbol_occurrences`, `relations`, `evidence`), `parse_status` por
  ocorrência, diagnósticos de parser (`file-too-large`,
  `unsupported-language`, timeout).
- **Sucesso:** responde "o índice enxerga o quê, e o que ele *não*
  enxerga" sem abrir o SQLite.
- **Frustração a evitar:** ausência de dados apresentada como zero real →
  distinção entre "sem dados" e "dado indisponível".

### P6 — Agente de IA (via ferramenta hospedeira)
- **Contexto:** não usa a UI diretamente — consome o Code Atlas pelo
  servidor MCP stdio (`code-atlas mcp`, 11 tools `atlas_*`, read-only,
  homologado). O Explorer é a **janela humana de auditoria** do que o
  agente recebe.
- **Objetivo (da persona humana que opera o agente):** inspecionar o
  ContextPack que será entregue ao agente — itens, orçamento, o que foi
  omitido, o que ficou `unresolved`.
- **Telas primárias:** `/context`, `/symbols/:id`, `/diagnostics`.
- **Dados-chave:** `ContextPack` — `manifest` (level, estimator,
  `tokens_estimated`, `truncated`, `ranking_explanation`), `budget`
  (`limit`, reservas, `available_items`), `items` (kind, priority,
  `est_tokens`, `evidence`, `expandable`), `unresolved_refs`,
  `expansion_refs`, `diagnostics` (`insufficient_budget`,
  `mandatory_context_omitted`, `suggested_minimum_budget`).
- **Sucesso:** o humano valida que o pack não omite contexto obrigatório
  antes de confiar no agente; consegue copiar o pack como JSON/markdown.
- **Frustração a evitar:** pack silenciosamente truncado → `truncated`
  e `omitted` sempre visíveis; `insufficient_budget` com o valor
  `suggested_minimum_budget` exibido.

### P7 — Explorador(a) de código desconhecido ("Newcomer")
- **Contexto:** primeiro dia no time; nunca viu o repo.
- **Objetivo:** orientação de alto nível — "por onde começo, o que é
  grande, o que é central".
- **Telas primárias:** `/dashboard`, `/graph` (overview), `/context`
  (level `orientation`), `/search`.
- **Dados-chave:** `/index/status` (escala do repo), `/graph/overview`
  (arquivos com mais símbolos), ContextPack `level=orientation`.
- **Sucesso:** em 15 min tem mapa mental: maiores arquivos, símbolos
  centrais (mais referenciados), e um pack de orientação copiável.
- **Frustração a evitar:** tela vazia sem próximo passo → todo estado
  vazio oferece ação (buscar, selecionar repo, ver diagnósticos).

## 4. Jornadas

> Cada jornada lista passos, **telas reais** e **dados reais da API**
> consumidos. Endpoints citados existem em
> `src/code_atlas/api/handlers.py`; campos citados existem nos contratos.

### Jornada A — Explorar Repositório (primeiro contato)

**Persona:** P7 (também porta de entrada de P1–P5).
**Pré-condição:** `code-atlas index <repo>` já rodou; `code-atlas explorer --open`.

| # | Passo | Tela | API/dados |
|---|---|---|---|
| A1 | Abrir Explorer → redirect `'' → /dashboard` | `/dashboard` | `GET /index/status` → por repo: `ready_snapshots`, `latest_ready`, `counts{file_inventory, symbol_occurrences, relations, evidence}`; `GET /repositories` → `root_handle` no seletor do toolbar |
| A2 | Confirmar repo ativo (toolbar) e saúde do índice | `/dashboard` | `latest_ready != null` = há snapshot consultável; `counts.relations / counts.symbol_occurrences` = densidade do grafo |
| A3 | Abrir visão macro | `/graph` | `GET /graph/overview?repository_id=&limit=500` → `items[{path, symbols, language}]`, `total_files`, `truncated` |
| A4 | Ordenar/filtrar arquivos por nº de símbolos; escolher arquivo denso | `/graph` | mesma resposta; `truncated=true` → estado `too-many-nodes` declarado |
| A5 | Detalhar um símbolo do arquivo | `/symbols/:id` | `GET /symbols/{occurrence_id}` → `qname`, `kind`, `language`, `path`, `span`, `parse_status`, `relations_summary[{type, resolution_status, n}]` |
| A6 | Ver vizinhança (quem importa/chama) | `/symbols/:id` ou `/graph` | `GET /graph/neighbors?occurrence_id=&direction=both&limit=50` → `edges[{id,from_id,to_id,type,resolution_status,resolver}]`, `nodes[…]`, `truncated` |
| A7 | (Opcional) pedir pack de orientação | `/context` | `POST /context {task:"orientação do repositório", level:"orientation", budget_tokens:4000}` → itens + manifest |

**Saída esperada:** mapa mental + pelo menos um `symbol_id`/`occ:N`
anotado. **Estados alternativos:** sem snapshot READY → `no-index`
(`GET /index/status` com `latest_ready=null`; a API devolve
`snapshot-not-ready` 409 nas consultas); zero arquivos → `empty` com
instrução de indexação.

### Jornada B — Entender uma Dependência

**Persona:** P1/P3. **Pergunta:** "de onde vem `pkg.util.helper` e quem
depende dele?"

| # | Passo | Tela | API/dados |
|---|---|---|---|
| B1 | Buscar nome qualificado | `/search` | `GET /search?q=helper&strategy=hybrid&limit=20` → `items[SearchHit{occurrence_id, qualified_name, kind, path, span, score, strategy, matched_fields, evidence}]`, `ranking_version`, `truncated`, `next_cursor`, `diagnostics` |
| B2 | Escolher o hit correto (kind/path/language como discriminadores) | `/search` | campos do hit; `evidence` = refs `occ:{id}` canônicas |
| B3 | Abrir detalhe | `/symbols/{occurrence_id}` | `GET /symbols/{id}` → `relations_summary` agrupado por `(type, resolution_status)` — ex.: `CALLS×resolved×7`, `CALLS×unresolved×2` |
| B4 | Listar chamadores | `/symbols/:id` | `GET /graph/neighbors?occurrence_id=&direction=in&limit=50&type-filter=CALLS` (filtro client-side sobre `edges` — a API não filtra por tipo **[alvo-C6: seam]**) |
| B5 | Para cada aresta suspeita, abrir a evidência | `/symbols/:id` | `GET /evidence/{id}` → `{extractor, extractor_version, notes, path, span, content_hash}` + `referencing_relations` |
| B6 | Copiar referência canônica | qualquer | clipboard: `occ:{occurrence_id}` / qname — ver `explorer-ui-ux.md` §4.4 |
| B7 | Aresta `unresolved`/`ambiguous`? Registrar como fato | `/symbols/:id` | `to_unresolved`/status — a UI rotula, não explica além do `resolver` de origem |

**Saída esperada:** lista de dependências **com** status de resolução —
a Dev sabe quais chamadores são `resolved` e quais são `unresolved`/`external`.
**Estados alternativos:** busca sem hits → `no-results` com sugestão de
filtros; símbolo de outro snapshot → `symbol-not-found` 404 → estado
`evidence-missing`-análogo com link para re-selecionar snapshot.

### Jornada C — Analisar Mudança (duas revisões → impacto → blast → testes)

**Persona:** P2/P4. **Pergunta:** "o que o diff entre `main` e este branch
pode quebrar?"

| # | Passo | Tela | API/dados |
|---|---|---|---|
| C1 | Abrir revisões do repo | `/revisions` | `GET /repositories/{id}/snapshots` → `items[{id,status,commit_sha,git_ref,created_at}]` (≤100, `id DESC`) |
| C2 | Selecionar **base** e **alvo** — snapshots READY ou expressões git | `/revisions` → `/impact` | `snapshot_id` numérico **ou** `base_revision`/`target_revision` (`HEAD~1`, sha, `snapshot:N`); snapshot FAILED visível mas não selecionável (API rejeita com `snapshot-failed`) |
| C3 | Rodar análise | `/impact` | `POST /impact/analyze {repository_root, base_revision, target_revision, include_possible:true}` → `ImpactResult` completo |
| C4 | Ler veredito do binding **antes** dos números | `/impact` | `diagnostics[]`: `revision-binding-mismatch`, `dirty-worktree`, `partial-analysis`, `snapshot-for-revision-missing` etc.; binding `exact`/`content_verified`/`partial`/`mismatch`/`unavailable` (estado interno do engine, exposto via diagnósticos) |
| C5 | Percorrer `symbol_changes` | `/impact` | `{symbol_key, qualified_name, change (added/removed/renamed/moved/signature_changed/body_changed/doc_changed), strength, old_signature, new_signature, old_path→new_path}` |
| C6 | Avaliar contratos | `/impact` | `contract_changes[{classification, compatibility: potential-risk|likely-compatible|no-contract-impact|indeterminate, strength}]` |
| C7 | Expandir **blast radius** | `/impact` (+ navegação para grafo **[alvo-C6]**) | `impacts[{impact: direct|transitive|possible|unknown, strength, path{origin→affected, hops, intermediate_keys, resolution_statuses, fully_verified}, consumer_state}]` |
| C8 | Listar testes | `/impact` | `test_candidates[{test_file, test_symbol, relationship: direct|transitive|heuristic|unknown, revision: base|target, strength, reason}]` |
| C9 | Ler risco | `/impact` | `risk{category: low|moderate|high|critical|unknown, uncertainty: low|moderate|high, reasons[], formula_version}` + `unknown_coverage[]` + `truncated` |
| C10 | Copiar relatório | `/impact` | exportar JSON do `ImpactResult` (clipboard/download local — nada sai da máquina) |

**Saída esperada:** veredito de risco com razões, lista auditável de
impactados (cada um com `strength` e cadeia `path`), e conjunto de testes
candidatos. **Regra inviolável:** se `diagnostics` contém
`partial-analysis` ou `revision-binding-mismatch`, o resultado inteiro
carrega o rótulo de **Análise parcial** (ver `explorer-ui-ux.md` §5.10) —
`risk` e listas nunca são exibidos como se completos.
**Estados alternativos:** revisão não resolvida → `invalid-revision` /
`revision-unresolved`; commit sem snapshot → `snapshot-for-revision-missing`
(com comando de index sugerido pelo backend); repo não indexado →
`repo-not-indexed` 404.

### Jornada D — Debug ("por que o Atlas não vê meu código?")

**Persona:** P5/P1. **Sintoma:** símbolo esperado não aparece na busca.

| # | Passo | Tela | API/dados |
|---|---|---|---|
| D1 | Abrir Diagnósticos | `/diagnostics` | `GET /index/status` → contagens por repo; banner de `GlobalErrorHandler.lastError` **[real]**; `GET /capabilities` → `search{strategies, fts5, vector, detail}` |
| D2 | Conferir snapshot efetivo | `/diagnostics` ou `/repositories` | `latest_ready` por repo; `/repositories/{id}/snapshots` → snapshots `FAILED`/`STALE` listados com `created_at` |
| D3 | Checar cobertura de parsing | `/diagnostics` | `counts.file_inventory` vs `counts.symbol_occurrences` — razão baixa ⇒ muitos arquivos só-inventário (linguagem sem adapter) |
| D4 | Inspecionar ocorrência suspeita | `/symbols/:id` | `parse_status`: `parsed` \| `parsed-with-errors` \| `parse-failed` — exibido literalmente |
| D5 | Checar saúde do canal vetorial | `/diagnostics` | `capabilities.search.vector`; se DB read-only, `vector-unavailable-readonly` aparece em `SearchResult.diagnostics` |
| D6 | Verificar aresta sem alvo | `/symbols/:id` | `resolution_status=unresolved` + `resolver` de origem — "o resolver X não conseguiu" é a explicação máxima disponível |
| D7 | Se nada explica: abrir evidência da aresta | `/symbols/:id` | `GET /evidence/{id}` → `extractor`/`extractor_version`/`content_hash`/`notes` |

**Saída esperada:** causa classificada em uma de: linguagem sem adapter,
arquivo acima do limite (`file-too-large`), parse parcial, snapshot
não-READY, resolver opt-in ausente (SCIP/LSP), ou limite de resolução
sintática conhecido (ex.: `d.speak()` `unresolved` por design —
documentado no README). **A UI nunca promete resposta que o backend não
tem** — quando a causa é indeterminada, o estado diz exatamente isso.

### Jornada E — Contexto para Agente

**Persona:** P6 (humano operando agente) / P2.
**Pergunta:** "qual contexto mínimo e suficiente entrego ao agente para a
tarefa X?"

| # | Passo | Tela | API/dados |
|---|---|---|---|
| E1 | Abrir construtor de pack | `/context` | formulário: `task` (obrigatório, ≤2000), `level` (`orientation`/`focus`/`deep`), `budget_tokens` (≤64000), `max_symbols` (≤200), `repository_root`, `snapshot_id` opcional |
| E2 | Gerar pack | `/context` | `POST /context` → `ContextPack` (`manifest`, `budget`, `items`, `selected_symbols`, `relevant_files`, `dependencies`, `unresolved_refs`, `expansion_refs`, `diagnostics`) |
| E3 | Ler o manifest **antes** dos itens | `/context` | `manifest{level, estimator (heuristic-chars4), tokens_estimated:true, selected, omitted, truncated, freshness, ranking_explanation, tokens_payload}` |
| E4 | Inspecionar itens por prioridade | `/context` | `items[{item_id, kind: symbol|file|dependency|evidence|snippet, priority 0–6, title, body, est_tokens, symbol_key, path, evidence[], expandable}]` — ordenados por `priority` |
| E5 | Verificar omissões e diagnósticos | `/context` | `diagnostics{codes, messages, insufficient_budget, mandatory_context_omitted, suggested_minimum_budget}` + `unresolved_refs[]` |
| E6 | Expandir progressive disclosure | `/context` | `expansion_refs[{ref_id, kind, reason}]` → novo `POST /context` com level mais profundo (ou CLI `context-expand`) **[alvo-C6: botão "expandir"]** |
| E7 | Copiar para o agente | `/context` | pack serializado (JSON) ou render markdown — mesmo envelope `to_dict()`; token estimado declarado, nunca exato |
| E8 | (Trilha real do agente) | — | Agente consome `code-atlas mcp` (`atlas_context`, `atlas_expand`, `atlas_search`, `atlas_impact`…) — o Explorer é a auditoria humana desse fluxo |

**Saída esperada:** pack copiado com consciência do que ficou de fora
(`omitted`, `unresolved_refs`, `mandatory_context_omitted`).
**Estados alternativos:** `task` vazia → `bad-param`; orçamento
insuficiente → `insufficient_budget` + `suggested_minimum_budget`
exibidos (nunca pack "mudo"); snapshot não-READY → `snapshot-not-ready`.

## 5. Requisitos transversais do produto

| ID | Requisito | Fonte |
|---|---|---|
| R1 | Toda tela declara o snapshot consultado (`snapshot_id` + `commit_sha` quando presente) | contratos: toda resposta carrega `snapshot_id` |
| R2 | Toda aresta/finding mostra status/estrength com marcador + rótulo (nunca só cor) | `explorer-design-system.md` + WCAG 1.4.1 |
| R3 | Todo truncamento da API é espelhado na UI (`truncated`, `total_files`, `next_cursor`) | `handlers.py` |
| R4 | Todo `Diagnostic`/`SearchDiagnostic`/`PackDiagnostics` é visível em contexto — nunca engolido | contratos |
| R5 | Erros tipados da API (`bad-param`, `no-repository`, `snapshot-not-ready`…) viram estados específicos, não genéricos | `explorer-ui-ux.md` §5 |
| R6 | `prefers-reduced-motion` → zero animação não essencial (tokens já zeram durações) | `tokens.css` |
| R7 | WCAG 2.2 AA: foco visível, skip link, alvos ≥24px, contraste auditado | `explorer-design-system.md` gaps pendentes |
| R8 | Sem telemetria, sem rede externa, sem fontes web | README + arch doc |
| R9 | Seletor de repositório funcional no toolbar (hoje: somente leitura) | gap declarado em `explorer-frontend-architecture.md` §Gaps |
| R10 | Estados de snapshot `BUILDING`/`FAILED`/`STALE` exibidos com rótulo, não escondidos | `SnapshotStatus` enum |

## 6. Sucesso sem telemetria

Sem analytics, "sucesso" se mede por evidência verificável em revisão:
- **Cobertura de estados:** cada estado do catálogo
  (`explorer-ui-ux.md` §5) tem teste ou demonstração manual registrada.
- **Auditoria de honestidade:** nenhum teste/review encontra dado
  simulado apresentado como real.
- **WCAG 2.2 AA:** checagem axe/Lighthouse sem violações críticas
  (pendência já declarada no design-system doc — fecha em lf-0093).
- **Performance:** bundles dentro dos budgets `angular.json`;
  renderização de grafo bounded medida (gate Cycle 6 do roadmap:
  "sem câmera que desaparece").
- **Revisão humana:** `docs/cycle-6-ux-review.md` preenchido por tela.

## 7. Roadmap declarado (seams — não construir silenciosamente)

| Item | Por que não entra no C6 |
|---|---|
| `GET /impact/{id}` (histórico de análises) | backend só tem `POST` síncrono; exige storage de resultados — declarado em `explorer-api.md` NOT RUN |
| WebSocket/streaming de progresso de indexação | fora de escopo lf-0086 |
| Rate limiting por cliente | loopback single-user — adiado |
| Adapters de domínio (SQL/dbt/Terraform/Iceberg/Glue) e multi-repo | Cycle 7 |
| Embeddings semânticos reais (provider de produção) | provider naïve documentado (ADR-0022); decisão fora do ciclo |
| Endpoints que o scaffold `ApiService` já declara mas a API não tem (`/symbols/{id}/relations`, `/revisions/{id}/impact`, `/symbols/{id}/context-pack`, `/repositories/{id}/snapshot` singular) | divergência conhecida do scaffold lf-0083 — reconciliar em lf-0090/91 contra `explorer-api.md`; questão aberta §8.Q1 |
| Edição/criação via UI | API é read-only por design |
| Colaboração/multi-usuário | fora dos princípios local-first |

## 8. Questões em aberto

- **Q1 — Divergência ApiService × API.** O scaffold chama endpoints
  inexistentes (§7). Decisão pendente: reescrever `ApiService` contra o
  contrato real em lf-0090, mantendo a assinatura por feature.
- **Q2 — Vocabulário de evidência.** Arestas persistem
  `resolution_status ∈ {resolved, ambiguous, unresolved, external}`;
  findings carregam `strength ∈ {verified, supported, possible, unknown}`;
  os tokens de design nomeiam `verified|supported|possible|unresolved|external`.
  Mapeamento de exibição proposto em `explorer-ui-ux.md` §6 — precisa de
  confirmação antes de lf-0085 congelar o badge.
- **Q3 — `unknown`/`Desconhecido` sem token.** A paleta tem 5 slots e
  `EvidenceStrength.unknown`/`ImpactKind.unknown`/`risk unknown` não têm
  cor própria. Proposta: rampa neutra (`--atlas-color-text-muted` +
  marcador `?`) — a confirmar em lf-0085.
- **Q4 — Repositório ativo sem param.** `/search` aceita
  `repository_root`; demais endpoints usam `repository_id`. A UI deve
  carregar ambos do repo selecionado — confirmar em lf-0090.
- **Q5 — Modelo `ContextPack` do scaffold** (`id`/`title`/`sections`) não
  reflete o envelope real (`manifest`/`items`/…). Reescrever o modelo em
  lf-0091.
- **Q6 — `snapshot.status` casing.** Backend emite `READY`/`FAILED`/…
  (maiúsculo); modelo TS declara minúsculo. Corrigir o modelo TS.
- **Q7 — `risk` do modelo TS** (`'low'|'medium'|'high'|'unknown'`) não
  casa com `RiskCategory` (`low|moderate|high|critical|unknown` objeto
  `RiskAssessment`). Corrigir o modelo TS.

---
*Spec de produto — não substitui specs de implementação. Implementadores:
ler junto com `explorer-ui-ux.md` (estados/interações) e
`cycle-6-ux-review.md` (critério de aceite visual).*
