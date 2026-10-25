import { test, expect, request } from '@playwright/test';
import type { APIRequestContext, Locator, Page } from '@playwright/test';
import { AxeBuilder } from '@axe-core/playwright';
import type { AxeResults, Result } from 'axe-core';
import {
  bootDomainFixtureServer,
  type DomainFixtureServer,
} from './domain-fixture-server';

/**
 * WCAG 2.2 AA accessibility audit (Phase 11 + cycle-8 Phase 14). Runs
 * axe-core against every main route in BOTH themes (light/dark, driven
 * by the `atlas-theme` localStorage key that ThemeService persists) plus
 * keyboard/focus/reduced-motion/zoom checks that axe cannot cover.
 *
 * Runs against the real `code-atlas explorer` subprocess serving the
 * compiled production build (see global-setup.ts). No mocks.
 *
 * The cycle-7 domain views (`/lineage`, `/contracts`, `/infra`,
 * `/pipelines`, `/federation`) are audited against a dedicated second
 * explorer instance (`domain-fixture-server.ts`) whose data dir indexes
 * the real domain fixtures — closing the cycle-7 NOT-RUN "new domain
 * screens lacked dedicated axe scans" without touching the single-repo
 * state other specs assert on.
 */

const BASE = process.env['ATLAS_E2E_BASE_URL'] ?? 'http://127.0.0.1:8743';

/** Every main route except /symbols/:id (needs a live occurrence id). */
const ROUTES = [
  '/dashboard',
  '/repositories',
  '/graph',
  '/search',
  '/impact',
  '/revisions',
  '/context',
  '/diagnostics',
  '/settings',
] as const;

const THEMES = ['light', 'dark'] as const;
type ThemeName = (typeof THEMES)[number];

/** WCAG 2.0/2.1/2.2 A+AA tags + axe best-practice (reported, not blocking). */
const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa', 'best-practice'];

/** Violations that block the audit. */
const BLOCKING_IMPACTS = new Set(['critical', 'serious']);

interface RouteScan {
  route: string;
  theme: ThemeName;
  results: AxeResults;
}

/** Collected across the whole run; summarized in the final aggregate test. */
const SCANS: RouteScan[] = [];

/** Ask the real API for an occurrence id so /symbols/{id} can be audited. */
async function discoverSymbolRoute(ctx: APIRequestContext): Promise<string | null> {
  try {
    const res = await ctx.fetch(`${BASE}/api/v1/search?q=hello&limit=5`);
    if (!res.ok()) return null;
    const body = (await res.json()) as { items?: { occurrence_id?: number }[] };
    const id = body.items?.find((hit) => Number.isInteger(hit.occurrence_id))?.occurrence_id;
    return id ? `/symbols/${id}` : null;
  } catch {
    return null;
  }
}

/** Persist the theme before the app boots; ThemeService reads it on init. */
async function setTheme(page: Page, theme: ThemeName): Promise<void> {
  await page.addInitScript((t) => {
    try {
      window.localStorage.setItem('atlas-theme', t);
    } catch {
      /* storage unavailable — service falls back to system */
    }
  }, theme);
}

/** Wait for the route's terminal state: heading up, no loading block. */
async function waitForSettled(page: Page): Promise<void> {
  await page.waitForLoadState('networkidle').catch(() => {});
  await expect(page.locator('h1').first()).toBeVisible();
  // The shared state block shows a spinner while the local API answers;
  // scanning mid-load would audit the loading UI, not the real screen.
  await expect(page.locator('.state-block__panel--loading')).toHaveCount(0, {
    timeout: 10_000,
  });
}

function formatViolations(violations: Result[]): string {
  if (violations.length === 0) return 'none';
  return violations
    .map((v) => {
      const targets = v.nodes
        .slice(0, 4)
        .map((n) => `      ${n.target.join(' ')} — ${n.failureSummary?.split('\n')[0] ?? ''}`)
        .join('\n');
      return `  [${v.impact}] ${v.id}: ${v.help} (${v.helpUrl})\n${targets}`;
    })
    .join('\n');
}

