/**
 * Typed contracts mirrored from the local Code Atlas HTTP API
 * (http://127.0.0.1:8741/api/v1). Keep this file in sync with the backend
 * contract; do not widen types with `any`.
 */

export interface Repository {
  id: string;
  root_handle: string;
}

export type SnapshotStatus = 'pending' | 'indexing' | 'ready' | 'failed' | 'stale';

export interface Snapshot {
  id: string;
  status: SnapshotStatus;
  commit_sha: string | null;
  worktree_digest: string | null;
  created_at: string;
}

export type SymbolLanguage = string;
export type SymbolKind =
  | 'module'
  | 'class'
  | 'function'
  | 'method'
  | 'variable'
  | 'interface'
  | 'enum'
  | 'constant'
  | 'other';

export interface SymbolNode {
  id: string;
  key: string;
  qname: string;
  language: SymbolLanguage;
  kind: SymbolKind;
  path: string;
}

export type RelationType =
  | 'calls'
  | 'imports'
  | 'inherits'
  | 'implements'
  | 'references'
  | 'defines'
  | 'other';

/**
 * Evidence states: how confidently the resolver stands behind an edge.
 * This is the backbone of the evidence-badge visual system.
 */
export type ResolutionStatus = 'verified' | 'supported' | 'possible' | 'unresolved' | 'external';

export interface RelationEdge {
  id: string;
  from: string;
  to: string;
  type: RelationType;
  resolution_status: ResolutionStatus;
  resolver: string;
}

export interface SearchResult {
  items: SymbolNode[];
  next_cursor: string | null;
  truncated: boolean;
}

export interface Diagnostic {
  code: string;
  message: string;
}

export interface SymbolChange {
  symbol_id: string;
  qname: string;
  change_kind: string;
}

export interface Impact {
  symbol_id: string;
  qname: string;
  path: string;
  resolution_status: ResolutionStatus;
  reason: string;
}

export interface TestCandidate {
  path: string;
  qname: string | null;
  confidence: number;
}

export interface ImpactResult {
  symbol_changes: SymbolChange[];
  impacts: Impact[];
  test_candidates: TestCandidate[];
  risk: 'low' | 'medium' | 'high' | 'unknown';
  diagnostics: Diagnostic[];
  truncated: boolean;
}

/**
 * ContextPack shape is intentionally loose (backend schema evolves quickly).
 * Known fields are typed; everything else is preserved as unknown values.
 */
export interface ContextPack {
  id: string;
  title: string;
  sections: ContextPackSection[];
  diagnostics: Diagnostic[];
  [key: string]: unknown;
}

export interface ContextPackSection {
  heading: string;
  content: string;
  [key: string]: unknown;
}
