import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  OnInit,
  computed,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
import {
  ApiImpactRequest,
  ApiImpactResult,
  ApiRepositoryItem,
  ApiService,
} from '../../core/api/api.service';
import { apiErrorText, isApiOffline } from '../../shared/errors';
import { StateBlockComponent } from '../../shared/components/state-block/state-block.component';
import { PerfLogService } from '../diagnostics/perf-log.service';
import { ImpactResultsComponent } from './impact-results.component';
import { ImpactView, impactToMarkdown, toImpactView } from './impact.vm';

type ImpactPhase = 'idle' | 'loading' | 'ready' | 'error' | 'offline';

@Component({
  selector: 'atlas-impact',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, StateBlockComponent, ImpactResultsComponent],
  template: `
    <section class="impact" aria-labelledby="impact-title">
      <header class="impact__header">
        <h1 id="impact-title">Análise de impacto</h1>
        <p class="impact__description">
          Compara duas revisões e estima o raio de impacto com evidência —
          nunca afirma cobertura que o índice não provou.
        </p>
      </header>

      <form
        class="impact__form"
        (ngSubmit)="analyze()"
        aria-label="Parâmetros da análise de impacto"
      >
        <div class="impact__row">
          <label class="impact__field impact__field--grow">
            <span>Repositório</span>
            <select
              name="repo"
              [(ngModel)]="form.repositoryRoot"
              required
              aria-required="true"
            >
              <option value="" disabled>Selecione…</option>
              @for (repo of repositories(); track repo.id) {
                <option [value]="repo.root_handle">{{ repo.root_handle }}</option>
              }
            </select>
          </label>

          <label class="impact__field">
            <span>Revisão base</span>
            <input
              type="text"
              name="base"
              [(ngModel)]="form.baseRevision"
              autocomplete="off"
              placeholder="HEAD~1, sha ou vazio"
            />
          </label>

          <label class="impact__field">
            <span>Revisão alvo</span>
            <input
              type="text"
              name="target"
              [(ngModel)]="form.targetRevision"
              autocomplete="off"
              placeholder="vazio = worktree atual"
            />
          </label>
        </div>

        <div class="impact__row">
          <label class="impact__field">
            <span>Símbolo (opcional)</span>
            <input
              type="text"
              name="symbol"
              [(ngModel)]="form.symbol"
              autocomplete="off"
              placeholder="filtra por nome qualificado"
            />
          </label>

          <label class="impact__field">
            <span>Arquivo (opcional)</span>
            <input
              type="text"
              name="file"
              [(ngModel)]="form.file"
              autocomplete="off"
              placeholder="caminho relativo"
            />
          </label>
        </div>

        <div class="impact__row impact__row--flags">
          <label class="impact__check">
            <input
              type="checkbox"
              name="includePossible"
              [(ngModel)]="form.includePossible"
            />
            Incluir candidatos possíveis (não verificados)
          </label>
          <label class="impact__check">
            <input type="checkbox" name="knownOnly" [(ngModel)]="form.knownOnly" />
            Somente conhecidos (restringe a verificados)
          </label>
        </div>

        <div class="impact__actions">
          <button
            type="submit"
            [disabled]="phase() === 'loading' || !form.repositoryRoot"
          >
            {{ phase() === 'loading' ? 'Analisando…' : 'Analisar impacto' }}
          </button>
        </div>

        @if (baseSnapshotId() !== null || targetSnapshotId() !== null) {
          <p class="impact__snapshots" role="status">
            Snapshots fixados pela URL — base #{{
              baseSnapshotId() ?? '—'
            }}
            · alvo #{{ targetSnapshotId() ?? '—' }}.
            <button
              type="button"
              class="impact__link"
              (click)="clearUrlSnapshots()"
            >
              Ignorar snapshots da URL
            </button>
          </p>
        }
      </form>

      @if (phase() === 'idle') {
        <p class="impact__hint">
          Escolha o repositório e as revisões para iniciar a análise.
        </p>
      }

      @if (showBlock()) {
        <atlas-state-block
          [state]="blockState()"
          loadingMessage="Analisando impacto entre as revisões…"
          emptyMessage="A análise concluiu sem achados para exibir."
          [errorMessage]="errorText()"
        />
      }

      @if (result(); as view) {
        <atlas-impact-results [view]="view" />
        <div class="impact__export">
          <button type="button" (click)="exportJson()">Exportar JSON</button>
          <button type="button" (click)="exportMarkdown()">
            Exportar Markdown
          </button>
        </div>
      }
    </section>
  `,
  styles: [
    `
      .impact {
        padding: var(--atlas-space-5);
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-4);
      }

      .impact__header h1 {
        margin: 0 0 var(--atlas-space-2);
        font-size: var(--atlas-font-size-xl);
      }

      .impact__description {
        margin: 0;
        color: var(--atlas-color-text-muted);
        max-width: 72ch;
      }

      .impact__form {
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-3);
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-4);
        background: var(--atlas-color-bg-raised);
      }

      .impact__row {
        display: flex;
        flex-wrap: wrap;
        gap: var(--atlas-space-3);
      }

      .impact__row--flags {
        gap: var(--atlas-space-5);
      }

      .impact__field {
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-1);
        font-size: var(--atlas-font-size-sm);
        min-width: 14rem;
      }

      .impact__field--grow {
        flex: 1;
      }

      .impact__field input,
      .impact__field select {
        padding: var(--atlas-space-2);
        border: 1px solid var(--atlas-color-border-strong);
        border-radius: var(--atlas-radius-sm);
        background: var(--atlas-color-bg);
        color: var(--atlas-color-text);
        font: inherit;
      }

      .impact__check {
        display: inline-flex;
        align-items: center;
        gap: var(--atlas-space-2);
        font-size: var(--atlas-font-size-sm);
      }

      .impact__actions button {
        padding: var(--atlas-space-2) var(--atlas-space-5);
        border: 0;
        border-radius: var(--atlas-radius-sm);
        background: var(--atlas-color-accent);
        color: var(--atlas-color-accent-text);
        font-weight: var(--atlas-font-weight-medium);
        cursor: pointer;
      }

      .impact__actions button:disabled {
        opacity: 0.6;
        cursor: not-allowed;
      }

      .impact__snapshots {
        margin: 0;
        font-size: var(--atlas-font-size-sm);
        color: var(--atlas-color-text-muted);
      }

      .impact__link {
        background: none;
        border: 0;
        padding: 0;
        color: var(--atlas-color-accent);
        cursor: pointer;
        font: inherit;
      }

      .impact__link:hover {
        text-decoration: underline;
      }

      .impact__export {
        display: flex;
        gap: var(--atlas-space-2);
        flex-wrap: wrap;
      }

      .impact__export button {
        padding: var(--atlas-space-2) var(--atlas-space-4);
        border-radius: var(--atlas-radius-sm);
        border: 1px solid var(--atlas-color-border-strong);
        background: var(--atlas-color-bg-subtle);
        color: var(--atlas-color-text);
        cursor: pointer;
      }

      .impact__hint {
        color: var(--atlas-color-text-muted);
      }
    `,
  ],
})
export class ImpactComponent implements OnInit, OnDestroy {
  readonly form = {
    repositoryRoot: '',
    baseRevision: '',
    targetRevision: '',
    symbol: '',
    file: '',
    includePossible: true,
    knownOnly: false,
  };

