import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  OnInit,
  computed,
  signal,
} from '@angular/core';
import {
  ApiHealthResponse,
  ApiService,
  ApiSnapshotItem,
} from '../../core/api/api.service';
import { apiErrorText, isApiOffline } from '../../shared/errors';
import { GlobalErrorHandler } from '../../core/errors/global-error-handler';
import { StateBlockComponent } from '../../shared/components/state-block/state-block.component';
import { PerfLogService } from './perf-log.service';
import {
  CapabilitiesView,
  IndexStatusRow,
  toCapabilitiesView,
  toIndexStatusRow,
} from './diagnostics.vm';

type DiagPhase = 'loading' | 'ready' | 'error' | 'offline';

/**
 * Diagnostics center — health, capabilities, index status, snapshot lists
 * and the in-memory call-latency log. Everything is loopback-local: the
 * timings list lives only in a signal for the lifetime of the app shell.
 */
@Component({
    selector: 'atlas-diagnostics',
    changeDetection: ChangeDetectionStrategy.OnPush,
    imports: [StateBlockComponent],
    template: `
    <section class="diagnostics" aria-labelledby="diagnostics-title">
      <header class="diagnostics__header">
        <h1 id="diagnostics-title">Diagnósticos</h1>
        <p class="diagnostics__description">
          Saúde da API local, capacidades do backend e contagens do índice.
        </p>
      </header>

      @if (errorHandler.lastError(); as last) {
        <div class="diagnostics__last-error" role="alert">
          <h2>Último erro capturado</h2>
          <p>{{ last.message }}</p>
        </div>
      }

      <section class="diagnostics__panel" aria-labelledby="diag-health">
        <h2 id="diag-health">API local</h2>
        <div class="diagnostics__health">
          <span
            class="health-dot health-dot--{{ healthState() }}"
            role="img"
            [attr.aria-label]="healthLabel()"
          ></span>
          <span>{{ healthLabel() }}</span>
          @if (health(); as h) {
            <span class="diagnostics__muted">
              — {{ h.service }} v{{ h.version }} · status “{{ h.status }}”
            </span>
          }
          <button type="button" (click)="refresh()" [disabled]="phase() === 'loading'">
            {{ phase() === 'loading' ? 'Verificando…' : 'Verificar novamente' }}
          </button>
        </div>
        @if (healthError()) {
          <p class="diagnostics__muted">{{ healthError() }}</p>
        }
      </section>

      @if (showBlock()) {
        <atlas-state-block
          [state]="blockState()"
          loadingMessage="Consultando o backend local…"
          [errorMessage]="errorText()"
        />
      }

      @if (capabilities(); as caps) {
        <section class="diagnostics__panel" aria-labelledby="diag-caps">
          <h2 id="diag-caps">Capacidades</h2>
          <ul class="diagnostics__caps">
            <li>
              <span
                class="cap-badge"
                [class.cap-badge--off]="!caps.vector"
              >
                {{ caps.vectorLabel }}
              </span>
            </li>
            <li>
              <span class="cap-badge" [class.cap-badge--off]="!caps.fts5">
                FTS5 {{ caps.fts5 ? 'disponível' : 'indisponível' }}
              </span>
            </li>
            <li>
              <span class="cap-badge">
                API v{{ caps.apiVersion }} · {{ caps.readonly ? 'somente leitura' : 'escrita' }}
              </span>
            </li>
          </ul>
          <p class="diagnostics__muted">
            Estratégias: {{ caps.strategies.join(', ') || '—' }}
            @if (caps.detail) {
              · {{ caps.detail }}
            }
          </p>
        </section>
      }

      @if (indexRows().length > 0) {
        <section class="diagnostics__panel" aria-labelledby="diag-index">
          <h2 id="diag-index">Índice por repositório</h2>
          <table class="diagnostics__table">
            <thead>
              <tr>
                <th scope="col">Repositório</th>
                <th scope="col">Snapshots prontos</th>
                <th scope="col">Último READY</th>
                <th scope="col">Contagens</th>
                <th scope="col">Snapshots</th>
              </tr>
            </thead>
            <tbody>
              @for (row of indexRows(); track row.repositoryId) {
                <tr>
                  <td><code>{{ row.rootHandle }}</code></td>
                  <td>{{ row.readySnapshots }}</td>
                  <td>{{ row.latestReady ?? '—' }}</td>
                  <td>
                    @if (row.hasCounts) {
                      <ul class="diagnostics__counts">
                        @for (count of row.counts; track count.key) {
                          <li>{{ count.label }}: {{ count.value ?? '—' }}</li>
                        }
                      </ul>
                    } @else {
                      <span class="diagnostics__muted">sem contagens (nenhum snapshot READY)</span>
                    }
                  </td>
                  <td>
                    <button
                      type="button"
                      class="diagnostics__link"
                      (click)="toggleSnapshots(row.repositoryId)"
                      [attr.aria-expanded]="expandedRepo() === row.repositoryId"
                      [attr.aria-controls]="'snapshots-' + row.repositoryId"
                    >
                      {{ expandedRepo() === row.repositoryId ? 'Ocultar' : 'Listar' }}
                    </button>
                  </td>
                </tr>
                @if (expandedRepo() === row.repositoryId) {
                  <tr>
                    <td colspan="5" [id]="'snapshots-' + row.repositoryId">
                      @if (snapshotsLoading()) {
                        <p role="status">Carregando snapshots…</p>
                      } @else if (snapshots().length === 0) {
                        <p class="diagnostics__muted">Nenhum snapshot registrado.</p>
                      } @else {
                        <table class="diagnostics__table diagnostics__table--nested">
                          <thead>
                            <tr>
                              <th scope="col">#</th>
                              <th scope="col">Status</th>
                              <th scope="col">Commit</th>
                              <th scope="col">Ref</th>
                              <th scope="col">Criado em</th>
                            </tr>
                          </thead>
                          <tbody>
                            @for (snap of snapshots(); track snap.id) {
                              <tr>
                                <td>{{ snap.id }}</td>
                                <td>{{ snap.status }}</td>
                                <td><code>{{ snap.commit_sha?.slice(0, 12) || '—' }}</code></td>
                                <td>{{ snap.git_ref || '—' }}</td>
                                <td>{{ snap.created_at }}</td>
                              </tr>
                            }
                          </tbody>
                        </table>
                      }
                    </td>
                  </tr>
                }
              }
            </tbody>
          </table>
        </section>
      }

      <section class="diagnostics__panel" aria-labelledby="diag-perf">
        <div class="diagnostics__perf-head">
          <h2 id="diag-perf">Latências recentes (memória local)</h2>
          <button
            type="button"
            class="diagnostics__link"
            (click)="perf.clear()"
            [disabled]="perf.entries().length === 0"
          >
            Limpar
          </button>
        </div>
        @if (perf.entries().length === 0) {
          <p class="diagnostics__muted">
            Nenhuma chamada registrada ainda — navegue pelas telas e volte aqui.
          </p>
        } @else {
          <table class="diagnostics__table">
            <thead>
              <tr>
                <th scope="col">Chamada</th>
                <th scope="col">Latência</th>
                <th scope="col">Resultado</th>
                <th scope="col">Horário</th>
              </tr>
            </thead>
            <tbody>
              @for (entry of perf.entries(); track entry.at + entry.label) {
                <tr>
                  <td><code>{{ entry.label }}</code></td>
                  <td>{{ entry.durationMs }} ms</td>
                  <td>{{ entry.ok ? 'ok' : 'falhou' }}</td>
                  <td>{{ formatTime(entry.at) }}</td>
                </tr>
              }
            </tbody>
          </table>
        }
      </section>
    </section>
  `,
    styles: [
        `
      .diagnostics {
        padding: var(--atlas-space-5);
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-4);
      }

      .diagnostics__header h1 {
        margin: 0 0 var(--atlas-space-2);
        font-size: var(--atlas-font-size-xl);
      }

      .diagnostics__description {
        margin: 0;
        color: var(--atlas-color-text-muted);
      }

      .diagnostics__last-error {
        padding: var(--atlas-space-3) var(--atlas-space-4);
        border: 1px solid var(--atlas-color-danger);
        background: var(--atlas-color-danger-bg);
        color: var(--atlas-color-danger);
        border-radius: var(--atlas-radius-md);
      }

      .diagnostics__last-error h2 {
        margin: 0 0 var(--atlas-space-2);
        font-size: var(--atlas-font-size-md);
      }

      .diagnostics__panel {
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-4);
        background: var(--atlas-color-bg-raised);
      }

      .diagnostics__panel h2 {
        margin: 0 0 var(--atlas-space-3);
        font-size: var(--atlas-font-size-lg);
      }

      .diagnostics__health {
        display: flex;
        align-items: center;
        gap: var(--atlas-space-3);
        flex-wrap: wrap;
      }

      .health-dot {
        width: 0.75rem;
        height: 0.75rem;
        border-radius: 50%;
        background: var(--atlas-color-text-muted);
      }

      .health-dot--ok {
        background: var(--atlas-evidence-verified);
      }

      .health-dot--down {
        background: var(--atlas-evidence-unresolved);
      }

      .health-dot--checking {
        background: var(--atlas-evidence-possible);
      }

      .diagnostics__health button {
        padding: var(--atlas-space-1) var(--atlas-space-3);
        border: 1px solid var(--atlas-color-border-strong);
        border-radius: var(--atlas-radius-sm);
        background: var(--atlas-color-bg-subtle);
        color: var(--atlas-color-text);
        cursor: pointer;
      }

      .diagnostics__caps {
        margin: 0 0 var(--atlas-space-2);
        padding: 0;
        list-style: none;
        display: flex;
        gap: var(--atlas-space-2);
        flex-wrap: wrap;
      }

      .cap-badge {
        display: inline-block;
        padding: var(--atlas-space-1) var(--atlas-space-3);
        border-radius: var(--atlas-radius-full);
        border: 1px solid var(--atlas-evidence-verified);
        color: var(--atlas-evidence-verified);
        background: var(--atlas-evidence-verified-bg);
        font-size: var(--atlas-font-size-sm);
      }

      .cap-badge--off {
        border-color: var(--atlas-color-border-strong);
        color: var(--atlas-color-text-muted);
        background: var(--atlas-color-bg-subtle);
      }

      .diagnostics__table {
        width: 100%;
        border-collapse: collapse;
        font-size: var(--atlas-font-size-sm);
      }

      .diagnostics__table th,
      .diagnostics__table td {
        text-align: left;
        vertical-align: top;
        padding: var(--atlas-space-1) var(--atlas-space-2);
        border-bottom: 1px solid var(--atlas-color-border);
        overflow-wrap: anywhere;
      }

      .diagnostics__table--nested {
        margin-top: var(--atlas-space-2);
      }

      .diagnostics__counts {
        margin: 0;
        padding: 0;
        list-style: none;
      }

      .diagnostics__link {
        background: none;
        border: 0;
        padding: 0;
        color: var(--atlas-color-accent);
        cursor: pointer;
        font: inherit;
      }

      .diagnostics__link:disabled {
        color: var(--atlas-color-text-muted);
        cursor: not-allowed;
      }

      .diagnostics__perf-head {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: var(--atlas-space-3);
      }

      .diagnostics__muted {
        color: var(--atlas-color-text-muted);
        font-size: var(--atlas-font-size-sm);
      }
    `,
    ]
})
export class DiagnosticsComponent implements OnInit, OnDestroy {
  readonly phase = signal<DiagPhase>('loading');
  readonly health = signal<ApiHealthResponse | null>(null);
  readonly healthError = signal('');
  readonly capabilities = signal<CapabilitiesView | null>(null);
  readonly indexRows = signal<IndexStatusRow[]>([]);
  readonly errorText = signal('');
  readonly expandedRepo = signal<number | null>(null);
  readonly snapshots = signal<ApiSnapshotItem[]>([]);
  readonly snapshotsLoading = signal(false);