/** Run axe on the page's current state and assert no blocking violations. */
async function axeScan(page: Page, label: string, theme: ThemeName): Promise<void> {
  const results = await new AxeBuilder({ page }).withTags(AXE_TAGS).analyze();
  SCANS.push({ route: label, theme, results });
  await test.info().attach(`axe-${theme}-${label.replace(/\//g, '_')}`, {
    body: JSON.stringify(
      results.violations.map((v) => ({
        id: v.id,
        impact: v.impact,
        tags: v.tags,
        help: v.help,
        nodes: v.nodes.map((n) => n.target.join(' ')),
      })),
      null,
      2,
    ),
    contentType: 'application/json',
  });

  const blocking = results.violations.filter((v) => v.impact && BLOCKING_IMPACTS.has(v.impact));
  expect(
    blocking,
    `axe found ${blocking.length} blocking violation(s) on ${label}:\n${formatViolations(blocking)}`,
  ).toEqual([]);
}

async function runAxeScan(page: Page, route: string, theme: ThemeName): Promise<void> {
  await setTheme(page, theme);
  await page.goto(route);
  await waitForSettled(page);
  // The theme preference is read on bootstrap — confirm it really applied
  // so a scan is never silently run under the wrong palette.
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  await axeScan(page, `${route} [${theme}]`, theme);
}

/**
 * Same contract as runAxeScan but for the dedicated domain-fixture
 * server: `url` is absolute (page.goto resolves it as-is), `label` is the
 * human label recorded in the audit summary, and `settle` waits for the
 * specific populated state being audited (never scans mid-load).
 */
async function runDomainScan(
  page: Page,
  url: string,
  theme: ThemeName,
  label: string,
  settle: () => Promise<void>,
): Promise<void> {
  await setTheme(page, theme);
  await page.goto(url);
  await waitForSettled(page);
  await settle();
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  await axeScan(page, `${label} [${theme}]`, theme);
}

/** Same overflow probe as e2e/screenshots.spec.ts (kept in sync). */
function measureHorizontalOverflow(): Promise<{ overflowPx: number; offenders: string[] }> {
  return new Promise((resolve) => {
    const doc = document.documentElement;
    const vw = doc.clientWidth;
    const overflowPx = Math.max(doc.scrollWidth, document.body.scrollWidth) - vw;
    const offenders: string[] = [];
    const containedByScroll = (el: HTMLElement): boolean => {
      let ancestor = el.parentElement;
      while (ancestor) {
        // Contained only inside a real clip/scroll container — an ancestor
        // that merely overflows visibly propagates the page-level bug.
        const ox = getComputedStyle(ancestor).overflowX;
        if (
          (ox === 'auto' || ox === 'scroll' || ox === 'hidden' || ox === 'clip') &&
          ancestor.scrollWidth > ancestor.clientWidth + 1
        ) {
          return true;
        }
        ancestor = ancestor.parentElement;
      }
      return false;
    };
    if (overflowPx > 1) {
      for (const el of Array.from(document.querySelectorAll<HTMLElement>('body *'))) {
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        const escapesRight = r.right > vw + 1;
        // Content visibly escaping the element's own box (unbreakable
        // text token) propagates to page overflow even when the element's
        // rect stays inside the viewport.
        const contentEscapes =
          cs.display !== 'inline' &&
          cs.overflowX === 'visible' &&
          el.scrollWidth > el.clientWidth + 1;
        if (!escapesRight && !contentEscapes) continue;
        if (containedByScroll(el)) continue;
        const cls = (el.className?.toString() ?? '').trim().split(/\s+/)[0] ?? '';
        offenders.push(
          `${el.tagName.toLowerCase()}${cls ? '.' + cls : ''}` +
            `${escapesRight ? ` right=${Math.round(r.right)}px` : ''}` +
            `${contentEscapes ? ` content-overflows-by=${el.scrollWidth - el.clientWidth}px` : ''}`,
        );
        if (offenders.length >= 8) break;
      }
    }
    resolve({ overflowPx, offenders });
  });
}

