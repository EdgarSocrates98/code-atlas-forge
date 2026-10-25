import iterate from 'graphology-layout-forceatlas2/iterate';
import {
  recordFallback,
  trackWorker,
  untrackWorker,
} from './graph-telemetry';

/**
 * Async ForceAtlas2 runner — Phase 0.2, hardened in Cycle 8.
 *
 * The synchronous `forceAtlas2.assign()` measured ~2.3 s of blocked main
 * thread at 2000 nodes. This module moves the iterations into a Web
 * Worker (fa2.worker.ts) and turns layout into a cancellable, guarded
 * operation:
 *
 * - one run at a time — `run()` supersedes (cancels) any in-flight run;
 * - `cancel()` terminates the worker immediately;
 * - `isStale` is polled between progress messages and re-checked at
 *   completion, so results computed for an older graph generation are
 *   resolved as 'stale' and can be dropped before they touch the graph;
 * - a watchdog resolves 'error' if the worker goes silent, so a failed
 *   spawn or dead worker can never hang `layoutBusy` forever;
 * - EVERY terminal status shuts its engine down — Cycle 8 bug fix: a
 *   'done'/'error' run used to clear `active` without terminating the
 *   worker, leaving one zombie worker per completed layout (and a
 *   permanently leaked one on destroy);
 * - when no usable Worker exists (CSP `worker-src`, disabled workers,
 *   startup load failure) the engine degrades to `ChunkedFa2Engine` —
 *   the same iterations time-boxed on the main thread — reported via
 *   `onFallback` so the UI can say the layout ran degraded, never
 *   silently pretend it did not run.
 *
 * The unit boundary is the ENGINE (worker transport), not the algorithm:
 * specs inject a fake `Fa2Engine` and exercise the lifecycle — real FA2
 * correctness stays covered by the e2e benchmark.
 */

/** Flat-matrix record layout — mirrors graphology's helpers.js (PPN=10). */
export const FA2_NODE_STRIDE = 10;
/** Offset of the `fixed` flag inside a node record. */
export const FA2_NODE_FIXED_OFFSET = 9;

export interface Fa2LayoutJob {
  /** Flat node matrix (order × 10). Buffer is transferred to the worker. */
  nodes: Float32Array;
  /** Flat edge matrix (size × 3). Buffer is transferred to the worker. */
  edges: Float32Array;
  /** Complete FA2 settings (defaults already merged by the caller). */
  settings: Record<string, unknown>;
  /** Total iterations to execute. */
  iterations: number;
  /** Emit a progress message every N iterations (>= 1). */
  progressEvery: number;
  /**
   * Staleness predicate — evaluated between progress messages and once
   * more before a 'done' result is produced. When it flips true the run
   * resolves 'stale' and the engine is cancelled.
   */
  isStale?(): boolean;
  /** Live progress feed — (iterations done, iterations total). */
  onProgress?(done: number, total: number): void;
}

export type Fa2RunStatus = 'done' | 'cancelled' | 'stale' | 'error';

export interface Fa2RunResult {
  status: Fa2RunStatus;
  /** Final node matrix — present only on 'done'. */
  nodes: Float32Array | null;
  iterationsDone: number;
  /** Wall-clock duration of the run, inclusive of worker round-trips. */
  elapsedMs: number;
  /**
   * Why the run executed on the main-thread fallback engine — null when
   * it ran in the Web Worker. Surfaced so the UI can report a degraded
   * layout honestly.
   */
  fallback: string | null;
}

export interface Fa2EngineEvents {
  onProgress(done: number, total: number): void;
  onDone(nodes: Float32Array): void;
  onError(error: unknown): void;
  /**
   * The engine degraded to a fallback backend before producing results
   * (e.g. no usable Worker). Fired once, before any progress — the run
   * still completes through the normal events afterwards.
   */
  onFallback?(reason: string): void;
}

/**
 * Compute backend for one layout pass. `start` must never block; every
 * outcome is reported through `events`. `cancel` must stop the work
 * promptly (worker terminate / clear timers) — after it, the engine may
 * still emit events, which the runner ignores.
 */
export interface Fa2Engine {
  start(job: Fa2LayoutJob, events: Fa2EngineEvents): void;
  cancel(): void;
}

export type Fa2EngineFactory = () => Fa2Engine;

interface Fa2WorkerReply {
  type: 'progress' | 'done' | 'error';
  done?: number;
  total?: number;
  nodes?: ArrayBuffer;
  message?: string;
}

