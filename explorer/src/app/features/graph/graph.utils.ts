import type {
  ApiFileOccurrence,
  ApiGraphEdge,
  ApiGraphNode,
  ApiGraphOverviewItem,
  ApiSearchHit,
} from '../../core/api/api.service';
import type { ResolutionStatus } from '../../core/models/api.models';
import type { GraphEdgeVM, GraphNodeVM } from './graph.models';

/* ------------------------------------------------------------------ */
/* Stable ids                                                          */
/* ------------------------------------------------------------------ */

export function occNodeId(occurrenceId: number): string {
  return `occ:${occurrenceId}`;
}

export function fileNodeId(path: string): string {
  return `file:${path}`;
}

export function edgeId(relationId: number): string {
  return `edge:${relationId}`;
}

/** Numeric occurrence id behind an `occ:{n}` node key; null otherwise. */
export function parseOccurrenceId(nodeId: string): number | null {
  if (!nodeId.startsWith('occ:')) return null;
  const n = Number(nodeId.slice(4));
  return Number.isFinite(n) && n > 0 ? n : null;
}

/* ------------------------------------------------------------------ */
/* Label helpers                                                       */
/* ------------------------------------------------------------------ */

export function basename(path: string): string {
  const i = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
  return i >= 0 ? path.slice(i + 1) : path;
}

/** File basename without its last extension — used as drill seed query. */
export function fileStem(path: string): string {
  const base = basename(path);
  const dot = base.lastIndexOf('.');
  return dot > 0 ? base.slice(0, dot) : base;
}

/** Short display name: last segment of a qualified name. */
export function shortName(qname: string): string {
  const i = qname.lastIndexOf('.');
  const name = i >= 0 ? qname.slice(i + 1) : qname;
  return name || qname;
}

/* ------------------------------------------------------------------ */
/* DTO → view-model mapping (pure, unit-tested)                        */
/* ------------------------------------------------------------------ */

export function toSymbolNodeVM(dto: ApiGraphNode): GraphNodeVM {
  return {
    id: occNodeId(dto.occurrence_id),
    kind: 'symbol',
    label: shortName(dto.qname),
    qname: dto.qname,
    symbolKind: dto.kind,
    language: dto.language,
    path: dto.path,
    occurrenceId: dto.occurrence_id,
    symbolId: dto.symbol_id,
    span: dto.span ? { start: dto.span.start, end: dto.span.end } : null,
    line: null,
    col: null,
    parseStatus: dto.parse_status,
    symbolCount: null,
    fileId: null,
    x: null,
    y: null,
    pinned: false,
    expanded: false,
  };
}

export function toFileNodeVM(item: ApiGraphOverviewItem): GraphNodeVM {
  return {
    id: fileNodeId(item.path),
    kind: 'file',
    label: basename(item.path),
    qname: item.path,
    symbolKind: 'file',
    language: item.language ?? '',
    path: item.path,
    occurrenceId: null,
    symbolId: null,
    span: null,
    line: null,
    col: null,
    parseStatus: '',
    symbolCount: item.symbols,
    fileId: item.file_id ?? null,
    x: null,
    y: null,
    pinned: false,
    expanded: false,
  };
}

/**
 * Search hits carry less data than graph nodes (no symbol_id/parse_status),
 * so the VM marks them incomplete — later neighbor DTOs upgrade the record.
 */
export function searchHitToVM(hit: ApiSearchHit): GraphNodeVM {
  return {
    id: occNodeId(hit.occurrence_id),
    kind: 'symbol',
    label: shortName(hit.qualified_name),
    qname: hit.qualified_name,
    symbolKind: hit.kind,
    language: hit.language,
    path: hit.path,
    occurrenceId: hit.occurrence_id,
    symbolId: null,
    span: hit.span ? { start: hit.span.start, end: hit.span.end } : null,
    line: null,
    col: null,
    parseStatus: '',
    symbolCount: null,
    fileId: null,
    x: null,
    y: null,
    pinned: false,
    expanded: false,
  };
}

/**
 * One row of GET /files/{id}/occurrences → symbol node. The endpoint is
 * file-scoped so `path` falls back to the drilled file when the item
 * doesn't carry its own.
 */
export function occurrenceToVM(item: ApiFileOccurrence, path: string): GraphNodeVM {
  return {
    id: occNodeId(item.id),
    kind: 'symbol',
    label: shortName(item.qualified_name),
    qname: item.qualified_name,
    symbolKind: item.kind,
    language: item.language ?? '',
    path: item.path ?? path,
    occurrenceId: item.id,
    symbolId: item.symbol_id ?? null,
    span:
      item.span_start !== null && item.span_end !== null
        ? { start: item.span_start, end: item.span_end }
        : null,
    line: item.line ?? null,
    col: item.col ?? null,
    parseStatus: item.parse_status ?? '',
    symbolCount: null,
    fileId: null,
    x: null,
    y: null,
    pinned: false,
    expanded: false,
  };
}

