import { TestBed } from '@angular/core/testing';
import { ApiService } from './api.service';
import { ApiAbortedError, ApiUnreachableError } from './api.errors';

describe('ApiService', () => {
  let service: ApiService;
  let originalFetch: typeof fetch;

  beforeEach(() => {
    TestBed.configureTestingModule({});
    service = TestBed.inject(ApiService);
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('wraps a network failure as ApiUnreachableError', async () => {
    globalThis.fetch = jasmine
      .createSpy('fetch')
      .and.rejectWith(new TypeError('Failed to fetch')) as unknown as typeof fetch;

    await expectAsync(service.listRepositories()).toBeRejectedWith(
      jasmine.any(ApiUnreachableError),
    );
  });

  it('wraps an aborted request as ApiAbortedError', async () => {
    globalThis.fetch = jasmine
      .createSpy('fetch')
      .and.rejectWith(new DOMException('Aborted', 'AbortError')) as unknown as typeof fetch;

    await expectAsync(service.listRepositories()).toBeRejectedWith(jasmine.any(ApiAbortedError));
  });

  it('passes an AbortSignal to fetch on every call', async () => {
    const fetchSpy = jasmine
      .createSpy('fetch')
      .and.resolveTo(new Response(JSON.stringify([]), { status: 200 }));
    globalThis.fetch = fetchSpy as unknown as typeof fetch;

    await service.listRepositories();

    const callArgs = fetchSpy.calls.mostRecent().args;
    const requestInit = callArgs[1] as RequestInit;
    expect(requestInit.signal).toBeInstanceOf(AbortSignal);
  });

  it('cancelAll() aborts in-flight requests', async () => {
    let capturedSignal: AbortSignal | undefined;
    globalThis.fetch = jasmine
      .createSpy('fetch')
      .and.callFake((_url: string, init?: RequestInit) => {
        capturedSignal = init?.signal ?? undefined;
        return new Promise(() => {
          /* never resolves, simulating a hung request */
        });
      }) as unknown as typeof fetch;

    void service.listRepositories();
    service.cancelAll();

    expect(capturedSignal?.aborted).toBe(true);
  });
});
