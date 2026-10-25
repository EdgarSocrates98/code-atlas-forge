import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  OnInit,
  computed,
  effect,
  signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router } from '@angular/router';
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
  LINEAGE_ENTITY_KINDS,
  domainLabel,
  edgeEvidenceLevel,
  endpointDetail,
  endpointLabel,
  entityLabel,
  kindLabel,
  relationLabel,
} from '../domain.utils';
import { LineageView, edgeTouchesRef, toLineageView } from './lineage.vm';

type LineagePhase = 'idle' | 'loading' | 'ready' | 'error' | 'offline';

/**
 * Data lineage view (lf-0122): pick a data entity and traverse its
 * upstream/downstream flow over the snapshot's domain_relations.
 * Table-first, evidence on every edge — no canvas, no invented relations.
 */
@Component({
  selector: 'atlas-lineage',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, StateBlockComponent, EvidenceBadgeComponent],
  template: `
    <section class="lineage" aria-labelledby="lineage-title">
      <header class="lineage__header">
        <h1 id="lineage-title">Linhagem de dados</h1>
        <p class="lineage__description">
          Fluxo de dados observado no snapshot ativo — arestas
          READS_FROM/WRITES_TO/TRANSFORMS/DERIVES_FROM com a evidência de
          cada relação. Alvos não resolvidos aparecem como tal.
        </p>
      </header>

      <form
        class="lineage__form"
        (ngSubmit)="applyFilter()"
        aria-label="Seleção de tabela para linhagem"
      >
        <label class="lineage__field lineage__field--grow">
          <span>Tabela / entidade de dados</span>
          <select
            name="table"
            [ngModel]="selectedId()"
            (ngModelChange)="selectEntity($event)"
            [disabled]="tablesPhase() === 'loading'"
            aria-label="Tabela de dados"
          >
            <option [ngValue]="null" disabled>
              {{ tablesPhase() === 'loading' ? 'Carregando…' : 'Selecione…' }}
            </option>
            @for (entity of tables(); track entity.id) {
              <option [ngValue]="entity.id">
                {{ entityLabel(entity) }} ({{ kindLabel(entity.kind) }})
              </option>
            }
          </select>
        </label>
        <label class="lineage__field">
          <span>Filtrar nomes</span>
          <input
            type="search"
            name="q"
            [(ngModel)]="filterText"
            placeholder="parte do nome"
            autocomplete="off"
          />
        </label>
        <div class="lineage__actions">
          <button type="submit" [disabled]="tablesPhase() === 'loading'">
            Filtrar
          </button>
        </div>
      </form>

      @if (showBlock()) {
        <atlas-state-block
          [state]="blockState()"
          loadingMessage="Consultando linhagem no snapshot ativo…"
          [errorMessage]="errorText()"
        />
      }

      @if (tablesPhase() === 'ready' && tables().length === 0) {
        <div class="lineage__empty" aria-live="polite">
          <h2>Nenhuma entidade de dados neste snapshot</h2>
          <p>
            O índice não registrou tabelas/views/transformações para o
            repositório ativo. A linhagem depende dos adapters de dados
            (SQL, AWS Data, PySpark) habilitados durante
            <code>code-atlas index</code>.
          </p>
        </div>
      }

      @if (view(); as lineage) {
        <article class="lineage__result" aria-live="polite">
          <header class="lineage__root">
            <h2>{{ endpointLabel(lineage.root) }}</h2>
            <p class="lineage__muted">
              {{ kindLabel(lineage.root.entity_kind ?? '') }} ·
              {{ domainLabel(lineage.root.domain ?? '') }}
              @if (lineage.truncated) {
                · resultado truncado pelo limite de profundidade/arestas
              }
            </p>
          </header>

          <div class="lineage__lanes">
            <section class="lineage__lane" aria-labelledby="lineage-upstream">
              <h3 id="lineage-upstream">
                Origem (upstream) — {{ lineage.upstream.length }}
              </h3>
              @if (lineage.upstream.length === 0) {
                <p class="lineage__muted">Nenhuma origem conhecida.</p>
              }
              <ul>
                @for (node of lineage.upstream; track node.ref) {
                  <li>
                    <span class="lineage__distance">d{{ node.distance }}</span>
                    <strong>{{ endpointLabel(node) }}</strong>
                    <span class="lineage__muted">{{ endpointDetail(node) }}</span>
                  </li>
                }
              </ul>
            </section>

            <section class="lineage__lane" aria-labelledby="lineage-downstream">
              <h3 id="lineage-downstream">
                Destino (downstream) — {{ lineage.downstream.length }}
              </h3>
              @if (lineage.downstream.length === 0) {
                <p class="lineage__muted">Nenhum destino conhecido.</p>
              }
              <ul>
                @for (node of lineage.downstream; track node.ref) {
                  <li>
                    <span class="lineage__distance">d{{ node.distance }}</span>
                    <strong>{{ endpointLabel(node) }}</strong>
                    <span class="lineage__muted">{{ endpointDetail(node) }}</span>
                  </li>
                }
              </ul>
            </section>
          </div>

          <table class="lineage__edges">
            <caption>
              Relações da linhagem — {{ lineage.edges.length }}
            </caption>
            <thead>
              <tr>
                <th scope="col">De</th>
                <th scope="col">Relação</th>
                <th scope="col">Para</th>
                <th scope="col">Evidência</th>
                <th scope="col">Resolvedor</th>
              </tr>
            </thead>
            <tbody>
              @for (edge of lineage.edges; track edge.id) {
                <tr>
                  <td>{{ endpointLabel(edge.from) }}</td>
                  <td>{{ relationLabel(edge.type) }}</td>
                  <td>
                    {{ endpointLabel(edge.to) }}
                    @if (edge.to.endpoint === 'unresolved') {
                      <span class="lineage__muted">(não resolvido)</span>
                    }
                  </td>
                  <td>
                    <atlas-evidence-badge [level]="edgeLevel(edge)" />
                  </td>
                  <td class="lineage__muted">{{ edge.resolver }}</td>
                </tr>
              }
            </tbody>
          </table>
        </article>
      }
    </section>
  `,
  styles: [
    `
      .lineage {
        padding: var(--atlas-space-5);
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-4);
      }

      .lineage__header h1 {
        margin: 0 0 var(--atlas-space-2);
        font-size: var(--atlas-font-size-xl);
      }

      .lineage__description {
        margin: 0;
        color: var(--atlas-color-text-muted);
        max-width: 72ch;
      }

      .lineage__form {
        display: flex;
        flex-wrap: wrap;
        align-items: end;
        gap: var(--atlas-space-3);
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-3) var(--atlas-space-4);
        background: var(--atlas-color-bg-raised);
      }

      .lineage__field {
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-1);
        font-size: var(--atlas-font-size-sm);
        min-width: 12rem;
      }

      .lineage__field--grow {
        flex: 1;
      }

      .lineage__field select,
      .lineage__field input {
        padding: var(--atlas-space-2);
        border: 1px solid var(--atlas-color-border-strong);
        border-radius: var(--atlas-radius-sm);
        background: var(--atlas-color-bg);
        color: var(--atlas-color-text);
        font: inherit;
      }

      .lineage__actions button {
        padding: var(--atlas-space-2) var(--atlas-space-4);
        border: 0;
        border-radius: var(--atlas-radius-sm);
        background: var(--atlas-color-accent);
        color: var(--atlas-color-accent-text);
        cursor: pointer;
      }

      .lineage__empty {
        padding: var(--atlas-space-4);
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        background: var(--atlas-color-bg-subtle);
        color: var(--atlas-color-text-muted);
      }

      .lineage__empty h2 {
        margin: 0 0 var(--atlas-space-2);
        font-size: var(--atlas-font-size-md);
        color: var(--atlas-color-text);
      }

      .lineage__root h2 {
        margin: 0;
        font-size: var(--atlas-font-size-lg);
      }

      .lineage__lanes {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(18rem, 1fr));
        gap: var(--atlas-space-3);
        margin: var(--atlas-space-3) 0;
      }

      .lineage__lane {
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-3) var(--atlas-space-4);
        background: var(--atlas-color-bg-raised);
      }

      .lineage__lane h3 {
        margin: 0 0 var(--atlas-space-2);
        font-size: var(--atlas-font-size-md);
      }

      .lineage__lane ul {
        margin: 0;
        padding: 0;
        list-style: none;
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-2);
      }

      .lineage__lane li {
        display: flex;
        align-items: baseline;
        gap: var(--atlas-space-2);
        flex-wrap: wrap;
      }

      .lineage__distance {
        font-family: var(--atlas-font-family-mono);
        font-size: var(--atlas-font-size-xs);
        color: var(--atlas-color-text-muted);
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-sm);
        padding: 0 var(--atlas-space-1);
      }

      .lineage__edges {
        width: 100%;
        border-collapse: collapse;
        font-size: var(--atlas-font-size-sm);
      }

      .lineage__edges caption {
        text-align: left;
        color: var(--atlas-color-text-muted);
        margin-bottom: var(--atlas-space-2);
      }

      .lineage__edges th,
      .lineage__edges td {
        border-bottom: 1px solid var(--atlas-color-border);
        padding: var(--atlas-space-2);
        text-align: left;
        overflow-wrap: anywhere;
      }

      .lineage__edges th {
        font-size: var(--atlas-font-size-xs);
        color: var(--atlas-color-text-muted);
      }

      .lineage__muted {
        color: var(--atlas-color-text-muted);
        font-size: var(--atlas-font-size-sm);
      }
    `,
  ],
})
export class LineageComponent implements OnInit, OnDestroy {
  readonly tables = signal<ApiDomainEntity[]>([]);
  readonly tablesPhase = signal<'loading' | 'ready'>('loading');
  readonly selectedId = signal<number | null>(null);
  readonly phase = signal<LineagePhase>('idle');
  readonly view = signal<LineageView | null>(null);
  readonly errorText = signal('');
  filterText = '';

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
  private pendingTableParam: string | null = null;

