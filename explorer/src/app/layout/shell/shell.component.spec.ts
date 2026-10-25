import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { ApiService } from '../../core/api/api.service';
import type { ApiRepositoryItem, ApiSnapshotItem } from '../../core/api/api.service';
import { ExplorerContextService } from '../../core/state/explorer-context.service';
import { ShellComponent, buildBreadcrumbs } from './shell.component';

const REPOS: ApiRepositoryItem[] = [
  { id: 1, identity_key: 'key-alpha', root_handle: '/repos/alpha' },
  { id: 2, identity_key: 'key-beta', root_handle: '/repos/beta' },
];

const SNAPSHOTS: ApiSnapshotItem[] = [
  {
    id: 10,
    status: 'READY',
    commit_sha: 'abc123',
    git_ref: 'main',
    created_at: '2026-01-02T00:00:00Z',
  },
];

function stubApi(): jasmine.SpyObj<ApiService> {
  const api = jasmine.createSpyObj<ApiService>('ApiService', [
    'listRepositories',
    'listSnapshots',
  ]);
  api.listRepositories.and.resolveTo({ items: REPOS, total: 2 });
  api.listSnapshots.and.resolveTo({ items: SNAPSHOTS });
  return api;
}

describe('buildBreadcrumbs', () => {
  it('maps a section route to a single unlinked crumb', () => {
    expect(buildBreadcrumbs('/repositories')).toEqual([
      { label: 'Repositórios', path: null },
    ]);
  });

  it('strips query params and hashes before mapping', () => {
    expect(buildBreadcrumbs('/graph?repo=/x&snapshot=10#top')).toEqual([
      { label: 'Grafo', path: null },
    ]);
  });

  it('labels symbol detail routes and keeps unnavigable segments unlinked', () => {
    const crumbs = buildBreadcrumbs('/symbols/42');
    expect(crumbs.length).toBe(2);
    expect(crumbs[0].label).toBe('Símbolo');
    expect(crumbs[0].path).toBeNull(); // /symbols alone is not a route
    expect(crumbs[1]).toEqual({ label: 'ocorrência #42', path: null });
  });

  it('returns nothing for the root path', () => {
    expect(buildBreadcrumbs('/')).toEqual([]);
    expect(buildBreadcrumbs('')).toEqual([]);
  });
});

describe('ShellComponent', () => {
  let api: jasmine.SpyObj<ApiService>;

  async function create(): Promise<ComponentFixture<ShellComponent>> {
    api = stubApi();
    await TestBed.configureTestingModule({
      imports: [ShellComponent],
      providers: [provideRouter([]), { provide: ApiService, useValue: api }],
    }).compileComponents();
    const fixture = TestBed.createComponent(ShellComponent);
    fixture.detectChanges();
    await fixture.whenStable();
    fixture.detectChanges();
    return fixture;
  }

  beforeEach(() => {
    localStorage.clear();
  });

  it('should render all navigation items', async () => {
    const fixture = await create();
    const links = (fixture.nativeElement as HTMLElement).querySelectorAll(
      '.shell__sidebar a',
    );
    expect(links.length).toBe(9);
  });

  it('should toggle theme on button click', async () => {
    const fixture = await create();
    const component = fixture.componentInstance;
    const before = component.theme.resolvedTheme();
    component.toggleTheme();
    expect(component.theme.resolvedTheme()).not.toBe(before);
  });

  it('renders the repository selector bound to the explorer context', async () => {
    const fixture = await create();
    const ctx = TestBed.inject(ExplorerContextService);
    expect(ctx.activeRepository()?.id).toBe(1);

    const select = (fixture.nativeElement as HTMLElement).querySelector<
      HTMLSelectElement
    >('.shell__repo-select');
    expect(select).not.toBeNull();
    expect(select?.disabled).toBe(false);
    expect(select?.querySelectorAll('option').length).toBe(2);

    select!.value = '2';
    select!.dispatchEvent(new Event('change'));
    fixture.detectChanges();
    expect(ctx.activeRepository()?.id).toBe(2);
  });

  it('collapses the sidebar and persists the choice', async () => {
    const fixture = await create();
    const component = fixture.componentInstance;
    expect(component.sidebarCollapsed()).toBe(false);

    component.toggleSidebar();
    fixture.detectChanges();

    expect(component.sidebarCollapsed()).toBe(true);
    expect(localStorage.getItem('atlas-sidebar-collapsed')).toBe('1');
    const shell = (fixture.nativeElement as HTMLElement).querySelector('.shell');
    expect(shell?.classList.contains('shell--collapsed')).toBe(true);

    component.toggleSidebar();
    expect(localStorage.getItem('atlas-sidebar-collapsed')).toBe('0');
  });

  it('renders route-based breadcrumbs for the current url', async () => {
    const fixture = await create();
    const component = fixture.componentInstance;

    component.currentUrl.set('/diagnostics');
    fixture.detectChanges();
    let el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('.shell__breadcrumbs')?.textContent).toContain(
      'Diagnósticos',
    );
    // single crumb = current page, no link
    expect(
      el.querySelectorAll('.shell__breadcrumbs a').length,
    ).toBe(0);

    component.currentUrl.set('/symbols/42');
    fixture.detectChanges();
    el = fixture.nativeElement as HTMLElement;
    const text = el.querySelector('.shell__breadcrumbs')?.textContent ?? '';
    expect(text).toContain('Símbolo');
    expect(text).toContain('ocorrência #42');
  });
});
