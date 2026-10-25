import { test, expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { spawn, type ChildProcess } from 'node:child_process';
import { existsSync } from 'node:fs';
import { readFileSync } from 'node:fs';
import { createServer } from 'node:net';
import { join } from 'node:path';

/**
 * Cycle 6.1 feature coverage (Phase 13): repositories control center,
 * global context deep links, search deep-link/URL-sync, symbol inspector,
 * impact export, context-pack expansion/copy, graph drill-down/edge
 * evidence/camera preservation, sidebar persistence and a real
 * kill/restart of the explorer subprocess.
 *
 * Same harness as flows.spec.ts: the real `code-atlas explorer` subprocess
 * serving the compiled Angular build + real stdlib HTTP API over the real
 * SQLite index of tests/fixtures/sample_repo. No request mocking; every
 * assertion targets visible data, fired requests, downloads or persisted
 * state.
 *
 * Fixture facts (verified against the live API):
 * - 1 repository (tests/fixtures/sample_repo), snapshot #1 READY,
 *   commit 20bcdfe… on ref cycle-6.1-explorer-hardening;
 * - counts: 5 files, 9 symbol occurrences, 4 relations, 2 evidence;
 * - occurrences: 4 (src/hello.py file), 6 (src.hello module),
 *   8 (src.hello.hello function), 5/7/9 analogs for util;
 * - relations: edge 1 CONTAINS occ:4→occ:6 (syntactic-scope-v1),
 *   edge 3 DEFINES occ:6→occ:8 (tree-sitter-python, evidence [1]).
 */

const REPO_ROOT_HANDLE =
  '/home/edgar/Documentos/projetos/forjas/code-atlas-forge/tests/fixtures/sample_repo';

/* ------------------------------------------------------------------ */
/* helpers                                                             */
/* ------------------------------------------------------------------ */

/**
 * Pixel-diff ratio between two PNG screenshots, decoded in the page itself
 * (no extra npm deps). Returns 0 for identical frames, ~1 for unrelated.
 */
async function pngDiffRatio(page: Page, a: Buffer, b: Buffer): Promise<number> {
  return page.evaluate(async ([a64, b64]) => {
    const load = (d: string) =>
      new Promise<HTMLImageElement>((res, rej) => {
        const i = new Image();
        i.onload = () => res(i);
        i.onerror = rej;
        i.src = `data:image/png;base64,${d}`;
      });
    const [ia, ib] = await Promise.all([load(a64), load(b64)]);
    if (ia.width !== ib.width || ia.height !== ib.height) return 1;
    const c = document.createElement('canvas');
    c.width = ia.width;
    c.height = ia.height;
    const g = c.getContext('2d');
    if (!g) return 1;
    g.drawImage(ia, 0, 0);
    const d1 = g.getImageData(0, 0, c.width, c.height).data;
    g.drawImage(ib, 0, 0);
    const d2 = g.getImageData(0, 0, c.width, c.height).data;
    let n = 0;
    for (let i = 0; i < d1.length; i += 4) {
      const delta =
        Math.abs(d1[i] - d2[i]) +
        Math.abs(d1[i + 1] - d2[i + 1]) +
        Math.abs(d1[i + 2] - d2[i + 2]);
      if (delta > 30) n++;
    }
    return n / (c.width * c.height);
  }, [a.toString('base64'), b.toString('base64')]);
}

/** Waits until the graph canvas mounted (WebGL) — skips the rest gracefully. */
async function waitGraphReady(page: Page, label: RegExp): Promise<boolean> {
  const stage = page.locator('.graph-stage');
  try {
    await expect(stage).toHaveAttribute('aria-label', label, { timeout: 15_000 });
    return true;
  } catch {
    return false;
  }
}

/**
 * Loads a route, tolerating the shared API rate limiter. The whole suite
 * (~66 specs incl. a11y scans) fires well over the product's real
 * 120 req/min budget, so a page can land while the sliding window is
 * still saturated and render the honest "API respondeu 429" error state.
 * When — and only when — that specific failure surfaces, we wait for the
 * window to drain and reload, exactly like a user pressing F5. Any other
 * error state is left alone for the assertions to report. Bounded: after
 * three retries the page stays as-is and asserts fail on real state.
 */
async function go(page: Page, url: string): Promise<void> {
  const failHeading = page.getByRole('heading', { name: 'Falha na operação' });
  await page.goto(url);
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      await failHeading.waitFor({ state: 'visible', timeout: 2_500 });
    } catch {
      return; // nenhum erro — a página seguiu o caminho normal
    }
    const body = (await page.locator('body').innerText().catch(() => '')) ?? '';
    if (!body.includes('429')) return; // outra falha — reporta como está
    await page.waitForTimeout(4_000); // deixa a janela deslizante drenar
    await page.reload();
  }
}