  constructor(
    private readonly api: ApiService,
    private readonly perf: PerfLogService,
    private readonly route: ActivatedRoute,
    private readonly router: Router,
    readonly ctx: ExplorerContextService,
  ) {
    effect(() => {
      const key = `${this.ctx.activeRepository()?.id ?? ''}:${this.ctx.activeSnapshot()?.id ?? ''}`;
      if (key === this.scopeKey) return;
      this.scopeKey = key;
      if (!key.replace(':', '')) return;
      // defer writes — signal updates are not allowed inside effect()
      queueMicrotask(() => {
        if (this.scopeKey === key) this.onScopeChange();
      });
    });
  }

  ngOnInit(): void {
    this.pendingTableParam = this.route.snapshot.queryParamMap.get('table');
    void this.ctx.init();
  }

  ngOnDestroy(): void {
    this.controller.abort();
  }

  private onScopeChange(): void {
    this.selectedId.set(null);
    this.view.set(null);
    this.phase.set('idle');
    void this.loadTables();
  }

  async loadTables(): Promise<void> {
    this.tablesPhase.set('loading');
    try {
      const res = await this.perf.time('GET /domains/entities', () =>
        this.api.getDomainEntities(
          {
            kind: LINEAGE_ENTITY_KINDS,
            q: this.filterText.trim() || undefined,
            limit: 200,
            repositoryId: this.ctx.activeRepository()?.id,
            snapshotId: this.ctx.activeSnapshot()?.id,
          },
          { signal: this.controller.signal },
        ),
      );
      this.tables.set(res.items ?? []);
      this.tablesPhase.set('ready');
      this.applyPendingTableParam();
    } catch (error) {
      this.tablesPhase.set('ready');
      this.tables.set([]);
      this.handleError(error);
    }
  }