  readonly phase = signal<ImpactPhase>('idle');
  readonly result = signal<ImpactView | null>(null);
  readonly repositories = signal<ApiRepositoryItem[]>([]);
  readonly errorText = signal('');
  /** Snapshot ids pinned via URL (?base_snapshot=&target_snapshot=). */
  readonly baseSnapshotId = signal<number | null>(null);
  readonly targetSnapshotId = signal<number | null>(null);

  readonly showBlock = computed(() => {
    const p = this.phase();
    if (p === 'loading' || p === 'error' || p === 'offline') return true;
    const view = this.result();
    return (
      p === 'ready' &&
      view !== null &&
      view.totalChanges === 0 &&
      view.totalImpacts === 0 &&
      view.totalTests === 0 &&
      view.diagnosticsTotal.length === 0 &&
      !view.semanticBlocked
    );
  });
  readonly blockState = computed(() => {
    switch (this.phase()) {
      case 'loading':
        return 'loading' as const;
      case 'offline':
        return 'offline' as const;
      case 'error':
        return 'error' as const;
      default:
        return 'empty' as const;
    }
  });

  private readonly controller = new AbortController();
  /** Raw response retained client-side for JSON/Markdown export. */
  private lastResult: ApiImpactResult | null = null;
  /** Set by restoreFromUrl — auto-runs once repositories finish loading. */
  private autoAnalyzePending = false;

  constructor(
    private readonly api: ApiService,
    private readonly perf: PerfLogService,
    private readonly router: Router,
    private readonly route: ActivatedRoute,
  ) {}

  ngOnInit(): void {
    this.restoreFromUrl();
    void this.loadRepositories();
  }

  ngOnDestroy(): void {
    this.controller.abort();
  }

  async analyze(): Promise<void> {
    if (!this.form.repositoryRoot) return;
    this.syncUrl();
    this.phase.set('loading');
    this.errorText.set('');
    this.result.set(null);
    this.lastResult = null;
    try {
      const res = await this.perf.time('POST /impact/analyze', () =>
        this.api.analyzeImpact(this.buildRequest(), {
          signal: this.controller.signal,
        }),
      );
      this.lastResult = res;
      this.result.set(toImpactView(res));
      this.phase.set('ready');
    } catch (error) {
      this.handleError(error);
    }
  }

