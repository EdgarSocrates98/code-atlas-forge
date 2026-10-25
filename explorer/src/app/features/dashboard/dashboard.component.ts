import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  OnInit,
  computed,
  signal,
} from '@angular/core';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../core/api/api.service';
import { apiErrorText, isApiOffline } from '../../shared/errors';
import { StateBlockComponent } from '../../shared/components/state-block/state-block.component';
import { PerfLogService } from '../diagnostics/perf-log.service';
import {
  IndexStatusRow,
  toIndexStatusRow,
} from '../diagnostics/diagnostics.vm';

type DashboardPhase = 'loading' | 'ready' | 'error' | 'offline';

interface QuickLink {
  path: string;
  title: string;
  description: string;
}

const QUICK_LINKS: QuickLink[] = [
  {
    path: '/search',
    title: 'Busca',
    description: 'Consulta híbrida sobre o índice com evidência por ocorrência.',
  },
  {
    path: '/impact',
    title: 'Impacto',
    description: 'Raio de impacto entre revisões, com risco e incerteza separados.',
  },
  {
    path: '/revisions',
    title: 'Revisões',
    description: 'Diff de símbolos entre snapshots indexados.',
  },
  {
    path: '/context',
    title: 'Context Packs',
    description: 'Pacotes de contexto com orçamento de tokens para uma tarefa.',
  },
  {
    path: '/diagnostics',
    title: 'Diagnósticos',
    description: 'Saúde da API, capacidades e contagens do índice.',
  },
];

@Component({
  selector: 'atlas-dashboard',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, StateBlockComponent],
  template: `
    <section class="dashboard" aria-labelledby="dashboard-title">
      <header class="dashboard__header">
        <h1 id="dashboard-title">Painel</h1>
        <p class="dashboard__description">
          Visão geral do índice local do Code Atlas — tudo loopback, nada sai da
          máquina.
        </p>
      </header>

      @if (phase() !== 'ready') {
        <atlas-state-block
          [state]="blockState()"
          loadingMessage="Consultando o índice local…"
          [errorMessage]="errorText()"
        />
      } @else {
        <div class="dashboard__cards" aria-label="Resumo do índice">
          <div class="dashboard__card">
            <h2>API local</h2>
            <p class="dashboard__metric dashboard__metric--ok">online</p>
          </div>
          <div class="dashboard__card">
            <h2>Repositórios</h2>
            <p class="dashboard__metric">{{ rows().length }}</p>
          </div>
          <div class="dashboard__card">
            <h2>Snapshots READY</h2>
            <p class="dashboard__metric">{{ totalReady() }}</p>
          </div>
          <div class="dashboard__card">
            <h2>Símbolos indexados</h2>
            <p class="dashboard__metric">{{ totalSymbols() }}</p>
          </div>
        </div>

        @if (rows().length > 0) {
          <section class="dashboard__panel" aria-labelledby="dash-index">
            <h2 id="dash-index">Repositórios indexados</h2>
            <table class="dashboard__table">
              <thead>
                <tr>
                  <th scope="col">Repositório</th>
                  <th scope="col">Snapshots prontos</th>
                  <th scope="col">Último READY</th>
                  <th scope="col">Ocorrências</th>
                </tr>
              </thead>
              <tbody>
                @for (row of rows(); track row.repositoryId) {
                  <tr>
                    <td><code>{{ row.rootHandle }}</code></td>
                    <td>{{ row.readySnapshots }}</td>
                    <td>{{ row.latestReady ?? '—' }}</td>
                    <td>{{ occurrences(row) }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </section>
        } @else {
          <p class="dashboard__empty">
            Nenhum repositório indexado — rode
            <code>code-atlas index --root &lt;path&gt;</code> e depois
            <code>code-atlas serve</code>.
          </p>
        }

        <nav class="dashboard__links" aria-label="Atalhos">
          @for (link of quickLinks; track link.path) {
            <a [routerLink]="link.path" class="dashboard__link-card">
              <strong>{{ link.title }}</strong>
              <span>{{ link.description }}</span>
            </a>
          }
        </nav>
      }
    </section>
  `,
  styles: [
    `
      .dashboard {
        padding: var(--atlas-space-5);
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-4);
      }

      .dashboard__header h1 {
        margin: 0 0 var(--atlas-space-2);
        font-size: var(--atlas-font-size-xl);
      }

      .dashboard__description {
        margin: 0;
        color: var(--atlas-color-text-muted);
      }

      .dashboard__cards {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
        gap: var(--atlas-space-3);
      }

      .dashboard__card {
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-3) var(--atlas-space-4);
        background: var(--atlas-color-bg-raised);
      }

      .dashboard__card h2 {
        margin: 0 0 var(--atlas-space-1);
        font-size: var(--atlas-font-size-sm);
        color: var(--atlas-color-text-muted);
        font-weight: var(--atlas-font-weight-regular);
      }

      .dashboard__metric {
        margin: 0;
        font-size: var(--atlas-font-size-xl);
        font-weight: var(--atlas-font-weight-bold);
      }

      .dashboard__metric--ok {
        color: var(--atlas-evidence-verified);
      }

      .dashboard__panel {
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-4);
        background: var(--atlas-color-bg-raised);
      }

      .dashboard__panel h2 {
        margin: 0 0 var(--atlas-space-3);
        font-size: var(--atlas-font-size-lg);
      }

      .dashboard__table {
        width: 100%;
        border-collapse: collapse;
        font-size: var(--atlas-font-size-sm);
      }

      .dashboard__table th,
      .dashboard__table td {
        text-align: left;
        padding: var(--atlas-space-1) var(--atlas-space-2);
        border-bottom: 1px solid var(--atlas-color-border);
      }

      .dashboard__empty {
        color: var(--atlas-color-text-muted);
      }

      .dashboard__links {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
        gap: var(--atlas-space-3);
      }

      .dashboard__link-card {
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-1);
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-3) var(--atlas-space-4);
        text-decoration: none;
        color: var(--atlas-color-text);
        background: var(--atlas-color-bg-raised);
      }

      .dashboard__link-card:hover {
        border-color: var(--atlas-color-accent);
      }

      .dashboard__link-card span {
        color: var(--atlas-color-text-muted);
        font-size: var(--atlas-font-size-sm);
      }
    `,
  ],
})
export class DashboardComponent implements OnInit, OnDestroy {
  readonly quickLinks = QUICK_LINKS;