  readonly healthState = computed(() => {
    if (this.phase() === 'offline' || (this.phase() === 'error' && !this.health())) {
      return 'down';
    }
    if (this.health()) return 'ok';
    return this.phase() === 'loading' ? 'checking' : 'down';
  });
  readonly healthLabel = computed(() => {
    switch (this.healthState()) {
      case 'ok':
        return 'API local online';
      case 'checking':
        return 'Verificando API local…';
      default:
        return 'API local indisponível';
    }
  });

  readonly showBlock = computed(
    () => this.phase() === 'error' || this.phase() === 'offline',
  );
  readonly blockState = computed(() =>
    this.phase() === 'offline' ? ('offline' as const) : ('error' as const),
  );

  private readonly controller = new AbortController();

  constructor(
    private readonly api: ApiService,
    readonly errorHandler: GlobalErrorHandler,
    readonly perf: PerfLogService,
  ) {}

  ngOnInit(): void {
    void this.refresh();
  }

  ngOnDestroy(): void {
    this.controller.abort();
  }

  async refresh(): Promise<void> {
    this.phase.set('loading');
    this.errorText.set('');
    this.healthError.set('');

    // Health is probed independently so a failure still shows a clear state.
    try {
      const health = await this.perf.time('GET /health', () =>
        this.api.getHealth({ signal: this.controller.signal }),
      );
      this.health.set(health);
    } catch (error) {
      this.health.set(null);
      this.healthError.set(apiErrorText(error));
      this.phase.set(isApiOffline(error) ? 'offline' : 'error');
      return;
    }

    try {
      const [caps, index] = await Promise.all([
        this.perf.time('GET /capabilities', () =>
          this.api.getCapabilities({ signal: this.controller.signal }),
        ),
        this.perf.time('GET /index/status', () =>
          this.api.getIndexStatus({ signal: this.controller.signal }),
        ),
      ]);
      this.capabilities.set(toCapabilitiesView(caps));
      this.indexRows.set((index.items ?? []).map(toIndexStatusRow));
      this.phase.set('ready');
    } catch (error) {
      this.errorText.set(apiErrorText(error));
      this.phase.set(isApiOffline(error) ? 'offline' : 'error');
    }
  }

  async toggleSnapshots(repositoryId: number): Promise<void> {
    if (this.expandedRepo() === repositoryId) {
      this.expandedRepo.set(null);
      return;
    }
    this.expandedRepo.set(repositoryId);
    this.snapshots.set([]);
    this.snapshotsLoading.set(true);
    try {
      const res = await this.perf.time('GET /repositories/{id}/snapshots', () =>
        this.api.listSnapshots(repositoryId, { signal: this.controller.signal }),
      );
      this.snapshots.set(res.items ?? []);
    } catch (error) {
      this.errorText.set(apiErrorText(error));
      if (isApiOffline(error)) this.phase.set('offline');
    } finally {
      this.snapshotsLoading.set(false);
    }
  }

  formatTime(at: number): string {
    return new Date(at).toLocaleTimeString('pt-BR');
  }
}