test.describe('WCAG 2.2 AA — axe scan, every route × theme', () => {
  for (const theme of THEMES) {
    for (const route of ROUTES) {
      test(`axe ${route} [${theme}]`, async ({ page }) => {
        await runAxeScan(page, route, theme);
      });
    }

    test(`axe /symbols/{id} [${theme}]`, async ({ page }) => {
      const ctx = await request.newContext();
      const route = await discoverSymbolRoute(ctx);
      await ctx.dispose();
      test.skip(!route, 'fixture index produced no occurrence id for /symbols/{id}');
      await runAxeScan(page, route as string, theme);
    });

    // Post-interaction scans: the results regions below only mount after a
    // real API round-trip, so they are never present in the idle-route scans.
    test(`axe /search with live results [${theme}]`, async ({ page }) => {
      await setTheme(page, theme);
      await page.goto('/search');
      await waitForSettled(page);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await page.getByLabel('Consulta').fill('hello');
      await page.getByRole('button', { name: /^Buscar$/ }).click();
      // Either the results region or an explicit empty/error block must
      // settle — the API is really up, so a hang is a real failure.
      await expect(
        page.locator('.search__results, .state-block__panel--empty, .state-block__panel--error').first(),
      ).toBeVisible({ timeout: 10_000 });
      await axeScan(page, `/search#results`, theme);
    });

    test(`axe /context with generated pack [${theme}]`, async ({ page }) => {
      await setTheme(page, theme);
      await page.goto('/context');
      await waitForSettled(page);
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
      await page.getByRole('textbox', { name: 'Tarefa' }).fill('auditar util.py e hello.py');
      await page.getByRole('button', { name: /Gerar pack/ }).click();
      await expect(
        page.locator('.context__pack, .state-block__panel--empty, .state-block__panel--error').first(),
      ).toBeVisible({ timeout: 10_000 });
      await axeScan(page, `/context#pack`, theme);
    });
  }

});