test.describe('cycle-6.1 flows', () => {
  /* ======================= repositories + context ======================= */

  test('11 — repositories page shows real counts and the auto-selected context', async ({
    page,
  }) => {
    await go(page, '/repositories');
    const card = page.locator('.repo-card');
    await expect(card).toHaveCount(1);
    await expect(card.locator('.repo-card__path')).toHaveText(REPO_ROOT_HANDLE);
    // Honest index counts from GET /index/status (fixture: 5 files,
    // 9 occurrences, 4 relations, 1 READY snapshot).
    await expect(card).toContainText('Snapshots READY');
    await expect(card).toContainText('Arquivos');
    await expect(card).toContainText('Símbolos');
    await expect(card).toContainText('Relações');
    const meta = card.locator('.repo-card__meta');
    await expect(meta.locator('dd').nth(1)).toHaveText('1'); // Snapshots READY
    await expect(meta.locator('dd').nth(2)).toContainText('#1'); // latest snapshot
    await expect(meta.locator('dd').nth(3)).toHaveText('5'); // Arquivos
    await expect(meta.locator('dd').nth(4)).toHaveText('9'); // Símbolos
    await expect(meta.locator('dd').nth(5)).toHaveText('4'); // Relações
    await expect(card).toContainText('cycle-6.1-explorer-hardening');
    await expect(card).toContainText('20bcdfee49b3');
    // With a single indexed repository the bootstrap auto-selects it — the
    // "Selecionar" path cannot be re-exercised (button is rendered only for
    // a non-active repo), so the assertable contract is the active state:
    // badge on the card, disabled "Selecionado" and the shell selector.
    await expect(card.locator('.repo-card__badge')).toHaveText('contexto ativo');
    await expect(card.getByRole('button', { name: 'Selecionado' })).toBeDisabled();
    await expect(page.locator('#shell-repo-select')).toHaveValue('1');
    await expect(page.locator('.shell__snapshot')).toContainText('mais recente');
  });

  test('12 — ?repo=&snapshot= deep link into /graph resolves; bogus repo warns', async ({
    page,
  }) => {
    await go(page, 
      `/graph?repo=${encodeURIComponent(REPO_ROOT_HANDLE)}&snapshot=1`,
    );
    // The global context honors the link: snapshot pinned, selector on repo.
    await expect(page.locator('.shell__snapshot')).toContainText('snapshot #1', {
      timeout: 10_000,
    });
    await expect(page.locator('#shell-repo-select')).toHaveValue('1');
    // The graph itself resolves to the real architecture view (5 files) —
    // canvas when WebGL is available, the accessible table otherwise.
    const stage = page.locator('.graph-stage');
    if (!(await waitGraphReady(page, /5 nós visíveis/))) {
      await page.getByRole('button', { name: 'Lista', exact: true }).click();
      await expect(page.locator('#nodes-caption')).toContainText('Nós (5)');
    }

    // A repo the index does not know must surface an explicit notice —
    // never a silently guessed context.
    await go(page, '/graph?repo=nao-existe&snapshot=1');
    await expect(page.locator('.shell__notice')).toContainText('não está indexado', {
      timeout: 10_000,
    });
  });

  /* ============================== search ============================== */

  test('13 — /search?q= deep-link auto-runs against the index', async ({ page }) => {
    await go(page, '/search?q=hello');
    const summary = page.locator('.search__summary');
    await expect(summary).toContainText('resultado(s)', { timeout: 10_000 });
    await expect(summary).toContainText('snapshot #1');
    // Real hits for "hello": the module, its file and the function.
    const hits = page.locator('li.search-hit');
    await expect(hits).toHaveCount(3);
    await expect(hits.locator('code.search-hit__qname')).toContainText([
      'src.hello',
      'src/hello.py',
      'src.hello.hello',
    ]);
    // The criteria stay visible in the form and in the address bar.
    await expect(page.locator('input[name="q"]')).toHaveValue('hello');
    await expect(page).toHaveURL(/[?&]q=hello/);
  });

  test('14 — typing syncs the URL; Inspector and graph links navigate', async ({
    page,
  }) => {
    await go(page, '/search');
    await page.locator('input[name="q"]').fill('util');
    // Debounced replaceUrl — the address bar mirrors the criteria.
    await expect(page).toHaveURL(/[?&]q=util/, { timeout: 5_000 });
    await page.getByRole('button', { name: /^Buscar$/ }).click();
    const hits = page.locator('li.search-hit');
    await expect(hits.first()).toBeVisible({ timeout: 10_000 });
    // The module hit specifically (src/util.py and src.util.util also
    // contain the substring — filter by the exact qualified name).
    const moduleHit = hits.filter({
      has: page.locator('code.search-hit__qname', { hasText: /^src\.util$/ }),
    });
    await expect(moduleHit).toHaveCount(1);

    // "Abrir no Inspector" lands on the real symbol page for the hit.
    await moduleHit.getByRole('link', { name: 'Abrir no Inspector' }).click();
    await expect(page).toHaveURL(/\/symbols\/7$/);
    await expect(page.locator('.symbol-detail')).toContainText('src.util');
    await page.goBack();

    // "Focar no grafo" deep-links the symbol-level graph at the occurrence.
    const hitsAgain = page.locator('li.search-hit');
    await expect(hitsAgain.first()).toBeVisible({ timeout: 10_000 });
    const functionHit = hitsAgain.filter({
      has: page.locator('code.search-hit__qname', { hasText: /^src\.util\.util$/ }),
    });
    await functionHit.getByRole('link', { name: 'Focar no grafo' }).click();
    await expect(page).toHaveURL(/\/graph\?.*focus=occ(%3A|:)9/);
    // occ:9 is a leaf function — its live neighborhood is src.util (module)
    // + the single incoming DEFINES edge. Verified against
    // /graph/neighbors?occurrence_id=9&direction=both.
    const stage = page.locator('.graph-stage');
    if (!(await waitGraphReady(page, /2 nós visíveis e 1 relação/))) {
      await page.getByRole('button', { name: 'Lista', exact: true }).click();
      await expect(page.locator('#edges-caption')).toContainText('Relações (1)');
    }
    // The focused occurrence arrives pre-selected in the inspector.
    await expect(page.locator('.inspector__title')).toHaveText('util', {
      timeout: 10_000,
    });
    await expect(page.locator('.inspector')).toContainText('src.util.util');
  });

  /* ============================== symbols ============================= */

  test('15 — /symbols/:id deep link renders real detail; malformed id degrades', async ({
    page,
  }) => {
    await go(page, '/symbols/8');
    const detail = page.locator('.symbol-detail');
    await expect(detail.locator('.card__title')).toHaveText('hello', {
      timeout: 10_000,
    });
    await expect(detail).toContainText('src.hello.hello');
    await expect(detail).toContainText('function');
    await expect(detail).toContainText('src/hello.py');
    await expect(detail).toContainText('python');
    await expect(detail).toContainText('ocorrência 8');
    // Grouped relations straight from the index: DEFINES ×1 (occ:6 → occ:8).
    await expect(detail).toContainText('Relações (1)');
    await expect(detail.locator('.relations')).toContainText('DEFINES');

    // Malformed ids never hit the API — the empty state is explicit.
    await go(page, '/symbols/not-a-number');
    await expect(page.getByText('Identificador de símbolo inválido.')).toBeVisible({
      timeout: 10_000,
    });
  });

  /* ============================== impact ============================== */

  test('16 — impact analysis runs on real revisions; JSON/Markdown export downloads', async ({
    page,
  }) => {
    await go(page, '/impact');
    const repoSelect = page.locator('select[name="repo"]');
    // The only indexed repository is offered as a real option.
    await expect(repoSelect.locator('option')).toHaveCount(2, { timeout: 10_000 });
    await repoSelect.selectOption({ index: 1 });
    await page.locator('input[name="base"]').fill('HEAD~1');
    await page.locator('input[name="target"]').fill('HEAD');
    await page.getByRole('button', { name: 'Analisar impacto' }).click();

    const result = page.locator('.impact-result');
    await expect(result).toBeVisible({ timeout: 15_000 });
    await expect(result).toContainText('mudança(s) de símbolo');
    await expect(result).toContainText('candidato(s) de impacto');
    // The fixture lives inside the parent git repo, so HEAD~1→HEAD is a
    // real diff; the backend flags the snapshot↔revision binding and the
    // blocked semantic pass honestly — those banners must render.
    await expect(result).toContainText('ChangeSet');
    await expect(result).toContainText('Análise parcial.');
    await expect(result).toContainText('Análise semântica bloqueada.');
    await expect(result).toContainText('Cobertura desconhecida');

    const [jsonDl] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: 'Exportar JSON' }).click(),
    ]);
    expect(jsonDl.suggestedFilename()).toBe('impact-analysis.json');
    const jsonPath = await jsonDl.path();
    const parsed = JSON.parse(readFileSync(jsonPath, 'utf-8')) as {
      schema_version?: number;
      change_set?: unknown;
    };
    expect(parsed.schema_version).toBe(1);
    expect(parsed.change_set).not.toBeNull();

    const [mdDl] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: 'Exportar Markdown' }).click(),
    ]);
    expect(mdDl.suggestedFilename()).toBe('impact-analysis.md');
    const mdPath = await mdDl.path();
    expect(readFileSync(mdPath, 'utf-8')).toContain('# Análise de impacto');
  });

  /* ============================ context pack =========================== */

  test('17 — tiny-budget pack renders clickable expansion-ref chips', async ({
    page,
  }) => {
    await go(page, '/context');
    // The "Padrão" option sends no repository_root and the backend answers
    // "resolve-failed" — the real flow needs the explicit repository.
    await page.locator('select[name="repo"]').selectOption({ index: 1 });
    await page.getByRole('textbox', { name: 'Tarefa' }).fill(
      'investigar util.py e hello.py',
    );
    await page.locator('select[name="level"]').selectOption('deep');
    // Smallest budget forces omissions → expansion refs appear.
    const budget = page.locator('input[name="budget"]');
    await budget.focus();
    await budget.press('Home');
    await page.getByRole('button', { name: 'Gerar pack' }).click();

    const pack = page.locator('.context__pack');
    await expect(pack).toBeVisible({ timeout: 15_000 });
    await expect(pack).toContainText('Omitidos pelo orçamento');
    const expansion = page.locator('#pack-expansion');
    await expect(expansion).toContainText('Pontos de expansão');

    // Every `sym:` ref becomes a real /search?q= deep link.
    const chip = pack.locator('a.context__chip', { hasText: 'src.util.util' });
    await expect(chip).toHaveAttribute('href', /\/search\?q=src\.util\.util/);
    await chip.click();
    await expect(page).toHaveURL(/\/search\?q=src\.util\.util/);
    // …and the landing page auto-runs the query (no dead-end navigation).
    await expect(page.locator('.search__summary')).toContainText('resultado(s)', {
      timeout: 10_000,
    });
  });

  test.describe('clipboard', () => {
    test.use({ permissions: ['clipboard-read', 'clipboard-write'] });

    test('18 — Copiar JSON writes the real pack; Exportar .json downloads it', async ({
      page,
    }) => {
      await go(page, '/context');
      await page.locator('select[name="repo"]').selectOption({ index: 1 });
      await page.getByRole('textbox', { name: 'Tarefa' }).fill(
        'investigar util.py e hello.py',
      );
      await page.getByRole('button', { name: 'Gerar pack' }).click();
      const pack = page.locator('.context__pack');
      await expect(pack).toBeVisible({ timeout: 15_000 });
      await expect(pack).toContainText('12 item(ns)');

      await page.getByRole('button', { name: 'Copiar JSON' }).click();
      await expect(page.locator('.context__copy-status')).toHaveText(
        'JSON copiado.',
      );
      const clip = await page.evaluate(() => navigator.clipboard.readText());
      const parsed = JSON.parse(clip) as { manifest?: { schema_version?: number } };
      expect(parsed.manifest?.schema_version).toBe(2);

      const [dl] = await Promise.all([
        page.waitForEvent('download'),
        page.getByRole('button', { name: 'Exportar .json' }).click(),
      ]);
      expect(dl.suggestedFilename()).toBe('context-pack.json');
      const exported = JSON.parse(readFileSync(await dl.path(), 'utf-8')) as {
        manifest?: { schema_version?: number; selected?: number };
      };
      expect(exported.manifest?.schema_version).toBe(2);
      expect(exported.manifest?.selected).toBe(12);
    });
  });

  /* =============================== graph ============================== */

  test('19 — architecture canvas renders real nodes; theme toggle keeps it alive', async ({
    page,
  }) => {
    await go(page, '/graph');
    const stage = page.locator('.graph-stage');
    const ok = await waitGraphReady(
      page,
      /Grafo com 5 nós visíveis e 0 relações/,
    );
    test.skip(!ok, 'WebGL indisponível — o fallback para a lista é o comportamento honesto');
    // sigma really mounted: canvases inside the stage.
    expect(await stage.locator('canvas').count()).toBeGreaterThan(0);

    // Node selection opens the inspector with real index fields — driven
    // by the documented keyboard path ([ ] cycles, Enter selects).
    await stage.focus();
    await page.keyboard.press(']');
    await page.keyboard.press('Enter');
    await expect(page.locator('.inspector__title')).toHaveText('util.py', {
      timeout: 10_000,
    });
    await expect(page.locator('.inspector')).toContainText('src/util.py');
    await expect(page.locator('.inspector')).toContainText('Símbolos');

    // Toggling the theme repaints the canvas — it must not be destroyed.
    const toggle = page.getByRole('button', { name: 'Alternar tema claro/escuro' });
    const before = await toggle.getAttribute('aria-pressed');
    const themeBefore = await page.evaluate(() =>
      document.documentElement.getAttribute('data-theme'),
    );
    await toggle.click();
    await expect(toggle).toHaveAttribute(
      'aria-pressed',
      before === 'true' ? 'false' : 'true',
    );
    await page.waitForFunction(
      (prev) => document.documentElement.getAttribute('data-theme') !== prev,
      themeBefore,
    );
    await expect(stage.locator('canvas').first()).toBeVisible();
    await expect(stage).toHaveAttribute('aria-label', /5 nós visíveis/);
    await expect(page.locator('.inspector__title')).toHaveText('util.py');
  });

  test('20 — edge selection shows relation evidence fields (focus mode)', async ({
    page,
  }) => {
    await go(page, '/graph?focus=occ:6');
    // The table exposes the same selection actions as the canvas — it is
    // the deterministic path regardless of WebGL availability.
    await page.getByRole('button', { name: 'Lista', exact: true }).click();
    await expect(page.locator('#edges-caption')).toContainText('Relações (2)', {
      timeout: 15_000,
    });
    const edgeRows = page.locator('.tables__section').nth(1).locator('tbody tr');
    await expect(edgeRows).toHaveCount(2);

    // Selecting the DEFINES edge opens its evidence record in the
    // inspector. The click must be scoped to the table row — the toolbar
    // also hosts a "DEFINES" filter chip earlier in DOM order.
    const defRow = edgeRows.filter({ hasText: 'DEFINES' });
    await defRow.getByRole('button', { name: 'DEFINES', exact: true }).click();
    const inspector = page.locator('.inspector');
    await expect(inspector.locator('.inspector__title')).toHaveText('Relação');
    await expect(inspector).toContainText('DEFINES');
    await expect(inspector).toContainText('Estado de evidência');
    await expect(inspector).toContainText('resolved');
    await expect(inspector).toContainText('Classe de confiança');
    await expect(inspector).toContainText('syntactic');
    await expect(inspector).toContainText('tree-sitter-python');
    // relations.evidence_ids=[1] — the proof is rendered, not invented.
    await expect(inspector).toContainText('Evidências');
    await expect(inspector.locator('.field--mono').last()).toHaveText('1');
    await expect(inspector).toContainText('hello (occ:6)');
    await expect(inspector).toContainText('hello (occ:8)');

    // Jumping from the edge to an endpoint selects that node.
    await inspector.getByRole('button', { name: 'hello (occ:8)' }).click();
    await expect(inspector.locator('.inspector__title')).toHaveText('hello');
    await expect(inspector).toContainText('src.hello.hello');
    await expect(inspector).toContainText('function');
  });

  test('21 — drill-down fires /files/{ref}/occurrences, never a filename search', async ({
    page,
  }) => {
    let searchCalls = 0;
    page.on('request', (req) => {
      if (req.url().includes('/api/v1/search')) searchCalls++;
    });
    await go(page, '/graph');
    await page.getByRole('button', { name: 'Lista', exact: true }).click();
    await expect(page.locator('#nodes-caption')).toContainText('Nós (5)', {
      timeout: 15_000,
    });

    const fileRow = page.locator('tbody tr', { hasText: 'src/util.py' });
    await expect(fileRow).toHaveCount(1);
    const occurrencesReq = page.waitForRequest(
      /\/api\/v1\/files\/[^/]+\/occurrences/,
      { timeout: 10_000 },
    );
    await fileRow.getByRole('button', { name: 'Detalhar' }).click();
    const req = await occurrencesReq;
    // The drill uses the occurrences endpoint — the old filename-search
    // heuristic is gone for good.
    expect(req.url()).toContain('/occurrences');
    expect(searchCalls).toBe(0);

    // Terminal state is honest either way: the cycle-6.1 backend omits
    // file_id from /graph/overview, so the path-based ref 404s and the UI
    // shows the explicit "endpoint unavailable" empty state instead of
    // fabricating nodes. If a future backend emits file_id, the drilled
    // view shows the file's occurrences — both are asserted as real
    // outcomes, no middle ground accepted.
    const emptyMsg = page.getByText(/não está disponível nesta versão da API/);
    const drilledBanner = page.getByText(/ocorrências indexadas no snapshot/);
    await expect(emptyMsg.or(drilledBanner)).toBeVisible({ timeout: 10_000 });
    // And the breadcrumb records where the drill went.
    await expect(
      page.getByRole('navigation', { name: 'Navegação estrutural' }),
    ).toContainText('src/util.py');
  });

  test('22 — zoom/pan keep the canvas alive; returning to /graph restores the camera', async ({
    page,
  }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await go(page, '/graph');
    const stage = page.locator('.graph-stage');
    const ok = await waitGraphReady(
      page,
      /Grafo com 5 nós visíveis e 0 relações/,
    );
    test.skip(!ok, 'WebGL indisponível neste ambiente');
    await page.waitForTimeout(1500); // layout + fit settle
    const shotFit = await stage.screenshot();

    // Zoom in twice, pan, select a node — the frame must actually change.
    await page.getByRole('button', { name: 'Aumentar zoom' }).click();
    await page.getByRole('button', { name: 'Aumentar zoom' }).click();
    await stage.focus();
    await page.keyboard.press('ArrowLeft');
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press(']');
    await page.keyboard.press('Enter');
    await expect(page.locator('.inspector__title')).toHaveText('util.py', {
      timeout: 10_000,
    });
    await page.waitForTimeout(600);
    const shotZoom = await stage.screenshot();
    const dZoom = await pngDiffRatio(page, shotFit, shotZoom);
    expect(dZoom).toBeGreaterThan(0.01); // camera provably moved
    expect(await stage.locator('canvas').count()).toBeGreaterThan(0);

    // "Ajustar" returns to the deterministic fit — near-identical frame.
    await page.getByRole('button', { name: 'Ajustar' }).click();
    await page.waitForTimeout(600);
    const shotFit2 = await stage.screenshot();
    expect(await pngDiffRatio(page, shotFit, shotFit2)).toBeLessThan(0.015);

    // Re-apply the moved camera (selection from the first leg persists —
    // "Ajustar" only refits the viewport), then navigate away and back —
    // the persisted view state must restore camera AND selection.
    await page.getByRole('button', { name: 'Aumentar zoom' }).click();
    await page.getByRole('button', { name: 'Aumentar zoom' }).click();
    await stage.focus();
    await page.keyboard.press('ArrowLeft');
    await page.keyboard.press('ArrowDown');
    await page.waitForTimeout(600);
    const shotMoved = await stage.screenshot();

    await page.getByRole('link', { name: 'Painel' }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
    await page.getByRole('link', { name: 'Grafo' }).click();
    await expect(stage).toHaveAttribute('aria-label', /5 nós visíveis/, {
      timeout: 15_000,
    });
    await page.waitForTimeout(2000); // layout + camera restore settle

    // Selection restore is DOM-observable proof the saved view was applied.
    await expect(page.locator('.inspector__title')).toHaveText('util.py', {
      timeout: 10_000,
    });
    const shotBack = await stage.screenshot();
    const dBack = await pngDiffRatio(page, shotMoved, shotBack);
    // The restored frame is closer to the pre-navigation camera than the
    // default fit ever was — "roughly preserved", not a re-fit.
    expect(dBack).toBeLessThan(dZoom);

    // Negative control: a real reload wipes the in-memory view state, so
    // the same page returns to the deterministic fit instead.
    await page.reload();
    await expect(stage).toHaveAttribute('aria-label', /5 nós visíveis/, {
      timeout: 15_000,
    });
    await page.waitForTimeout(2000);
    const shotReload = await stage.screenshot();
    expect(await pngDiffRatio(page, shotFit, shotReload)).toBeLessThan(0.015);
    await expect(page.locator('.inspector__title')).toHaveCount(0);
  });

  /* =============================== shell ============================== */

  test('23 — sidebar collapse persists across reload', async ({ page }) => {
    await go(page, '/dashboard');
    const toggle = page.getByRole('button', { name: 'Ocultar navegação' });
    const nav = page.getByRole('navigation', { name: 'Navegação principal' });
    await expect(nav).toBeVisible();
    await toggle.click();
    await expect(nav).toBeHidden();
    await expect(
      page.getByRole('button', { name: 'Mostrar navegação' }),
    ).toHaveAttribute('aria-expanded', 'false');

    await page.reload();
    await expect(
      page.getByRole('button', { name: 'Mostrar navegação' }),
    ).toHaveAttribute('aria-expanded', 'false');
    await expect(
      page.getByRole('navigation', { name: 'Navegação principal' }),
    ).toBeHidden();

    // Restore for symmetry — and because the toggle itself must still work.
    await page.getByRole('button', { name: 'Mostrar navegação' }).click();
    await expect(
      page.getByRole('navigation', { name: 'Navegação principal' }),
    ).toBeVisible();
  });

  /* ==================== API kill / restart (real) ===================== */

  test('24 — killing the explorer subprocess produces the offline state; restart recovers', async ({
    page,
  }) => {
    // Runs on a dedicated ephemeral port so the shared suite server is
    // never touched — no ordering constraints, no leaked processes.
    const dataDir = process.env['ATLAS_E2E_DATA_DIR'];
    test.skip(
      !dataDir,
      'ATLAS_E2E_DATA_DIR não propagado — workspace do índice desconhecido',
    );

    const port = await freePort();
    const base = `http://127.0.0.1:${port}`;
    let proc = spawnExplorer(port, dataDir!);
    try {
      await waitForServer(`${base}/api/v1/repositories`, 15_000);
      await page.goto(`${base}/repositories`);
      await expect(page.locator('.repo-card')).toBeVisible({ timeout: 10_000 });

      // Real kill — not a route stub. The SPA shell is already loaded, so
      // the in-page "Atualizar" exercises the unreachable-API path.
      proc.kill('SIGTERM');
      await waitForDown(`${base}/api/v1/repositories`, 10_000);
      await page.getByRole('button', { name: 'Atualizar' }).click();
      await expect(
        page.getByText('API local indisponível', { exact: false }),
      ).toBeVisible({ timeout: 10_000 });

      // Real restart on the same port + same index: data comes back.
      proc = spawnExplorer(port, dataDir!);
      await waitForServer(`${base}/api/v1/repositories`, 15_000);
      await page.getByRole('button', { name: 'Atualizar' }).click();
      await expect(page.locator('.repo-card')).toBeVisible({ timeout: 10_000 });
      await expect(page.locator('.repo-card')).toContainText('sample_repo');
    } finally {
      try {
        proc.kill('SIGTERM');
      } catch {
        /* já morto */
      }
    }
  });
});

