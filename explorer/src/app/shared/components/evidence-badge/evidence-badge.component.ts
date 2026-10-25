import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

/**
 * Evidence strength as returned by the backend plus the relation-level
 * resolution states. The badge always renders marker + text label next to
 * the color so the state never depends on color alone (WCAG 2.2 SC 1.4.1).
 */
export type EvidenceLevel =
  | 'verified'
  | 'supported'
  | 'possible'
  | 'unknown'
  | 'unresolved'
  | 'external';

const LEVEL_META: Record<EvidenceLevel, { label: string; marker: string }> = {
  verified: { label: 'Verificado', marker: '✓' },
  supported: { label: 'Apoiado', marker: '▲' },
  possible: { label: 'Possível', marker: '◆' },
  unknown: { label: 'Desconhecido', marker: '?' },
  unresolved: { label: 'Não resolvido', marker: '✕' },
  external: { label: 'Externo', marker: '◇' },
};

/**
 * Evidence-strength badge: color + marker + pt-BR label driven by the
 * `--atlas-evidence-*` design tokens.
 */
@Component({
  selector: 'atlas-evidence-badge',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <span
      class="evidence-badge evidence-badge--{{ level() }}"
      [title]="label()"
      role="img"
      [attr.aria-label]="label()"
    >
      <span class="evidence-badge__marker" aria-hidden="true">{{ marker() }}</span>
      <span class="evidence-badge__label">{{ label() }}</span>
    </span>
  `,
  styles: [
    `
      .evidence-badge {
        display: inline-flex;
        align-items: center;
        gap: var(--atlas-space-1);
        padding: 0 var(--atlas-space-2);
        border-radius: var(--atlas-radius-full);
        border: 1px solid currentColor;
        font-size: var(--atlas-font-size-xs);
        font-weight: var(--atlas-font-weight-medium);
        line-height: var(--atlas-line-height-tight);
        white-space: nowrap;
      }

      .evidence-badge__marker {
        font-weight: var(--atlas-font-weight-bold);
      }

      .evidence-badge--verified {
        color: var(--atlas-evidence-verified);
        background: var(--atlas-evidence-verified-bg);
      }

      .evidence-badge--supported {
        color: var(--atlas-evidence-supported);
        background: var(--atlas-evidence-supported-bg);
      }

      .evidence-badge--possible {
        color: var(--atlas-evidence-possible);
        background: var(--atlas-evidence-possible-bg);
      }

      .evidence-badge--unknown {
        color: var(--atlas-color-text-muted);
        background: var(--atlas-color-bg-subtle);
      }

      .evidence-badge--unresolved {
        color: var(--atlas-evidence-unresolved);
        background: var(--atlas-evidence-unresolved-bg);
      }

      .evidence-badge--external {
        color: var(--atlas-evidence-external);
        background: var(--atlas-evidence-external-bg);
      }
    `,
  ],
})
export class EvidenceBadgeComponent {
  readonly level = input.required<EvidenceLevel>();

  readonly label = computed(() => LEVEL_META[this.level()].label);
  readonly marker = computed(() => LEVEL_META[this.level()].marker);
}
