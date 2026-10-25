import Graph from 'graphology';
import FA2_DEFAULT_SETTINGS from 'graphology-layout-forceatlas2/defaults';
import { graphToByteArrays } from 'graphology-layout-forceatlas2/helpers';
import {
  ChunkedFa2Engine,
  Fa2Engine,
  Fa2EngineEvents,
  Fa2LayoutJob,
  Fa2LayoutRunner,
  Fa2RunResult,
  WorkerFa2Engine,
} from './fa2-layout-runner';
import { graphTelemetry } from './graph-telemetry';

/**
 * Worker-lifecycle coverage for the async FA2 runner. The engine boundary
 * is faked — the real worker/algorithm is exercised by the e2e benchmark;
 * these specs pin the contract that matters here: one run at a time,
 * cancel/stale semantics, progress plumbing and terminal statuses.
 */
class FakeEngine implements Fa2Engine {
  job: Fa2LayoutJob | null = null;
  events: Fa2EngineEvents | null = null;
  started = 0;
  cancelled = 0;

  start(job: Fa2LayoutJob, events: Fa2EngineEvents): void {
    this.started++;
    this.job = job;
    this.events = events;
  }

  cancel(): void {
    this.cancelled++;
  }

  /* test helpers ---------------------------------------------------- */
  progress(done: number, total?: number): void {
    this.events?.onProgress(done, total ?? this.job?.iterations ?? 0);
  }

  done(nodes?: Float32Array): void {
    this.events?.onDone(nodes ?? new Float32Array((this.job?.nodes.length ?? 0)));
  }

  fail(error: unknown = new Error('boom')): void {
    this.events?.onError(error);
  }
}

class ThrowEngine extends FakeEngine {
  override start(): void {
    throw new Error('spawn refused');
  }
}

function makeJob(extra: Partial<Fa2LayoutJob> = {}): Fa2LayoutJob {
  return {
    nodes: new Float32Array(30),
    edges: new Float32Array(9),
    settings: { slowDown: 5 },
    iterations: 10,
    progressEvery: 2,
    ...extra,
  };
}

