import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { DiagnosticsListComponent } from '../../shared/components/diagnostics-list/diagnostics-list.component';
import { EvidenceBadgeComponent } from '../../shared/components/evidence-badge/evidence-badge.component';
import type { ImpactView } from './impact.vm';

/**
 * Presentational renderer for an ImpactResult view-model.
 *
 * Shared by the Impact screen (revisions-by-expression) and the Revisions
 * screen (snapshot pair). Hard rules encoded here:
 * - partial analysis is ALWAYS bannered and never styled as verified;
 * - uncertainty is rendered as an axis separate from risk category;
 * - test candidates are labelled as candidates, never as coverage.
 *
 * Staged reveal honors `prefers-reduced-motion` purely via CSS — no timers.
 */
@Component({
    selector: 'atlas-impact-results',
    changeDetection: ChangeDetectionStrategy.OnPush,
    imports: [RouterLink, DiagnosticsListComponent, EvidenceBadgeComponent],
    template: `
    @if (view(); as v) {
      <div class="impact-result" role="region" aria-live="polite" aria-label="Resultado da análise de impacto">
        @if (v.partial) {
          <div class="impact-result__banner impact-result__banner--partial" role="alert">
            <strong>Análise parcial.</strong>
            O vínculo revisão↔snapshot não está totalmente verificado — trate
            os achados abaixo como indicativos, não como prova.
          </div>
        }
        @if (v.semanticBlocked) {
          <div class="impact-result__banner impact-result__banner--blocked" role="alert">
            <strong>Análise semântica bloqueada.</strong>
            O backend não atribuiu mudanças a estas revisões (binding
            indisponível ou conflitante). Apenas dados git/diagnósticos foram
            preservados.
          </div>
        }
        @if (v.truncated) {
          <div class="impact-result__banner impact-result__banner--warn" role="status">
            Resultado truncado pelos limites do analisador — a lista abaixo é um
            subconjunto, não o universo completo.
          </div>
        }

        <section class="impact-result__section impact-stage" style="--stage-i: 0">
          <h2>Resumo</h2>
          <p>
            {{ v.repository }} · base <code>{{ v.baseRevision || '—' }}</code>
            → alvo <code>{{ v.targetRevision || '(worktree)' }}</code>
            @if (v.baseSnapshotId !== null || v.targetSnapshotId !== null) {
              · snapshots base #{{ v.baseSnapshotId ?? '—' }} / alvo #{{
                v.targetSnapshotId ?? '—'
              }}
            }
          </p>
          <p>
            {{ v.totalChanges }} mudança(s) de símbolo · {{ v.totalImpacts }}
            candidato(s) de impacto · {{ v.totalTests }} candidato(s) de teste
          </p>
          @if (v.changeSet) {
            <p class="impact-result__muted">
              ChangeSet {{ v.changeSet.source }}: {{ v.changeSet.fileCount }}
              arquivo(s){{ v.changeSet.dirtyWorktree ? ' · worktree sujo' : '' }}
            </p>
          }
        </section>

        <section
          class="impact-result__section impact-stage"
          style="--stage-i: 1"
          aria-labelledby="risk-title"
        >
          <h2 id="risk-title">Risco</h2>
          <div class="risk">
            <div class="risk__axis">
              <span class="risk__axis-label">Categoria</span>
              <span class="risk__category risk__category--{{ v.risk.category }}">
                {{ v.risk.categoryLabel }}
              </span>
            </div>
            <div class="risk__axis">
              <span class="risk__axis-label">Incerteza (eixo separado)</span>
              <span class="risk__uncertainty">{{ v.risk.uncertaintyLabel }}</span>
            </div>
            @if (v.risk.formulaVersion) {
              <p class="impact-result__muted">fórmula {{ v.risk.formulaVersion }}</p>
            }
            @if (v.risk.reasons.length > 0) {
              <ul class="risk__reasons">
                @for (reason of v.risk.reasons; track $index) {
                  <li>{{ reason }}</li>
                }
              </ul>
            } @else {
              <p class="impact-result__muted">Nenhuma razão de risco registrada.</p>
            }
          </div>
        </section>

        @if (v.symbolGroups.length > 0) {
          <section
            class="impact-result__section impact-stage"
            style="--stage-i: 2"
            aria-labelledby="changes-title"
          >
            <h2 id="changes-title">Símbolos alterados</h2>
            @for (group of v.symbolGroups; track group.kind) {
              <h3 class="impact-result__group-title">
                {{ group.label }} ({{ group.items.length }})
              </h3>
              <ul class="impact-result__list">
                @for (change of group.items; track change.symbolKey) {
                  <li class="impact-item">
                    <div class="impact-item__head">
                      <code>{{ change.qualifiedName }}</code>
                      <atlas-evidence-badge [level]="change.strength" />
                    </div>
                    <p class="impact-result__muted">
                      {{ change.language }} · {{ change.kind }}
                      @if (change.detail) {
                        · {{ change.detail }}
                      }
                    </p>
                    @if (change.oldSignature || change.newSignature) {
                      <div class="impact-item__sides">
                        <div>
                          <span class="impact-item__side-label">Antes</span>
                          <code>{{ change.oldSignature || '—' }}</code>
                        </div>
                        <div>
                          <span class="impact-item__side-label">Depois</span>
                          <code>{{ change.newSignature || '—' }}</code>
                        </div>
                      </div>
                    }
                    @if (change.oldPath !== change.newPath) {
                      <div class="impact-item__sides">
                        <div>
                          <span class="impact-item__side-label">Caminho antes</span>
                          <code>{{ change.oldPath || '—' }}</code>
                        </div>
                        <div>
                          <span class="impact-item__side-label">Caminho depois</span>
                          <code>{{ change.newPath || '—' }}</code>
                        </div>
                      </div>
                    }
                  </li>
                }
              </ul>
            }
          </section>
        }

        @if (v.contractChanges.length > 0) {
          <section
            class="impact-result__section impact-stage"
            style="--stage-i: 3"
            aria-labelledby="contracts-title"
          >
            <h2 id="contracts-title">Contratos públicos</h2>
            <ul class="impact-result__list">
              @for (contract of v.contractChanges; track contract.symbolKey) {
                <li class="impact-item">
                  <div class="impact-item__head">
                    <code>{{ contract.symbolKey }}</code>
                    <atlas-evidence-badge [level]="contract.strength" />
                  </div>
                  <p class="impact-result__muted">
                    {{ contract.classification }} — {{ contract.compatibilityLabel }}
                    @if (contract.detail) {
                      · {{ contract.detail }}
                    }
                  </p>
                </li>
              }
            </ul>
          </section>
        }

        @if (v.blastGroups.length > 0) {
          <section
            class="impact-result__section impact-stage"
            style="--stage-i: 4"
            aria-labelledby="blast-title"
          >
            <h2 id="blast-title">Raio de impacto (candidatos)</h2>
            @for (group of v.blastGroups; track group.hops) {
              <h3 class="impact-result__group-title">
                {{ group.label }} ({{ group.items.length }})
              </h3>
              <ul class="impact-result__list">
                @for (item of group.items; track item.symbolKey + item.occurrenceId) {
                  <li class="impact-item">
                    <div class="impact-item__head">
                      <code>{{ item.qualifiedName }}</code>
                      <span class="impact-item__side-actions">
                        @if (item.occurrenceId !== null) {
                          <a
                            class="impact-item__graph"
                            [routerLink]="['/graph']"
                            [queryParams]="{
                              focus: 'occ:' + item.occurrenceId,
                            }"
                          >
                            Focar no grafo
                          </a>
                        }
                        <atlas-evidence-badge [level]="item.strength" />
                      </span>
                    </div>
                    <p class="impact-result__muted">
                      {{ item.impactLabel }} · {{ item.language }} ·
                      {{ item.kind }}
                      @if (item.viaCount > 0) {
                        · via {{ item.viaCount }} intermediário(s)
                      }
                      @if (item.fullyVerified) {
                        · cadeia totalmente verificada
                      }
                      @if (item.consumerLabel) {
                        · consumidor {{ item.consumerLabel }}
                      }
                    </p>
                    @if (item.reason) {
                      <p class="impact-result__muted">{{ item.reason }}</p>
                    }
                    @if (item.sourcePath) {
                      <p class="impact-result__muted">
                        <code>{{ item.sourcePath }}</code>
                      </p>
                    }
                  </li>
                }
              </ul>
            }
          </section>
        }

        @if (v.testCandidates.length > 0) {
          <section
            class="impact-result__section impact-stage"
            style="--stage-i: 5"
            aria-labelledby="tests-title"
          >
            <h2 id="tests-title">Candidatos de teste</h2>
            <p class="impact-result__muted">
              Sugestões derivadas de relações comprovadas — ser candidato não
              significa cobertura garantida.
            </p>
            <ul class="impact-result__list">
              @for (test of v.testCandidates; track test.testFile + test.testSymbol) {
                <li class="impact-item">
                  <div class="impact-item__head">
                    <code>{{ test.testSymbol || test.testFile }}</code>
                    <span class="impact-item__side-actions">
                      @if (test.occurrenceId !== null) {
                        <a
                          class="impact-item__graph"
                          [routerLink]="['/graph']"
                          [queryParams]="{
                            focus: 'occ:' + test.occurrenceId,
                          }"
                        >
                          Focar no grafo
                        </a>
                      }
                      <atlas-evidence-badge [level]="test.strength" />
                    </span>
                  </div>
                  <p class="impact-result__muted">
                    <span class="impact-item__tag">{{ test.revisionLabel }}</span>
                    relação {{ test.relationshipLabel }} ·
                    {{ test.reason || 'sem justificativa registrada' }}
                  </p>
                  @if (test.testSymbol) {
                    <p class="impact-result__muted">
                      <code>{{ test.testFile }}</code>
                    </p>
                  }
                </li>
              }
            </ul>
          </section>
        }

        @if (v.unknownCoverage.length > 0) {
          <section
            class="impact-result__section impact-stage"
            style="--stage-i: 6"
            aria-labelledby="unknown-title"
          >
            <h2 id="unknown-title">Cobertura desconhecida</h2>
            <p class="impact-result__muted">
              Áreas onde o índice não tem evidência suficiente para afirmar
              impacto — ausência de achado aqui não prova ausência de efeito.
            </p>
            <ul class="impact-result__list">
              @for (area of v.unknownCoverage; track area) {
                <li><code>{{ area }}</code></li>
              }
            </ul>
          </section>
        }

        @if (v.diagnosticsTotal.length > 0) {
          <atlas-diagnostics-list
            [items]="v.diagnosticsTotal"
            title="Diagnósticos da análise"
          />
        }
      </div>
    }
  `,
    styles: [
        `
      .impact-result {
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-4);
      }

      .impact-stage {
        animation: impact-rise var(--atlas-motion-slow) var(--atlas-motion-easing)
          both;
        animation-delay: calc(var(--stage-i, 0) * 90ms);
      }

      @keyframes impact-rise {
        from {
          opacity: 0;
          transform: translateY(6px);
        }
        to {
          opacity: 1;
          transform: none;
        }
      }

      @media (prefers-reduced-motion: reduce) {
        .impact-stage {
          animation: none;
        }
      }

      .impact-result__banner {
        padding: var(--atlas-space-3) var(--atlas-space-4);
        border-radius: var(--atlas-radius-md);
        border: 1px solid var(--atlas-color-warning);
        background: var(--atlas-color-warning-bg);
        color: var(--atlas-color-warning);
      }

      .impact-result__banner--blocked {
        border-color: var(--atlas-color-danger);
        background: var(--atlas-color-danger-bg);
        color: var(--atlas-color-danger);
      }

      .impact-result__banner--warn {
        border-style: dashed;
      }

      .impact-result__section {
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        padding: var(--atlas-space-4);
        background: var(--atlas-color-bg-raised);
      }

      .impact-result__section h2 {
        margin: 0 0 var(--atlas-space-2);
        font-size: var(--atlas-font-size-lg);
      }

      .impact-result__group-title {
        margin: var(--atlas-space-3) 0 var(--atlas-space-2);
        font-size: var(--atlas-font-size-md);
      }

      .impact-result__list {
        margin: 0;
        padding: 0;
        list-style: none;
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-2);
      }

      .impact-item {
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-sm);
        padding: var(--atlas-space-2) var(--atlas-space-3);
      }

      .impact-item__head {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        gap: var(--atlas-space-3);
        flex-wrap: wrap;
      }

      .impact-item__head code {
        font-family: var(--atlas-font-family-mono);
        overflow-wrap: anywhere;
      }

      .impact-item__side-actions {
        display: inline-flex;
        align-items: baseline;
        gap: var(--atlas-space-3);
        flex-wrap: wrap;
      }

      .impact-item__graph {
        color: var(--atlas-color-accent);
        font-size: var(--atlas-font-size-sm);
        white-space: nowrap;
      }

      .impact-item__graph:hover {
        text-decoration: underline;
      }

      .impact-item__sides {
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: var(--atlas-space-3);
        margin-top: var(--atlas-space-2);
      }

      .impact-item__sides code {
        display: block;
        font-family: var(--atlas-font-family-mono);
        font-size: var(--atlas-font-size-sm);
        overflow-wrap: anywhere;
        background: var(--atlas-color-bg-subtle);
        border-radius: var(--atlas-radius-sm);
        padding: var(--atlas-space-1) var(--atlas-space-2);
      }

      .impact-item__side-label {
        display: block;
        font-size: var(--atlas-font-size-xs);
        color: var(--atlas-color-text-muted);
        margin-bottom: var(--atlas-space-1);
      }

      .impact-item__tag {
        display: inline-block;
        padding: 0 var(--atlas-space-2);
        margin-right: var(--atlas-space-2);
        border-radius: var(--atlas-radius-full);
        border: 1px solid var(--atlas-color-border-strong);
        font-size: var(--atlas-font-size-xs);
      }

      .impact-result__muted {
        color: var(--atlas-color-text-muted);
        font-size: var(--atlas-font-size-sm);
        margin: var(--atlas-space-1) 0 0;
        overflow-wrap: anywhere;
      }

      .risk {
        display: flex;
        flex-direction: column;
        gap: var(--atlas-space-2);
      }

      .risk__axis {
        display: flex;
        align-items: center;
        gap: var(--atlas-space-3);
      }

      .risk__axis-label {
        min-width: 12rem;
        color: var(--atlas-color-text-muted);
        font-size: var(--atlas-font-size-sm);
      }

      .risk__category {
        font-weight: var(--atlas-font-weight-bold);
        padding: 0 var(--atlas-space-2);
        border-radius: var(--atlas-radius-sm);
      }

      .risk__category--low {
        color: var(--atlas-evidence-verified);
        background: var(--atlas-evidence-verified-bg);
      }

      .risk__category--moderate {
        color: var(--atlas-evidence-possible);
        background: var(--atlas-evidence-possible-bg);
      }

      .risk__category--high,
      .risk__category--critical {
        color: var(--atlas-evidence-unresolved);
        background: var(--atlas-evidence-unresolved-bg);
      }

      .risk__category--unknown {
        color: var(--atlas-color-text-muted);
        background: var(--atlas-color-bg-subtle);
      }

      .risk__uncertainty {
        color: var(--atlas-color-text);
      }

      .risk__reasons {
        margin: var(--atlas-space-2) 0 0;
        padding-left: var(--atlas-space-5);
      }
    `,
    ]
})
export class ImpactResultsComponent {
  readonly view = input.required<ImpactView>();
}
