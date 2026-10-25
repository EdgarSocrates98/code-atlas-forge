import type { ResolutionStatus } from '../../core/models/api.models';

/** View modes of the graph screen. */
export type GraphMode = 'architecture' | 'symbols';

/** How far the breadcrumb drill has gone. */
export interface DrillLevel {
  kind: 'architecture' | 'file';
  /** File path when kind === 'file'. */
  path?: string;
  /** Expected symbol count reported by the overview aggregate. */
  expectedSymbols?: number;
}

/**
 * Stable node view-model. `id` is always either the backend-provided
 * `occ:{occurrence_id}` (symbol nodes) or `file:{path}` (architecture
 * aggregates) — never a generated uuid, so dedup is by construction.
 */
export interface GraphNodeVM {
  id: string;
  kind: 'symbol' | 'file';
  /** Display label (symbol name or file basename). */
  label: string;
  /** Full qualified name when a symbol node. */
  qname: string;
  symbolKind: string;
  language: string;
  path: string;
  occurrenceId: number | null;
  symbolId: number | null;
  span: { start: number; end: number } | null;
  parseStatus: string;
  /** Aggregate count for file nodes. */
  symbolCount: number | null;
  /** Position in graph coordinates; absent until layout ran. */
  x: number | null;
  y: number | null;
  pinned: boolean;
  expanded: boolean;
}

export interface GraphEdgeVM {
  /** `edge:{relation_id}` — stable across merges. */
  id: string;
  /** Node ids (`occ:{n}` / `file:{path}`). */
  from: string;
  to: string;
  type: string;
  resolutionStatus: string;
  resolver: string;
  evidenceIds: number[];
}

/** Async phase of the whole graph surface. */
export type GraphPhase = 'idle' | 'loading' | 'ready' | 'empty' | 'error' | 'offline';

/** Bump pair describing one data revision of the graph. */
export interface GraphDataVersion {
  /** Monotonic revision — changes whenever the node/edge set changes. */
  version: number;
  /**
   * True when this revision needs a layout pass (fresh load or explicit
   * re-layout request). False for incremental merges, selection changes
   * and restores — positions are carried on the nodes themselves.
   */
  needsLayout: boolean;
}

export const DEFAULT_NODE_CAP = 500;
export const OVERVIEW_LIMIT = 2000;
export const NEIGHBOR_LIMIT = 100;
/** How many seed occurrences get a neighbors fetch during file drill. */
export const DRILL_NEIGHBOR_BUDGET = 12;
export const DRILL_SEARCH_LIMIT = 200;
export const DRILL_MAX_PAGES = 5;

export const ALL_RESOLUTION_STATUSES: ResolutionStatus[] = [
  'verified',
  'supported',
  'possible',
  'unresolved',
  'external',
];
