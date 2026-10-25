import type {
  ApiDomainEdge,
  ApiDomainEntity,
} from '../../../core/api/api.service';

/** A terraform module and the entities it provisions (PROVISIONS edges). */
export interface InfraModuleView {
  module: ApiDomainEntity | null;
  /** display title — module qualified_name or the grouping namespace. */
  title: string;
  resources: ApiDomainEntity[];
}

export interface InfraView {
  /** Declared terraform_module entities with their provisioned members. */
  modules: InfraModuleView[];
  /** Resources/roles reached by PROVISIONS but grouping by namespace. */
  namespaceGroups: InfraModuleView[];
  iamRoles: ApiDomainEntity[];
  /** PROVISIONS/USES_RESOURCE/ASSUMES_ROLE edges, as returned. */
  edges: ApiDomainEdge[];
  edgesTruncated: boolean;
}

const GROUPED_KINDS = new Set(['terraform_resource', 'iam_role']);

/**
 * Groups terraform entities for the infra view: resources+roles under
 * their declaring module (PROVISIONS edge when present, resource
 * `namespace` otherwise), roles listed separately, and the raw edge list
 * kept verbatim for the evidence table.
 */
export function buildInfraView(
  entities: readonly ApiDomainEntity[],
  edges: readonly ApiDomainEdge[],
  edgesTruncated: boolean,
): InfraView {
  const byId = new Map(entities.map((e) => [e.id, e]));
  const modules = entities
    .filter((e) => e.kind === 'terraform_module')
    .sort(byQName);
  const resources = entities.filter((e) => GROUPED_KINDS.has(e.kind));
  const iamRoles = entities
    .filter((e) => e.kind === 'iam_role')
    .sort(byQName);

  const provisions = new Map<number, ApiDomainEntity[]>();
  const provisioned = new Set<number>();
  for (const edge of edges) {
    if (edge.type !== 'PROVISIONS') continue;
    const modId = edge.from.entity_id;
    const resId = edge.to.entity_id;
    if (modId == null || resId == null) continue;
    const res = byId.get(resId);
    if (!res || !GROUPED_KINDS.has(res.kind)) continue;
    const list = provisions.get(modId) ?? [];
    list.push(res);
    provisions.set(modId, list);
    provisioned.add(resId);
  }

  const moduleViews: InfraModuleView[] = modules.map((module) => ({
    module,
    title: module.qualified_name,
    resources: (provisions.get(module.id) ?? []).sort(byQName),
  }));

  // namespaces dos recursos sem aresta PROVISIONS — agrupados pelo
  // namespace observado, nunca por módulo inventado.
  const nsGroups = new Map<string, ApiDomainEntity[]>();
  for (const res of resources) {
    if (provisioned.has(res.id)) continue;
    const key = res.namespace || '';
    const list = nsGroups.get(key) ?? [];
    list.push(res);
    nsGroups.set(key, list);
  }
  const namespaceGroups: InfraModuleView[] = [...nsGroups.entries()]
    .map(([ns, list]) => ({
      module: null,
      title: ns || '(raiz — sem módulo declarado)',
      resources: list.sort(byQName),
    }))
    .sort((a, b) => a.title.localeCompare(b.title));

  return {
    modules: moduleViews,
    namespaceGroups,
    iamRoles,
    edges: [...edges],
    edgesTruncated,
  };
}

function byQName(a: ApiDomainEntity, b: ApiDomainEntity): number {
  return a.qualified_name.localeCompare(b.qualified_name) || a.id - b.id;
}
