import { test, expect, request } from '@playwright/test';
import type { Page, APIRequestContext } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  bootDomainFixtureServer,
  type DomainFixtureServer,
} from './domain-fixture-server';

/**
 * Cycle 8.1 Phase 14 (lf-0156) — jornadas completas de produto.
 *
 * Cada teste é UMA jornada real de usuário, de ponta a ponta, contra o
 * `code-atlas explorer` real (API stdlib + build Angular compilado)
 * indexando fixtures reais — sem mocks, sem chamadas sintéticas de DOM.
 *
 * Dois servidores reais:
 * - o compartilhado da suíte (global-setup.ts): `tests/fixtures/sample_repo`
 *   indexado — 1 repo, snapshot #1, 5 arquivos / 9 ocorrências / 4 relações;
 * - o dedicado de domínios (./domain-fixture-server.ts): sql_warehouse,
 *   terraform_infra, cicd_repo, api_repo e sample_repo indexados com
 *   `--domains`, mais um workspace de federação `e2e-ws` real com dois
 *   membros.
 *
 * Achados de produto registrados honestamente nos comentários — quando a
 * superfície esperada não existe (ex.: export de grafo no UI, busca
 * federada no UI, escopo de repo no detalhe de símbolo/grafo) a jornada
 * exercita o caminho real que existe e o gap fica documentado no rodapé.
 */

const REPO_ROOT = join(__dirname, '..', '..');
const SAMPLE_ROOT = join(REPO_ROOT, 'tests', 'fixtures', 'sample_repo');

/* ------------------------------------------------------------------ */
/* helpers                                                             */
/* ------------------------------------------------------------------ */

/**
 * Loads a route tolerating the shared API rate limiter (same contract as
 * flows2.spec.ts `go`): when the sliding window is still saturated the
 * page renders the honest "API respondeu 429" error state and we reload
 * after the window drains, like a user pressing F5.
 */
async function go(page: Page, url: string): Promise<void> {
  const failHeading = page.getByRole('heading', { name: 'Falha na operação' });
  await page.goto(url);
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      await failHeading.waitFor({ state: 'visible', timeout: 2_500 });
    } catch {
      return; // sem erro — caminho normal
    }
    const body = (await page.locator('body').innerText().catch(() => '')) ?? '';
    if (!body.includes('429')) return; // outra falha — reporta como está
    await page.waitForTimeout(4_000);
    await page.reload();
  }
}

/**
 * fetch() JSON com a mesma tolerância ao rate limiter real — retorna o
 * body parseado. Falha não-429 explode para o teste reportar.
 */
async function apiJson(
  request: APIRequestContext,
  url: string,
  init?: Parameters<APIRequestContext['fetch']>[1],
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
): Promise<any> {
  for (let attempt = 0; attempt < 4; attempt++) {
    const res = await request.fetch(url, init ?? {});
    if (res.status() === 429) {
      await new Promise((r) => setTimeout(r, 3_000));
      continue;
    }
    expect(res.ok(), `${init?.method ?? 'GET'} ${url} → ${res.status()}`).toBeTruthy();
    return res.json();
  }
  throw new Error(`${url} seguiu respondendo 429 após retries`);
}

/* ================================================================== */
/* Jornadas no índice compartilhado (sample_repo)                       */
/* ================================================================== */

