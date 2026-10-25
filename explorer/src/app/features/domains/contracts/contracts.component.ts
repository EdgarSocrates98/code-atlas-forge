import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  OnInit,
  computed,
  effect,
  signal,
} from '@angular/core';
import {
  ApiDomainEdge,
  ApiDomainEntity,
  ApiService,
} from '../../../core/api/api.service';
import { ExplorerContextService } from '../../../core/state/explorer-context.service';
import { apiErrorText, isApiOffline } from '../../../shared/errors';
import {
  BlockState,
  StateBlockComponent,
} from '../../../shared/components/state-block/state-block.component';
import { EvidenceBadgeComponent } from '../../../shared/components/evidence-badge/evidence-badge.component';
import { PerfLogService } from '../../diagnostics/perf-log.service';
import {
  edgeEvidenceLevel,
  endpointDetail,
  endpointLabel,
} from '../domain.utils';
import { ContractsView, buildContractsView } from './contracts.vm';

type ContractsPhase = 'loading' | 'ready' | 'error' | 'offline';
type ConsumersPhase = 'idle' | 'loading' | 'ready' | 'error';

export interface ConsumersState {
  phase: ConsumersPhase;
  edges: ApiDomainEdge[];
  error: string;
}

/**
 * API contract view (lf-0122): services → endpoints with method+path,
 * lazy CALLS_ENDPOINT consumers per endpoint, and the contract spec
 * files as provenance. Everything is snapshot-scoped and read-only.
 */
