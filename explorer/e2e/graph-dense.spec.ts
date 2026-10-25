import { test, expect, Page } from '@playwright/test';
import { spawn, execFileSync, ChildProcess } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync, readFileSync, rmSync, readdirSync } from 'node:fs';
import { tmpdir, cpus, totalmem, platform, release } from 'node:os';
import { dirname, join } from 'node:path';
import { createServer } from 'node:net';

/**
 * REAL graph-density benchmark — Cycle 7 Phase 0.3. The graph-scale spec
 * measures file-node overviews, which carry ZERO edges; real dependency
 * graphs are dense. This spec measures the dense surface: the symbol-level
 * ego network that `GET /graph/neighbors` expands around one occurrence
 * (`/graph?focus={occ_id}` deep link + per-node lazy expansion + file drill).
 *
 * Fixture (tests/fixtures/gen_large_repo.py, still deterministic):
 *   N flat modules; every func_{i} calls `calls` distinct neighbours and
 *   imports them (syntactic resolution of `mod_j.func_j` requires the
 *   import). `hubs` first modules gain `hub_{h}` calling `fanout` distinct
 *   targets — a high-fan-out symbol whose ego fetch exceeds the client's
 *   NEIGHBOR_LIMIT (100 edges; server cap 200).
 *
 * RENDERER REALITY (measured, not assumed — see docs/graph-dense-benchmarks.md):
 *   sigma 3.0.2 `eachEdgeAttributesUpdatedGraphUpdate` treats "no hints" as
 *   "layout unchanged" (the node handler does the opposite), so the batched
 *   `graph.updateEachEdgeAttributes()` in paintAll() requests a
 *   skipIndexation repaint for every edge. Edges added since the last
 *   process() have no edgeProgramIndex → `Sigma: edge "…" can't be repaint`.
 *   - Canvas MOUNTS with edges already in the store (`?focus=` deep link,
 *     table→graph remount): the throw lands inside ngAfterViewInit's
 *     try/catch → markRendererFailed() → permanent accessible-table
 *     fallback. FATAL for any non-empty edge set.
 *   - Edges arrive while the canvas is already mounted (expand/drill/
 *     cap-restore): the throw escapes through the Angular effect as an
 *     unhandled error, but the edgeAdded-scheduled RAF render runs
 *     process() afterwards → self-heals. TRANSIENT, canvas survives.
 *   This spec therefore measures BOTH surfaces per dataset:
 *   Phase A `?focus=hub` — the surface a real user gets today, whatever it
 *     is, with the fatal throw recorded verbatim; then the table
 *     expansion/collapse path (the only surface guaranteed to exist).
 *   Phase B arch→drill — mount the canvas on the zero-edge overview, then
 *     bring dense edges in-session via drillFile(); pan FPS, canvas
 *     expansion, cap collapse/restore, and the table→graph remount
 *     (expected fatal again).
 *
 * Assertions cover only invariants (a surface renders, edges>0,
 * truncation notice, RAF keeps ticking, no >5 s main-thread freeze).
 * Renderer mode, sigma error text and every timing go verbatim to
 * benchmarks/results/graph-dense-latest.json.
 */

const REPO_ROOT = join(__dirname, '..', '..');
const FIXTURE_GEN = join(REPO_ROOT, 'tests', 'fixtures', 'gen_large_repo.py');
const WORK = process.env['ATLAS_DENSE_DIR'] ?? join(tmpdir(), 'atlas-graph-dense');
const RESULTS_JSON = join(
  REPO_ROOT,
  'benchmarks',
  'results',
  'graph-dense-latest.json',
);

interface DenseDataset {
  label: string;
  files: number;
  calls: number;
}

/* Datasets: `files:calls` — the CALLS out-degree of every ring function
 * (≈ "each module calls M others" → N*M CALLS edges). Defaults match the
 * phase brief: 500n/1ke, 1000n/5ke, 2000n/10ke of ring CALLS alone. */
const DATASETS: DenseDataset[] = (
  process.env['ATLAS_DENSE_DATASETS'] ?? '500:2,1000:5,2000:5'
)
  .split(',')
  .map((s) => s.trim())
  .filter(Boolean)
  .map((s) => {
    const [f, c] = s.split(':').map(Number);
    return { label: `${f}-c${c}`, files: f, calls: c };
  });
const HUBS = Number(process.env['ATLAS_DENSE_HUBS'] ?? 3);
const HUB_FANOUT = Number(process.env['ATLAS_DENSE_FANOUT'] ?? 150);
const EXPANSIONS = Number(process.env['ATLAS_DENSE_EXPANSIONS'] ?? 15);
const CURL_REPS = 5;
const PAN_MS = 3000;
const CLIENT_NEIGHBOR_LIMIT = 100; // NEIGHBOR_LIMIT in graph.models.ts
const SERVER_NEIGHBOR_CAP = 200; // _int(..., 200) in api/handlers.py
const FREEZE_MS = 5000;
/** `]` presses tried while hunting mod_0000.py in the arch view. */
const DRILL_TARGET_TRIES = 80;
/** Symbol nodes tried for the canvas-side 'e' expansion. */
const CANVAS_EXPAND_TRIES = 4;

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

function repoDir(d: DenseDataset): string {
  return join(WORK, `repo-dense-${d.files}-c${d.calls}-h${HUBS}x${HUB_FANOUT}`);
}
function dataDir(d: DenseDataset): string {
  return join(WORK, `data-dense-${d.files}-c${d.calls}-h${HUBS}x${HUB_FANOUT}`);
}

