import type {
  ApiContractChange,
  ApiEvidenceStrength,
  ApiImpactCandidate,
  ApiImpactDiagnostic,
  ApiImpactResult,
  ApiSymbolChange,
  ApiTestCandidate,
} from '../../core/api/api.service';

/* ------------------------------------------------------------------------- */
/* Partial-analysis detection                                                  */
/* ------------------------------------------------------------------------- */

/**
 * Diagnostics emitted by the snapshot resolver / revision binding / diff
 * reader (see src/code_atlas/impact/*). When any of these are present the
 * result cannot be presented as fully verified — the UI must surface a
 * PARTIAL banner instead.
 */
export const BINDING_DIAGNOSTIC_CODES: ReadonlySet<string> = new Set([
  'repo-not-indexed',
  'invalid-revision',
  'git-error',
  'revision-unresolved',
  'revision-binding-mismatch',
  'dirty-worktree',
  'snapshot-for-revision-missing',
  'content-unverifiable',
  'content-mismatch',
  'snapshot-missing',
  'cross-repo-snapshot',
  'snapshot-failed',
  'snapshot-unavailable',
  'partial-analysis',
  'semantic-blocked',
]);

/** Diagnostics that mean semantic analysis did not run at all. */
export const BLOCKING_DIAGNOSTIC_CODES: ReadonlySet<string> = new Set([
  'semantic-blocked',
  'snapshot-unavailable',
  'repo-not-indexed',
]);

/**
 * True when the result carries binding/partial diagnostics. A partial
 * result must never be styled as verified.
 */
export function isPartialAnalysis(
  diagnostics: readonly ApiImpactDiagnostic[],
): boolean {
  return diagnostics.some((d) => BINDING_DIAGNOSTIC_CODES.has(d.code));
}

/** True when the backend reports that semantic analysis was blocked. */
export function isSemanticBlocked(result: ApiImpactResult): boolean {
  return (
    result.unknown_coverage.includes('semantic-blocked') ||
    result.unknown_coverage.includes('no-snapshot') ||
    result.diagnostics.some((d) => BLOCKING_DIAGNOSTIC_CODES.has(d.code))
  );
}

/* ------------------------------------------------------------------------- */
/* pt-BR labels                                                                */
/* ------------------------------------------------------------------------- */

export const CHANGE_KIND_LABELS: Record<string, string> = {
  added: 'Adicionados',
  removed: 'Removidos',
  renamed: 'Renomeados',
  moved: 'Movidos',
  signature_changed: 'Assinatura alterada',
  body_changed: 'Corpo alterado',
  doc_changed: 'Documentação alterada',
  no_semantic_change: 'Sem mudança semântica',
  unknown: 'Indeterminado',
};

const CHANGE_KIND_ORDER = Object.keys(CHANGE_KIND_LABELS);

export function changeKindLabel(kind: string): string {
  return CHANGE_KIND_LABELS[kind] ?? `Outros (${kind})`;
}

export const IMPACT_KIND_LABELS: Record<string, string> = {
  direct: 'Direto',
  transitive: 'Transitivo',
  possible: 'Possível',
  unknown: 'Indeterminado',
};

export const CONSUMER_STATE_LABELS: Record<string, string> = {
  still_present: 'presente no alvo',
  also_removed: 'também removido',
  consumer_modified: 'modificado no alvo',
  unresolved_in_target: 'não resolvido no alvo',
};

export const COMPATIBILITY_LABELS: Record<string, string> = {
  'potential-risk': 'risco potencial',
  'likely-compatible': 'provavelmente compatível',
  'no-contract-impact': 'sem impacto de contrato',
  indeterminate: 'indeterminado',
};

export const RELATIONSHIP_LABELS: Record<string, string> = {
  direct: 'direta',
  transitive: 'transitiva',
  heuristic: 'heurística',
  unknown: 'desconhecida',
};

export const RISK_CATEGORY_LABELS: Record<string, string> = {
  low: 'Baixo',
  moderate: 'Moderado',
  high: 'Alto',
  critical: 'Crítico',
  unknown: 'Indeterminado',
};

export const UNCERTAINTY_LABELS: Record<string, string> = {
  low: 'Baixa',
  moderate: 'Moderada',
  high: 'Alta',
};

export function revisionTagLabel(revision: string): string {
  if (revision === 'base') return 'revisão base';
  if (revision === 'target') return 'revisão alvo';
  return 'sem vínculo de revisão';
}