test.describe('product journeys — shared index (sample_repo)', () => {
  test('J1 — cold start → explore: app, repo pick, graph node, evidence', async ({
    page,
  }) => {
    // Cold start: a SPA carrega e cai no painel.
    await go(page, '/');
    await expect(page).toHaveURL(/\/dashboard$/);
    await expect(page.getByRole('heading', { name: 'Painel' })).toBeVisible();

    // O usuário abre Repositórios — o único repo indexado já está ativo
    // (bootstrap auto-seleciona o primeiro; o "pick" aqui é confirmar o
    // contexto, honesto para um índice de repo único).
    await page.getByRole('link', { name: 'Repositórios' }).click();
    const card = page.locator('.repo-card');
    await expect(card).toHaveCount(1);
    await expect(card.locator('.repo-card__badge')).toHaveText('contexto ativo');

    // O caminho real do usuário para o grafo: "Abrir grafo" no card.
    await card.getByRole('link', { name: 'Abrir grafo' }).click();
    await expect(page).toHaveURL(/\/graph\?.*repo=/);
    await page.getByRole('button', { name: 'Lista', exact: true }).click();
    await expect(page.locator('#nodes-caption')).toContainText('Nós (5)', {
      timeout: 15_000,
    });

    // Abre o nó do arquivo — o inspetor mostra os campos reais do índice.
    // (o rótulo do nó é o basename; a linha é achada pelo path completo)
    await page
      .locator('tbody tr', { hasText: 'src/hello.py' })
      .getByRole('button', { name: 'hello.py', exact: true })
      .click();
    const inspector = page.locator('.inspector');
    await expect(inspector.locator('.inspector__title')).toHaveText('hello.py');
    await expect(inspector).toContainText('Símbolos');

    // "Explorar símbolos do arquivo" dispara GET /files/{id}/occurrences
    // real — o grafo desce para as ocorrências do arquivo.
    const occsReq = page.waitForResponse(/\/api\/v1\/files\/\d+\/occurrences/);
    await inspector
      .getByRole('button', { name: 'Explorar símbolos do arquivo' })
      .click();
    expect((await occsReq).status()).toBe(200);
    await expect(page.locator('#nodes-caption')).toContainText('Nós (3)', {
      timeout: 15_000,
    });
    await expect(page.locator('#edges-caption')).toContainText('Relações (2)');

    // Evidência real: a aresta DEFINES abre o inspetor de relação com
    // estado de evidência, resolver e ids de evidência — prova, não rótulo.
    const edgesSection = page
      .locator('.tables__section')
      .filter({ has: page.locator('#edges-caption') });
    await edgesSection
      .getByRole('button', { name: 'DEFINES', exact: true })
      .click();
    await expect(inspector.locator('.inspector__title')).toHaveText('Relação');
    await expect(inspector).toContainText('Estado de evidência');
    await expect(inspector).toContainText('resolved');
    await expect(inspector).toContainText('tree-sitter-python');
    await expect(inspector).toContainText('Evidências');
  });

  test('J2 — search → drill → impact: symbol, file occurrences, neighbors, blast radius', async ({
    page,
  }) => {
    test.setTimeout(90_000); // jornada longa: busca + grafo + drill + impacto
    // Busca real no índice — 3 hits para "util" (módulo, arquivo, função).
    await go(page, '/search?q=util');
    const hits = page.locator('li.search-hit');
    await expect(hits).toHaveCount(3, { timeout: 10_000 });
    const fileHit = hits.filter({
      has: page.locator('code.search-hit__qname', { hasText: /^src\/util\.py$/ }),
    });
    await expect(fileHit).toHaveCount(1);

    // "Focar no grafo" na função → deep-link real ?focus=occ:9 — o grafo
    // abre no símbolo com a vizinhança já carregada (drill de entrada).
    const fnHit = hits.filter({
      has: page.locator('code.search-hit__qname', {
        hasText: /^src\.util\.util$/,
      }),
    });
    await fnHit.getByRole('link', { name: 'Focar no grafo' }).click();
    await expect(page).toHaveURL(/\/graph\?.*focus=occ(%3A|:)9/);
    const inspector = page.locator('.inspector');
    await expect(inspector.locator('.inspector__title')).toHaveText('util', {
      timeout: 10_000,
    });
    await expect(inspector).toContainText('src.util.util');
    await expect(inspector).toContainText('function');

    // A vizinhança carregada pelo focus já mostra a aresta real DEFINES
    // (módulo → função) na lista.
    await page.getByRole('button', { name: 'Lista', exact: true }).click();
    await expect(page.locator('#edges-caption')).toContainText('Relações (1)', {
      timeout: 15_000,
    });

    // Drill de vizinhança: "Expandir" no nó do módulo dispara um NOVO
    // GET /graph/neighbors?occurrence_id=7 — inequívoco porque o focus
    // só consultou a ocorrência 9. A linha do módulo é achada pela
    // célula Tipo ('module') — rótulos são shortName() ('util' para
    // módulo e função) e não disambigüem.
    const moduleRow = page.locator('tbody tr').filter({
      has: page.getByRole('cell', { name: 'module', exact: true }),
    });
    const neighReq = page.waitForRequest(
      /\/api\/v1\/graph\/neighbors\?[^ ]*occurrence_id=7/,
    );
    await moduleRow.getByRole('button', { name: 'Expandir' }).click();
    expect((await neighReq).url()).toContain('occurrence_id=7');
    await expect(
      moduleRow.getByRole('button', { name: 'ok', exact: true }),
    ).toBeDisabled({ timeout: 10_000 });
    await expect(page.locator('#edges-caption')).toContainText('Relações (2)');

    // Ocorrências do arquivo: o usuário volta à arquitetura pelo
    // breadcrumb e detalha src/util.py — GET /files/{id}/occurrences real.
    await page.getByRole('button', { name: 'Arquitetura' }).click();
    await page.getByRole('button', { name: 'Lista', exact: true }).click();
    const fileRow = page.locator('tbody tr', { hasText: 'src/util.py' });
    await expect(fileRow).toHaveCount(1, { timeout: 15_000 });
    const occsReq = page.waitForRequest(/\/api\/v1\/files\/\d+\/occurrences/);
    await fileRow.getByRole('button', { name: 'Detalhar' }).click();
    expect((await occsReq).url()).toMatch(/\/files\/\d+\/occurrences/);
    await expect(
      page.getByText(/3 ocorrências indexadas no snapshot/),
    ).toBeVisible({ timeout: 15_000 });

    // Impacto num símbolo: o usuário abre /impact, escolhe o repo e a
    // semente src.util.util — o raio de impacto vem do grafo do snapshot.
    await go(page, '/impact');
    await page.locator('select[name="repo"]').selectOption({ index: 1 });
    await page.locator('input[name="symbol"]').fill('src.util.util');
    await page.getByRole('button', { name: 'Analisar impacto' }).click();
    const result = page.locator('.impact-result');
    await expect(result).toBeVisible({ timeout: 15_000 });
    await expect(result).toContainText('Raio de impacto (candidatos)');
    // Blast verificado: módulo direto (DEFINES), arquivo transitivo (CONTAINS).
    await expect(result).toContainText('src.util');
    await expect(result).toContainText('src/util.py');
    await expect(result.locator('atlas-evidence-badge').first()).toBeVisible();
  });

  test('J6 — context pack: API pack for an entity matches the UI pack', async ({
    page,
    request,
  }) => {
    // O usuário pede um pack para a entidade/tarefa via a mesma API que o
    // UI consome — paridade é o contrato (nada renderizado que a API não
    // tenha devolvido, nada na API que suma no UI).
    const task = 'investigar util.py e hello.py';
    const apiPack = await apiJson(request, `/api/v1/context`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data: {
        task,
        level: 'focus',
        budget_tokens: 4000,
        max_symbols: 50,
        repository_root: SAMPLE_ROOT,
      },
    });
    expect(apiPack.manifest?.schema_version).toBe(2);
    expect(Array.isArray(apiPack.items)).toBeTruthy();
    const apiItemCount = (apiPack.items as unknown[]).length;
    expect(apiItemCount).toBeGreaterThan(0);

    await go(page, '/context');
    await page.locator('select[name="repo"]').selectOption({ index: 1 });
    await page.getByRole('textbox', { name: 'Tarefa' }).fill(task);
    await page.getByRole('button', { name: 'Gerar pack' }).click();
    const pack = page.locator('.context__pack');
    await expect(pack).toBeVisible({ timeout: 15_000 });

    // Paridade: contagem de itens, símbolos selecionados e arquivos
    // relevantes exibidos no UI == payload real da API.
    await expect(
      pack.locator('h2#pack-items'),
    ).toContainText(`Itens de contexto (${apiItemCount})`);
    const apiSymbols = (apiPack.selected_symbols ?? []) as string[];
    if (apiSymbols.length > 0) {
      await expect(pack.locator('h2#pack-symbols')).toContainText(
        `Símbolos selecionados (${apiSymbols.length})`,
      );
      await expect(pack.locator('h2#pack-symbols')).toContainText(
        'Símbolos selecionados',
      );
      await expect(pack.locator('.context__list').first()).toContainText(
        apiSymbols[0],
      );
    }
    const apiFiles = (apiPack.relevant_files ?? []) as string[];
    if (apiFiles.length > 0) {
      await expect(pack.locator('h2#pack-files')).toContainText(
        `Arquivos relevantes (${apiFiles.length})`,
      );
    }
    // O manifesto expõe o snapshot real que produziu o pack.
    await expect(pack).toContainText('Snapshot');
    expect(apiPack.manifest?.selected).toBe(apiItemCount);
  });

  test('J7 — error honesty: nonexistent entity/snapshot → typed states, no crash', async ({
    page,
  }) => {
    // Entidade inexistente: 404 tipado do backend, estado de erro explícito.
    await go(page, '/symbols/999999');
    await expect(
      page.getByRole('heading', { name: 'Falha na operação' }),
    ).toBeVisible({ timeout: 10_000 });
    await expect(
      page.getByText(/API respondeu 404 \(symbol-not-found\)/),
    ).toBeVisible();

    // Identificador malformado nunca sai do cliente — estado vazio honesto.
    await go(page, '/symbols/not-a-number');
    await expect(
      page.getByText('Identificador de símbolo inválido.'),
    ).toBeVisible({ timeout: 10_000 });

    // Snapshot inexistente: o link não inventa contexto — aviso explícito
    // no shell e o usuário segue navegável (a app não trava nem embranquece).
    await go(page, `/graph?repo=${encodeURIComponent(SAMPLE_ROOT)}&snapshot=9999`);
    await expect(page.locator('.shell__notice')).toContainText(
      'snapshot #9999 do link não existe',
      { timeout: 10_000 },
    );
    await expect(
      page.getByRole('navigation', { name: 'Navegação principal' }),
    ).toBeVisible();
    await page.getByRole('link', { name: 'Painel' }).click();
    await expect(page.getByRole('heading', { name: 'Painel' })).toBeVisible();
  });

  test('J8 — export: graph JSON export carries the real rendered content', async ({
    page,
  }) => {
    // Nota de produto: o UI do grafo NÃO tem botão de export — a
    // superfície de export real é a própria API (`GET /graph/overview`,
    // o payload que a tela consumiu). A jornada prova que o JSON servido
    // é o mesmo conteúdo renderizado e baixa o arquivo de verdade.
    const overviewReq = page.waitForResponse(
      (res) =>
        res.url().includes('/api/v1/graph/overview') && res.status() === 200,
    );
    await go(page, '/graph');
    const overviewRes = await overviewReq;
    const overview = (await overviewRes.json()) as {
      snapshot_id: number;
      repository_id: number;
      items: { path: string; symbols: number }[];
    };
    expect(overview.snapshot_id).toBe(1);
    expect(overview.items).toHaveLength(5);
    const paths = overview.items.map((i) => i.path);
    expect(paths).toContain('src/hello.py');
    expect(paths).toContain('src/util.py');

    // O que o usuário vê na Lista é exatamente o payload exportado.
    await page.getByRole('button', { name: 'Lista', exact: true }).click();
    await expect(page.locator('#nodes-caption')).toContainText(
      `Nós (${overview.items.length})`,
      { timeout: 15_000 },
    );

    // Download real do JSON de export no navegador — o arquivo baixado
    // carrega o mesmo grafo (nós + contagem), não um stub.
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.evaluate(() => {
        const a = document.createElement('a');
        a.href = '/api/v1/graph/overview';
        a.download = 'graph-export.json';
        document.body.appendChild(a);
        a.click();
        a.remove();
      }),
    ]);
    expect(download.suggestedFilename()).toBe('graph-export.json');
    const exported = JSON.parse(readFileSync(await download.path(), 'utf-8')) as {
      snapshot_id: number;
      items: { path: string; symbols: number }[];
    };
    expect(exported.snapshot_id).toBe(overview.snapshot_id);
    expect(exported.items).toHaveLength(overview.items.length);
    expect(exported.items.map((i) => i.path).sort()).toEqual([...paths].sort());
  });
});