function countModules(dir: string): number {
  if (!existsSync(dir)) return 0;
  return readdirSync(dir).filter((f) => /^mod_\d+\.py$/.test(f)).length;
}

/** Generate + index fixture if absent; returns true when work ran. */
function ensureFixture(d: DenseDataset): { generated: boolean; indexed: boolean; indexMs: number | null } {
  const out = { generated: false, indexed: false, indexMs: null as number | null };
  const repo = repoDir(d);
  if (countModules(repo) !== d.files) {
    execFileSync(
      pythonBin(),
      [
        FIXTURE_GEN,
        '--files', String(d.files),
        '--calls', String(d.calls),
        '--hubs', String(HUBS),
        '--hub-fanout', String(HUB_FANOUT),
        '--out', repo,
      ],
      { cwd: REPO_ROOT, stdio: 'inherit' },
    );
    out.generated = true;
  }
  const db = join(dataDir(d), 'atlas.db');
  if (!existsSync(db)) {
    mkdirSync(dataDir(d), { recursive: true });
    const t0 = Date.now();
    execFileSync(pythonBin(), ['-m', 'code_atlas', 'index', repo], {
      cwd: REPO_ROOT,
      env: { ...process.env, CODE_ATLAS_DATA_DIR: dataDir(d) },
      stdio: 'inherit',
    });
    out.indexed = true;
    out.indexMs = Date.now() - t0;
  }
  return out;
}

interface DbStats {
  snapshot_id: number;
  relations_total: number;
  by_type: Record<string, number>;
  by_status: Record<string, number>;
  occurrences: number;
  symbols: number;
  files: number;
  hubs: Record<string, number>;
}

/** Read the REAL index contents — never an estimate from the generator. */
function dbStats(d: DenseDataset): DbStats {
  const script = `
import sqlite3, json, sys
conn = sqlite3.connect(sys.argv[1])
sid = conn.execute("SELECT MAX(id) FROM snapshots WHERE status='READY'").fetchone()[0]
out = {
 "snapshot_id": sid,
 "relations_total": conn.execute("SELECT COUNT(*) FROM relations WHERE snapshot_id=?", (sid,)).fetchone()[0],
 "by_type": dict(conn.execute("SELECT type, COUNT(*) FROM relations WHERE snapshot_id=? GROUP BY type", (sid,)).fetchall()),
 "by_status": dict(conn.execute("SELECT resolution_status, COUNT(*) FROM relations WHERE snapshot_id=? GROUP BY resolution_status", (sid,)).fetchall()),
 "occurrences": conn.execute("SELECT COUNT(*) FROM symbol_occurrences WHERE snapshot_id=?", (sid,)).fetchone()[0],
 "symbols": conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0],
 "files": conn.execute("SELECT COUNT(*) FROM source_files").fetchone()[0],
 "hubs": {q: o for q, o in conn.execute(
    "SELECT s.qualified_name, o.id FROM symbol_occurrences o JOIN symbols s ON s.id=o.logical_symbol_id"
    " WHERE o.snapshot_id=? AND s.qualified_name GLOB 'mod_*.hub_*' ORDER BY 1", (sid,)).fetchall()},
}
print(json.dumps(out))
`;
  const out = execFileSync(pythonBin(), ['-c', script, join(dataDir(d), 'atlas.db')], {
    encoding: 'utf-8',
  });
  return JSON.parse(out) as DbStats;
}

async function startExplorer(d: DenseDataset): Promise<{ proc: ChildProcess; base: string; port: number }> {
  const port = await freePort();
  const proc = spawn(pythonBin(), ['-m', 'code_atlas', 'explorer', '--port', String(port)], {
    cwd: REPO_ROOT,
    env: { ...process.env, CODE_ATLAS_DATA_DIR: dataDir(d) },
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
  throw new Error(`explorer(${d.label}) did not come up on :${port}: ${String(lastErr)}`);
}

interface CurlNeighbors {
  samples_ms: number[];
  median_ms: number;
  edges: number;
  nodes: number;
  truncated: boolean;
}

function curlNeighbors(base: string, occ: number, limit: number): CurlNeighbors {
  const samples: number[] = [];
  let body = { edges: 0, nodes: 0, truncated: false };
  for (let i = 0; i < CURL_REPS; i++) {
    const out = execFileSync(
      'curl',
      [
        '-s',
        '-o',
        i === 0 ? '-' : '/dev/null',
        '-w',
        '\n%{time_total}',
        `${base}/api/v1/graph/neighbors?occurrence_id=${occ}&direction=both&limit=${limit}`,
      ],
      { encoding: 'utf-8' },
    );
    const lines = out.trimEnd().split('\n');
    samples.push(Number(lines[lines.length - 1]) * 1000);
    if (i === 0) {
      const parsed = JSON.parse(lines.slice(0, -1).join('\n')) as {
        edges: unknown[];
        nodes: unknown[];
        truncated: boolean;
      };
      body = { edges: parsed.edges.length, nodes: parsed.nodes.length, truncated: parsed.truncated };
    }
  }
  const sorted = [...samples].sort((a, b) => a - b);
  return { samples_ms: samples, median_ms: sorted[Math.floor(sorted.length / 2)], ...body };
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

const EMPTY_PAN: PanStats = {
  frames: 0, fps_mean: 0, delta_mean_ms: 0, delta_p95_ms: 0,
  delta_max_ms: 0, frames_over_33ms: 0, draw_calls: 0, draw_verts: 0,
};

/** Sample RAF deltas for `ms` while dragging the sigma canvas. */
async function measurePan(page: Page, ms: number): Promise<PanStats> {
  const stage = page.locator('.graph-stage');
  const box = await stage.boundingBox();
  if (!box) return { ...EMPTY_PAN };
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
  }, ms + 400);

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
  /** Largest single RAF gap after `mark` — the synchronous block. */
  max_gap_ms: number;
  /** Frame-clock span from `mark` to the last observed frame. */
  settle_ms: number;
}

/**
 * Largest RAF gap produced by `action` — same technique as graph-scale:
 * wait until the RAF clock advances `settleMs` past `mark`, then read the
 * biggest recorded gap. Resolves right after a synchronous block ends.
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

interface SurfaceCounts {
  /** Node count visible on the active surface (canvas label or table caption). */
  rendered: number | null;
  edges: number | null;
  hidden: number | null;
  known: number | null;
  noticeText: string | null;
  mode: 'canvas' | 'table' | 'none';
  /** True while the WebGL-fallback banner is on screen. */
  rendererFailed: boolean;
}

