import { test, expect, Page } from '@playwright/test';
import { spawn, execFileSync, ChildProcess } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync, readFileSync, rmSync, readdirSync } from 'node:fs';
import { tmpdir, cpus, totalmem, platform, release } from 'node:os';
import { dirname, join } from 'node:path';
import { createServer } from 'node:net';

/**
 * Graph CEILING suite — Cycle 8.1 Phase 9 (lf-0152). Runs the surfaces
 * every earlier cycle declared NOT RUN, on the Angular 22 build:
 *
 *   1. dense ceiling — grow a live sigma canvas to ≥5,000 rendered edges
 *      (the "5k-edge ceiling" left NOT RUN by cycle-8 graph hardening,
 *      which peaked at 259 edges via bounded expansion). Measured: edges
 *      per real keyboard expansion, pan FPS, a real worker FA2 relayout
 *      (wall + main-thread gap + worker-reported ms), heap, draw calls.
 *
 *   2. mount/unmount heap attribution — the cycle-8 suite recorded
 *      ~400 KB/cycle of retained heap and declared attribution NOT RUN
 *      ("needs heap snapshots"). This test takes real V8 heap snapshots
 *      via CDP HeapProfiler around N settled mount cycles and diffs
 *      self_size by constructor — a real attribution table, plus DOM
 *      counters per cycle.
 *
 *   3. long-session memory trend — sustained mixed interaction
 *      (pan drag, wheel zoom, kbd node cycling, and a real /search
 *      round-trip every 8th round) with post-GC heap sampled per
 *      window: slope + interpretation, not a single before/after.
 *
 * Fixture: gen_large_repo.py dense family, shared ATLAS_DENSE_DIR with
 * graph-dense/graph-hardiness. The ceiling dataset is `500-c100-h1x400`
 * (500 modules, each func_i calls the next 100 ring modules + imports —
 * every symbol occurrence has degree ≥ ~200, so each real expansion
 * merges up to the client's NEIGHBOR_LIMIT=100 edges; one hub×400 gives
 * the `?focus=` deep link a degree > the fetch limit).
 *
 * Assertions pin invariants only (canvas alive, edges ≥ target, RAF
 * ticking, counters consistent, no page errors). Every number is
 * recorded verbatim to benchmarks/results/graph-ceiling-latest.json.
 */

const REPO_ROOT = join(__dirname, '..', '..');
const FIXTURE_GEN = join(REPO_ROOT, 'tests', 'fixtures', 'gen_large_repo.py');
const WORK = process.env['ATLAS_DENSE_DIR'] ?? join(tmpdir(), 'atlas-graph-dense');
const RESULTS_JSON = join(REPO_ROOT, 'benchmarks', 'results', 'graph-ceiling-latest.json');

/* The ceiling dataset: ring degree ≥ the 100-edge fetch limit ⇒ every
 * productive expansion lands ~100 fresh edges (measured, not assumed —
 * `expansions[]` records the real per-try delta). */
const CEILING = { label: '500-c100-h1x400', files: 500, calls: 100, hubs: 1, fanout: 400 };
/* The lifecycle dataset: identical to graph-hardiness (shared index). */
const CYCLES = { label: '500-c2-h3x150', files: 500, calls: 2, hubs: 3, fanout: 150 };

const EDGE_TARGET = 5_000;
const EXPAND_MAX_TRIES = Number(process.env['ATLAS_CEILING_TRIES'] ?? 160);
const MOUNT_CYCLES = 10;
const SESSION_ROUNDS = Number(process.env['ATLAS_SESSION_ROUNDS'] ?? 40);
const SEARCH_EVERY = 8;
const PAN_MS = 3_000;
const FREEZE_MS = 5_000;

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

function repoDir(d: typeof CEILING): string {
  return join(WORK, `repo-dense-${d.files}-c${d.calls}-h${d.hubs}x${d.fanout}`);
}
function dataDir(d: typeof CEILING): string {
  return join(WORK, `data-dense-${d.files}-c${d.calls}-h${d.hubs}x${d.fanout}`);
}

function countModules(dir: string): number {
  if (!existsSync(dir)) return 0;
  return readdirSync(dir).filter((f) => /^mod_\d+\.py$/.test(f)).length;
}

function ensureFixture(d: typeof CEILING): { generated: boolean; indexed: boolean; indexMs: number | null } {
  const out = { generated: false, indexed: false, indexMs: null as number | null };
  if (countModules(repoDir(d)) !== d.files) {
    execFileSync(
      pythonBin(),
      [
        FIXTURE_GEN,
        '--files', String(d.files),
        '--calls', String(d.calls),
        '--hubs', String(d.hubs),
        '--hub-fanout', String(d.fanout),
        '--out', repoDir(d),
      ],
      { cwd: REPO_ROOT, stdio: 'inherit' },
    );
    out.generated = true;
  }
  const db = join(dataDir(d), 'atlas.db');
  if (!existsSync(db)) {
    mkdirSync(dataDir(d), { recursive: true });
    const t0 = Date.now();
    execFileSync(pythonBin(), ['-m', 'code_atlas', 'index', repoDir(d)], {
      cwd: REPO_ROOT,
      env: { ...process.env, CODE_ATLAS_DATA_DIR: dataDir(d) },
      stdio: 'inherit',
    });
    out.indexed = true;
    out.indexMs = Date.now() - t0;
  }
  return out;
}

