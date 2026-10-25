import { test, expect, Page } from '@playwright/test';
import { spawn, execFileSync, ChildProcess } from 'node:child_process';
import { existsSync, mkdirSync, readdirSync, writeFileSync } from 'node:fs';
import { tmpdir, cpus, totalmem, platform, release } from 'node:os';
import { dirname, join } from 'node:path';
import { createServer } from 'node:net';

/**
 * Graph HARDENING suite — Cycle 8 Phases 4+5 (lf-0131). Not a benchmark:
 * a failure-path and lifecycle suite over the sigma canvas + the FA2
 * layout worker, measured against the real compiled explorer (worker
 * chunk served same-origin under the app CSP).
 *
 * Dataset: gen_large_repo.py dense family — `500-c2` (500 modules, 2
 * ring calls each, 3 hubs × 150 fanout; 5401 relations in the index),
 * shared verbatim with graph-dense.spec via ATLAS_DENSE_DIR. 500
 * overview nodes give a ~0.5–1.5 s worker layout — long enough to kill
 * deterministically mid-flight, short enough to keep cycles honest.
 *
 * Instrumentation under test (all real, all shipped):
 *   window.__atlasGraph — dev hook from graph-telemetry.ts: live sigma /
 *     worker counters + handles + per-status run counters.
 *   window.__probe      — RAF gap sampler + add/removeEventListener net
 *     counters (this spec's init script).
 *   CDP JSHeapUsedSize after HeapProfiler.collectGarbage.
 *
 * Assertions pin invariants only (no invented budgets); every timing is
 * recorded verbatim to benchmarks/results/graph-hardiness-latest.json.
 */

const REPO_ROOT = join(__dirname, '..', '..');
const FIXTURE_GEN = join(REPO_ROOT, 'tests', 'fixtures', 'gen_large_repo.py');
const WORK = process.env['ATLAS_DENSE_DIR'] ?? join(tmpdir(), 'atlas-graph-dense');
const RESULTS_JSON = join(REPO_ROOT, 'benchmarks', 'results', 'graph-hardiness-latest.json');

/* Same dense fixture as graph-dense.spec — 500 files × 2 calls, 3 hubs. */
const DATASET = { label: '500-c2', files: 500, calls: 2 };
const HUBS = 3;
const HUB_FANOUT = 150;

const NAV_CYCLES = 10;
const MOUNT_CYCLES = 20;
const EXPAND_MAX_TRIES = 40;
const WATCHDOG_SLA_MS = 45_000; // runner watchdog is 30 s; generous SLA

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

function repoDir(): string {
  return join(WORK, `repo-dense-${DATASET.files}-c${DATASET.calls}-h${HUBS}x${HUB_FANOUT}`);
}
function dataDir(): string {
  return join(WORK, `data-dense-${DATASET.files}-c${DATASET.calls}-h${HUBS}x${HUB_FANOUT}`);
}

function countModules(dir: string): number {
  if (!existsSync(dir)) return 0;
  return readdirSync(dir).filter((f) => /^mod_\d+\.py$/.test(f)).length;
}

/** Generate + index the dense fixture if absent (shared with dense spec). */
function ensureFixture(): { generated: boolean; indexed: boolean } {
  const out = { generated: false, indexed: false };
  if (countModules(repoDir()) !== DATASET.files) {
    execFileSync(
      pythonBin(),
      [
        FIXTURE_GEN,
        '--files', String(DATASET.files),
        '--calls', String(DATASET.calls),
        '--hubs', String(HUBS),
        '--hub-fanout', String(HUB_FANOUT),
        '--out', repoDir(),
      ],
      { cwd: REPO_ROOT, stdio: 'inherit' },
    );
    out.generated = true;
  }
  const db = join(dataDir(), 'atlas.db');
  if (!existsSync(db)) {
    mkdirSync(dataDir(), { recursive: true });
    execFileSync(pythonBin(), ['-m', 'code_atlas', 'index', repoDir()], {
      cwd: REPO_ROOT,
      env: { ...process.env, CODE_ATLAS_DATA_DIR: dataDir() },
      stdio: 'inherit',
    });
    out.indexed = true;
  }
  return out;
}

/** Real index contents — the hub occurrence id for the ego deep link. */
function hubOccurrenceId(): number {
  const script = `
import sqlite3, json, sys
conn = sqlite3.connect(sys.argv[1])
sid = conn.execute("SELECT MAX(id) FROM snapshots WHERE status='READY'").fetchone()[0]
row = conn.execute(
  "SELECT o.id FROM symbol_occurrences o JOIN symbols s ON s.id=o.logical_symbol_id"
  " WHERE o.snapshot_id=? AND s.qualified_name='mod_0000.hub_0000'", (sid,)).fetchone()
rel = conn.execute("SELECT COUNT(*) FROM relations WHERE snapshot_id=?", (sid,)).fetchone()[0]
print(json.dumps({"hub": row[0], "relations": rel}))
`;
  const out = execFileSync(pythonBin(), ['-c', script, join(dataDir(), 'atlas.db')], {
    encoding: 'utf-8',
  });
  return JSON.parse(out) as { hub: number; relations: number };
}

