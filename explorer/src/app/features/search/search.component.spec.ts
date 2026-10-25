import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { ApiService } from '../../core/api/api.service';
import { ApiUnreachableError } from '../../core/api/api.errors';
import type { ApiSearchResponse } from '../../core/api/api.service';
import { SearchComponent } from './search.component';
import {
  SEARCH_HISTORY_ENABLED_KEY,
  SEARCH_HISTORY_KEY,
  SearchHistoryStore,
  appendSearchPage,
  toSearchView,
} from './search.vm';

function makeResponse(overrides: Partial<ApiSearchResponse> = {}): ApiSearchResponse {
  return {
    schema_version: 1,
    repository: '/repo',
    snapshot_id: 7,
    query: 'foo',
    strategy: 'hybrid',
    ranking_version: 'bm25-fts5-v1',
    items: [
      {
        occurrence_id: 11,
        qualified_name: 'pkg.mod.foo',
        kind: 'function',
        language: 'python',
        path: 'pkg/mod.py',
        span: { start: 10, end: 42 },
        score: 3.14159,
        strategy: 'hybrid',
        matched_fields: ['qname'],
        evidence: ['bm25:qname'],
      },
    ],
    limit: 20,
    truncated: false,
    next_cursor: 'cursor-abc',
    diagnostics: [],
    ...overrides,
  };
}

function makeStorage(): Storage {
  const map = new Map<string, string>();
  return {
    get length() {
      return map.size;
    },
    clear: () => map.clear(),
    getItem: (k: string) => map.get(k) ?? null,
    key: (i: number) => [...map.keys()][i] ?? null,
    removeItem: (k: string) => void map.delete(k),
    setItem: (k: string, v: string) => void map.set(k, String(v)),
  };
}

describe('search view-model', () => {
  it('maps hits to view-model with formatted span, score and graph focus', () => {
    const view = toSearchView(makeResponse());
    expect(view.items.length).toBe(1);
    const hit = view.items[0];
    expect(hit.occurrenceId).toBe(11);
    expect(hit.graphFocus).toBe('occ:11');
    expect(hit.spanText).toBe('10–42');
    expect(hit.scoreText).toBe('3.14');
    expect(hit.evidence).toEqual(['bm25:qname']);
    expect(view.nextCursor).toBe('cursor-abc');
  });

  it('handles null span and empty evidence', () => {
    const res = makeResponse();
    res.items = [{ ...res.items[0], span: null, evidence: [] }];
    const hit = toSearchView(res).items[0];
    expect(hit.spanText).toBeNull();
    expect(hit.evidence).toEqual([]);
  });

  it('appends cursor pages deduplicating by occurrence_id', () => {
    const first = toSearchView(makeResponse()).items;
    const page2 = makeResponse({
      items: [
        { ...makeResponse().items[0], occurrence_id: 11 }, // duplicate
        { ...makeResponse().items[0], occurrence_id: 22, qualified_name: 'b' },
      ],
    });
    const merged = appendSearchPage(first, page2);
    expect(merged.map((i) => i.occurrenceId)).toEqual([11, 22]);
  });
});

describe('SearchHistoryStore (opt-in)', () => {
  it('is disabled by default and never writes when disabled', () => {
    const storage = makeStorage();
    const store = new SearchHistoryStore(storage);
    expect(store.isEnabled()).toBe(false);
    store.add({ query: 'x', strategy: '', language: '', kind: '', path: '' });
    expect(store.list()).toEqual([]);
    expect(storage.getItem(SEARCH_HISTORY_KEY)).toBeNull();
  });

  it('persists and dedupes entries when enabled', () => {
    const storage = makeStorage();
    const store = new SearchHistoryStore(storage);
    store.setEnabled(true);
    store.add({ query: 'a', strategy: 'hybrid', language: '', kind: '', path: '' });
    store.add({ query: 'b', strategy: '', language: 'py', kind: '', path: '' });
    store.add({ query: 'a', strategy: 'hybrid', language: '', kind: '', path: '' });
    const list = store.list();
    expect(list.map((e) => e.query)).toEqual(['a', 'b']);
    expect(storage.getItem(SEARCH_HISTORY_ENABLED_KEY)).toBe('1');
  });

  it('wipes stored history when the user opts out', () => {
    const storage = makeStorage();
    const store = new SearchHistoryStore(storage);
    store.setEnabled(true);
    store.add({ query: 'a', strategy: '', language: '', kind: '', path: '' });
    store.setEnabled(false);
    expect(store.isEnabled()).toBe(false);
    expect(storage.getItem(SEARCH_HISTORY_KEY)).toBeNull();
  });

  it('is inert without storage (SSR/private mode)', () => {
    const store = new SearchHistoryStore(null);
    expect(store.isEnabled()).toBe(false);
    store.setEnabled(true);
    store.add({ query: 'a', strategy: '', language: '', kind: '', path: '' });
    expect(store.list()).toEqual([]);
  });
});

