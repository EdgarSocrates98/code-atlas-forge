import { test, expect } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

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
  // inside a horizontally scrollable ancestor.
  return new Promise((resolve) => {
    const doc = document.documentElement;
    const vw = doc.clientWidth;
    const overflowPx = Math.max(doc.scrollWidth, document.body.scrollWidth) - vw;
    const offenders: string[] = [];
    if (overflowPx > 1) {
      for (const el of Array.from(document.querySelectorAll<HTMLElement>('body *'))) {
        const r = el.getBoundingClientRect();
        if (r.right <= vw + 1) continue;
        let ancestor = el.parentElement;
        let contained = false;
        while (ancestor) {
          // Contained only inside a real clip/scroll container — an ancestor
          // that merely overflows visibly propagates the page-level bug.
          const ox = getComputedStyle(ancestor).overflowX;
          if (
            (ox === 'auto' || ox === 'scroll' || ox === 'hidden' || ox === 'clip') &&
            ancestor.scrollWidth > ancestor.clientWidth + 1
          ) {
            contained = true;
            break;
          }
          ancestor = ancestor.parentElement;
        }
        if (contained) continue;
        const cls = (el.className?.toString() ?? '').trim().split(/\s+/)[0] ?? '';
        offenders.push(
          `${el.tagName.toLowerCase()}${cls ? '.' + cls : ''} right=${Math.round(r.right)}px`,
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