/* ------------------------------------------------------------------------- */
/* View-model                                                                  */
/* ------------------------------------------------------------------------- */

export interface SymbolChangeView {
  symbolKey: string;
  qualifiedName: string;
  language: string;
  kind: string;
  change: string;
  changeLabel: string;
  strength: ApiEvidenceStrength;
  oldSignature: string | null;
  newSignature: string | null;
  oldPath: string | null;
  newPath: string | null;
  detail: string;
}

export interface SymbolChangeGroup {
  kind: string;
  label: string;
  items: SymbolChangeView[];
}

export interface BlastItemView {
  symbolKey: string;
  qualifiedName: string;
  language: string;
  kind: string;
  impactKind: string;
  impactLabel: string;
  strength: ApiEvidenceStrength;
  hops: number | null;
  fullyVerified: boolean;
  reason: string;
  viaCount: number;
  consumerState: string;
  consumerLabel: string | null;
  sourcePath: string | null;
  occurrenceId: number | null;
}

export interface BlastGroup {
  /** -1 = sem cadeia conhecida (path nulo). */
  hops: number;
  label: string;
  items: BlastItemView[];
}

export interface TestCandidateView {
  testFile: string;
  testSymbol: string | null;
  relationship: string;
  relationshipLabel: string;
  reason: string;
  strength: ApiEvidenceStrength;
  revision: string;
  revisionLabel: string;
}

export interface ContractChangeView {
  symbolKey: string;
  classification: string;
  compatibility: string;
  compatibilityLabel: string;
  oldSignature: string | null;
  newSignature: string | null;
  strength: ApiEvidenceStrength;
  detail: string;
}

export interface RiskView {
  category: string;
  categoryLabel: string;
  uncertainty: string;
  uncertaintyLabel: string;
  reasons: readonly string[];
  formulaVersion: string;
}

export interface ChangeSetView {
  fileCount: number;
  source: string;
  dirtyWorktree: boolean;
  commitIds: readonly string[];
}

export interface ImpactView {
  /** Never style this result as verified when true. */
  partial: boolean;
  semanticBlocked: boolean;
  truncated: boolean;
  repository: string;
  baseRevision: string;
  targetRevision: string;
  baseSnapshotId: number | null;
  targetSnapshotId: number | null;
  /** All diagnostics, in original order. */
  diagnosticsTotal: readonly ApiImpactDiagnostic[];
  bindingDiagnostics: readonly ApiImpactDiagnostic[];
  otherDiagnostics: readonly ApiImpactDiagnostic[];
  changeSet: ChangeSetView | null;
  symbolGroups: readonly SymbolChangeGroup[];
  contractChanges: readonly ContractChangeView[];
  blastGroups: readonly BlastGroup[];
  testCandidates: readonly TestCandidateView[];
  risk: RiskView;
  unknownCoverage: readonly string[];
  totalChanges: number;
  totalImpacts: number;
  totalTests: number;
}

export function toSymbolChangeView(change: ApiSymbolChange): SymbolChangeView {
  return {
    symbolKey: change.symbol_key,
    qualifiedName: change.qualified_name,
    language: change.language,
    kind: change.kind,
    change: change.change,
    changeLabel: changeKindLabel(change.change),
    strength: change.strength,
    oldSignature: change.old_signature,
    newSignature: change.new_signature,
    oldPath: change.old_path,
    newPath: change.new_path,
    detail: change.detail,
  };
}

export function groupSymbolChanges(
  changes: readonly ApiSymbolChange[],
): SymbolChangeGroup[] {
  const groups = new Map<string, SymbolChangeView[]>();
  for (const c of changes) {
    const list = groups.get(c.change) ?? [];
    list.push(toSymbolChangeView(c));
    groups.set(c.change, list);
  }
  const order = (kind: string): number => {
    const i = CHANGE_KIND_ORDER.indexOf(kind);
    return i === -1 ? CHANGE_KIND_ORDER.length : i;
  };
  return [...groups.entries()]
    .sort(([a], [b]) => order(a) - order(b))
    .map(([kind, items]) => ({ kind, label: changeKindLabel(kind), items }));
}

