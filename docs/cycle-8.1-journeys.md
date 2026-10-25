# Cycle 8.1 — Product Journeys E2E (Phase 14, lf-0156)

Branch `cycle-8.1-ga-readiness`. Jornadas completas do produto, medidas de
ponta a ponta contra superfícies reais — sem mocks, sem chamadas HTTP
sintéticas em vez do fluxo, e **sem executar código/jobs/terraform/
pipelines dos repos indexados** (toda leitura passa pelo índice/API
read-only).

## Inventário

| # | Jornada | Superfície real | Arquivo |
|---|---|---|---|
| J1 | cold start → explore | SPA `/` → `/repositories` → "Abrir grafo" → nó → drill de ocorrências → inspetor de aresta com evidência | `explorer/e2e/journeys.spec.ts` |
| J2 | search → drill → impact | `/search?q=util` → "Focar no grafo" (`?focus=occ:9`) → expandir vizinhos → `Detalhar` arquivo → `/impact` blast | `explorer/e2e/journeys.spec.ts` |
| J3 | domain lineage | `/lineage` (server de domínios) → pick real no `<select>` → lanes upstream/downstream → tabela de arestas com badge de evidência + resolvedor | `explorer/e2e/journeys.spec.ts` |
| J4 | contracts | `/contracts` → serviço + endpoints (providers) → disclosure lazy de consumidores com fetch `?direction=in` real | `explorer/e2e/journeys.spec.ts` |
| J5 | federation | `/federation` → workspace `e2e-ws` auto-selecionado → membros → busca federada API → detalhe repo-scoped do membro B | `explorer/e2e/journeys.spec.ts` |
| J6 | context pack | `POST /api/v1/context` + `/context` UI — paridade item-a-item entre o payload e o que a tela renderiza | `explorer/e2e/journeys.spec.ts` |
| J7 | error honesty | `/symbols/999999` → 404 tipado `symbol-not-found`; `/symbols/not-a-number` → estado vazio cliente; `?snapshot=9999` → `.shell__notice` explícito | `explorer/e2e/journeys.spec.ts` |
| J8 | export | `GET /api/v1/graph/overview` (o payload que a tela consumiu) → download real `graph-export.json` no browser → conteúdo idêntico | `explorer/e2e/journeys.spec.ts` |
| B1 | CLI journey | `index → search → impact → context → status + doctor → honestidade` in-process (`run_cli`, mesmo padrão de `evaluation/run_impact_validation.py`) | `tests/e2e_journeys/test_cli_journey.py` |

## Como roda

Dois servidores **reais** (`code-atlas explorer` subprocess + API stdlib):

- compartilhado da suíte (`e2e/global-setup.ts`): indexa
  `tests/fixtures/sample_repo` num `CODE_ATLAS_DATA_DIR` temporário —
  J1, J2, J6, J7, J8;
- dedicado de domínios (`e2e/domain-fixture-server.ts`, bootado no
  `beforeAll`): indexa `sql_warehouse`/`terraform_infra`/`cicd_repo`/
  `api_repo`/`sample_repo` com `--domains` e cria o workspace de
  federação real `e2e-ws` (membros `sql_warehouse` + `sample_repo`) —
  J3, J4, J5.

Entidades consumidas pelas jornadas de domínio são **descobertas via API
viva** (raiz de linhagem com arestas nas duas lanes; primeiro
`api_endpoint`), nunca hard-coded — uma raiz que fica vazia seria uma
jornada vazia.

## Verificação — comandos verbatim

### Playwright

