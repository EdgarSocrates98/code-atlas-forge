import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  OnInit,
  computed,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import {
  ApiImpactRequest,
  ApiRepositoryItem,
  ApiService,
} from '../../core/api/api.service';
import { apiErrorText, isApiOffline } from '../../shared/errors';
import { StateBlockComponent } from '../../shared/components/state-block/state-block.component';
import { PerfLogService } from '../diagnostics/perf-log.service';
import { ImpactResultsComponent } from './impact-results.component';
import { ImpactView, toImpactView } from './impact.vm';

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

  constructor(
    private readonly api: ApiService,
    private readonly perf: PerfLogService,
  ) {}

  ngOnInit(): void {
    void this.loadRepositories();
  }

  ngOnDestroy(): void {
    this.controller.abort();
  }

  async analyze(): Promise<void> {
    if (!this.form.repositoryRoot) return;
    this.phase.set('loading');
    this.errorText.set('');
    this.result.set(null);
    try {
      const res = await this.perf.time('POST /impact/analyze', () =>
        this.api.analyzeImpact(this.buildRequest(), {
          signal: this.controller.signal,
        }),
      );
      this.result.set(toImpactView(res));
      this.phase.set('ready');
    } catch (error) {
      this.handleError(error);
    }
  }

  private buildRequest(): ApiImpactRequest {
    const req: ApiImpactRequest = {
      repository_root: this.form.repositoryRoot,
      include_possible: this.form.includePossible,
      known_only: this.form.knownOnly,
    };
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

  private async loadRepositories(): Promise<void> {
    try {
      const res = await this.perf.time('GET /repositories', () =>
        this.api.listRepositories({ signal: this.controller.signal }),
      );
      this.repositories.set(res.items ?? []);
      if (res.items?.length === 1) {
        this.form.repositoryRoot = res.items[0].root_handle;
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