  /** Drops the URL-pinned snapshot ids and re-syncs the address bar. */
  clearUrlSnapshots(): void {
    this.baseSnapshotId.set(null);
    this.targetSnapshotId.set(null);
    this.syncUrl();
  }

  exportJson(): void {
    if (!this.lastResult) return;
    this.download(
      'impact-analysis.json',
      JSON.stringify(this.lastResult, null, 2),
      'application/json',
    );
  }

  exportMarkdown(): void {
    if (!this.lastResult) return;
    this.download(
      'impact-analysis.md',
      impactToMarkdown(this.lastResult),
      'text/markdown',
    );
  }

  private download(filename: string, text: string, type: string): void {
    if (typeof document === 'undefined') return;
    const blob = new Blob([text], { type });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = filename;
    anchor.click();
    URL.revokeObjectURL(url);
  }

  private buildRequest(): ApiImpactRequest {
    const req: ApiImpactRequest = {
      repository_root: this.form.repositoryRoot,
      include_possible: this.form.includePossible,
      known_only: this.form.knownOnly,
    };
    const baseSnap = this.baseSnapshotId();
    if (baseSnap !== null) req.base_snapshot_id = baseSnap;
    const targetSnap = this.targetSnapshotId();
    if (targetSnap !== null) req.target_snapshot_id = targetSnap;
    if (this.form.baseRevision.trim()) {
      req.base_revision = this.form.baseRevision.trim();
    }
    if (this.form.targetRevision.trim()) {
      req.target_revision = this.form.targetRevision.trim();
    }
    if (this.form.symbol.trim()) req.symbol = this.form.symbol.trim();
    if (this.form.file.trim()) req.file = this.form.file.trim();
    return req;
  }

  /** Writes the current parameters to the URL (replaceUrl — no history spam). */
  private syncUrl(): void {
    const queryParams: Record<string, string | number> = {};
    if (this.form.repositoryRoot) {
      queryParams['repo'] = this.form.repositoryRoot;
    }
    const base = this.form.baseRevision.trim();
    if (base) queryParams['base'] = base;
    const target = this.form.targetRevision.trim();
    if (target) queryParams['target'] = target;
    const symbol = this.form.symbol.trim();
    if (symbol) queryParams['symbol'] = symbol;
    const file = this.form.file.trim();
    if (file) queryParams['file'] = file;
    const baseSnap = this.baseSnapshotId();
    if (baseSnap !== null) queryParams['base_snapshot'] = baseSnap;
    const targetSnap = this.targetSnapshotId();
    if (targetSnap !== null) queryParams['target_snapshot'] = targetSnap;
    if (!this.form.includePossible) queryParams['possible'] = '0';
    if (this.form.knownOnly) queryParams['known'] = '1';
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams,
      replaceUrl: true,
    });
  }

  /**
   * Restores the analysis parameters from the URL
   * (?repo=&base=&target=&base_snapshot=&target_snapshot=&symbol=&file=&possible=&known=).
   * A `repo` param marks the URL as a restore intent and re-runs the
   * analysis once the repository list confirms the form is usable.
   */
  private restoreFromUrl(): void {
    const qp = this.route.snapshot.queryParamMap;
    this.form.repositoryRoot = qp.get('repo') ?? '';
    this.form.baseRevision = qp.get('base') ?? '';
    this.form.targetRevision = qp.get('target') ?? '';
    this.form.symbol = qp.get('symbol') ?? '';
    this.form.file = qp.get('file') ?? '';
    const possible = qp.get('possible');
    if (possible !== null) {
      this.form.includePossible = possible !== '0' && possible !== 'false';
    }
    const known = qp.get('known');
    if (known !== null) {
      this.form.knownOnly = known === '1' || known === 'true';
    }
    this.baseSnapshotId.set(parseSnapshotParam(qp.get('base_snapshot')));
    this.targetSnapshotId.set(parseSnapshotParam(qp.get('target_snapshot')));
    this.autoAnalyzePending = this.form.repositoryRoot !== '';
  }

  private async loadRepositories(): Promise<void> {
    try {
      const res = await this.perf.time('GET /repositories', () =>
        this.api.listRepositories({ signal: this.controller.signal }),
      );
      this.repositories.set(res.items ?? []);
      if (res.items?.length === 1 && !this.form.repositoryRoot) {
        this.form.repositoryRoot = res.items[0].root_handle;
      }
      if (this.autoAnalyzePending) {
        this.autoAnalyzePending = false;
        void this.analyze();
      }
    } catch (error) {
      if (isApiOffline(error)) {
        this.phase.set('offline');
      } else {
        this.errorText.set(apiErrorText(error));
        this.phase.set('error');
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

/** Positive integer snapshot id from a URL param; null when absent/invalid. */
function parseSnapshotParam(raw: string | null): number | null {
  if (raw === null) return null;
  const value = Number(raw);
  return Number.isInteger(value) && value > 0 ? value : null;
}