describe('SearchComponent', () => {
  let api: jasmine.SpyObj<ApiService>;

  beforeEach(async () => {
    localStorage.clear();
    api = jasmine.createSpyObj<ApiService>('ApiService', [
      'listRepositories',
      'searchCode',
    ]);
    api.listRepositories.and.resolveTo({
      items: [{ id: 1, identity_key: 'k', root_handle: '/repo' }],
      total: 1,
    });
    api.searchCode.and.resolveTo(makeResponse());

    await TestBed.configureTestingModule({
      imports: [SearchComponent],
      providers: [
        provideRouter([{ path: 'search', component: SearchComponent }]),
        { provide: ApiService, useValue: api },
      ],
    }).compileComponents();
  });

  it('runs a search, sends filters and renders hits', async () => {
    const fixture = TestBed.createComponent(SearchComponent);
    fixture.detectChanges();
    await fixture.whenStable();

    const component = fixture.componentInstance;
    component.form.query = 'foo';
    component.form.strategy = 'hybrid';
    component.form.language = 'python';
    await component.submit();
    fixture.detectChanges();

    expect(api.searchCode).toHaveBeenCalledWith(
      jasmine.objectContaining({
        q: 'foo',
        strategy: 'hybrid',
        language: 'python',
      }),
      jasmine.anything(),
    );
    expect(component.phase()).toBe('ready');
    expect(component.items().length).toBe(1);

    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('.search-hit__qname')?.textContent).toContain('pkg.mod.foo');
    const link = el.querySelector<HTMLAnchorElement>('.search-hit__graph');
    expect(link?.getAttribute('href')).toContain('/graph');
    expect(link?.getAttribute('href')).toMatch(/focus=occ(%3A|:)11/);
    const inspector = el.querySelector<HTMLAnchorElement>(
      '.search-hit__actions a[href*="symbols"]',
    );
    expect(inspector?.getAttribute('href')).toContain('/symbols/11');
    expect(inspector?.textContent).toContain('Abrir no Inspector');
  });

  it('restores query and filters from the URL and auto-runs', async () => {
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(
      '/search?q=foo&strategy=lexical&language=python&kind=function&path=src&limit=50&repo=/repo',
      SearchComponent,
    );
    await harness.fixture.whenStable();

    expect(api.searchCode).toHaveBeenCalledWith(
      jasmine.objectContaining({
        q: 'foo',
        strategy: 'lexical',
        language: 'python',
        kind: 'function',
        path: 'src',
        limit: 50,
        repository_root: '/repo',
      }),
      jasmine.anything(),
    );
  });

  it('writes the current criteria to the URL on submit', async () => {
    const harness = await RouterTestingHarness.create();
    const component = await harness.navigateByUrl('/search', SearchComponent);
    await harness.fixture.whenStable();

    component.form.query = 'bar';
    component.form.strategy = 'lexical';
    component.form.path = 'src/auth';
    await component.submit();
    await harness.fixture.whenStable();

    const url = decodeURIComponent(TestBed.inject(Router).url);
    expect(url).toContain('/search');
    expect(url).toContain('q=bar');
    expect(url).toContain('strategy=lexical');
    expect(url).toContain('path=src/auth');
  });

  it('restores but does not auto-run when the URL carries filters without q', async () => {
    const harness = await RouterTestingHarness.create();
    const component = await harness.navigateByUrl(
      '/search?strategy=vector&language=rust',
      SearchComponent,
    );
    await harness.fixture.whenStable();

    expect(component.form.strategy).toBe('vector');
    expect(component.form.language).toBe('rust');
    expect(api.searchCode).not.toHaveBeenCalled();
  });

  it('paginates via next_cursor and appends results', async () => {
    const fixture = TestBed.createComponent(SearchComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    component.form.query = 'foo';
    await component.submit();

    api.searchCode.and.resolveTo(
      makeResponse({
        items: [
          { ...makeResponse().items[0], occurrence_id: 22, qualified_name: 'other' },
        ],
        next_cursor: null,
      }),
    );
    await component.loadMore();

    const calls = api.searchCode.calls.allArgs();
    expect(calls[1][0].cursor).toBe('cursor-abc');
    expect(component.items().map((i) => i.occurrenceId)).toEqual([11, 22]);
    expect(component.result()?.nextCursor).toBeNull();
  });

  it('does not write history unless the user opted in', async () => {
    const fixture = TestBed.createComponent(SearchComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    component.form.query = 'foo';
    await component.submit();
    expect(localStorage.getItem(SEARCH_HISTORY_KEY)).toBeNull();

    component.historyStore.setEnabled(true);
    component.historyEnabled.set(true);
    await component.submit();
    expect(localStorage.getItem(SEARCH_HISTORY_KEY)).not.toBeNull();
    expect(component.history().length).toBe(1);
  });

  it('surfaces api-disconnected state on unreachable API', async () => {
    api.searchCode.and.rejectWith(new ApiUnreachableError(new TypeError('down')));
    const fixture = TestBed.createComponent(SearchComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    const component = fixture.componentInstance;

    component.form.query = 'foo';
    await component.submit();
    fixture.detectChanges();

    expect(component.phase()).toBe('offline');
    const el = fixture.nativeElement as HTMLElement;
    expect(el.textContent).toContain('API local indisponível');
  });
});
