import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink } from '@angular/router';
import { EvidenceBadgeComponent, EvidenceLevel } from '../../shared/components/evidence-badge/evidence-badge.component';
import { GraphDataStore } from './graph-data.store';
import { GraphNodeVM } from './graph.models';
import { toEvidenceLevel } from './graph.utils';

/**
 * Tabular alternative to the canvas — the WCAG-parity view. Every action
 * available on the canvas (select, expand, pin, drill, open) exists here
 * as a plain button so keyboard and AT users lose nothing.
 */
@Component({
  selector: 'atlas-graph-table',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, EvidenceBadgeComponent],
  template: `
    <div class="tables">
      <section class="tables__section" aria-labelledby="nodes-caption">
        <h2 id="nodes-caption" class="tables__caption">
          Nós ({{ store.nodes().length }})
          @if (store.hiddenNodeCount() > 0) {
            <span class="tables__muted">+ {{ store.hiddenNodeCount() }} ocultos pelo limite</span>
          }
        </h2>
        <div class="tables__scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">Nome</th>
                <th scope="col">Tipo</th>
                <th scope="col">Linguagem</th>
                <th scope="col">Arquivo</th>
                <th scope="col">Ações</th>
              </tr>
            </thead>
            <tbody>
              @for (n of store.nodes(); track n.id) {
                <tr [class.row--selected]="n.id === store.selectedNodeId()">
                  <td class="cell--name">
                    <button type="button" class="linklike" (click)="store.selectNode(n.id)">
                      {{ n.label }}
                    </button>
                    @if (n.pinned) {
                      <span class="tag" title="Nó fixado">fixo</span>
                    }
                  </td>
                  <td>{{ n.kind === 'file' ? 'arquivo' : n.symbolKind }}</td>
                  <td>{{ n.language || '—' }}</td>
                  <td class="cell--mono">{{ n.path }}</td>
                  <td class="cell--actions">
                    @if (n.kind === 'file') {
                      <button
                        type="button"
                        class="mini"
                        (click)="drill(n)"
                      >
                        Detalhar
                      </button>
                    } @else {
                      <button
                        type="button"
                        class="mini"
                        [disabled]="n.expanded || store.expansionBusy().has(n.id)"
                        (click)="expand(n)"
                      >
                        {{ n.expanded ? 'ok' : 'Expandir' }}
                      </button>
                      @if (n.occurrenceId !== null) {
                        <a class="mini" [routerLink]="['/symbols', n.occurrenceId]">Abrir</a>
                      }
                    }
                    <button type="button" class="mini" (click)="store.togglePin(n.id)">
                      {{ n.pinned ? 'Soltar' : 'Fixar' }}
                    </button>
                  </td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      </section>

      <section class="tables__section" aria-labelledby="edges-caption">
        <h2 id="edges-caption" class="tables__caption">
          Relações ({{ store.visibleEdges().length }})
          @if (store.hiddenEdgeCount() > 0) {
            <span class="tables__muted">{{ store.hiddenEdgeCount() }} filtradas</span>
          }
        </h2>
        <div class="tables__scroll">
          <table>
            <thead>
              <tr>
                <th scope="col">De</th>
                <th scope="col">Para</th>
                <th scope="col">Tipo</th>
                <th scope="col">Evidência</th>
                <th scope="col">Resolver</th>
              </tr>
            </thead>
            <tbody>
              @for (e of store.visibleEdges(); track e.id) {
                <tr [class.row--selected]="e.id === store.selectedEdgeId()">
                  <td>
                    <button type="button" class="linklike" (click)="store.selectNode(e.from)">
                      {{ label(e.from) }}
                    </button>
                  </td>
                  <td>
                    <button type="button" class="linklike" (click)="store.selectNode(e.to)">
                      {{ label(e.to) }}
                    </button>
                  </td>
                  <td>
                    <button type="button" class="linklike" (click)="store.selectEdge(e.id)">
                      {{ e.type }}
                    </button>
                  </td>
                  <td><atlas-evidence-badge [level]="levelOf(e.resolutionStatus)" /></td>
                  <td class="cell--mono">{{ e.resolver }}</td>
                </tr>
              }
            </tbody>
          </table>
        </div>
      </section>
    </div>
  `,
  styles: [
    `
      .tables {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: var(--atlas-space-4);
        min-height: 0;
        height: 100%;
      }
      @media (max-width: 1100px) {
        .tables {
          grid-template-columns: 1fr;
          grid-auto-rows: minmax(0, 1fr);
        }
      }
      .tables__section {
        display: flex;
        flex-direction: column;
        min-height: 0;
      }
      .tables__caption {
        font-size: var(--atlas-font-size-sm);
        margin: 0 0 var(--atlas-space-2);
      }
      .tables__muted {
        color: var(--atlas-color-text-muted);
        font-weight: var(--atlas-font-weight-regular);
      }
      .tables__scroll {
        overflow: auto;
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        flex: 1;
      }
      table {
        width: 100%;
        border-collapse: collapse;
        font-size: var(--atlas-font-size-sm);
      }
      th,
      td {
        text-align: left;
        padding: var(--atlas-space-1) var(--atlas-space-2);
        border-bottom: 1px solid var(--atlas-color-border);
        vertical-align: top;
      }
      thead th {
        position: sticky;
        top: 0;
        background: var(--atlas-color-bg-subtle);
        z-index: 1;
      }
      .row--selected {
        background: var(--atlas-evidence-supported-bg);
      }
      .cell--name {
        font-weight: var(--atlas-font-weight-medium);
      }
      .cell--mono {
        font-family: var(--atlas-font-family-mono);
        font-size: var(--atlas-font-size-xs);
        overflow-wrap: anywhere;
      }
      .cell--actions {
        white-space: nowrap;
      }
      .linklike {
        border: none;
        background: none;
        padding: 0;
        color: var(--atlas-color-accent);
        cursor: pointer;
        font: inherit;
        text-align: left;
        overflow-wrap: anywhere;
      }
      .mini {
        font-size: var(--atlas-font-size-xs);
        padding: 0 var(--atlas-space-2);
        margin-right: var(--atlas-space-1);
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-sm);
        background: var(--atlas-color-bg);
        color: var(--atlas-color-text);
        cursor: pointer;
        text-decoration: none;
        display: inline-block;
        line-height: 1.6;
      }
      .mini:disabled {
        opacity: 0.55;
        cursor: default;
      }
      .tag {
        margin-left: var(--atlas-space-1);
        font-size: var(--atlas-font-size-xs);
        color: var(--atlas-evidence-possible);
        border: 1px solid currentColor;
        border-radius: var(--atlas-radius-full);
        padding: 0 var(--atlas-space-1);
      }
    `,
  ],
})
export class GraphTableComponent {
  constructor(readonly store: GraphDataStore) {}

  levelOf(status: string): EvidenceLevel {
    return toEvidenceLevel(status);
  }

  label(nodeId: string): string {
    return this.store.nodes().find((n) => n.id === nodeId)?.label ?? nodeId;
  }

  expand(node: GraphNodeVM): void {
    void this.store.expandNode(node.id);
  }

  drill(node: GraphNodeVM): void {
    void this.store.drillFile(node.path, node.symbolCount ?? 0);
  }
}
