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
  ApiSnapshotItem,
} from '../../core/api/api.service';
import { apiErrorText, isApiOffline } from '../../shared/errors';
import { StateBlockComponent } from '../../shared/components/state-block/state-block.component';
import { PerfLogService } from '../diagnostics/perf-log.service';
import { ImpactResultsComponent } from '../impact/impact-results.component';
import { ImpactView, toImpactView } from '../impact/impact.vm';

type RevisionsPhase = 'idle' | 'loading' | 'ready' | 'error' | 'offline';

/**
 * Revisions screen — compares two snapshots (or free revision expressions)
 * of one repository through POST /impact/analyze and renders the symbol
 * changes side by side, grouped by change kind.
 *
 * Snapshot selectors send base/target_snapshot_id (validated server-side
 * against the repository); the optional revision text fields bind a git
 * expression to that snapshot, which may yield binding diagnostics — these
 * surface as the PARTIAL banner, never as a verified result.
 */
@Component({
  selector: 'atlas-revisions',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FormsModule, StateBlockComponent, ImpactResultsComponent],
  template: `
    <section class="revisions" aria-labelledby="revisions-title">
      <header class="revisions__header">
        <h1 id="revisions-title">Revisões</h1>
        <p class="revisions__description">
          Diff de símbolos entre dois snapshots indexados do mesmo repositório.
        </p>
      </header>

      <form
        class="revisions__form"
        (ngSubmit)="compare()"
        aria-label="Seleção de revisões para comparação"
      >
        <label class="revisions__field revisions__field--repo">
          <span>Repositório</span>
          <select
            name="repo"
            [(ngModel)]="form.repositoryId"
            (ngModelChange)="onRepositoryChange($event)"
            required
            aria-required="true"
          >
            <option [ngValue]="null" disabled>Selecione…</option>
            @for (repo of repositories(); track repo.id) {
              <option [ngValue]="repo.id">{{ repo.root_handle }}</option>
            }
          </select>
        </label>

        @if (snapshotsLoading()) {
          <p class="revisions__loading" role="status">Carregando snapshots…</p>
        } @else if (form.repositoryId !== null && snapshots().length === 0) {
          <p class="revisions__loading" role="status">
            Nenhum snapshot indexado neste repositório ainda — indexe com a CLI
            (<code>code-atlas index</code>) e retorne.
          </p>
        }

        <div class="revisions__pair" role="group" aria-label="Par de revisões">
          <fieldset class="revisions__side">
            <legend>Base</legend>
            <label class="revisions__field">
              <span>Snapshot base</span>
              <select name="baseSnap" [(ngModel)]="form.baseSnapshotId">
                <option [ngValue]="null">Nenhum (usar expressão)</option>
                @for (snap of snapshots(); track snap.id) {
                  <option [ngValue]="snap.id">
                    #{{ snap.id }} · {{ snap.status }} ·
                    {{ shortSha(snap.commit_sha) }} · {{ snap.created_at }}
                  </option>
                }
              </select>
            </label>
            <label class="revisions__field">
              <span>Revisão base (opcional)</span>
              <input
                type="text"
                name="baseRev"
                [(ngModel)]="form.baseRevision"
                autocomplete="off"
                placeholder="sha ou expressão git — validada contra o snapshot"
              />
            </label>
          </fieldset>

          <div class="revisions__arrow" aria-hidden="true">→</div>

          <fieldset class="revisions__side">
            <legend>Alvo</legend>
            <label class="revisions__field">
              <span>Snapshot alvo</span>
              <select name="targetSnap" [(ngModel)]="form.targetSnapshotId">
                <option [ngValue]="null">Nenhum (usar expressão)</option>
                @for (snap of snapshots(); track snap.id) {
                  <option [ngValue]="snap.id">
                    #{{ snap.id }} · {{ snap.status }} ·
                    {{ shortSha(snap.commit_sha) }} · {{ snap.created_at }}
                  </option>
                }
              </select>
            </label>
            <label class="revisions__field">
              <span>Revisão alvo (opcional)</span>
              <input
                type="text"
                name="targetRev"
                [(ngModel)]="form.targetRevision"
                autocomplete="off"
                placeholder="vazio = worktree atual"
              />
            </label>
          </fieldset>
        </div>

        <div class="revisions__actions">
          <button
            type="submit"
            [disabled]="phase() === 'loading' || !canCompare()"
          >
            {{ phase() === 'loading' ? 'Comparando…' : 'Comparar' }}
          </button>
          <p class="revisions__note">
            Sem snapshot nem expressão de um lado, o backend resolve o par
            implicitamente (READY mais recente e seu antecessor).
          </p>
        </div>
      </form>

      @if (phase() === 'idle') {
        <p class="revisions__hint">Selecione o par para comparar.</p>
      }

      @if (showBlock()) {
        <atlas-state-block
          [state]="blockState()"
          loadingMessage="Comparando revisões…"
          emptyMessage="Nenhuma diferença de símbolos entre as revisões selecionadas."
          [errorMessage]="errorText()"
        />
      }

      @if (result(); as view) {
        <div class="revisions__binding" role="status" aria-live="polite">
          <strong>Vínculo de revisão:</strong>
          base <code>{{ view.baseRevision || '—' }}</code>
          (snapshot #{{ view.baseSnapshotId ?? '—' }}) → alvo
          <code>{{ view.targetRevision || '(worktree)' }}</code>
          (snapshot #{{ view.targetSnapshotId ?? '—' }})
          @if (view.partial) {
            — <span class="revisions__partial-tag">PARCIAL</span>
          } @else {
            — <span class="revisions__exact-tag">verificado</span>
          }
        </div>
        <atlas-impact-results [view]="view" />
      }
    </section>
  `,
  styles: [
    `
      .revisions {
        padding: var(--atlas-space-5);
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-4);
      }

      .revisions__header h1 {
        margin: 0 0 var(--atlas-space-2);
        font-size: var(--atlas-font-size-xl);
      }

      .revisions__description {
        margin: 0;
        color: var(--atlas-color-text-muted);
      }

      .revisions__form {
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-3);
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-4);
        background: var(--atlas-color-bg-raised);
      }

      .revisions__field {
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-1);
        font-size: var(--atlas-font-size-sm);
      }

      .revisions__field--repo {
        max-width: 32rem;
      }

      .revisions__field input,
      .revisions__field select {
        padding: var(--atlas-space-2);
        border: 1px solid var(--atlas-color-border-strong);
        border-radius: var(--atlas-radius-sm);
        background: var(--atlas-color-bg);
        color: var(--atlas-color-text);
        font: inherit;
      }

      .revisions__pair {
        display: flex;
        gap: var(--atlas-space-4);
        align-items: stretch;
      }

      .revisions__side {
        flex: 1;
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-2);
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-3);
        margin: 0;
      }

      .revisions__side legend {
        font-size: var(--atlas-font-size-sm);
        font-weight: var(--atlas-font-weight-medium);
        padding: 0 var(--atlas-space-1);
      }

      .revisions__arrow {
        align-self: center;
        color: var(--atlas-color-text-muted);
        font-size: var(--atlas-font-size-xl);
      }

      .revisions__actions {
        display: flex;
        align-items: center;
        gap: var(--atlas-space-4);
        flex-wrap: wrap;
      }

      .revisions__actions button {
        padding: var(--atlas-space-2) var(--atlas-space-5);
        border: 0;
        border-radius: var(--atlas-radius-sm);
        background: var(--atlas-color-accent);
        color: var(--atlas-color-accent-text);
        font-weight: var(--atlas-font-weight-medium);
        cursor: pointer;
      }

      .revisions__actions button:disabled {
        opacity: 0.6;
        cursor: not-allowed;
      }

      .revisions__note,
      .revisions__hint,
      .revisions__loading {
        color: var(--atlas-color-text-muted);
        font-size: var(--atlas-font-size-sm);
        margin: 0;
      }

      .revisions__binding {
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-3) var(--atlas-space-4);
        background: var(--atlas-color-bg-subtle);
        overflow-wrap: anywhere;
      }

      .revisions__partial-tag {
        color: var(--atlas-color-warning);
        font-weight: var(--atlas-font-weight-bold);
      }

      .revisions__exact-tag {
        color: var(--atlas-evidence-verified);
        font-weight: var(--atlas-font-weight-medium);
      }
    `,
  ],
})
export class RevisionsComponent implements OnInit, OnDestroy {
  readonly form = {
    repositoryId: null as number | null,
    baseSnapshotId: null as number | null,
    targetSnapshotId: null as number | null,
    baseRevision: '',
    targetRevision: '',
  };

