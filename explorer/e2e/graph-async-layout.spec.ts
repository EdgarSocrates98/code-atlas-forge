import { test, expect, Page } from '@playwright/test';
import { spawn, execFileSync, ChildProcess } from 'node:child_process';
import { existsSync, mkdirSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'node:net';

/**
 * Async layout contract — Cycle 7 Phase 0.2. The ForceAtlas2 pass runs in
 * a Web Worker since this cycle; this spec asserts the observable UI
 * contract against the real explorer subprocess + real compiled Angular
 * dist (worker chunk served same-origin under the app's CSP):
 *
 *   1. during a relayout the main thread stays responsive — max RAF gap
 *      < 300 ms while the worker pass is provably in flight;
 *   2. the Cancel button aborts the pass promptly (no commit, no error);
 *   3. a data revision mid-flight drops the stale run — the node set that
 *      wins is the new one and the lifecycle recovers.
 *
 * Dataset: the same gen_large_repo.py fixture family as graph-scale.spec
 * (ATLAS_SCALE_DIR shared — a fixture indexed by the benchmark run is
 * reused verbatim). Default size 3000 files: the overview API caps at
 * OVERVIEW_LIMIT=2000 items, so the rendered set after the cap raise is
 * 2000 nodes — the size where the synchronous pass blocked ~2.3 s.
 * (At 3000 files the server-truncation notice is legitimately present;
 * assertions target the layout-failure text specifically, never "any
 * warning".)
 */

const REPO_ROOT = join(__dirname, '..', '..');
const FIXTURE_GEN = join(REPO_ROOT, 'tests', 'fixtures', 'gen_large_repo.py');
const WORK = process.env['ATLAS_SCALE_DIR'] ?? join(tmpdir(), 'atlas-graph-scale');
const SIZE = Number(process.env['ATLAS_ASYNC_LAYOUT_SIZE'] ?? '3000');
const MAX_CAP = 2000;

function pythonBin(): string {
  if (process.env['ATLAS_PYTHON']) return process.env['ATLAS_PYTHON'];
  const venv = join(REPO_ROOT, '.venv', 'bin', 'python');
  if (existsSync(venv)) return venv;
  return 'python3';
}

async function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const srv = createServer();
    srv.once('error', reject);
    srv.listen(0, '127.0.0.1', () => {
      const addr = srv.address();
      const port = typeof addr === 'object' && addr ? addr.port : 0;
      srv.close(() => resolve(port));
    });
  });
}

function repoDir(n: number): string {
  return join(WORK, `repo-${n}`);
}
function dataDir(n: number): string {
  return join(WORK, `data-${n}`);
}

function countModules(dir: string): number {
  if (!existsSync(dir)) return 0;
  return readdirSync(dir).filter((f) => /^mod_\d+\.py$/.test(f)).length;
}

/** Generate + index fixture SIZE if absent (shared with graph-scale). */
function ensureFixture(n: number): void {
  const repo = repoDir(n);
  if (countModules(repo) !== n) {
    execFileSync(pythonBin(), [FIXTURE_GEN, '--files', String(n), '--out', repo], {
      cwd: REPO_ROOT,
      stdio: 'inherit',
    });
  }
  const db = join(dataDir(n), 'atlas.db');
  if (!existsSync(db)) {
    mkdirSync(dataDir(n), { recursive: true });
    execFileSync(pythonBin(), ['-m', 'code_atlas', 'index', repo], {
      cwd: REPO_ROOT,
      env: { ...process.env, CODE_ATLAS_DATA_DIR: dataDir(n) },
      stdio: 'inherit',
    });
  }
}