async function readCounts(page: Page): Promise<SurfaceCounts> {
  return page.evaluate(() => {
    const stage = document.querySelector('.graph-stage');
    const label = stage?.getAttribute('aria-label') ?? '';
    const rm = /(\d+) nós visíveis/.exec(label);
    const em = /e (\d+) relações/.exec(label);
    const notice = document.querySelector('.notice--warning');
    const text = notice?.textContent ?? null;
    const hm = text ? /(\d+) ocultos/.exec(text) : null;
    const km = text ? /de\s+(\d+)\s+nós conhecidos/.exec(text) : null;
    let rendered = rm ? Number(rm[1]) : null;
    let edges = em ? Number(em[1]) : null;
    const tableText = document.querySelector('atlas-graph-table')?.textContent ?? '';
    if (rendered === null) {
      const tm = /Nós \((\d+)\)/.exec(tableText);
      rendered = tm ? Number(tm[1]) : null;
    }
    if (edges === null) {
      const tm = /Relações \((\d+)\)/.exec(tableText);
      edges = tm ? Number(tm[1]) : null;
    }
    const warnings = Array.from(document.querySelectorAll('.notice--warning')).map((n) =>
      (n.textContent ?? '').replace(/\s+/g, ' ').trim(),
    );
    return {
      rendered,
      edges,
      hidden: hm ? Number(hm[1]) : 0,
      known: km ? Number(km[1]) : null,
      noticeText: text ? text.replace(/\s+/g, ' ').trim() : null,
      mode: stage ? 'canvas' : document.querySelector('atlas-graph-table') ? 'table' : 'none',
      rendererFailed: warnings.some((w) => w.includes('WebGL indisponível')),
    };
  });
}

/** Node/edge totals as the accessible table reports them (canvas-free). */
async function tableCounts(page: Page): Promise<{ nodes: number; edges: number }> {
  return page.evaluate(() => {
    const text = document.querySelector('atlas-graph-table')?.textContent ?? '';
    const nm = /Nós \((\d+)\)/.exec(text);
    const em = /Relações \((\d+)\)/.exec(text);
    return { nodes: nm ? Number(nm[1]) : 0, edges: em ? Number(em[1]) : 0 };
  });
}

type Surface = 'canvas' | 'table' | 'none';

/**
 * Wait until the page exposes ANY usable graph surface — a sigma stage
 * with a populated aria-label or the accessible table — and report which.
 * 'none' means an atlas-state-block/empty shell appeared instead.
 */
