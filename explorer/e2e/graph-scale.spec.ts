import { test, expect, Page } from '@playwright/test';
import { spawn, execFileSync, ChildProcess } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync, readFileSync, rmSync, readdirSync } from 'node:fs';
import { tmpdir, cpus, totalmem, platform, release } from 'node:os';
import { dirname, join } from 'node:path';
import { createServer } from 'node:net';

/**
 * REAL graph-scale benchmark — Cycle 6.1 Phase 5. Nothing estimated:
 * every number below is measured in this Playwright Chromium (headless,
 * SwiftShader WebGL) against a real `code-atlas explorer` subprocess
 * serving the compiled Angular dist + real SQLite atlas.db.
 *
 * For each dataset size N (files → architecture-overview nodes) this spec:
 *   1. guarantees the fixture (tests/fixtures/gen_large_repo.py) and a
 *      per-size index (CODE_ATLAS_DATA_DIR/data-N/atlas.db) exist;
 *   2. times GET /api/v1/graph/overview via curl (5 reps);
 *   3. loads /graph, measures time-to-first-rendered-node, overview fetch
 *      latency (Resource Timing), long tasks / RAF gaps (layout block);
 *   4. samples requestAnimationFrame deltas during a 3 s mouse-drag pan;
 *   5. reads rendered/hidden/truncated counts from the DOM (what the app
 *      actually exposes — the store is not on `window`);
 *   6. measures JS heap via CDP Performance.getMetrics;
 *   7. when the fetched set exceeds the 500-node cap, raises the cap to
 *      2000 and re-measures render + real ForceAtlas2 relayout + pan —
 *      the honest "cap mechanism at the real limit" experiment.
 *
 * Assertions cover only invariants that MUST hold (phase ready, cap math,
 * truncation flags). All timings/FPS/heap are recorded verbatim to
 * benchmarks/results/graph-scale-latest.json — never asserted.
 *
 * Honest scope note: the architecture overview is one node per FILE;
 * edges are not drawn at this level (the API returns no overview edges),
 * so this benchmark exercises node-load + cap + layout, not edge render.
 * The symbol-level drill path is fetch-bounded by design (≤2500 nodes)
 * and is not the scale surface.
 */

const REPO_ROOT = join(__dirname, '..', '..');
const FIXTURE_GEN = join(REPO_ROOT, 'tests', 'fixtures', 'gen_large_repo.py');
const WORK = process.env['ATLAS_SCALE_DIR'] ?? join(tmpdir(), 'atlas-graph-scale');
/* Cycle 8.1 Phase 9 adds 10000 — the declared "10k nodes" ceiling the
 * cycle-6.1 homologation left NOT RUN (the API still truncates to 2000
 * items and the canvas cap to 2000; the dataset exercises the full
 * 10000-known → 2000-fetched → 500/2000-rendered funnel). */
const SIZES = (process.env['ATLAS_SCALE_SIZES'] ?? '100,500,1000,3000,5000,10000')
  .split(',')
  .map((s) => Number(s.trim()))
  .filter((n) => Number.isFinite(n) && n > 0);
const RESULTS_JSON = join(
  REPO_ROOT,
  'benchmarks',
  'results',
  'graph-scale-latest.json',
);
const DEFAULT_CAP = 500; // mirror of DEFAULT_NODE_CAP in graph.models.ts
const MAX_CAP = 2000; // node cap clamp + OVERVIEW_LIMIT
const CURL_REPS = 5;
const PAN_MS = 3000;

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

