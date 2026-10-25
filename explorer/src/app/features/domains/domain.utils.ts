import type {
  ApiDomainEdge,
  ApiDomainEndpoint,
  ApiDomainEntity,
  ApiDomainLineageNode,
} from '../../core/api/api.service';
import type { EvidenceLevel } from '../../shared/components/evidence-badge/evidence-badge.component';

/* ---------------------------------------------------------------------------
 * Shared domain-view vocabulary (lf-0122, Cycle 7 Phase 14).
 *
 * pt-BR labels for the closed domain vocabulary — unknown domains/kinds
 * degrade to the raw identifier, never to an invented label.
 * ------------------------------------------------------------------------- */

export const DOMAIN_LABELS: Readonly<Record<string, string>> = {
  sql: 'SQL / dados',
  'aws-data': 'AWS Data',
  pyspark: 'PySpark',
  api: 'Contratos de API',
  terraform: 'Infraestrutura (Terraform)',
  cicd: 'CI/CD',
};

export const KIND_LABELS: Readonly<Record<string, string>> = {
  data_table: 'tabela',
  data_column: 'coluna',
  data_view: 'view',
  data_transformation: 'transformação',
  data_pipeline: 'pipeline de dados',
  spark_job: 'job Spark',
  glue_job: 'job Glue',
  data_catalog: 'catálogo de dados',
  iceberg_table: 'tabela Iceberg',
  api_endpoint: 'endpoint',
  api_service: 'serviço',
  api_contract: 'contrato',
  terraform_resource: 'recurso',
  terraform_module: 'módulo',
  iam_role: 'role IAM',
  cicd_workflow: 'workflow',
  cicd_job: 'job',
  repository: 'repositório',
  project: 'projeto',
};

/** Entity kinds eligible as lineage roots (data-plane, not code). */
export const LINEAGE_ENTITY_KINDS =
  'data_table,data_view,iceberg_table,data_column,data_transformation';

export function domainLabel(domain: string): string {
  return DOMAIN_LABELS[domain] ?? domain;
}

export function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind;
}

/**
 * Maps a domain edge to an evidence-strength badge level.
 * resolution_status is authoritative; confidence_class only refines a
 * `resolved` edge (heuristic → supported, inferred → possible — the
 * backend never emits inferred+resolved, but the map stays honest).
 */
export function edgeEvidenceLevel(
  edge: Pick<ApiDomainEdge, 'resolution_status' | 'confidence_class'>,
): EvidenceLevel {
  switch (edge.resolution_status) {
    case 'unresolved':
      return 'unresolved';
    case 'external':
      return 'external';
    case 'ambiguous':
      return 'possible';
    case 'resolved':
      if (edge.confidence_class === 'inferred') return 'possible';
      if (edge.confidence_class === 'heuristic') return 'supported';
      return 'verified';
    default:
      return 'unknown';
  }
}

/** Short human label for an endpoint — unresolved shows its raw text. */
export function endpointLabel(ep: ApiDomainEndpoint): string {
  if (ep.endpoint === 'unresolved') {
    return ep.label ?? '';
  }
  if (ep.qualified_name) {
    return ep.qualified_name;
  }
  if (ep.name) {
    return ep.name;
  }
  return ep.ref;
}

/** Detail string under the endpoint label — path, kind, domain. */
export function endpointDetail(ep: ApiDomainEndpoint): string {
  if (ep.endpoint === 'unresolved') {
    return 'alvo não resolvido';
  }
  const parts: string[] = [];
  if (ep.endpoint === 'entity' && ep.entity_kind) {
    parts.push(kindLabel(ep.entity_kind));
  } else if (ep.endpoint === 'symbol' && ep.kind) {
    parts.push(`símbolo ${ep.kind}`);
  }
  if (ep.domain) parts.push(domainLabel(ep.domain));
  if (ep.path) parts.push(ep.path);
  if (ep.stale) parts.push('fora do snapshot');
  return parts.join(' · ');
}

/** Display name of a domain entity (qualified name preferred). */
export function entityLabel(entity: ApiDomainEntity): string {
  return entity.qualified_name || entity.name || `#${entity.id}`;
}

/** pt-BR verb phrase for an edge type. */
export function relationLabel(type: string): string {
  const labels: Record<string, string> = {
    READS_FROM: 'lê de',
    WRITES_TO: 'escreve em',
    TRANSFORMS: 'transforma',
    DERIVES_FROM: 'deriva de',
    DEPENDS_ON: 'depende de',
    CALLS_ENDPOINT: 'chama endpoint',
    IMPLEMENTS_CONTRACT: 'implementa contrato',
    PROVISIONS: 'provisiona',
    USES_RESOURCE: 'usa recurso',
    ASSUMES_ROLE: 'assume role',
    TRIGGERS: 'dispara',
    DEPLOYS: 'implanta em',
    CONTAINS: 'contém',
    DEFINES: 'define',
    TESTS: 'testa',
    REFERENCES: 'referencia',
    IMPORTS: 'importa',
    CALLS: 'chama',
  };
  return labels[type] ?? type.toLowerCase();
}

/** Sorts lineage nodes per lane: distance asc, then label. */
export function byLaneAndDistance(
  a: ApiDomainLineageNode,
  b: ApiDomainLineageNode,
): number {
  return (
    a.distance - b.distance ||
    endpointLabel(a).localeCompare(endpointLabel(b))
  );
}
