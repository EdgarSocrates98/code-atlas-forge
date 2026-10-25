import type {
  ApiFileOccurrence,
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
  isFinitePosition,
  layoutIterations,
  occNodeId,
  occurrenceToVM,
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
    expect(vm.fileId).toBeNull(); // field absent on the wire → null, never invented
  });

  it('carries file_id when the overview reports it', () => {
    const vm = toFileNodeVM({ path: 'a.py', symbols: 1, language: null, file_id: 42 });
    expect(vm.fileId).toBe(42);
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
    expect(vm.confidenceClass).toBe(''); // absent on the wire stays empty
    expect(vm.toUnresolved).toBeNull();
    expect(vm.evidenceIds).toEqual([]);

    const withEv = toEdgeVM({ ...dto, evidence_ids: [3, 4], confidence_class: 'syntactic' });
    expect(withEv.evidenceIds).toEqual([3, 4]);
    expect(withEv.confidenceClass).toBe('syntactic');
  });

  it('maps a NULL to_id to an empty target plus the unresolved name', () => {
    const vm = toEdgeVM({
      id: 8,
      from_id: 1,
      to_id: null,
      to_unresolved: 'pkg.missing.Symbol',
      type: 'references',
      resolution_status: 'unresolved',
      resolver: 'syntax',
    });
    expect(vm.to).toBe('');
    expect(vm.toUnresolved).toBe('pkg.missing.Symbol');
  });

  it('maps a file-occurrence row into a symbol VM (occurrences endpoint)', () => {
    const item: ApiFileOccurrence = {
      id: 'occ:21',
      occurrence_id: 21,
      qualified_name: 'mod.sub.fn',
      kind: 'function',
      span_start: 120,
      span_end: 140,
      line: 7,
      col: 4,
      language: 'python',
      symbol_id: 99,
      parse_status: 'ok',
    };
    const vm = occurrenceToVM(item, 'mod/sub.py');
    expect(vm.id).toBe('occ:21');
    expect(vm.kind).toBe('symbol');
    expect(vm.label).toBe('fn');
    expect(vm.qname).toBe('mod.sub.fn');
    expect(vm.path).toBe('mod/sub.py'); // falls back to the drilled file
    expect(vm.occurrenceId).toBe(21);
    expect(vm.symbolId).toBe(99);
    expect(vm.span).toEqual({ start: 120, end: 140 });
    expect(vm.line).toBe(7);
    expect(vm.col).toBe(4);
    expect(vm.pinned).toBeFalse();
    expect(vm.expanded).toBeFalse();
  });

  it('maps occurrences with null spans/optional fields honestly', () => {
    const vm = occurrenceToVM(
      { id: 'occ:3', occurrence_id: 3, qualified_name: 'top', kind: 'module', span_start: null, span_end: null, line: null, col: null },
      'top.py',
    );
    expect(vm.span).toBeNull();
    expect(vm.line).toBeNull();
    expect(vm.language).toBe('');
    expect(vm.symbolId).toBeNull();
    expect(vm.parseStatus).toBe('');
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

describe('graph.utils — geometry sanitization & layout bounds', () => {
  it('isFinitePosition accepts finite pairs and rejects everything else', () => {
    expect(isFinitePosition(0, -12.5)).toBeTrue();
    expect(isFinitePosition(NaN, 0)).toBeFalse();
    expect(isFinitePosition(0, Infinity)).toBeFalse();
    expect(isFinitePosition(-Infinity, 0)).toBeFalse();
    expect(isFinitePosition(null, 0)).toBeFalse();
    expect(isFinitePosition(0, undefined)).toBeFalse();
  });

  it('hashPosition always yields finite coordinates (sanitize fallback)', () => {
    for (let i = 0; i < 50; i++) {
      const p = hashPosition(`occ:${i}`, i, 50);
      expect(isFinitePosition(p.x, p.y)).toBeTrue();
    }
  });

  it('layoutIterations stays inside [80, 400] for any order', () => {
    expect(layoutIterations(0)).toBe(400); // degenerate → clamped
    expect(layoutIterations(1)).toBe(400);
    expect(layoutIterations(100)).toBe(400);
    expect(layoutIterations(750)).toBe(80);
    expect(layoutIterations(1000)).toBe(80);
    expect(layoutIterations(25000)).toBe(80);
    for (const order of [1, 5, 42, 200, 751, 5000, 100000]) {
      const n = layoutIterations(order);
      expect(n).toBeGreaterThanOrEqual(80);
      expect(n).toBeLessThanOrEqual(400);
    }
  });
});

describe('graph.utils — evidence levels', () => {
  it('passes known statuses through, maps unknown safely', () => {
    expect(toEvidenceLevel('verified')).toBe('verified');
    expect(toEvidenceLevel('external')).toBe('external');
    expect(toEvidenceLevel('whatever')).toBe('unknown');
    expect(toEvidenceLevel('')).toBe('unknown');
    expect(toEvidenceLevel('resolved')).toBe('verified');
    expect(toEvidenceLevel('ambiguous')).toBe('possible');
    expect(toEvidenceLevel('unresolved')).toBe('unresolved');
  });
});