@Component({
    selector: 'atlas-contracts',
    changeDetection: ChangeDetectionStrategy.OnPush,
    imports: [StateBlockComponent, EvidenceBadgeComponent],
    template: `
    <section class="contracts" aria-labelledby="contracts-title">
      <header class="contracts__header">
        <h1 id="contracts-title">Contratos de API</h1>
        <p class="contracts__description">
          Serviços, endpoints e consumidores observados no snapshot ativo.
          A proveniência de cada contrato é o arquivo de especificação —
          consumidores são chamadas literais extraídas do código.
        </p>
        <button
          type="button"
          (click)="load()"
          [disabled]="phase() === 'loading'"
        >
          {{ phase() === 'loading' ? 'Atualizando…' : 'Atualizar' }}
        </button>
      </header>

      @if (showBlock()) {
        <atlas-state-block
          [state]="blockState()"
          loadingMessage="Consultando entidades de API no snapshot…"
          [errorMessage]="errorText()"
        />
      }

      @if (phase() === 'ready' && isEmpty()) {
        <div class="contracts__empty" aria-live="polite">
          <h2>Nenhum contrato de API neste snapshot</h2>
          <p>
            O índice não registrou specs OpenAPI nem rotas de framework
            para o repositório ativo — habilite o adapter
            <code>api</code> durante <code>code-atlas index</code>.
          </p>
        </div>
      }

      @if (view(); as v) {
        @if (v.contracts.length > 0) {
          <section aria-labelledby="contracts-files">
            <h2 id="contracts-files">Arquivos de contrato</h2>
            <table class="contracts__table">
              <thead>
                <tr>
                  <th scope="col">Nome</th>
                  <th scope="col">Arquivo</th>
                  <th scope="col">Formato</th>
                  <th scope="col">Versão</th>
                </tr>
              </thead>
              <tbody>
                @for (c of v.contracts; track c.id) {
                  <tr>
                    <td>{{ c.name || c.qualified_name }}</td>
                    <td><code>{{ c.path ?? c.qualified_name }}</code></td>
                    <td>{{ c.attributes['format'] || '—' }}</td>
                    <td>{{ c.attributes['api_version'] || c.attributes['spec_version'] || '—' }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </section>
        }

        @for (svc of v.services; track svc.service.id) {
          <section class="contracts__service" [attr.aria-labelledby]="'svc-' + svc.service.id">
            <h2 id="svc-{{ svc.service.id }}">
              {{ svc.service.name || svc.service.qualified_name }}
              @if (svc.service.name && svc.service.qualified_name !== svc.service.name) {
                <span class="contracts__muted">({{ svc.service.qualified_name }})</span>
              }
            </h2>
            <dl class="contracts__meta">
              @if (svc.service.attributes['title']; as title) {
                <div><dt>Título</dt><dd>{{ title }}</dd></div>
              }
              @if (svc.service.attributes['version']; as version) {
                <div><dt>Versão</dt><dd>{{ version }}</dd></div>
              }
              @if (svc.service.attributes['framework']; as fw) {
                <div><dt>Framework</dt><dd>{{ fw }}</dd></div>
              }
              @if (svc.service.attributes['server_url']; as url) {
                <div><dt>Server</dt><dd><code>{{ url }}</code></dd></div>
              }
              @if (svc.service.attributes['base_path']; as base) {
                <div><dt>Base path</dt><dd><code>{{ base }}</code></dd></div>
              }
              @if (svc.service.attributes['spec']; as spec) {
                <div><dt>Spec</dt><dd><code>{{ spec }}</code></dd></div>
              }
              @if (svc.service.path; as p) {
                <div><dt>Origem</dt><dd><code>{{ p }}</code></dd></div>
              }
            </dl>

            @if (svc.endpoints.length === 0) {
              <p class="contracts__muted">Nenhum endpoint observado.</p>
            } @else {
              <table class="contracts__table">
                <thead>
                  <tr>
                    <th scope="col">Método</th>
                    <th scope="col">Path</th>
                    <th scope="col">Consumidores</th>
                  </tr>
                </thead>
                <tbody>
                  @for (ep of svc.endpoints; track ep.id) {
                    <tr>
                      <td><code>{{ ep.attributes['method'] || '—' }}</code></td>
                      <td>
                        <code>{{ ep.attributes['full_path'] || ep.attributes['path'] || ep.qualified_name }}</code>
                        @if (ep.path; as p) {
                          <div class="contracts__muted">{{ p }}</div>
                        }
                      </td>
                      <td>
                        <button
                          type="button"
                          class="contracts__consumers-toggle"
                          (click)="toggleConsumers(ep.id)"
                          [attr.aria-expanded]="consumersOpen().has(ep.id)"
                          [attr.aria-controls]="'consumers-' + ep.id"
                        >
                          {{ consumersOpen().has(ep.id) ? 'Ocultar' : 'Consumidores' }}
                        </button>
                        @if (consumersOpen().has(ep.id)) {
                          <!-- The disclosure region must keep this id in
                               every phase — aria-controls on the toggle
                               always points at it (axe aria-valid-attr-value
                               fails on a dangling reference). -->
                          <div
                            class="contracts__consumers-region"
                            [id]="'consumers-' + ep.id"
                          >
                            @switch (consumerState(ep.id).phase) {
                              @case ('loading') {
                                <p class="contracts__muted" role="status">Carregando…</p>
                              }
                              @case ('error') {
                                <p class="contracts__error" role="alert">{{ consumerState(ep.id).error }}</p>
                              }
                              @case ('ready') {
                                @if (consumerState(ep.id).edges.length === 0) {
                                  <p class="contracts__muted">Nenhum consumidor conhecido.</p>
                                } @else {
                                  <ul class="contracts__consumers">
                                    @for (edge of consumerState(ep.id).edges; track edge.id) {
                                      <li>
                                        <atlas-evidence-badge [level]="edgeLevel(edge)" />
                                        <span>{{ endpointLabel(edge.from) }}</span>
                                        <span class="contracts__muted">{{ endpointDetail(edge.from) }}</span>
                                      </li>
                                    }
                                  </ul>
                                }
                              }
                            }
                          </div>
                        }
                      </td>
                    </tr>
                  }
                </tbody>
              </table>
            }
          </section>
        }

        @if (v.unboundEndpoints.length > 0) {
          <section aria-labelledby="contracts-unbound">
            <h2 id="contracts-unbound">Endpoints sem serviço associado</h2>
            <table class="contracts__table">
              <thead>
                <tr>
                  <th scope="col">Endpoint</th>
                  <th scope="col">Namespace</th>
                  <th scope="col">Origem</th>
                </tr>
              </thead>
              <tbody>
                @for (ep of v.unboundEndpoints; track ep.id) {
                  <tr>
                    <td><code>{{ ep.qualified_name }}</code></td>
                    <td>{{ ep.namespace || '—' }}</td>
                    <td><code>{{ ep.path ?? '—' }}</code></td>
                  </tr>
                }
              </tbody>
            </table>
          </section>
        }
      }
    </section>
  `,
    styles: [
        `
      .contracts {
        padding: var(--atlas-space-5);
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-4);
      }

      .contracts__header {
        display: flex;
        flex-wrap: wrap;
        align-items: baseline;
        gap: var(--atlas-space-2) var(--atlas-space-4);
      }

      .contracts__header h1 {
        margin: 0;
        font-size: var(--atlas-font-size-xl);
      }

      .contracts__description {
        margin: 0;
        color: var(--atlas-color-text-muted);
        flex: 1;
        min-width: 16rem;
        overflow-wrap: anywhere;
      }

      .contracts__header button {
        padding: var(--atlas-space-1) var(--atlas-space-3);
        border: 1px solid var(--atlas-color-border-strong);
        border-radius: var(--atlas-radius-sm);
        background: var(--atlas-color-bg-subtle);
        color: var(--atlas-color-text);
        cursor: pointer;
      }

      .contracts__empty {
        padding: var(--atlas-space-4);
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        background: var(--atlas-color-bg-subtle);
        color: var(--atlas-color-text-muted);
      }

      .contracts__empty h2 {
        margin: 0 0 var(--atlas-space-2);
        font-size: var(--atlas-font-size-md);
        color: var(--atlas-color-text);
      }

      .contracts__service {
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-3) var(--atlas-space-4);
        background: var(--atlas-color-bg-raised);
      }

      .contracts__service h2 {
        margin: 0 0 var(--atlas-space-2);
        font-size: var(--atlas-font-size-lg);
      }

      .contracts__meta {
        margin: 0 0 var(--atlas-space-3);
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
        gap: var(--atlas-space-2) var(--atlas-space-4);
      }

      .contracts__meta dt {
        font-size: var(--atlas-font-size-xs);
        color: var(--atlas-color-text-muted);
      }

      .contracts__meta dd {
        margin: 0;
        font-size: var(--atlas-font-size-sm);
        overflow-wrap: anywhere;
      }

      .contracts__table {
        width: 100%;
        border-collapse: collapse;
        font-size: var(--atlas-font-size-sm);
      }

      .contracts__table th,
      .contracts__table td {
        border-bottom: 1px solid var(--atlas-color-border);
        padding: var(--atlas-space-2);
        text-align: left;
        vertical-align: top;
        overflow-wrap: anywhere;
      }

      .contracts__table th {
        font-size: var(--atlas-font-size-xs);
        color: var(--atlas-color-text-muted);
      }

      .contracts__table code {
        font-family: var(--atlas-font-family-mono);
      }

      .contracts__consumers-toggle {
        font: inherit;
        font-size: var(--atlas-font-size-xs);
        padding: var(--atlas-space-1) var(--atlas-space-2);
        border: 1px solid var(--atlas-color-border-strong);
        border-radius: var(--atlas-radius-sm);
        background: var(--atlas-color-bg-subtle);
        color: var(--atlas-color-text);
        cursor: pointer;
      }

      .contracts__consumers {
        margin: var(--atlas-space-2) 0 0;
        padding: 0;
        list-style: none;
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-1);
      }

      .contracts__consumers li {
        display: flex;
        align-items: baseline;
        gap: var(--atlas-space-2);
        flex-wrap: wrap;
        overflow-wrap: anywhere;
      }

      .contracts__muted {
        color: var(--atlas-color-text-muted);
        font-size: var(--atlas-font-size-xs);
      }

      .contracts__error {
        color: var(--atlas-color-danger);
        font-size: var(--atlas-font-size-sm);
      }
    `,
    ]
})
export class ContractsComponent implements OnInit, OnDestroy {
  readonly phase = signal<ContractsPhase>('loading');
  readonly view = signal<ContractsView | null>(null);
  readonly errorText = signal('');
  readonly consumers = signal<ReadonlyMap<number, ConsumersState>>(new Map());
  readonly consumersOpen = signal<ReadonlySet<number>>(new Set());

