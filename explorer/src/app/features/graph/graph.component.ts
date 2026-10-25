import {
  ChangeDetectionStrategy,
  Component,
  OnInit,
  signal,
  viewChild,
} from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { EvidenceBadgeComponent } from '../../shared/components/evidence-badge/evidence-badge.component';
import { StateBlockComponent } from '../../shared/components/state-block/state-block.component';
import { GraphCanvasComponent } from './graph-canvas.component';
import { GraphDataStore } from './graph-data.store';
import { GraphDiagnosticsComponent } from './graph-diagnostics.component';
import { GraphInspectorComponent } from './graph-inspector.component';
import { GraphTableComponent } from './graph-table.component';
import { ALL_RESOLUTION_STATUSES } from './graph.models';
import { toEvidenceLevel } from './graph.utils';

type ViewMode = 'graph' | 'table';

/**
 * Host for the graph feature (lf-0088/0089): architecture overview +
 * symbol graph + inspector + drill-down breadcrumb.
 * All data lives in GraphDataStore (component-scoped provider, so the
 * canvas/inspector/table share one instance per visit).
 */
@Component({
  selector: 'atlas-graph',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    StateBlockComponent,
    EvidenceBadgeComponent,
    GraphCanvasComponent,
    GraphDiagnosticsComponent,
    GraphInspectorComponent,
    GraphTableComponent,
  ],
  providers: [GraphDataStore],
  templateUrl: './graph.component.html',
  styleUrl: './graph.component.css',
})
export class GraphComponent implements OnInit {
  readonly canvas = viewChild(GraphCanvasComponent);
  readonly viewModes: { id: ViewMode; label: string }[] = [
    { id: 'graph', label: 'Grafo' },
    { id: 'table', label: 'Lista' },
  ];
  readonly statuses = ALL_RESOLUTION_STATUSES;
  viewMode: ViewMode = 'graph';
  /** Diagnostics panel open state — closed destroys the rAF sampler. */
  readonly showDiagnostics = signal(false);

  constructor(
    readonly store: GraphDataStore,
    private readonly route: ActivatedRoute,
  ) {}

  ngOnInit(): void {
    const focus = this.route.snapshot.queryParamMap.get('focus');
    if (focus) {
      void this.store.startAtSymbol(focus);
    } else {
      void this.store.loadInitial();
    }
  }

  setView(mode: ViewMode): void {
    this.viewMode = mode;
  }

  toggleDiagnostics(): void {
    this.showDiagnostics.update((open) => !open);
  }

  levelOf(status: string) {
    return toEvidenceLevel(status);
  }

  onCapChange(event: Event): void {
    const input = event.target as HTMLInputElement;
    const value = Number(input.value);
    if (Number.isFinite(value) && value > 0) {
      this.store.setNodeCap(value);
    }
  }

  retry(): void {
    void this.store.loadInitial();
  }

  isFiltered(status: string): boolean {
    return this.store.statusFilter().has(status);
  }

  isTypeFiltered(type: string): boolean {
    return this.store.edgeTypeFilter().has(type);
  }
}
