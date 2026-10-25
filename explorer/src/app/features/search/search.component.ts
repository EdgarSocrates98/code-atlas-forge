import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import {
  ApiSearchParams,
  ApiSearchStrategy,
  ApiService,
} from '../../core/api/api.service';
import { apiErrorText, isApiOffline } from '../../shared/errors';
import { DiagnosticsListComponent } from '../../shared/components/diagnostics-list/diagnostics-list.component';
import { EvidenceBadgeComponent } from '../../shared/components/evidence-badge/evidence-badge.component';
import { BlockState, StateBlockComponent } from '../../shared/components/state-block/state-block.component';
import { PerfLogService } from '../diagnostics/perf-log.service';
import {
  SearchHistoryEntry,
  SearchHistoryStore,
  appendSearchPage,
  toSearchView,
} from './search.vm';
import type { SearchHitView, SearchView } from './search.vm';

type SearchPhase = 'idle' | 'loading' | 'loading-more' | 'ready' | 'error' | 'offline';

const STRATEGIES: { value: ApiSearchStrategy; label: string }[] = [
  { value: 'lexical', label: 'Léxica' },
  { value: 'structural', label: 'Estrutural' },
  { value: 'hybrid', label: 'Híbrida' },
  { value: 'vector', label: 'Vetorial' },
  { value: 'graph', label: 'Grafo' },
];

const KINDS = [
  'module',
  'class',
  'function',
  'method',
  'variable',
  'interface',
  'enum',
  'constant',
  'other',
];

const LIMITS = [10, 20, 50, 100];

