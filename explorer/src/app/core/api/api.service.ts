import { Inject, Injectable } from '@angular/core';
import { ATLAS_API_BASE_URL } from './api.config';
import { ApiAbortedError, ApiHttpError, ApiUnreachableError } from './api.errors';
import type {
  ContextPack,
  ImpactResult,
  RelationEdge,
  Repository,
  SearchResult,
  Snapshot,
  SymbolNode,
} from '../models/api.models';

export interface ApiRequestOptions {
  signal?: AbortSignal;
}

/* ---------------------------------------------------------------------------
 * v1 wire contracts (additive — cycle-6 features).
 *
 * These interfaces mirror the live backend handlers in
 * `src/code_atlas/api/handlers.py`. They intentionally coexist with the
 * legacy models in `core/models/api.models.ts`: the legacy shapes describe a
 * pre-v1 contract kept for the graph/symbols workstreams, while the types
 * below describe what the running server actually returns today.
 * ------------------------------------------------------------------------- */

/** GET /health */
export interface ApiHealthResponse {
  status: string;
  service: string;
  version: number;
}

/** GET /capabilities */
export interface ApiSearchCapabilities {
  strategies: string[];
  fts5: boolean;
  vector: boolean;
  detail: string;
}

export interface ApiCapabilitiesResponse {
  search: ApiSearchCapabilities;
  api_version: number;
  readonly: boolean;
}

/** GET /repositories (envelope — legacy getRepositories() predates it). */
export interface ApiRepositoryItem {
  id: number;
  identity_key: string;
  root_handle: string;
}

export interface ApiRepositoriesResponse {
  items: ApiRepositoryItem[];
  total: number;
}

/** GET /repositories/{id}/snapshots */
export interface ApiSnapshotItem {
  id: number;
  status: string;
  commit_sha: string | null;
  git_ref: string | null;
  created_at: string;
}

export interface ApiSnapshotsResponse {
  items: ApiSnapshotItem[];
}

/** GET /index/status */
export interface ApiIndexStatusItem {
  repository_id: number;
  root_handle: string;
  ready_snapshots: number;
  latest_ready: number | null;
  counts: Record<string, number>;
}

export interface ApiIndexStatusResponse {
  items: ApiIndexStatusItem[];
}

/** GET /search — SearchResult envelope (schema_version 1). */
export type ApiSearchStrategy =
  | 'lexical'
  | 'structural'
  | 'hybrid'
  | 'vector'
  | 'graph';

export interface ApiSearchParams {
  q: string;
  strategy?: ApiSearchStrategy;
  language?: string;
  kind?: string;
  path?: string;
  limit?: number;
  cursor?: string | null;
  repository_root?: string;
  snapshot_id?: number;
}

export interface ApiSearchHit {
  occurrence_id: number;
  qualified_name: string;
  kind: string;
  language: string;
  path: string;
  span: { start: number; end: number } | null;
  score: number;
  strategy: string;
  matched_fields: string[];
  evidence: string[];
}

export interface ApiSearchDiagnostic {
  code: string;
  severity: string;
  message: string;
}

export interface ApiSearchResponse {
  schema_version: number;
  repository: string;
  snapshot_id: number;
  query: string;
  strategy: string;
  ranking_version: string;
  items: ApiSearchHit[];
  limit: number;
  truncated: boolean;
  next_cursor: string | null;
  diagnostics: ApiSearchDiagnostic[];
}

/** POST /context — ContextPack (schema_version 2). */
export type ApiContextLevel = 'orientation' | 'focus' | 'deep';

export interface ApiContextRequest {
  task: string;
  repository_root?: string;
  snapshot_id?: number | null;
  level?: ApiContextLevel;
  budget_tokens?: number;
  max_symbols?: number;
}

export interface ApiPackEvidence {
  kind: string;
  ref: string;
  detail: string;
}

export interface ApiContextItem {
  item_id: string;
  kind: string;
  priority: number;
  title: string;
  body: string;
  est_tokens: number;
  symbol_key: string;
  path: string;
  evidence: ApiPackEvidence[];
  expandable: boolean;
}

export interface ApiExpansionRef {
  ref_id: string;
  kind: string;
  reason: string;
}

export interface ApiPackBudget {
  limit: number;
  reserved_meta: number;
  reserved_evidence: number;
  reserved_diagnostics: number;
  available_items: number;
  estimator: string;
}

export interface ApiPackDiagnostics {
  codes: string[];
  messages: string[];
  insufficient_budget: boolean;
  mandatory_context_omitted: boolean;
  suggested_minimum_budget: number;
}