  readonly phase = signal<RevisionsPhase>('idle');
  readonly snapshotsLoading = signal(false);
  readonly repositories = signal<ApiRepositoryItem[]>([]);
  readonly snapshots = signal<ApiSnapshotItem[]>([]);
  readonly result = signal<ImpactView | null>(null);
  readonly errorText = signal('');

  /**
   * Plain method (not computed): `form` is a mutable object mutated by
   * ngModel, which does not mark signals dirty — a computed would go stale.
   */
  canCompare(): boolean {
    if (this.form.repositoryId === null) return false;
    return (
      this.form.baseSnapshotId !== null ||
      this.form.targetSnapshotId !== null ||
      this.form.baseRevision.trim() !== '' ||
      this.form.targetRevision.trim() !== ''
    );
  }

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

  async onRepositoryChange(repositoryId: number | null): Promise<void> {
    this.snapshots.set([]);
    this.form.baseSnapshotId = null;
    this.form.targetSnapshotId = null;
    if (repositoryId === null) return;
    this.snapshotsLoading.set(true);
    try {
      const res = await this.perf.time('GET /repositories/{id}/snapshots', () =>
        this.api.listSnapshots(repositoryId, { signal: this.controller.signal }),
      );
      this.snapshots.set(res.items ?? []);
      const ready = (res.items ?? []).filter((s) => s.status === 'READY');
      if (ready.length >= 2) {
        this.form.targetSnapshotId = ready[0].id;
        this.form.baseSnapshotId = ready[1].id;
      } else if (ready.length === 1) {
        this.form.targetSnapshotId = ready[0].id;
      }
    } catch (error) {
      if (isApiOffline(error)) {
        this.phase.set('offline');
      } else {
        this.errorText.set(apiErrorText(error));
        this.phase.set('error');
      }
    } finally {
      this.snapshotsLoading.set(false);
    }
  }

