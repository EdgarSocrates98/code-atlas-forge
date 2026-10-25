import {
  ChangeDetectionStrategy,
  Component,
  OnDestroy,
  OnInit,
  computed,
  signal,
} from '@angular/core';
import {
  ApiFederationViewMember,
  ApiFederationWorkspace,
  ApiFederationWorkspaceItem,
  ApiService,
} from '../../../core/api/api.service';
import { apiErrorText, isApiOffline } from '../../../shared/errors';
import {
  BlockState,
  StateBlockComponent,
} from '../../../shared/components/state-block/state-block.component';
import { PerfLogService } from '../../diagnostics/perf-log.service';

type FederationPhase = 'loading' | 'ready' | 'error' | 'offline';
type DetailPhase = 'idle' | 'loading' | 'ready' | 'error';

export interface MemberRow {
  repository_id: number;
  root_handle: string;
  identity_key: string;
  pinned_snapshot_id: number | null;
  added_at: string;
  /** from the resolved view, when loaded. */
  view?: ApiFederationViewMember;
}

/**
 * Federation view (lf-0122): workspaces, members and their pinned
 * snapshots. Read-only — mutations stay CLI-only
 * (`code-atlas federation …`). When no workspace exists the screen says
 * so explicitly instead of fabricating state.
 */