/** Real index contents — hub occurrence + relation totals. */
function dbStats(d: typeof CEILING): { hub: number; relations: number; occurrences: number } {
  const script = `
import sqlite3, json, sys
conn = sqlite3.connect(sys.argv[1])
sid = conn.execute("SELECT MAX(id) FROM snapshots WHERE status='READY'").fetchone()[0]
row = conn.execute(
  "SELECT o.id FROM symbol_occurrences o JOIN symbols s ON s.id=o.logical_symbol_id"
  " WHERE o.snapshot_id=? AND s.qualified_name='mod_0000.hub_0000'", (sid,)).fetchone()
out = {
 "hub": row[0] if row else 0,
 "relations": conn.execute("SELECT COUNT(*) FROM relations WHERE snapshot_id=?", (sid,)).fetchone()[0],
 "occurrences": conn.execute("SELECT COUNT(*) FROM symbol_occurrences WHERE snapshot_id=?", (sid,)).fetchone()[0],
}
print(json.dumps(out))
`;
  const out = execFileSync(pythonBin(), ['-c', script, join(dataDir(d), 'atlas.db')], {
    encoding: 'utf-8',
  });
  return JSON.parse(out) as { hub: number; relations: number; occurrences: number };
}

async function startExplorer(d: typeof CEILING): Promise<{ proc: ChildProcess; base: string }> {
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
      if (res.status) return { proc, base };
    } catch (err) {
      lastErr = err;
    }
    await new Promise((r) => setTimeout(r, 200));
  }
  proc.kill('SIGTERM');
  throw new Error(`ceiling explorer(${d.label}) did not come up: ${String(lastErr)}`);
}

/* ------------------------------- probes ------------------------------ */

interface ProbeBag {
  frames: { t: number; d: number }[];
  longtasks: { start: number; duration: number }[];
  drawCalls: number;
  drawVerts: number;
  sigmaErrors: string[];
  listenerAdds: number;
  listenerRemoves: number;
  globalNet: number;
}

/** Same probe family as graph-dense + graph-hardiness, merged: RAF
 * sampler, longtask observer, WebGL draw counters, window.Error proxy
 * for caught Sigma throws, and net global-listener accounting. */
