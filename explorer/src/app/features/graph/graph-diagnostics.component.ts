import {
  ChangeDetectionStrategy,
  Component,
  NgZone,
  OnDestroy,
  OnInit,
  input,
  signal,
} from '@angular/core';
import type { GraphCanvasComponent } from './graph-canvas.component';
import { GraphDataStore } from './graph-data.store';

/** How often the sampler republishes values (ms). */
const SAMPLE_WINDOW_MS = 500;

/**
 * Collapsible diagnostics panel for the graph feature (Phase 15).
 *
 * Every number shown is real:
 * - node/edge/hidden counts and the render cap come straight from
 *   GraphDataStore signals;
 * - camera ratio and layout flags are read live off the sigma canvas
 *   ("—" when there is no canvas — table view or WebGL failure);
 * - FPS is estimated by a requestAnimationFrame sampler that exists only
 *   while this panel is alive — closing the panel destroys the component
 *   and cancels the loop, so no permanent profiling runs in the app.
 *
 * No transitions/animations: the panel is honest under
 * prefers-reduced-motion by construction.
 */
@Component({
  selector: 'atlas-graph-diagnostics',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="diag" id="graph-diagnostics" aria-label="Diagnósticos do grafo">
      <h2 class="diag__title">Diagnósticos do grafo</h2>
      <dl class="diag__grid">
        <div class="diag__item">
          <dt>Nós visíveis</dt>
          <dd class="diag__value">{{ store.nodes().length }}</dd>
        </div>
        <div class="diag__item">
          <dt>Nós ocultos (limite)</dt>
          <dd class="diag__value">{{ store.hiddenNodeCount() }}</dd>
        </div>
        <div class="diag__item">
          <dt>Relações visíveis</dt>
          <dd class="diag__value">{{ store.visibleEdges().length }}</dd>
        </div>
        <div class="diag__item">
          <dt>Relações carregadas</dt>
          <dd class="diag__value">{{ store.edges().length }}</dd>
        </div>
        <div class="diag__item">
          <dt>Razão de zoom</dt>
          <dd class="diag__value">{{ zoomText() }}</dd>
        </div>
        <div class="diag__item">
          <dt>Estado do layout</dt>
          <dd class="diag__value">{{ layoutBusy() ? 'executando' : 'ocioso' }}</dd>
        </div>
        <div class="diag__item">
          <dt>Último layout</dt>
          <dd class="diag__value">{{ lastLayoutText() }}</dd>
        </div>
        <div class="diag__item">
          <dt>Limite de renderização</dt>
          <dd class="diag__value">{{ store.nodeCap() }}</dd>
        </div>
        <div class="diag__item">
          <dt>FPS (estimado)</dt>
          <dd class="diag__value">{{ fps() ?? '—' }}</dd>
        </div>
      </dl>
      <p class="diag__note">
        Amostra ao vivo — FPS e câmera são medidos só enquanto este painel está
        aberto; "—" indica valor não mensurável no momento.
      </p>
    </section>
  `,
  styles: [
    `
      .diag {
        border: 1px solid var(--atlas-color-border);
        border-radius: var(--atlas-radius-md);
        background: var(--atlas-color-bg-subtle);
        padding: var(--atlas-space-2) var(--atlas-space-3);
      }
      .diag__title {
        margin: 0 0 var(--atlas-space-2);
        font-size: var(--atlas-font-size-sm);
      }
      .diag__grid {
        margin: 0;
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(8.5rem, 1fr));
        gap: var(--atlas-space-2) var(--atlas-space-3);
      }
      .diag__item dt {
        font-size: var(--atlas-font-size-xs);
        color: var(--atlas-color-text-muted);
      }
      .diag__item dd {
        margin: 0;
      }
      .diag__value {
        font-family: var(--atlas-font-family-mono);
        font-size: var(--atlas-font-size-sm);
      }
      .diag__note {
        margin: var(--atlas-space-2) 0 0;
        font-size: var(--atlas-font-size-xs);
        color: var(--atlas-color-text-muted);
      }
    `,
  ],
})
export class GraphDiagnosticsComponent implements OnInit, OnDestroy {
  /**
   * Live canvas handle from the host — undefined whenever the sigma
   * renderer is not mounted (table view, loading phase, WebGL failure).
   */
  readonly canvas = input<GraphCanvasComponent | undefined>();

  readonly fps = signal<number | null>(null);
  readonly zoomRatio = signal<number | null>(null);
  readonly layoutBusy = signal(false);
  readonly lastLayoutMs = signal<number | null>(null);

  private rafId: number | null = null;
  private frames = 0;
  private windowStart = 0;

  constructor(
    readonly store: GraphDataStore,
    private readonly zone: NgZone,
  ) {}

  ngOnInit(): void {
    this.startSampler();
  }

  ngOnDestroy(): void {
    this.stopSampler();
  }

  zoomText(): string {
    const ratio = this.zoomRatio();
    if (ratio === null) return '—';
    // Ratio is "fraction of the framed graph on screen" — show it raw with
    // enough precision to be useful at both extremes of the camera range.
    return ratio >= 10 ? ratio.toFixed(0) : ratio.toFixed(2);
  }

  lastLayoutText(): string {
    const ms = this.lastLayoutMs();
    if (ms === null) return '—';
    return `${ms < 10 ? ms.toFixed(1) : Math.round(ms)} ms`;
  }

  /**
   * rAF loop running outside Angular: counts frames and republishes every
   * SAMPLE_WINDOW_MS — frequent enough to be live, coarse enough that the
   * numbers stay readable. Reads camera/layout state in the same tick so
   * nothing is sampled while the panel is closed.
   */
  private startSampler(): void {
    if (typeof requestAnimationFrame !== 'function') return;
    this.zone.runOutsideAngular(() => {
      const tick = (now: number): void => {
        this.frames++;
        if (this.windowStart === 0) this.windowStart = now;
        const elapsed = now - this.windowStart;
        if (elapsed >= SAMPLE_WINDOW_MS) {
          this.fps.set(Math.round((this.frames * 1000) / elapsed));
          this.frames = 0;
          this.windowStart = now;
          const canvas = this.canvas();
          this.zoomRatio.set(canvas?.cameraRatio() ?? null);
          this.layoutBusy.set(canvas?.layoutBusy ?? false);
          this.lastLayoutMs.set(canvas?.lastLayoutMs ?? null);
        }
        this.rafId = requestAnimationFrame(tick);
      };
      this.rafId = requestAnimationFrame(tick);
    });
  }

  private stopSampler(): void {
    if (this.rafId !== null) {
      cancelAnimationFrame(this.rafId);
      this.rafId = null;
    }
  }
}
