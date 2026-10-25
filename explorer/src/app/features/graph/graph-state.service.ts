import { Injectable } from '@angular/core';
import type { GraphMode } from './graph.models';

/** Camera triple worth restoring — angle is always 0 (rotation disabled). */
export interface GraphCameraState {
  x: number;
  y: number;
  ratio: number;
}

/**
 * Everything needed to re-enter one graph view exactly as it was left:
 * camera, mode and selection. Positions are not stored — node VMs carry
 * their own coordinates and the layout is deterministic per data version.
 */
export interface GraphViewState {
  /** Mode the state was captured in — camera only restores on mode match. */
  mode: GraphMode;
  camera: GraphCameraState | null;
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  pinnedIds: string[];
}

/**
 * Outlives the component-scoped GraphDataStore so navigating away and back
 * (graph → symbol page → graph) restores the camera and the selection.
 *
 * Keys are `r{repo}/s{snapshot}/{mode}`: repo+snapshot scope per the spec,
 * mode suffix so an architecture camera is never applied over a symbol
 * layout (and vice-versa). Bounded to the last few distinct views — this
 * is a UX nicety, not a cache of record.
 */
@Injectable({ providedIn: 'root' })
export class GraphStateService {
  private static readonly MAX_KEYS = 8;
  private readonly views = new Map<string, GraphViewState>();

  static key(
    repositoryId: number | null,
    snapshotId: number | null,
    mode: GraphMode,
  ): string {
    return `r${repositoryId ?? '?'}/s${snapshotId ?? '?'}/${mode}`;
  }

  /** Insert-or-replace; evicts the least recently written key past the cap. */
  save(key: string, state: GraphViewState): void {
    this.views.delete(key);
    this.views.set(key, state);
    while (this.views.size > GraphStateService.MAX_KEYS) {
      const oldest = this.views.keys().next().value;
      if (oldest === undefined) break;
      this.views.delete(oldest);
    }
  }

  /** Read once and remove — a restore applies to the next entry only. */
  consume(key: string): GraphViewState | undefined {
    const state = this.views.get(key);
    if (state) this.views.delete(key);
    return state;
  }

  clear(): void {
    this.views.clear();
  }
}
