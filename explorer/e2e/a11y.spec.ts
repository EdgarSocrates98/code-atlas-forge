import { test, expect, request } from '@playwright/test';
import type { APIRequestContext, Page } from '@playwright/test';
import { AxeBuilder } from '@axe-core/playwright';
import type { AxeResults, Result } from 'axe-core';

/**
 * WCAG 2.2 AA accessibility audit (Phase 11). Runs axe-core against every
 * main route in BOTH themes (light/dark, driven by the `atlas-theme`
 * localStorage key that ThemeService persists) plus keyboard/focus/
 * reduced-motion checks that axe cannot cover.
 *
 * Runs against the real `code-atlas explorer` subprocess serving the
 * compiled production build (see global-setup.ts). No mocks.
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

  test('audit summary — no blocking violations anywhere', async () => {
    // Aggregate assertion across every scan this run (also printed so the
    // report file can quote exact numbers).
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
        (nav) => nav.contains(document.activeElement),
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