/** Generate + index fixture N if absent; returns true when work ran. */
function ensureFixture(n: number): { generated: boolean; indexed: boolean; indexMs: number | null } {
  const out = { generated: false, indexed: false, indexMs: null as number | null };
  const repo = repoDir(n);
  if (countModules(repo) !== n) {
    execFileSync(pythonBin(), [FIXTURE_GEN, '--files', String(n), '--out', repo], {
      cwd: REPO_ROOT,
      stdio: 'inherit',
    });
    out.generated = true;
  }
  const db = join(dataDir(n), 'atlas.db');
  if (!existsSync(db)) {
    mkdirSync(dataDir(n), { recursive: true });
    const t0 = Date.now();
    execFileSync(pythonBin(), ['-m', 'code_atlas', 'index', repo], {
      cwd: REPO_ROOT,
      env: { ...process.env, CODE_ATLAS_DATA_DIR: dataDir(n) },
      stdio: 'inherit',
    });
    out.indexed = true;
    out.indexMs = Date.now() - t0;
  }
  return out;
}

async function startExplorer(n: number): Promise<{ proc: ChildProcess; base: string; port: number }> {
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
      if (res.status) return { proc, base, port };
    } catch (err) {
      lastErr = err;
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  proc.kill('SIGTERM');
  throw new Error(`explorer(${n}) did not come up on :${port}: ${String(lastErr)}`);
}

interface CurlTiming {
  samples_ms: number[];
  median_ms: number;
  body: { items: number; total_files: number; truncated: boolean };
}

function curlOverview(base: string): CurlTiming {
  const samples: number[] = [];
  let body: CurlTiming['body'] = { items: 0, total_files: 0, truncated: false };
  for (let i = 0; i < CURL_REPS; i++) {
    // `-w '\n%{time_total}'`: stdout = <body or empty>\n<seconds>.
    const out = execFileSync(
      'curl',
      [
        '-s',
        '-o',
        i === 0 ? '-' : '/dev/null',
        '-w',
        '\n%{time_total}',
        `${base}/api/v1/graph/overview?limit=${MAX_CAP}`,
      ],
      { encoding: 'utf-8' },
    );
    const lines = out.trimEnd().split('\n');
    samples.push(Number(lines[lines.length - 1]) * 1000);
    if (i === 0) {
      const jsonPart = lines.slice(0, -1).join('\n');
      const parsed = JSON.parse(jsonPart) as {
        items: unknown[];
        total_files: number;
        truncated: boolean;
      };
      body = {
        items: parsed.items.length,
        total_files: parsed.total_files,
        truncated: parsed.truncated,
      };
    }
  }
  const sorted = [...samples].sort((a, b) => a - b);
  return { samples_ms: samples, median_ms: sorted[Math.floor(sorted.length / 2)], body };
}

async function heapMB(page: Page): Promise<number | null> {
  try {
    const cdp = await page.context().newCDPSession(page);
    await cdp.send('Performance.enable');
    const { metrics } = await cdp.send('Performance.getMetrics');
    const m = metrics.find((x: { name: string }) => x.name === 'JSHeapUsedSize');
    await cdp.detach();
    return m ? Math.round((m.value / 1048576) * 10) / 10 : null;
  } catch {
    return null;
  }
}

/** Snapshot only the draw counters — never the whole probe (the frames
 *  array is thousands of entries and must not leak into results JSON). */
async function drawStats(page: Page): Promise<{ drawCalls: number; drawVerts: number }> {
  return page.evaluate(() => {
    const p = (window as unknown as { __probe: { drawCalls: number; drawVerts: number } }).__probe;
    return { drawCalls: p.drawCalls, drawVerts: p.drawVerts };
  });
}

interface PanStats {
  frames: number;
  fps_mean: number;
  delta_mean_ms: number;
  delta_p95_ms: number;
  delta_max_ms: number;
  frames_over_33ms: number;
  draw_calls: number;
  draw_verts: number;
}