async function waitForSurface(page: Page, timeout = 60_000): Promise<Surface> {
  const handle = await page.waitForFunction(
    () => {
      const stage = document.querySelector('.graph-stage');
      if (stage && /\d+ nós visíveis/.test(stage.getAttribute('aria-label') ?? '')) {
        return 'canvas';
      }
      if (document.querySelector('atlas-graph-table tbody tr')) return 'table';
      /* 'loading' is transient — only error/offline/empty blocks mean the
       * load genuinely produced no graph surface. */
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

/**
 * Wait until the ACTIVE surface reports `nodes`/`edges` (canvas aria-label
 * or table captions — whichever exists). Returns the surface seen.
 */
async function waitSurfaceCounts(
  page: Page,
  nodes: number | null,
  edges: number | null,
  timeout = 30_000,
): Promise<Surface> {
  const handle = await page.waitForFunction(
    ([wantN, wantE]) => {
      const stage = document.querySelector('.graph-stage');
      const label = stage?.getAttribute('aria-label') ?? '';
      const tableText = document.querySelector('atlas-graph-table')?.textContent ?? '';
      let n: number | null = null;
      let e: number | null = null;
      let surface: string | null = null;
      if (stage) {
        const nm = /(\d+) nós visíveis/.exec(label);
        const em = /e (\d+) relações/.exec(label);
        n = nm ? Number(nm[1]) : null;
        e = em ? Number(em[1]) : null;
        surface = 'canvas';
      } else if (tableText) {
        const nm = /Nós \((\d+)\)/.exec(tableText);
        const em = /Relações \((\d+)\)/.exec(tableText);
        n = nm ? Number(nm[1]) : null;
        e = em ? Number(em[1]) : null;
        surface = 'table';
      }
      if (!surface) return false;
      if (wantN !== null && n !== wantN) return false;
      if (wantE !== null && e !== wantE) return false;
      return surface;
    },
    [nodes, edges],
    { polling: 'raf', timeout },
  );
  return (await handle.jsonValue()) as Surface;
}

/** Wait `n` animation frames — bounds the post-commit sigma process() heal. */
async function waitFrames(page: Page, n = 3): Promise<void> {
  await page.evaluate(
    (count) =>
      new Promise<void>((resolve) => {
        const tick = (left: number) =>
          left <= 0 ? resolve() : requestAnimationFrame(() => tick(left - 1));
        tick(count);
      }),
    n,
  );
}

/** RAF still ticking = main thread alive. Throws when frozen >2 s. */
async function assertRafAlive(page: Page): Promise<number> {
  const lastFrame = await page.evaluate(() => {
    const f = (window as unknown as { __probe: { frames: { t: number; d: number }[] } }).__probe.frames;
    return f.length ? f[f.length - 1].t : -1;
  });
  const now = await page.evaluate(() => performance.now());
  expect(now - lastFrame, 'RAF must still be ticking (no frozen renderer)').toBeLessThan(2000);
  return Math.round((now - lastFrame) * 100) / 100;
}

interface ProbeBag {
  frames: { t: number; d: number }[];
  longtasks: { start: number; duration: number }[];
  drawCalls: number;
  drawVerts: number;
  sigmaErrors: string[];
}

/**
 * Install the RAF/longtask/WebGL probes (same init script as graph-scale)
 * PLUS a window.Error proxy that captures every `new Error('Sigma: …')`
 * — including the ones the canvas swallows inside ngAfterViewInit's
 * try/catch, which never reach pageerror or console.
 */
async function installProbes(page: Page): Promise<void> {
  await page.addInitScript(() => {
    (window as unknown as { __probe: ProbeBag }).__probe = {
      frames: [] as { t: number; d: number }[],
      longtasks: [] as { start: number; duration: number }[],
      drawCalls: 0,
      drawVerts: 0,
      sigmaErrors: [] as string[],
    };
    let last = performance.now();
    const loop = (t: number) => {
      const p = (window as unknown as { __probe: ProbeBag }).__probe;
      p.frames.push({ t, d: t - last });
      last = t;
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
    try {
      new PerformanceObserver((l) => {
        const p = (window as unknown as { __probe: ProbeBag }).__probe;
        for (const e of l.getEntries()) p.longtasks.push({ start: e.startTime, duration: e.duration });
      }).observe({ entryTypes: ['longtask'] });
    } catch {
      /* longtask unsupported — recorded as empty */
    }
    /* The renderer's `Sigma: edge "…" can't be repaint` is thrown inside a
     * try/catch and never reaches the console — capture it at construction. */
    const origError = window.Error;
    const record = (msg: unknown) => {
      const s = String(msg ?? '');
      if (s.includes('Sigma:')) {
        (window as unknown as { __probe: ProbeBag }).__probe.sigmaErrors.push(s);
      }
    };
    const ProxyError = new Proxy(origError, {
      construct(target, args) {
        record(args[0]);
        return new (target as new (...a: unknown[]) => Error)(...args);
      },
      apply(target, thisArg, args) {
        record(args[0]);
        return Reflect.apply(target, thisArg, args);
      },
    });
    (window as unknown as { Error: unknown }).Error = ProxyError;

    const proto = (window as unknown as { __probe: ProbeBag }).__probe;
    for (const glProto of [WebGL2RenderingContext.prototype, WebGLRenderingContext.prototype]) {
      const gl = glProto as unknown as Record<string, ((...a: number[]) => void) | undefined>;
      for (const fn of ['drawElements', 'drawArrays', 'drawElementsInstanced', 'drawArraysInstanced'] as const) {
        const orig = gl[fn];
        if (!orig) continue;
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
}

async function probeSnapshot(page: Page): Promise<{
  sigmaErrors: string[];
  longtasks: number[];
  maxRafGap: number;
}> {
  return page.evaluate(() => {
    const p = (window as unknown as { __probe: ProbeBag }).__probe;
    const gaps = p.frames.map((f) => f.d);
    return {
      sigmaErrors: [...p.sigmaErrors],
      longtasks: p.longtasks.map((l) => Math.round(l.duration * 100) / 100),
      maxRafGap: Math.round((gaps.length ? Math.max(...gaps) : 0) * 100) / 100,
    };
  });
}

interface ExpansionRecord {
  index: number;
  target: string;
  wall_ms: number;
  nodes_after: number;
  edges_after: number;
}

interface DatasetResult {
  dataset: DenseDataset & { hubs: number; hub_fanout: number };
  fixture: { generated: boolean; indexed: boolean; indexMs: number | null };
  db: DbStats;
  api: Record<string, unknown>;
  measured: Record<string, unknown>;
}

const results: DatasetResult[] = [];

/* Traces/screenshots off — same rationale as graph-scale (teardown race,
 * numbers-only benchmark). */
test.use({ trace: 'off', video: 'off', screenshot: 'off' });

test.describe('graph density — real browser benchmarks', () => {
  test.describe.configure({ timeout: 300_000 });

  test.beforeAll(() => {
    /* Generating + indexing three repos (up to 2000 files) exceeds the
     * default hook timeout — widen it for this hook. */
    test.setTimeout(240_000);
    mkdirSync(WORK, { recursive: true });
    mkdirSync(dirname(RESULTS_JSON), { recursive: true });
    for (const d of DATASETS) {
      const stale = join(dirname(RESULTS_JSON), `graph-dense-${d.label}.json`);
      if (existsSync(stale)) rmSync(stale, { force: true });
      const r = ensureFixture(d);
      const db = dbStats(d);
      results.push({
        dataset: { ...d, hubs: HUBS, hub_fanout: HUB_FANOUT },
        fixture: r,
        db,
        api: {},
        measured: {},
      });
    }
  });

  for (const d of DATASETS) {
    test(`dense ego view @ ${d.files} files × ${d.calls} calls`, async ({ browser }) => {
      const rec = results.find((r) => r.dataset.label === d.label)!;
      const hubQName = `mod_0000.hub_0000`;
      const hubOcc = rec.db.hubs[hubQName];
      expect(hubOcc, `hub occurrence ${hubQName} must exist in the index`).toBeGreaterThan(0);
      const { proc, base } = await startExplorer(d);
      const pageErrors: string[] = [];
      const page = await browser.newPage();
      page.on('pageerror', (e) => pageErrors.push(`pageerror: ${String(e)}`));
      page.on('console', (m) => {
        if (m.type() === 'error') pageErrors.push(`console.error: ${m.text()}`);
      });
      const measured: Record<string, unknown> = {};
      /* Assign early: the same object is mutated through both phases, so a
       * mid-test failure still leaves partial measurements in `results`
       * (and afterAll merges whatever exists). */
      rec.measured = measured;
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

        /* ---- API latency on the hub (curl, outside the browser) --------- */
        const apiClient = curlNeighbors(base, hubOcc, CLIENT_NEIGHBOR_LIMIT);
        const apiCap = curlNeighbors(base, hubOcc, SERVER_NEIGHBOR_CAP);
        rec.api = {
          hub_occurrence_id: hubOcc,
          client_limit_100: apiClient,
          server_cap_200: apiCap,
        };
        measured['env'] = env;

        /* ================================================================
         * PHASE A — deep link /graph?focus={hub}
         * The canvas mounts only at phase=='ready' — i.e. AFTER the focus
         * data is already in the store. With edges present, the first
         * paintAll() throws inside ngAfterViewInit → markRendererFailed →
         * accessible-table fallback. Whatever surface appears is measured
         * as-is; sigma error text is captured verbatim.
         * ============================================================== */
        await installProbes(page);
        const heapBefore = await heapMB(page);
        const t0 = Date.now();
        await page.goto(`${base}/graph?focus=${hubOcc}`, { waitUntil: 'domcontentloaded' });

        const surface0 = await waitForSurface(page, 60_000);
        const wallNavToReady = Date.now() - t0;
        expect(
          surface0,
          'focus load must expose a graph surface (canvas or table)',
        ).not.toBe('none');

        const resource = await page.evaluate(() => {
          const nav = performance.getEntriesByType('navigation')[0] as PerformanceNavigationTiming | undefined;
          const nb = performance
            .getEntriesByType('resource')
            .find((r) => r.name.includes('/api/v1/graph/neighbors')) as PerformanceResourceTiming | undefined;
          const sym = performance
            .getEntriesByType('resource')
            .find((r) => r.name.includes('/api/v1/symbols/')) as PerformanceResourceTiming | undefined;
          return {
            navDuration: nav ? Math.round(nav.duration * 100) / 100 : null,
            neighborsApi: nb
              ? {
                  start: Math.round(nb.startTime * 100) / 100,
                  duration: Math.round(nb.duration * 100) / 100,
                  responseEnd: Math.round(nb.responseEnd * 100) / 100,
                  transferSize: nb.transferSize,
                  encodedBodySize: nb.encodedBodySize,
                }
              : null,
            symbolApi: sym ? Math.round(sym.duration * 100) / 100 : null,
          };
        });

        const counts0 = await readCounts(page);
        const probe0 = await probeSnapshot(page);
        expect(counts0.edges ?? 0, 'ego view must expose edges on some surface').toBeGreaterThan(0);
        /* The hub's degree (fanout CALLS + 1 DEFINES) exceeds the client
         * fetch limit — the truncation warning MUST surface honestly. */
        expect(counts0.noticeText ?? '').toContain('truncou');
        expect(probe0.maxRafGap, 'initial load must not freeze >5s').toBeLessThan(FREEZE_MS);

        /* ---- bulk expansions via the accessible table -------------------
         * Same expandNode() path, DOM-addressable, and the only surface
         * guaranteed to exist (it IS the renderer-failure fallback).
         * FIRST the hub row: re-expanding an already-truncated node
         * fetches the same first page of neighbours — the no-pagination
         * no-op, recorded honestly. Then R ordinary nodes. */
        if ((await page.locator('atlas-graph-table tbody tr').count()) === 0) {
          await page.getByRole('button', { name: 'Lista', exact: true }).click();
        }
        await page.locator('atlas-graph-table tbody tr').first().waitFor({ timeout: 15_000 });
        const tableMark = await page.evaluate(() => performance.now());
        const countsTable0 = await tableCounts(page);

        const nodesTable = page.locator('atlas-graph-table section', {
          has: page.locator('#nodes-caption'),
        });
        const hubRow = nodesTable.locator('tr', {
          has: page.locator('button.linklike', { hasText: /^\s*hub_0000\s*$/ }),
        });
        const hubBtn = hubRow.locator('button', { hasText: 'Expandir' });
        let hubSelfExpand: Record<string, unknown> | null = null;
        if ((await hubBtn.count()) > 0) {
          const tHub = Date.now();
          await hubBtn.click();
          await hubRow
            .locator('button', { hasText: /^\s*ok\s*$|^Expandindo/ })
            .waitFor({ timeout: 30_000 });
          hubSelfExpand = {
            wall_ms: Date.now() - tHub,
            counts_after: await tableCounts(page),
            note: 're-expands the truncated hub — same first page, no pagination (no-op by design)',
          };
        }

        const expansions: ExpansionRecord[] = [];
        const table0 = await tableCounts(page);
        let edgesPrev = table0.edges;
        for (let i = 0; i < EXPANSIONS; i++) {
          const btn = page
            .locator('atlas-graph-table section', {
              has: page.locator('#nodes-caption'),
            })
            .locator('button:enabled', { hasText: 'Expandir' })
            .first();
          if ((await btn.count()) === 0) break;
          const t = Date.now();
          await btn.click();
          await page.waitForFunction(
            (before) => {
              const text = document.querySelector('atlas-graph-table')?.textContent ?? '';
              const m = /Relações \((\d+)\)/.exec(text);
              return m && Number(m[1]) > before;
            },
            edgesPrev,
            { timeout: 30_000 },
          );
          const after = await tableCounts(page);
          expansions.push({
            index: i,
            target: `first-enabled-${i}`,
            wall_ms: Date.now() - t,
            nodes_after: after.nodes,
            edges_after: after.edges,
          });
          edgesPrev = after.edges;
        }
        const denseTableCounts = await tableCounts(page);
        const expandBlock = await page.evaluate((mark) => {
          const f = (window as unknown as { __probe: ProbeBag }).__probe.frames;
          let max = 0;
          for (const fr of f) if (fr.t >= mark && fr.d > max) max = fr.d;
          return Math.round(max * 100) / 100;
        }, tableMark);

        /* ---- collapse via the node cap (works on either surface) --------
         * 500 → 100 drops nodes + their edges; restore returns them. */
        const capInput = page.locator('input.cap__input');
        const collapseTo = Math.min(100, Math.max(10, denseTableCounts.nodes - 1));
        const collapse = await measureActionBlock(page, async () => {
          await capInput.fill(String(collapseTo));
          await capInput.dispatchEvent('change');
          await waitSurfaceCounts(page, Math.min(denseTableCounts.nodes, collapseTo), null);
        });
        const countsCollapsed = await readCounts(page);
        const restore = await measureActionBlock(page, async () => {
          await capInput.fill('500');
          await capInput.dispatchEvent('change');
          await waitSurfaceCounts(page, Math.min(denseTableCounts.nodes, 500), null);
        });
        const countsRestored = await readCounts(page);
        expect(countsRestored.rendered).toBe(Math.min(denseTableCounts.nodes, 500));

        const rafAliveA = await assertRafAlive(page);
        const probeA = await probeSnapshot(page);
        const heapA = await heapMB(page);

        measured['focus_load'] = {
          url: `/graph?focus=${hubOcc}`,
          surface: surface0,
          renderer_failed_banner: counts0.rendererFailed,
          wall_nav_to_ready_ms: wallNavToReady,
          resource,
          counts_initial: counts0,
          max_raf_gap_ms: probe0.maxRafGap,
          longtasks: probe0.longtasks,
          sigma_errors: probe0.sigmaErrors,
          page_errors: [...pageErrors],
          heap_mb: { before_load: heapBefore, end: heapA },
          raf_alive_ms: rafAliveA,
        };
        measured['table_expansion_phase'] = {
          view: 'accessible table (renderer fallback keeps it usable)',
          hub_self_expand: hubSelfExpand,
          expansions,
          counts_before: countsTable0,
          counts_after: denseTableCounts,
          max_raf_gap_during_phase_ms: expandBlock,
          cap_collapse: {
            collapsed_to: collapseTo,
            apply: { max_gap_ms: collapse.max_gap_ms, settle_ms: collapse.settle_ms },
            counts_collapsed: countsCollapsed,
            restore: { max_gap_ms: restore.max_gap_ms, settle_ms: restore.settle_ms },
            counts_restored: countsRestored,
          },
          sigma_errors_after: (await probeSnapshot(page)).sigmaErrors,
        };
      } finally {
        await page.close().catch(() => {});
      }

      /* ==================================================================
       * PHASE B — arch-view canvas + in-session drill attempt
       * The overview carries file nodes + ZERO edges → the canvas mounts
       * cleanly and IS measurable (pan FPS at dense file counts). The
       * ONLY in-session edge source is drillFile() — ']' + Enter + 'e'
       * on a file node. If it lands edges on the live canvas the whole
       * canvas-metric chain below runs; whatever it actually does is
       * recorded verbatim (outcome + page state + sigma errors).
       * ================================================================ */
      const page2Errors: string[] = [];
      const page2 = await browser.newPage();
      page2.on('pageerror', (e) => page2Errors.push(`pageerror: ${String(e)}`));
      page2.on('console', (m) => {
        if (m.type() === 'error') page2Errors.push(`console.error: ${m.text()}`);
      });
      try {
        await installProbes(page2);
        const heapB0 = await heapMB(page2);
        const tB = Date.now();
        await page2.goto(`${base}/graph`, { waitUntil: 'domcontentloaded' });
        const surfaceB0 = await waitForSurface(page2, 60_000);
        const archCounts = await readCounts(page2);
        const archReady = Date.now() - tB;

        let panArch: PanStats | null = null;
        let drillOutcome: Record<string, unknown> = { attempted: false };
        let canvasAfterDrill: SurfaceCounts | null = null;
        let drillSigmaErrors: string[] = [];
        let panEgo: PanStats | null = null;
        let canvasExpand: Record<string, unknown> | null = null;
        let capCollapse: Record<string, unknown> | null = null;
        let panDense: PanStats | null = null;
        let remount: Record<string, unknown> | null = null;
        let recovery: Record<string, unknown> = { attempted: false };

        if (surfaceB0 === 'canvas') {
          /* ---- pan on the dense file-node overview (canvas, 0 edges) --
           * a REAL canvas measurement at this dataset's scale — the one
           * sigma surface that works today. */
          panArch = await measurePan(page2, PAN_MS);

          /* Target mod_0000.py precisely: ']' cycles kbdFocus through
           * nodes() in insertion order, Enter selects it into the
           * inspector (title = node label). Bound the hunt; drill whatever
           * is focused as an honest fallback. */
          const stage2 = page2.locator('.graph-stage');
          await stage2.focus();
          let drilledFile: string | null = null;
          let focusedLabel = '';
          for (let k = 0; k < Math.min(DRILL_TARGET_TRIES, archCounts.rendered ?? 10); k++) {
            await page2.keyboard.press(']');
            await page2.keyboard.press('Enter');
            focusedLabel =
              (await page2.locator('.inspector__title').first().textContent().catch(() => '')) ?? '';
            if (focusedLabel.includes('mod_0000.py')) {
              drilledFile = 'mod_0000.py';
              break;
            }
          }
          /* 'e' on a selected file node → drillFile(): file-mode symbol
           * view + DRILL_NEIGHBOR_BUDGET(12) seeds × /graph/neighbors.
           * Wait for ANY terminal outcome — edges on a live canvas, an
           * error/empty state block, or table edges — never blind-time. */
          const tDrill = Date.now();
          await page2.keyboard.press('e');
          const outcome = await page2
            .waitForFunction(
              () => {
                const el = document.querySelector('.graph-stage');
                if (el) {
                  const m = /e (\d+) relações/.exec(el.getAttribute('aria-label') ?? '');
                  if (m && Number(m[1]) > 0) return 'canvas-edges';
                }
                const tableText = document.querySelector('atlas-graph-table')?.textContent ?? '';
                const em = /Relações \((\d+)\)/.exec(tableText);
                if (em && Number(em[1]) > 0) return 'table-edges';
                const block = document.querySelector('atlas-state-block');
                const st = block?.getAttribute('state') ?? '';
                if (block && st !== 'loading') return `state:${st || 'unknown'}`;
                return false;
              },
              undefined,
              { polling: 'raf', timeout: 60_000 },
            )
            .then((h) => h.jsonValue() as Promise<string>)
            .catch(() => 'timeout');
          const drillState = await page2.evaluate(() => ({
            state: document.querySelector('atlas-state-block')?.getAttribute('state') ?? null,
            stateText: (document.querySelector('atlas-state-block')?.textContent ?? '')
              .replace(/\s+/g, ' ')
              .trim()
              .slice(0, 300),
            breadcrumb: (document.querySelector('.breadcrumb')?.textContent ?? '')
              .replace(/\s+/g, ' ')
              .trim()
              .slice(0, 200),
          }));
          await waitFrames(page2, 4); // post-commit process() heal window
          canvasAfterDrill = await readCounts(page2);
          drillSigmaErrors = (await probeSnapshot(page2)).sigmaErrors;
          drillOutcome = {
            attempted: true,
            target: drilledFile ?? `unresolved (drilled ${focusedLabel.trim() || 'whatever kbd-focus reached'})`,
            action: "']' ×k + Enter + e — drillFile() in-session",
            wall_ms: Date.now() - tDrill,
            outcome,
            page_state: drillState,
            counts_after: canvasAfterDrill,
            sigma_errors_during_drill: drillSigmaErrors,
            canvas_alive_with_edges:
              canvasAfterDrill.mode === 'canvas' &&
              !canvasAfterDrill.rendererFailed &&
              (canvasAfterDrill.edges ?? 0) > 0,
          };

          if (drillOutcome['canvas_alive_with_edges'] === true) {
            /* ---- pan on the drilled ego view ------------------------ */
            panEgo = await measurePan(page2, PAN_MS);

            /* ---- one expansion ON THE CANVAS (keyboard path) -------- */
            await page2.locator('.graph-stage').focus();
            const edgesB4 = canvasAfterDrill.edges ?? 0;
            const tries: Record<string, unknown>[] = [];
            let added = 0;
            for (let i = 0; i < CANVAS_EXPAND_TRIES && added === 0; i++) {
              const mark = await page2.evaluate(() => performance.now());
              await page2.keyboard.press(']');
              await page2.keyboard.press('Enter');
              await page2.keyboard.press('e');
              const ok = await page2
                .waitForFunction(
                  (before) => {
                    const el = document.querySelector('.graph-stage');
                    const m = /e (\d+) relações/.exec(el?.getAttribute('aria-label') ?? '');
                    return m && Number(m[1]) > before;
                  },
                  edgesB4 + added,
                  { polling: 'raf', timeout: 8_000 },
                )
                .then(() => true)
                .catch(() => false);
              const label =
                (await page2.locator('.inspector__title').first().textContent().catch(() => '')) ?? '';
              tries.push({ node_label: label.trim(), grew: ok });
              if (ok) {
                added = 1;
                canvasExpand = {
                  key_sequence: '] Enter e (focus next symbol → select → expand)',
                  wall_ms_estimate: Math.round((await page2.evaluate((m) => performance.now() - m, mark)) * 100) / 100,
                  edges_before: edgesB4,
                  attempts: i + 1,
                };
              }
            }
            canvasExpand = canvasExpand ?? {
              attempts: CANVAS_EXPAND_TRIES,
              grew: false,
              note: 'no focused symbol added edges — expansion targets may already be expanded',
            };
            (canvasExpand as Record<string, unknown>)['tries'] = tries;
            await waitFrames(page2, 4);

            /* ---- cap collapse + restore on the canvas --------------- */
            const capInput2 = page2.locator('input.cap__input');
            const countsNow = await readCounts(page2);
            const collapseTo2 = Math.min(100, Math.max(10, (countsNow.rendered ?? 200) - 1));
            const col = await measureActionBlock(page2, async () => {
              await capInput2.fill(String(collapseTo2));
              await capInput2.dispatchEvent('change');
              await waitSurfaceCounts(page2, Math.min(countsNow.rendered ?? 0, collapseTo2), null);
            });
            const countsCol = await readCounts(page2);
            const res = await measureActionBlock(page2, async () => {
              await capInput2.fill('500');
              await capInput2.dispatchEvent('change');
              await waitSurfaceCounts(page2, Math.min(countsNow.rendered ?? 0, 500), null);
            });
            const countsRes = await readCounts(page2);
            capCollapse = {
              collapsed_to: collapseTo2,
              apply: { max_gap_ms: col.max_gap_ms, settle_ms: col.settle_ms },
              counts_collapsed: countsCol,
              restore: { max_gap_ms: res.max_gap_ms, settle_ms: res.settle_ms },
              counts_restored: countsRes,
            };

            panDense = await measurePan(page2, PAN_MS);

            /* ---- remount: table→graph rebuilds sigma over edges — the
             * SAME fatal mount path as ?focus=. Recorded, not asserted. */
            const tRem = Date.now();
            await page2.getByRole('button', { name: 'Lista', exact: true }).click();
            await page2.locator('atlas-graph-table tbody tr').first().waitFor({ timeout: 15_000 });
            await page2.getByRole('button', { name: 'Grafo', exact: true }).click();
            await page2.waitForTimeout(1200); // let initRenderer run + throw
            const remountCounts = await readCounts(page2);
            remount = {
              wall_ms: Date.now() - tRem,
              surface_after: remountCounts.mode,
              renderer_failed: remountCounts.rendererFailed,
              sigma_errors_so_far: (await probeSnapshot(page2)).sigmaErrors.length,
            };
          } else {
            /* ---- recovery from a failed drill: the breadcrumb's
             * "Voltar" restores the frozen architecture view — check the
             * canvas comes back (arch snapshot has 0 edges → safe mount). */
            const back = page2.locator('.breadcrumb__back');
            if ((await back.count()) > 0) {
              const tRec = Date.now();
              await back.click();
              /* The stale error block is still in DOM right after the
               * click — wait for it to detach before judging the surface
               * (else 'none' wins the race in ~50 ms). */
              await page2
                .waitForSelector('atlas-state-block', { state: 'detached', timeout: 15_000 })
                .catch(() => {});
              const backTo = await waitForSurface(page2, 30_000).catch(() => 'none' as Surface);
              recovery = {
                attempted: true,
                via: 'breadcrumb "← Voltar sem perder o contexto" (exitDrill → arch snapshot)',
                wall_ms: Date.now() - tRec,
                surface_after: backTo,
                counts_after: await readCounts(page2),
              };
            }
          }
        }

        const rafAliveB = await assertRafAlive(page2);
        const probeB = await probeSnapshot(page2);
        const heapB1 = await heapMB(page2);

        measured['canvas_session'] = {
          arch_load: {
            surface: surfaceB0,
            wall_nav_to_ready_ms: archReady,
            counts: archCounts,
            note: 'overview carries 0 edges — canvas mounts cleanly',
          },
          pan_arch: panArch,
          drill_attempt: drillOutcome,
          recovery_after_drill_error: recovery,
          edge_canvas_metrics: {
            pan_ego: panEgo,
            canvas_expand: canvasExpand,
            cap_collapse: capCollapse,
            pan_dense: panDense,
            remount_table_to_graph: remount,
            ...(panEgo === null
              ? {
                  status: 'unreachable-in-this-build',
                  reason:
                    'canvas mounts only at phase=ready (data already in store) → ' +
                    'paintAll→updateEachEdgeAttributes throws Sigma "edge … can\'t be repaint" ' +
                    'for any edge-bearing mount (fatal); the only in-session edge ' +
                    'source (drillFile) never merges edges — see drill_attempt.outcome.',
                }
              : {}),
          },
          sigma_errors: probeB.sigmaErrors,
          page_errors: [...page2Errors],
          longtasks: probeB.longtasks,
          heap_mb: { before: heapB0, end: heapB1 },
          raf_alive_ms: rafAliveB,
        };
      } finally {
        await page2.close().catch(() => {});
        proc.kill('SIGTERM');
      }

      writeFileSync(
        join(dirname(RESULTS_JSON), `graph-dense-${d.label}.json`),
        JSON.stringify(rec, null, 2) + '\n',
      );
    });
  }

  test.afterAll(async () => {
    const dir = dirname(RESULTS_JSON);
    const merged = DATASETS.map((d) => {
      const file = join(dir, `graph-dense-${d.label}.json`);
      if (existsSync(file)) return JSON.parse(readFileSync(file, 'utf-8')) as DatasetResult;
      return results.find((r) => r.dataset.label === d.label) ?? null;
    }).filter(Boolean);
    const summary = {
      tool: 'code-atlas graph-dense e2e (Cycle 7 Phase 0.3)',
      generated_at: new Date().toISOString(),
      work_dir: WORK,
      datasets: DATASETS,
      params: {
        hubs: HUBS,
        hub_fanout: HUB_FANOUT,
        expansions: EXPANSIONS,
        client_neighbor_limit: CLIENT_NEIGHBOR_LIMIT,
        server_neighbor_cap: SERVER_NEIGHBOR_CAP,
      },
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
    console.log(`\ngraph-dense results → ${RESULTS_JSON}`);
  });
});
