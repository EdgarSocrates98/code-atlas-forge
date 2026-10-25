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
  endpointLabel,
  kindLabel,
  relationLabel,
} from '../domain.utils';
import { InfraView, buildInfraView } from './infra.vm';

type InfraPhase = 'loading' | 'ready' | 'error' | 'offline';

const INFRA_EDGE_TYPES = 'PROVISIONS,USES_RESOURCE,ASSUMES_ROLE';

/**
 * Infrastructure view (lf-0122): terraform modules, their provisioned
 * resources, IAM roles and the PROVISIONS/USES_RESOURCE/ASSUMES_ROLE
 * edge list — everything verbatim from the snapshot's domain_relations.
 */
@Component({
  selector: 'atlas-infra',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [StateBlockComponent, EvidenceBadgeComponent],
  template: `
    <section class="infra" aria-labelledby="infra-title">
      <header class="infra__header">
        <h1 id="infra-title">Infraestrutura</h1>
        <p class="infra__description">
          Recursos Terraform agrupados por módulo, roles IAM e as relações
          de provisionamento observadas no snapshot ativo.
        </p>
        <button type="button" (click)="load()" [disabled]="phase() === 'loading'">
          {{ phase() === 'loading' ? 'Atualizando…' : 'Atualizar' }}
        </button>
      </header>

      @if (showBlock()) {
        <atlas-state-block
          [state]="blockState()"
          loadingMessage="Consultando entidades Terraform no snapshot…"
          [errorMessage]="errorText()"
        />
      }

      @if (phase() === 'ready' && isEmpty()) {
        <div class="infra__empty" aria-live="polite">
          <h2>Nenhum recurso Terraform neste snapshot</h2>
          <p>
            O índice não registrou módulos, recursos ou roles para o
            repositório ativo — habilite o adapter <code>terraform</code>
            durante <code>code-atlas index</code>.
          </p>
        </div>
      }

      @if (view(); as v) {
        @if (v.modules.length > 0 || v.namespaceGroups.length > 0) {
          <section aria-labelledby="infra-modules">
            <h2 id="infra-modules">Módulos e recursos</h2>
            @for (group of allGroups(v); track group.title) {
              <article class="infra__group">
                <h3>
                  {{ group.title }}
                  @if (group.module === null) {
                    <span class="infra__muted">(namespace)</span>
                  }
                </h3>
                @if (group.module; as mod) {
                  @if (mod.attributes['source'] || mod.attributes['module_role']) {
                    <p class="infra__muted">
                      {{ mod.attributes['module_role'] ? 'papel: ' + mod.attributes['module_role'] : '' }}
                      {{ mod.attributes['source'] ? 'source: ' + mod.attributes['source'] : '' }}
                    </p>
                  }
                }
                @if (group.resources.length === 0) {
                  <p class="infra__muted">Nenhum recurso provisionado observado.</p>
                } @else {
                  <table class="infra__table">
                    <thead>
                      <tr>
                        <th scope="col">Recurso</th>
                        <th scope="col">Tipo</th>
                        <th scope="col">Origem</th>
                      </tr>
                    </thead>
                    <tbody>
                      @for (res of group.resources; track res.id) {
                        <tr>
                          <td><code>{{ res.qualified_name }}</code></td>
                          <td>{{ kindLabel(res.kind) }}</td>
                          <td>
                            @if (res.path) {
                              <code>{{ res.path }}</code>
                            } @else {
                              —
                            }
                          </td>
                        </tr>
                      }
                    </tbody>
                  </table>
                }
              </article>
            }
          </section>
        }

        @if (v.iamRoles.length > 0) {
          <section aria-labelledby="infra-iam">
            <h2 id="infra-iam">Roles IAM</h2>
            <table class="infra__table">
              <thead>
                <tr>
                  <th scope="col">Role</th>
                  <th scope="col">Serviços confiáveis</th>
                  <th scope="col">Origem</th>
                </tr>
              </thead>
              <tbody>
                @for (role of v.iamRoles; track role.id) {
                  <tr>
                    <td><code>{{ role.qualified_name }}</code></td>
                    <td>{{ role.attributes['trusted_services'] || '—' }}</td>
                    <td><code>{{ role.path ?? '—' }}</code></td>
                  </tr>
                }
              </tbody>
            </table>
          </section>
        }

        @if (v.edges.length > 0) {
          <section aria-labelledby="infra-edges">
            <h2 id="infra-edges">Relações de infraestrutura</h2>
            @if (v.edgesTruncated) {
              <p class="infra__muted">
                Lista truncada pelo limite de arestas da consulta.
              </p>
            }
            <table class="infra__table">
              <thead>
                <tr>
                  <th scope="col">De</th>
                  <th scope="col">Relação</th>
                  <th scope="col">Para</th>
                  <th scope="col">Evidência</th>
                </tr>
              </thead>
              <tbody>
                @for (edge of v.edges; track edge.id) {
                  <tr>
                    <td>{{ endpointLabel(edge.from) }}</td>
                    <td>{{ relationLabel(edge.type) }}</td>
                    <td>
                      {{ endpointLabel(edge.to) }}
                      @if (edge.to.endpoint === 'unresolved') {
                        <span class="infra__muted">(não resolvido)</span>
                      }
                    </td>
                    <td><atlas-evidence-badge [level]="edgeLevel(edge)" /></td>
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
      .infra {
        padding: var(--atlas-space-5);
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-4);
      }

      .infra__header {
        display: flex;
        flex-wrap: wrap;
        align-items: baseline;
        gap: var(--atlas-space-2) var(--atlas-space-4);
      }

      .infra__header h1 {
        margin: 0;
        font-size: var(--atlas-font-size-xl);
      }

      .infra__description {
        margin: 0;
        color: var(--atlas-color-text-muted);
        flex: 1;
        min-width: 16rem;
        overflow-wrap: anywhere;
      }

      .infra__header button {
        padding: var(--atlas-space-1) var(--atlas-space-3);
        border: 1px solid var(--atlas-color-border-strong);
        border-radius: var(--atlas-radius-sm);
        background: var(--atlas-color-bg-subtle);
        color: var(--atlas-color-text);
        cursor: pointer;
      }

      .infra__empty {
        padding: var(--atlas-space-4);
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        background: var(--atlas-color-bg-subtle);
        color: var(--atlas-color-text-muted);
      }

      .infra__empty h2 {
        margin: 0 0 var(--atlas-space-2);
        font-size: var(--atlas-font-size-md);
        color: var(--atlas-color-text);
      }

      .infra__group {
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-3) var(--atlas-space-4);
        margin-bottom: var(--atlas-space-3);
        background: var(--atlas-color-bg-raised);
      }

      .infra__group h3 {
        margin: 0 0 var(--atlas-space-2);
        font-size: var(--atlas-font-size-md);
      }

      .infra__table {
        width: 100%;
        border-collapse: collapse;
        font-size: var(--atlas-font-size-sm);
      }

      .infra__table th,
      .infra__table td {
        border-bottom: 1px solid var(--atlas-color-border);
        padding: var(--atlas-space-2);
        text-align: left;
        overflow-wrap: anywhere;
      }

      .infra__table th {
        font-size: var(--atlas-font-size-xs);
        color: var(--atlas-color-text-muted);
      }

      .infra__table code {
        font-family: var(--atlas-font-family-mono);
      }

      .infra__muted {
        color: var(--atlas-color-text-muted);
        font-size: var(--atlas-font-size-xs);
      }
    `,
  ],
})
export class InfraComponent implements OnInit, OnDestroy {
  readonly phase = signal<InfraPhase>('loading');
  readonly view = signal<InfraView | null>(null);
  readonly errorText = signal('');

