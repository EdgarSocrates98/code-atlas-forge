import { test, expect, Page } from '@playwright/test';

/**
 * Cross-browser homologation smoke (Cycle 8.1, Phase 6 — lf-0149).
 *
 * Minimal contract per browser engine: the shell loads, a data-bearing
 * route renders against the real API, and the session produces zero
 * uncaught page errors and zero console.error calls.
 *
 * This spec is intentionally small — it is the only file matched by the
 * `firefox`/`webkit` projects in playwright.config.ts (opt-in via
 * `ATLAS_E2E_XBROWSER=1`). Full-suite runs on non-Chromium engines are a
 * separate (documented) NOT RUN.
 */

type Surface = 'canvas' | 'table' | 'none';

/** Collect uncaught exceptions + console.error calls for a test. */
function watchPage(page: Page): { pageErrors: string[]; consoleErrors: string[] } {
  const pageErrors: string[] = [];
  const consoleErrors: string[] = [];
  page.on('pageerror', (e) => pageErrors.push(String(e)));
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(msg.text());
  });
  return { pageErrors, consoleErrors };
}

function expectClean(collected: { pageErrors: string[]; consoleErrors: string[] }): void {
  expect(collected.pageErrors, 'uncaught page errors').toEqual([]);
  expect(collected.consoleErrors, 'console.error calls').toEqual([]);
}

/**
 * Same surface contract as graph-dense.spec.ts: the graph route must end
 * on a real surface (sigma canvas with populated aria-label or the
 * accessible table) — never on an error/offline state block.
 */
async function waitForGraphSurface(page: Page, timeout = 30_000): Promise<Surface> {
  const handle = await page.waitForFunction(
    () => {
      const stage = document.querySelector('.graph-stage');
      if (stage && /\d+ nós visíveis/.test(stage.getAttribute('aria-label') ?? '')) {
        return 'canvas';
      }
      if (document.querySelector('atlas-graph-table tbody tr')) return 'table';
      const block = document.querySelector('atlas-state-block');
      const state = block?.getAttribute('state') ?? '';
      if (block && state !== 'loading') return 'none';
      return false;
    },
    undefined,
    { polling: 'raf', timeout },
  );
  return (await handle.jsonValue()) as Surface;
}

test.describe('cross-browser smoke', () => {
  test('app loads — shell renders with main navigation', async ({ page }) => {
    const collected = watchPage(page);
    await page.goto('/');
    await expect(page.getByText('Code Atlas Explorer')).toBeVisible();
    await expect(
      page.getByRole('navigation', { name: 'Navegação principal' }),
    ).toBeVisible();
    await expect(
      page.getByText('API local indisponível', { exact: false }),
    ).not.toBeVisible();
    expectClean(collected);
  });

  test('graph route renders a real surface (canvas or table)', async ({ page }) => {
    const collected = watchPage(page);
    await page.goto('/graph');
    await expect(page.getByRole('heading', { name: 'Grafo' })).toBeVisible();
    const surface = await waitForGraphSurface(page);
    // 'none' means an error/offline block — the API is really up, so that
    // is a genuine render failure on this engine, not a data condition.
    expect(surface, 'graph surface').not.toBe('none');
    expectClean(collected);
  });

  test('search executes against the real API without errors', async ({ page }) => {
    const collected = watchPage(page);
    await page.goto('/search');
    await expect(page.getByRole('heading', { name: 'Busca' })).toBeVisible();
    await page.getByLabel('Consulta').fill('hello');
    await page.getByRole('button', { name: /^Buscar$/ }).click();
    // Terminal state must not be the offline error — same contract as
    // flows.spec.ts 03 (result summary, empty state or error block are all
    // acceptable engine-level outcomes).
    await expect(
      page.getByText('API local indisponível', { exact: false }),
    ).not.toBeVisible({ timeout: 10_000 });
    expectClean(collected);
  });
});
