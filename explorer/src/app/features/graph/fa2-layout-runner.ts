/**
 * Async ForceAtlas2 runner — Phase 0.2.
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
 *   spawn or dead worker can never hang `layoutBusy` forever.
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
}

export interface Fa2EngineEvents {
  onProgress(done: number, total: number): void;
  onDone(nodes: Float32Array): void;
  onError(error: unknown): void;
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
 * Real backend: one dedicated worker per run. Same-origin script —
 * compatible with the explorer CSP (`script-src 'self'`); no Blob URLs.
 */
class WorkerFa2Engine implements Fa2Engine {
  private worker: Worker | null = null;

  start(job: Fa2LayoutJob, events: Fa2EngineEvents): void {
    const worker = new Worker(new URL('./fa2.worker', import.meta.url), {
      type: 'module',
    });
    this.worker = worker;
    worker.onmessage = (event: MessageEvent<Fa2WorkerReply>) => {
      const msg = event.data;
      if (!msg) return;
      if (msg.type === 'progress' && typeof msg.done === 'number') {
        events.onProgress(msg.done, msg.total ?? job.iterations);
      } else if (msg.type === 'done' && msg.nodes) {
        events.onDone(new Float32Array(msg.nodes));
      } else if (msg.type === 'error') {
        events.onError(new Error(msg.message ?? 'fa2 worker error'));
      }
    };
    worker.onerror = (event: ErrorEvent) => {
      events.onError(new Error(event.message || 'fa2 worker failed'));
    };
    worker.onmessageerror = () => {
      events.onError(new Error('fa2 worker messageerror'));
    };
    worker.postMessage(
      {
        type: 'start',
        nodes: job.nodes.buffer,
        edges: job.edges.buffer,
        settings: job.settings,
        iterations: job.iterations,
        progressEvery: job.progressEvery,
      },
      [job.nodes.buffer, job.edges.buffer],
    );
  }

  cancel(): void {
    this.worker?.terminate();
    this.worker = null;
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
        if (this.active?.finish === finish) this.active = null;
        resolve({
          status,
          nodes,
          iterationsDone,
          elapsedMs: this.now() - startedAt,
        });
      };

      const armWatchdog = (): void => {
        clearWatchdog();
        watchdog = setTimeout(() => {
          engine?.cancel();
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
              engine?.cancel();
              finish('stale');
            }
          },
          onDone: (nodes) => {
            if (settled) return;
            iterationsDone = job.iterations;
            if (job.isStale?.()) {
              engine?.cancel();
              finish('stale');
              return;
            }
            finish('done', nodes);
          },
          onError: (error) => {
            if (settled) return;
            finish('error');
          },
        });
      } catch {
        // start() may have partially initialized the engine — cancel it so
        // a half-spawned backend can never linger.
        engine?.cancel();
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
    active.engine.cancel();
    active.finish('cancelled');
  }
}