async function startExplorer(): Promise<{ proc: ChildProcess; base: string }> {
  const port = await freePort();
  const proc = spawn(pythonBin(), ['-m', 'code_atlas', 'explorer', '--port', String(port)], {
    cwd: REPO_ROOT,
    env: { ...process.env, CODE_ATLAS_DATA_DIR: dataDir() },
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
  throw new Error(`hardiness explorer did not come up: ${String(lastErr)}`);
}

/* ------------------------------- probes ------------------------------ */

interface Probe {
  frames: { t: number; d: number }[];
  listenerAdds: number;
  listenerRemoves: number;
  /** Net live listeners on long-lived targets (window/document/html/mql). */
  globalNet: number;
}

/**
 * RAF sampler + net live-listener accounting. `once:true` listeners that
 * already fired are a knowable overcount — recorded verbatim, the real
 * leak signal is per-cycle GROWTH of `globalNet`, not its absolute value.
 */
async function installProbes(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as { __probe: Probe };
    w.__probe = {
      frames: [],
      listenerAdds: 0,
      listenerRemoves: 0,
      globalNet: 0,
    };
    const isGlobalTarget = (t: EventTarget | null): boolean =>
      t === window || t === document || t === document.documentElement ||
      (typeof MediaQueryList !== 'undefined' && t instanceof MediaQueryList);
    const oAdd = EventTarget.prototype.addEventListener;
    const oRem = EventTarget.prototype.removeEventListener;
    EventTarget.prototype.addEventListener = function (this: EventTarget, type, l, o) {
      w.__probe.listenerAdds++;
      if (isGlobalTarget(this)) w.__probe.globalNet++;
      return oAdd.call(this, type, l, o);
    };
    EventTarget.prototype.removeEventListener = function (this: EventTarget, type, l, o) {
      w.__probe.listenerRemoves++;
      if (isGlobalTarget(this)) w.__probe.globalNet--;
      return oRem.call(this, type, l, o);
    };
    let last = performance.now();
    const loop = (t: number) => {
      w.__probe.frames.push({ t, d: t - last });
      last = t;
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  });
}

interface Telemetry {
  sigmaLive: number;
  sigmaTotal: number;
  workersLive: number;
  workersTotal: number;
  fallbacks: number;
  lastLayoutError: string | null;
  runs: { started: number; done: number; cancelled: number; stale: number; error: number };
  workerHandles: number;
}

/** Counter snapshot — the live handles are not serializable, so `workers`
 * crosses as its size and `sigma` as a boolean. */
async function telemetry(page: Page): Promise<Telemetry | null> {
  return page.evaluate(() => {
    const g = (window as unknown as {
      __atlasGraph?: Omit<Telemetry, 'workerHandles'> & { workers: Set<Worker>; sigma: unknown };
    }).__atlasGraph;
    if (!g) return null;
    return {
      sigmaLive: g.sigmaLive,
      sigmaTotal: g.sigmaTotal,
      workersLive: g.workersLive,
      workersTotal: g.workersTotal,
      fallbacks: g.fallbacks,
      lastLayoutError: g.lastLayoutError,
      runs: { ...g.runs },
      workerHandles: g.workers.size,
    };
  });
}

/** Terminate every live layout worker — the honest mid-run crash. */
async function killLiveWorkers(page: Page): Promise<number> {
  return page.evaluate(() => {
    const g = (window as unknown as {
      __atlasGraph?: { workers: Set<Worker> };
    }).__atlasGraph;
    if (!g) return 0;
    let killed = 0;
    for (const w of g.workers) {
      w.terminate();
      killed++;
    }
    return killed;
  });
}

/** JSHeapUsedSize (MB) after an explicit GC — honest before/after delta. */
async function heapMB(page: Page): Promise<number | null> {
  try {
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('HeapProfiler.enable');
    await cdp.send('HeapProfiler.collectGarbage');
    await cdp.send('Performance.enable');
    const { metrics } = await cdp.send('Performance.getMetrics');
    await cdp.detach();
    const m = metrics.find((x: { name: string }) => x.name === 'JSHeapUsedSize');
    return m ? Math.round((m.value / 1048576) * 100) / 100 : null;
  } catch {
    return null;
  }
}

/** Rendered node/edge counts off the stage aria-label. */
async function renderedCounts(page: Page): Promise<{ nodes: number | null; edges: number | null }> {
  return page.evaluate(() => {
    const stage = document.querySelector('.graph-stage');
    const label = stage?.getAttribute('aria-label') ?? '';
    const nm = /(\d+) nós visíveis/.exec(label);
    const em = /e (\d+) relações/.exec(label);
    return { nodes: nm ? Number(nm[1]) : null, edges: em ? Number(em[1]) : null };
  });
}

async function waitForCanvas(page: Page, timeout = 60_000): Promise<void> {
  await page.waitForFunction(
    () =>
      /\d+ nós visíveis/.test(
        document.querySelector('.graph-stage')?.getAttribute('aria-label') ?? '',
      ),
    undefined,
    { polling: 'raf', timeout },
  );
}

/** Wait until no layout run is in flight (worker or chunked fallback). */
async function waitLayoutIdle(page: Page, timeout = 60_000): Promise<void> {
  await page.waitForFunction(
    () => !document.querySelector('.graph-stage')?.hasAttribute('aria-busy'),
    undefined,
    { polling: 'raf', timeout },
  );
}

async function maxRafGapSince(page: Page, mark: number): Promise<number> {
  return page.evaluate((m) => {
    const p = (window as unknown as { __probe: Probe }).__probe;
    let max = 0;
    for (const fr of p.frames) if (fr.t >= m && fr.d > max) max = fr.d;
    return Math.round(max * 100) / 100;
  }, mark);
}

/** Nodes with non-finite positions in the live sigma graph (−1 = none). */
async function badPositions(page: Page): Promise<number> {
  return page.evaluate(() => {
    const g = (window as unknown as {
      __atlasGraph?: {
        sigma: {
          getGraph(): {
            forEachNode(cb: (id: string, attr: Record<string, unknown>) => void): void;
          };
        } | null;
      };
    }).__atlasGraph;
    const s = g?.sigma;
    if (!s) return -1;
    let bad = 0;
    s.getGraph().forEachNode((_id, a) => {
      if (!Number.isFinite(a['x']) || !Number.isFinite(a['y'])) bad++;
    });
    return bad;
  });
}

/** Viewport-space midpoints of every edge, longest first. */
async function edgeMidpoints(page: Page): Promise<{ id: string; x: number; y: number; len: number }[]> {
  return page.evaluate(() => {
    const g = (window as unknown as {
      __atlasGraph?: {
        sigma: {
          getGraph(): {
            forEachEdge(
              cb: (
                id: string,
                attr: Record<string, unknown>,
                source: string,
                target: string,
              ) => void,
            ): void;
            getNodeAttributes(id: string): { x: number; y: number };
          };
          graphToViewport(p: { x: number; y: number }): { x: number; y: number };
        } | null;
      };
    }).__atlasGraph;
    const s = g?.sigma;
    if (!s) return [];
    const graph = s.getGraph();
    const out: { id: string; x: number; y: number; len: number }[] = [];
    graph.forEachEdge((id, _attr, source, target) => {
      const a = s.graphToViewport(graph.getNodeAttributes(source));
      const b = s.graphToViewport(graph.getNodeAttributes(target));
      out.push({
        id,
        x: (a.x + b.x) / 2,
        y: (a.y + b.y) / 2,
        len: Math.hypot(b.x - a.x, b.y - a.y),
      });
    });
    return out.sort((p, q) => q.len - p.len);
  });
}

const LAYOUT_FAIL_RE = /cálculo de layout falhou/;
const FALLBACK_RE = /thread principal — Web Worker indisponível/;
const CANCEL_RE = /Cancelar layout/;
const RELAYOUT_RE = /Reorganizar/;

/* Numbers-only suite — artifacts race teardown (same rule as the other
 * graph specs). */
test.use({ trace: 'off', video: 'off', screenshot: 'off' });

test.describe('graph hardiness — cycle-8 phases 4+5', () => {
  test.describe.configure({ timeout: 420_000 });

  let proc: ChildProcess;
  let base: string;
  let hubOcc = 0;
  let relationsTotal = 0;
  const measured: Record<string, unknown> = {};

  test.beforeAll(async () => {
    test.setTimeout(300_000);
    mkdirSync(WORK, { recursive: true });
    const fixture = ensureFixture();
    const db = hubOccurrenceId();
    hubOcc = db.hub;
    relationsTotal = db.relations;
    const started = await startExplorer();
    proc = started.proc;
    base = started.base;
    measured['fixture'] = {
      dataset: `${DATASET.files} files × ${DATASET.calls} calls, hubs ${HUBS}×${HUB_FANOUT}`,
      generated: fixture.generated,
      indexed: fixture.indexed,
      hub_occurrence: hubOcc,
      relations_in_snapshot: relationsTotal,
    };
  });

  test.afterAll(() => {
    proc?.kill('SIGTERM');
    mkdirSync(dirname(RESULTS_JSON), { recursive: true });
    writeFileSync(
      RESULTS_JSON,
      JSON.stringify(
        {
          tool: 'code-atlas graph-hardiness e2e (Cycle 8 Phases 4+5, lf-0131)',
          generated_at: new Date().toISOString(),
          environment: {
            platform: platform(),
            os_release: release(),
            cpu: cpus()[0]?.model ?? 'unknown',
            cpu_cores: cpus().length,
            total_mem_gb: Math.round(totalmem() / 1073741824),
            node: process.version,
          },
          measured,
        },
        null,
        2,
      ) + '\n',
    );
    // eslint-disable-next-line no-console
    console.log(`\ngraph-hardiness results → ${RESULTS_JSON}`);
  });

  /* ================================================================== *
   * 1. Rapid navigation stress — /graph → /search → /graph ×10 with an
   *    in-flight layout on the way out. Zombie-worker + leaked-sigma
   *    detection via live counters, heap measured honestly.
   * ================================================================== */
  test('rapid navigation ×10 — in-flight layout killed on every leave', async ({
    page,
  }) => {
    await installProbes(page);
    const pageErrors: string[] = [];
    page.on('pageerror', (e) => pageErrors.push(String(e)));

    await page.goto(`${base}/graph`, { waitUntil: 'domcontentloaded' });
    await waitForCanvas(page);
    await waitLayoutIdle(page);
    const heapBefore = await heapMB(page);
    const tele0 = await telemetry(page);
    expect(tele0).not.toBeNull();

    const cycleRows: Record<string, unknown>[] = [];
    let leftMidFlight = 0;
    const sidebar = page.locator('#shell-sidebar');

    for (let i = 0; i < NAV_CYCLES; i++) {
      // Fresh relayout so a run is provably in flight when we leave.
      await page.getByRole('button', { name: RELAYOUT_RE }).click();
      // aria-busy lands synchronously when the run starts — the honest
      // "was a layout in flight" signal (the worker handle may appear a
      // few ms later and could slip under RAF polling on a fast layout).
      const busyAtClick = await page.locator('.graph-stage').getAttribute('aria-busy');
      // The Cancel affordance exists only while store.layoutBusy is set —
      // the same in-flight signal the async-layout spec relies on.
      const cancelVisible = await page
        .getByRole('button', { name: CANCEL_RE })
        .isVisible()
        .catch(() => false);
      const hadWorker = await page
        .waitForFunction(() => (window.__atlasGraph?.workersLive ?? 0) > 0, undefined, {
          polling: 'raf',
          timeout: 4_000,
        })
        .then(() => true)
        .catch(() => false);

      // Navigate away immediately — destroy mid-layout.
      const t0 = Date.now();
      await sidebar.getByRole('link', { name: 'Busca', exact: true }).click();
      await page.waitForFunction(() => !document.querySelector('.graph-stage'), undefined, {
        timeout: 10_000,
      });
      const away = await telemetry(page);
      const midFlight = busyAtClick === 'true' || cancelVisible || hadWorker;
      cycleRows.push({
        cycle: i,
        layout_busy_at_leave: busyAtClick === 'true',
        cancel_button_visible: cancelVisible,
        worker_seen_live: hadWorker,
        ms_to_leave: Date.now() - t0,
        sigmaLive_on_search: away?.sigmaLive ?? null,
        workersLive_on_search: away?.workersLive ?? null,
      });
      if (midFlight) leftMidFlight++;

      // The hard invariants: nothing survives the destroy.
      expect(away?.sigmaLive, `cycle ${i}: sigma leaked off-graph`).toBe(0);
      expect(away?.workersLive, `cycle ${i}: zombie worker after nav`).toBe(0);

      await sidebar.getByRole('link', { name: 'Grafo', exact: true }).click();
      await waitForCanvas(page);
    }

    await waitLayoutIdle(page);
    await page.waitForTimeout(600); // pending resolves land
    const tele1 = await telemetry(page);
    const heapAfter = await heapMB(page);

    expect(tele1?.sigmaLive).toBe(1); // exactly one live canvas
    expect(tele1?.workersLive).toBe(0);
    // The suite is only meaningful if destroys really happened mid-flight;
    // if layouts finish too fast to catch, that fact itself is recorded.
    expect(leftMidFlight, 'no cycle left while a layout was in flight').toBeGreaterThan(0);
    // Every started run resolved — nothing left hanging.
    const r = tele1!.runs;
    expect(r.started).toBe(r.done + r.cancelled + r.stale + r.error);
    // Workers spawned only for runs that reached an engine.
    expect(tele1!.workersTotal).toBeLessThanOrEqual(r.started);
    // No renderer accumulated: sigmaTotal grew by exactly one per return.
    expect(tele1!.sigmaTotal).toBe((tele0?.sigmaTotal ?? 0) + NAV_CYCLES);
    expect(pageErrors).toEqual([]);

    measured['rapid_navigation'] = {
      cycles: NAV_CYCLES,
      cycles_left_mid_layout: leftMidFlight,
      rows: cycleRows,
      heap_mb: { before: heapBefore, after: heapAfter, delta: heapAfter !== null && heapBefore !== null ? Math.round((heapAfter - heapBefore) * 100) / 100 : null },
      telemetry_after: tele1,
      page_errors: pageErrors,
    };
  });

  /* ================================================================== *
   * 2. Worker killed mid-layout — the watchdog (30 s silence budget) is
   *    the only thing that can notice a terminate()d worker. The UI must
   *    degrade honestly: busy clears, error surfaces, lifecycle recovers.
   * ================================================================== */
  test('worker killed mid-layout → watchdog degrades honestly', async ({ page }) => {
    await installProbes(page);
    await page.goto(`${base}/graph`, { waitUntil: 'domcontentloaded' });
    await waitForCanvas(page);
    await waitLayoutIdle(page);
    const tele0 = await telemetry(page);

    await page.getByRole('button', { name: RELAYOUT_RE }).click();
    await page.waitForFunction(() => (window.__atlasGraph?.workersLive ?? 0) > 0, undefined, {
      polling: 'raf',
      timeout: 10_000,
    });
    const killed = await killLiveWorkers(page);
    expect(killed).toBeGreaterThan(0);
    const t0 = Date.now();

    // The dead worker never speaks again — only the watchdog can finish
    // the run. Measure how long the UI honestly takes to notice.
    await expect(page.getByText(LAYOUT_FAIL_RE)).toBeVisible({ timeout: WATCHDOG_SLA_MS });
    const noticedMs = Date.now() - t0;

    await expect(page.getByRole('button', { name: CANCEL_RE })).toBeHidden();
    await expect(page.locator('.graph-stage')).not.toHaveAttribute('aria-busy', 'true');
    const teleErr = await telemetry(page);
    expect(teleErr?.workersLive).toBe(0);
    expect(teleErr?.runs.error).toBeGreaterThanOrEqual((tele0?.runs.error ?? 0) + 1);
    expect(teleErr?.lastLayoutError).toContain('layout falhou');

    // Error surfaced in the diagnostics panel too, not only the toast.
    await page.getByRole('button', { name: 'Diagnósticos' }).click();
    await expect(
      page.locator('#graph-diagnostics .diag__item', { hasText: 'Erro de layout' }).locator('dd'),
    ).toHaveText(/layout falhou/, { timeout: 3_000 });
    await expect(
      page.locator('#graph-diagnostics .diag__item', { hasText: 'Estado do layout' }).locator('dd'),
    ).toHaveText(/ocioso/);

    // Lifecycle recovers: a fresh relayout completes.
    await page.getByRole('button', { name: RELAYOUT_RE }).click();
    await expect(page.getByRole('button', { name: CANCEL_RE })).toBeVisible({ timeout: 10_000 });
    await waitLayoutIdle(page, 30_000);
    await expect(page.getByRole('button', { name: CANCEL_RE })).toBeHidden();
    const teleOk = await telemetry(page);
    expect(teleOk?.runs.done).toBeGreaterThanOrEqual((teleErr?.runs.done ?? 0) + 1);
    expect(await badPositions(page)).toBe(0);

    measured['worker_crash'] = {
      killed_workers: killed,
      watchdog_notice_ms: noticedMs,
      telemetry_at_error: teleErr,
      telemetry_after_recovery: teleOk,
      note: 'terminate() is silent by design — the 30 s watchdog is the honest detector',
    };
  });

  /* ================================================================== *
   * 3. No usable Worker → chunked main-thread fallback. The layout still
   *    converges, the degraded path is announced, and the main thread is
   *    never held hostage (chunks are ~12 ms).
   * ================================================================== */
  test('Worker unavailable → chunked fallback lays out + reports degraded', async ({
    browser,
  }) => {
    const page = await browser.newPage();
    try {
      await page.addInitScript(() => {
        Object.defineProperty(window, 'Worker', {
          value: undefined,
          writable: true,
          configurable: true,
        });
      });
      await installProbes(page);
      const mark = await page.evaluate(() => performance.now());
      await page.goto(`${base}/graph`, { waitUntil: 'domcontentloaded' });
      await waitForCanvas(page);
      await waitLayoutIdle(page);
      const maxGap = await maxRafGapSince(page, mark);

      const counts = await renderedCounts(page);
      expect(counts.nodes).toBe(500);

      const tele = await telemetry(page);
      expect(tele?.fallbacks).toBeGreaterThanOrEqual(1);
      expect(tele?.workersTotal).toBe(0); // no worker ever spawned
      expect(tele?.workersLive).toBe(0);
      expect(tele?.runs.done).toBeGreaterThanOrEqual(1);

      // Degradation is announced, never silent.
      await expect(page.getByText(FALLBACK_RE)).toBeVisible();
      // Chunked layout still commits real, finite positions.
      expect(await badPositions(page)).toBe(0);
      // And the ~12 ms chunking keeps the main thread honest.
      expect(maxGap).toBeLessThan(1000);

      measured['worker_unavailable_fallback'] = {
        max_raf_gap_ms_during_chunked_layout: maxGap,
        counts_after: counts,
        telemetry: tele,
      };
    } finally {
      await page.close().catch(() => {});
    }
  });

  /* ================================================================== *
   * 4. Concurrent ops against a running layout — relayout racing a cap
   *    change, a filter toggle, a theme flip and an expansion. Generation
   *    discipline: no mixed-state commit, no corrupt geometry, counters
   *    stay consistent.
   * ================================================================== */
  test('relayout racing cap/filter/theme/expand — no mixed-state commit', async ({
    browser,
  }) => {
    const page = await browser.newPage();
    try {
      await installProbes(page);
      const pageErrors: string[] = [];
      page.on('pageerror', (e) => pageErrors.push(String(e)));

      await page.goto(`${base}/graph?focus=${hubOcc}`, { waitUntil: 'domcontentloaded' });
      await waitForCanvas(page);
      await waitLayoutIdle(page);
      const before = await renderedCounts(page);
      expect(before.nodes).toBeGreaterThan(0);
      expect(before.edges).toBeGreaterThan(0);

      const ops: Record<string, unknown>[] = [];

      // Fire a relayout, then storm it with every concurrent op class.
      await page.getByRole('button', { name: RELAYOUT_RE }).click();
      const busy0 = await page
        .locator('.graph-stage')
        .getAttribute('aria-busy', { timeout: 5_000 });
      ops.push({ op: 'relayout', saw_busy: busy0 === 'true' });

      // Cap change mid-flight — a data revision; cancels the run.
      const capInput = page.locator('input.cap__input');
      await capInput.fill('60');
      await capInput.dispatchEvent('change');
      ops.push({ op: 'cap→60' });

      // New relayout on the revised set — storm it again.
      await page.getByRole('button', { name: RELAYOUT_RE }).click();
      ops.push({ op: 'relayout#2' });

      // Filter toggle — view-local, must not disturb the worker run.
      const chip = page.locator('.chip', { hasText: /calls/i }).first();
      if (await chip.count()) {
        await chip.click();
        ops.push({ op: 'filter:calls' });
      }
      // Theme flip — MutationObserver → repaint only.
      await page.getByRole('button', { name: 'Alternar tema claro/escuro' }).click();
      ops.push({ op: 'theme-toggle' });
      // Keyboard expand — fetch lands whenever the API answers.
      await page.locator('.graph-stage').focus();
      await page.keyboard.press(']');
      await page.keyboard.press('Enter');
      await page.keyboard.press('e');
      ops.push({ op: 'kbd-expand' });

      // Let everything settle, then judge the final state.
      await waitLayoutIdle(page, 60_000);
      await page.waitForTimeout(800);
      const after = await renderedCounts(page);
      const tele = await telemetry(page);

      // No mixed commit: geometry finite, busy cleared, no failure text.
      expect(await badPositions(page)).toBe(0);
      await expect(page.getByText(LAYOUT_FAIL_RE)).toHaveCount(0);
      await expect(page.locator('.graph-stage')).not.toHaveAttribute('aria-busy', 'true');
      expect(tele?.workersLive).toBe(0);
      const r = tele!.runs;
      expect(r.started).toBe(r.done + r.cancelled + r.stale + r.error);
      // The cap still holds — rendered nodes never exceed it.
      expect(after.nodes ?? 0).toBeLessThanOrEqual(60);
      expect(pageErrors).toEqual([]);

      measured['concurrent_ops'] = {
        ops,
        counts_before: before,
        counts_after: after,
        telemetry: tele,
        page_errors: pageErrors,
      };
    } finally {
      await page.close().catch(() => {});
    }
  });

  /* ================================================================== *
   * 5. Edge picking on a dense view — real click on the computed edge
   *    midpoint must open the inspector with evidence fields. Edges are
   *    grown via real expansions first (bounded tries, honest count).
   * ================================================================== */
  test('edge click under a dense view opens the inspector', async ({ browser }) => {
    const page = await browser.newPage();
    try {
      await installProbes(page);
      await page.goto(`${base}/graph?focus=${hubOcc}`, { waitUntil: 'domcontentloaded' });
      await waitForCanvas(page);
      await waitLayoutIdle(page);
      const atFocus = await renderedCounts(page);
      expect(atFocus.edges).toBeGreaterThan(0);

      /* Grow the edge set via the real expand path: ] focuses the next
       * node, Enter selects it, 'e' expands it — each merge lands ≤100
       * edges (NEIGHBOR_LIMIT). Bounded tries; the count reached is the
       * honest density, recorded verbatim. */
      const stage = page.locator('.graph-stage');
      await stage.focus();
      let edgesNow = atFocus.edges ?? 0;
      let expansionsDone = 0;
      for (let i = 0; i < EXPAND_MAX_TRIES; i++) {
        await page.keyboard.press(']');
        await page.keyboard.press('Enter');
        await page.keyboard.press('e');
        const grew = await page
          .waitForFunction(
            (prev) => {
              const m = /e (\d+) relações/.exec(
                document.querySelector('.graph-stage')?.getAttribute('aria-label') ?? '',
              );
              return m && Number(m[1]) > prev;
            },
            edgesNow,
            { polling: 'raf', timeout: 3_000 },
          )
          .then(() => true)
          .catch(() => false);
        const now = await renderedCounts(page);
        edgesNow = now.edges ?? edgesNow;
        if (grew) expansionsDone++;
        if (i % 10 === 9) await page.waitForTimeout(150); // let merges breathe
      }
      await waitLayoutIdle(page, 60_000);

      /* Click the longest on-screen edge at its true midpoint — sigma's
       * picking framebuffer resolves it; fall through to ¼/¾ points of
       * the next-longest edges if the first misses. */
      const box = await stage.boundingBox();
      expect(box).not.toBeNull();
      let clicked: { id: string; x: number; y: number; len: number } | null = null;
      let clickLatencyMs: number | null = null;
      const candidates = (await edgeMidpoints(page)).filter(
        (p) => p.x > 8 && p.y > 8 && box && p.x < box.width - 8 && p.y < box.height - 8 && p.len > 6,
      );
      for (const cand of candidates.slice(0, 8)) {
        for (const frac of [0.5, 0.35, 0.65]) {
          const pt = { x: cand.x, y: cand.y };
          if (frac !== 0.5) {
            // slide along the same segment — need endpoints, so recompute
            const seg = await page.evaluate(
              ([edgeId, f]) => {
                const s = (window as unknown as {
                  __atlasGraph?: {
                    sigma: {
                      getGraph(): {
                        source(e: string): string;
                        target(e: string): string;
                        getNodeAttributes(id: string): { x: number; y: number };
                      };
                      graphToViewport(p: { x: number; y: number }): { x: number; y: number };
                    } | null;
                  };
                }).__atlasGraph?.sigma;
                if (!s) return null;
                const g = s.getGraph();
                const a = s.graphToViewport(g.getNodeAttributes(g.source(edgeId)));
                const b = s.graphToViewport(g.getNodeAttributes(g.target(edgeId)));
                return { x: a.x + (b.x - a.x) * (f as number), y: a.y + (b.y - a.y) * (f as number) };
              },
              [cand.id, frac],
            );
            if (!seg) continue;
            pt.x = seg.x;
            pt.y = seg.y;
          }
          const tClick = await page.evaluate(() => performance.now());
          await page.mouse.click(box!.x + pt.x, box!.y + pt.y);
          const hit = await page
            .waitForFunction(
              () =>
                document
                  .querySelector('.inspector__title')
                  ?.textContent?.trim() === 'Relação',
              undefined,
              { polling: 'raf', timeout: 2_000 },
            )
            .then(() => true)
            .catch(() => false);
          if (hit) {
            clickLatencyMs = Math.round(
              (await page.evaluate((t) => performance.now() - t, tClick)) * 100,
            ) / 100;
            clicked = cand;
            break;
          }
        }
        if (clicked) break;
      }

      expect(clicked, 'no edge was pickable among the top candidates').not.toBeNull();
      // Inspector shows the real evidence fields of the selected edge.
      await expect(page.locator('.inspector__kind')).toHaveText(/\w+/);
      await expect(page.getByText('Estado de evidência')).toBeVisible();
      await expect(page.getByText('Evidências')).toBeVisible();

      measured['edge_pick'] = {
        focus: `occ:${hubOcc} (mod_0000.hub_0000)`,
        counts_at_focus: atFocus,
        expansions_done: expansionsDone,
        edges_at_click: edgesNow,
        relations_in_snapshot: relationsTotal,
        candidates_in_bounds: candidates.length,
        clicked_edge: clicked?.id ?? null,
        edge_screen_len_px: clicked?.len ?? null,
        click_to_inspector_ms: clickLatencyMs,
        note: 'edge hover affordance does not exist (no enterEdge handler) — click only',
      };
    } finally {
      await page.close().catch(() => {});
    }
  });

  /* ================================================================== *
   * 6. Long-session leak check — mount/unmount the canvas ×20 via the
   *    Lista↔Grafo view toggle. Each destroy must kill sigma + any live
   *    worker; counters and heap are measured, not assumed.
   * ================================================================== */
  test('mount/unmount canvas ×20 — leaked sigma/workers/listeners', async ({ page }) => {
    await installProbes(page);
    await page.goto(`${base}/graph`, { waitUntil: 'domcontentloaded' });
    await waitForCanvas(page);
    await waitLayoutIdle(page);

    /* Warm-up: first mounts pay one-time costs (JIT, sigma/font/GL
     * caches, Angular internals). 3 toggle cycles before the baseline so
     * the measured delta is steady-state, not first-use noise. */
    for (let i = 0; i < 3; i++) {
      await page.getByRole('button', { name: 'Lista', exact: true }).click();
      await page.waitForSelector('atlas-graph-table', { timeout: 10_000 });
      await page.getByRole('button', { name: 'Grafo', exact: true }).click();
      await waitForCanvas(page, 30_000);
      await waitLayoutIdle(page, 30_000);
    }

    const tele0 = await telemetry(page);
    const heap0 = await heapMB(page);
    const probe0 = await page.evaluate(() => {
      const p = (window as unknown as { __probe: Probe }).__probe;
      return { adds: p.listenerAdds, removes: p.listenerRemoves, globalNet: p.globalNet };
    });

    const rows: Record<string, unknown>[] = [];
    let midLayoutDestroys = 0;
    for (let i = 0; i < MOUNT_CYCLES; i++) {
      // Kill the canvas — 'Lista' swaps in the accessible table.
      await page.getByRole('button', { name: 'Lista', exact: true }).click();
      await page.waitForSelector('atlas-graph-table', { timeout: 10_000 });
      let t = await telemetry(page);
      expect(t?.sigmaLive, `cycle ${i}: sigma alive on table view`).toBe(0);
      expect(t?.workersLive, `cycle ${i}: worker alive on table view`).toBe(0);

      // Remount — a fresh sigma + a fresh layout run (needsLayout stays
      // armed for the revision, so every mount does spawn a worker).
      await page.getByRole('button', { name: 'Grafo', exact: true }).click();
      await waitForCanvas(page, 30_000);
      t = await telemetry(page);
      const inFlight = (t?.workersLive ?? 0) > 0;
      if (inFlight) midLayoutDestroys++; // next Lista click kills mid-run
      rows.push({ cycle: i, workers_live_after_mount: t?.workersLive ?? null });
      expect(t?.sigmaLive, `cycle ${i}: remounted sigma count`).toBe(1);
      // Every even cycle let the layout finish; odd cycles kill mid-run.
      if (i % 2 === 0) await waitLayoutIdle(page, 30_000);
    }

    await page.getByRole('button', { name: 'Lista', exact: true }).click();
    await page.waitForSelector('atlas-graph-table', { timeout: 10_000 });
    await page.waitForTimeout(800); // pending resolves land
    const tele1 = await telemetry(page);
    const heap1 = await heapMB(page);
    const probe1 = await page.evaluate(() => {
      const p = (window as unknown as { __probe: Probe }).__probe;
      return { adds: p.listenerAdds, removes: p.listenerRemoves, globalNet: p.globalNet };
    });

    expect(tele1?.sigmaLive).toBe(0);
    expect(tele1?.workersLive).toBe(0);
    const r = tele1!.runs;
    expect(r.started).toBe(r.done + r.cancelled + r.stale + r.error);
    // Workers spawned only where a run existed.
    expect(tele1!.workersTotal).toBeLessThanOrEqual(r.started);
    // Sigma count grew by exactly the mounts made — none leaked.
    expect(tele1?.sigmaTotal).toBe((tele0?.sigmaTotal ?? 0) + MOUNT_CYCLES);
    // Global-scope listeners must not grow per cycle (canvas-owned
    // listeners die with their elements; window/document ones must not).
    const globalNetDelta = probe1.globalNet - probe0.globalNet;
    expect(globalNetDelta).toBeLessThanOrEqual(MOUNT_CYCLES); // honest bound: ≤1/cycle

    measured['mount_unmount'] = {
      cycles: MOUNT_CYCLES,
      destroys_while_layout_in_flight: midLayoutDestroys,
      heap_mb: {
        before: heap0,
        after: heap1,
        delta: heap0 !== null && heap1 !== null ? Math.round((heap1 - heap0) * 100) / 100 : null,
      },
      listeners: {
        before: probe0,
        after: probe1,
        global_net_delta: globalNetDelta,
        element_bound_net_delta: probe1.adds - probe1.removes - (probe0.adds - probe0.removes) - globalNetDelta,
      },
      telemetry_after: tele1,
      rows: rows.slice(0, 5),
    };
  });
});