export interface ApiPackManifest {
  schema_version: number;
  repository: string;
  snapshot_id: number;
  level: string;
  estimator: string;
  /** True = token counts are estimates, never exact. */
  tokens_estimated: boolean;
  selected: number;
  omitted: number;
  truncated: boolean;
  freshness: string;
  ranking_explanation: string;
  tokens_payload: number;
}

export interface ApiContextPack {
  manifest: ApiPackManifest;
  budget: ApiPackBudget;
  items: ApiContextItem[];
  selected_symbols: string[];
  relevant_files: string[];
  dependencies: ApiPackEvidence[];
  unresolved_refs: string[];
  expansion_refs: ApiExpansionRef[];
  diagnostics: ApiPackDiagnostics;
}

/** POST /impact/analyze — ImpactResult (schema_version 1). */
export type ApiEvidenceStrength =
  | 'verified'
  | 'supported'
  | 'possible'
  | 'unknown';

export interface ApiImpactRequest {
  repository_root: string;
  base_revision?: string;
  target_revision?: string;
  symbol?: string | null;
  file?: string | null;
  base_snapshot_id?: number | null;
  target_snapshot_id?: number | null;
  include_possible?: boolean;
  known_only?: boolean;
}

export interface ApiFileChange {
  kind: string;
  old_path: string | null;
  new_path: string | null;
  old_hash: string | null;
  new_hash: string | null;
  similarity: number | null;
  binary: boolean;
}

export interface ApiChangeSet {
  base_revision: string;
  target_revision: string;
  files: ApiFileChange[];
  source: string;
  dirty_worktree: boolean;
  commit_ids: string[];
}

export interface ApiSymbolChange {
  symbol_key: string;
  qualified_name: string;
  language: string;
  kind: string;
  change: string;
  strength: ApiEvidenceStrength;
  old_signature: string | null;
  new_signature: string | null;
  old_path: string | null;
  new_path: string | null;
  old_occurrence_id: number | null;
  new_occurrence_id: number | null;
  detail: string;
}

export interface ApiContractChange {
  symbol_key: string;
  classification: string;
  compatibility: string;
  old_signature: string | null;
  new_signature: string | null;
  strength: ApiEvidenceStrength;
  detail: string;
}

export interface ApiImpactPath {
  origin_symbol_key: string;
  affected_symbol_key: string;
  hops: number;
  relation_ids: number[];
  intermediate_keys: string[];
  resolution_statuses: string[];
  fully_verified: boolean;
  reason: string;
}

export interface ApiImpactCandidate {
  symbol_key: string;
  qualified_name: string;
  language: string;
  kind: string;
  impact: string;
  strength: ApiEvidenceStrength;
  path: ApiImpactPath | null;
  source_path: string | null;
  occurrence_id: number | null;
  consumer_state: string;
}

export interface ApiTestCandidate {
  test_file: string;
  test_symbol: string | null;
  relationship: string;
  reason: string;
  strength: ApiEvidenceStrength;
  occurrence_id: number | null;
  revision: string;
}

export interface ApiRiskAssessment {
  category: string;
  uncertainty: string;
  reasons: string[];
  formula_version: string;
}

export interface ApiImpactDiagnostic {
  code: string;
  message: string;
}

export interface ApiImpactResult {
  schema_version: number;
  analyzer_version: string;
  repository: string;
  base_revision: string;
  target_revision: string;
  base_snapshot_id: number | null;
  target_snapshot_id: number | null;
  change_set: ApiChangeSet | null;
  symbol_changes: ApiSymbolChange[];
  contract_changes: ApiContractChange[];
  impacts: ApiImpactCandidate[];
  test_candidates: ApiTestCandidate[];
  risk: ApiRiskAssessment;
  unknown_coverage: string[];
  truncated: boolean;
  diagnostics: ApiImpactDiagnostic[];
}

/* ------------------------------------------------------------------ */
/* Graph endpoints (lf-0088 / lf-0089)                                 */
/* ------------------------------------------------------------------ */

/** GET /graph/overview — one aggregate item per source file. */
export interface ApiGraphOverviewItem {
  path: string;
  symbols: number;
  language: string | null;
}

export interface ApiGraphOverview {
  snapshot_id: number;
  repository_id: number;
  items: ApiGraphOverviewItem[];
  total_files: number;
  truncated: boolean;
}

export type ApiGraphDirection = 'in' | 'out' | 'both';

export interface ApiGraphSpan {
  start: number;
  end: number;
}

/**
 * Occurrence node as returned by /graph/neighbors and /symbols/{id}.
 * `id` already comes formatted as `occ:{occurrence_id}` by the backend —
 * the explorer uses it verbatim as the stable graph node key.
 */
export interface ApiGraphNode {
  id: string;
  occurrence_id: number;
  symbol_id: number;
  qname: string;
  kind: string;
  language: string;
  path: string;
  span: ApiGraphSpan;
  parse_status: string;
}