test.describe('manual checks — keyboard, focus, motion', () => {
  test('skip link is first tab stop and moves focus to main', async ({ page }) => {
    await page.goto('/dashboard');
    await waitForSettled(page);
    await page.keyboard.press('Tab');
    const skip = page.locator('.shell__skip-link');
    await expect(skip).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.locator('#shell-main')).toBeFocused();
  });

  test('focus-visible renders a visible outline on interactive elements', async ({ page }) => {
    await page.goto('/dashboard');
    await waitForSettled(page);
    // Tab reaches the skip link first — it must get the visible outline.
    await page.keyboard.press('Tab');
    const skip = page.locator('.shell__skip-link');
    await expect(skip).toBeFocused();
    const skipOutline = await skip.evaluate((el) => {
      const s = getComputedStyle(el);
      return { style: s.outlineStyle, width: s.outlineWidth };
    });
    expect(skipOutline.style).not.toBe('none');
    expect(parseFloat(skipOutline.width)).toBeGreaterThan(0);

    // A sidebar nav link focused via keyboard must also show the ring.
    const navLink = page
      .getByRole('navigation', { name: 'Navegação principal' })
      .getByRole('link', { name: 'Busca' });
    await navLink.focus();
    const linkOutline = await navLink.evaluate((el) => {
      const s = getComputedStyle(el);
      return { style: s.outlineStyle, width: s.outlineWidth };
    });
    expect(linkOutline.style).not.toBe('none');
    expect(parseFloat(linkOutline.width)).toBeGreaterThan(0);
  });

  // Cycle-8.1 (lf-0150): the Angular 18→22 migration rewrote the compiled
  // bundle — re-verify the :focus-visible ring on the two interactive
  // surfaces the original suite only covered via the sidebar/skip-link:
  // the /search form field and the /graph canvas toolbar controls.
  test('focus ring is visible on /search input and /graph toolbar controls', async ({
    page,
  }) => {
    const outlineOf = async (loc: Locator) =>
      loc.evaluate((el) => {
        const s = getComputedStyle(el);
        return { style: s.outlineStyle, width: s.outlineWidth };
      });

    await page.goto('/search');
    await waitForSettled(page);
    // The query input is reachable by keyboard — walk the tab order until
    // focus lands on it (same contract as the lineage form check).
    const query = page.getByLabel('Consulta');
    let reached = false;
    for (let i = 0; i < 60 && !reached; i++) {
      await page.keyboard.press('Tab');
      reached = await query.evaluate((el) => document.activeElement === el);
    }
    expect(reached, 'tab order never reached the /search query input').toBe(true);
    const inputOutline = await outlineOf(query);
    expect(inputOutline.style).not.toBe('none');
    expect(parseFloat(inputOutline.width)).toBeGreaterThan(0);

    await page.goto('/graph');
    await waitForSettled(page);
    // Canvas toolbar is always rendered (seg buttons + caption input);
    // keyboard focus must paint the visible ring on a real button.
    const zoomIn = page.getByRole('button', { name: 'Aumentar zoom' });
    await zoomIn.focus();
    const btnOutline = await outlineOf(zoomIn);
    expect(btnOutline.style).not.toBe('none');
    expect(parseFloat(btnOutline.width)).toBeGreaterThan(0);
    // And the control is operable: Enter must not error and the canvas
    // stage (or accessible table fallback) stays attached.
    await page.keyboard.press('Enter');
    await expect(
      page.locator('.graph-stage, .tables__section table').first(),
    ).toBeAttached({ timeout: 10_000 });
  });

  test('keyboard-only navigation traverses the sidebar and activates a route', async ({
    page,
  }) => {
    await page.goto('/dashboard');
    await waitForSettled(page);
    // Walk the tab order until focus lands inside the sidebar nav; the
    // sidebar must be reachable without a mouse.
    const sidebar = page.getByRole('navigation', { name: 'Navegação principal' });
    let inside = false;
    for (let i = 0; i < 40 && !inside; i++) {
      await page.keyboard.press('Tab');
      inside = await page.evaluate(
        (nav) => nav !== null && nav.contains(document.activeElement),
        await sidebar.elementHandle(),
      );
    }
    expect(inside, 'tab order never reached the sidebar navigation').toBe(true);
    // Keep tabbing to the "Busca" link, then activate with Enter.
    const searchLink = sidebar.getByRole('link', { name: 'Busca' });
    await searchLink.focus();
    await page.keyboard.press('Enter');
    await expect(page).toHaveURL(/\/search$/);
  });

  test('prefers-reduced-motion neutralizes transitions/animations and the app still works', async ({
    page,
  }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/dashboard');
    await waitForSettled(page);
    expect(
      await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches),
    ).toBe(true);
    // Design tokens collapse every motion duration to 0ms under reduce —
    // the body theme transition must compute to 0s.
    const bodyTransition = await page.evaluate(
      () => getComputedStyle(document.body).transitionDuration,
    );
    const durations = bodyTransition.split(',').map((s) => s.trim());
    expect(durations.length).toBeGreaterThan(0);
    expect(durations.every((d) => d === '0s')).toBe(true);
    // Core shell still usable: nav works and a route change renders.
    await page.getByRole('link', { name: 'Grafo' }).click();
    await expect(page.getByRole('heading', { name: 'Grafo' })).toBeVisible();
    await waitForSettled(page);
    // Either the WebGL stage or the accessible table fallback renders —
    // reduced motion must not break graph loading.
    await expect(
      page.locator('.graph-stage, .tables__section table').first(),
    ).toBeAttached({ timeout: 10_000 });
  });
});

/* ---------------------------------------------------------------------------
 * Cycle-8 Phase 14 — domain views audit (cycle-7 routes).
 *
 * All checks below run against a dedicated explorer instance serving an
 * index where the real domain fixtures were indexed with `--domains`
 * (see ./domain-fixture-server.ts): /lineage → sql_warehouse,
 * /contracts → api_repo, /infra → terraform_infra, /pipelines → cicd_repo,
 * /federation → a real `e2e-ws` workspace with two members. Empty-state
 * scans point at sample_repo on the same server (no domain entities).
 * Closes the cycle-7 NOT-RUN "new domain screens lacked dedicated axe
 * scans" (L6 / homologation item 25).
 * ------------------------------------------------------------------------- */