export function toEdgeVM(dto: ApiGraphEdge): GraphEdgeVM {
  return {
    id: edgeId(dto.id),
    from: occNodeId(dto.from_id),
    // NULL to_id means the target never resolved — the edge stays in the
    // store for inspection but cannot be drawn (no fabricated endpoint).
    to: dto.to_id === null ? '' : occNodeId(dto.to_id),
    toUnresolved: dto.to_unresolved ?? null,
    type: dto.type,
    resolutionStatus: dto.resolution_status,
    confidenceClass: dto.confidence_class ?? '',
    resolver: dto.resolver,
    evidenceIds: dto.evidence_ids ?? [],
  };
}

/* ------------------------------------------------------------------ */
/* Deterministic placement (golden-angle spiral around a seed)         */
/* ------------------------------------------------------------------ */

const GOLDEN_ANGLE = 2.399963229728653;
const SPIRAL_STEP = 30;

/**
 * Deterministic position for the i-th expansion node around `seed`.
 * Pure function — same (seed, index) always yields the same spot, so
 * re-renders and test runs are stable.
 */
export function spiralPosition(
  seed: { x: number; y: number },
  index: number,
): { x: number; y: number } {
  const r = SPIRAL_STEP * Math.sqrt(index + 1);
  const theta = index * GOLDEN_ANGLE;
  return { x: seed.x + r * Math.cos(theta), y: seed.y + r * Math.sin(theta) };
}

/**
 * True only for usable graph coordinates — NaN/Infinity/null are rejected
 * before they can poison sigma's spatial index (NaN positions corrupt the
 * whole render pass, not just the offending node).
 */
export function isFinitePosition(
  x: number | null | undefined,
  y: number | null | undefined,
): boolean {
  return Number.isFinite(x) && Number.isFinite(y);
}

/**
 * Bounded ForceAtlas2 iteration count: ~60k units of work per pass,
 * clamped to [80, 400] so the synchronous layout can never run unbounded
 * — tiny graphs still converge, huge graphs stay under the frame budget.
 */
export function layoutIterations(order: number): number {
  const n = Math.max(1, Math.floor(order) || 1);
  return Math.min(400, Math.max(80, Math.round(60000 / n)));
}

/**
 * Deterministic fallback position for nodes that arrive without a seed
 * (e.g. a fresh symbol-graph load). Hashes the node id onto a ring so the
 * initial layout always starts from the same configuration.
 */
export function hashPosition(id: string, index: number, total: number): { x: number; y: number } {
  let h = 0;
  for (let i = 0; i < id.length; i++) {
    h = (h * 31 + id.charCodeAt(i)) | 0;
  }
  const ring = Math.floor(index / Math.max(1, Math.ceil(Math.sqrt(total))));
  const angle = ((h >>> 0) % 6283) / 1000 + ring * GOLDEN_ANGLE;
  const radius = 60 + ring * 45;
  return { x: radius * Math.cos(angle), y: radius * Math.sin(angle) };
}

/* ------------------------------------------------------------------ */
/* Evidence states                                                     */
/* ------------------------------------------------------------------ */

const KNOWN_STATUSES = new Set<string>([
  'verified',
  'supported',
  'possible',
  'unresolved',
  'external',
]);

/**
 * Vocabulário real do backend (migrations: resolution_status CHECK):
 * resolved → aresta provada por evidência registrada;
 * ambiguous → resolução ambígua, incerteza honesta;
 * unresolved/external → mapeamento direto.
 * Nunca promove: ausência de status conhecido → 'unknown'.
 */
const STATUS_MAP: Record<string, ResolutionStatus | 'unknown'> = {
  resolved: 'verified',
  ambiguous: 'possible',
};

/** Maps a raw resolution_status to a badge level, never inventing one. */
export function toEvidenceLevel(status: string): ResolutionStatus | 'unknown' {
  if (KNOWN_STATUSES.has(status)) {
    return status as ResolutionStatus;
  }
  return STATUS_MAP[status] ?? 'unknown';
}

/** Marker glyph per evidence level — mirrors --atlas-evidence-*-marker. */
export const EVIDENCE_MARKERS: Record<string, string> = {
  verified: '✓',
  supported: '▲',
  possible: '◆',
  unresolved: '✕',
  external: '◇',
  unknown: '?',
};