export function toBlastItemView(candidate: ApiImpactCandidate): BlastItemView {
  const hops = candidate.path ? candidate.path.hops : null;
  const consumerLabel = candidate.consumer_state
    ? (CONSUMER_STATE_LABELS[candidate.consumer_state] ?? candidate.consumer_state)
    : null;
  return {
    symbolKey: candidate.symbol_key,
    qualifiedName: candidate.qualified_name,
    language: candidate.language,
    kind: candidate.kind,
    impactKind: candidate.impact,
    impactLabel: IMPACT_KIND_LABELS[candidate.impact] ?? candidate.impact,
    strength: candidate.strength,
    hops,
    fullyVerified: candidate.path?.fully_verified ?? false,
    reason: candidate.path?.reason ?? '',
    viaCount: candidate.path?.intermediate_keys.length ?? 0,
    consumerState: candidate.consumer_state,
    consumerLabel,
    sourcePath: candidate.source_path,
    occurrenceId: candidate.occurrence_id,
  };
}

export function groupBlastCandidates(
  impacts: readonly ApiImpactCandidate[],
): BlastGroup[] {
  const groups = new Map<number, BlastItemView[]>();
  for (const impact of impacts) {
    const hops = impact.path ? impact.path.hops : -1;
    const list = groups.get(hops) ?? [];
    list.push(toBlastItemView(impact));
    groups.set(hops, list);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => (a === -1 ? 1 : b === -1 ? -1 : a - b))
    .map(([hops, items]) => ({
      hops,
      label:
        hops === -1
          ? 'Sem cadeia conhecida'
          : hops === 1
            ? '1 salto da origem'
            : `${hops} saltos da origem`,
      items,
    }));
}

export function toTestCandidateView(candidate: ApiTestCandidate): TestCandidateView {
  return {
    testFile: candidate.test_file,
    testSymbol: candidate.test_symbol,
    relationship: candidate.relationship,
    relationshipLabel:
      RELATIONSHIP_LABELS[candidate.relationship] ?? candidate.relationship,
    reason: candidate.reason,
    strength: candidate.strength,
    revision: candidate.revision,
    revisionLabel: revisionTagLabel(candidate.revision),
  };
}

export function toContractChangeView(change: ApiContractChange): ContractChangeView {
  return {
    symbolKey: change.symbol_key,
    classification: change.classification,
    compatibility: change.compatibility,
    compatibilityLabel:
      COMPATIBILITY_LABELS[change.compatibility] ?? change.compatibility,
    oldSignature: change.old_signature,
    newSignature: change.new_signature,
    strength: change.strength,
    detail: change.detail,
  };
}

export function toImpactView(result: ApiImpactResult): ImpactView {
  const diagnostics = result.diagnostics ?? [];
  const bindingDiagnostics = diagnostics.filter((d) =>
    BINDING_DIAGNOSTIC_CODES.has(d.code),
  );
  const otherDiagnostics = diagnostics.filter(
    (d) => !BINDING_DIAGNOSTIC_CODES.has(d.code),
  );
  const risk = result.risk;
  return {
    partial: isPartialAnalysis(diagnostics),
    semanticBlocked: isSemanticBlocked(result),
    truncated: Boolean(result.truncated),
    repository: result.repository,
    baseRevision: result.base_revision,
    targetRevision: result.target_revision,
    baseSnapshotId: result.base_snapshot_id,
    targetSnapshotId: result.target_snapshot_id,
    diagnosticsTotal: diagnostics,
    bindingDiagnostics,
    otherDiagnostics,
    changeSet: result.change_set
      ? {
          fileCount: result.change_set.files.length,
          source: result.change_set.source,
          dirtyWorktree: result.change_set.dirty_worktree,
          commitIds: result.change_set.commit_ids,
        }
      : null,
    symbolGroups: groupSymbolChanges(result.symbol_changes ?? []),
    contractChanges: (result.contract_changes ?? []).map(toContractChangeView),
    blastGroups: groupBlastCandidates(result.impacts ?? []),
    testCandidates: (result.test_candidates ?? []).map(toTestCandidateView),
    risk: {
      category: risk?.category ?? 'unknown',
      categoryLabel: RISK_CATEGORY_LABELS[risk?.category] ?? 'Indeterminado',
      uncertainty: risk?.uncertainty ?? 'high',
      uncertaintyLabel: UNCERTAINTY_LABELS[risk?.uncertainty] ?? 'Alta',
      reasons: risk?.reasons ?? [],
      formulaVersion: risk?.formula_version ?? '',
    },
    unknownCoverage: result.unknown_coverage ?? [],
    totalChanges: (result.symbol_changes ?? []).length,
    totalImpacts: (result.impacts ?? []).length,
    totalTests: (result.test_candidates ?? []).length,
  };
}