test.describe('domain views — populated fixture server', () => {
  let server: DomainFixtureServer;
  /** A data entity whose /domains/lineage returns ≥1 edge (API-verified). */
  let lineageEntityId: number | null = null;
  /** An api_endpoint with ≥1 inbound CALLS_ENDPOINT edge, if any exists. */
  let consumerEndpointId: number | null = null;

  test.beforeAll(async () => {
    test.setTimeout(240_000); // 5 real index runs + federation + server boot
    server = await bootDomainFixtureServer();

    const ctx = await request.newContext();
    try {
      // Pick a lineage root that actually produces edges — the populated
      // scan must audit real rows, not an empty result.
      const entities = (await (
        await ctx.fetch(
          `${server.baseUrl}/api/v1/domains/entities?domain=sql&kind=data_table,data_view,data_transformation&repository_id=${server.repos.sql}&limit=200`,
        )
      ).json()) as { items?: { id: number; kind: string }[] };
      const ordered = [...(entities.items ?? [])].sort(
        (a, b) => (a.kind === 'data_table' ? 0 : 1) - (b.kind === 'data_table' ? 0 : 1),
      );
      for (const entity of ordered) {
        const res = await ctx.fetch(
          `${server.baseUrl}/api/v1/domains/lineage?table=${entity.id}&repository_id=${server.repos.sql}`,
        );
        if (!res.ok()) continue;
        const body = (await res.json()) as { edges?: unknown[] };
        if ((body.edges?.length ?? 0) > 0) {
          lineageEntityId = entity.id;
          break;
        }
      }
      lineageEntityId ??= ordered[0]?.id ?? null;

      // Pick an endpoint that has real CALLS_ENDPOINT consumers so the
      // expanded-disclosure scan audits a populated list when one exists.
      const endpoints = (await (
        await ctx.fetch(
          `${server.baseUrl}/api/v1/domains/entities?domain=api&kind=api_endpoint&repository_id=${server.repos.api}&limit=200`,
        )
      ).json()) as { items?: { id: number }[] };
      for (const ep of endpoints.items ?? []) {
        const res = await ctx.fetch(
          `${server.baseUrl}/api/v1/domains/entities/${ep.id}/neighbors?direction=in&repository_id=${server.repos.api}`,
        );
        if (!res.ok()) continue;
        const body = (await res.json()) as { edges?: { type?: string }[] };
        if ((body.edges ?? []).some((e) => e.type === 'CALLS_ENDPOINT')) {
          consumerEndpointId = ep.id;
          break;
        }
      }
    } finally {
      await ctx.dispose();
    }
  });

  test.afterAll(() => {
    server?.stop();
  });

  /* --------------------------- axe scans -------------------------------- */

  for (const theme of THEMES) {
    test(`axe /lineage populated [${theme}]`, async ({ page }) => {
      await runDomainScan(
        page,
        `${server.baseUrl}/lineage?repo=${server.repos.sql}`,
        theme,
        `/lineage`,
        async () => {
          // Form populated: entity options beyond the placeholder.
          await expect(
            page.locator('select[name="table"] option').nth(1),
          ).toBeAttached({ timeout: 10_000 });
        },
      );
    });

    test(`axe /lineage with selected entity [${theme}]`, async ({ page }) => {
      test.skip(
        lineageEntityId === null,
        'sql fixture produced no lineage entity id',
      );
      await runDomainScan(
        page,
        `${server.baseUrl}/lineage?repo=${server.repos.sql}&table=${lineageEntityId}`,
        theme,
        `/lineage#result`,
        async () => {
          await expect(page.locator('.lineage__result')).toBeVisible({
            timeout: 10_000,
          });
          await expect(page.locator('.lineage__edges')).toBeAttached();
        },
      );
    });

    test(`axe /contracts populated [${theme}]`, async ({ page }) => {
      await runDomainScan(
        page,
        `${server.baseUrl}/contracts?repo=${server.repos.api}`,
        theme,
        `/contracts`,
        async () => {
          await expect(page.locator('.contracts__table').first()).toBeAttached({
            timeout: 10_000,
          });
        },
      );
    });

    test(`axe /contracts with consumers expanded [${theme}]`, async ({ page }) => {
      await runDomainScan(
        page,
        `${server.baseUrl}/contracts?repo=${server.repos.api}`,
        theme,
        `/contracts#consumers`,
        async () => {
          await expect(page.locator('.contracts__table').first()).toBeAttached({
            timeout: 10_000,
          });
          const toggle = (
            consumerEndpointId !== null
              ? page.locator(
                  `.contracts__consumers-toggle[aria-controls="consumers-${consumerEndpointId}"]`,
                )
              : page.locator('.contracts__consumers-toggle')
          ).first();
          await toggle.click();
          const cell = page.locator('td', { has: toggle });
          // Settle on the terminal consumer state — list, explicit empty,
          // or error — never the transient `role="status"` loading text.
          await expect(
            cell
              .locator(
                '.contracts__consumers, p.contracts__error, p.contracts__muted:not([role="status"])',
              )
              .first(),
          ).toBeAttached({ timeout: 10_000 });
        },
      );
    });

    test(`axe /infra populated [${theme}]`, async ({ page }) => {
      await runDomainScan(
        page,
        `${server.baseUrl}/infra?repo=${server.repos.terraform}`,
        theme,
        `/infra`,
        async () => {
          await expect(page.locator('.infra__table').first()).toBeAttached({
            timeout: 10_000,
          });
        },
      );
    });

    test(`axe /pipelines populated [${theme}]`, async ({ page }) => {
      await runDomainScan(
        page,
        `${server.baseUrl}/pipelines?repo=${server.repos.cicd}`,
        theme,
        `/pipelines`,
        async () => {
          await expect(page.locator('.pipelines__workflow').first()).toBeAttached(
            { timeout: 10_000 },
          );
        },
      );
    });

    test(`axe /federation populated [${theme}]`, async ({ page }) => {
      await runDomainScan(
        page,
        `${server.baseUrl}/federation`,
        theme,
        `/federation`,
        async () => {
          // Single workspace → auto-selected → members table renders.
          await expect(page.locator('.federation__table')).toBeAttached({
            timeout: 10_000,
          });
        },
      );
    });

    // Empty states: sample_repo on the same server has no domain entities
    // — the honest "nothing indexed" screens are audited too.
    for (const [route, block] of [
      ['/lineage', '.lineage__empty'],
      ['/contracts', '.contracts__empty'],
      ['/infra', '.infra__empty'],
      ['/pipelines', '.pipelines__empty'],
    ] as const) {
      test(`axe ${route} empty state [${theme}]`, async ({ page }) => {
        await runDomainScan(
          page,
          `${server.baseUrl}${route}?repo=${server.repos.sample}`,
          theme,
          `${route} (empty)`,
          async () => {
            await expect(page.locator(block)).toBeAttached({ timeout: 10_000 });
          },
        );
      });
    }

    // No-workspace federation state exists only on the shared suite
    // server (sample_repo index, no `code-atlas federation` mutation).
    test(`axe /federation no workspace [${theme}]`, async ({ page }) => {
      await runDomainScan(
        page,
        `${BASE}/federation`,
        theme,
        `/federation (no workspace)`,
        async () => {
          await expect(
            page.locator('.federation__empty, .federation__list').first(),
          ).toBeAttached({ timeout: 10_000 });
        },
      );
    });
  }

  /* ----------------- keyboard / focus / motion / zoom -------------------- */

  test('skip link is first tab stop on a domain view', async ({ page }) => {
    await page.goto(`${server.baseUrl}/pipelines?repo=${server.repos.cicd}`);
    await waitForSettled(page);
    await page.keyboard.press('Tab');
    const skip = page.locator('.shell__skip-link');
    await expect(skip).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.locator('#shell-main')).toBeFocused();
  });

  test('tab order reaches the lineage form controls; focus ring is visible', async ({
    page,
  }) => {
    await page.goto(`${server.baseUrl}/lineage?repo=${server.repos.sql}`);
    await waitForSettled(page);
    await expect(
      page.locator('select[name="table"] option').nth(1),
    ).toBeAttached({ timeout: 10_000 });

    const select = page.locator('select[name="table"]');
    let reached = false;
    for (let i = 0; i < 60 && !reached; i++) {
      await page.keyboard.press('Tab');
      reached = await select.evaluate((el) => document.activeElement === el);
    }
    expect(reached, 'tab order never reached the lineage entity select').toBe(
      true,
    );
    const outline = await select.evaluate((el) => {
      const s = getComputedStyle(el);
      return { style: s.outlineStyle, width: s.outlineWidth };
    });
    expect(outline.style).not.toBe('none');
    expect(parseFloat(outline.width)).toBeGreaterThan(0);

    // Next stop must be the sibling "Filtrar nomes" input or the submit —
    // proving in-form order is sequential, not jumped.
    await page.keyboard.press('Tab');
    const nextTag = await page.evaluate(
      () => document.activeElement?.tagName.toLowerCase() ?? '',
    );
    expect(['input', 'button', 'select']).toContain(nextTag);
  });

  test('tab order reaches in-table controls on /contracts; Enter expands consumers', async ({
    page,
  }) => {
    await page.goto(`${server.baseUrl}/contracts?repo=${server.repos.api}`);
    await waitForSettled(page);
    await expect(page.locator('.contracts__table').first()).toBeAttached({
      timeout: 10_000,
    });

    const toggle = page.locator('.contracts__consumers-toggle').first();
    let reached = false;
    for (let i = 0; i < 150 && !reached; i++) {
      await page.keyboard.press('Tab');
      reached = await toggle.evaluate((el) => document.activeElement === el);
    }
    expect(
      reached,
      'tab order never reached a consumers toggle inside the endpoints table',
    ).toBe(true);
    // Keyboard-focused button must show the visible focus ring.
    const outline = await toggle.evaluate((el) => {
      const s = getComputedStyle(el);
      return { style: s.outlineStyle, width: s.outlineWidth };
    });
    expect(outline.style).not.toBe('none');
    expect(parseFloat(outline.width)).toBeGreaterThan(0);

    await page.keyboard.press('Enter');
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  });

  test('federation workspace button is keyboard-operable', async ({ page }) => {
    await page.goto(`${server.baseUrl}/federation`);
    await waitForSettled(page);
    await expect(page.locator('.federation__table')).toBeAttached({
      timeout: 10_000,
    });
    const wsButton = page.locator('.federation__ws').first();
    await wsButton.focus();
    await page.keyboard.press('Enter');
    // Re-selecting the workspace via keyboard keeps the members table —
    // the control is operable without a pointer.
    await expect(wsButton).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('.federation__table')).toBeAttached();
  });

  test('Escape is a consistent no-op on domain views (no dialogs/overlays)', async ({
    page,
  }) => {
    await page.goto(`${server.baseUrl}/contracts?repo=${server.repos.api}`);
    await waitForSettled(page);
    await expect(page.locator('.contracts__table').first()).toBeAttached({
      timeout: 10_000,
    });
    const toggle = page.locator('.contracts__consumers-toggle').first();
    await toggle.click();
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    const urlBefore = page.url();
    await page.keyboard.press('Escape');
    // Domain views own no dismissable surface — Escape must not collapse
    // the disclosure, navigate away, or break the page. Same contract as
    // a native <details> element.
    await expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(page.url()).toBe(urlBefore);
    await expect(page.locator('h1').first()).toBeVisible();
  });

  for (const route of [
    { path: '/contracts', repo: 'api' as const, wait: '.contracts__table' },
    {
      path: '/lineage',
      repo: 'sql' as const,
      wait: '.lineage__edges, select[name="table"]',
    },
  ] as const) {
    test(`prefers-reduced-motion zeroes all motion on populated ${route.path}`, async ({
      page,
    }) => {
      await page.emulateMedia({ reducedMotion: 'reduce' });
      const tableParam =
        route.path === '/lineage' && lineageEntityId !== null
          ? `&table=${lineageEntityId}`
          : '';
      await page.goto(
        `${server.baseUrl}${route.path}?repo=${server.repos[route.repo]}${tableParam}`,
      );
      await waitForSettled(page);
      expect(
        await page.evaluate(() =>
          matchMedia('(prefers-reduced-motion: reduce)').matches,
        ),
      ).toBe(true);
      await expect(page.locator(route.wait).first()).toBeAttached({
        timeout: 10_000,
      });
      // Every rendered element must compute zero transition/animation
      // duration — the motion tokens collapse to 0ms under reduce and the
      // domain views define no hardcoded durations of their own.
      const offenders = await page.evaluate(() => {
        const bad: string[] = [];
        const nonZero = (v: string) =>
          v.split(',').some((d) => parseFloat(d) > 0);
        for (const el of Array.from(
          document.querySelectorAll<HTMLElement>('body *'),
        )) {
          const s = getComputedStyle(el);
          if (nonZero(s.transitionDuration) || nonZero(s.animationDuration)) {
            const cls = (el.className?.toString() ?? '').split(/\s+/)[0] ?? '';
            bad.push(
              `${el.tagName.toLowerCase()}.${cls} t=${s.transitionDuration} a=${s.animationDuration}`,
            );
            if (bad.length >= 8) break;
          }
        }
        return bad;
      });
      expect(offenders).toEqual([]);
    });
  }

  // WCAG 2.2 SC 1.4.10 proxy: 1280×720 @200% zoom == 640×360 CSS px.
  // Asserted on the populated states in the light theme (reflow is
  // theme-independent; both palettes share the same layout CSS).
  const ZOOM_ROUTES = [
    { name: '/lineage', url: () => `/lineage?repo=${server.repos.sql}${lineageEntityId !== null ? `&table=${lineageEntityId}` : ''}`, wait: '.lineage__edges, select[name="table"]' },
    { name: '/contracts', url: () => `/contracts?repo=${server.repos.api}`, wait: '.contracts__table' },
    { name: '/infra', url: () => `/infra?repo=${server.repos.terraform}`, wait: '.infra__table' },
    { name: '/pipelines', url: () => `/pipelines?repo=${server.repos.cicd}`, wait: '.pipelines__workflow' },
    { name: '/federation', url: () => `/federation`, wait: '.federation__table' },
  ] as const;

  for (const { name, url, wait } of ZOOM_ROUTES) {
    test(`zoom 200% reflow keeps ${name} usable`, async ({ page }) => {
      await page.setViewportSize({ width: 640, height: 360 });
      await page.goto(`${server.baseUrl}${url()}`);
      await waitForSettled(page);
      await expect(page.locator(wait).first()).toBeAttached({
        timeout: 10_000,
      });
      const report = await page.evaluate(measureHorizontalOverflow);
      expect(
        report.overflowPx,
        `horizontal page overflow at 640px on ${name} — offenders: ${report.offenders.join(', ')}`,
      ).toBeLessThanOrEqual(1);
      await expect(page.locator('h1').first()).toBeVisible();
    });
  }
});

/* Aggregates every scan above (module-level SCANS); declared last so the
 * printed total covers the domain-view scans too. */
test('audit summary — no blocking violations anywhere', async () => {
  const all = SCANS.flatMap((s) =>
    s.results.violations.map((v) => ({ scan: `${s.route} [${s.theme}]`, v })),
  );
  const blocking = all.filter(({ v }) => v.impact && BLOCKING_IMPACTS.has(v.impact));
  const advisory = all.filter(({ v }) => !v.impact || !BLOCKING_IMPACTS.has(v.impact));
  console.log(
    `A11Y AUDIT SUMMARY — scans: ${SCANS.length}, ` +
      `blocking violations: ${blocking.length}, advisory: ${advisory.length}`,
  );
  for (const { scan, v } of advisory) {
    console.log(`  advisory ${scan}: [${v.impact}] ${v.id} — ${v.help}`);
  }
  expect(
    blocking,
    `blocking violations:\n${blocking.map(({ scan, v }) => `${scan} → ${v.id}`).join('\n')}`,
  ).toEqual([]);
});