/**
 * Main-thread fallback engine: the SAME `iterate` passes the worker runs,
 * sliced into ~12 ms chunks between `setTimeout` yields so the event
 * loop stays responsive. Used when no usable Web Worker exists — the
 * layout still converges, just slower and reported as degraded via
 * `onFallback`.
 *
 * Progress is emitted at least once per chunk (not only at exact
 * `progressEvery` boundaries) so the runner watchdog sees a live engine.
 */
export class ChunkedFa2Engine implements Fa2Engine {
  /** Budget of contiguous iterate() calls per macrotask. */
  static readonly CHUNK_BUDGET_MS = 12;

  private timer: ReturnType<typeof setTimeout> | null = null;
  private cancelled = false;

  start(job: Fa2LayoutJob, events: Fa2EngineEvents): void {
    const total = Math.max(0, Math.floor(job.iterations) || 0);
    const every = Math.max(1, Math.floor(job.progressEvery) || 1);
    const nodes = job.nodes;
    const edges = job.edges;
    let i = 0;
    let lastReported = 0;

    const step = (): void => {
      if (this.cancelled) return;
      this.timer = null;
      const chunkStart = performance.now();
      try {
        while (i < total && performance.now() - chunkStart < ChunkedFa2Engine.CHUNK_BUDGET_MS) {
          iterate(job.settings, nodes, edges);
          i++;
          if (i % every === 0 && i < total) {
            lastReported = i;
            events.onProgress(i, total);
          }
        }
      } catch (error) {
        events.onError(error);
        return;
      }
      if (i >= total) {
        events.onDone(nodes);
        return;
      }
      if (i !== lastReported) {
        lastReported = i;
        events.onProgress(i, total);
      }
      this.timer = setTimeout(step, 0);
    };
    this.timer = setTimeout(step, 0);
  }

  cancel(): void {
    this.cancelled = true;
    if (this.timer !== null) {
      clearTimeout(this.timer);
      this.timer = null;
    }
  }
}

/**
 * Real backend: one dedicated worker per run. Same-origin script —
 * compatible with the explorer CSP (`script-src 'self'`); no Blob URLs.
 *
 * Degradation contract: if no Worker can be used (API absent, spawn
 * refused, or the worker errors before its first message — the classic
 * CSP `worker-src` signature), the run transparently continues on the
 * chunked main-thread engine and reports `onFallback`. A worker that
 * dies AFTER producing output is a real crash — reported via `onError`,
 * never silently retried on the main thread.
 */
export class WorkerFa2Engine implements Fa2Engine {
  private worker: Worker | null = null;
  private delegate: ChunkedFa2Engine | null = null;

  start(job: Fa2LayoutJob, events: Fa2EngineEvents): void {
    if (typeof Worker !== 'function') {
      this.fallbackToChunked(job, events, 'worker-api-unavailable');
      return;
    }
    let worker: Worker;
    try {
      worker = new Worker(new URL('./fa2.worker', import.meta.url), {
        type: 'module',
      });
    } catch {
      this.fallbackToChunked(job, events, 'worker-spawn-refused');
      return;
    }
    this.worker = worker;
    trackWorker(worker);

    // Whether the worker produced at least one protocol message. An
    // onerror before that point means the script never ran (blocked or
    // missing chunk) — safe to redo on the main thread.
    let heard = false;

    worker.onmessage = (event: MessageEvent<Fa2WorkerReply>) => {
      const msg = event.data;
      if (!msg) return;
      heard = true;
      if (msg.type === 'progress' && typeof msg.done === 'number') {
        events.onProgress(msg.done, msg.total ?? job.iterations);
      } else if (msg.type === 'done' && msg.nodes) {
        events.onDone(new Float32Array(msg.nodes));
      } else if (msg.type === 'error') {
        events.onError(new Error(msg.message ?? 'fa2 worker error'));
      }
    };
    worker.onerror = (event: ErrorEvent) => {
      if (!heard && this.worker !== null) {
        this.shutdownWorker();
        this.fallbackToChunked(job, events, 'worker-startup-error');
        return;
      }
      events.onError(new Error(event.message || 'fa2 worker failed'));
    };
    worker.onmessageerror = () => {
      events.onError(new Error('fa2 worker messageerror'));
    };
    // Transfer COPIES of the matrices, not the caller's arrays: if the
    // worker dies before its first message we fall back to the chunked
    // engine, which needs `job.nodes`/`job.edges` still attached on this
    // thread (a transferred buffer detaches to length 0). At the render
    // cap this is ~100 kB — sub-millisecond.
    const nodesCopy = job.nodes.slice();
    const edgesCopy = job.edges.slice();
    worker.postMessage(
      {
        type: 'start',
        nodes: nodesCopy.buffer,
        edges: edgesCopy.buffer,
        settings: job.settings,
        iterations: job.iterations,
        progressEvery: job.progressEvery,
      },
      [nodesCopy.buffer, edgesCopy.buffer],
    );
  }