async function installProbes(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const w = window as unknown as { __probe: ProbeBag };
    w.__probe = {
      frames: [],
      longtasks: [],
      drawCalls: 0,
      drawVerts: 0,
      sigmaErrors: [],
      listenerAdds: 0,
      listenerRemoves: 0,
      globalNet: 0,
    };
    let last = performance.now();
    const loop = (t: number) => {
      w.__probe.frames.push({ t, d: t - last });
      last = t;
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
    try {
      new PerformanceObserver((l) => {
        for (const e of l.getEntries()) {
          w.__probe.longtasks.push({ start: e.startTime, duration: e.duration });
        }
      }).observe({ entryTypes: ['longtask'] });
    } catch {
      /* longtask unsupported — recorded as empty */
    }
    const origError = window.Error;
    const record = (msg: unknown) => {
      const s = String(msg ?? '');
      if (s.includes('Sigma:')) w.__probe.sigmaErrors.push(s);
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

    const proto = w.__probe;
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

/** JSHeapUsedSize (MB) after an explicit GC — honest before/after. */
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

/** CDP Memory.getDOMCounters — DOM nodes, documents, live JS listeners. */
async function domCounters(page: Page): Promise<{ nodes: number; documents: number; jsEventListeners: number } | null> {
  try {
    const cdp = await page.context().newCDPSession(page);
    const c = (await cdp.send('Memory.getDOMCounters')) as {
      nodes: number;
      documents: number;
      jsEventListeners: number;
    };
    await cdp.detach();
    return c;
  } catch {
    return null;
  }
}

/** Raw V8 heap snapshot collected through CDP chunk events. */
async function heapSnapshot(page: Page): Promise<string> {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('HeapProfiler.enable');
  await cdp.send('HeapProfiler.collectGarbage');
  let buf = '';
  const onChunk = (e: { chunk: string }) => {
    buf += e.chunk;
  };
  cdp.on('HeapProfiler.addHeapSnapshotChunk', onChunk);
  try {
    await cdp.send('HeapProfiler.takeHeapSnapshot', { reportProgress: false });
  } finally {
    cdp.off('HeapProfiler.addHeapSnapshotChunk', onChunk);
    await cdp.detach();
  }
  return buf;
}

interface SnapshotAgg {
  total_bytes: number;
  total_nodes: number;
  detached_bytes: number;
  detached_nodes: number;
  by_key: Record<string, { count: number; bytes: number; detached: number }>;
}

/**
 * Parse a .heapsnapshot payload and aggregate self_size per
 * `${type}:${name}` constructor key. `snapshot.meta` is a nested JSON
 * string in the wire format; node_fields order varies across V8
 * versions, so every index is resolved from the meta, never hardcoded.
 */
function aggregateSnapshot(buf: string): SnapshotAgg {
  const snap = JSON.parse(buf) as {
    /* `snapshot` is an object whose `meta` is a JSON string on older V8;
     * Chromium 141+ emits plain objects at both levels — accept either. */
    snapshot: { meta: string | { node_fields: string[]; node_types: string[][] } } | string;
    nodes: number[];
    strings: string[];
  };
  interface SnapObj {
    meta: string | { node_fields: string[]; node_types: string[][] };
  }
  const snapObj: SnapObj = typeof snap.snapshot === 'string'
    ? (JSON.parse(snap.snapshot) as SnapObj)
    : (snap.snapshot as SnapObj);
  const meta = typeof snapObj.meta === 'string'
    ? (JSON.parse(snapObj.meta) as { node_fields: string[]; node_types: string[][] })
    : snapObj.meta;
  const fields = meta.node_fields;
  const stride = fields.length;
  const typeIdx = fields.indexOf('type');
  const nameIdx = fields.indexOf('name');
  const sizeIdx = fields.indexOf('self_size');
  const detIdx = fields.indexOf('detachedness');
  const typeNames = meta.node_types[0] ?? [];
  const byKey: SnapshotAgg['by_key'] = {};
  let totalBytes = 0;
  let detachedBytes = 0;
  let detachedNodes = 0;
  const nodes = snap.nodes;
  const strings = snap.strings;
  for (let i = 0; i + stride <= nodes.length; i += stride) {
    const typeName = typeNames[nodes[i + typeIdx]] ?? `type:${nodes[i + typeIdx]}`;
    const name = strings[nodes[i + nameIdx]] ?? '?';
    const size = nodes[i + sizeIdx] ?? 0;
    const detached = detIdx >= 0 ? nodes[i + detIdx] === 1 : false;
    totalBytes += size;
    const key = `${typeName}:${name}`;
    const rec = (byKey[key] ??= { count: 0, bytes: 0, detached: 0 });
    rec.count++;
    rec.bytes += size;
    if (detached) {
      rec.detached++;
      detachedBytes += size;
      detachedNodes++;
    }
  }
  return {
    total_bytes: totalBytes,
    total_nodes: Math.floor(nodes.length / stride),
    detached_bytes: detachedBytes,
    detached_nodes: detachedNodes,
    by_key: byKey,
  };
}

/** Top-N retained classes between two snapshots (by byte delta). */
function diffSnapshots(a: SnapshotAgg, b: SnapshotAgg, topN = 30): Record<string, unknown>[] {
  const keys = new Set([...Object.keys(a.by_key), ...Object.keys(b.by_key)]);
  const rows = [...keys].map((k) => {
    const ra = a.by_key[k] ?? { count: 0, bytes: 0, detached: 0 };
    const rb = b.by_key[k] ?? { count: 0, bytes: 0, detached: 0 };
    return {
      key: k,
      delta_bytes: rb.bytes - ra.bytes,
      delta_count: rb.count - ra.count,
      bytes_after: rb.bytes,
      count_after: rb.count,
      detached_after: rb.detached,
    };
  });
  rows.sort((x, y) => y.delta_bytes - x.delta_bytes);
  return rows.slice(0, topN);
}

async function drawStats(page: Page): Promise<{ drawCalls: number; drawVerts: number }> {
  return page.evaluate(() => {
    const p = (window as unknown as { __probe: ProbeBag }).__probe;
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

/** Canvas counts from the live aria-label (visible edges = rendered). */
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

async function waitLayoutIdle(page: Page, timeout = 60_000): Promise<void> {
  await page.waitForFunction(
    () => !document.querySelector('.graph-stage')?.hasAttribute('aria-busy'),
    undefined,
    { polling: 'raf', timeout },
  );
}

interface Telemetry {
  sigmaLive: number;
  sigmaTotal: number;
  workersLive: number;
  workersTotal: number;
  fallbacks: number;
  lastLayoutError: string | null;
  runs: { started: number; done: number; cancelled: number; stale: number; error: number };
}

async function telemetry(page: Page): Promise<Telemetry | null> {
  return page.evaluate(() => {
    const g = (window as unknown as {
      __atlasGraph?: Omit<Telemetry, 'workers'> & { workers: Set<Worker> };
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
    };
  });
}

async function probeSnapshot(page: Page): Promise<{ sigmaErrors: string[]; maxRafGap: number; longtasks: number[] }> {
  return page.evaluate(() => {
    const p = (window as unknown as { __probe: ProbeBag }).__probe;
    const gaps = p.frames.map((f) => f.d);
    return {
      sigmaErrors: [...p.sigmaErrors],
      maxRafGap: Math.round((gaps.length ? Math.max(...gaps) : 0) * 100) / 100,
      longtasks: p.longtasks.map((l) => Math.round(l.duration * 100) / 100),
    };
  });
}

/** Max RAF gap strictly after `mark` (page clock). */
async function maxRafGapSince(page: Page, mark: number): Promise<number> {
  return page.evaluate((m) => {
    const p = (window as unknown as { __probe: ProbeBag }).__probe;
    let max = 0;
    for (const fr of p.frames) if (fr.t >= m && fr.d > max) max = fr.d;
    return Math.round(max * 100) / 100;
  }, mark);
}

const RELAYOUT_RE = /Reorganizar/;
const CANCEL_RE = /Cancelar layout/;

/* Numbers-only suite — artifacts race teardown (same rule as the other
 * graph specs). */
test.use({ trace: 'off', video: 'off', screenshot: 'off' });

test.describe('graph ceiling — cycle-8.1 phase 9', () => {
  test.describe.configure({ timeout: 600_000 });

  let proc: ChildProcess;
  let base = '';
  const measured: Record<string, unknown> = {};

  test.beforeAll(async () => {
    test.setTimeout(300_000);
    mkdirSync(WORK, { recursive: true });
    mkdirSync(dirname(RESULTS_JSON), { recursive: true });
    /* Fresh run: drop stale per-test records — afterAll merges whatever
     * files exist and a leftover would poison the summary. */
    for (const name of ['dense_ceiling', 'heap_attribution', 'long_session']) {
      const stale = join(dirname(RESULTS_JSON), `graph-ceiling-${name}.json`);
      if (existsSync(stale)) rmSync(stale, { force: true });
    }
    const f1 = ensureFixture(CEILING);
    const f2 = ensureFixture(CYCLES);
    const db1 = dbStats(CEILING);
    const db2 = dbStats(CYCLES);
    const started = await startExplorer(CEILING);
    proc = started.proc;
    base = started.base;
    measured['fixtures'] = {
      ceiling: {
        dataset: `${CEILING.files} files × ${CEILING.calls} calls, ${CEILING.hubs} hub × ${CEILING.fanout}`,
        ...f1,
        db: db1,
      },
      lifecycle: {
        dataset: `${CYCLES.files} files × ${CYCLES.calls} calls, ${CYCLES.hubs} hubs × ${CYCLES.fanout}`,
        ...f2,
        db: db2,
      },
    };
  });

  test.afterAll(() => {
    proc?.kill('SIGTERM');
    /* Merge the per-test records — the authoritative data lives in
     * graph-ceiling-{name}.json (a failed test still leaves its partial
     * record behind), this file is the summary view. A worker restart
     * between tests cannot lose an already-persisted record. */
    const dir = dirname(RESULTS_JSON);
    for (const name of ['dense_ceiling', 'heap_attribution', 'long_session']) {
      const file = join(dir, `graph-ceiling-${name}.json`);
      if (existsSync(file)) {
        measured[name] = JSON.parse(readFileSync(file, 'utf-8')) as Record<string, unknown>;
      }
    }
    mkdirSync(dir, { recursive: true });
    writeFileSync(
      RESULTS_JSON,
      JSON.stringify(
        {
          tool: 'code-atlas graph-ceiling e2e (Cycle 8.1 Phase 9, lf-0152)',
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
    console.log(`\ngraph-ceiling results → ${RESULTS_JSON}`);
  });

  /* ================================================================== *
   * 1. THE 5k-EDGE CEILING — real keyboard expansions on a live canvas
   *    until `visibleEdges ≥ 5000` (the number the aria-label reports —
   *    both endpoints rendered). Then pan FPS + a real worker relayout
   *    on the dense set.
   * ================================================================== */
  test(`dense ceiling — ≥${EDGE_TARGET} rendered edges on the live canvas`, async ({
    page,
  }) => {
    const pageErrors: string[] = [];
    page.on('pageerror', (e) => pageErrors.push(`pageerror: ${String(e)}`));
    page.on('console', (m) => {
      if (m.type() === 'error') pageErrors.push(`console.error: ${m.text()}`);
    });
    await installProbes(page);

    /* Filled progressively + persisted in `finally` — a mid-test failure
     * still leaves the partial measurement on disk. */
    const rec: Record<string, unknown> = { dataset: CEILING.label };
    try {
      const db = (measured['fixtures'] as { ceiling: { db: { hub: number } } }).ceiling.db;
      expect(db.hub, 'hub occurrence must exist in the ceiling fixture').toBeGreaterThan(0);

      const heap0 = await heapMB(page);
      const t0 = Date.now();
      await page.goto(`${base}/graph?focus=${db.hub}`, { waitUntil: 'domcontentloaded' });
      await waitForCanvas(page);
      await waitLayoutIdle(page);
      const navMs = Date.now() - t0;

      /* Surface gate: on this build the `?focus=` deep link mounts the
       * sigma canvas with edges (cycle-7 mount-throw is fixed). A table
       * surface here is a real regression — asserted, not just recorded. */
      const counts0 = await renderedCounts(page);
      expect(await page.locator('.graph-stage').count(), 'deep link must mount the canvas').toBe(1);
      expect(counts0.edges ?? 0, 'focus ego must carry edges').toBeGreaterThan(0);
      rec['focus'] = { occurrence: db.hub, nav_to_ready_ms: navMs, counts_initial: counts0 };

      /* Lift the node cap to the max so nothing rendered is hidden — the
       * ceiling number is then "all merged edges with both endpoints in
       * the store", the honest top of the renderable surface. */
      const capInput = page.locator('input.cap__input');
      await capInput.fill('2000');
      await capInput.dispatchEvent('change');
      await waitLayoutIdle(page, 30_000);

      /* Expansion loop — `]` cycles kbd focus through store.nodes()
       * (insertion order), Enter selects, `e` expands the selection via
       * GET /graph/neighbors (≤100 edges/merge). Duds (already-expanded
       * nodes re-fetching the same page) are counted, not hidden. */
      const stage = page.locator('.graph-stage');
      await stage.focus();
      const expansions: Record<string, unknown>[] = [];
      let edgesNow = (await renderedCounts(page)).edges ?? 0;
      let duds = 0;
      let tries = 0;
      const tExpand = Date.now();
      while (edgesNow < EDGE_TARGET && tries < EXPAND_MAX_TRIES) {
        tries++;
        await stage.focus();
        const t = Date.now();
        await page.keyboard.press(']');
        await page.keyboard.press('Enter');
        await page.keyboard.press('e');
        const before = edgesNow;
        const grew = await page
          .waitForFunction(
            (prev) => {
              const m = /e (\d+) relações/.exec(
                document.querySelector('.graph-stage')?.getAttribute('aria-label') ?? '',
              );
              return m && Number(m[1]) > prev;
            },
            before,
            { polling: 'raf', timeout: 3_000 },
          )
          .then(() => true)
          .catch(() => false);
        const now = await renderedCounts(page);
        const added = Math.max(0, (now.edges ?? edgesNow) - before);
        if (!grew) duds++;
        else
          expansions.push({
            try: tries,
            wall_ms: Date.now() - t,
            edges_added: added,
            edges_after: now.edges,
            nodes_after: now.nodes,
          });
        edgesNow = now.edges ?? edgesNow;
        /* Let merges breathe every 10 tries — the canvas sync is O(edges)
         * per commit; without air the wait above measures queueing, not
         * the expansion. */
        if (tries % 10 === 0) await page.waitForTimeout(120);
      }
      const expandMs = Date.now() - tExpand;
      await waitLayoutIdle(page, 60_000);

      const heapGrown = await heapMB(page);
      const panDense = await measurePan(page, PAN_MS);

      /* A REAL worker relayout on the dense set — wall window, main-thread
       * gap (async-layout contract: <300 ms), and the worker-reported
       * duration from the diagnostics panel. */
      const relMark = await page.evaluate(() => performance.now());
      const tRel = Date.now();
      await page.getByRole('button', { name: RELAYOUT_RE }).click();
      await expect(page.getByRole('button', { name: CANCEL_RE })).toBeVisible({ timeout: 10_000 });
      await expect(page.locator('.graph-stage')).toHaveAttribute('aria-busy', 'true');
      await waitLayoutIdle(page, 120_000);
      const relayoutWallMs = Date.now() - tRel;
      const relGap = await maxRafGapSince(page, relMark);

      /* Worker-reported layout ms — "Último layout" in the diagnostics. */
      await page.getByRole('button', { name: 'Diagnósticos' }).click();
      const lastLayoutText = (
        (await page
          .locator('#graph-diagnostics .diag__item', { hasText: 'Último layout' })
          .locator('dd')
          .textContent()
          .catch(() => null)) ?? ''
      ).trim();

      const tele = await telemetry(page);
      const probe = await probeSnapshot(page);
      const heap1 = await heapMB(page);
      const countsFinal = await renderedCounts(page);

      /* Invariants — the ceiling is the point of the test. */
      expect(
        countsFinal.edges ?? 0,
        `canvas must reach ≥${EDGE_TARGET} rendered edges`,
      ).toBeGreaterThanOrEqual(EDGE_TARGET);
      expect(probe.sigmaErrors, 'no Sigma errors during the dense session').toEqual([]);
      expect(probe.maxRafGap, 'no main-thread freeze ≥5s anywhere').toBeLessThan(FREEZE_MS);
      expect(pageErrors).toEqual([]);
      const r = tele!.runs;
      expect(r.started).toBe(r.done + r.cancelled + r.stale + r.error);

      rec['growth'] = {
        edge_target: EDGE_TARGET,
        edges_final: countsFinal.edges,
        nodes_final: countsFinal.nodes,
        productive_expansions: expansions.length,
        dud_tries: duds,
        tries_total: tries,
        wall_ms: expandMs,
        edges_per_productive_expansion:
          expansions.length > 0
            ? Math.round(
                (expansions.reduce((a, e) => a + (e['edges_added'] as number), 0) /
                  expansions.length) * 100,
              ) / 100
            : 0,
        expansions,
      };
      rec['pan_dense'] = panDense;
      rec['relayout_dense'] = {
        wall_ms: relayoutWallMs,
        main_thread_max_gap_ms: relGap,
        worker_reported: lastLayoutText,
      };
      rec['heap_mb'] = { before_load: heap0, after_growth: heapGrown, end: heap1 };
      rec['telemetry'] = tele;
      rec['probe'] = probe;
      rec['page_errors'] = pageErrors;
    } finally {
      /* Persist immediately — the merged file is rebuilt in afterAll and
       * a worker restart between tests must not lose a finished record. */
      writeFileSync(
        join(dirname(RESULTS_JSON), 'graph-ceiling-dense_ceiling.json'),
        JSON.stringify(rec, null, 2) + '\n',
      );
    }
  });

  /* ================================================================== *
   * 2. HEAP ATTRIBUTION — real V8 heap-snapshot diff around settled
   *    mount/unmount cycles (the ~400 KB/cycle the cycle-8 suite could
   *    not attribute). Snapshots are aggregated by constructor key in
   *    this process; the JSON records the top retained classes.
   *
   *    MEASURED CAVEAT (this cycle, see control_inpage_cycles): the
   *    retained DOM mass the instrumented protocol reports — whole
   *    destroyed <atlas-graph-table> trees, ~9–18k post-GC nodes/cycle —
   *    is held by DevTools remote handles ("... / DevTools console" →
   *    Global handles), i.e. by the measurement itself, not the app.
   *    In-page-driven cycles retain ~1/8th the heap and ~12 nodes/cycle.
   * ================================================================== */
  test('mount/unmount heap attribution — V8 snapshot diff', async ({ browser }) => {
    /* Separate explorer for the lifecycle dataset — its DATA_DIR differs
     * from the ceiling dataset, so it needs its own subprocess. */
    const rec: Record<string, unknown> = { dataset: CYCLES.label };
    const started = await startExplorer(CYCLES);
    const base2 = started.base;
    const page = await browser.newPage();
    try {
      await installProbes(page);
      const pageErrors: string[] = [];
      page.on('pageerror', (e) => pageErrors.push(`pageerror: ${String(e)}`));

      await page.goto(`${base2}/graph`, { waitUntil: 'domcontentloaded' });
      await waitForCanvas(page);
      await waitLayoutIdle(page);

      /* Warm-up: first mounts pay one-time costs (JIT, sigma/font/GL
       * caches, Angular internals) — identical to the cycle-8 protocol
       * so the delta compares honestly against its ~400 KB/cycle. */
      const toggleOnce = async (): Promise<void> => {
        await page.getByRole('button', { name: 'Lista', exact: true }).click();
        await page.waitForSelector('atlas-graph-table', { timeout: 15_000 });
        await page.getByRole('button', { name: 'Grafo', exact: true }).click();
        await waitForCanvas(page, 30_000);
        await waitLayoutIdle(page, 30_000);
      };
      for (let i = 0; i < 3; i++) await toggleOnce();

      const tele0 = await telemetry(page);
      const heap0 = await heapMB(page);
      const dom0 = await domCounters(page);
      const snapA = aggregateSnapshot(await heapSnapshot(page));

      const rows: Record<string, unknown>[] = [];
      /* Live reference: rows pushed later still land in the finally-write. */
      rec['dom_counters'] = { before: dom0, per_cycle_rows: rows };
      for (let i = 0; i < MOUNT_CYCLES; i++) {
        const c0 = await domCounters(page);
        await toggleOnce();
        const t = await telemetry(page);
        expect(t?.sigmaLive, `cycle ${i}: remounted sigma count`).toBe(1);
        const c1 = await domCounters(page);
        rows.push({
          cycle: i,
          dom_nodes: c1?.nodes ?? null,
          dom_listeners: c1?.jsEventListeners ?? null,
          dom_nodes_delta: c0 && c1 ? c1.nodes - c0.nodes : null,
        });
      }

      /* Settle fully: leave on the table view so the last destroy is
       * measured too, then let pending resolves land before the shot. */
      await page.getByRole('button', { name: 'Lista', exact: true }).click();
      await page.waitForSelector('atlas-graph-table', { timeout: 15_000 });
      await page.waitForTimeout(800);
      const snapB = aggregateSnapshot(await heapSnapshot(page));
      const tele1 = await telemetry(page);
      const heap1 = await heapMB(page);
      const dom1 = await domCounters(page);

      const perCycleKb =
        heap0 !== null && heap1 !== null
          ? Math.round(((heap1 - heap0) / MOUNT_CYCLES) * 1024 * 10) / 10
          : null;

      /* CONTROL — instrumentation-free cycles. The measured cycles above
       * are driven by Playwright locators + per-cycle CDP counters; a
       * dedicated probe run showed that protocol retains each destroyed
       * <atlas-graph-table> via "DevTools console" global handles
       * (~18k post-GC DOM nodes/cycle — the instrumented delta in
       * `dom_counters` rows). The same cycles run entirely in-page
       * (el.click() inside one evaluate, zero element handles, no CDP
       * in the loop) retain ~nothing — so the honest app-side bound is
       * the residual heap delta measured here, NOT the artifact above. */
      const heapC0 = await heapMB(page);
      const domC0 = await domCounters(page);
      await page.evaluate(async () => {
        const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
        const findBtn = (label: string) =>
          [...document.querySelectorAll('button')].find((b) => b.textContent?.trim() === label);
        /* Start on the graph view so every loop iteration is a full
         * table-create + table-destroy cycle. */
        findBtn('Grafo')?.click();
        for (let w = 0; w < 200 && !document.querySelector('.graph-stage'); w++) {
          await sleep(100);
        }
        await sleep(400);
        for (let i = 0; i < 4; i++) {
          findBtn('Lista')?.click();
          for (let w = 0; w < 100 && !document.querySelector('atlas-graph-table'); w++) {
            await sleep(100);
          }
          await sleep(250);
          findBtn('Grafo')?.click();
          for (let w = 0; w < 200 && !document.querySelector('.graph-stage'); w++) {
            await sleep(100);
          }
          await sleep(400);
        }
      });
      const heapC1 = await heapMB(page);
      const domC1 = await domCounters(page);
      rec['control_inpage_cycles'] = {
        cycles: 4,
        instrumentation: 'none in the loop — el.click() inside one evaluate; CDP only before/after',
        heap_mb: {
          before: heapC0,
          after: heapC1,
          delta: heapC0 !== null && heapC1 !== null ? Math.round((heapC1 - heapC0) * 100) / 100 : null,
          per_cycle_kb:
            heapC0 !== null && heapC1 !== null
              ? Math.round(((heapC1 - heapC0) / 4) * 1024 * 10) / 10
              : null,
        },
        dom_nodes: {
          before: domC0?.nodes ?? null,
          after: domC1?.nodes ?? null,
          delta: domC0 && domC1 ? domC1.nodes - domC0.nodes : null,
        },
        note:
          'in-page cycles retain ≈ 1/8th the instrumented heap delta and ' +
          '≈ 12 DOM nodes/cycle — the instrumented figure is a DevTools ' +
          'remote-handle artifact, not a confirmed app leak',
      };

      rec['cycles'] = MOUNT_CYCLES;
      rec['cycles_mode'] = 'all settled (waitLayoutIdle after every mount)';
      rec['heap_mb'] = {
        before: heap0,
        after: heap1,
        delta: heap0 !== null && heap1 !== null ? Math.round((heap1 - heap0) * 100) / 100 : null,
        per_cycle_kb: perCycleKb,
      };
      (rec['dom_counters'] as Record<string, unknown>)['after'] = dom1;
      rec['snapshot_diff'] = {
        a_total_mb: Math.round((snapA.total_bytes / 1048576) * 100) / 100,
        b_total_mb: Math.round((snapB.total_bytes / 1048576) * 100) / 100,
        delta_mb: Math.round(((snapB.total_bytes - snapA.total_bytes) / 1048576) * 100) / 100,
        detached_mb_after: Math.round((snapB.detached_bytes / 1048576) * 100) / 100,
        detached_nodes_after: snapB.detached_nodes,
        top_retained_classes: diffSnapshots(snapA, snapB),
      };
      rec['telemetry_after'] = tele1;
      rec['page_errors'] = pageErrors;

      /* Invariants — recorded above so a counter regression leaves the
       * full measurement behind. */
      expect(tele1?.sigmaLive).toBe(0);
      expect(tele1?.workersLive).toBe(0);
      const r = tele1!.runs;
      expect(r.started).toBe(r.done + r.cancelled + r.stale + r.error);
      expect(tele1!.sigmaTotal).toBe((tele0?.sigmaTotal ?? 0) + MOUNT_CYCLES);
      expect(pageErrors).toEqual([]);
    } finally {
      writeFileSync(
        join(dirname(RESULTS_JSON), 'graph-ceiling-heap_attribution.json'),
        JSON.stringify(rec, null, 2) + '\n',
      );
      await page.close().catch(() => {});
      started.proc.kill('SIGTERM');
    }
  });

  /* ================================================================== *
   * 3. LONG-SESSION TREND — sustained mixed interaction (pan, wheel
   *    zoom, kbd cycle, select; a real /search round-trip every 8th
   *    round). Post-GC heap sampled per window → slope, not a guess.
   * ================================================================== */
  test('long-session memory trend — pan/zoom/kbd/search ×N', async ({ page }) => {
    const pageErrors: string[] = [];
    page.on('pageerror', (e) => pageErrors.push(`pageerror: ${String(e)}`));
    await installProbes(page);

    const rec: Record<string, unknown> = {
      rounds: SESSION_ROUNDS,
      mix: `pan+wheel-zoom+kbd-cycle+select per round; real /search round-trip every ${SEARCH_EVERY}`,
    };
    try {
      const db = (measured['fixtures'] as { ceiling: { db: { hub: number } } }).ceiling.db;
      await page.goto(`${base}/graph?focus=${db.hub}`, { waitUntil: 'domcontentloaded' });
      await waitForCanvas(page);
      await waitLayoutIdle(page);
      const stage = page.locator('.graph-stage');
      const box0 = await stage.boundingBox();
      expect(box0).not.toBeNull();

      /* Warm the surface before the baseline (first-use costs). */
      for (let i = 0; i < 2; i++) {
        await stage.focus();
        await page.keyboard.press(']');
        await page.keyboard.press('Enter');
        await page.keyboard.press('Escape');
      }
      const heapBase = await heapMB(page);
      const samples: Record<string, unknown>[] = [];
      rec['heap_baseline_mb'] = heapBase;
      rec['samples'] = samples; // live reference — pushes land in finally-write

      const sidebar = page.locator('#shell-sidebar');
      for (let round = 0; round < SESSION_ROUNDS; round++) {
        if (round > 0 && round % SEARCH_EVERY === 0) {
          /* Real search round-trip: leave the graph, run a query, come
           * back. The graph remount on return is part of the session. */
          await sidebar.getByRole('link', { name: 'Busca', exact: true }).click();
          await page.locator('input[name="q"]').fill('func_0300');
          await page.getByRole('button', { name: /Buscar/ }).click();
          await page
            .locator('.search-hit')
            .first()
            .waitFor({ timeout: 15_000 })
            .catch(() => {});
          await sidebar.getByRole('link', { name: 'Grafo', exact: true }).click();
          await waitForCanvas(page, 30_000);
          await waitLayoutIdle(page, 30_000);
          await stage.focus();
        } else {
          /* In-graph sustained interaction: wheel zoom + drag pan + kbd
           * cycle + select — the real operator surface. */
          const box = await stage.boundingBox();
          if (box) {
            const cx = box.x + box.width / 2;
            const cy = box.y + box.height / 2;
            await page.mouse.move(cx, cy);
            await page.mouse.wheel(0, -240); // zoom in
            await page.mouse.wheel(0, 240); // zoom back out
            await page.mouse.move(cx + 40, cy);
            await page.mouse.down();
            await page.mouse.move(cx - 40, cy + 30, { steps: 6 });
            await page.mouse.up();
          }
          await stage.focus();
          for (let k = 0; k < 3; k++) {
            await page.keyboard.press(']');
          }
          await page.keyboard.press('Enter');
          await page.keyboard.press('Escape');
        }
        if (round % 5 === 4 || round === SESSION_ROUNDS - 1) {
          samples.push({
            round,
            heap_mb: await heapMB(page),
            dom: await domCounters(page),
          });
        }
      }

      await page.getByRole('button', { name: 'Lista', exact: true }).click();
      await page.waitForSelector('atlas-graph-table', { timeout: 15_000 });
      await page.waitForTimeout(600);
      const teleEnd = await telemetry(page);
      const heapEnd = await heapMB(page);
      const probe = await probeSnapshot(page);

      /* Linear fit over the sampled series — the trend claim. */
      const pts = samples
        .map((s) => ({ x: s['round'] as number, y: s['heap_mb'] as number | null }))
        .filter((p): p is { x: number; y: number } => p.y !== null);
      let slopeKbPerRound: number | null = null;
      if (pts.length >= 2) {
        const xm = pts.reduce((a, p) => a + p.x, 0) / pts.length;
        const ym = pts.reduce((a, p) => a + p.y, 0) / pts.length;
        const num = pts.reduce((a, p) => a + (p.x - xm) * (p.y - ym), 0);
        const den = pts.reduce((a, p) => a + (p.x - xm) * (p.x - xm), 0) || 1;
        slopeKbPerRound = Math.round((num / den) * 1024 * 10) / 10;
      }

      rec['heap_end_mb'] = heapEnd;
      rec['trend'] = {
        slope_kb_per_round: slopeKbPerRound,
        first_sample_mb: pts[0]?.y ?? null,
        last_sample_mb: pts[pts.length - 1]?.y ?? null,
      };
      rec['telemetry_end'] = teleEnd;
      rec['probe'] = probe;
      rec['page_errors'] = pageErrors;

      expect(probe.sigmaErrors).toEqual([]);
      expect(pageErrors).toEqual([]);
      expect(teleEnd?.sigmaLive).toBe(0);
      expect(teleEnd?.workersLive).toBe(0);
    } finally {
      writeFileSync(
        join(dirname(RESULTS_JSON), 'graph-ceiling-long_session.json'),
        JSON.stringify(rec, null, 2) + '\n',
      );
    }
  });
});
