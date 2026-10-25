import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { ApiService } from '../../core/api/api.service';
import type {
  ApiIndexStatusItem,
  ApiRepositoryItem,
  ApiSnapshotItem,
} from '../../core/api/api.service';
import { ApiHttpError, ApiUnreachableError } from '../../core/api/api.errors';
import { ExplorerContextService } from '../../core/state/explorer-context.service';
import { RepositoriesComponent } from './repositories.component';
import {
  buildRepositoryRows,
  findLatestReady,
  shortSha,
} from './repositories.vm';

const REPOS: ApiRepositoryItem[] = [
  { id: 1, identity_key: 'key-alpha', root_handle: '/repos/alpha' },
  { id: 2, identity_key: 'key-beta', root_handle: '/repos/beta' },
];

const INDEX_STATUS: ApiIndexStatusItem[] = [
  {
    repository_id: 1,
    root_handle: '/repos/alpha',
    ready_snapshots: 2,
    latest_ready: 10,
    counts: { file_inventory: 12, symbol_occurrences: 34, relations: 56 },
  },
  {
    repository_id: 2,
    root_handle: '/repos/beta',
    ready_snapshots: 1,
    latest_ready: 5,
    counts: {},
  },
];

const SNAPSHOTS_ALPHA: ApiSnapshotItem[] = [
  {
    id: 11,
    status: 'INDEXING',
    commit_sha: null,
    git_ref: null,
    created_at: '2026-01-03T00:00:00Z',
  },
  {
    id: 10,
    status: 'READY',
    commit_sha: 'abc123def4567890',
    git_ref: 'main',
    created_at: '2026-01-02T00:00:00Z',
  },
];

function stubApi(
  overrides: {
    repos?: ApiRepositoryItem[];
    status?: ApiIndexStatusItem[];
    snapshotsByRepo?: Record<number, ApiSnapshotItem[] | Error>;
  } = {},
): jasmine.SpyObj<ApiService> {
  const api = jasmine.createSpyObj<ApiService>('ApiService', [
    'listRepositories',
    'getIndexStatus',
    'listSnapshots',
  ]);
  api.listRepositories.and.resolveTo({
    items: overrides.repos ?? REPOS,
    total: (overrides.repos ?? REPOS).length,
  });
  api.getIndexStatus.and.resolveTo({ items: overrides.status ?? INDEX_STATUS });
  const snapshots = overrides.snapshotsByRepo ?? { 1: SNAPSHOTS_ALPHA, 2: [] };
  api.listSnapshots.and.callFake((id: number) => {
    const value = snapshots[id];
    if (value instanceof Error) {
      return Promise.reject(value);
    }
    return Promise.resolve({ items: value ?? [] });
  });
  return api;
}

async function createComponent(
  api: jasmine.SpyObj<ApiService>,
): Promise<ComponentFixture<RepositoriesComponent>> {
  await TestBed.configureTestingModule({
    imports: [RepositoriesComponent],
    providers: [provideRouter([]), { provide: ApiService, useValue: api }],
  }).compileComponents();
  const fixture = TestBed.createComponent(RepositoriesComponent);
  fixture.detectChanges();
  await fixture.whenStable();
  fixture.detectChanges();
  return fixture;
}

describe('repositories view-model', () => {
  it('findLatestReady scans for READY even when a newer snapshot is not ready', () => {
    expect(findLatestReady(SNAPSHOTS_ALPHA)?.id).toBe(10);
    expect(findLatestReady([])).toBeNull();
  });

  it('buildRepositoryRows joins repos, index status and snapshot detail honestly', () => {
    const rows = buildRepositoryRows(
      REPOS,
      INDEX_STATUS,
      new Map([[1, SNAPSHOTS_ALPHA]]),
    );
    expect(rows.length).toBe(2);
    expect(rows[0].symbols).toBe(34);
    expect(rows[0].latestReady?.id).toBe(10);
    // repo 2 has status counts but no snapshot detail fetched
    expect(rows[1].latestReadyId).toBe(5);
    expect(rows[1].latestReady).toBeNull();
    expect(rows[1].files).toBeNull();
  });

  it('shortSha truncates commit hashes', () => {
    expect(shortSha('abc123def4567890')).toBe('abc123def456');
    expect(shortSha(null)).toBe('');
  });
});