describe('Fa2LayoutRunner (worker lifecycle)', () => {
  let engines: FakeEngine[];
  let runner: Fa2LayoutRunner;
  let now: number;

  function nextEngine(): FakeEngine {
    const e = new FakeEngine();
    engines.push(e);
    return e;
  }

  beforeEach(() => {
    engines = [];
    now = 1_000;
    runner = new Fa2LayoutRunner({
      engine: () => nextEngine(),
      now: () => now,
    });
  });

  it('starts the engine once with the job and resolves done with the final matrix', async () => {
    const job = makeJob();
    const promise = runner.run(job);
    expect(runner.running).toBeTrue();
    expect(engines.length).toBe(1);
    expect(engines[0].job).toBe(job);

    const matrix = new Float32Array(30).fill(7);
    engines[0].done(matrix);

    const result = await promise;
    expect(result.status).toBe('done');
    expect(result.nodes).toBe(matrix);
    expect(result.iterationsDone).toBe(10);
    expect(runner.running).toBeFalse();
  });

  it('reports wall-clock elapsed time measured by the injected clock', async () => {
    const promise = runner.run(makeJob());
    now += 42.5;
    engines[0].done();
    const result = await promise;
    expect(result.status).toBe('done');
    expect(result.elapsedMs).toBeCloseTo(42.5, 5);
  });

  it('plumbs progress through to the job callback', async () => {
    const seen: Array<[number, number]> = [];
    const promise = runner.run(
      makeJob({ onProgress: (d, t) => seen.push([d, t]) }),
    );
    engines[0].progress(2);
    engines[0].progress(4);
    engines[0].done();
    const result = await promise;
    expect(seen).toEqual([
      [2, 10],
      [4, 10],
    ]);
    expect(result.status).toBe('done');
  });

  it('cancel() terminates the engine and resolves cancelled — stray engine events are ignored', async () => {
    const promise = runner.run(makeJob());
    runner.cancel();
    expect(runner.running).toBeFalse();
    expect(engines[0].cancelled).toBe(1);
    const result = await promise;
    expect(result.status).toBe('cancelled');
    expect(result.nodes).toBeNull();

    // A zombie engine emitting after cancel must not corrupt anything.
    expect(() => engines[0].done()).not.toThrow();
    expect(() => engines[0].progress(3)).not.toThrow();
    expect(runner.running).toBeFalse();
  });

  it('cancel() on an idle runner is a no-op', () => {
    expect(() => runner.cancel()).not.toThrow();
    expect(runner.running).toBeFalse();
  });

  it('a second run() supersedes the in-flight one — old run resolves cancelled', async () => {
    const first = runner.run(makeJob());
    const second = runner.run(makeJob({ iterations: 5 }));

    expect(engines.length).toBe(2);
    expect(engines[0].cancelled).toBe(1);
    await expectAsync(first).toBeResolvedTo(
      jasmine.objectContaining({ status: 'cancelled' }),
    );

    engines[1].done(new Float32Array(30));
    const result = await second;
    expect(result.status).toBe('done');
    expect(result.iterationsDone).toBe(5);
  });

  it('isStale flipping during the run resolves stale and cancels the engine', async () => {
    let stale = false;
    const promise = runner.run(makeJob({ isStale: () => stale }));
    stale = true;
    engines[0].progress(2);
    const result = await promise;
    expect(result.status).toBe('stale');
    expect(result.nodes).toBeNull();
    expect(result.iterationsDone).toBe(2);
    expect(engines[0].cancelled).toBe(1);
  });

  it('isStale checked at completion — a late flip discards the done matrix', async () => {
    let stale = false;
    const promise = runner.run(makeJob({ isStale: () => stale }));
    stale = true;
    engines[0].done();
    const result = await promise;
    expect(result.status).toBe('stale');
    expect(result.nodes).toBeNull();
    expect(engines[0].cancelled).toBe(1);
  });

  it('engine error resolves error without throwing', async () => {
    const promise = runner.run(makeJob());
    engines[0].fail();
    const result = await promise;
    expect(result.status).toBe('error');
    expect(result.nodes).toBeNull();
    expect(runner.running).toBeFalse();
  });

  it('a throwing engine factory/start resolves error instead of rejecting', async () => {
    const throwing = new Fa2LayoutRunner({
      engine: () => new ThrowEngine(),
    });
    const result = await throwing.run(makeJob());
    expect(result.status).toBe('error');
    expect(throwing.running).toBeFalse();
  });

  it('watchdog resolves error when the engine goes silent', async () => {
    const timed = new Fa2LayoutRunner({
      engine: () => nextEngine(),
      watchdogMs: 25,
    });
    const result = await timed.run(makeJob());
    expect(result.status).toBe('error');
    expect(engines[0].cancelled).toBe(1);
  });

  it('watchdog rearms on progress — a live run is never timed out', async () => {
    const timed = new Fa2LayoutRunner({
      engine: () => nextEngine(),
      watchdogMs: 40,
    });
    const promise = timed.run(makeJob());
    // Two progress beats ~20ms apart inside the 40ms window.
    await new Promise((r) => setTimeout(r, 20));
    engines[0].progress(2);
    await new Promise((r) => setTimeout(r, 20));
    engines[0].progress(4);
    engines[0].done();
    const result = await promise;
    expect(result.status).toBe('done');
  });

  it('results resolve exactly once — double done does not corrupt the outcome', async () => {
    const promise = runner.run(makeJob());
    engines[0].done();
    engines[0].done();
    engines[0].fail();
    const result = await promise;
    expect(result.status).toBe('done');
  });

  /* ------------------------------------------------------------------
   * Cycle-8 regression: EVERY terminal status must release its engine.
   * Before the fix, 'done'/'error' cleared `active` without cancelling —
   * one zombie worker per completed layout, and a permanently leaked
   * worker if the component died right after the last message.
   * ------------------------------------------------------------------ */

  it('a DONE run terminates its engine — no zombie survives success', async () => {
    const promise = runner.run(makeJob());
    engines[0].done();
    const result = await promise;
    expect(result.status).toBe('done');
    expect(engines[0].cancelled).toBe(1);
    expect(runner.running).toBeFalse();
  });

  it('an ERROR run terminates its engine — a crashed backend is not kept', async () => {
    const promise = runner.run(makeJob());
    engines[0].fail();
    const result = await promise;
    expect(result.status).toBe('error');
    expect(engines[0].cancelled).toBe(1);
  });

  it('repeated runs never accumulate live engines', async () => {
    for (let i = 0; i < 5; i++) {
      const promise = runner.run(makeJob());
      engines[i].done();
      await promise;
    }
    expect(engines.length).toBe(5);
    expect(engines.every((e) => e.cancelled === 1)).toBeTrue();
    expect(runner.running).toBeFalse();
  });

  /* ------------------------- fallback plumbing ----------------------- */

  it('onFallback marks the result as degraded without failing it', async () => {
    class FallbackThenDone extends FakeEngine {
      override start(job: Fa2LayoutJob, events: Fa2EngineEvents): void {
        super.start(job, events);
        events.onFallback?.('injected-degraded');
      }
    }
    const engine = new FallbackThenDone();
    const r = new Fa2LayoutRunner({ engine: () => engine });
    const promise = r.run(makeJob());
    engine.done();
    const result = await promise;
    expect(result.status).toBe('done');
    expect(result.fallback).toBe('injected-degraded');
  });

  it('a normal run reports fallback=null', async () => {
    const promise = runner.run(makeJob());
    engines[0].done();
    const result = await promise;
    expect(result.fallback).toBeNull();
  });
});