  readonly phase = signal<DashboardPhase>('loading');
  readonly rows = signal<IndexStatusRow[]>([]);
  readonly errorText = signal('');

  readonly blockState = computed(() => {
    switch (this.phase()) {
      case 'loading':
        return 'loading' as const;
      case 'offline':
        return 'offline' as const;
      default:
        return 'error' as const;
    }
  });
  readonly totalReady = computed(() =>
    this.rows().reduce((sum, row) => sum + row.readySnapshots, 0),
  );
  readonly totalSymbols = computed(() =>
    this.rows().reduce(
      (sum, row) =>
        sum +
        (row.counts.find((c) => c.key === 'symbol_occurrences')?.value ?? 0),
      0,
    ),
  );

  private readonly controller = new AbortController();

  constructor(
    private readonly api: ApiService,
    private readonly perf: PerfLogService,
  ) {}

  ngOnInit(): void {
    void this.load();
  }

  ngOnDestroy(): void {
    this.controller.abort();
  }

  async load(): Promise<void> {
    this.phase.set('loading');
    this.errorText.set('');
    try {
      await this.perf.time('GET /health', () =>
        this.api.getHealth({ signal: this.controller.signal }),
      );
      const index = await this.perf.time('GET /index/status', () =>
        this.api.getIndexStatus({ signal: this.controller.signal }),
      );
      this.rows.set((index.items ?? []).map(toIndexStatusRow));
      this.phase.set('ready');
    } catch (error) {
      this.errorText.set(apiErrorText(error));
      this.phase.set(isApiOffline(error) ? 'offline' : 'error');
    }
  }

  occurrences(row: IndexStatusRow): number | string {
    return row.counts.find((c) => c.key === 'symbol_occurrences')?.value ?? '—';
  }
}