describe('RepositoriesComponent', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('renders repository cards with real counts and latest READY snapshot', async () => {
    const fixture = await createComponent(stubApi());
    const el = fixture.nativeElement as HTMLElement;
    const cards = el.querySelectorAll('.repo-card');
    expect(cards.length).toBe(2);

    const first = cards[0].textContent ?? '';
    expect(first).toContain('/repos/alpha');
    expect(first).toContain('key-alpha');
    expect(first).toContain('#10'); // READY snapshot, not the INDEXING #11
    expect(first).toContain('2026-01-02');
    expect(first).toContain('abc123def456');
    expect(first).toContain('34');
    expect(first).toContain('56');

    const second = cards[1].textContent ?? '';
    expect(second).toContain('/repos/beta');
    expect(second).toContain('#5'); // latest_ready from index/status
    expect(second).toContain('—'); // honest missing counts
  });

  it('marks the selected repository as active context', async () => {
    const fixture = await createComponent(stubApi());
    const component = fixture.componentInstance;
    const ctx = TestBed.inject(ExplorerContextService);
    expect(ctx.activeRepository()).toBeNull();

    component.select(component.rows()[0]);
    fixture.detectChanges();

    expect(ctx.activeRepository()?.id).toBe(1);
    expect(ctx.activeSnapshot()?.id).toBe(10); // pinned to the shown READY snapshot
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('.repo-card--active')).not.toBeNull();
    expect(el.textContent).toContain('contexto ativo');
  });

  it('builds deep links carrying repo and snapshot params', async () => {
    const fixture = await createComponent(stubApi());
    const component = fixture.componentInstance;
    expect(component.graphParams(component.rows()[0])).toEqual({
      repo: '/repos/alpha',
      snapshot: 10,
    });
    // repo 2 has no snapshot detail fetched, but index/status reports latest_ready
    expect(component.graphParams(component.rows()[1])).toEqual({
      repo: '/repos/beta',
      snapshot: 5,
    });
    expect(component.searchParams(component.rows()[0])).toEqual({
      repo: '/repos/alpha',
    });

    const link = (fixture.nativeElement as HTMLElement).querySelector<
      HTMLAnchorElement
    >('.repo-card__actions a');
    expect(link?.getAttribute('href')).toContain('/graph');
    expect(link?.getAttribute('href')).toContain('repo=');
  });

  it('shows the honest empty state with index instructions', async () => {
    const api = stubApi({ repos: [], status: [] });
    const fixture = await createComponent(api);
    const el = fixture.nativeElement as HTMLElement;
    expect(fixture.componentInstance.phase()).toBe('ready');
    expect(el.textContent).toContain('code-atlas index');
    expect(el.textContent).not.toContain('code-atlas serve');
  });

  it('surfaces the offline state when the API is unreachable', async () => {
    const api = stubApi();
    api.listRepositories.and.rejectWith(
      new ApiUnreachableError(new TypeError('down')),
    );
    const fixture = await createComponent(api);
    const el = fixture.nativeElement as HTMLElement;
    expect(fixture.componentInstance.phase()).toBe('offline');
    expect(el.textContent).toContain('API local indisponível');
  });

  it('surfaces the error state on HTTP failures', async () => {
    const api = stubApi();
    api.getIndexStatus.and.rejectWith(new ApiHttpError(500, 'Server Error', null));
    const fixture = await createComponent(api);
    expect(fixture.componentInstance.phase()).toBe('error');
    expect((fixture.nativeElement as HTMLElement).textContent).toContain(
      'API respondeu 500',
    );
  });

  it('keeps a row honest when its snapshot detail fetch fails', async () => {
    const api = stubApi({
      snapshotsByRepo: {
        1: SNAPSHOTS_ALPHA,
        2: new ApiUnreachableError(new TypeError('down')),
      },
    });
    const fixture = await createComponent(api);
    const el = fixture.nativeElement as HTMLElement;
    const cards = el.querySelectorAll('.repo-card');
    expect(cards.length).toBe(2);
    // repo 2 still shows latest_ready id from index/status, no invented detail
    expect(cards[1].textContent).toContain('#5');
    expect(fixture.componentInstance.phase()).toBe('ready');
  });
});