export interface ApiGraphEdge {
  id: number;
  from_id: number;
  to_id: number;
  type: string;
  resolution_status: string;
  resolver: string;
  /**
   * Present in the relations table but not emitted by /graph/neighbors
   * today; declared optional so the inspector never invents it.
   */
  evidence_ids?: number[];
}

export interface ApiGraphNeighbors {
  snapshot_id: number;
  edges: ApiGraphEdge[];
  nodes: ApiGraphNode[];
  truncated: boolean;
}

export interface ApiRelationSummaryEntry {
  type: string;
  resolution_status: string;
  n: number;
}

/** GET /symbols/{occurrence_id} — node + grouped relation counts. */
export interface ApiSymbolDetail extends ApiGraphNode {
  relations_summary: ApiRelationSummaryEntry[];
}

/** Optional scoping shared by the graph/symbol read endpoints. */
export interface ApiGraphScope {
  repositoryId?: number;
  snapshotId?: number;
  limit?: number;
}

/**
 * Thin fetch-based client for the local Code Atlas HTTP API.
 *
 * Design decisions:
 * - fetch() over HttpClient: the API is strictly local and loopback-only,
 *   and fetch keeps the dependency surface (and bundle) minimal while still
 *   giving us first-class AbortController support per call.
 * - Every call accepts an external AbortSignal AND creates its own internal
 *   AbortController so callers never need to manage cleanup manually; call
 *   `cancelAll()` on component destroy to abort any in-flight calls made
 *   without an explicit signal.
 */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private readonly inFlight = new Set<AbortController>();

  constructor(@Inject(ATLAS_API_BASE_URL) private readonly baseUrl: string) {}

  getContextPack(
    symbolId: string,
    options?: ApiRequestOptions,
  ): Promise<ContextPack> {
    return this.request<ContextPack>(
      'GET',
      `/symbols/${encodeURIComponent(symbolId)}/context-pack`,
      undefined,
      options,
    );
  }

  /* -----------------------------------------------------------------------
   * v1 endpoints (additive — used by the cycle-6 feature screens).
   * --------------------------------------------------------------------- */

  /** GET /health — liveness probe used by the diagnostics center. */
  getHealth(options?: ApiRequestOptions): Promise<ApiHealthResponse> {
    return this.request<ApiHealthResponse>('GET', '/health', undefined, options);
  }

  /** GET /capabilities — backend feature flags (vector search, readonly). */
  getCapabilities(options?: ApiRequestOptions): Promise<ApiCapabilitiesResponse> {
    return this.request<ApiCapabilitiesResponse>(
      'GET',
      '/capabilities',
      undefined,
      options,
    );
  }

  /** GET /repositories — envelope shape ({items, total}). */
  listRepositories(options?: ApiRequestOptions): Promise<ApiRepositoriesResponse> {
    return this.request<ApiRepositoriesResponse>(
      'GET',
      '/repositories',
      undefined,
      options,
    );
  }

  /** GET /repositories/{id}/snapshots */
  listSnapshots(
    repositoryId: number,
    options?: ApiRequestOptions,
  ): Promise<ApiSnapshotsResponse> {
    return this.request<ApiSnapshotsResponse>(
      'GET',
      `/repositories/${encodeURIComponent(repositoryId)}/snapshots`,
      undefined,
      options,
    );
  }

  /** GET /index/status — per-repository snapshot + table counts. */
  getIndexStatus(options?: ApiRequestOptions): Promise<ApiIndexStatusResponse> {
    return this.request<ApiIndexStatusResponse>(
      'GET',
      '/index/status',
      undefined,
      options,
    );
  }

  /**
   * GET /search — versioned search envelope with cursor pagination.
   * `params.cursor` resumes a previous result set (fingerprint-checked
   * server-side); pass `next_cursor` verbatim, never fabricate one.
   */
  searchCode(
    params: ApiSearchParams,
    options?: ApiRequestOptions,
  ): Promise<ApiSearchResponse> {
    const qs = new URLSearchParams();
    qs.set('q', params.q);
    if (params.strategy) qs.set('strategy', params.strategy);
    if (params.language) qs.set('language', params.language);
    if (params.kind) qs.set('kind', params.kind);
    if (params.path) qs.set('path', params.path);
    if (params.limit !== undefined) qs.set('limit', String(params.limit));
    if (params.cursor) qs.set('cursor', params.cursor);
    if (params.repository_root) qs.set('repository_root', params.repository_root);
    if (params.snapshot_id !== undefined) {
      qs.set('snapshot_id', String(params.snapshot_id));
    }
    return this.request<ApiSearchResponse>(
      'GET',
      `/search?${qs.toString()}`,
      undefined,
      options,
    );
  }

  /** POST /impact/analyze — revision-pair impact analysis. */
  analyzeImpact(
    requestBody: ApiImpactRequest,
    options?: ApiRequestOptions,
  ): Promise<ApiImpactResult> {
    return this.request<ApiImpactResult>(
      'POST',
      '/impact/analyze',
      requestBody,
      options,
    );
  }

  /**
   * POST /context — builds a ContextPack for a task.
   * The pack stays in-memory in the caller; nothing is persisted or sent
   * anywhere else by this client.
   */
  buildContextPack(
    requestBody: ApiContextRequest,
    options?: ApiRequestOptions,
  ): Promise<ApiContextPack> {
    return this.request<ApiContextPack>('POST', '/context', requestBody, options);
  }

  /**
   * GET /graph/overview — file-level aggregate for the architecture view.
   * Never returns the full symbol graph; `truncated` reports whether
   * `total_files` exceeds the returned window.
   */
  getGraphOverview(
    scope?: ApiGraphScope,
    options?: ApiRequestOptions,
  ): Promise<ApiGraphOverview> {
    return this.request<ApiGraphOverview>(
      'GET',
      `/graph/overview${graphScopeQuery(scope)}`,
      undefined,
      options,
    );
  }

  /**
   * GET /graph/neighbors — lazy expansion around one occurrence.
   * `occurrence_id` is the numeric part of a node key (`occ:{id}`).
   */
  getGraphNeighbors(
    occurrenceId: number,
    direction: ApiGraphDirection = 'both',
    scope?: ApiGraphScope,
    options?: ApiRequestOptions,
  ): Promise<ApiGraphNeighbors> {
    const qs = new URLSearchParams({ occurrence_id: String(occurrenceId), direction });
    return this.request<ApiGraphNeighbors>(
      'GET',
      `/graph/neighbors?${qs.toString()}${graphScopeQuery(scope, '&')}`,
      undefined,
      options,
    );
  }

  /**
   * GET /symbols/{occurrence_id} — node detail + relations_summary.
   * Distinct from the legacy getSymbol() which targets a pre-v1 route.
   */
  getSymbolDetail(
    occurrenceId: number,
    scope?: ApiGraphScope,
    options?: ApiRequestOptions,
  ): Promise<ApiSymbolDetail> {
    return this.request<ApiSymbolDetail>(
      'GET',
      `/symbols/${encodeURIComponent(occurrenceId)}${graphScopeQuery(scope)}`,
      undefined,
      options,
    );
  }

  /** Aborts every request currently in flight that this service started. */
  cancelAll(): void {
    for (const controller of this.inFlight) {
      controller.abort();
    }
    this.inFlight.clear();
  }

  private async request<T>(
    method: 'GET' | 'POST' | 'PUT' | 'DELETE',
    path: string,
    body: unknown,
    options?: ApiRequestOptions,
  ): Promise<T> {
    const controller = new AbortController();
    this.inFlight.add(controller);

    if (options?.signal) {
      if (options.signal.aborted) {
        controller.abort();
      } else {
        options.signal.addEventListener('abort', () => controller.abort(), { once: true });
      }
    }

    try {
      const response = await fetch(`${this.baseUrl}${path}`, {
        method,
        signal: controller.signal,
        headers: body ? { 'Content-Type': 'application/json' } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });

      if (!response.ok) {
        let parsedBody: unknown = null;
        try {
          parsedBody = await response.json();
        } catch {
          parsedBody = null;
        }
        throw new ApiHttpError(response.status, response.statusText, parsedBody);
      }

      if (response.status === 204) {
        return undefined as T;
      }

      return (await response.json()) as T;
    } catch (error) {
      if (error instanceof ApiHttpError) {
        throw error;
      }
      if (error instanceof DOMException && error.name === 'AbortError') {
        throw new ApiAbortedError();
      }
      throw new ApiUnreachableError(error);
    } finally {
      this.inFlight.delete(controller);
    }
  }
}

/**
 * Serializes an ApiGraphScope into a query string, prefixed with `?` (or the
 * given separator when the caller already started a query string). Returns
 * an empty string when no scope fields are set.
 */
function graphScopeQuery(scope: ApiGraphScope | undefined, separator = '?'): string {
  if (!scope) return '';
  const qs = new URLSearchParams();
  if (scope.repositoryId !== undefined) qs.set('repository_id', String(scope.repositoryId));
  if (scope.snapshotId !== undefined) qs.set('snapshot_id', String(scope.snapshotId));
  if (scope.limit !== undefined) qs.set('limit', String(scope.limit));
  const text = qs.toString();
  return text ? `${separator}${text}` : '';
}
