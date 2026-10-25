import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';
import {
  bootDomainFixtureServer,
  type DomainFixtureServer,
} from './domain-fixture-server';

/**
 * Phase 9 — real screenshots for visual review.
 *
 * Captures every main route at desktop (1280x720) and mobile (375x812)
 * in both light and dark themes against the real `code-atlas explorer`
 * subprocess started by global-setup.ts (same env-var contract:
 * ATLAS_E2E_BASE_URL is the baseURL; the fixture repo is really indexed).
 * No mocks, no masking — the PNGs show what a user actually sees.
 *
 * Output: <repo>/docs/screenshots/{route}-{viewport}-{theme}.png
 *
 * Each capture also asserts the honest invariant that nothing pushes the
 * page wider than the viewport (no off-viewport elements / horizontal
 * page scroll) — the check doubles as the visual-regression tripwire for
 * the review pass. Elements inside a horizontally scrollable container
 * are contained overflow, not page overflow, and are ignored.
 */

const REPO_ROOT = join(__dirname, '..', '..');
const SHOTS_DIR = join(REPO_ROOT, 'docs', 'screenshots');

const ROUTES = [
  'dashboard',
  'repositories',
  'graph',
  'search',
  'impact',
  'revisions',
  'context',
  'diagnostics',
  'settings',
] as const;

const VIEWPORTS = [
  { name: 'desktop', width: 1280, height: 720 },
  { name: 'mobile', width: 375, height: 812 },
] as const;

const THEMES = ['light', 'dark'] as const;

interface OverflowReport {
  overflowPx: number;
  offenders: string[];
}