/* --------------------------- process helpers -------------------------- */

const REPO_ROOT = join(__dirname, '..', '..');

function pythonBin(): string {
  if (process.env['ATLAS_PYTHON']) return process.env['ATLAS_PYTHON'];
  const venv = join(REPO_ROOT, '.venv', 'bin', 'python');
  if (existsSync(venv)) return venv;
  return 'python3';
}

function spawnExplorer(port: number, dataDir: string): ChildProcess {
  return spawn(
    pythonBin(),
    ['-m', 'code_atlas', 'explorer', '--port', String(port)],
    {
      cwd: REPO_ROOT,
      env: { ...process.env, CODE_ATLAS_DATA_DIR: dataDir },
      stdio: 'ignore',
    },
  );
}

async function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = createServer();
    srv.once('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const addr = srv.address();
      const port = typeof addr === 'object' && addr ? addr.port : 0;
      srv.close(() => (port ? resolve(port) : reject(new Error('sem porta livre'))));
    });
  });
}

async function waitForServer(url: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  let lastErr: unknown;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url);
      if (res.status) return;
    } catch (err) {
      lastErr = err;
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`explorer não subiu em ${url}: ${String(lastErr)}`);
}

async function waitForDown(url: string, timeoutMs: number): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      await fetch(url);
    } catch {
      return; // conexão recusada — servidor realmente fora do ar
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  throw new Error(`servidor ainda responde em ${url} após SIGTERM`);
}