@Component({
  selector: 'atlas-search',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    FormsModule,
    RouterLink,
    DiagnosticsListComponent,
    EvidenceBadgeComponent,
    StateBlockComponent,
  ],
  template: `
    <section class="search" aria-labelledby="search-title">
      <header class="search__header">
        <h1 id="search-title">Busca</h1>
        <p class="search__description">
          Busca léxica, estrutural, híbrida, vetorial e por grafo sobre o índice local.
        </p>
      </header>

      <form class="search__form" (ngSubmit)="submit()" aria-label="Critérios de busca">
        <div class="search__row search__row--query">
          <label class="search__field search__field--grow">
            <span>Consulta</span>
            <input
              type="search"
              name="q"
              [(ngModel)]="form.query"
              (ngModelChange)="syncUrlSoon()"
              maxlength="300"
              required
              autocomplete="off"
              placeholder="nome qualificado, trecho, caminho…"
            />
          </label>

          <label class="search__field">
            <span>Repositório</span>
            <select
              name="repo"
              [(ngModel)]="form.repositoryRoot"
              (ngModelChange)="syncUrlSoon()"
            >
              <option value="">Padrão (primeiro indexado)</option>
              @for (repo of repositories(); track repo.id) {
                <option [value]="repo.root_handle">{{ repo.root_handle }}</option>
              }
            </select>
          </label>
        </div>

        <div class="search__row">
          <label class="search__field">
            <span>Estratégia</span>
            <select
              name="strategy"
              [(ngModel)]="form.strategy"
              (ngModelChange)="syncUrlSoon()"
            >
              <option value="">Padrão (híbrida)</option>
              @for (s of strategies; track s.value) {
                <option [value]="s.value">{{ s.label }}</option>
              }
            </select>
          </label>

          <label class="search__field">
            <span>Linguagem</span>
            <input
              type="text"
              name="language"
              [(ngModel)]="form.language"
              (ngModelChange)="syncUrlSoon()"
              list="search-languages"
              autocomplete="off"
              placeholder="ex.: python"
            />
            <datalist id="search-languages">
              <option value="python"></option>
              <option value="typescript"></option>
              <option value="javascript"></option>
              <option value="go"></option>
              <option value="rust"></option>
              <option value="java"></option>
            </datalist>
          </label>

          <label class="search__field">
            <span>Kind</span>
            <select
              name="kind"
              [(ngModel)]="form.kind"
              (ngModelChange)="syncUrlSoon()"
            >
              <option value="">Todos</option>
              @for (k of kinds; track k) {
                <option [value]="k">{{ k }}</option>
              }
            </select>
          </label>

          <label class="search__field">
            <span>Caminho contém</span>
            <input
              type="text"
              name="path"
              [(ngModel)]="form.path"
              (ngModelChange)="syncUrlSoon()"
              autocomplete="off"
              placeholder="ex.: src/auth"
            />
          </label>

          <label class="search__field search__field--small">
            <span>Limite</span>
            <select
              name="limit"
              [(ngModel)]="form.limit"
              (ngModelChange)="syncUrlSoon()"
            >
              @for (l of limits; track l) {
                <option [ngValue]="l">{{ l }}</option>
              }
            </select>
          </label>
        </div>

        <div class="search__actions">
          <button type="submit" [disabled]="isBusy() || !form.query.trim()">
            {{ phase() === 'loading' ? 'Buscando…' : 'Buscar' }}
          </button>

          <label class="search__history-toggle">
            <input
              type="checkbox"
              name="historyEnabled"
              [checked]="historyEnabled()"
              (change)="toggleHistory($event)"
            />
            Guardar histórico de buscas neste navegador
          </label>
        </div>
      </form>

      @if (historyEnabled() && history().length > 0) {
        <section class="search__history" aria-labelledby="search-history-title">
          <div class="search__history-head">
            <h2 id="search-history-title">Histórico local</h2>
            <button type="button" class="search__link" (click)="clearHistory()">
              Limpar histórico
            </button>
          </div>
          <ul>
            @for (entry of history(); track entry.at) {
              <li>
                <button type="button" class="search__link" (click)="applyHistory(entry)">
                  {{ entry.query }}
                </button>
                <span class="search__history-meta">{{ describeEntry(entry) }}</span>
              </li>
            }
          </ul>
        </section>
      }

      @if (phase() === 'idle') {
        <p class="search__hint">Informe uma consulta para iniciar a busca.</p>
      }

      @if (showStates()) {
        <atlas-state-block
          [state]="blockState()"
          loadingMessage="Buscando no índice local…"
          emptyMessage="Nenhum resultado para esta combinação de consulta e filtros."
          [errorMessage]="errorText()"
        />
      }

      @if (result(); as res) {
        <div class="search__results" role="region" aria-live="polite" aria-label="Resultados da busca">
          <p class="search__summary" role="status">
            {{ items().length }} resultado(s) — estratégia “{{ res.strategy }}”,
            snapshot #{{ res.snapshotId }}@if (res.repository) {
              , repositório {{ res.repository }}
            }
            @if (res.truncated) {
              <strong class="search__warn">
                — resposta truncada pelo limite; refine a consulta ou carregue mais.
              </strong>
            }
          </p>

          @if (res.diagnostics.length > 0) {
            <atlas-diagnostics-list
              [items]="res.diagnostics"
              title="Diagnósticos da busca"
            />
          }

          <ol class="search__list">
            @for (hit of items(); track hit.occurrenceId) {
              <li class="search-hit">
                <div class="search-hit__head">
                  <code class="search-hit__qname">{{ hit.qualifiedName }}</code>
                  <span class="search-hit__actions">
                    <a
                      class="search-hit__action"
                      [routerLink]="['/symbols', hit.occurrenceId]"
                    >
                      Abrir no Inspector
                    </a>
                    <a
                      class="search-hit__action search-hit__graph"
                      [routerLink]="['/graph']"
                      [queryParams]="{ focus: hit.graphFocus }"
                    >
                      Focar no grafo
                    </a>
                  </span>
                </div>
                <div class="search-hit__meta">
                  <atlas-evidence-badge [level]="badgeLevel(hit)" />
                  <span class="search-hit__chip">{{ hit.kind }}</span>
                  <span class="search-hit__chip">{{ hit.language }}</span>
                  <span class="search-hit__chip">{{ hit.strategy }}</span>
                  <span class="search-hit__chip" title="Score de ranking">
                    score {{ hit.scoreText }}
                  </span>
                  @for (field of hit.matchedFields; track field) {
                    <span class="search-hit__chip search-hit__chip--match">
                      match: {{ field }}
                    </span>
                  }
                </div>
                <p class="search-hit__location">
                  <code>{{ hit.path }}</code>
                  @if (hit.spanText) {
                    <span>· trecho {{ hit.spanText }}</span>
                  }
                </p>
                @if (hit.evidence.length > 0) {
                  <ul
                    class="search-hit__evidence"
                    [attr.aria-label]="'Evidências de ' + hit.qualifiedName"
                  >
                    @for (ev of hit.evidence; track $index) {
                      <li>{{ ev }}</li>
                    }
                  </ul>
                }
              </li>
            }
          </ol>

          @if (res.nextCursor) {
            <button
              type="button"
              class="search__more"
              (click)="loadMore()"
              [disabled]="phase() === 'loading-more'"
            >
              {{ phase() === 'loading-more' ? 'Carregando…' : 'Carregar mais resultados' }}
            </button>
          }
        </div>
      }
    </section>
  `,
  styles: [
    `
      .search {
        padding: var(--atlas-space-5);
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-4);
      }

      .search__header h1 {
        margin: 0 0 var(--atlas-space-2);
        font-size: var(--atlas-font-size-xl);
      }

      .search__description {
        margin: 0;
        color: var(--atlas-color-text-muted);
      }

      .search__form {
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-3);
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-4);
        background: var(--atlas-color-bg-raised);
      }

      .search__row {
        display: flex;
        flex-wrap: wrap;
        gap: var(--atlas-space-3);
      }

      .search__field {
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-1);
        font-size: var(--atlas-font-size-sm);
        min-width: 10rem;
      }

      .search__field--grow {
        flex: 1;
      }

      .search__field--small {
        min-width: 6rem;
      }

      .search__field input,
      .search__field select {
        padding: var(--atlas-space-2);
        border: 1px solid var(--atlas-color-border-strong);
        border-radius: var(--atlas-radius-sm);
        background: var(--atlas-color-bg);
        color: var(--atlas-color-text);
        font: inherit;
      }

      .search__actions {
        display: flex;
        align-items: center;
        gap: var(--atlas-space-4);
        flex-wrap: wrap;
      }

      .search__actions button[type='submit'] {
        padding: var(--atlas-space-2) var(--atlas-space-5);
        border: 0;
        border-radius: var(--atlas-radius-sm);
        background: var(--atlas-color-accent);
        color: var(--atlas-color-accent-text);
        font-weight: var(--atlas-font-weight-medium);
        cursor: pointer;
      }

      .search__actions button[type='submit']:disabled {
        opacity: 0.6;
        cursor: not-allowed;
      }

      .search__history-toggle {
        display: inline-flex;
        align-items: center;
        gap: var(--atlas-space-2);
        font-size: var(--atlas-font-size-sm);
      }

      .search__history {
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-3) var(--atlas-space-4);
      }

      .search__history-head {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: var(--atlas-space-3);
      }

      .search__history h2 {
        margin: 0;
        font-size: var(--atlas-font-size-md);
      }

      .search__history ul {
        margin: var(--atlas-space-2) 0 0;
        padding: 0;
        list-style: none;
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-1);
      }

      .search__history-meta {
        color: var(--atlas-color-text-muted);
        font-size: var(--atlas-font-size-xs);
        margin-left: var(--atlas-space-2);
      }

      .search__link {
        background: none;
        border: 0;
        padding: 0;
        color: var(--atlas-color-accent);
        cursor: pointer;
        font: inherit;
        text-align: left;
      }

      .search__link:hover {
        text-decoration: underline;
      }

      .search__hint {
        color: var(--atlas-color-text-muted);
      }

      .search__summary {
        margin: 0;
      }

      .search__warn {
        color: var(--atlas-color-warning);
      }

      .search__list {
        margin: var(--atlas-space-3) 0 0;
        padding: 0;
        list-style: none;
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-3);
      }

      .search-hit {
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-3) var(--atlas-space-4);
        background: var(--atlas-color-bg-raised);
      }

      .search-hit__head {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        gap: var(--atlas-space-3);
        flex-wrap: wrap;
      }

      .search-hit__qname {
        font-family: var(--atlas-font-family-mono);
        font-weight: var(--atlas-font-weight-bold);
        overflow-wrap: anywhere;
      }

      .search-hit__actions {
        display: inline-flex;
        gap: var(--atlas-space-3);
        flex-wrap: wrap;
        font-size: var(--atlas-font-size-sm);
        white-space: nowrap;
      }

      .search-hit__action {
        color: var(--atlas-color-accent);
      }

      .search-hit__action:hover {
        text-decoration: underline;
      }

      .search-hit__meta {
        display: flex;
        flex-wrap: wrap;
        gap: var(--atlas-space-1);
        margin-top: var(--atlas-space-2);
      }

      .search-hit__chip {
        padding: 0 var(--atlas-space-2);
        border-radius: var(--atlas-radius-full);
        background: var(--atlas-color-bg-subtle);
        border: 1px solid var(--atlas-color-border);
        font-size: var(--atlas-font-size-xs);
        white-space: nowrap;
      }

      .search-hit__chip--match {
        border-color: var(--atlas-color-accent);
      }

      .search-hit__location {
        margin: var(--atlas-space-2) 0 0;
        font-size: var(--atlas-font-size-sm);
        color: var(--atlas-color-text-muted);
        overflow-wrap: anywhere;
      }

      .search-hit__evidence {
        margin: var(--atlas-space-2) 0 0;
        padding-left: var(--atlas-space-5);
        font-size: var(--atlas-font-size-sm);
        color: var(--atlas-color-text-muted);
      }

      .search__more {
        margin-top: var(--atlas-space-4);
        padding: var(--atlas-space-2) var(--atlas-space-4);
        border: 1px solid var(--atlas-color-border-strong);
        border-radius: var(--atlas-radius-sm);
        background: var(--atlas-color-bg-subtle);
        color: var(--atlas-color-text);
        cursor: pointer;
      }
    `,
  ],
})
export class SearchComponent implements OnInit, OnDestroy {
  readonly strategies = STRATEGIES;
  readonly kinds = KINDS;
  readonly limits = LIMITS;

