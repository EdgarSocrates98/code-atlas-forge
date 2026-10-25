import { ChangeDetectionStrategy, Component, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { EvidenceBadgeComponent, EvidenceLevel } from '../../shared/components/evidence-badge/evidence-badge.component';
import { GraphDataStore } from './graph-data.store';
import { GraphEdgeVM, GraphNodeVM } from './graph.models';
import { parseOccurrenceId, toEvidenceLevel } from './graph.utils';

/**
 * Side drawer inspecting the selected node or edge. Data comes straight
 * from the store — never refetched for display except `relations_summary`
 * (which only the /symbols/{id} detail endpoint provides).
 */
@Component({
    selector: 'atlas-graph-inspector',
    changeDetection: ChangeDetectionStrategy.OnPush,
    imports: [RouterLink, EvidenceBadgeComponent],
    template: `
    <aside class="inspector" aria-label="Inspetor de seleção">
      @if (node(); as n) {
        <header class="inspector__header">
          <h2 class="inspector__title">{{ n.label }}</h2>
          <span class="inspector__kind">{{ n.kind === 'file' ? 'arquivo' : n.symbolKind }}</span>
        </header>

        <dl class="inspector__fields">
          <div class="field">
            <dt>Nome qualificado</dt>
            <dd class="field--mono">
              {{ n.qname }}
              <button type="button" class="copy" (click)="copyQname(n.qname)">
                {{ copied() ? 'Copiado!' : 'Copiar' }}
              </button>
            </dd>
          </div>
          <div class="field">
            <dt>Arquivo</dt>
            <dd class="field--mono">{{ n.path }}</dd>
          </div>
          @if (n.language) {
            <div class="field">
              <dt>Linguagem</dt>
              <dd>{{ n.language }}</dd>
            </div>
          }
          @if (n.span) {
            <div class="field">
              <dt>Trecho</dt>
              <dd class="field--mono">{{ n.span.start }} – {{ n.span.end }}</dd>
            </div>
          }
          @if (n.line !== null) {
            <div class="field">
              <dt>Linha</dt>
              <dd class="field--mono">{{ n.line }}{{ n.col !== null ? ':' + n.col : '' }}</dd>
            </div>
          }
          @if (n.symbolCount !== null) {
            <div class="field">
              <dt>Símbolos</dt>
              <dd>{{ n.symbolCount }}</dd>
            </div>
          }
          @if (n.parseStatus) {
            <div class="field">
              <dt>Estado do parse</dt>
              <dd>{{ n.parseStatus }}</dd>
            </div>
          }
          @if (store.snapshotId() !== null) {
            <div class="field">
              <dt>Snapshot</dt>
              <dd class="field--mono">#{{ store.snapshotId() }}</dd>
            </div>
          }
          @if (n.pinned) {
            <div class="field">
              <dt>Fixação</dt>
              <dd>Nó fixado na posição</dd>
            </div>
          }
        </dl>

        @if (store.detailLoading()) {
          <p class="inspector__note">Carregando resumo de relações…</p>
        } @else {
          @if (store.selectedDetail(); as detail) {
            @if (detail.relations_summary.length) {
              <h3 class="inspector__subtitle">Relações</h3>
              <ul class="inspector__relations">
                @for (rel of detail.relations_summary; track rel.type + rel.resolution_status) {
                  <li>
                    <atlas-evidence-badge [level]="levelOf(rel.resolution_status)" />
                    <span class="rel__type">{{ rel.type }}</span>
                    <span class="rel__n">× {{ rel.n }}</span>
                  </li>
                }
              </ul>
            } @else {
              <p class="inspector__note">Sem relações registradas neste snapshot.</p>
            }
          }
        }

        <footer class="inspector__actions">
          @if (n.kind === 'file') {
            <button type="button" class="action action--primary" (click)="drill(n)">
              Explorar símbolos do arquivo
            </button>
          } @else {
            <button
              type="button"
              class="action action--primary"
              [disabled]="n.expanded || busy()"
              (click)="expand(n)"
            >
              {{ n.expanded ? 'Expandido' : busy() ? 'Expandindo…' : 'Expandir vizinhança' }}
            </button>
            <a class="action" [routerLink]="['/symbols', occId(n)]">Abrir página do símbolo</a>
          }
          <button type="button" class="action" (click)="store.togglePin(n.id)">
            {{ n.pinned ? 'Soltar nó' : 'Fixar nó' }}
          </button>
        </footer>
      } @else {
        @if (edge(); as e) {
          <header class="inspector__header">
            <h2 class="inspector__title">Relação</h2>
            <span class="inspector__kind">{{ e.type }}</span>
          </header>

          <dl class="inspector__fields">
            <div class="field">
              <dt>ID</dt>
              <dd class="field--mono">{{ e.id }}</dd>
            </div>
            <div class="field">
              <dt>De</dt>
              <dd>
                <button type="button" class="linklike" (click)="goNode(e.from)">
                  {{ nodeLabel(e.from) }}
                </button>
              </dd>
            </div>
            <div class="field">
              <dt>Para</dt>
              <dd>
                @if (e.to) {
                  <button type="button" class="linklike" (click)="goNode(e.to)">
                    {{ nodeLabel(e.to) }}
                  </button>
                } @else {
                  <span>Alvo não resolvido{{ e.toUnresolved ? ': ' + e.toUnresolved : '' }}</span>
                }
              </dd>
            </div>
            <div class="field">
              <dt>Estado de evidência</dt>
              <dd>
                <atlas-evidence-badge [level]="levelOf(e.resolutionStatus)" />
                <span class="field__raw">{{ e.resolutionStatus }}</span>
              </dd>
            </div>
            @if (e.confidenceClass) {
              <div class="field">
                <dt>Classe de confiança</dt>
                <dd class="field--mono">{{ e.confidenceClass }}</dd>
              </div>
            }
            <div class="field">
              <dt>Resolver</dt>
              <dd class="field--mono">{{ e.resolver }}</dd>
            </div>
            <div class="field">
              <dt>Evidências</dt>
              <dd class="field--mono">
                @if (e.evidenceIds.length) {
                  {{ e.evidenceIds.join(', ') }}
                } @else {
                  <span class="field__empty">Sem evidência registrada</span>
                }
              </dd>
            </div>
          </dl>
        } @else {
          <p class="inspector__hint">
            Selecione um nó ou uma relação no grafo (ou na lista) para inspecionar
            detalhes, evidência e ações.
          </p>
        }
      }
    </aside>
  `,
    styles: [
        `
      .inspector {
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-3);
        padding: var(--atlas-space-4);
        height: 100%;
        overflow: auto;
        border-left: 1px solid var(--atlas-color-border);
        background: var(--atlas-color-bg-raised);
      }
      .inspector__header {
        display: flex;
        align-items: baseline;
        gap: var(--atlas-space-2);
        flex-wrap: wrap;
      }
      .inspector__title {
        margin: 0;
        font-size: var(--atlas-font-size-lg);
        overflow-wrap: anywhere;
      }
      .inspector__kind {
        font-size: var(--atlas-font-size-xs);
        color: var(--atlas-color-text-muted);
        text-transform: uppercase;
        letter-spacing: 0.04em;
      }
      .inspector__subtitle {
        margin: var(--atlas-space-2) 0 0;
        font-size: var(--atlas-font-size-sm);
      }
      .inspector__fields {
        margin: 0;
        display: grid;
        gap: var(--atlas-space-2);
      }
      .field dt {
        font-size: var(--atlas-font-size-xs);
        color: var(--atlas-color-text-muted);
      }
      .field dd {
        margin: 0;
        font-size: var(--atlas-font-size-sm);
        overflow-wrap: anywhere;
      }
      .field--mono {
        font-family: var(--atlas-font-family-mono);
        font-size: var(--atlas-font-size-xs);
      }
      .field__raw {
        margin-left: var(--atlas-space-2);
        font-family: var(--atlas-font-family-mono);
        font-size: var(--atlas-font-size-xs);
        color: var(--atlas-color-text-muted);
      }
      .field__empty {
        font-family: var(--atlas-font-family-sans, inherit);
        color: var(--atlas-color-text-muted);
      }
      .copy {
        margin-left: var(--atlas-space-2);
        font-size: var(--atlas-font-size-xs);
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-sm);
        background: var(--atlas-color-bg);
        color: var(--atlas-color-text);
        padding: 0 var(--atlas-space-2);
        cursor: pointer;
      }
      .inspector__relations {
        list-style: none;
        margin: 0;
        padding: 0;
        display: grid;
        gap: var(--atlas-space-1);
      }
      .inspector__relations li {
        display: flex;
        align-items: center;
        gap: var(--atlas-space-2);
        font-size: var(--atlas-font-size-sm);
      }
      .rel__n {
        color: var(--atlas-color-text-muted);
      }
      .inspector__actions {
        display: flex;
        flex-wrap: wrap;
        gap: var(--atlas-space-2);
        margin-top: auto;
        padding-top: var(--atlas-space-3);
        border-top: 1px solid var(--atlas-color-border);
      }
      .action {
        font-size: var(--atlas-font-size-sm);
        padding: var(--atlas-space-1) var(--atlas-space-3);
        border-radius: var(--atlas-radius-md);
        border: 1px solid var(--atlas-color-border);
        background: var(--atlas-color-bg);
        color: var(--atlas-color-text);
        cursor: pointer;
        text-decoration: none;
      }
      .action--primary {
        background: var(--atlas-color-accent);
        border-color: var(--atlas-color-accent);
        color: var(--atlas-color-accent-text);
      }
      .action:disabled {
        opacity: 0.6;
        cursor: default;
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
      .inspector__hint,
      .inspector__note {
        color: var(--atlas-color-text-muted);
        font-size: var(--atlas-font-size-sm);
      }
    `,
    ]
})
export class GraphInspectorComponent {
  readonly copied = signal(false);
  private copyTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(readonly store: GraphDataStore) {}

  node(): GraphNodeVM | null {
    return this.store.selectedNode();
  }

  edge(): GraphEdgeVM | null {
    return this.store.selectedEdge();
  }

  busy(): boolean {
    const id = this.store.selectedNodeId();
    return id ? this.store.expansionBusy().has(id) : false;
  }

  levelOf(status: string): EvidenceLevel {
    return toEvidenceLevel(status);
  }

  occId(node: GraphNodeVM): number | null {
    return node.occurrenceId ?? parseOccurrenceId(node.id);
  }

  nodeLabel(nodeId: string): string {
    const n = this.store.nodes().find((x) => x.id === nodeId);
    return n ? `${n.label} (${n.id})` : nodeId;
  }

  goNode(nodeId: string): void {
    this.store.selectNode(nodeId);
  }

  expand(node: GraphNodeVM): void {
    void this.store.expandNode(node.id);
  }

  drill(node: GraphNodeVM): void {
    void this.store.drillFile(node.path, node.symbolCount ?? 0, node.fileId);
  }

  async copyQname(qname: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(qname);
      this.copied.set(true);
    } catch {
      // clipboard unavailable (permissions/headless) — select fallback
      this.copied.set(false);
      return;
    }
    if (this.copyTimer) clearTimeout(this.copyTimer);
    this.copyTimer = setTimeout(() => this.copied.set(false), 1600);
  }
}