  applyFilter(): void {
    void this.loadTables();
  }

  selectEntity(id: number | null): void {
    this.selectedId.set(id);
    this.syncUrl();
    if (id === null) {
      this.view.set(null);
      this.phase.set('idle');
      return;
    }
    void this.loadLineage(id);
  }

  async loadLineage(entityId: number): Promise<void> {
    this.phase.set('loading');
    this.errorText.set('');
    try {
      const res = await this.perf.time('GET /domains/lineage', () =>
        this.api.getDomainLineage(entityId, 'both', undefined, this.scope(), {
          signal: this.controller.signal,
        }),
      );
      this.view.set(toLineageView(res));
      this.phase.set('ready');
    } catch (error) {
      this.view.set(null);
      this.handleError(error);
    }
  }

  edgesFor(nodeRef: string) {
    return this.view()?.edges.filter((e) => edgeTouchesRef(e, nodeRef)) ?? [];
  }

  edgeLevel = edgeEvidenceLevel;
  endpointLabel = endpointLabel;
  endpointDetail = endpointDetail;
  entityLabel = entityLabel;
  kindLabel = kindLabel;
  domainLabel = domainLabel;
  relationLabel = relationLabel;

  private scope() {
    return {
      repositoryId: this.ctx.activeRepository()?.id,
      snapshotId: this.ctx.activeSnapshot()?.id,
    };
  }

  private applyPendingTableParam(): void {
    const wanted = this.pendingTableParam;
    if (wanted === null) return;
    this.pendingTableParam = null;
    const match = this.tables().find(
      (e) => String(e.id) === wanted || e.qualified_name === wanted,
    );
    if (match) {
      this.selectEntity(match.id);
      return;
    }
    if (!/^\d+$/.test(wanted) && wanted.length <= 300) {
      // qualified_name exato — o backend resolve e responde honesto.
      void this.loadLineageByName(wanted);
    }
  }

  private async loadLineageByName(qname: string): Promise<void> {
    this.phase.set('loading');
    this.errorText.set('');
    try {
      const res = await this.perf.time('GET /domains/lineage', () =>
        this.api.getDomainLineage(qname, 'both', undefined, this.scope(), {
          signal: this.controller.signal,
        }),
      );
      this.view.set(toLineageView(res));
      this.phase.set('ready');
    } catch (error) {
      this.view.set(null);
      this.handleError(error);
    }
  }

  private syncUrl(): void {
    const queryParams: Record<string, string | number> = {
      ...this.ctx.contextQueryParams(),
    };
    const id = this.selectedId();
    if (id !== null) queryParams['table'] = id;
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams,
      replaceUrl: true,
    });
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
