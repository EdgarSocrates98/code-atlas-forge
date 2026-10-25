# Explorer Accessibility Audit — Cycle 6 (lf-0093, Phase 16: WCAG 2.2 AA)

Status: **static code audit only.** No browser, screen reader, or
automated a11y scanner (axe, Lighthouse) was run — this session had no
process-execution tool. Findings below come from reading templates,
component styles and design tokens directly. Contrast ratios were
computed by hand (WCAG relative-luminance formula) against the literal
hex values in `src/design-system/tokens.css`; they were not measured in a
rendered page. Treat this as a reviewed-but-unverified-in-browser audit.

## Checklist and findings

### 1. Focus-visible styles — PASS (pre-existing)
`src/styles.css` defines a global `:focus-visible` rule (2px solid
`--atlas-color-focus-ring`, visible offset), and `graph-canvas.component.ts`
adds its own `.graph-stage:focus-visible` outline for the WebGL stage
(default browser focus rings don't render reliably on a `<div>` acting as
a canvas host). No violation found.

### 2. `aria-live` on async states — PASS (pre-existing)
`StateBlockComponent` wraps all async phases in `aria-live="polite"`, with
`role="status"` for loading and `role="alert"` for error/offline (both of
those roles carry an implicit assertive/polite live region, so the
double-annotation is intentional belt-and-suspenders, not redundant
noise). The graph feature's own transient notices
(`graph.component.html`) use `role="status"` / `role="alert"` directly for
truncation/renderer-failure/transient-error banners. No violation found.

### 3. Skip link — PASS (pre-existing)
`shell.component.html` has `<a class="shell__skip-link" href="#shell-main">`
as the first focusable element, targeting `<main id="shell-main"
tabindex="-1">`. CSS moves it on-screen on `:focus`. No violation found.

### 4. Table alternative for the graph — PASS (pre-existing)
`GraphTableComponent` is a full non-canvas alternative: same node/edge
data, same actions (select, expand, pin, drill, open), rendered as two
real `<table>`s with `scope="col"` headers and `aria-labelledby` captions.
The host template (`graph.component.html`) switches to it automatically
when `store.rendererFailed()` (WebGL unavailable) and lets the user choose
it manually via the "Lista" view-mode toggle. No violation found.

### 5. `prefers-reduced-motion` coverage — PASS (pre-existing)
- `design-system/tokens.css` zeroes all `--atlas-motion-*` custom
  properties under `@media (prefers-reduced-motion: reduce)`, which
  neutralizes the `body` background/color transition in `styles.css` and
  the loading spinner's `animation-duration` reference in
  `state-block.component.ts`.
- `state-block.component.ts` additionally has an explicit
  `@media (prefers-reduced-motion: reduce) { .state-block__spinner {
  animation: none; } }` rule, which is the belt for the already-zeroed
  duration (suspenders) — correct, not redundant, since
  `animation-duration: 800ms` is hardcoded inline rather than reading the
  custom property.
- `graph-canvas.component.ts` reads `matchMedia('(prefers-reduced-motion:
  reduce)')` at runtime and switches camera zoom/pan from
  `animatedZoom`/`animatedUnzoom` to instant `camera.setState(...)` — this
  is real behavioral (not just CSS) reduced-motion handling for the
  WebGL canvas, which CSS media queries cannot reach. No violation found.

### 6. Contrast of evidence tokens — 1 violation found and fixed

Checked every evidence/status color pair in `tokens.css` against its
paired background by computing WCAG relative luminance by hand:

| Token pair (light theme) | Approx. contrast | Verdict |
|---|---|---|
| `--atlas-evidence-verified` on `-bg` | ~5.3:1 | pass (≥4.5:1) |
| `--atlas-evidence-supported` on `-bg` | ~5.2:1 | pass |
| `--atlas-evidence-possible` (#8a5a00 on #fdf1dc) | ~5.3:1 | pass |
| `--atlas-evidence-unresolved` on `-bg` | ~5.3:1 | pass |
| `--atlas-evidence-external` on `-bg` | ~5.4:1 | pass |
| `--atlas-color-text-muted` (#545b68) on `--atlas-color-bg` (#fff) | ~6.8:1 | pass |

All evidence badges (`EvidenceBadgeComponent`) also pair color with a
distinct marker glyph (✓ ▲ ◆ ✕ ◇) and a text label, so none of them rely on
hue alone (WCAG 2.2 SC 1.4.1) — confirmed by reading the template, which
always renders `marker` + `label` spans regardless of level.

**Violation found:** `graph.component.css` `.chip--off` (the dimmed state
of an edge-type/status filter chip when other filters are active) used
`opacity: 0.45` on top of `--atlas-color-text` / `--atlas-color-bg-subtle`.
Unlike `:disabled` controls elsewhere in the codebase (which WCAG 1.4.3
explicitly exempts), this chip is **not disabled** — it remains a fully
interactive toggle (`aria-pressed`, clickable) that is simply
visually de-emphasized because other filters are active. Blending its
text color to ~45% opacity over a light background pushes the effective
contrast down to roughly 1.8:1, well under the 4.5:1 AA threshold for
normal text, and it's not large/bold text that would qualify for the
3:1 exception.

**Fix applied** (`explorer/src/app/features/graph/graph.component.css`):
replaced the opacity dim with an explicit muted-but-compliant treatment —
`color: var(--atlas-color-text-muted)` (already verified ~6.8:1 against
background elsewhere in this table), a plain `background:
var(--atlas-color-bg)`, and a `dashed` border for an additional
non-color visual cue that the chip is in its "off" state. This keeps the
same at-a-glance distinction between active/inactive chips without
dropping text below AA contrast, and without depending on color alone
(the dashed border plus `aria-pressed` already carry the state
non-visually and non-chromatically).

Audited but intentionally left alone: `.mini:disabled` (`graph-table.component.ts`)
and `.action:disabled` (`graph-inspector.component.ts`) also use
`opacity`, but both are genuinely `disabled` HTML buttons, which WCAG
2.2 SC 1.4.3 exempts ("Text or images of text that are part of an
inactive user interface component ... have no contrast requirement").

### 7. Not audited in this pass (scope/time)
- No live contrast measurement (e.g. via browser devtools or axe) — hand
  computation only, cross-check recommended before sign-off.
- Dark theme contrast was not hand-computed exhaustively; spot checks on
  `--atlas-evidence-*` dark values suggest comparable or better ratios
  (light text on dark saturated backgrounds), but this is not a
  substitute for measurement.
- Full keyboard-trap / tab-order walkthrough of the graph canvas +
  inspector + table in a real browser was not performed.
- No screen-reader pass (NVDA/VoiceOver) was performed.

## Test suite impact
No existing spec files were modified for a11y; the only code change is
the CSS fix in section 6 above, which does not alter any component's
public API, template structure, or `aria-*` attributes — existing specs
for `graph.component`/`graph-table.component` should be unaffected.
**Not verified by running the test suite in this session (no shell
access)** — see `docs/cycle-6-benchmarks.md` section 4.
