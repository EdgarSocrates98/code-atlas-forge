import { Injectable, signal } from '@angular/core';
import type {
  ApiGraphNode,
  ApiRepositoryItem,
  ApiSnapshotItem,
} from '../api/api.service';
import { ApiService } from '../api/api.service';
import { apiErrorText, isApiOffline } from '../../shared/errors';

export type ExplorerContextPhase = 'idle' | 'loading' | 'ready' | 'error' | 'offline';

/** Query-param shape understood by deep links (`?repo=&snapshot=`). */
export interface ExplorerRouteContext {
  repo?: string | null;
  snapshot?: string | number | null;
}

interface PersistedContext {
  repoIdentity: string | null;
  snapshotId: number | null;
}

const STORAGE_KEY = 'atlas-explorer-context';
const EMPTY_PERSISTED: PersistedContext = { repoIdentity: null, snapshotId: null };

function isReadySnapshot(snapshot: ApiSnapshotItem): boolean {
  return (snapshot.status ?? '').toUpperCase() === 'READY';
}

/**
 * Global explorer context (lf-0098): which repository and snapshot the
 * user is looking at, plus the currently selected symbol.
 *
 * Semantics:
 * - `activeRepository` is an explicit selection; on bootstrap it restores
 *   the persisted identity (validated against GET /repositories) and falls
 *   back explicitly to the first indexed repository when stale.
 * - `activeSnapshot` is a nullable pin: `null` means "server default"
 *   (the latest READY snapshot), never a fabricated snapshot.
 * - `selectedSymbol` is an occurrence node (the `occ:{id}` shape emitted
 *   by the graph endpoints) or null.
 * - Only non-sensitive identifiers (identity_key + snapshot id) are
 *   persisted to localStorage; nothing else leaves the page.
 */
@Injectable({ providedIn: 'root' })
export class ExplorerContextService {
  /* ----------------------------- repositories ---------------------------- */
  readonly repositories = signal<readonly ApiRepositoryItem[]>([]);
  readonly repositoriesPhase = signal<ExplorerContextPhase>('idle');
  readonly repositoriesError = signal('');

  /* --------------------------- active context ---------------------------- */
  readonly activeRepository = signal<ApiRepositoryItem | null>(null);
  readonly snapshots = signal<readonly ApiSnapshotItem[]>([]);
  readonly snapshotsPhase = signal<ExplorerContextPhase>('idle');
  readonly activeSnapshot = signal<ApiSnapshotItem | null>(null);
  readonly selectedSymbol = signal<ApiGraphNode | null>(null);

  /**
   * Explicit, user-visible note when persisted or deep-linked state could
   * not be honored (stale repository, missing/incompatible snapshot).
   * Empty string means no notice — never a guessed message.
   */
  readonly notice = signal('');

  private initPromise: Promise<void> | null = null;
  private snapshotsPromise: Promise<void> | null = null;

  constructor(private readonly api: ApiService) {}

  /**
   * Idempotent bootstrap: loads the repository list once and restores the
   * persisted context. Subsequent calls return the in-flight/finished
   * promise, so every consumer (shell, feature screens) can safely call it.
   */
  init(): Promise<void> {
    this.initPromise ??= this.bootstrap();
    return this.initPromise;
  }

  /** Re-runs bootstrap (e.g. after the API comes back online). */
  refresh(): Promise<void> {
    this.initPromise = this.bootstrap();
    return this.initPromise;
  }

  /**
   * Selects the active repository. Clears `selectedSymbol` and any
   * snapshot pin (a snapshot belongs to exactly one repository, so a
   * repo change always makes the pin incompatible) and reloads the repo's
   * snapshot list in the background.
   */
  selectRepository(repository: ApiRepositoryItem): void {
    const current = this.activeRepository();
    if (current?.id === repository.id) {
      return;
    }
    this.activeRepository.set(repository);
    this.selectedSymbol.set(null);
    this.activeSnapshot.set(null);
    this.snapshots.set([]);
    this.persist();
    void this.loadSnapshots();
  }

  /** Pins an explicit snapshot; `null` returns to the server default. */
  selectSnapshot(snapshot: ApiSnapshotItem | null): void {
    if (snapshot && !this.activeRepository()) {
      return; // snapshot pin requires an active repository
    }
    this.activeSnapshot.set(snapshot);
    this.persist();
  }