```
$ cd explorer && npx playwright test e2e/journeys.spec.ts
snapshot #1 READY — 5 inventory files
graph: 9 symbols, 9 occurrences, 4 relations (0 files failed)
secrets excluded: 1

Running 8 tests using 1 worker

  ✓  1 [chromium] › J1 — cold start → explore: app, repo pick, graph node, evidence (4.1s)
  ✓  2 [chromium] › J2 — search → drill → impact: symbol, file occurrences, neighbors, blast radius (6.9s)
  ✓  3 [chromium] › J6 — context pack: API pack for an entity matches the UI pack (3.1s)
  ✓  4 [chromium] › J7 — error honesty: nonexistent entity/snapshot → typed states, no crash (6.0s)
  ✓  5 [chromium] › J8 — export: graph JSON export carries the real rendered content (3.0s)
snapshot #1 READY — 4 inventory files   (sql_warehouse  → 36 entidades, 32 relações de domínio)
snapshot #2 READY — 13 inventory files  (terraform_infra → 24/45)
snapshot #3 READY — 5 inventory files   (cicd_repo → 13/34)
snapshot #4 READY — 12 inventory files  (api_repo → 30/43)
snapshot #5 READY — 5 inventory files   (sample_repo)
workspace: e2e-ws · repository_id: 1 (member 1) · repository_id: 5 (member 2)
  ✓  6 [chromium] › J3 — domain lineage: pick table, upstream/downstream, edge evidence (3.0s)
  ✓  7 [chromium] › J4 — contracts: expand service, see consumers/providers (3.0s)
  ✓  8 [chromium] › J5 — federation: workspace → federated search → member-B repo-scoped detail (5.8s)

  8 passed (41.9s)
```

### Pytest

```
$ .venv/bin/python -m pytest tests/e2e_journeys/test_cli_journey.py -v
tests/e2e_journeys/test_cli_journey.py::test_cli_journey_index_search_impact_context_diagnostics PASSED [100%]
============================== 1 passed in 0.55s ===============================
```

## Evidência medida (amostras)

- **J1** — `GET /files/{id}/occurrences` real disparado pelo botão
  "Explorar símbolos do arquivo"; drill de `src/hello.py` → `Nós (3)` /
  `Relações (2)`; aresta `DEFINES` → inspetor `Relação` com
  `resolved` · `syntactic` · `tree-sitter-python` · `Evidências: 1`.
- **J2** — busca `util` → 3 hits (`src.util`, `src.util.util`,
  `src/util.py`); `?focus=occ:9` → `Relações (1)` (DEFINES módulo→função);
  "Expandir" no módulo → `GET /graph/neighbors?occurrence_id=7` observado
  na rede → `Relações (2)`; `/files/{id}/occurrences` → `3 ocorrências
  indexadas`; `/impact` seed `src.util.util` → raio `src.util` (direto,
  verified) + `src/util.py` (transitivo, verified) com badges.
- **J3** — raiz descoberta via API com lanes nos dois sentidos
  (`customer_stats` na medição); pick real no select → URL
  `?table=<id>`; `Origem (upstream)` / `Destino (downstream)` com
  distâncias `d1…`; cada linha da tabela de arestas traz
  `atlas-evidence-badge` + resolvedor preenchido.
- **J4** — providers: specs reais (Pet Store etc.) + endpoints `/pets`
  com método; consumers: `GET /domains/entities/{id}/neighbors?direction=in`
  observado → região mostra estado real do corpus (ver achado A1).
- **J5** — `GET /federation/workspaces` → `e2e-ws`; `members_searched=2`;
  `repositories_with_hits` inclui o membro B (`sample_repo`, id 5); hit
  `repository_id=5, snapshot_id` idêntico ao da view; detalhe
  `/search?repo=<root_handle>` → summary `snapshot #<mesmo>` +
  `repositório <root>` + hit visível.
- **J6** — pack API `manifest.schema_version=2`, `selected =
  len(items)`; UI renderiza `Itens de contexto (N)`, `Símbolos
  selecionados (N)`, `Arquivos relevantes (N)` idênticos ao payload.
- **J7** — `symbol-not-found` 404 → bloco `Falha na operação` com texto
  `API respondeu 404 (symbol-not-found)`; id malformado →
  `Identificador de símbolo inválido.`; `?snapshot=9999` →
  `snapshot #9999 do link não existe` + navegação íntegra.
- **J8** — overview `snapshot_id=1`, 5 itens (`src/hello.py`,
  `src/util.py`, …); download `graph-export.json` → parse → mesmo
  snapshot e mesmo conjunto de paths que a Lista renderizada.