  readonly form = {
    query: '',
    repositoryRoot: '',
    strategy: '' as ApiSearchStrategy | '',
    language: '',
    kind: '',
    path: '',
    limit: 20,
  };

  readonly phase = signal<SearchPhase>('idle');
  readonly result = signal<SearchView | null>(null);
  readonly items = signal<readonly SearchHitView[]>([]);
  readonly repositories = signal<{ id: number; root_handle: string }[]>([]);
  readonly errorText = signal('');
  readonly historyEnabled = signal(false);
  readonly history = signal<SearchHistoryEntry[]>([]);

  readonly isBusy = computed(() => this.phase() === 'loading' || this.phase() === 'loading-more');
  readonly showStates = computed(() => {
    const p = this.phase();
    if (p === 'error' || p === 'offline' || p === 'loading') return true;
    return p === 'ready' && this.items().length === 0;
  });
  readonly blockState = computed<BlockState>(() => {
    switch (this.phase()) {
      case 'loading':
      case 'loading-more':
        return 'loading';
      case 'offline':
        return 'offline';
      case 'error':
        return 'error';
      default:
        return 'empty';
    }
  });

  private readonly controller = new AbortController();
  private urlSyncTimer: ReturnType<typeof setTimeout> | null = null;
  /** Public so tests (and a future settings surface) can drive the opt-in. */
  readonly historyStore = new SearchHistoryStore(
    typeof localStorage === 'undefined' ? null : localStorage,
  );