  selectSymbol(symbol: ApiGraphNode | null): void {
    this.selectedSymbol.set(symbol);
  }

  clearNotice(): void {
    this.notice.set('');
  }

  /**
   * Applies `?repo=&snapshot=` deep-link params to the global context.
   * `repo` resolves against root_handle, identity_key or numeric id;
   * `snapshot` must be a positive numeric id that exists in the active
   * repository. Unresolvable values never invent context — they surface
   * an explicit notice instead.
   */
  async syncFromQueryParams(params: ExplorerRouteContext): Promise<void> {
    await this.init();

    const repoParam = params.repo;
    if (repoParam !== undefined && repoParam !== null && repoParam !== '') {
      const match = this.resolveRepository(String(repoParam));
      if (match) {
        this.selectRepository(match);
      } else {
        this.notice.set(
          `O link aponta para o repositório "${repoParam}", que não está indexado.`,
        );
        return; // without a repository the snapshot param is meaningless
      }
    }

    const snapshotParam = params.snapshot;
    if (snapshotParam === undefined || snapshotParam === null || snapshotParam === '') {
      return;
    }
    const snapshotId = Number(snapshotParam);
    if (!Number.isFinite(snapshotId) || snapshotId <= 0) {
      this.notice.set(`Parâmetro ?snapshot= inválido: "${String(snapshotParam)}".`);
      return;
    }
    await this.applySnapshotParam(snapshotId);
  }

  /** Query params carrying the current context — for `[queryParams]` bindings. */
  contextQueryParams(): { repo?: string; snapshot?: number } {
    const params: { repo?: string; snapshot?: number } = {};
    const repo = this.activeRepository();
    if (repo) {
      params.repo = repo.root_handle;
    }
    const snapshot = this.activeSnapshot();
    if (snapshot) {
      params.snapshot = snapshot.id;
    }
    return params;
  }

  /** Builds a router-ready deep link to `path` carrying the current context. */
  deepLink(
    path: string,
    extra?: Record<string, string | number>,
  ): { commands: string[]; queryParams: Record<string, string | number> } {
    return {
      commands: [path],
      queryParams: { ...this.contextQueryParams(), ...extra },
    };
  }

  /**
   * Resolves a repository identifier against the loaded list. Accepts
   * root_handle (deep links), identity_key (persisted state) or the
   * numeric id encoded as a string.
   */
  resolveRepository(identity: string): ApiRepositoryItem | null {
    const needle = identity.trim();
    if (!needle) {
      return null;
    }
    return (
      this.repositories().find(
        (repo) =>
          repo.root_handle === needle ||
          repo.identity_key === needle ||
          String(repo.id) === needle,
      ) ?? null
    );
  }

  /** Loads the snapshot list of the active repository (background refresh). */
  async loadSnapshots(): Promise<void> {
    const repo = this.activeRepository();
    if (!repo) {
      this.snapshots.set([]);
      this.snapshotsPhase.set('idle');
      return;
    }
    this.snapshotsPhase.set('loading');
    const promise = (async () => {
      try {
        const res = await this.api.listSnapshots(repo.id);
        if (this.activeRepository()?.id !== repo.id) {
          return; // selection changed while loading — drop stale response
        }
        this.snapshots.set(res.items ?? []);
        this.snapshotsPhase.set('ready');
      } catch {
        if (this.activeRepository()?.id === repo.id) {
          this.snapshotsPhase.set('error');
        }
      }
    })();
    this.snapshotsPromise = promise;
    await promise;
  }

  /** Awaits any in-flight snapshot load; starts one when needed. */
  private async ensureSnapshotsLoaded(): Promise<void> {
    if (this.snapshotsPhase() === 'ready') {
      return;
    }
    if (this.snapshotsPhase() === 'loading' && this.snapshotsPromise) {
      await this.snapshotsPromise;
      return;
    }
    await this.loadSnapshots();
  }