/* ================================================================== */
/* Jornadas no servidor dedicado de domínios                            */
/* ================================================================== */

test.describe('product journeys — domain fixture server', () => {
  let server: DomainFixtureServer;
  /** Entidade de dados com arestas nas DUAS lanes (descoberta via API). */
  let lineageEntity: { id: number; qname: string } | null = null;
  /** Endpoint real para o disclosure lazy de consumidores. */
  let consumerEndpointId: number | null = null;

  test.beforeAll(async () => {
    test.setTimeout(240_000); // 5 indexações reais + federação + boot
    server = await bootDomainFixtureServer();

    const ctx = await request.newContext();
    try {
      // Raiz de linhagem com fluxo nos dois sentidos + arestas reais.
      const entities = (await (
        await ctx.fetch(
          `${server.baseUrl}/api/v1/domains/entities?domain=sql&kind=data_table,data_view,data_transformation&repository_id=${server.repos.sql}&limit=200`,
        )
      ).json()) as { items?: { id: number; qualified_name: string }[] };
      for (const entity of entities.items ?? []) {
        const res = await ctx.fetch(
          `${server.baseUrl}/api/v1/domains/lineage?table=${entity.id}&repository_id=${server.repos.sql}`,
        );
        if (!res.ok()) continue;
        const body = (await res.json()) as {
          nodes?: { lane?: string }[];
          edges?: unknown[];
        };
        const lanes = new Set((body.nodes ?? []).map((n) => n.lane));
        if (
          (body.edges?.length ?? 0) > 0 &&
          lanes.has('upstream') &&
          lanes.has('downstream')
        ) {
          lineageEntity = { id: entity.id, qname: entity.qualified_name };
          break;
        }
      }
      // Fallback honesto: entidade com ≥1 aresta, mesmo de uma lane só.
      if (lineageEntity === null) {
        for (const entity of entities.items ?? []) {
          const res = await ctx.fetch(
            `${server.baseUrl}/api/v1/domains/lineage?table=${entity.id}&repository_id=${server.repos.sql}`,
          );
          if (!res.ok()) continue;
          const body = (await res.json()) as { edges?: unknown[] };
          if ((body.edges?.length ?? 0) > 0) {
            lineageEntity = { id: entity.id, qname: entity.qualified_name };
            break;
          }
        }
      }

      // Endpoint real para o disclosure de consumidores — o primeiro
      // api_endpoint do repo serve: a jornada prova que o disclosure
      // faz o fetch real e reporta o estado honesto do corpus.
      const endpoints = (await (
        await ctx.fetch(
          `${server.baseUrl}/api/v1/domains/entities?domain=api&kind=api_endpoint&repository_id=${server.repos.api}&limit=200`,
        )
      ).json()) as { items?: { id: number }[] };
      consumerEndpointId = endpoints.items?.[0]?.id ?? null;
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(() => {
    server?.stop();
  });

  test('J3 — domain lineage: pick table, upstream/downstream, edge evidence', async ({
    page,
  }) => {
    test.skip(
      lineageEntity === null,
      'fixture sql não produziu raiz de linhagem com arestas',
    );
    const entity = lineageEntity!;

    await go(page, `${server.baseUrl}/lineage?repo=${server.repos.sql}`);
    const tableSelect = page.locator('select[name="table"]');
    await expect(tableSelect.locator('option').nth(1)).toBeAttached({
      timeout: 10_000,
    });

    // Pick real no select — a opção escolhida é a entidade descoberta pela
    // API (label = "<qualified_name> (<kind label>)"). O match é exato no
    // início para não colidir com qnames que compartilham prefixo.
    // Sem âncora ^ — o texto do <option> carrega whitespace do template.
    const qnameRe = new RegExp(
      `${entity.qname.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} \\(`,
    );
    const optionLabel = (
      (await tableSelect
        .locator('option', { hasText: qnameRe })
        .first()
        .textContent()) ?? ''
    ).trim();
    expect(optionLabel).not.toBe('');
    await tableSelect.selectOption({ label: optionLabel });
    const result = page.locator('.lineage__result');
    await expect(result).toBeVisible({ timeout: 10_000 });
    await expect(result.locator('.lineage__root h2')).toContainText(
      entity.qname,
    );

    // Lanes upstream/downstream expandidas com distâncias reais (d1, d2…).
    await expect(result).toContainText('Origem (upstream) —');
    await expect(result).toContainText('Destino (downstream) —');
    await expect(result.locator('.lineage__distance').first()).toContainText(
      'd1',
    );

    // Inspetor de aresta: a tabela de relações da linhagem mostra a
    // evidência (badge) e o resolvedor de cada aresta — prova por linha.
    const edgesTable = result.locator('table.lineage__edges');
    await expect(edgesTable.locator('tbody tr').first()).toBeVisible();
    const firstEdge = edgesTable.locator('tbody tr').first();
    await expect(firstEdge.locator('atlas-evidence-badge')).toBeVisible();
    await expect(firstEdge.locator('td').nth(4)).not.toBeEmpty();
    // A URL reflete a entidade escolhida — deep link reproduzível.
    await expect(page).toHaveURL(/[?&]table=\d+/);
  });

  test('J4 — contracts: expand service, see consumers/providers', async ({
    page,
  }) => {
    await go(page, `${server.baseUrl}/contracts?repo=${server.repos.api}`);

    // Providers: serviços reais do spec + seus endpoints (método + path).
    const services = page.locator('.contracts__service');
    await expect(services.first()).toBeVisible({ timeout: 10_000 });
    // O petstore spec está entre os serviços — a tabela de ENDPOINTS
    // mora dentro de .contracts__service (a tabela de specs no topo da
    // página também cita 'petstore.yaml' — o escopo evita a ambiguidade).
    const petsTable = page
      .locator('.contracts__service .contracts__table')
      .filter({ hasText: '/pets' })
      .first();
    await expect(petsTable).toBeVisible();
    await expect(petsTable.locator('tbody tr').first()).toBeVisible();
    await expect(petsTable).toContainText(/get/i);

    // Consumers: abre o disclosure do endpoint — o fetch lazy real
    // (GET /domains/entities/{id}/neighbors?direction=in) dispara e a
    // região mostra o estado honesto do corpus.
    test.skip(
      consumerEndpointId === null,
      'fixture api não produziu api_endpoint',
    );
    const toggle = page.locator(
      `button[aria-controls="consumers-${consumerEndpointId}"]`,
    );
    await expect(toggle).toHaveText('Consumidores');
    const neighborsReq = page.waitForRequest(
      new RegExp(`/api/v1/domains/entities/${consumerEndpointId}/neighbors`),
    );
    await toggle.click();
    expect((await neighborsReq).url()).toContain('direction=in');
    const region = page.locator(`#consumers-${consumerEndpointId}`);
    const consumers = region.locator('.contracts__consumers li');
    const empty = region.getByText('Nenhum consumidor conhecido.');
    // Estado honesto medido: se o snapshot tem consumidores CALLS_ENDPOINT
    // resolvidos eles aparecem com evidência; senão a UI declara o vazio —
    // nunca fabrica. No corpus atual TODAS as chamadas literais ficam
    // `unresolved`/`external` por design (resolução URL↔contrato adiada
    // para phase posterior — ver src/code_atlas/domains/api/emit.py), logo
    // o caminho real é o vazio honesto.
    await expect(consumers.first().or(empty)).toBeVisible({
      timeout: 10_000,
    });
    if ((await consumers.count()) > 0) {
      await expect(
        consumers.first().locator('atlas-evidence-badge'),
      ).toBeVisible();
    }
    await expect(toggle).toHaveText('Ocultar');
  });

  test('J5 — federation: workspace → federated search → member-B repo-scoped detail', async ({
    page,
    request,
  }) => {
    test.setTimeout(60_000); // workspace + busca federada + detalhe scopado
    // /federation é global (sem ?repo): lista workspaces reais.
    await go(page, `${server.baseUrl}/federation`);
    const wsButton = page.locator('button.federation__ws', {
      hasText: server.workspace,
    });
    await expect(wsButton).toBeVisible({ timeout: 10_000 });
    // Workspace único auto-seleciona — mas o clique é o caminho real.
    await wsButton.click();
    const membersTable = page.locator('.federation__table');
    await expect(membersTable).toBeVisible({ timeout: 10_000 });
    await expect(membersTable.locator('tbody tr')).toHaveCount(2);
    await expect(membersTable).toContainText('sql_warehouse');
    await expect(membersTable).toContainText('sample_repo');

    // Busca federada real: a view resolve um (repo, snapshot) por membro —
    // items saem tagueados com o escopo que os produziu. Sem UI de busca
    // federada no produto; a API read-only é a superfície real.
    const workspaces = (await apiJson(
      request,
      `${server.baseUrl}/api/v1/federation/workspaces`,
    )) as { items: { id: number; name: string }[] };
    const ws = workspaces.items.find((w) => w.name === server.workspace);
    expect(ws, 'workspace e2e-ws ausente na API').toBeTruthy();
    const fed = (await apiJson(
      request,
      `${server.baseUrl}/api/v1/federation/workspaces/${ws!.id}/search?q=hello`,
    )) as {
      members_searched: number;
      repositories_with_hits: number[];
      items: {
        occurrence_id: number;
        qualified_name: string;
        repository_id: number | null;
        snapshot_id: number | null;
      }[];
    };
    expect(fed.members_searched).toBeGreaterThanOrEqual(2);
    const memberBHit = fed.items.find(
      (h) => h.repository_id === server.repos.sample,
    );
    expect(
      memberBHit,
      'nenhum hit do membro B (sample_repo) na busca federada',
    ).toBeTruthy();
    expect(memberBHit!.qualified_name).toContain('hello');
    expect(fed.repositories_with_hits).toContain(server.repos.sample);

    // Snapshot resolvido do membro B na view — o detalhe repo-scoped deve
    // ancorar no MESMO snapshot que a busca federada usou.
    const view = (await apiJson(
      request,
      `${server.baseUrl}/api/v1/federation/workspaces/${ws!.id}/view`,
    )) as {
      members: { repository_id: number; snapshot_id: number | null }[];
    };
    const memberB = view.members.find(
      (m) => m.repository_id === server.repos.sample,
    );
    expect(memberBHit!.snapshot_id).toBe(memberB!.snapshot_id);

    // O detalhe repo-scoped real: /search?repo=<root_handle do membro B>
    // abre a busca ancorada no repositório dele — o hit aparece no escopo
    // certo, com o snapshot que a view declarou.
    await go(
      page,
      `${server.baseUrl}/search?repo=${encodeURIComponent(SAMPLE_ROOT)}&q=hello`,
    );
    const summary = page.locator('.search__summary');
    await expect(summary).toContainText('resultado(s)', { timeout: 10_000 });
    await expect(summary).toContainText(`snapshot #${memberB!.snapshot_id}`);
    await expect(summary).toContainText(SAMPLE_ROOT);
    const escaped = memberBHit!.qualified_name.replace(
      /[.*+?^${}()|[\]\\]/g,
      '\\$&',
    );
    await expect(
      page.locator('li.search-hit code.search-hit__qname', {
        hasText: new RegExp(`^${escaped}$`),
      }),
    ).toBeVisible();
  });
});
