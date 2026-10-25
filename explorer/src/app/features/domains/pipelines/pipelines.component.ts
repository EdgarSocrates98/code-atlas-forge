import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  OnInit,
  computed,
  effect,
  signal,
} from '@angular/core';
import { ApiService } from '../../../core/api/api.service';
import { ExplorerContextService } from '../../../core/state/explorer-context.service';
import { apiErrorText, isApiOffline } from '../../../shared/errors';
import {
  BlockState,
  StateBlockComponent,
} from '../../../shared/components/state-block/state-block.component';
import { EvidenceBadgeComponent } from '../../../shared/components/evidence-badge/evidence-badge.component';
import { PerfLogService } from '../../diagnostics/perf-log.service';
import {
  PipelineWorkflowView,
  buildPipelinesView,
  jobDisplayName,
} from './pipelines.vm';

type PipelinesPhase = 'loading' | 'ready' | 'error' | 'offline';

const PIPELINE_EDGE_TYPES = 'DEPENDS_ON,DEPLOYS,TRIGGERS';

/**
 * CI/CD pipelines view (lf-0122): workflows → jobs → `needs` DAG in
 * accessible table form (with computed DAG levels), plus triggers.
 * Unresolved `needs` targets are shown as text, never linked.
 */
@Component({
  selector: 'atlas-pipelines',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [StateBlockComponent, EvidenceBadgeComponent],
  template: `
    <section class="pipelines" aria-labelledby="pipelines-title">
      <header class="pipelines__header">
        <h1 id="pipelines-title">Pipelines CI/CD</h1>
        <p class="pipelines__description">
          Workflows, jobs e dependências <code>needs</code> observadas nos
          arquivos de workflow do snapshot ativo.
        </p>
        <button type="button" (click)="load()" [disabled]="phase() === 'loading'">
          {{ phase() === 'loading' ? 'Atualizando…' : 'Atualizar' }}
        </button>
      </header>

      @if (showBlock()) {
        <atlas-state-block
          [state]="blockState()"
          loadingMessage="Consultando workflows no snapshot…"
          [errorMessage]="errorText()"
        />
      }

      @if (phase() === 'ready' && workflows().length === 0) {
        <div class="pipelines__empty" aria-live="polite">
          <h2>Nenhum workflow CI/CD neste snapshot</h2>
          <p>
            O índice não registrou arquivos de workflow para o repositório
            ativo — habilite o adapter <code>cicd</code> durante
            <code>code-atlas index</code>.
          </p>
        </div>
      }

      @for (wf of workflows(); track wf.workflow.id) {
        <section class="pipelines__workflow" [attr.aria-labelledby]="'wf-' + wf.workflow.id">
          <h2 id="wf-{{ wf.workflow.id }}">
            {{ wf.workflow.attributes['name'] || wf.workflow.name || wf.workflow.qualified_name }}
            @if (wf.workflow.attributes['platform']) {
              <span class="pipelines__muted">({{ wf.workflow.attributes['platform'] }})</span>
            }
          </h2>
          <p class="pipelines__muted"><code>{{ wf.workflow.path ?? wf.workflow.qualified_name }}</code></p>

          @if (wf.triggers.length > 0) {
            <p>
              <span class="pipelines__label">Gatilhos:</span>
              @for (t of wf.triggers; track t) {
                <code class="pipelines__chip">{{ t }}</code>
              }
            </p>
          }

          @if (wf.jobs.length === 0) {
            <p class="pipelines__muted">Nenhum job observado.</p>
          } @else {
            @if (wf.cyclic) {
              <p class="pipelines__warn" role="note">
                Dependências <code>needs</code> contêm um ciclo — níveis do
                DAG não exibidos para os jobs afetados.
              </p>
            }
            <table class="pipelines__table">
              <thead>
                <tr>
                  <th scope="col">Job</th>
                  <th scope="col">Nível</th>
                  <th scope="col">Runner</th>
                  <th scope="col">Needs</th>
                  <th scope="col">Deploy</th>
                </tr>
              </thead>
              <tbody>
                @for (j of wf.jobs; track j.job.id) {
                  <tr>
                    <td>
                      <code>{{ displayName(j.job) }}</code>
                      @if (j.job.attributes['name']) {
                        <div class="pipelines__muted">{{ j.job.attributes['name'] }}</div>
                      }
                    </td>
                    <td>{{ j.level ?? '—' }}</td>
                    <td>{{ j.job.attributes['runs_on'] || '—' }}</td>
                    <td>
                      @for (n of j.needs; track n) {
                        <code class="pipelines__chip">{{ n }}</code>
                      }
                      @for (n of j.unresolvedNeeds; track n) {
                        <span class="pipelines__chip pipelines__chip--unresolved">{{ n }} (não resolvido)</span>
                      }
                      @if (j.needs.length === 0 && j.unresolvedNeeds.length === 0) {
                        —
                      }
                    </td>
                    <td>
                      @for (d of j.deploys; track d) {
                        <code class="pipelines__chip">{{ d }}</code>
                      }
                      @if (j.deploys.length === 0) {
                        —
                      }
                    </td>
                  </tr>
                }
              </tbody>
            </table>
          }
        </section>
      }
    </section>
  `,
  styles: [
    `
      .pipelines {
        padding: var(--atlas-space-5);
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-4);
      }

      .pipelines__header {
        display: flex;
        flex-wrap: wrap;
        align-items: baseline;
        gap: var(--atlas-space-2) var(--atlas-space-4);
      }

      .pipelines__header h1 {
        margin: 0;
        font-size: var(--atlas-font-size-xl);
      }

      .pipelines__description {
        margin: 0;
        color: var(--atlas-color-text-muted);
        flex: 1;
        min-width: 16rem;
      }

      .pipelines__header button {
        padding: var(--atlas-space-1) var(--atlas-space-3);
        border: 1px solid var(--atlas-color-border-strong);
        border-radius: var(--atlas-radius-sm);
        background: var(--atlas-color-bg-subtle);
        color: var(--atlas-color-text);
        cursor: pointer;
      }

      .pipelines__empty {
        padding: var(--atlas-space-4);
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        background: var(--atlas-color-bg-subtle);
        color: var(--atlas-color-text-muted);
      }

      .pipelines__empty h2 {
        margin: 0 0 var(--atlas-space-2);
        font-size: var(--atlas-font-size-md);
        color: var(--atlas-color-text);
      }

      .pipelines__workflow {
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-3) var(--atlas-space-4);
        background: var(--atlas-color-bg-raised);
      }

      .pipelines__workflow h2 {
        margin: 0;
        font-size: var(--atlas-font-size-lg);
      }

      .pipelines__table {
        width: 100%;
        border-collapse: collapse;
        font-size: var(--atlas-font-size-sm);
      }

      .pipelines__table th,
      .pipelines__table td {
        border-bottom: 1px solid var(--atlas-color-border);
        padding: var(--atlas-space-2);
        text-align: left;
        vertical-align: top;
        overflow-wrap: anywhere;
      }

      .pipelines__table th {
        font-size: var(--atlas-font-size-xs);
        color: var(--atlas-color-text-muted);
      }

      .pipelines__chip {
        display: inline-block;
        margin: 0 var(--atlas-space-1) var(--atlas-space-1) 0;
        padding: 0 var(--atlas-space-2);
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-sm);
        font-size: var(--atlas-font-size-xs);
        font-family: var(--atlas-font-family-mono);
        background: var(--atlas-color-bg-subtle);
      }

      .pipelines__chip--unresolved {
        font-family: inherit;
        border-style: dashed;
        color: var(--atlas-color-text-muted);
      }

      .pipelines__muted {
        color: var(--atlas-color-text-muted);
        font-size: var(--atlas-font-size-xs);
      }

      .pipelines__warn {
        color: var(--atlas-color-warning, #8a6d1a);
        font-size: var(--atlas-font-size-sm);
      }

      .pipelines__label {
        font-size: var(--atlas-font-size-xs);
        color: var(--atlas-color-text-muted);
        margin-right: var(--atlas-space-2);
      }
    `,
  ],
})
export class PipelinesComponent implements OnInit, OnDestroy {
  readonly phase = signal<PipelinesPhase>('loading');
  readonly workflows = signal<PipelineWorkflowView[]>([]);
  readonly errorText = signal('');