/** Sample RAF deltas for `ms` while dragging the sigma canvas. */
async function measurePan(page: Page, ms: number): Promise<PanStats> {
  const empty: PanStats = {
    frames: 0, fps_mean: 0, delta_mean_ms: 0, delta_p95_ms: 0,
    delta_max_ms: 0, frames_over_33ms: 0, draw_calls: 0, draw_verts: 0,
  };
  const stage = page.locator('.graph-stage');
  const box = await stage.boundingBox();
  if (!box) return empty;
  const cx = box.x + box.width / 2;
  const cy = box.y + box.height / 2;
  const draws0 = await drawStats(page);

  const sampling = page.evaluate(async (windowMs) => {
    const deltas: number[] = [];
    let last = performance.now();
    const t0 = last;
    await new Promise<void>((resolve) => {
      const tick = (t: number) => {
        deltas.push(t - last);
        last = t;
        if (t - t0 >= windowMs) resolve();
        else requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });
    return deltas;
  }, ms + 400); // small slack so sampling outlives the drag

  // 3 s circular drag (~2 revolutions, ~50 ms steps) — real sigma mouse pan
  // that always stays inside the stage regardless of its size.
  const rx = Math.min(140, box.width / 4);
  const ry = Math.min(90, box.height / 4);
  await page.mouse.move(cx + rx, cy);
  await page.mouse.down();
  const steps = Math.floor(ms / 50);
  for (let i = 1; i <= steps; i++) {
    const a = (i / steps) * Math.PI * 4;
    await page.mouse.move(cx + rx * Math.cos(a), cy + ry * Math.sin(a));
    await page.waitForTimeout(50);
  }
  await page.mouse.up();

  // All RAF deltas of the window: fps = frames / elapsed — self-consistent.
  const deltas = await sampling;
  const clean = deltas.filter((d) => d > 0);
  const sorted = [...clean].sort((a, b) => a - b);
  const sum = clean.reduce((a, b) => a + b, 0);
  const windowS = sum / 1000 || ms / 1000;
  const draws1 = await drawStats(page);
  return {
    frames: clean.length,
    fps_mean: Math.round((clean.length / windowS) * 10) / 10,
    delta_mean_ms: Math.round((sum / (clean.length || 1)) * 100) / 100,
    delta_p95_ms: Math.round((sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.95))] ?? 0) * 100) / 100,
    delta_max_ms: Math.round((sorted[sorted.length - 1] ?? 0) * 100) / 100,
    frames_over_33ms: clean.filter((d) => d > 33.4).length,
    draw_calls: draws1.drawCalls - draws0.drawCalls,
    draw_verts: draws1.drawVerts - draws0.drawVerts,
  };
}

interface ActionBlock {
  /** Largest single RAF gap after `mark` — the synchronous layout block. */
  max_gap_ms: number;
  /** Frame-clock span from `mark` to the last observed frame — ≈ the
   *  block + settle margin when a block happened. */
  settle_ms: number;
}

/**
 * Measure the main-thread block caused by an action (cap change, the
 * "Reorganizar" ForceAtlas2 relayout). Angular signal effects run in a
 * LATER task than the DOM event, so timing the click itself misses the
 * block. Instead we wait until RAF timestamps advance `settleMs` past
 * `mark` — during a synchronous block RAF stalls, so this resolves right
 * after the block ends (or ~settleMs later when there was no block).
 */
async function measureActionBlock(
  page: Page,
  action: () => Promise<void>,
  settleMs = 1600,
  timeoutMs = 90_000,
): Promise<ActionBlock> {
  const mark = await page.evaluate(() => performance.now());
  await action();
  await page.waitForFunction(
    ([m, s]) => {
      const f = (window as unknown as { __probe: { frames: { t: number; d: number }[] } }).__probe.frames;
      return f.length > 0 && f[f.length - 1].t - m > (s as number);
    },
    [mark, settleMs],
    { timeout: timeoutMs },
  );
  const end = await page.evaluate(
    (m) => {
      const f = (window as unknown as { __probe: { frames: { t: number; d: number }[] } }).__probe.frames;
      let max = 0;
      let last = m as number;
      for (const fr of f) {
        if (fr.t >= m) {
          if (fr.d > max) max = fr.d;
          last = fr.t;
        }
      }
      return { max, last };
    },
    mark,
  );
  return {
    max_gap_ms: Math.round(end.max * 100) / 100,
    settle_ms: Math.round((end.last - mark) * 100) / 100,
  };
}