  readonly isEmpty = computed(() => {
    const v = this.view();
    return (
      v !== null &&
      v.contracts.length === 0 &&
      v.services.length === 0 &&
      v.unboundEndpoints.length === 0
    );
  });
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
    this.consumers.set(new Map());
    this.consumersOpen.set(new Set());
    try {
      const res = await this.perf.time('GET /domains/entities', () =>
        this.api.getDomainEntities(
          {
            domain: 'api',
            limit: 200,
            repositoryId: this.ctx.activeRepository()?.id,
            snapshotId: this.ctx.activeSnapshot()?.id,
          },
          { signal: this.controller.signal },
        ),
      );
      this.view.set(buildContractsView(res.items ?? []));
      this.phase.set('ready');
    } catch (error) {
      this.view.set(null);
      this.handleError(error);
    }
  }

  async toggleConsumers(endpointId: number): Promise<void> {
    const open = new Set(this.consumersOpen());
    if (open.has(endpointId)) {
      open.delete(endpointId);
      this.consumersOpen.set(open);
      return;
    }
    open.add(endpointId);
    this.consumersOpen.set(open);
    if (this.consumers().has(endpointId)) return;
    this.setConsumers(endpointId, { phase: 'loading', edges: [], error: '' });
    try {
      const res = await this.perf.time('GET /domains/entities/{id}/neighbors', () =>
        this.api.getDomainEntityNeighbors(
          endpointId,
          'in',
          {
            repositoryId: this.ctx.activeRepository()?.id,
            snapshotId: this.ctx.activeSnapshot()?.id,
          },
          { signal: this.controller.signal },
        ),
      );
      const calls = (res.edges ?? []).filter(
        (e) => e.type === 'CALLS_ENDPOINT',
      );
      this.setConsumers(endpointId, { phase: 'ready', edges: calls, error: '' });
    } catch (error) {
      this.setConsumers(endpointId, {
        phase: 'error',
        edges: [],
        error: apiErrorText(error),
      });
    }
  }

  consumerState(id: number): ConsumersState {
    return this.consumers().get(id) ?? { phase: 'idle', edges: [], error: '' };
  }

  edgeLevel = edgeEvidenceLevel;
  endpointLabel = endpointLabel;
  endpointDetail = endpointDetail;

  private setConsumers(id: number, state: ConsumersState): void {
    const next = new Map(this.consumers());
    next.set(id, state);
    this.consumers.set(next);
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
