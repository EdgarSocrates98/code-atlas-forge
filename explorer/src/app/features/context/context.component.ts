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
  ApiContextLevel,
  ApiContextPack,
  ApiContextRequest,
  ApiRepositoryItem,
  ApiService,
} from '../../core/api/api.service';
import { apiErrorText, isApiOffline } from '../../shared/errors';
import { StateBlockComponent } from '../../shared/components/state-block/state-block.component';
import { PerfLogService } from '../diagnostics/perf-log.service';
import { PackView, packToMarkdown, toPackView } from './context.vm';

type ContextPhase = 'idle' | 'loading' | 'ready' | 'error' | 'offline';

const LEVELS: { value: ApiContextLevel; label: string; hint: string }[] = [
  { value: 'orientation', label: 'Orientação', hint: 'visão macro, menos itens' },
  { value: 'focus', label: 'Foco', hint: 'recorte direto da tarefa' },
  { value: 'deep', label: 'Profundo', hint: 'máximo de contexto no orçamento' },
];

@Component({
  selector: 'atlas-context',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, StateBlockComponent],
  template: `
    <section class="context" aria-labelledby="context-title">
      <header class="context__header">
        <h1 id="context-title">Context Packs</h1>
        <p class="context__description">
          Gera um pacote de contexto determinístico para uma tarefa. O pack fica
          em memória nesta página — nada é enviado a serviços externos.
        </p>
      </header>

      <form
        class="context__form"
        (ngSubmit)="build()"
        aria-label="Parâmetros do context pack"
      >
        <label class="context__field">
          <span>Tarefa</span>
          <textarea
            name="task"
            [(ngModel)]="form.task"
            rows="3"
            maxlength="2000"
            required
            aria-required="true"
            placeholder="descreva a tarefa — ex.: corrigir timeout no retry do cliente HTTP"
          ></textarea>
        </label>

        <div class="context__row">
          <label class="context__field context__field--grow">
            <span>Repositório</span>
            <select name="repo" [(ngModel)]="form.repositoryRoot">
              <option value="">Padrão (primeiro indexado)</option>
              @for (repo of repositories(); track repo.id) {
                <option [value]="repo.root_handle">{{ repo.root_handle }}</option>
              }
            </select>
          </label>

          <label class="context__field">
            <span>Nível</span>
            <select name="level" [(ngModel)]="form.level">
              @for (level of levels; track level.value) {
                <option [value]="level.value">
                  {{ level.label }} — {{ level.hint }}
                </option>
              }
            </select>
          </label>
        </div>

        <div class="context__row">
          <label class="context__field context__field--grow">
            <span>Orçamento de tokens: {{ form.budgetTokens }}</span>
            <input
              type="range"
              name="budget"
              [(ngModel)]="form.budgetTokens"
              min="512"
              max="64000"
              step="256"
              [attr.aria-valuetext]="form.budgetTokens + ' tokens'"
            />
          </label>

          <label class="context__field context__field--small">
            <span>Máx. símbolos</span>
            <input
              type="number"
              name="maxSymbols"
              [(ngModel)]="form.maxSymbols"
              min="1"
              max="200"
            />
          </label>
        </div>

        <div class="context__actions">
          <button
            type="submit"
            [disabled]="phase() === 'loading' || !form.task.trim()"
          >
            {{ phase() === 'loading' ? 'Gerando…' : 'Gerar pack' }}
          </button>
        </div>
      </form>

      @if (phase() === 'idle') {
        <p class="context__hint">Descreva a tarefa para gerar um pack.</p>
      }

      @if (showBlock()) {
        <atlas-state-block
          [state]="blockState()"
          loadingMessage="Montando o context pack…"
          emptyMessage="O pack foi gerado sem itens selecionados."
          [errorMessage]="errorText()"
        />
      }

      @if (pack(); as view) {
        <div class="context__pack" aria-live="polite" aria-label="Context pack gerado">
          @if (view.insufficientBudget || view.mandatoryContextOmitted) {
            <div class="context__banner" role="alert">
              <strong>Orçamento insuficiente.</strong>
              O pack omitiu contexto obrigatório.
              @if (view.suggestedMinimumBudget > 0) {
                Orçamento mínimo sugerido: {{ view.suggestedMinimumBudget }} tokens.
              }
            </div>
          }
          @if (view.truncated) {
            <div class="context__banner context__banner--warn" role="status">
              Pack truncado pelo orçamento — use o controle acima para aumentá-lo
              ou consulte os pontos de expansão.
            </div>
          }

          <section class="context__panel" aria-labelledby="pack-manifest">
            <h2 id="pack-manifest">Manifesto</h2>
            <dl class="context__meta">
              <div><dt>Repositório</dt><dd>{{ view.repository || '—' }}</dd></div>
              <div><dt>Snapshot</dt><dd>#{{ view.snapshotId }}</dd></div>
              <div><dt>Nível</dt><dd>{{ view.level }}</dd></div>
              <div><dt>Estimador</dt><dd>{{ view.estimator }}</dd></div>
              <div><dt>Frescor</dt><dd>{{ view.freshness }}</dd></div>
              <div>
                <dt>Itens</dt>
                <dd>{{ view.selected }} selecionado(s), {{ view.omitted }} omitido(s)</dd>
              </div>
            </dl>
            @if (view.rankingExplanation) {
              <p class="context__muted">{{ view.rankingExplanation }}</p>
            }
          </section>

          <section class="context__panel" aria-labelledby="pack-budget">
            <h2 id="pack-budget">Orçamento</h2>
            <div
              class="budget"
              role="img"
              [attr.aria-label]="
                'Uso estimado: ' +
                view.budget.usedPercent +
                '% do limite de ' +
                view.budget.limit +
                ' tokens'
              "
            >
              <div class="budget__track">
                <div
                  class="budget__fill"
                  [class.budget__fill--over]="view.budget.overBudget"
                  [style.width.%]="view.budget.usedPercent"
                ></div>
              </div>
              <p class="context__muted">
                ~{{ view.budget.reservedTotal + view.budget.itemsEstimate }} de
                {{ view.budget.limit }} tokens
                ({{ view.budget.usedPercent }}%)
                — reservas {{ view.budget.reservedTotal }}, itens ~{{
                  view.budget.itemsEstimate
                }}, payload ~{{ view.tokensPayload }}
              </p>
              <p class="context__muted">
                Valores <strong>estimados</strong> pelo estimador
                “{{ view.budget.estimator }}” — nunca contagem exata de tokens.
              </p>
            </div>
          </section>

          @if (view.selectedSymbols.length > 0) {
            <section class="context__panel" aria-labelledby="pack-symbols">
              <h2 id="pack-symbols">
                Símbolos selecionados ({{ view.selectedSymbols.length }})
              </h2>
              <ul class="context__list">
                @for (symbol of view.selectedSymbols; track symbol) {
                  <li><code>{{ symbol }}</code></li>
                }
              </ul>
            </section>
          }

          @if (view.relevantFiles.length > 0) {
            <section class="context__panel" aria-labelledby="pack-files">
              <h2 id="pack-files">
                Arquivos relevantes ({{ view.relevantFiles.length }})
              </h2>
              <ul class="context__list">
                @for (file of view.relevantFiles; track file) {
                  <li><code>{{ file }}</code></li>
                }
              </ul>
            </section>
          }

          @if (view.items.length > 0) {
            <section class="context__panel" aria-labelledby="pack-items">
              <h2 id="pack-items">Itens de contexto ({{ view.items.length }})</h2>
              <table class="context__table">
                <thead>
                  <tr>
                    <th scope="col">Item</th>
                    <th scope="col">Tipo</th>
                    <th scope="col">Prioridade</th>
                    <th scope="col">~Tokens</th>
                    <th scope="col">Origem</th>
                  </tr>
                </thead>
                <tbody>
                  @for (item of view.items; track item.itemId) {
                    <tr>
                      <td>
                        {{ item.title }}
                        @if (item.expandable) {
                          <span class="context__tag">expansível</span>
                        }
                      </td>
                      <td>{{ item.kind }}</td>
                      <td>{{ item.priority }}</td>
                      <td>{{ item.estTokens }}</td>
                      <td><code>{{ item.path || item.symbolKey || '—' }}</code></td>
                    </tr>
                  }
                </tbody>
              </table>
            </section>
          }

          @if (view.dependencies.length > 0) {
            <section class="context__panel" aria-labelledby="pack-deps">
              <h2 id="pack-deps">Dependências ({{ view.dependencies.length }})</h2>
              <ul class="context__list">
                @for (dep of view.dependencies; track dep.ref) {
                  <li>
                    <code>{{ dep.ref }}</code> ({{ dep.kind }})
                    @if (dep.detail) {
                      — {{ dep.detail }}
                    }
                  </li>
                }
              </ul>
            </section>
          }

          @if (view.unresolvedRefs.length > 0) {
            <section class="context__panel" aria-labelledby="pack-unresolved">
              <h2 id="pack-unresolved">
                Referências não resolvidas ({{ view.unresolvedRefs.length }})
              </h2>
              <ul class="context__list">
                @for (ref of view.unresolvedRefs; track ref) {
                  <li><code>{{ ref }}</code></li>
                }
              </ul>
            </section>
          }

          @if (view.expansionRefs.length > 0) {
            <section class="context__panel" aria-labelledby="pack-expansion">
              <h2 id="pack-expansion">
                Pontos de expansão ({{ view.expansionRefs.length }})
              </h2>
              <ul class="context__list">
                @for (ref of view.expansionRefs; track ref.refId) {
                  <li>
                    <code>{{ ref.refId }}</code> ({{ ref.kind }}) — {{ ref.reason }}
                  </li>
                }
              </ul>
            </section>
          }

          @if (view.diagnosticCodes.length > 0 || view.diagnosticMessages.length > 0) {
            <section class="context__panel context__panel--warn" aria-labelledby="pack-diags">
              <h2 id="pack-diags">Diagnósticos do pack</h2>
              <ul class="context__list">
                @for (code of view.diagnosticCodes; track code) {
                  <li><code>{{ code }}</code></li>
                }
                @for (message of view.diagnosticMessages; track message) {
                  <li>{{ message }}</li>
                }
              </ul>
            </section>
          }

          <div class="context__export">
            <button type="button" (click)="copyJson()">Copiar JSON</button>
            <button type="button" (click)="copyMarkdown()">Copiar Markdown</button>
            <button type="button" (click)="exportJson()">Exportar .json</button>
            <span class="context__copy-status" role="status" aria-live="polite">
              {{ copyStatus() }}
            </span>
          </div>
        </div>
      }
    </section>
  `,
  styles: [
    `
      .context {
        padding: var(--atlas-space-5);
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-4);
      }

      .context__header h1 {
        margin: 0 0 var(--atlas-space-2);
        font-size: var(--atlas-font-size-xl);
      }

      .context__description {
        margin: 0;
        color: var(--atlas-color-text-muted);
        max-width: 72ch;
      }

      .context__form {
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-3);
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-4);
        background: var(--atlas-color-bg-raised);
      }

      .context__row {
        display: flex;
        flex-wrap: wrap;
        gap: var(--atlas-space-3);
      }

      .context__field {
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-1);
        font-size: var(--atlas-font-size-sm);
        min-width: 12rem;
      }

      .context__field--grow {
        flex: 1;
      }

      .context__field--small {
        min-width: 7rem;
      }

      .context__field input,
      .context__field select,
      .context__field textarea {
        padding: var(--atlas-space-2);
        border: 1px solid var(--atlas-color-border-strong);
        border-radius: var(--atlas-radius-sm);
        background: var(--atlas-color-bg);
        color: var(--atlas-color-text);
        font: inherit;
      }

      .context__actions button,
      .context__export button {
        padding: var(--atlas-space-2) var(--atlas-space-4);
        border-radius: var(--atlas-radius-sm);
        border: 1px solid var(--atlas-color-border-strong);
        background: var(--atlas-color-bg-subtle);
        color: var(--atlas-color-text);
        cursor: pointer;
      }

      .context__actions button[type='submit'] {
        border: 0;
        background: var(--atlas-color-accent);
        color: var(--atlas-color-accent-text);
        font-weight: var(--atlas-font-weight-medium);
      }

      .context__actions button:disabled {
        opacity: 0.6;
        cursor: not-allowed;
      }

      .context__hint,
      .context__muted {
        color: var(--atlas-color-text-muted);
        font-size: var(--atlas-font-size-sm);
        margin: var(--atlas-space-1) 0 0;
      }

      .context__banner {
        padding: var(--atlas-space-3) var(--atlas-space-4);
        border-radius: var(--atlas-radius-md);
        border: 1px solid var(--atlas-color-danger);
        background: var(--atlas-color-danger-bg);
        color: var(--atlas-color-danger);
      }

      .context__banner--warn {
        border-color: var(--atlas-color-warning);
        background: var(--atlas-color-warning-bg);
        color: var(--atlas-color-warning);
      }

      .context__pack {
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-4);
      }

      .context__panel {
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-4);
        background: var(--atlas-color-bg-raised);
      }

      .context__panel--warn {
        border-color: var(--atlas-color-warning);
      }

      .context__panel h2 {
        margin: 0 0 var(--atlas-space-3);
        font-size: var(--atlas-font-size-lg);
      }

      .context__meta {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
        gap: var(--atlas-space-2) var(--atlas-space-4);
        margin: 0;
      }

      .context__meta dt {
        font-size: var(--atlas-font-size-xs);
        color: var(--atlas-color-text-muted);
      }

      .context__meta dd {
        margin: 0;
        overflow-wrap: anywhere;
      }

      .budget__track {
        height: 0.75rem;
        border-radius: var(--atlas-radius-full);
        background: var(--atlas-color-bg-subtle);
        border: 1px solid var(--atlas-color-border);
        overflow: hidden;
      }

      .budget__fill {
        height: 100%;
        background: var(--atlas-color-accent);
        transition: width var(--atlas-motion-base) var(--atlas-motion-easing);
      }

      .budget__fill--over {
        background: var(--atlas-color-danger);
      }

      @media (prefers-reduced-motion: reduce) {
        .budget__fill {
          transition: none;
        }
      }

      .context__list {
        margin: 0;
        padding-left: var(--atlas-space-5);
      }

      .context__list li {
        margin-bottom: var(--atlas-space-1);
        overflow-wrap: anywhere;
      }

      .context__table {
        width: 100%;
        border-collapse: collapse;
        font-size: var(--atlas-font-size-sm);
      }

      .context__table th,
      .context__table td {
        text-align: left;
        padding: var(--atlas-space-1) var(--atlas-space-2);
        border-bottom: 1px solid var(--atlas-color-border);
        overflow-wrap: anywhere;
      }

      .context__tag {
        display: inline-block;
        margin-left: var(--atlas-space-2);
        padding: 0 var(--atlas-space-2);
        border-radius: var(--atlas-radius-full);
        border: 1px solid var(--atlas-color-accent);
        color: var(--atlas-color-accent);
        font-size: var(--atlas-font-size-xs);
      }

      .context__export {
        display: flex;
        align-items: center;
        gap: var(--atlas-space-2);
        flex-wrap: wrap;
      }

      .context__copy-status {
        color: var(--atlas-color-text-muted);
        font-size: var(--atlas-font-size-sm);
      }
    `,
  ],
})
export class ContextComponent implements OnInit, OnDestroy {
  readonly levels = LEVELS;