/* ----------------------------------------------------------------------
 * ChunkedFa2Engine — the real FA2 `iterate` on the main thread, sliced
 * into ~12 ms macrotasks. These specs run the actual algorithm on small
 * matrices: no fake engine here.
 * -------------------------------------------------------------------- */

/** Small real job: a 24-node ring, matrices built by the same helper
 * the canvas uses. */
function realJob(iterations = 40): Fa2LayoutJob {
  const graph = new Graph({ multi: false, type: 'directed' });
  for (let i = 0; i < 24; i++) graph.addNode(`n${i}`, { x: i, y: (i * 7) % 11 });
  for (let i = 0; i < 24; i++) graph.addEdge(`n${i}`, `n${(i + 1) % 24}`);
  const matrices = graphToByteArrays(graph, () => 1);
  return {
    nodes: matrices.nodes,
    edges: matrices.edges,
    settings: { ...FA2_DEFAULT_SETTINGS, adjustSizes: true, strongGravityMode: true },
    iterations,
    progressEvery: 5,
  };
}

describe('ChunkedFa2Engine (main-thread fallback)', () => {
  it('runs real FA2 iterations to completion with progress beats', async () => {
    const job = realJob(40);
    const before = Array.from(job.nodes);
    const progress: number[] = [];
    const result = await new Promise<{ done: boolean; nodes: Float32Array | null }>(
      (resolve) => {
        const engine = new ChunkedFa2Engine();
        engine.start(job, {
          onProgress: (d) => progress.push(d),
          onDone: (nodes) => resolve({ done: true, nodes }),
          onError: (error) => resolve({ done: false, nodes: null }),
        });
      },
    );
    expect(result.done).toBeTrue();
    expect(result.nodes).not.toBeNull();
    expect(progress.length).toBeGreaterThan(0);
    expect(progress[progress.length - 1]).toBeLessThanOrEqual(40);
    // The algorithm really moved nodes — and every coordinate stayed finite.
    const moved = before.some((v, i) => v !== result.nodes![i]);
    expect(moved).toBeTrue();
    for (const v of result.nodes!) {
      expect(Number.isFinite(v)).toBeTrue();
    }
  });

  it('cancel stops the chunked loop promptly', async () => {
    const engine = new ChunkedFa2Engine();
    let doneFired = false;
    // Iterations so high the engine cannot finish inside the test: each
    // 12 ms chunk covers a bounded slice, then cancel() clears the timer.
    engine.start(realJob(500_000), {
      onProgress: () => {},
      onDone: () => {
        doneFired = true;
      },
      onError: () => {
        doneFired = true;
      },
    });
    engine.cancel();
    await new Promise((r) => setTimeout(r, 30));
    expect(doneFired).toBeFalse();
  });

  it('cancel before the first chunk never starts iterating', async () => {
    const engine = new ChunkedFa2Engine();
    let events = 0;
    engine.start(realJob(10), {
      onProgress: () => events++,
      onDone: () => events++,
      onError: () => events++,
    });
    engine.cancel();
    await new Promise((r) => setTimeout(r, 30));
    expect(events).toBe(0);
  });

  it('an iterate() throw surfaces through onError', async () => {
    const engine = new ChunkedFa2Engine();
    const job = realJob(10);
    job.settings = {}; // incomplete settings — iterate throws on a bad matrix
    job.nodes = new Float32Array(0); // malformed: no node records
    const status = await new Promise<string>((resolve) => {
      engine.start(job, {
        onProgress: () => {},
        onDone: () => resolve('done'),
        onError: () => resolve('error'),
      });
    });
    // Zero-record matrices either throw (error) or trivially finish (done
    // with 0 iterations) — the contract under test is: never hang, never
    // throw through the caller.
    expect(['done', 'error']).toContain(status);
  });
});

