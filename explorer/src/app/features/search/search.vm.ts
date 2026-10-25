import type {
  ApiSearchDiagnostic,
  ApiSearchHit,
  ApiSearchResponse,
  ApiSearchStrategy,
} from '../../core/api/api.service';

/** View-model for one search hit. All fields pre-formatted for display. */
export interface SearchHitView {
  occurrenceId: number;
  /** Deep-link target for the graph route: `occ:{id}`. */
  graphFocus: string;
  qualifiedName: string;
  kind: string;
  language: string;
  path: string;
  /** `12–48` style span or null when the hit carries no span. */
  spanText: string | null;
  scoreText: string;
  strategy: string;
  matchedFields: readonly string[];
  evidence: readonly string[];
}

export interface SearchView {
  items: readonly SearchHitView[];
  nextCursor: string | null;
  truncated: boolean;
  diagnostics: readonly ApiSearchDiagnostic[];
  query: string;
  strategy: string;
  repository: string;
  snapshotId: number;
  rankingVersion: string;
}

export function toSearchHitView(hit: ApiSearchHit): SearchHitView {
  const span = hit.span;
  return {
    occurrenceId: hit.occurrence_id,
    graphFocus: `occ:${hit.occurrence_id}`,
    qualifiedName: hit.qualified_name,
    kind: hit.kind,
    language: hit.language,
    path: hit.path,
    spanText: span ? `${span.start}–${span.end}` : null,
    scoreText: hit.score.toFixed(2),
    strategy: hit.strategy,
    matchedFields: hit.matched_fields ?? [],
    evidence: hit.evidence ?? [],
  };
}

export function toSearchView(res: ApiSearchResponse): SearchView {
  return {
    items: (res.items ?? []).map(toSearchHitView),
    nextCursor: res.next_cursor ?? null,
    truncated: Boolean(res.truncated),
    diagnostics: res.diagnostics ?? [],
    query: res.query,
    strategy: res.strategy,
    repository: res.repository,
    snapshotId: res.snapshot_id,
    rankingVersion: res.ranking_version,
  };
}

/**
 * Appends a `loadMore` page to the accumulated items, deduplicating by
 * occurrence_id (overlapping pages must not double-render a hit).
 */
export function appendSearchPage(
  current: readonly SearchHitView[],
  page: ApiSearchResponse,
): readonly SearchHitView[] {
  const seen = new Set(current.map((i) => i.occurrenceId));
  const fresh = (page.items ?? [])
    .map(toSearchHitView)
    .filter((i) => !seen.has(i.occurrenceId));
  return [...current, ...fresh];
}

/* ------------------------------------------------------------------------- */
/* Search history — localStorage, strictly opt-in.                            */
/* ------------------------------------------------------------------------- */

export const SEARCH_HISTORY_ENABLED_KEY = 'atlas-search-history-enabled';
export const SEARCH_HISTORY_KEY = 'atlas-search-history';

const HISTORY_LIMIT = 20;

export interface SearchHistoryEntry {
  query: string;
  strategy: ApiSearchStrategy | '';
  language: string;
  kind: string;
  path: string;
  at: number;
}

/**
 * Small storage-backed history. The injected Storage makes it trivially
 * testable (pass a stub); `null` disables persistence entirely.
 * Writes only ever happen when the user explicitly opted in — and opting
 * out wipes the stored list.
 */
export class SearchHistoryStore {
  constructor(private readonly storage: Storage | null) {}

  isEnabled(): boolean {
    if (!this.storage) return false;
    try {
      return this.storage.getItem(SEARCH_HISTORY_ENABLED_KEY) === '1';
    } catch {
      return false;
    }
  }

  setEnabled(enabled: boolean): void {
    if (!this.storage) return;
    try {
      this.storage.setItem(SEARCH_HISTORY_ENABLED_KEY, enabled ? '1' : '0');
      if (!enabled) {
        this.storage.removeItem(SEARCH_HISTORY_KEY);
      }
    } catch {
      /* storage unavailable — preference stays in-memory */
    }
  }

  list(): SearchHistoryEntry[] {
    if (!this.storage || !this.isEnabled()) return [];
    try {
      const raw = this.storage.getItem(SEARCH_HISTORY_KEY);
      if (!raw) return [];
      const parsed: unknown = JSON.parse(raw);
      if (!Array.isArray(parsed)) return [];
      return parsed
        .filter((e): e is SearchHistoryEntry => isHistoryEntry(e))
        .slice(0, HISTORY_LIMIT);
    } catch {
      return [];
    }
  }

  /** No-op unless history is enabled — never write silently. */
  add(entry: Omit<SearchHistoryEntry, 'at'>): void {
    if (!this.isEnabled()) return;
    const next: SearchHistoryEntry = { ...entry, at: Date.now() };
    const deduped = this.list().filter((e) => !sameSearch(e, next));
    this.write([next, ...deduped].slice(0, HISTORY_LIMIT));
  }

  clear(): void {
    if (!this.storage) return;
    try {
      this.storage.removeItem(SEARCH_HISTORY_KEY);
    } catch {
      /* ignore */
    }
  }

  private write(entries: SearchHistoryEntry[]): void {
    if (!this.storage) return;
    try {
      this.storage.setItem(SEARCH_HISTORY_KEY, JSON.stringify(entries));
    } catch {
      /* quota / private mode — keep in-memory only */
    }
  }
}

function sameSearch(a: SearchHistoryEntry, b: SearchHistoryEntry): boolean {
  return (
    a.query === b.query &&
    a.strategy === b.strategy &&
    a.language === b.language &&
    a.kind === b.kind &&
    a.path === b.path
  );
}

function isHistoryEntry(value: unknown): value is SearchHistoryEntry {
  if (typeof value !== 'object' || value === null) return false;
  const v = value as Record<string, unknown>;
  return typeof v['query'] === 'string' && typeof v['at'] === 'number';
}
