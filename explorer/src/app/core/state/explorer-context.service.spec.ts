import { TestBed } from '@angular/core/testing';
import { ApiService } from '../api/api.service';
import type {
  ApiGraphNode,
  ApiRepositoryItem,
  ApiSnapshotItem,
} from '../api/api.service';
import { ApiUnreachableError } from '../api/api.errors';
import { ExplorerContextService } from './explorer-context.service';

const STORAGE_KEY = 'atlas-explorer-context';

const REPOS: ApiRepositoryItem[] = [
  { id: 1, identity_key: 'key-alpha', root_handle: '/repos/alpha' },
  { id: 2, identity_key: 'key-beta', root_handle: '/repos/beta' },
];

const SNAPSHOTS: ApiSnapshotItem[] = [
  {
    id: 10,
    status: 'READY',
    commit_sha: 'abc123def456',
    git_ref: 'main',
    created_at: '2026-01-02T00:00:00Z',
  },
  {
    id: 9,
    status: 'READY',
    commit_sha: null,
    git_ref: null,
    created_at: '2026-01-01T00:00:00Z',
  },
];

const FAKE_NODE: ApiGraphNode = {
  id: 'occ:5',
  occurrence_id: 5,
  symbol_id: 3,
  qname: 'pkg.mod.func',
  kind: 'function',
  language: 'python',
  path: 'pkg/mod.py',
  span: { start: 1, end: 2 },
  parse_status: 'ok',
};

function flush(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve));
}

describe('ExplorerContextService', () => {
  let api: jasmine.SpyObj<ApiService>;
  let service: ExplorerContextService;

  beforeEach(() => {
    localStorage.clear();
    api = jasmine.createSpyObj<ApiService>('ApiService', [
      'listRepositories',
      'listSnapshots',
    ]);
    api.listRepositories.and.resolveTo({ items: REPOS, total: 2 });
    api.listSnapshots.and.resolveTo({ items: SNAPSHOTS });
    TestBed.configureTestingModule({
      providers: [{ provide: ApiService, useValue: api }],
    });
    service = TestBed.inject(ExplorerContextService);
  });

  it('init() loads repositories once and selects the first one', async () => {
    await service.init();
    await service.init(); // idempotent — no second fetch
    expect(api.listRepositories.calls.count()).toBe(1);
    expect(service.repositories().length).toBe(2);
    expect(service.repositoriesPhase()).toBe('ready');
    expect(service.activeRepository()?.id).toBe(1);
    expect(service.activeSnapshot()).toBeNull(); // null = server default
  });

  it('restores persisted repository identity and snapshot pin', async () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ repoIdentity: 'key-beta', snapshotId: 10 }),
    );
    await service.init();
    await flush();
    expect(service.activeRepository()?.id).toBe(2);
    expect(service.activeSnapshot()?.id).toBe(10);
  });

  it('falls back explicitly when the persisted repository is stale', async () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ repoIdentity: 'key-gone', snapshotId: null }),
    );
    await service.init();
    expect(service.activeRepository()?.id).toBe(1);
    expect(service.notice()).toContain('não está mais indexado');
    // persisted state is rewritten to the explicit fallback
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '{}') as {
      repoIdentity?: string;
    };
    expect(stored.repoIdentity).toBe('key-alpha');
  });

  it('clears the context honestly when no repository is indexed', async () => {
    api.listRepositories.and.resolveTo({ items: [], total: 0 });
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ repoIdentity: 'key-gone', snapshotId: 10 }),
    );
    await service.init();
    expect(service.activeRepository()).toBeNull();
    expect(service.notice()).toContain('nenhum repositório ativo');
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('reports offline state when the API is unreachable', async () => {
    api.listRepositories.and.rejectWith(
      new ApiUnreachableError(new TypeError('down')),
    );
    await service.init();
    expect(service.repositoriesPhase()).toBe('offline');
    expect(service.repositoriesError()).toBeTruthy();
    expect(service.activeRepository()).toBeNull();
  });

  it('clears selectedSymbol and snapshot pin when the repository changes', async () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ repoIdentity: 'key-alpha', snapshotId: 10 }),
    );
    await service.init();
    await flush();
    expect(service.activeSnapshot()?.id).toBe(10);

    service.selectSymbol(FAKE_NODE);
    service.selectRepository(REPOS[1]);

    expect(service.activeRepository()?.id).toBe(2);
    expect(service.selectedSymbol()).toBeNull();
    expect(service.activeSnapshot()).toBeNull();
    await flush();
    expect(api.listSnapshots).toHaveBeenCalledWith(2);
  });

  it('pins and unpins snapshots via selectSnapshot', async () => {
    await service.init();
    service.selectSnapshot(SNAPSHOTS[1]);
    expect(service.activeSnapshot()?.id).toBe(9);
    service.selectSnapshot(null);
    expect(service.activeSnapshot()).toBeNull();
  });

  it('resolves repositories by root_handle, identity_key and numeric id', async () => {
    await service.init();
    expect(service.resolveRepository('/repos/beta')?.id).toBe(2);
    expect(service.resolveRepository('key-alpha')?.id).toBe(1);
    expect(service.resolveRepository('2')?.id).toBe(2);
    expect(service.resolveRepository('nope')).toBeNull();
  });

  it('applies ?repo= and ?snapshot= deep-link params', async () => {
    await service.init();
    await service.syncFromQueryParams({ repo: '/repos/beta', snapshot: '10' });
    expect(service.activeRepository()?.id).toBe(2);
    expect(service.activeSnapshot()?.id).toBe(10);
    expect(service.notice()).toBe('');
  });

  it('keeps current context and notices on an unknown ?repo=', async () => {
    await service.init();
    await service.syncFromQueryParams({ repo: '/does/not/exist' });
    expect(service.activeRepository()?.id).toBe(1);
    expect(service.notice()).toContain('não está indexado');
  });

  it('notices on an invalid ?snapshot= without inventing one', async () => {
    await service.init();
    await service.syncFromQueryParams({ snapshot: 'abc' });
    expect(service.activeSnapshot()).toBeNull();
    expect(service.notice()).toContain('inválido');
  });

  it('notices when ?snapshot= does not exist in the repository', async () => {
    await service.init();
    await service.syncFromQueryParams({ snapshot: '999' });
    await flush();
    expect(service.activeSnapshot()).toBeNull();
    expect(service.notice()).toContain('#999');
  });

  it('builds query params and deep links carrying the context', async () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ repoIdentity: 'key-alpha', snapshotId: 10 }),
    );
    await service.init();
    await flush();
    expect(service.contextQueryParams()).toEqual({
      repo: '/repos/alpha',
      snapshot: 10,
    });
    const link = service.deepLink('/graph', { focus: 'occ:5' });
    expect(link.commands).toEqual(['/graph']);
    expect(link.queryParams).toEqual({
      repo: '/repos/alpha',
      snapshot: 10,
      focus: 'occ:5',
    });
  });
});