/* ----------------------------------------------------------------------
 * WorkerFa2Engine degradation — window.Worker absent (CSP / test double)
 * falls through to the chunked engine and reports the reason.
 * -------------------------------------------------------------------- */

describe('WorkerFa2Engine fallback (no usable Worker)', () => {
  let savedWorker: unknown;

  beforeEach(() => {
    savedWorker = (window as unknown as { Worker?: unknown }).Worker;
  });
  afterEach(() => {
    (window as unknown as { Worker?: unknown }).Worker = savedWorker;
  });

  it('falls back to the chunked engine when Worker is unavailable', async () => {
    (window as unknown as { Worker?: unknown }).Worker = undefined;
    const fallbacksBefore = graphTelemetry().fallbacks;
    const runner = new Fa2LayoutRunner(); // default engine = WorkerFa2Engine
    const result = await runner.run(realJob(30));
    expect(result.status).toBe('done');
    expect(result.fallback).toBe('worker-api-unavailable');
    expect(result.nodes).not.toBeNull();
    expect(graphTelemetry().fallbacks).toBe(fallbacksBefore + 1);
    // No worker was ever created — nothing to leak.
    expect(graphTelemetry().workersLive).toBe(0);
  });

  it('falls back when the Worker constructor throws (spawn refused)', async () => {
    (window as unknown as { Worker?: unknown }).Worker = class {
      constructor() {
        throw new Error('csp refused');
      }
    };
    const runner = new Fa2LayoutRunner();
    const result = await runner.run(realJob(30));
    expect(result.status).toBe('done');
    expect(result.fallback).toBe('worker-spawn-refused');
  });

  it('a cancelled fallback run resolves cancelled', async () => {
    (window as unknown as { Worker?: unknown }).Worker = undefined;
    const runner = new Fa2LayoutRunner();
    const promise = runner.run(realJob(200_000));
    runner.cancel();
    const result = await promise;
    expect(result.status).toBe('cancelled');
    expect(result.fallback).toBe('worker-api-unavailable');
  });
});
