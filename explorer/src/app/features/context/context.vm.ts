import type {
  ApiContextPack,
  ApiPackEvidence,
} from '../../core/api/api.service';

/* ------------------------------------------------------------------------- */
/* View-model                                                                  */
/* ------------------------------------------------------------------------- */

export interface BudgetView {
  limit: number;
  reservedTotal: number;
  itemsEstimate: number;
  available: number;
  /** 0–100, used fraction of the declared limit. */
  usedPercent: number;
  /** Always true for this backend — counts are estimates, never exact. */
  estimated: boolean;
  estimator: string;
  overBudget: boolean;
}

export interface ContextItemView {
  itemId: string;
  kind: string;
  priority: number;
  title: string;
  estTokens: number;
  symbolKey: string;
  path: string;
  expandable: boolean;
  evidenceCount: number;
}

export interface PackView {
  repository: string;
  snapshotId: number;
  level: string;
  estimator: string;
  tokensEstimated: boolean;
  tokensPayload: number;
  selected: number;
  omitted: number;
  truncated: boolean;
  freshness: string;
  rankingExplanation: string;
  budget: BudgetView;
  items: readonly ContextItemView[];
  selectedSymbols: readonly string[];
  relevantFiles: readonly string[];
  dependencies: readonly ApiPackEvidence[];
  unresolvedRefs: readonly string[];
  expansionRefs: readonly { refId: string; kind: string; reason: string }[];
  diagnosticCodes: readonly string[];
  diagnosticMessages: readonly string[];
  insufficientBudget: boolean;
  mandatoryContextOmitted: boolean;
  suggestedMinimumBudget: number;
}

export function toBudgetView(pack: ApiContextPack): BudgetView {
  const budget = pack.budget;
  const reservedTotal =
    (budget?.reserved_meta ?? 0) +
    (budget?.reserved_evidence ?? 0) +
    (budget?.reserved_diagnostics ?? 0);
  const itemsEstimate = (pack.items ?? []).reduce(
    (sum, item) => sum + (item.est_tokens || 0),
    0,
  );
  const limit = Math.max(0, budget?.limit ?? 0);
  const used = reservedTotal + itemsEstimate;
  const usedPercent = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  return {
    limit,
    reservedTotal,
    itemsEstimate,
    available: budget?.available_items ?? 0,
    usedPercent,
    estimated: Boolean(pack.manifest?.tokens_estimated),
    estimator: budget?.estimator ?? '',
    overBudget: limit > 0 && used > limit,
  };
}

export function toPackView(pack: ApiContextPack): PackView {
  const manifest = pack.manifest;
  const diagnostics = pack.diagnostics;
  return {
    repository: manifest?.repository ?? '',
    snapshotId: manifest?.snapshot_id ?? 0,
    level: manifest?.level ?? '',
    estimator: manifest?.estimator ?? '',
    tokensEstimated: Boolean(manifest?.tokens_estimated),
    tokensPayload: manifest?.tokens_payload ?? 0,
    selected: manifest?.selected ?? 0,
    omitted: manifest?.omitted ?? 0,
    truncated: Boolean(manifest?.truncated),
    freshness: manifest?.freshness ?? '',
    rankingExplanation: manifest?.ranking_explanation ?? '',
    budget: toBudgetView(pack),
    items: (pack.items ?? []).map((item) => ({
      itemId: item.item_id,
      kind: item.kind,
      priority: item.priority,
      title: item.title,
      estTokens: item.est_tokens,
      symbolKey: item.symbol_key,
      path: item.path,
      expandable: item.expandable,
      evidenceCount: (item.evidence ?? []).length,
    })),
    selectedSymbols: pack.selected_symbols ?? [],
    relevantFiles: pack.relevant_files ?? [],
    dependencies: pack.dependencies ?? [],
    unresolvedRefs: pack.unresolved_refs ?? [],
    expansionRefs: (pack.expansion_refs ?? []).map((r) => ({
      refId: r.ref_id,
      kind: r.kind,
      reason: r.reason,
    })),
    diagnosticCodes: diagnostics?.codes ?? [],
    diagnosticMessages: diagnostics?.messages ?? [],
    insufficientBudget: Boolean(diagnostics?.insufficient_budget),
    mandatoryContextOmitted: Boolean(diagnostics?.mandatory_context_omitted),
    suggestedMinimumBudget: diagnostics?.suggested_minimum_budget ?? 0,
  };
}

/* ------------------------------------------------------------------------- */
/* Markdown rendering — deterministic, for copy/export only.                   */
/* ------------------------------------------------------------------------- */

export function packToMarkdown(pack: ApiContextPack): string {
  const view = toPackView(pack);
  const lines: string[] = [];
  lines.push('# Context Pack');
  lines.push('');
  lines.push(
    `- Repositório: ${view.repository || '(desconhecido)'} · snapshot #${view.snapshotId} · nível ${view.level}`,
  );
  lines.push(
    `- Orçamento: ${view.budget.limit} tokens (estimado; estimador ${view.budget.estimator})`,
  );
  lines.push(
    `- Selecionados: ${view.selected} · omitidos: ${view.omitted}${view.truncated ? ' · TRUNCADO' : ''}`,
  );
  if (view.rankingExplanation) {
    lines.push(`- Ranking: ${view.rankingExplanation}`);
  }
  lines.push('');

  if (view.selectedSymbols.length > 0) {
    lines.push('## Símbolos selecionados');
    for (const s of view.selectedSymbols) lines.push(`- \`${s}\``);
    lines.push('');
  }

  if (view.relevantFiles.length > 0) {
    lines.push('## Arquivos relevantes');
    for (const f of view.relevantFiles) lines.push(`- \`${f}\``);
    lines.push('');
  }

  if (view.items.length > 0) {
    lines.push('## Itens de contexto');
    for (const item of view.items) {
      lines.push(
        `- (${item.kind}, prioridade ${item.priority}, ~${item.estTokens} tok) ${item.title}`,
      );
    }
    lines.push('');
  }

  if (view.dependencies.length > 0) {
    lines.push('## Dependências');
    for (const dep of view.dependencies) {
      lines.push(`- \`${dep.ref}\` (${dep.kind})${dep.detail ? ` — ${dep.detail}` : ''}`);
    }
    lines.push('');
  }

  if (view.unresolvedRefs.length > 0) {
    lines.push('## Referências não resolvidas');
    for (const ref of view.unresolvedRefs) lines.push(`- \`${ref}\``);
    lines.push('');
  }

  if (view.expansionRefs.length > 0) {
    lines.push('## Pontos de expansão');
    for (const ref of view.expansionRefs) {
      lines.push(`- \`${ref.refId}\` (${ref.kind}) — ${ref.reason}`);
    }
    lines.push('');
  }

  if (view.diagnosticMessages.length > 0 || view.diagnosticCodes.length > 0) {
    lines.push('## Diagnósticos');
    for (const code of view.diagnosticCodes) lines.push(`- \`${code}\``);
    for (const message of view.diagnosticMessages) lines.push(`- ${message}`);
    lines.push('');
  }

  return lines.join('\n');
}