  constructor(
    private readonly api: ApiService,
    private readonly perf: PerfLogService,
    private readonly router: Router,
    private readonly route: ActivatedRoute,
  ) {}

  ngOnInit(): void {
    this.restoreFromUrl();
    this.historyEnabled.set(this.historyStore.isEnabled());
    this.history.set(this.historyStore.list());
    void this.loadRepositories();
    // A `?q=` deep-link restores the executed search, not just the form.
    if (this.form.query.trim()) {
      void this.submit();
    }
  }

  ngOnDestroy(): void {
    if (this.urlSyncTimer !== null) {
      clearTimeout(this.urlSyncTimer);
      this.urlSyncTimer = null;
    }
    this.controller.abort();
  }

  /**
   * Debounced URL write for `ngModelChange` — coalesces keystrokes into a
   * single replaceUrl navigation so the address bar always mirrors the
   * current criteria without spamming history.
   */
  syncUrlSoon(): void {
    if (this.urlSyncTimer !== null) {
      clearTimeout(this.urlSyncTimer);
    }
    this.urlSyncTimer = setTimeout(() => {
      this.urlSyncTimer = null;
      this.syncUrl();
    }, 250);
  }

  async submit(): Promise<void> {
    const q = this.form.query.trim();
    if (!q) return;
    this.syncUrl();
    this.phase.set('loading');
    this.errorText.set('');
    this.result.set(null);
    this.items.set([]);
    try {
      const res = await this.perf.time('GET /search', () =>
        this.api.searchCode(this.buildParams(), { signal: this.controller.signal }),
      );
      const view = toSearchView(res);
      this.result.set(view);
      this.items.set(view.items);
      this.phase.set('ready');
      this.recordHistory(q);
    } catch (error) {
      this.handleError(error);
    }
  }

  async loadMore(): Promise<void> {
    const cursor = this.result()?.nextCursor;
    if (!cursor || this.isBusy()) return;
    this.phase.set('loading-more');
    try {
      const page = await this.perf.time('GET /search (página)', () =>
        this.api.searchCode(this.buildParams(cursor), {
          signal: this.controller.signal,
        }),
      );
      const view = toSearchView(page);
      this.items.set(appendSearchPage(this.items(), page));
      this.result.set({ ...view, items: this.items() });
      this.phase.set('ready');
    } catch (error) {
      this.handleError(error);
    }
  }