async function readCounts(page: Page): Promise<{
  rendered: number | null;
  edges: number | null;
  hidden: number | null;
  known: number | null;
  noticeText: string | null;
  mode: 'canvas' | 'table' | 'none';
}> {
  return page.evaluate(() => {
    const stage = document.querySelector('.graph-stage');
    const label = stage?.getAttribute('aria-label') ?? '';
    const rm = /(\d+) nós visíveis/.exec(label);
    const em = /e (\d+) relações/.exec(label);
    const notice = document.querySelector('.notice--warning');
    const text = notice?.textContent ?? null;
    const hm = text ? /(\d+) ocultos/.exec(text) : null;
    const km = text ? /de\s+(\d+)\s+nós conhecidos/.exec(text) : null;
    return {
      rendered: rm ? Number(rm[1]) : null,
      edges: em ? Number(em[1]) : null,
      hidden: hm ? Number(hm[1]) : 0,
      known: km ? Number(km[1]) : null,
      noticeText: text ? text.replace(/\s+/g, ' ').trim() : null,
      mode: stage ? 'canvas' : document.querySelector('atlas-graph-table') ? 'table' : 'none',
    };
  });
}

interface SizeResult {
  size: number;
  fixture: { generated: boolean; indexed: boolean; indexMs: number | null };
  api: CurlTiming;
  measured: Record<string, unknown>;
}

const results: SizeResult[] = [];

/* Traces/screenshots off for the whole file: `retain-on-failure` races
 * page.close() in this heavy suite (ENOENT on recording1.trace / artifacts
 * zip) and adds nothing to a numbers-only benchmark. Must be file-level —
 * worker-scoped fixtures are not allowed inside a describe group. */
test.use({ trace: 'off', video: 'off', screenshot: 'off' });