  readonly isEmpty = computed(() => {
    const v = this.view();
    return (
      v !== null &&
      v.modules.length === 0 &&
      v.namespaceGroups.length === 0 &&
      v.iamRoles.length === 0 &&
      v.edges.length === 0
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
    try {
      const scope = {
        repositoryId: this.ctx.activeRepository()?.id,
        snapshotId: this.ctx.activeSnapshot()?.id,
      };
      const [entities, relations] = await Promise.all([
        this.perf.time('GET /domains/entities', () =>
          this.api.getDomainEntities(
            { domain: 'terraform', limit: 200, ...scope },
            { signal: this.controller.signal },
          ),
        ),
        this.perf.time('GET /domains/relations', () =>
          this.api.getDomainRelations(
            { type: INFRA_EDGE_TYPES, limit: 200, ...scope },
            { signal: this.controller.signal },
          ),
        ),
      ]);
      this.view.set(
        buildInfraView(
          entities.items ?? [],
          relations.edges ?? [],
          relations.truncated ?? false,
        ),
      );
      this.phase.set('ready');
    } catch (error) {
      this.view.set(null);
      this.handleError(error);
    }
  }

  allGroups(v: InfraView) {
    return [...v.modules, ...v.namespaceGroups];
  }

  edgeLevel = edgeEvidenceLevel;
  endpointLabel = endpointLabel;
  kindLabel = kindLabel;
  relationLabel = relationLabel;

  private handleError(error: unknown): void {
    if (isApiOffline(error)) {
      this.phase.set('offline');
      return;
    }
    this.errorText.set(apiErrorText(error));
    this.phase.set('error');
  }
}