  private async bootstrap(): Promise<void> {
    this.repositoriesPhase.set('loading');
    this.repositoriesError.set('');
    let items: ApiRepositoryItem[];
    try {
      const res = await this.api.listRepositories();
      items = res.items ?? [];
    } catch (error) {
      this.repositoriesPhase.set(isApiOffline(error) ? 'offline' : 'error');
      this.repositoriesError.set(apiErrorText(error));
      return;
    }
    this.repositories.set(items);
    this.repositoriesPhase.set('ready');

    const persisted = this.readPersisted();
    if (!items.length) {
      this.activeRepository.set(null);
      this.activeSnapshot.set(null);
      this.selectedSymbol.set(null);
      this.snapshots.set([]);
      if (persisted.repoIdentity) {
        this.notice.set(
          'O repositório salvo não está mais indexado; nenhum repositório ativo.',
        );
        this.dropPersisted();
      }
      return;
    }

    const match = persisted.repoIdentity
      ? this.resolveRepository(persisted.repoIdentity)
      : null;
    if (persisted.repoIdentity && !match) {
      this.notice.set(
        'O repositório salvo não está mais indexado; usando o primeiro da lista.',
      );
    }
    this.selectRepository(match ?? items[0]);

    if (match && persisted.snapshotId !== null) {
      await this.restorePersistedSnapshot(persisted.snapshotId);
    }
  }

  /** Restores a persisted snapshot pin; drops it explicitly when stale. */
  private async restorePersistedSnapshot(snapshotId: number): Promise<void> {
    await this.ensureSnapshotsLoaded();
    if (this.snapshotsPhase() === 'error') {
      // Cannot validate — do not claim the snapshot is gone.
      this.activeSnapshot.set(null);
      this.persist();
      this.notice.set(
        'Não foi possível validar o snapshot salvo; usando o mais recente.',
      );
      return;
    }
    const snapshot = this.snapshots().find((s) => s.id === snapshotId) ?? null;
    if (snapshot && isReadySnapshot(snapshot)) {
      this.selectSnapshot(snapshot);
      return;
    }
    this.activeSnapshot.set(null);
    this.persist();
    this.notice.set(
      snapshot
        ? `O snapshot #${snapshotId} salvo não está mais READY; usando o mais recente.`
        : `O snapshot #${snapshotId} salvo não existe mais; usando o mais recente.`,
    );
  }

  private async applySnapshotParam(snapshotId: number): Promise<void> {
    if (!this.activeRepository()) {
      this.notice.set(
        `O link aponta para o snapshot #${snapshotId}, mas nenhum repositório está ativo.`,
      );
      return;
    }
    await this.ensureSnapshotsLoaded();
    if (this.snapshotsPhase() === 'error') {
      this.notice.set(
        'Não foi possível verificar o snapshot do link; tente novamente.',
      );
      return;
    }
    const snapshot = this.snapshots().find((s) => s.id === snapshotId) ?? null;
    if (snapshot && isReadySnapshot(snapshot)) {
      this.selectSnapshot(snapshot);
      return;
    }
    this.notice.set(
      snapshot
        ? `O snapshot #${snapshotId} do link está ${snapshot.status}; usando o mais recente.`
        : `O snapshot #${snapshotId} do link não existe neste repositório.`,
    );
  }

  private persist(): void {
    if (typeof localStorage === 'undefined') {
      return;
    }
    const repo = this.activeRepository();
    if (!repo) {
      this.dropPersisted();
      return;
    }
    const payload: PersistedContext = {
      repoIdentity: repo.identity_key,
      snapshotId: this.activeSnapshot()?.id ?? null,
    };
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(payload));
    } catch {
      // Storage unavailable (private mode/quota) — context stays in-memory.
    }
  }

  private dropPersisted(): void {
    if (typeof localStorage === 'undefined') {
      return;
    }
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore — storage unavailable
    }
  }

  private readPersisted(): PersistedContext {
    if (typeof localStorage === 'undefined') {
      return EMPTY_PERSISTED;
    }
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) {
        return EMPTY_PERSISTED;
      }
      const parsed = JSON.parse(raw) as Partial<PersistedContext>;
      return {
        repoIdentity:
          typeof parsed.repoIdentity === 'string' ? parsed.repoIdentity : null,
        snapshotId:
          typeof parsed.snapshotId === 'number' ? parsed.snapshotId : null,
      };
    } catch {
      return EMPTY_PERSISTED;
    }
  }
}