@Component({
  selector: 'atlas-federation',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [StateBlockComponent],
  template: `
    <section class="federation" aria-labelledby="federation-title">
      <header class="federation__header">
        <h1 id="federation-title">Federação</h1>
        <p class="federation__description">
          Workspaces federados e pins de snapshot por repositório. Esta
          tela é somente leitura — a gestão é feita via
          <code>code-atlas federation</code>.
        </p>
        <button type="button" (click)="load()" [disabled]="phase() === 'loading'">
          {{ phase() === 'loading' ? 'Atualizando…' : 'Atualizar' }}
        </button>
      </header>

      @if (showBlock()) {
        <atlas-state-block
          [state]="blockState()"
          loadingMessage="Consultando workspaces de federação…"
          [errorMessage]="errorText()"
        />
      }

      @if (phase() === 'ready' && workspaces().length === 0) {
        <div class="federation__empty" aria-live="polite">
          <h2>Federação não configurada</h2>
          <p>
            Nenhum workspace federado foi registrado neste índice. Use
            <code>code-atlas federation create &lt;nome&gt;</code> e
            <code>code-atlas federation add …</code> no CLI para agrupar
            repositórios com pins de snapshot.
          </p>
        </div>
      }

      @if (workspaces().length > 0) {
        <section aria-labelledby="federation-workspaces">
          <h2 id="federation-workspaces">Workspaces</h2>
          <ul class="federation__list">
            @for (ws of workspaces(); track ws.id) {
              <li>
                <button
                  type="button"
                  class="federation__ws"
                  [class.federation__ws--active]="selected()?.id === ws.id"
                  (click)="select(ws)"
                  [attr.aria-pressed]="selected()?.id === ws.id"
                >
                  <span class="federation__ws-name">{{ ws.name }}</span>
                  <span class="federation__muted">
                    {{ ws.members }} membro(s) · criado em {{ ws.created_at }}
                  </span>
                </button>
              </li>
            }
          </ul>
        </section>
      }

      @if (detailPhase() === 'loading') {
        <p role="status" class="federation__muted">Carregando workspace…</p>
      }
      @if (detailPhase() === 'error') {
        <p role="alert" class="federation__error">{{ detailError() }}</p>
      }

      @if (selectedWorkspace(); as sel) {
        <section class="federation__detail" aria-labelledby="federation-members">
          <h2 id="federation-members">Membros de {{ sel.name }}</h2>
          @if (sel.members.length === 0) {
            <p class="federation__muted">Workspace sem membros.</p>
          } @else {
            <table class="federation__table">
              <thead>
                <tr>
                  <th scope="col">Repositório</th>
                  <th scope="col">Identidade</th>
                  <th scope="col">Pin</th>
                  <th scope="col">Snapshot resolvido</th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                @for (m of memberRows(); track m.repository_id) {
                  <tr>
                    <td><code>{{ m.root_handle }}</code></td>
                    <td><code>{{ m.identity_key }}</code></td>
                    <td>
                      {{ m.pinned_snapshot_id ?? '—' }}
                      @if (m.view?.pinned) {
                        <span class="federation__muted">(override)</span>
                      }
                    </td>
                    <td>
                      {{ m.view?.snapshot_id ?? '—' }}
                      @if (m.view?.resolution) {
                        <span class="federation__muted">({{ m.view!.resolution }})</span>
                      }
                    </td>
                    <td>{{ m.view?.snapshot_status ?? '—' }}</td>
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
      .federation {
        padding: var(--atlas-space-5);
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-4);
      }

      .federation__header {
        display: flex;
        flex-wrap: wrap;
        align-items: baseline;
        gap: var(--atlas-space-2) var(--atlas-space-4);
      }

      .federation__header h1 {
        margin: 0;
        font-size: var(--atlas-font-size-xl);
      }

      .federation__description {
        margin: 0;
        color: var(--atlas-color-text-muted);
        flex: 1;
        min-width: 16rem;
        overflow-wrap: anywhere;
      }

      .federation__header button {
        padding: var(--atlas-space-1) var(--atlas-space-3);
        border: 1px solid var(--atlas-color-border-strong);
        border-radius: var(--atlas-radius-sm);
        background: var(--atlas-color-bg-subtle);
        color: var(--atlas-color-text);
        cursor: pointer;
      }

      .federation__empty {
        padding: var(--atlas-space-4);
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        background: var(--atlas-color-bg-subtle);
        color: var(--atlas-color-text-muted);
      }

      .federation__empty h2 {
        margin: 0 0 var(--atlas-space-2);
        font-size: var(--atlas-font-size-md);
        color: var(--atlas-color-text);
      }

      .federation__list {
        margin: 0;
        padding: 0;
        list-style: none;
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-2);
      }

      .federation__ws {
        width: 100%;
        display: flex;
        align-items: baseline;
        gap: var(--atlas-space-3);
        padding: var(--atlas-space-2) var(--atlas-space-3);
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        background: var(--atlas-color-bg-raised);
        color: var(--atlas-color-text);
        font: inherit;
        text-align: left;
        cursor: pointer;
      }

      .federation__ws--active {
        border-color: var(--atlas-color-accent);
      }

      .federation__ws-name {
        font-weight: var(--atlas-font-weight-semibold);
      }

      .federation__detail {
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-3) var(--atlas-space-4);
        background: var(--atlas-color-bg-raised);
      }

      .federation__detail h2 {
        margin: 0 0 var(--atlas-space-3);
        font-size: var(--atlas-font-size-lg);
      }

      .federation__table {
        width: 100%;
        border-collapse: collapse;
        font-size: var(--atlas-font-size-sm);
      }

      .federation__table th,
      .federation__table td {
        border-bottom: 1px solid var(--atlas-color-border);
        padding: var(--atlas-space-2);
        text-align: left;
        overflow-wrap: anywhere;
      }

      .federation__table th {
        font-size: var(--atlas-font-size-xs);
        color: var(--atlas-color-text-muted);
      }

      .federation__table code {
        font-family: var(--atlas-font-family-mono);
      }

      .federation__muted {
        color: var(--atlas-color-text-muted);
        font-size: var(--atlas-font-size-xs);
      }

      .federation__error {
        color: var(--atlas-color-danger);
        font-size: var(--atlas-font-size-sm);
      }
    `,
  ],
})
export class FederationComponent implements OnInit, OnDestroy {
  readonly phase = signal<FederationPhase>('loading');
  readonly workspaces = signal<ApiFederationWorkspaceItem[]>([]);
  readonly errorText = signal('');
  readonly detailPhase = signal<DetailPhase>('idle');
  readonly detailError = signal('');
  readonly selected = signal<ApiFederationWorkspaceItem | null>(null);
  readonly selectedWorkspace = signal<ApiFederationWorkspace | null>(null);
  readonly memberRows = signal<MemberRow[]>([]);

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
      const res = await this.perf.time('GET /federation/workspaces', () =>
        this.api.listFederationWorkspaces({ signal: this.controller.signal }),
      );
      this.workspaces.set(res.items ?? []);
      this.phase.set('ready');
      // auto-select a single workspace — a conveniência não inventa estado.
      const items = res.items ?? [];
      if (items.length === 1) {
        void this.select(items[0]);
      }
    } catch (error) {
      this.workspaces.set([]);
      this.handleError(error);
    }
  }

  async select(ws: ApiFederationWorkspaceItem): Promise<void> {
    this.selected.set(ws);
    this.detailPhase.set('loading');
    this.detailError.set('');
    this.selectedWorkspace.set(null);
    this.memberRows.set([]);
    try {
      const [workspace, view] = await Promise.all([
        this.perf.time('GET /federation/workspaces/{id}', () =>
          this.api.getFederationWorkspace(ws.id, {
            signal: this.controller.signal,
          }),
        ),
        this.perf.time('GET /federation/workspaces/{id}/view', () =>
          this.api.getFederationView(ws.id, { signal: this.controller.signal }),
        ),
      ]);
      this.selectedWorkspace.set(workspace);
      const byRepo = new Map(
        (view.members ?? []).map((m) => [m.repository_id, m]),
      );
      this.memberRows.set(
        (workspace.members ?? []).map((m) => ({
          ...m,
          view: byRepo.get(m.repository_id),
        })),
      );
      this.detailPhase.set('ready');
    } catch (error) {
      this.selectedWorkspace.set(null);
      this.memberRows.set([]);
      this.detailError.set(apiErrorText(error));
      this.detailPhase.set('error');
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
