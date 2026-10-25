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

  describe('domain + federation endpoints (lf-0122)', () => {
    let fetchSpy: jasmine.Spy;

    function lastUrl(): string {
      return String(fetchSpy.calls.mostRecent().args[0]);
    }

    beforeEach(() => {
      fetchSpy = jasmine
        .createSpy('fetch')
        .and.resolveTo(new Response(JSON.stringify({}), { status: 200 }));
      globalThis.fetch = fetchSpy as unknown as typeof fetch;
    });

    it('getDomains hits /domains with repository/snapshot scope', async () => {
      await service.getDomains({ repositoryId: 7, snapshotId: 42 });
      expect(lastUrl()).toContain('/api/v1/domains?');
      expect(lastUrl()).toContain('repository_id=7');
      expect(lastUrl()).toContain('snapshot_id=42');
    });

    it('getDomainEntities serializes domain/kind/q/limit once each', async () => {
      await service.getDomainEntities({
        domain: 'sql',
        kind: 'data_table,data_view',
        q: 'orders',
        limit: 50,
        offset: 10,
        repositoryId: 7,
        snapshotId: 42,
      });
      const url = lastUrl();
      expect(url).toContain('/api/v1/domains/entities?');
      expect(url).toContain('domain=sql');
      expect(url).toContain('kind=data_table%2Cdata_view');
      expect(url).toContain('q=orders');
      expect(url.match(/limit=/g)?.length).toBe(1);
      expect(url).toContain('limit=50');
      expect(url).toContain('offset=10');
      expect(url).toContain('repository_id=7');
      expect(url).toContain('snapshot_id=42');
    });

    it('getDomainEntities omits offset=0', async () => {
      await service.getDomainEntities({ limit: 10, offset: 0 });
      expect(lastUrl()).not.toContain('offset=');
    });

    it('getDomainEntityNeighbors encodes id and maps direction', async () => {
      await service.getDomainEntityNeighbors(12, 'in', { snapshotId: 3 });
      const url = lastUrl();
      expect(url).toContain('/api/v1/domains/entities/12/neighbors?');
      expect(url).toContain('direction=in');
      expect(url).toContain('snapshot_id=3');
    });

    it('getDomainEntityNeighbors omits direction for the default both', async () => {
      await service.getDomainEntityNeighbors(12);
      expect(lastUrl()).not.toContain('direction=');
    });

    it('getDomainRelations filters by comma-separated type', async () => {
      await service.getDomainRelations({
        type: 'DEPENDS_ON,TRIGGERS',
        limit: 200,
        repositoryId: 1,
      });
      const url = lastUrl();
      expect(url).toContain('/api/v1/domains/relations?');
      expect(url).toContain('type=DEPENDS_ON%2CTRIGGERS');
      expect(url).toContain('limit=200');
      expect(url).toContain('repository_id=1');
    });

    it('getDomainLineage passes table + direction + depth + scope', async () => {
      await service.getDomainLineage('analytics.orders', 'upstream', 3, {
        snapshotId: 9,
      });
      const url = lastUrl();
      expect(url).toContain('/api/v1/domains/lineage?');
      expect(url).toContain('table=analytics.orders');
      expect(url).toContain('direction=upstream');
      expect(url).toContain('depth=3');
      expect(url).toContain('snapshot_id=9');
    });

    it('getDomainLineage accepts numeric ids without direction/depth extras', async () => {
      await service.getDomainLineage(5);
      const url = lastUrl();
      expect(url).toContain('table=5');
      expect(url).toContain('direction=both');
      expect(url).not.toContain('depth=');
    });

    it('listFederationWorkspaces hits /federation/workspaces', async () => {
      await service.listFederationWorkspaces();
      expect(lastUrl()).toContain('/api/v1/federation/workspaces');
    });

    it('getFederationWorkspace encodes the workspace id', async () => {
      await service.getFederationWorkspace(3);
      expect(lastUrl()).toContain('/api/v1/federation/workspaces/3');
      expect(lastUrl()).not.toContain('/view');
    });

    it('getFederationView hits the /view subresource', async () => {
      await service.getFederationView(3);
      expect(lastUrl()).toContain('/api/v1/federation/workspaces/3/view');
    });
  });
});