  readonly showBlock = computed(() => {
    const p = this.phase();
    return p === 'loading' || p === 'error' || p === 'offline';
  });
  readonly blockState = computed<BlockState>(() => {
    switch (this.phase()) {
      case 'loading':
        return 'loading';
      case 'offline':
        return 'offline';
      default:
        return 'error';
    }
  });

  private readonly controller = new AbortController();
  private scopeKey = '';

  constructor(
    private readonly api: ApiService,
    private readonly perf: PerfLogService,
    readonly ctx: ExplorerContextService,
  ) {
    effect(() => {
      const key = `${this.ctx.activeRepository()?.id ?? ''}:${this.ctx.activeSnapshot()?.id ?? ''}`;
      if (key === this.scopeKey) return;
      this.scopeKey = key;
      if (!key.replace(':', '')) return;
      // defer writes — signal updates are not allowed inside effect()
      queueMicrotask(() => {
        if (this.scopeKey === key) void this.load();
      });
    });
  }

  ngOnInit(): void {
    void this.ctx.init();
  }

  ngOnDestroy(): void {
    this.controller.abort();
  }

  async load(): Promise<void> {
    this.phase.set('loading');
    this.errorText.set('');
    try {
      const scope = {
        repositoryId: this.ctx.activeRepository()?.id,
        snapshotId: this.ctx.activeSnapshot()?.id,
      };
      const [entities, relations] = await Promise.all([
        this.perf.time('GET /domains/entities', () =>
          this.api.getDomainEntities(
            { domain: 'cicd', limit: 200, ...scope },
            { signal: this.controller.signal },
          ),
        ),
        this.perf.time('GET /domains/relations', () =>
          this.api.getDomainRelations(
            { type: PIPELINE_EDGE_TYPES, limit: 200, ...scope },
            { signal: this.controller.signal },
          ),
        ),
      ]);
      this.workflows.set(
        buildPipelinesView(entities.items ?? [], relations.edges ?? []),
      );
      this.phase.set('ready');
    } catch (error) {
      this.workflows.set([]);
      this.handleError(error);
    }
  }

  displayName = jobDisplayName;

  private handleError(error: unknown): void {
    if (isApiOffline(error)) {
      this.phase.set('offline');
      return;
    }
    this.errorText.set(apiErrorText(error));
    this.phase.set('error');
  }
}
