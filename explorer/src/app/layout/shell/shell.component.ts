import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  computed,
  signal,
} from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import {
  NavigationEnd,
  Router,
  RouterLink,
  RouterLinkActive,
  RouterOutlet,
} from '@angular/router';
import { filter } from 'rxjs';
import { ThemeService } from '../../core/state/theme.service';
import { ExplorerContextService } from '../../core/state/explorer-context.service';

interface NavItem {
  path: string;
  label: string;
}

export interface BreadcrumbItem {
  label: string;
  /** Router path for a linked crumb; null for the current page (or dead end). */
  path: string | null;
}

const NAV_ITEMS: NavItem[] = [
  { path: '/dashboard', label: 'Painel' },
  { path: '/repositories', label: 'Repositórios' },
  { path: '/graph', label: 'Grafo' },
  { path: '/search', label: 'Busca' },
  { path: '/impact', label: 'Impacto' },
  { path: '/revisions', label: 'Revisões' },
  { path: '/context', label: 'Context Packs' },
  { path: '/lineage', label: 'Linhagem' },
  { path: '/contracts', label: 'Contratos' },
  { path: '/infra', label: 'Infraestrutura' },
  { path: '/pipelines', label: 'Pipelines' },
  { path: '/federation', label: 'Federação' },
  { path: '/diagnostics', label: 'Diagnósticos' },
  { path: '/settings', label: 'Configurações' },
];

const ROUTE_LABELS: Record<string, string> = {
  ...Object.fromEntries(
    NAV_ITEMS.map((item) => [item.path.replace(/^\//, ''), item.label]),
  ),
  symbols: 'Símbolo',
};

const SIDEBAR_STORAGE_KEY = 'atlas-sidebar-collapsed';

/**
 * Route-based breadcrumbs: one crumb per URL segment. Linked crumbs only
 * point to real routes from NAV_ITEMS — an intermediate segment without
 * its own route (e.g. `/symbols`) renders unlinked, never a dead href.
 */
export function buildBreadcrumbs(url: string): BreadcrumbItem[] {
  const path = url.split('?')[0].split('#')[0];
  const segments = path.split('/').filter(Boolean);
  const items: BreadcrumbItem[] = [];
  let acc = '';
  segments.forEach((segment, index) => {
    acc += `/${segment}`;
    const isLast = index === segments.length - 1;
    const previous = segments[index - 1] ?? '';
    const label =
      previous === 'symbols'
        ? `ocorrência #${segment}`
        : (ROUTE_LABELS[segment] ?? segment);
    const navigable = NAV_ITEMS.some((item) => item.path === acc);
    items.push({ label, path: isLast ? null : navigable ? acc : null });
  });
  return items;
}

function readSidebarCollapsed(): boolean {
  if (typeof localStorage === 'undefined') {
    return false;
  }
  try {
    return localStorage.getItem(SIDEBAR_STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

function persistSidebarCollapsed(collapsed: boolean): void {
  if (typeof localStorage === 'undefined') {
    return;
  }
  try {
    localStorage.setItem(SIDEBAR_STORAGE_KEY, collapsed ? '1' : '0');
  } catch {
    // Storage unavailable — collapse state stays in-memory only.
  }
}

@Component({
  selector: 'atlas-shell',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, RouterLinkActive, RouterOutlet],
  templateUrl: './shell.component.html',
  styleUrl: './shell.component.css',
})
export class ShellComponent implements OnInit {
  readonly navItems = NAV_ITEMS;
  readonly sidebarCollapsed = signal(readSidebarCollapsed());
  readonly currentUrl = signal('');
  readonly breadcrumbs = computed(() => buildBreadcrumbs(this.currentUrl()));

  constructor(
    readonly theme: ThemeService,
    readonly ctx: ExplorerContextService,
    private readonly router: Router,
  ) {
    this.router.events
      .pipe(
        filter((event): event is NavigationEnd => event instanceof NavigationEnd),
        takeUntilDestroyed(),
      )
      .subscribe((event) => {
        this.currentUrl.set(event.urlAfterRedirects);
        void this.ctx.syncFromQueryParams(this.queryContext());
      });
  }

  ngOnInit(): void {
    this.currentUrl.set(this.router.url);
    void this.bootstrapContext();
  }

  toggleTheme(): void {
    this.theme.toggle();
  }

  toggleSidebar(): void {
    const next = !this.sidebarCollapsed();
    this.sidebarCollapsed.set(next);
    persistSidebarCollapsed(next);
  }

  onRepositoryChange(event: Event): void {
    const raw = (event.target as HTMLSelectElement).value;
    const repo = this.ctx.repositories().find((r) => String(r.id) === raw);
    if (repo) {
      this.ctx.selectRepository(repo);
    }
  }

  private async bootstrapContext(): Promise<void> {
    await this.ctx.init();
    await this.ctx.syncFromQueryParams(this.queryContext());
  }

  private queryContext(): { repo: string | null; snapshot: string | null } {
    const params = this.router.routerState.snapshot.root.queryParamMap;
    return { repo: params.get('repo'), snapshot: params.get('snapshot') };
  }
}
