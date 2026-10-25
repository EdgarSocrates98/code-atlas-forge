import type {
  ApiGraphEdge,
  ApiGraphNode,
  ApiGraphOverviewItem,
  ApiSearchHit,
} from '../../core/api/api.service';
import {
  basename,
  edgeId,
  fileNodeId,
  fileStem,
  hashPosition,
  occNodeId,
  parseOccurrenceId,
  searchHitToVM,
  shortName,
  spiralPosition,
  toEdgeVM,
  toEvidenceLevel,
  toFileNodeVM,
  toSymbolNodeVM,
} from './graph.utils';

describe('graph.utils — stable ids', () => {
  it('builds occ:{id} node keys', () => {
    expect(occNodeId(42)).toBe('occ:42');
    expect(fileNodeId('src/a.py')).toBe('file:src/a.py');
    expect(edgeId(7)).toBe('edge:7');
  });

  it('parses occurrence ids only from occ: keys', () => {
    expect(parseOccurrenceId('occ:12')).toBe(12);
    expect(parseOccurrenceId('file:x.py')).toBeNull();
    expect(parseOccurrenceId('occ:abc')).toBeNull();
    expect(parseOccurrenceId('occ:0')).toBeNull();
    expect(parseOccurrenceId('occ:-3')).toBeNull();
  });
});

describe('graph.utils — label helpers', () => {
  it('extracts basename/stem/short names', () => {
    expect(basename('a/b/c.py')).toBe('c.py');
    expect(basename('c.py')).toBe('c.py');
    expect(fileStem('a/b/c.py')).toBe('c');
    expect(fileStem('a/.env')).toBe('.env');
    expect(shortName('pkg.mod.Class.m')).toBe('m');
    expect(shortName('plain')).toBe('plain');
  });
});

describe('graph.utils — DTO mapping', () => {
  const nodeDto: ApiGraphNode = {
    id: 'occ:9',
    occurrence_id: 9,
    symbol_id: 33,
    qname: 'pkg.mod.fn',
    kind: 'function',
    language: 'python',
    path: 'pkg/mod.py',
    span: { start: 10, end: 20 },
    parse_status: 'ok',
  };

  it('maps a graph node DTO to a symbol VM with occ id', () => {
    const vm = toSymbolNodeVM(nodeDto);
    expect(vm.id).toBe('occ:9');
    expect(vm.kind).toBe('symbol');
    expect(vm.label).toBe('fn');
    expect(vm.qname).toBe('pkg.mod.fn');
    expect(vm.occurrenceId).toBe(9);
    expect(vm.symbolId).toBe(33);
    expect(vm.span).toEqual({ start: 10, end: 20 });
    expect(vm.pinned).toBeFalse();
    expect(vm.expanded).toBeFalse();
  });

  it('maps an overview item to a file VM', () => {
    const item: ApiGraphOverviewItem = { path: 'src/x.ts', symbols: 12, language: 'typescript' };
    const vm = toFileNodeVM(item);
    expect(vm.id).toBe('file:src/x.ts');
    expect(vm.kind).toBe('file');
    expect(vm.label).toBe('x.ts');
    expect(vm.symbolCount).toBe(12);
    expect(vm.occurrenceId).toBeNull();
  });

  it('maps an edge DTO preserving evidence optionality', () => {
    const dto: ApiGraphEdge = {
      id: 5,
      from_id: 1,
      to_id: 2,
      type: 'calls',
      resolution_status: 'verified',
      resolver: 'scip',
    };
    const vm = toEdgeVM(dto);
    expect(vm.id).toBe('edge:5');
    expect(vm.from).toBe('occ:1');
    expect(vm.to).toBe('occ:2');
    expect(vm.evidenceIds).toEqual([]);

    const withEv = toEdgeVM({ ...dto, evidence_ids: [3, 4] });
    expect(withEv.evidenceIds).toEqual([3, 4]);
  });

  it('maps search hits into incomplete symbol VMs', () => {
    const hit: ApiSearchHit = {
      occurrence_id: 77,
      qualified_name: 'mod.fn',
      kind: 'function',
      language: 'python',
      path: 'mod.py',
      span: null,
      score: 1,
      strategy: 'structural',
      matched_fields: ['qualified_name'],
      evidence: ['occ:77'],
    };
    const vm = searchHitToVM(hit);
    expect(vm.id).toBe('occ:77');
    expect(vm.symbolId).toBeNull();
    expect(vm.parseStatus).toBe('');
    expect(vm.span).toBeNull();
  });
});

describe('graph.utils — deterministic placement', () => {
  it('spiral positions are deterministic and distinct', () => {
    const seed = { x: 100, y: -50 };
    const a1 = spiralPosition(seed, 0);
    const a2 = spiralPosition(seed, 0);
    const b = spiralPosition(seed, 1);
    expect(a1).toEqual(a2);
    expect(a1).not.toEqual(b);
    expect(Number.isFinite(a1.x) && Number.isFinite(a1.y)).toBeTrue();
  });

  it('hash positions are deterministic for the same id', () => {
    expect(hashPosition('occ:1', 0, 10)).toEqual(hashPosition('occ:1', 0, 10));
    expect(hashPosition('occ:1', 0, 10)).not.toEqual(hashPosition('occ:2', 0, 10));
  });
});

describe('graph.utils — evidence levels', () => {
  it('passes known statuses through, maps unknown safely', () => {
    expect(toEvidenceLevel('verified')).toBe('verified');
    expect(toEvidenceLevel('external')).toBe('external');
    expect(toEvidenceLevel('whatever')).toBe('unknown');
    expect(toEvidenceLevel('')).toBe('unknown');
  });
});