  async compare(): Promise<void> {
    const repo = this.repositories().find((r) => r.id === this.form.repositoryId);
    if (!repo) return;
    this.phase.set('loading');
    this.errorText.set('');
    this.result.set(null);
    try {
      const res = await this.perf.time('POST /impact/analyze', () =>
        this.api.analyzeImpact(this.buildRequest(repo.root_handle), {
          signal: this.controller.signal,
        }),
      );
      this.result.set(toImpactView(res));
      this.phase.set('ready');
    } catch (error) {
      this.handleError(error);
    }
  }

  shortSha(sha: string | null): string {
    return sha ? sha.slice(0, 12) : 'sem sha';
  }

  private buildRequest(repositoryRoot: string): ApiImpactRequest {
    const req: ApiImpactRequest = { repository_root: repositoryRoot };
    if (this.form.baseSnapshotId !== null) {
      req.base_snapshot_id = this.form.baseSnapshotId;
    }
    if (this.form.targetSnapshotId !== null) {
      req.target_snapshot_id = this.form.targetSnapshotId;
    }
    if (this.form.baseRevision.trim()) {
      req.base_revision = this.form.baseRevision.trim();
    }
    if (this.form.targetRevision.trim()) {
      req.target_revision = this.form.targetRevision.trim();
    }
    return req;
  }

  private async loadRepositories(): Promise<void> {
    try {
      const res = await this.perf.time('GET /repositories', () =>
        this.api.listRepositories({ signal: this.controller.signal }),
      );
      this.repositories.set(res.items ?? []);
      if (res.items?.length === 1) {
        this.form.repositoryId = res.items[0].id;
        await this.onRepositoryChange(res.items[0].id);
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
