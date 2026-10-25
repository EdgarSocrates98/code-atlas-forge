import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, signal } from '@angular/core';
import { ActivatedRoute, RouterLink } from '@angular/router';
import { ApiService, ApiSymbolDetail } from '../../core/api/api.service';
import { ApiAbortedError, ApiUnreachableError } from '../../core/api/api.errors';
import { apiErrorText } from '../../shared/errors';
import { EvidenceBadgeComponent, EvidenceLevel } from '../../shared/components/evidence-badge/evidence-badge.component';
import { StateBlockComponent } from '../../shared/components/state-block/state-block.component';

type Phase = 'loading' | 'ready' | 'empty' | 'error' | 'offline';

/**
 * Symbol detail page (/symbols/:id where :id is the occurrence_id, plain
 * or `occ:{id}`). Shows the boundary record + grouped relations_summary
 * and links back to the symbol-level graph at this occurrence.
 */
@Component({
  selector: 'atlas-symbols',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink, StateBlockComponent, EvidenceBadgeComponent],
  template: `
    <section class="symbol-detail">
      <header class="symbol-detail__header">
        <h1>Símbolo</h1>
      </header>

      @switch (phase()) {
        @case ('loading') {
          <atlas-state-block state="loading" loadingMessage="Carregando símbolo…" />
        }
        @case ('offline') {
          <atlas-state-block state="offline" [detail]="errorDetail()" />
        }
        @case ('error') {
          <atlas-state-block state="error" [errorMessage]="errorMessage()" [detail]="errorDetail()" />
        }
        @case ('empty') {
          <atlas-state-block state="empty" emptyMessage="Identificador de símbolo inválido." />
        }
        @case ('ready') {
          @if (detail(); as d) {
            <article class="card">
              <header class="card__header">
                <h2 class="card__title">{{ shortName(d.qname) }}</h2>
                <span class="card__kind">{{ d.kind }}</span>
              </header>

              <dl class="fields">
                <div class="field">
                  <dt>Nome qualificado</dt>
                  <dd class="mono">
                    {{ d.qname }}
                    <button type="button" class="copy" (click)="copyQname(d.qname)">
                      {{ copied() ? 'Copiado!' : 'Copiar' }}
                    </button>
                  </dd>
                </div>
                <div class="field">
                  <dt>Identificadores</dt>
                  <dd class="mono">
                    ocorrência {{ d.occurrence_id }} · símbolo {{ d.symbol_id }}
                  </dd>
                </div>
                <div class="field">
                  <dt>Arquivo</dt>
                  <dd class="mono">{{ d.path }}</dd>
                </div>
                <div class="field">
                  <dt>Linguagem</dt>
                  <dd>{{ d.language }}</dd>
                </div>
                <div class="field">
                  <dt>Trecho</dt>
                  <dd class="mono">{{ d.span.start }} – {{ d.span.end }}</dd>
                </div>
                <div class="field">
                  <dt>Estado do parse</dt>
                  <dd>{{ d.parse_status }}</dd>
                </div>
              </dl>

              <h3 class="card__subtitle">Relações ({{ d.relations_summary.length }})</h3>
              @if (d.relations_summary.length) {
                <table class="relations">
                  <thead>
                    <tr>
                      <th scope="col">Tipo</th>
                      <th scope="col">Evidência</th>
                      <th scope="col">Ocorrências</th>
                    </tr>
                  </thead>
                  <tbody>
                    @for (r of d.relations_summary; track r.type + r.resolution_status) {
                      <tr>
                        <td>{{ r.type }}</td>
                        <td><atlas-evidence-badge [level]="levelOf(r.resolution_status)" /></td>
                        <td>{{ r.n }}</td>
                      </tr>
                    }
                  </tbody>
                </table>
              } @else {
                <p class="muted">Sem relações registradas neste snapshot.</p>
              }

              <footer class="card__actions">
                <a
                  class="action action--primary"
                  [routerLink]="['/graph']"
                  [queryParams]="{ focus: d.occurrence_id }"
                >
                  Ver no grafo
                </a>
                <a class="action" routerLink="/graph">Abrir arquitetura</a>
              </footer>
            </article>
          }
        }
      }
    </section>
  `,
  styles: [
    `
      .symbol-detail {
        padding: var(--atlas-space-5);
        max-width: 72ch;
      }
      .symbol-detail__header h1 {
        font-size: var(--atlas-font-size-xl);
        margin: 0 0 var(--atlas-space-4);
      }
      .card {
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-4);
        background: var(--atlas-color-bg-raised);
      }
      .card__header {
        display: flex;
        align-items: baseline;
        gap: var(--atlas-space-2);
        flex-wrap: wrap;
      }
      .card__title {
        margin: 0;
        font-size: var(--atlas-font-size-lg);
        overflow-wrap: anywhere;
      }
      .card__kind {
        font-size: var(--atlas-font-size-xs);
        color: var(--atlas-color-text-muted);
        text-transform: uppercase;
        letter-spacing: 0.04em;
      }
      .card__subtitle {
        font-size: var(--atlas-font-size-md);
        margin: var(--atlas-space-4) 0 var(--atlas-space-2);
      }
      .fields {
        display: grid;
        gap: var(--atlas-space-2);
        margin: var(--atlas-space-3) 0 0;
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
      .mono {
        font-family: var(--atlas-font-family-mono);
        font-size: var(--atlas-font-size-xs);
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
      .relations {
        width: 100%;
        border-collapse: collapse;
        font-size: var(--atlas-font-size-sm);
      }
      .relations th,
      .relations td {
        text-align: left;
        padding: var(--atlas-space-1) var(--atlas-space-2);
        border-bottom: 1px solid var(--atlas-color-border);
      }
      .muted {
        color: var(--atlas-color-text-muted);
      }
      .card__actions {
        display: flex;
        gap: var(--atlas-space-2);
        margin-top: var(--atlas-space-4);
      }
      .action {
        font-size: var(--atlas-font-size-sm);
        padding: var(--atlas-space-1) var(--atlas-space-3);
        border-radius: var(--atlas-radius-md);
        border: 1px solid var(--atlas-color-border);
        background: var(--atlas-color-bg);
        color: var(--atlas-color-text);
        text-decoration: none;
      }
      .action--primary {
        background: var(--atlas-color-accent);
        border-color: var(--atlas-color-accent);
        color: var(--atlas-color-accent-text);
      }
    `,
  ],
})
export class SymbolsComponent implements OnInit, OnDestroy {
  readonly phase = signal<Phase>('loading');
  readonly detail = signal<ApiSymbolDetail | null>(null);
  readonly errorMessage = signal('');
  readonly errorDetail = signal('');
  readonly copied = signal(false);
  private readonly controller = new AbortController();
  private copyTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly api: ApiService,
    private readonly route: ActivatedRoute,
  ) {}

  ngOnInit(): void {
    const raw = this.route.snapshot.paramMap.get('id') ?? '';
    const occId = this.parseId(raw);
    if (occId === null) {
      this.phase.set('empty');
      return;
    }
    void this.load(occId);
  }

  ngOnDestroy(): void {
    this.controller.abort();
  }

  private parseId(raw: string): number | null {
    const text = raw.startsWith('occ:') ? raw.slice(4) : raw;
    const n = Number(text);
    return Number.isInteger(n) && n > 0 ? n : null;
  }

  private async load(occurrenceId: number): Promise<void> {
    this.phase.set('loading');
    try {
      const detail = await this.api.getSymbolDetail(
        occurrenceId,
        {},
        { signal: this.controller.signal },
      );
      this.detail.set(detail);
      this.phase.set('ready');
    } catch (error) {
      if (error instanceof ApiAbortedError) return;
      if (error instanceof ApiUnreachableError) {
        this.phase.set('offline');
        this.errorDetail.set(apiErrorText(error));
        return;
      }
      this.phase.set('error');
      this.errorMessage.set(apiErrorText(error));
      this.errorDetail.set('');
    }
  }

  levelOf(status: string): EvidenceLevel {
    const known = new Set(['verified', 'supported', 'possible', 'unresolved', 'external']);
    return (known.has(status) ? status : 'unknown') as EvidenceLevel;
  }

  shortName(qname: string): string {
    const i = qname.lastIndexOf('.');
    return i >= 0 ? qname.slice(i + 1) || qname : qname;
  }

  async copyQname(qname: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(qname);
      this.copied.set(true);
      if (this.copyTimer) clearTimeout(this.copyTimer);
      this.copyTimer = setTimeout(() => this.copied.set(false), 1600);
    } catch {
      this.copied.set(false);
    }
  }
}
