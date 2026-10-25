import {
  Fa2Engine,
  Fa2EngineEvents,
  Fa2LayoutJob,
  Fa2LayoutRunner,
  Fa2RunResult,
} from './fa2-layout-runner';

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
});
