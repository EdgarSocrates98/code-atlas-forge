import type {
  ApiCapabilitiesResponse,
  ApiIndexStatusItem,
} from '../../core/api/api.service';

export const INDEX_COUNT_KEYS = [
  'file_inventory',
  'symbol_occurrences',
  'relations',
  'evidence',
] as const;

export const INDEX_COUNT_LABELS: Record<string, string> = {
  file_inventory: 'Arquivos',
  symbol_occurrences: 'Ocorrências de símbolo',
  relations: 'Relações',
  evidence: 'Evidências',
};

export interface IndexStatusRow {
  repositoryId: number;
  rootHandle: string;
  readySnapshots: number;
  latestReady: number | null;
  counts: { key: string; label: string; value: number | null }[];
  hasCounts: boolean;
}

export function toIndexStatusRow(item: ApiIndexStatusItem): IndexStatusRow {
  const counts = INDEX_COUNT_KEYS.map((key) => ({
    key,
    label: INDEX_COUNT_LABELS[key] ?? key,
    value: typeof item.counts?.[key] === 'number' ? item.counts[key] : null,
  }));
  return {
    repositoryId: item.repository_id,
    rootHandle: item.root_handle,
    readySnapshots: item.ready_snapshots,
    latestReady: item.latest_ready,
    counts,
    hasCounts: counts.some((c) => c.value !== null),
  };
}

export interface CapabilitiesView {
  strategies: readonly string[];
  fts5: boolean;
  vector: boolean;
  vectorLabel: string;
  detail: string;
  apiVersion: number;
  readonly: boolean;
}

export function toCapabilitiesView(res: ApiCapabilitiesResponse): CapabilitiesView {
  const search = res.search;
  const vector = Boolean(search?.vector);
  return {
    strategies: search?.strategies ?? [],
    fts5: Boolean(search?.fts5),
    vector,
    vectorLabel: vector ? 'Busca vetorial disponível' : 'Busca vetorial indisponível',
    detail: search?.detail ?? '',
    apiVersion: res.api_version ?? 0,
    readonly: Boolean(res.readonly),
  };
}