function measureHorizontalOverflow(): Promise<OverflowReport> {
  // Runs in the page. Returns document-level horizontal overflow plus the
  // first offending elements (for diagnosis), skipping content contained
  // inside a horizontally scrollable ancestor. Two offender shapes:
  // elements whose box escapes the viewport's right edge, and elements
  // whose *content* visibly escapes their own box (unbreakable text
  // tokens — the box stays in-bounds but the overflowing text still
  // propagates to document scrollWidth).
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

test.describe('screenshots — real visual capture', () => {
  for (const route of ROUTES) {
    for (const viewport of VIEWPORTS) {
      for (const theme of THEMES) {
        test(`${route} · ${viewport.name} · ${theme}`, async ({ page }) => {
          mkdirSync(SHOTS_DIR, { recursive: true });

          // Theme preference persists in localStorage before app boot
          // (ThemeService reads 'atlas-theme' at construction).
          await page.addInitScript(
            (value) => window.localStorage.setItem('atlas-theme', value),
            theme,
          );
          await page.setViewportSize({ width: viewport.width, height: viewport.height });

          await page.goto(`/${route}`, { waitUntil: 'networkidle' });
          // Let async feature data resolve — loading blocks disappear.
          await page
            .locator('.state-block__panel--loading')
            .first()
            .waitFor({ state: 'detached', timeout: 8_000 })
            .catch(() => {});
          // Settle camera fit / layout transitions (≤350ms by design).
          await page.waitForTimeout(600);

          const report = await page.evaluate(measureHorizontalOverflow);
          expect(
            report.overflowPx,
            `horizontal overflow on /${route} — offenders: ${report.offenders.join(', ')}`,
          ).toBeLessThanOrEqual(1);

          await page.screenshot({
            path: join(SHOTS_DIR, `${route}-${viewport.name}-${theme}.png`),
            fullPage: false,
          });
        });
      }
    }
  }
});

/* ---------------------------------------------------------------------------
 * Cycle-8 Phase 14 — domain views (cycle-7 routes) visual capture.
 *
 * Runs against the dedicated domain-fixture server (see
 * ./domain-fixture-server.ts) so every capture shows populated views —
 * real tables, disclosures, badges — not empty states. The empty states
 * are still captured once (desktop/light) because they are the screens a
 * user actually sees before adapters are enabled. A 640×360 capture per
 * route approximates 200% zoom on a 1280×720 desktop for the reflow
 * review.
 * ------------------------------------------------------------------------- */
test.describe('screenshots — domain views (populated fixture server)', () => {
  let server: DomainFixtureServer;
  /** Data entity whose /domains/lineage yields edges (API-verified). */
  let lineageEntityId: number | null = null;

  test.beforeAll(async () => {
    test.setTimeout(240_000); // 5 real index runs + federation + server boot
    server = await bootDomainFixtureServer();
    try {
      const res = await fetch(
        `${server.baseUrl}/api/v1/domains/entities?domain=sql&kind=data_table,data_view,data_transformation&repository_id=${server.repos.sql}&limit=200`,
      );
      const { items } = (await res.json()) as {
        items?: { id: number; kind: string }[];
      };
      for (const entity of items ?? []) {
        const r = await fetch(
          `${server.baseUrl}/api/v1/domains/lineage?table=${entity.id}&repository_id=${server.repos.sql}`,
        );
        if (!r.ok) continue;
        const body = (await r.json()) as { edges?: unknown[] };
        if ((body.edges?.length ?? 0) > 0) {
          lineageEntityId = entity.id;
          break;
        }
      }
    } catch {
      lineageEntityId = null; // screenshots still capture the populated form
    }
  });

  test.afterAll(() => {
    server?.stop();
  });

  const DOMAIN_ROUTES = [
    {
      name: 'lineage',
      url: () =>
        `/lineage?repo=${server.repos.sql}` +
        (lineageEntityId !== null ? `&table=${lineageEntityId}` : ''),
      wait: () =>
        lineageEntityId !== null ? '.lineage__edges' : 'select[name="table"]',
    },
    {
      name: 'contracts',
      url: () => `/contracts?repo=${server.repos.api}`,
      wait: () => '.contracts__table',
    },
    {
      name: 'infra',
      url: () => `/infra?repo=${server.repos.terraform}`,
      wait: () => '.infra__table',
    },
    {
      name: 'pipelines',
      url: () => `/pipelines?repo=${server.repos.cicd}`,
      wait: () => '.pipelines__workflow',
    },
    {
      name: 'federation',
      url: () => `/federation`,
      wait: () => '.federation__table',
    },
  ] as const;

  for (const { name, url, wait } of DOMAIN_ROUTES) {
    for (const viewport of VIEWPORTS) {
      for (const theme of THEMES) {
        test(`${name} · ${viewport.name} · ${theme}`, async ({ page }) => {
          mkdirSync(SHOTS_DIR, { recursive: true });
          await page.addInitScript(
            (value) => window.localStorage.setItem('atlas-theme', value),
            theme,
          );
          await page.setViewportSize({
            width: viewport.width,
            height: viewport.height,
          });

          await page.goto(`${server.baseUrl}${url()}`, {
            waitUntil: 'networkidle',
          });
          await page
            .locator('.state-block__panel--loading')
            .first()
            .waitFor({ state: 'detached', timeout: 8_000 })
            .catch(() => {});
          await page.locator(wait()).first().waitFor({
            state: 'attached',
            timeout: 10_000,
          });
          await page.waitForTimeout(600);

          const report = await page.evaluate(measureHorizontalOverflow);
          expect(
            report.overflowPx,
            `horizontal overflow on ${name} — offenders: ${report.offenders.join(', ')}`,
          ).toBeLessThanOrEqual(1);

          await page.screenshot({
            path: join(SHOTS_DIR, `${name}-${viewport.name}-${theme}.png`),
            fullPage: false,
          });
        });
      }
    }

    // 200% zoom approximation: 640 CSS px width on a 1280 reference.
    test(`${name} · zoom200 · light`, async ({ page }) => {
      mkdirSync(SHOTS_DIR, { recursive: true });
      await page.addInitScript(
        (value) => window.localStorage.setItem('atlas-theme', value),
        'light',
      );
      await page.setViewportSize({ width: 640, height: 360 });
      await page.goto(`${server.baseUrl}${url()}`, { waitUntil: 'networkidle' });
      await page.locator(wait()).first().waitFor({
        state: 'attached',
        timeout: 10_000,
      });
      await page.waitForTimeout(600);
      const report = await page.evaluate(measureHorizontalOverflow);
      expect(
        report.overflowPx,
        `horizontal overflow at 640px (≈200% zoom) on ${name} — offenders: ${report.offenders.join(', ')}`,
      ).toBeLessThanOrEqual(1);
      await page.screenshot({
        path: join(SHOTS_DIR, `${name}-zoom200-light.png`),
        fullPage: false,
      });
    });
  }

  // Empty states (sample_repo — no domain entities), desktop+light only:
  // they are the honest pre-adapter screens.
  const EMPTY_ROUTES = [
    { name: 'lineage', block: '.lineage__empty' },
    { name: 'contracts', block: '.contracts__empty' },
    { name: 'infra', block: '.infra__empty' },
    { name: 'pipelines', block: '.pipelines__empty' },
  ] as const;

  for (const { name, block } of EMPTY_ROUTES) {
    test(`${name} · empty · desktop · light`, async ({ page }) => {
      mkdirSync(SHOTS_DIR, { recursive: true });
      await page.addInitScript(
        (value) => window.localStorage.setItem('atlas-theme', value),
        'light',
      );
      await page.setViewportSize({ width: 1280, height: 720 });
      await page.goto(`${server.baseUrl}/${name}?repo=${server.repos.sample}`, {
        waitUntil: 'networkidle',
      });
      await page.locator(block).waitFor({ state: 'attached', timeout: 10_000 });
      await page.waitForTimeout(600);
      await page.screenshot({
        path: join(SHOTS_DIR, `${name}-empty-desktop-light.png`),
        fullPage: false,
      });
    });
  }
});