  toggleHistory(event: Event): void {
    const enabled = (event.target as HTMLInputElement).checked;
    this.historyStore.setEnabled(enabled);
    this.historyEnabled.set(enabled);
    this.history.set(this.historyStore.list());
  }

  clearHistory(): void {
    this.historyStore.clear();
    this.history.set([]);
  }

  applyHistory(entry: SearchHistoryEntry): void {
    this.form.query = entry.query;
    this.form.strategy = entry.strategy;
    this.form.language = entry.language;
    this.form.kind = entry.kind;
    this.form.path = entry.path;
    void this.submit();
  }

  describeEntry(entry: SearchHistoryEntry): string {
    const parts: string[] = [];
    if (entry.strategy) parts.push(`estratégia ${entry.strategy}`);
    if (entry.language) parts.push(entry.language);
    if (entry.kind) parts.push(entry.kind);
    if (entry.path) parts.push(`caminho ${entry.path}`);
    return parts.join(' · ');
  }

  /** Evidence badge level for a hit: hits carry evidence strings, not levels. */
  badgeLevel(hit: SearchHitView): 'supported' | 'unknown' {
    return hit.evidence.length > 0 ? 'supported' : 'unknown';
  }

  /** Writes the current criteria to the URL (replaceUrl — no history spam). */
  private syncUrl(): void {
    if (this.urlSyncTimer !== null) {
      clearTimeout(this.urlSyncTimer);
      this.urlSyncTimer = null;
    }
    const queryParams: Record<string, string | number> = {};
    const q = this.form.query.trim();
    if (q) queryParams['q'] = q;
    if (this.form.strategy) queryParams['strategy'] = this.form.strategy;
    const language = this.form.language.trim();
    if (language) queryParams['language'] = language;
    if (this.form.kind) queryParams['kind'] = this.form.kind;
    const path = this.form.path.trim();
    if (path) queryParams['path'] = path;
    if (this.form.repositoryRoot) queryParams['repo'] = this.form.repositoryRoot;
    if (this.form.limit !== 20) queryParams['limit'] = this.form.limit;
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams,
      replaceUrl: true,
    });
  }

  /** Restores query + filters from the URL (?q=&strategy=&…), validating enums. */
  private restoreFromUrl(): void {
    const qp = this.route.snapshot.queryParamMap;
    this.form.query = qp.get('q') ?? '';
    const strategy = qp.get('strategy');
    this.form.strategy = STRATEGIES.some((s) => s.value === strategy)
      ? (strategy as ApiSearchStrategy)
      : '';
    this.form.language = qp.get('language') ?? '';
    const kind = qp.get('kind');
    this.form.kind = kind !== null && KINDS.includes(kind) ? kind : '';
    this.form.path = qp.get('path') ?? '';
    const limit = Number(qp.get('limit'));
    this.form.limit = LIMITS.includes(limit) ? limit : 20;
    this.form.repositoryRoot = qp.get('repo') ?? '';
  }

  private buildParams(cursor?: string): ApiSearchParams {
    const params: ApiSearchParams = {
      q: this.form.query.trim(),
      limit: this.form.limit,
    };
    if (this.form.strategy) params.strategy = this.form.strategy;
    if (this.form.language.trim()) params.language = this.form.language.trim();
    if (this.form.kind) params.kind = this.form.kind;
    if (this.form.path.trim()) params.path = this.form.path.trim();
    if (this.form.repositoryRoot) params.repository_root = this.form.repositoryRoot;
    if (cursor) params.cursor = cursor;
    return params;
  }

  private recordHistory(query: string): void {
    if (!this.historyEnabled()) return;
    this.historyStore.add({
      query,
      strategy: this.form.strategy,
      language: this.form.language.trim(),
      kind: this.form.kind,
      path: this.form.path.trim(),
    });
    this.history.set(this.historyStore.list());
  }

  private async loadRepositories(): Promise<void> {
    try {
      const res = await this.api.listRepositories({ signal: this.controller.signal });
      this.repositories.set(res.items ?? []);
    } catch (error) {
      // Repo list is a filter convenience — only surface hard offline state.
      if (isApiOffline(error)) {
        this.phase.set('offline');
      }
    }
  }

  private handleError(error: unknown): void {
    if (isApiOffline(error)) {
      this.phase.set('offline');
      return;
    }
    this.errorText.set(apiErrorText(error));
    this.phase.set('error');
  }
}
