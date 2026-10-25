import type {
  ApiIndexStatusItem,
  ApiRepositoryItem,
  ApiSnapshotItem,
} from '../../core/api/api.service';

/**
 * One row of the repository control center. Every field is nullable where
 * the backend may legitimately not provide the data — the template renders
 * an honest "—" for those, never a fabricated number.
 */
export interface RepositoryRow {
  repository: ApiRepositoryItem;
  /** READY snapshot count from GET /index/status (null when status missing). */
  readySnapshots: number | null;
  /** Id of the newest READY snapshot (index/status, else snapshot list). */
  latestReadyId: number | null;
  /** Full snapshot record when the per-repo snapshot fetch succeeded. */
  latestReady: ApiSnapshotItem | null;
  /** Indexed files in the latest READY snapshot (file_inventory count). */
  files: number | null;
  /** Symbol occurrences in the latest READY snapshot. */
  symbols: number | null;
  /** Relations in the latest READY snapshot. */
  relations: number | null;
}

export function isReadySnapshot(snapshot: ApiSnapshotItem): boolean {
  return (snapshot.status ?? '').toUpperCase() === 'READY';
}

/**
 * Newest READY snapshot of a list. The API returns snapshots ordered by
 * id DESC, so the first READY item is the latest — we still scan (rather
 * than trusting position 0) because non-READY entries come first.
 */
export function findLatestReady(
  items: readonly ApiSnapshotItem[],
): ApiSnapshotItem | null {
  return items.find(isReadySnapshot) ?? null;
}

export function shortSha(commitSha: string | null): string {
  return commitSha ? commitSha.slice(0, 12) : '';
}

function countOrNull(counts: Record<string, number> | undefined, key: string): number | null {
  const value = counts?.[key];
  return typeof value === 'number' ? value : null;
}

/**
 * Joins the three honest data sources the API offers for a repository:
 * GET /repositories (identity), GET /index/status (READY counts +
 * latest_ready id) and GET /repositories/{id}/snapshots (snapshot detail).
 * A repo missing from index/status or whose snapshot fetch failed keeps
 * nulls — the UI shows "—" instead of guessing.
 */
export function buildRepositoryRows(
  repositories: readonly ApiRepositoryItem[],
  statusItems: readonly ApiIndexStatusItem[],
  snapshotsByRepo: ReadonlyMap<number, readonly ApiSnapshotItem[]>,
): RepositoryRow[] {
  const statusById = new Map(statusItems.map((item) => [item.repository_id, item]));
  return repositories.map((repository) => {
    const status = statusById.get(repository.id);
    const latestReady = findLatestReady(snapshotsByRepo.get(repository.id) ?? []);
    return {
      repository,
      readySnapshots:
        typeof status?.ready_snapshots === 'number' ? status.ready_snapshots : null,
      latestReadyId: status?.latest_ready ?? latestReady?.id ?? null,
      latestReady,
      files: countOrNull(status?.counts, 'file_inventory'),
      symbols: countOrNull(status?.counts, 'symbol_occurrences'),
      relations: countOrNull(status?.counts, 'relations'),
    };
  });
}
