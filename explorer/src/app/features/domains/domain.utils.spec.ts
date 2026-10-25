import type {
  ApiDomainEdge,
  ApiDomainEndpoint,
  ApiDomainEntity,
} from '../../core/api/api.service';
import {
  domainLabel,
  edgeEvidenceLevel,
  endpointDetail,
  endpointLabel,
  entityLabel,
  kindLabel,
  relationLabel,
} from './domain.utils';

function edge(
  resolution: string,
  confidence: string,
): Pick<ApiDomainEdge, 'resolution_status' | 'confidence_class'> {
  return { resolution_status: resolution, confidence_class: confidence };
}

function endpoint(overrides: Partial<ApiDomainEndpoint>): ApiDomainEndpoint {
  return { ref: 'de:1', endpoint: 'entity', ...overrides };
}

describe('domain.utils', () => {
  it('maps resolution_status to evidence levels honestly', () => {
    expect(edgeEvidenceLevel(edge('resolved', 'resolved'))).toBe('verified');
    expect(edgeEvidenceLevel(edge('resolved', 'syntactic'))).toBe('verified');
    expect(edgeEvidenceLevel(edge('resolved', 'heuristic'))).toBe('supported');
    expect(edgeEvidenceLevel(edge('ambiguous', 'resolved'))).toBe('possible');
    expect(edgeEvidenceLevel(edge('unresolved', 'resolved'))).toBe('unresolved');
    expect(edgeEvidenceLevel(edge('external', 'resolved'))).toBe('external');
    expect(edgeEvidenceLevel(edge('nonsense', 'x'))).toBe('unknown');
  });

  it('never reports inferred confidence as verified', () => {
    // the backend must not emit inferred+resolved; if it ever does, the
    // badge still degrades to 'possible' instead of claiming verification.
    expect(edgeEvidenceLevel(edge('resolved', 'inferred'))).toBe('possible');
  });

  it('labels unresolved endpoints by their raw label', () => {
    const ep = endpoint({
      endpoint: 'unresolved',
      label: 's3://bucket/path',
    });
    expect(endpointLabel(ep)).toBe('s3://bucket/path');
    expect(endpointDetail(ep)).toContain('não resolvido');
  });

  it('prefers qualified_name for resolved endpoints', () => {
    const ep = endpoint({
      qualified_name: 'analytics.orders',
      name: 'orders',
      entity_kind: 'data_table',
      domain: 'sql',
    });
    expect(endpointLabel(ep)).toBe('analytics.orders');
    expect(endpointDetail(ep)).toContain('tabela');
  });

  it('degrades unknown domains/kinds to the raw identifier', () => {
    expect(domainLabel('custom-domain')).toBe('custom-domain');
    expect(kindLabel('mystery_kind')).toBe('mystery_kind');
    expect(relationLabel('WEIRD_REL')).toBe('weird_rel');
  });

  it('entityLabel falls back to name then id', () => {
    const base: ApiDomainEntity = {
      id: 7,
      domain: 'sql',
      kind: 'data_table',
      qualified_name: '',
      name: 't',
      catalog: '',
      namespace: '',
      environment: '',
      attributes: {},
      occurrences: 1,
      first_occurrence_id: 1,
      path: null,
      line: null,
      extraction_status: 'ready',
    };
    expect(entityLabel(base)).toBe('t');
    expect(entityLabel({ ...base, name: '' })).toBe('#7');
    expect(entityLabel({ ...base, qualified_name: 'db.t' })).toBe('db.t');
  });
});
