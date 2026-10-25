import type {
  ApiDomainEdge,
  ApiDomainLineage,
  ApiDomainLineageNode,
} from '../../../core/api/api.service';
import { byLaneAndDistance } from '../domain.utils';

/** Lane buckets for the lineage view — never re-ordered heuristically. */
export interface LineageView {
  root: ApiDomainLineageNode;
  /** nodes feeding the root, closest first (distance asc). */
  upstream: ApiDomainLineageNode[];
  /** nodes fed by the root, closest first. */
  downstream: ApiDomainLineageNode[];
  edges: ApiDomainEdge[];
  truncated: boolean;
}

/**
 * Splits the backend lineage response into upstream/downstream lanes.
 * Node order inside each lane is deterministic (distance, then label);
 * edges arrive already sorted by relation id from the backend.
 */
export function toLineageView(res: ApiDomainLineage): LineageView {
  const upstream: ApiDomainLineageNode[] = [];
  const downstream: ApiDomainLineageNode[] = [];
  for (const node of res.nodes ?? []) {
    if (node.lane === 'upstream') upstream.push(node);
    else if (node.lane === 'downstream') downstream.push(node);
  }
  upstream.sort(byLaneAndDistance);
  downstream.sort(byLaneAndDistance);
  return {
    root: res.root,
    upstream,
    downstream,
    edges: res.edges ?? [],
    truncated: res.truncated,
  };
}

/** True when the edge touches the given node ref at either endpoint. */
export function edgeTouchesRef(edge: ApiDomainEdge, ref: string): boolean {
  return edge.from.ref === ref || edge.to.ref === ref;
}