- **B1** — `files_indexed=5`, `symbols_new=9`, `evidence=2`; search
  `hello` → `total=3`, root_handle == fixture; impact →
  `base=target=snapshot:1`, `src.util` direct + `src/util.py` transitive,
  ambos `verified`, `diagnostics=[]`, `edge_refs` `rel:*` consistentes,
  `false_confidence_leaks=[]`; pack schema 2 com `src/util.py`;
  `status` `READY`, `doctor` `healthy=true`; root não indexado →
  diagnóstico `repo-not-indexed` com `impacts=[]`.

## Achados e decisões honestas

### A1 — Consumidores CALLS_ENDPOINT nunca resolvem no corpus (gap de fase, declarado)

Os 10 call-sites `CALLS_ENDPOINT` extraídos de `api_repo` saem todos com
`resolution_status` `unresolved` (URL relativa) ou `external` (URL
absoluta) — **zero** aresta aponta para `to_entity_occ_id`. Verificado no
banco para os 4 fixtures de domínio (43 relações no repo api): literal
`"/v1/pets"` não vira referência ao endpoint `GET /v1/pets` indexado.

Isso é **design declarado**, não bug introduzido:
`src/code_atlas/domains/api/emit.py` documenta "a resolução
literal-URL↔contrato é da Phase 9 — aqui só registramos candidatos, nunca
resolvemos". Consequência medida: o disclosure de consumidores em
`/contracts` faz o fetch lazy real e renderiza honestamente `Nenhum
consumidor conhecido.` — nunca fabrica. J4 asserita exatamente esse
comportamento (fetch observado + estado terminal explícito), e o teste
aceita consumidores reais se uma fase futura resolver as arestas.

**Ação recomendada (fora do escopo deste spec):** promover a resolução
URL↔endpoint quando a Phase 9 correspondente for agendada — até lá, a
tela de "consumers" sempre estará vazia em qualquer índice real, o que é
verdade sobre o produto mas vale constar no release note.

### A2 — Sem botão de export no UI do grafo

A jornada "export" pedida pelo spec não tem superfície de UI dedicada —
`impact` exporta (`Exportar JSON`/`Markdown`) e `context` exporta
(`Exportar .json`), mas `/graph` não. A superfície de export real do
grafo é a própria API (`GET /graph/overview`). J8 prova que o payload
servido == conteúdo renderizado e baixa o arquivo de verdade via
`<a download>` no browser. Se a decisão de produto for "export precisa
de botão no grafo", fica como backlog — reportado, não contornado.

### A3 — Sem superfície de busca federada no UI

`/federation` lista workspace/membros/resolução de snapshot; a busca
cross-repo existe apenas na API (`GET /federation/workspaces/{id}/search`)
e como escopo `?workspace=`/`?federation=` do `/search` backend. J5
exercita a busca federada real via API e ancora o "detalhe do membro B"
no caminho UI real que existe (`/search?repo=<root_handle>` com
`snapshot #N` idêntico ao resolvido pela view federada). Gap de UI
documentado — a funcionalidade backend está medida e preserva
`repository_id`/`snapshot_id` por hit sem colapsar homônimos.

### A4 — Notas de ambiente/infra (não-produto)

- Um explorer órfão de uma sessão anterior ocupava `:8743` no primeiro
  run local — `porta indisponível` do subprocess e o `waitForServer`
  passava contra o processo velho (mesmo fixture, mesmos números). O
  resultado final medido acima foi re-executado com porta limpa.
- `tsconfig.json` raiz do explorer emite TS2591/TS2304 (globals node)
  para **todos** os arquivos `e2e/` — condição pré-existente; a suíte
  transpila via Playwright. `ng lint` sem config vigente.
- `impact` na fixture copiada sem git resolve `base/target` para
  `snapshot:<id>` — semântica snapshot-bound honesta do engine, sem
  `invalid-revision`; usada na jornada CLI.

## Limites preservados

- Nenhum código/job/Terraform/pipeline dos fixtures executado — o
  servidor de domínios apenas **indexa**; as jornadas só leem via
  UI/API/CLI.
- Nenhuma feature removida ou mockada para passar; estados honestos
  (empty/error/unresolved/external) permanecem distinguíveis e são
  asseridos como tais.
- Arquivos tocados: `explorer/e2e/journeys.spec.ts` (novo),
  `tests/e2e_journeys/test_cli_journey.py` (novo), este documento.
  `src/` e demais specs E2E intactos.