  cancel(): void {
    this.shutdownWorker();
    this.delegate?.cancel();
    this.delegate = null;
  }

  /** Terminate + untrack the live worker handle, if any. */
  private shutdownWorker(): void {
    const worker = this.worker;
    this.worker = null;
    if (worker) {
      worker.terminate();
      untrackWorker(worker);
    }
  }

  private fallbackToChunked(
    job: Fa2LayoutJob,
    events: Fa2EngineEvents,
    reason: string,
  ): void {
    recordFallback();
    events.onFallback?.(reason);
    const delegate = new ChunkedFa2Engine();
    this.delegate = delegate;
    delegate.start(job, events);
  }
}

export interface Fa2LayoutRunnerOptions {
  engine?: Fa2EngineFactory;
  /** Silence budget per run — no message within it resolves 'error'. */
  watchdogMs?: number;
  now?: () => number;
}

const DEFAULT_WATCHDOG_MS = 30_000;

export class Fa2LayoutRunner {
  private active: { engine: Fa2Engine; finish: (status: Fa2RunStatus, nodes?: Float32Array) => void } | null =
    null;
  private readonly engineFactory: Fa2EngineFactory;
  private readonly watchdogMs: number;
  private readonly now: () => number;

  constructor(options: Fa2LayoutRunnerOptions = {}) {
    this.engineFactory = options.engine ?? (() => new WorkerFa2Engine());
    this.watchdogMs = options.watchdogMs ?? DEFAULT_WATCHDOG_MS;
    this.now = options.now ?? (() => performance.now());
  }

  get running(): boolean {
    return this.active !== null;
  }

  /**
   * Start a bounded FA2 pass. Any in-flight run is cancelled first — the
   * newest layout request always wins. The promise always settles exactly
   * once with a terminal status; it never rejects.
   */
  run(job: Fa2LayoutJob): Promise<Fa2RunResult> {
    this.cancel();
    const startedAt = this.now();
    const promise = new Promise<Fa2RunResult>((resolve) => {
      let iterationsDone = 0;
      let settled = false;
      let fallback: string | null = null;
      let watchdog: ReturnType<typeof setTimeout> | null = null;
      let engine: Fa2Engine | null = null;

      const clearWatchdog = (): void => {
        if (watchdog !== null) {
          clearTimeout(watchdog);
          watchdog = null;
        }
      };

      const finish = (status: Fa2RunStatus, nodes: Float32Array | null = null): void => {
        if (settled) return;
        settled = true;
        clearWatchdog();
        // Every terminal status releases the engine — 'done' and 'error'
        // included. Dropping `active` without cancelling used to leave
        // the finished worker alive: one zombie per completed layout,
        // and a permanently leaked worker if the component died between
        // the last progress message and the next cancel().
        engine?.cancel();
        if (this.active?.finish === finish) this.active = null;
        resolve({
          status,
          nodes,
          iterationsDone,
          elapsedMs: this.now() - startedAt,
          fallback,
        });
      };

      const armWatchdog = (): void => {
        clearWatchdog();
        watchdog = setTimeout(() => {
          finish('error');
        }, this.watchdogMs);
      };

      try {
        engine = this.engineFactory();
        this.active = { engine, finish };
        armWatchdog();
        engine.start(job, {
          onProgress: (done, total) => {
            if (settled) return;
            iterationsDone = done;
            job.onProgress?.(done, total);
            armWatchdog();
            if (job.isStale?.()) {
              finish('stale');
            }
          },
          onDone: (nodes) => {
            if (settled) return;
            iterationsDone = job.iterations;
            if (job.isStale?.()) {
              finish('stale');
              return;
            }
            finish('done', nodes);
          },
          onError: (error) => {
            if (settled) return;
            finish('error');
          },
          onFallback: (reason) => {
            fallback = reason;
          },
        });
      } catch {
        // start() may have partially initialized the engine — finish()
        // cancels it so a half-spawned backend can never linger.
        finish('error');
      }
    });
    return promise;
  }

  /** Cancel the in-flight run, if any — resolves it as 'cancelled'. */
  cancel(): void {
    const active = this.active;
    if (!active) return;
    this.active = null;
    // finish() shuts the engine down — exactly once per run.
    active.finish('cancelled');
  }
}