  readonly form = {
    task: '',
    repositoryRoot: '',
    level: 'focus' as ApiContextLevel,
    budgetTokens: 4000,
    maxSymbols: 50,
  };

  readonly phase = signal<ContextPhase>('idle');
  readonly pack = signal<PackView | null>(null);
  readonly repositories = signal<ApiRepositoryItem[]>([]);
  readonly errorText = signal('');
  readonly copyStatus = signal('');

  readonly showBlock = computed(() => {
    const p = this.phase();
    if (p === 'loading' || p === 'error' || p === 'offline') return true;
    const pack = this.pack();
    return (
      p === 'ready' &&
      pack !== null &&
      pack.items.length === 0 &&
      pack.selectedSymbols.length === 0 &&
      pack.relevantFiles.length === 0
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
  private lastPack: ApiContextPack | null = null;

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

  async build(): Promise<void> {
    const task = this.form.task.trim();
    if (!task) return;
    this.phase.set('loading');
    this.errorText.set('');
    this.pack.set(null);
    this.copyStatus.set('');
    try {
      const res = await this.perf.time('POST /context', () =>
        this.api.buildContextPack(this.buildRequest(task), {
          signal: this.controller.signal,
        }),
      );
      this.lastPack = res;
      this.pack.set(toPackView(res));
      this.phase.set('ready');
    } catch (error) {
      this.handleError(error);
    }
  }

  async copyJson(): Promise<void> {
    if (!this.lastPack) return;
    await this.copyText(JSON.stringify(this.lastPack, null, 2), 'JSON copiado.');
  }

  async copyMarkdown(): Promise<void> {
    if (!this.lastPack) return;
    await this.copyText(packToMarkdown(this.lastPack), 'Markdown copiado.');
  }

  exportJson(): void {
    if (!this.lastPack || typeof document === 'undefined') return;
    const blob = new Blob([JSON.stringify(this.lastPack, null, 2)], {
      type: 'application/json',
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = 'context-pack.json';
    anchor.click();
    URL.revokeObjectURL(url);
    this.copyStatus.set('Arquivo context-pack.json gerado localmente.');
  }

  private async copyText(text: string, success: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      this.copyStatus.set(success);
    } catch {
      this.copyStatus.set(
        'Cópia indisponível neste navegador — use Exportar .json.',
      );
    }
  }

  private buildRequest(task: string): ApiContextRequest {
    const req: ApiContextRequest = {
      task,
      level: this.form.level,
      budget_tokens: this.form.budgetTokens,
      max_symbols: this.form.maxSymbols,
    };
    if (this.form.repositoryRoot) req.repository_root = this.form.repositoryRoot;
    return req;
  }

  private async loadRepositories(): Promise<void> {
    try {
      const res = await this.api.listRepositories({ signal: this.controller.signal });
      this.repositories.set(res.items ?? []);
    } catch (error) {
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