async function startExplorer(n: number): Promise<{ proc: ChildProcess; base: string }> {
  const port = await freePort();
  const proc = spawn(pythonBin(), ['-m', 'code_atlas', 'explorer', '--port', String(port)], {
    cwd: REPO_ROOT,
    env: { ...process.env, CODE_ATLAS_DATA_DIR: dataDir(n) },
    stdio: 'pipe',
  });
  const base = `http://127.0.0.1:${port}`;
  const deadline = Date.now() + 30_000;
  let lastErr: unknown;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${base}/api/v1/repositories`);
      if (res.status) return { proc, base };
    } catch (err) {
      lastErr = err;
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  proc.kill('SIGTERM');
  throw new Error(`explorer(${n}) did not come up: ${String(lastErr)}`);
}

/** Stage-rendered node count from the aria-label, or null while absent. */
async function renderedCount(page: Page): Promise<number | null> {
  return page.evaluate(() => {
    const stage = document.querySelector('.graph-stage');
    const m = /(\d+) nós visíveis/.exec(stage?.getAttribute('aria-label') ?? '');
    return m ? Number(m[1]) : null;
  });
}

/** Install the RAF-gap sampler before the app scripts run. */
async function installProbes(page: Page): Promise<void> {
  await page.addInitScript(() => {
    (window as unknown as { __frames: { t: number; d: number }[] }).__frames = [];
    let last = performance.now();
    const loop = (t: number) => {
      const f = (window as unknown as { __frames: { t: number; d: number }[] }).__frames;
      f.push({ t, d: t - last });
      last = t;
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  });
}

/** Collect RAF deltas for `ms` measured from the call site. */
async function sampleRafGaps(page: Page, ms: number): Promise<number[]> {
  const start = await page.evaluate(() => performance.now());
  await page.waitForTimeout(ms);
  return page.evaluate((mark) => {
    const f = (window as unknown as { __frames: { t: number; d: number }[] }).__frames;
    return f.filter((fr) => fr.t >= mark).map((fr) => fr.d);
  }, start);
}

const CANCEL_RE = /Cancelar layout/;
const RELAYOUT_RE = /Reorganizar/;

/* Numbers-only contract suite — artifacts add nothing and race teardown. */
test.use({ trace: 'off', video: 'off', screenshot: 'off' });

test.describe('graph async layout — worker contract', () => {
  // Fixture gen + index + one explorer boot; the layout pass itself is ~2 s.
  test.describe.configure({ timeout: 180_000 });

  let proc: ChildProcess;
  let base: string;

  test.beforeAll(async () => {
    mkdirSync(WORK, { recursive: true });
    ensureFixture(SIZE);
    const started = await startExplorer(SIZE);
    proc = started.proc;
    base = started.base;
  });

  test.afterAll(() => {
    proc?.kill('SIGTERM');
  });

  /**
   * Bring the page to a stable 2000-node canvas: overview loads at the
   * default cap, the initial async layout settles (Cancel button is the
   * honest in-flight signal — visible only while a run is alive), then
   * the cap raises to the full set so relayout spans ~2 s.
   */
  async function loadAtCap(page: Page): Promise<void> {
    await installProbes(page);
    await page.goto(`${base}/graph`, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(
      () => /\d+ nós visíveis/.test(
        document.querySelector('.graph-stage')?.getAttribute('aria-label') ?? '',
      ),
      undefined,
      { polling: 'raf', timeout: 60_000 },
    );
    // No need to settle the initial pass here — raising the cap is a data
    // revision and legitimately supersedes whatever is in flight.
    const capInput = page.locator('input.cap__input');
    await capInput.fill(String(MAX_CAP));
    await capInput.dispatchEvent('change');
    await page.waitForFunction(
      (expected) => {
        const el = document.querySelector('.graph-stage');
        const m = /(\d+) nós visíveis/.exec(el?.getAttribute('aria-label') ?? '');
        return !!m && Number(m[1]) === expected;
      },
      MAX_CAP,
      { polling: 'raf', timeout: 30_000 },
    );
  }

  test('relayout keeps the main thread responsive (max RAF gap < 300 ms)', async ({
    page,
  }) => {
    await loadAtCap(page);
    expect(await renderedCount(page)).toBe(MAX_CAP);

    const cancel = page.getByRole('button', { name: CANCEL_RE });
    await page.getByRole('button', { name: RELAYOUT_RE }).click();

    // In-flight proof: the affordance exists only while the worker runs.
    await expect(cancel).toBeVisible({ timeout: 10_000 });
    await expect(page.locator('.graph-stage')).toHaveAttribute('aria-busy', 'true');

    // Mid-flight window: the old synchronous pass would land ONE ~2.3 s
    // gap here; the worker pass must keep every frame gap under budget.
    const gaps = await sampleRafGaps(page, 1_500);
    expect(gaps.length).toBeGreaterThan(10); // frames really flowed mid-flight
    const maxGap = Math.max(...gaps);
    expect(maxGap, `main-thread gap during worker layout`).toBeLessThan(300);

    // The run completes honestly — busy clears when the commit lands.
    await expect(cancel).toBeHidden({ timeout: 30_000 });
    expect(await renderedCount(page)).toBe(MAX_CAP);
  });

  test('cancel aborts the in-flight relayout — no commit, no error', async ({
    page,
  }) => {
    await loadAtCap(page);
    const cancel = page.getByRole('button', { name: CANCEL_RE });

    // Establish a deterministic committed baseline: one full relayout at
    // 2000 nodes. Its duration lands in "Último layout" — the value a
    // cancelled run must NOT overwrite.
    await page.getByRole('button', { name: RELAYOUT_RE }).click();
    await expect(cancel).toBeVisible({ timeout: 10_000 });
    await expect(cancel).toBeHidden({ timeout: 30_000 });

    await page.getByRole('button', { name: 'Diagnósticos' }).click();
    const lastLayout = page.locator('#graph-diagnostics .diag__item', {
      hasText: 'Último layout',
    }).locator('dd');
    await expect(lastLayout).toHaveText(/\d+(\.\d+)? ms/, { timeout: 30_000 });
    const before = (await lastLayout.textContent())?.trim() ?? '';
    expect(before).not.toBe('—');

    await page.getByRole('button', { name: RELAYOUT_RE }).click();
    await expect(cancel).toBeVisible({ timeout: 10_000 });

    await cancel.click();
    // Aborted early — a completed 2000-node pass would take seconds more.
    await expect(cancel).toBeHidden({ timeout: 2_000 });
    await expect(page.locator('.graph-stage')).not.toHaveAttribute('aria-busy', 'true');

    // No commit happened: the last-layout clock keeps its previous value.
    // (Panel samples every 500 ms — give it a beat to publish.)
    await page.waitForTimeout(700);
    const after = (await lastLayout.textContent())?.trim() ?? '';
    expect(after, 'cancelled run must not overwrite lastLayoutMs').toBe(before);
    await expect(
      page.locator('#graph-diagnostics .diag__item', { hasText: 'Estado do layout' }).locator('dd'),
    ).toHaveText(/ocioso/);
    await expect(page.getByText('O cálculo de layout falhou')).toHaveCount(0);

    // Lifecycle recovers: a fresh relayout runs to completion.
    await page.getByRole('button', { name: RELAYOUT_RE }).click();
    await expect(cancel).toBeVisible({ timeout: 10_000 });
    await expect(cancel).toBeHidden({ timeout: 30_000 });
    await expect(lastLayout).toHaveText(/\d+(\.\d+)? ms/);
  });

  test('a data revision mid-layout drops the stale run cleanly', async ({
    page,
  }) => {
    await loadAtCap(page);

    const cancel = page.getByRole('button', { name: CANCEL_RE });
    await page.getByRole('button', { name: RELAYOUT_RE }).click();
    await expect(cancel).toBeVisible({ timeout: 10_000 });

    // Data revision while the worker is mid-flight: cap 2000 → 700 bumps
    // the generation — the matrices describe a node set that no longer
    // exists; the run must be dropped, never committed.
    const capInput = page.locator('input.cap__input');
    await capInput.fill('700');
    await capInput.dispatchEvent('change');

    await page.waitForFunction(
      () => {
        const el = document.querySelector('.graph-stage');
        const m = /(\d+) nós visíveis/.exec(el?.getAttribute('aria-label') ?? '');
        return !!m && Number(m[1]) === 700;
      },
      undefined,
      { polling: 'raf', timeout: 15_000 },
    );
    await expect(cancel).toBeHidden({ timeout: 3_000 });
    await expect(page.locator('.graph-stage')).not.toHaveAttribute('aria-busy', 'true');
    await expect(page.getByText('O cálculo de layout falhou')).toHaveCount(0);

    // The new generation lays out normally — the stale run did not wedge
    // the lifecycle.
    await page.getByRole('button', { name: RELAYOUT_RE }).click();
    await expect(cancel).toBeVisible({ timeout: 10_000 });
    await expect(cancel).toBeHidden({ timeout: 30_000 });
    expect(await renderedCount(page)).toBe(700);
  });
});
