import type { ApiDomainEntity } from '../../../core/api/api.service';

/** Service + the endpoints it contains, plus the contract that defines it. */
export interface ContractServiceView {
  service: ApiDomainEntity;
  endpoints: ApiDomainEntity[];
}

export interface ContractsView {
  /** api_contract entities — provenance of the spec files. */
  contracts: ApiDomainEntity[];
  services: ContractServiceView[];
  /**
   * Endpoints whose `namespace` matches no service qualified_name —
   * surfaced honestly instead of being dropped.
   */
  unboundEndpoints: ApiDomainEntity[];
}

/**
 * Groups api_endpoint entities under their api_service via the shared
 * `namespace == service.qualified_name` convention used by every API
 * adapter (OpenAPI specs and framework route extractors alike).
 */
export function buildContractsView(
  entities: readonly ApiDomainEntity[],
): ContractsView {
  const contracts = entities
    .filter((e) => e.kind === 'api_contract')
    .sort(byName);
  const services = entities
    .filter((e) => e.kind === 'api_service')
    .sort(byName);
  const endpoints = entities
    .filter((e) => e.kind === 'api_endpoint')
    .sort(byName);
  const byQName = new Map(services.map((s) => [s.qualified_name, s]));
  const grouped = new Map<number, ApiDomainEntity[]>(
    services.map((s) => [s.id, []]),
  );
  const unboundEndpoints: ApiDomainEntity[] = [];
  for (const ep of endpoints) {
    const svc = byQName.get(ep.namespace);
    if (svc) {
      grouped.get(svc.id)!.push(ep);
    } else {
      unboundEndpoints.push(ep);
    }
  }
  return {
    contracts,
    services: services.map((service) => ({
      service,
      endpoints: grouped.get(service.id) ?? [],
    })),
    unboundEndpoints,
  };
}

function byName(a: ApiDomainEntity, b: ApiDomainEntity): number {
  return (
    a.qualified_name.localeCompare(b.qualified_name) ||
    a.name.localeCompare(b.name) ||
    a.id - b.id
  );
}