test.describe('graph scale — real browser benchmarks', () => {
  // Heavy suite: per test = server boot + 5 curls + load + 2×3s pans +
  // cap raise + up to two ForceAtlas2 passes (~2.3 s at 2000 nodes).
  test.describe.configure({ timeout: 300_000 });

  test.beforeAll(() => {
    mkdirSync(WORK, { recursive: true });
    mkdirSync(dirname(RESULTS_JSON), { recursive: true });
    for (const n of SIZES) {
      // fresh per-size record — a stale file would poison the merged run
      const stale = join(dirname(RESULTS_JSON), `graph-scale-${n}.json`);
      if (existsSync(stale)) rmSync(stale, { force: true });
      const r = ensureFixture(n);
      results.push({ size: n, fixture: r, api: null as unknown as CurlTiming, measured: {} });
    }
  });

  for (const n of SIZES) {
    test(`architecture overview @ ${n} file nodes`, async ({ browser }) => {
      const rec = results.find((r) => r.size === n)!;
      const { proc, base } = await startExplorer(n);
      const page = await browser.newPage();
      try {
        /* ---- environment ------------------------------------------------ */
        const env = await page.evaluate(() => {
          const c = document.createElement('canvas');
          const gl = c.getContext('webgl2');
          const ext = gl?.getExtension('WEBGL_debug_renderer_info');
          return {
            webgl2: !!gl,
            renderer: ext ? String(gl?.getParameter(ext.UNMASKED_RENDERER_WEBGL)) : null,
            hardwareConcurrency: navigator.hardwareConcurrency,
            devicePixelRatio: window.devicePixelRatio,
          };
        });

        /* ---- API latency (curl, outside the browser) -------------------- */
        rec.api = curlOverview(base);
        const fetched = rec.api.body.items;
        const expectedRendered = Math.min(fetched, DEFAULT_CAP);
        const expectedHidden = Math.max(0, fetched - DEFAULT_CAP);

        /* ---- load /graph with RAF + longtask + WebGL draw probes -------- */
        await page.addInitScript(() => {
          (window as unknown as { __probe: unknown }).__probe = {
            frames: [] as { t: number; d: number }[],
            longtasks: [] as { start: number; duration: number }[],
            drawCalls: 0,
            drawVerts: 0,
          };
          let last = performance.now();
          const loop = (t: number) => {
            const p = (window as unknown as { __probe: { frames: { t: number; d: number }[] } }).__probe;
            p.frames.push({ t, d: t - last });
            last = t;
            requestAnimationFrame(loop);
          };
          requestAnimationFrame(loop);
          try {
            new PerformanceObserver((l) => {
              const p = (window as unknown as { __probe: { longtasks: { start: number; duration: number }[] } }).__probe;
              for (const e of l.getEntries()) p.longtasks.push({ start: e.startTime, duration: e.duration });
            }).observe({ entryTypes: ['longtask'] });
          } catch {
            /* longtask unsupported — recorded as empty */
          }
          // Canvas-side evidence: sigma paints via glDrawElements/drawArrays.
          // drawCalls = render activity, drawVerts ~ rendered geometry scale.
          const proto = (window as unknown as { __probe: { drawCalls: number; drawVerts: number } }).__probe;
          for (const glProto of [WebGL2RenderingContext.prototype, WebGLRenderingContext.prototype]) {
            const gl = glProto as unknown as Record<string, ((...a: number[]) => void) | undefined>;
            for (const fn of ['drawElements', 'drawArrays', 'drawElementsInstanced', 'drawArraysInstanced'] as const) {
              const orig = gl[fn];
              if (!orig) continue;
              // Vertex load ≈ count × instanceCount. `count` index:
              //   drawElements*(mode,count,…)      → 1
              //   drawArrays*(mode,first,count)     → 2
              // `instanceCount` index:
              //   drawElementsInstanced(…,offset,ic) → 4
              //   drawArraysInstanced(mode,first,count,ic) → 3
              const isArrays = fn.startsWith('drawArrays');
              const countIdx = isArrays ? 2 : 1;
              const instIdx = fn === 'drawElementsInstanced' ? 4 : fn === 'drawArraysInstanced' ? 3 : -1;
              (glProto as unknown as Record<string, unknown>)[fn] = function (
                this: WebGL2RenderingContext,
                ...args: number[]
              ) {
                proto.drawCalls++;
                proto.drawVerts +=
                  Number(args[countIdx] ?? 0) * (instIdx >= 0 ? Math.max(1, Number(args[instIdx] ?? 1)) : 1);
                return orig.apply(this, args as never);
              };
            }
          }
        });

        const heapBefore = await heapMB(page);
        const t0 = Date.now();
        await page.goto(`${base}/graph`, { waitUntil: 'domcontentloaded' });

        // Time-to-first-rendered-node: stage aria-label gains the live count
        // only after phase=ready; RAF-polling returns post-layout/paint.
        const readyTs = await page.waitForFunction(
          () => {
            const el = document.querySelector('.graph-stage');
            if (el && /\d+ nós visíveis/.test(el.getAttribute('aria-label') ?? '')) {
              return performance.now();
            }
            return false;
          },
          undefined,
          { polling: 'raf', timeout: 60_000 },
        );
        const readyMs = await readyTs.jsonValue();
        const wallNavToReady = Date.now() - t0;

        const resource = await page.evaluate(() => {
          const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined;
          const api = performance
            .getEntriesByType('resource')
            .find((r) => r.name.includes('/api/v1/graph/overview')) as PerformanceResourceTiming | undefined;
          const repos = performance
            .getEntriesByType('resource')
            .find((r) => r.name.includes('/api/v1/repositories')) as PerformanceResourceTiming | undefined;
          const probe = (window as unknown as { __probe: { frames: { t: number; d: number }[]; longtasks: { start: number; duration: number }[] } }).__probe;
          const gaps = probe.frames.map((f) => f.d);
          return {
            navDuration: nav ? Math.round(nav.duration * 100) / 100 : null,
            overviewApi: api
              ? {
                  start: Math.round(api.startTime * 100) / 100,
                  duration: Math.round(api.duration * 100) / 100,
                  responseEnd: Math.round(api.responseEnd * 100) / 100,
                  transferSize: api.transferSize,
                  encodedBodySize: api.encodedBodySize,
                }
              : null,
            reposApi: repos ? Math.round(repos.duration * 100) / 100 : null,
            maxRafGap: Math.round((gaps.length ? Math.max(...gaps) : 0) * 100) / 100,
            longtasks: probe.longtasks.map((l) => Math.round(l.duration * 100) / 100),
          };
        });

        const counts = await readCounts(page);
        expect(counts.mode, 'WebGL canvas mode expected (sigma)').toBe('canvas');
        expect(counts.rendered).toBe(expectedRendered);
        expect(counts.hidden).toBe(expectedHidden);
        expect(rec.api.body.truncated).toBe(rec.api.body.total_files > fetched);

        /* ---- pan FPS @ default cap -------------------------------------- */
        const heapAfterRender = await heapMB(page);
        const pan = await measurePan(page, PAN_MS);
        const heapAfterPan = await heapMB(page);

        /* ---- cap mechanism + relayout — ORDER MATTERS ---------------------
         * PRODUCT BUG (verified live, see docs/graph-performance-homologation.md):
         * the canvas `effect(() => sync())` dies permanently after the FIRST
         * user-triggered relayout — `runLayout` writes the `nodes` signal via
         * `reportPositions` inside the effect that reads it. Every later
         * dataVersion bump (cap change, expansion, selection, second
         * relayout) never reaches sigma: 0 draw calls. So the cap raise MUST
         * happen BEFORE the first relayout, or the canvas silently stays at
         * the old node set while the store label claims otherwise. */
        let capExperiment: Record<string, unknown> | null = null;
        let relayout: { max_gap_ms: number; settle_ms: number } | null = null;
        if (fetched > DEFAULT_CAP) {
          const raisedRendered = Math.min(fetched, MAX_CAP);
          const capInput = page.locator('input.cap__input');
          const apply = await measureActionBlock(page, async () => {
            await capInput.fill(String(MAX_CAP));
            await capInput.dispatchEvent('change');
            await page.waitForFunction(
              (expected) => {
                const el = document.querySelector('.graph-stage');
                const m = /(\d+) nós visíveis/.exec(el?.getAttribute('aria-label') ?? '');
                return m && Number(m[1]) === expected ? performance.now() : false;
              },
              raisedRendered,
              { polling: 'raf', timeout: 30_000 },
            );
          });
          const counts2 = await readCounts(page);
          expect(counts2.rendered).toBe(raisedRendered);
          expect(counts2.hidden).toBe(Math.max(0, fetched - raisedRendered));
          const drawsAfterRaise = await drawStats(page);

          const pan2 = await measurePan(page, PAN_MS);

          /* First user relayout — the REAL ForceAtlas2 layout on the raised
           * set while the effect is still alive (~2.3 s block at 2000 nodes,
           * 80 iterations, no Barnes-Hut). */
          const relayoutAtCap = await measureActionBlock(
            page,
            () => page.getByRole('button', { name: 'Reorganizar' }).click(),
            1600,
            90_000,
          );
          const heapAtCap = await heapMB(page);
          capExperiment = {
            raised_to: MAX_CAP,
            rendered: counts2.rendered,
            hidden: counts2.hidden,
            apply: { max_gap_ms: apply.max_gap_ms, settle_ms: apply.settle_ms },
            draw_totals_after_raise: drawsAfterRaise,
            pan_at_cap: pan2,
            relayout_at_cap: { max_gap_ms: relayoutAtCap.max_gap_ms, settle_ms: relayoutAtCap.settle_ms },
            heap_after_mb: heapAtCap,
          };
        } else {
          /* Rendered set fits the cap — measure FA2 on that set. */
          relayout = await measureActionBlock(page, () =>
            page.getByRole('button', { name: 'Reorganizar' }).click(),
          );
        }

        /* ---- BUG EVIDENCE: the relayout AFTER the first one is a no-op ----
         * Independent of size, the second "Reorganizar" click produces zero
         * draw calls and no RAF block — the canvas froze after the first. */
        const drawsBeforeBug = await drawStats(page);
        const bugProbe = await measureActionBlock(page, () =>
          page.getByRole('button', { name: 'Reorganizar' }).click(),
          1200,
        );
        const drawsAfterBug = await drawStats(page);
        const drawsInBugWindow = drawsAfterBug.drawCalls - drawsBeforeBug.drawCalls;

        rec.measured = {
          env,
          wall_nav_to_ready_ms: wallNavToReady,
          ready_ms_page_clock: Math.round((readyMs as number) * 100) / 100,
          resource,
          counts,
          heap_mb: { before_load: heapBefore, after_render: heapAfterRender, after_pan: heapAfterPan },
          pan_at_default_cap: pan,
          relayout,
          cap_experiment: capExperiment,
          /* Product bug evidence — the relayout AFTER the first one never
           * reaches sigma: effect dead ⇒ max_gap ~frame, drawCalls delta 0. */
          second_relayout_probe: {
            max_gap_ms: bugProbe.max_gap_ms,
            settle_ms: bugProbe.settle_ms,
            draw_calls_in_window: drawsInBugWindow,
            /* honest evidence, observed live: effect dead ⇒ no block, no paint */
            canvas_frozen_after_first_relayout:
              bugProbe.max_gap_ms < 50 && drawsInBugWindow === 0,
          },
        };
        /* Persist THIS test's record immediately — hook lifecycle quirks
         * (afterAll firing early/twice) must not lose measured data. */
        writeFileSync(
          join(dirname(RESULTS_JSON), `graph-scale-${n}.json`),
          JSON.stringify(rec, null, 2) + '\n',
        );
      } finally {
        // Tolerate teardown races — the record is already persisted; the
        // explorer subprocess MUST still be reaped.
        await page.close().catch(() => {});
        proc.kill('SIGTERM');
      }
    });
  }

  test.afterAll(async () => {
    /* Merge the per-size records the tests persisted — the authoritative
     * data lives in graph-scale-{n}.json; this file is the summary view. */
    const dir = dirname(RESULTS_JSON);
    const merged = SIZES.map((n) => {
      const file = join(dir, `graph-scale-${n}.json`);
      if (existsSync(file)) return JSON.parse(readFileSync(file, 'utf-8')) as SizeResult;
      return results.find((r) => r.size === n) ?? null;
    }).filter(Boolean);
    const summary = {
      tool: 'code-atlas graph-scale e2e (Cycle 6.1 Phase 5)',
      generated_at: new Date().toISOString(),
      work_dir: WORK,
      sizes: SIZES,
      caps: { default_node_cap: DEFAULT_CAP, max_cap: MAX_CAP, overview_limit: MAX_CAP },
      environment: {
        platform: platform(),
        os_release: release(),
        cpu: cpus()[0]?.model ?? 'unknown',
        cpu_cores: cpus().length,
        total_mem_gb: Math.round(totalmem() / 1073741824),
        node: process.version,
      },
      results: merged.length ? merged : results,
    };
    mkdirSync(dir, { recursive: true });
    writeFileSync(RESULTS_JSON, JSON.stringify(summary, null, 2) + '\n');
    // eslint-disable-next-line no-console
    console.log(`\ngraph-scale results → ${RESULTS_JSON}`);
  });
});
