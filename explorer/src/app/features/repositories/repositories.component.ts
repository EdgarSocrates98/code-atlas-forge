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
import type { ApiSnapshotItem } from '../../core/api/api.service';
import { ExplorerContextService } from '../../core/state/explorer-context.service';
import { apiErrorText, isApiOffline } from '../../shared/errors';
import {
  BlockState,
  StateBlockComponent,
} from '../../shared/components/state-block/state-block.component';
import { PerfLogService } from '../diagnostics/perf-log.service';
import {
  RepositoryRow,
  buildRepositoryRows,
  shortSha,
} from './repositories.vm';

type RepositoriesPhase = 'loading' | 'ready' | 'error' | 'offline';

interface CopyFeedback {
  repositoryId: number;
  ok: boolean;
}

/**
 * Repository control center (lf-0097): the real list of repositories known
 * to the local index, their latest READY snapshot and honest index counts.
 * Selection drives the global explorer context; action links build deep
 * links that carry `?repo=&snapshot=` for context-aware navigation.
 * Read-only: nothing here writes to the API — indexing happens exclusively
 * via `code-atlas index` on the CLI.
 */
@Component({
    selector: 'atlas-repositories',
    changeDetection: ChangeDetectionStrategy.OnPush,
    imports: [RouterLink, StateBlockComponent],
    template: `
    <section class="repositories" aria-labelledby="repositories-title">
      <header class="repositories__header">
        <h1 id="repositories-title">Repositórios</h1>
        <p class="repositories__description">
          Repositórios conhecidos pelo índice local. Selecionar um repositório
          define o contexto global do Explorer.
        </p>
        <button
          type="button"
          class="repositories__refresh"
          (click)="load()"
          [disabled]="phase() === 'loading'"
        >
          {{ phase() === 'loading' ? 'Atualizando…' : 'Atualizar' }}
        </button>
      </header>

      @if (showBlock()) {
        <atlas-state-block
          [state]="blockState()"
          loadingMessage="Consultando repositórios no índice local…"
          [errorMessage]="errorText()"
        />
      }

      @if (phase() === 'ready' && rows().length === 0) {
        <div class="repositories__empty" aria-live="polite">
          <h2>Nenhum repositório indexado</h2>
          <p>
            O índice local está vazio. Indexe um diretório com
            <code>code-atlas index &lt;caminho&gt;</code> e recarregue esta página
            — o Explorer lê apenas o índice gerado pela CLI.
          </p>
        </div>
      }

      @if (rows().length > 0) {
        <ul class="repositories__list" aria-label="Repositórios indexados">
          @for (row of rows(); track row.repository.id) {
            <li
              class="repo-card"
              [class.repo-card--active]="isActive(row)"
            >
              <div class="repo-card__head">
                <code class="repo-card__path">{{ row.repository.root_handle }}</code>
                @if (isActive(row)) {
                  <span class="repo-card__badge">contexto ativo</span>
                }
              </div>

              <dl class="repo-card__meta">
                <div>
                  <dt>Identidade</dt>
                  <dd><code>{{ row.repository.identity_key }}</code></dd>
                </div>
                <div>
                  <dt>Snapshots READY</dt>
                  <dd>{{ formatCount(row.readySnapshots) }}</dd>
                </div>
                <div>
                  <dt>Snapshot mais recente</dt>
                  <dd>
                    @if (row.latestReadyId !== null) {
                      #{{ row.latestReadyId }}
                      @if (row.latestReady; as snap) {
                        <span class="repo-card__muted">
                          · {{ snap.created_at }}
                          @if (snap.git_ref) {
                            · {{ snap.git_ref }}
                          }
                          @if (shortSha(snap.commit_sha)) {
                            · <code>{{ shortSha(snap.commit_sha) }}</code>
                          }
                        </span>
                      }
                    } @else {
                      —
                    }
                  </dd>
                </div>
                <div>
                  <dt>Arquivos</dt>
                  <dd>{{ formatCount(row.files) }}</dd>
                </div>
                <div>
                  <dt>Símbolos</dt>
                  <dd>{{ formatCount(row.symbols) }}</dd>
                </div>
                <div>
                  <dt>Relações</dt>
                  <dd>{{ formatCount(row.relations) }}</dd>
                </div>
              </dl>

              <div class="repo-card__actions">
                @if (isActive(row)) {
                  <button type="button" disabled>Selecionado</button>
                } @else {
                  <button
                    type="button"
                    class="repo-card__select"
                    (click)="select(row)"
                  >
                    Selecionar
                  </button>
                }
                <a
                  [routerLink]="['/graph']"
                  [queryParams]="graphParams(row)"
                >
                  Abrir grafo
                </a>
                <a
                  [routerLink]="['/search']"
                  [queryParams]="searchParams(row)"
                >
                  Pesquisar
                </a>
                <a [routerLink]="['/diagnostics']">Diagnósticos</a>
                <button type="button" (click)="copyPath(row)">
                  Copiar caminho
                </button>
                @if (copyFeedback(); as fb) {
                  @if (fb.repositoryId === row.repository.id) {
                    <span
                      class="repo-card__copy-feedback"
                      [class.repo-card__copy-feedback--error]="!fb.ok"
                      role="status"
                    >
                      {{ fb.ok ? 'Caminho copiado.' : 'Falha ao copiar — selecione o caminho acima.' }}
                    </span>
                  }
                }
              </div>
            </li>
          }
        </ul>
      }
    </section>
  `,
    styles: [
        `
      .repositories {
        padding: var(--atlas-space-5);
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-4);
      }

      .repositories__header {
        display: flex;
        flex-wrap: wrap;
        align-items: baseline;
        gap: var(--atlas-space-2) var(--atlas-space-4);
      }

      .repositories__header h1 {
        margin: 0;
        font-size: var(--atlas-font-size-xl);
      }

      .repositories__description {
        margin: 0;
        color: var(--atlas-color-text-muted);
        flex: 1;
        min-width: 16rem;
      }

      .repositories__refresh {
        padding: var(--atlas-space-1) var(--atlas-space-3);
        border: 1px solid var(--atlas-color-border-strong);
        border-radius: var(--atlas-radius-sm);
        background: var(--atlas-color-bg-subtle);
        color: var(--atlas-color-text);
        cursor: pointer;
      }

      .repositories__refresh:disabled {
        opacity: 0.6;
        cursor: not-allowed;
      }

      .repositories__empty {
        padding: var(--atlas-space-4);
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        background: var(--atlas-color-bg-subtle);
        color: var(--atlas-color-text-muted);
      }

      .repositories__empty h2 {
        margin: 0 0 var(--atlas-space-2);
        font-size: var(--atlas-font-size-md);
        color: var(--atlas-color-text);
      }

      .repositories__empty p {
        margin: 0;
      }

      .repositories__empty code,
      .repo-card__meta code {
        font-family: var(--atlas-font-family-mono);
        font-size: var(--atlas-font-size-sm);
        overflow-wrap: anywhere;
      }

      .repositories__list {
        margin: 0;
        padding: 0;
        list-style: none;
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-3);
      }

      .repo-card {
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-3) var(--atlas-space-4);
        background: var(--atlas-color-bg-raised);
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-3);
      }

      .repo-card--active {
        border-color: var(--atlas-color-accent);
        box-shadow: 0 0 0 1px var(--atlas-color-accent);
      }

      .repo-card__head {
        display: flex;
        align-items: baseline;
        gap: var(--atlas-space-3);
        flex-wrap: wrap;
      }

      .repo-card__path {
        font-family: var(--atlas-font-family-mono);
        font-weight: var(--atlas-font-weight-bold);
        overflow-wrap: anywhere;
      }

      .repo-card__badge {
        padding: 0 var(--atlas-space-2);
        border-radius: var(--atlas-radius-full);
        border: 1px solid var(--atlas-evidence-verified);
        color: var(--atlas-evidence-verified);
        background: var(--atlas-evidence-verified-bg);
        font-size: var(--atlas-font-size-xs);
      }

      .repo-card__meta {
        margin: 0;
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(12rem, 1fr));
        gap: var(--atlas-space-2) var(--atlas-space-4);
      }

      .repo-card__meta dt {
        font-size: var(--atlas-font-size-xs);
        color: var(--atlas-color-text-muted);
      }

      .repo-card__meta dd {
        margin: 0;
        font-size: var(--atlas-font-size-sm);
        overflow-wrap: anywhere;
      }

      .repo-card__muted {
        color: var(--atlas-color-text-muted);
      }

      .repo-card__actions {
        display: flex;
        align-items: center;
        gap: var(--atlas-space-3);
        flex-wrap: wrap;
      }

      .repo-card__actions a,
      .repo-card__actions button {
        font: inherit;
        font-size: var(--atlas-font-size-sm);
      }

      .repo-card__actions button {
        padding: var(--atlas-space-1) var(--atlas-space-3);
        border: 1px solid var(--atlas-color-border-strong);
        border-radius: var(--atlas-radius-sm);
        background: var(--atlas-color-bg-subtle);
        color: var(--atlas-color-text);
        cursor: pointer;
      }

      .repo-card__actions button:disabled {
        opacity: 0.6;
        cursor: not-allowed;
      }

      .repo-card__actions .repo-card__select {
        border-color: var(--atlas-color-accent);
        color: var(--atlas-color-accent);
      }

      .repo-card__copy-feedback {
        font-size: var(--atlas-font-size-xs);
        color: var(--atlas-evidence-verified);
      }

      .repo-card__copy-feedback--error {
        color: var(--atlas-color-warning);
      }
    `,
    ]
})
export class RepositoriesComponent implements OnInit, OnDestroy {
  readonly phase = signal<RepositoriesPhase>('loading');
  readonly rows = signal<RepositoryRow[]>([]);
  readonly errorText = signal('');
  readonly copyFeedback = signal<CopyFeedback | null>(null);

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
  private copyFeedbackTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly api: ApiService,
    private readonly perf: PerfLogService,
    readonly ctx: ExplorerContextService,
  ) {}

  ngOnInit(): void {
    void this.load();
  }

  ngOnDestroy(): void {
    this.controller.abort();
    if (this.copyFeedbackTimer !== null) {
      clearTimeout(this.copyFeedbackTimer);
    }
  }

  async load(): Promise<void> {
    this.phase.set('loading');
    this.errorText.set('');
    try {
      const [repos, status] = await Promise.all([
        this.perf.time('GET /repositories', () =>
          this.api.listRepositories({ signal: this.controller.signal }),
        ),
        this.perf.time('GET /index/status', () =>
          this.api.getIndexStatus({ signal: this.controller.signal }),
        ),
      ]);
      const items = repos.items ?? [];
      // Per-repo snapshot detail is fetched best-effort: a single failure
      // downgrades that row to "—" instead of blanking the whole screen.
      const snapshotPairs = await Promise.all(
        items.map(async (repo) => {
          try {
            const res = await this.api.listSnapshots(repo.id, {
              signal: this.controller.signal,
            });
            return [repo.id, res.items ?? []] as const;
          } catch {
            return [repo.id, [] as readonly ApiSnapshotItem[]] as const;
          }
        }),
      );
      const snapshotsByRepo = new Map<number, readonly ApiSnapshotItem[]>(
        snapshotPairs.map(([id, snaps]) => [id, snaps]),
      );
      this.rows.set(buildRepositoryRows(items, status.items ?? [], snapshotsByRepo));
      this.phase.set('ready');
    } catch (error) {
      this.errorText.set(apiErrorText(error));
      this.phase.set(isApiOffline(error) ? 'offline' : 'error');
    }
  }

  /** Selects the repository as global context, pinning the shown READY snapshot. */
  select(row: RepositoryRow): void {
    this.ctx.selectRepository(row.repository);
    if (row.latestReady) {
      this.ctx.selectSnapshot(row.latestReady);
    }
  }

  isActive(row: RepositoryRow): boolean {
    return this.ctx.activeRepository()?.id === row.repository.id;
  }

  graphParams(row: RepositoryRow): Record<string, string | number> {
    const params: Record<string, string | number> = {
      repo: row.repository.root_handle,
    };
    if (row.latestReadyId !== null) {
      params['snapshot'] = row.latestReadyId;
    }
    return params;
  }

  searchParams(row: RepositoryRow): Record<string, string | number> {
    return { repo: row.repository.root_handle };
  }

  async copyPath(row: RepositoryRow): Promise<void> {
    const path = row.repository.root_handle;
    let ok = false;
    try {
      if (!navigator.clipboard) {
        throw new Error('clipboard indisponível');
      }
      await navigator.clipboard.writeText(path);
      ok = true;
    } catch {
      ok = false;
    }
    this.copyFeedback.set({ repositoryId: row.repository.id, ok });
    if (this.copyFeedbackTimer !== null) {
      clearTimeout(this.copyFeedbackTimer);
    }
    this.copyFeedbackTimer = setTimeout(() => this.copyFeedback.set(null), 4000);
  }

  formatCount(value: number | null): string {
    return value === null ? '—' : String(value);
  }

  shortSha(commitSha: string | null): string {
    return shortSha(commitSha);
  }
}
