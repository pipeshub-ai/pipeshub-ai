# ADR-UI-05: A hue's text step holds AA on every surface it sits on

- Package: R32-UIFU (STATUS R31UIFU-FU-2).
- Related: R31-AXET (step 9/10 text to step 11, TEXTSTEP-0..5), R31-UIFU (the hue rule, TEXTSTEP-6), QAFINAL-FU-AXE-2.
- Finding it answers: R31-UIFU's TEXTSTEP-6 table: in light, Radix's step 11 of jade, amber, green, blue and orange is
  4.06-4.50:1 on the olive-2/3 panels (WCAG AA for small text is 4.5:1), while every neutral 11 is 5.2:1 or more.

## Context

R31 made step 11 the one text step of every scale. It checked step 11 against white only. The classic UI puts most
text on tinted surfaces: olive-2 154 times and olive-3 75 times as a named background, and olive-4/5 for hover and
selected rows. Radix's neutral 11 holds 4.5:1 down to step 5 (olive-11 on olive-5 is 4.56:1). Its light hue 11 does
not: amber-11 is 4.06:1 on olive-3 and 3.53:1 on olive-5; red-11 is 4.27:1 on olive-4. Radix's own soft Badge and
Callout draw a11 on the hue's a3 tint, and are under AA there too (orange 3.99:1). Measured per use, 787 hue-text
lines in the classic UI were under 4.5:1 on a panel they can sit on, 394 of them on the page or olive-2/3.

The options were:

1. **A second, darker "text on tint" token per hue.** Every use would have to know which panel it sits on. A static
   check cannot know that in general (the panel is often an ancestor in another file), so the rule could not be
   enforced, and two text tokens per hue invite the wrong pick.
2. **Step 12 for small text on tints.** Step 12 is near black (amber-12 is brown, jade-12 a dark green-grey): a warning
   or a success word stops reading as one. It has the same "which panel" problem as option 1.
3. **Change the panels.** Olive-2/3 are the product's surface hierarchy. A lighter panel loses the hierarchy, and
   hover and selected rows (olive-4/5) still fail.

## Decision

D1. **Step 11 is the text step on every surface, so it is raised where it is defined, not per use.** In light,
`frontend/app/globals.css` redefines the step 11 of each hue the UI writes text in whose Radix value misses the rule.
Uses do not change and need not know their panel. This is option 1 with the token placed at step 11 itself.

D2. **The rule is the neutral's own floor.** A hue's step 11, solid and alpha, meets 4.5:1 in both appearances on:
- the page;
- neutral surface steps 1-5 (app, subtle, panel, hover, pressed);
- its own step 3;
- its own a3 tint over the page or a card (olive-2/3), where a soft Badge, Callout or Button sits.

D3. **Recognisable by construction.** Each raised value is Radix's step 11 darkened in OKLCH, with hue and chroma
kept, just far enough to meet D2:
- jade #007258;
- amber #985300;
- green #02734a;
- blue #0064bd;
- orange #b53800;
- red #c31e28;
- sky #006b95.

The hue moves at most 4.5 degrees (orange), and lightness stays between Radix's 11 and 12. The solid and alpha step
are one opaque colour, so Radix's colour props (`color="amber"` reads `--amber-a11`) draw what `var(--amber-11)` draws.
Dark mode keeps Radix's values (7:1 or more on olive-3).

D4. **One test holds it.** `frontend/app/components/ui/__tests__/text-step-contrast.test.ts` derives the hues from the
code: every `var(--hue-(a)11)` that is not a fill, and every `color="hue"` on a Radix part that draws a11.
- TEXTSTEP-6 checks D2 for each of those hues in both appearances.
- TEXTSTEP-6b checks D3: hue within 6 degrees, lightness between 11 and 12, solid equal to alpha.
- TEXTSTEP-7 checks each use against the panel it sits on. That is the background its element, style or CSS rule
  names (the same arm of a ternary); for a soft part, its a3 tint over it. If no background is named, the use is
  checked on every D2 surface.

A new hue used as text that misses the rule fails there until it gets its line in the `globals.css` block.

## As built

- `frontend/app/globals.css`: the block `:root:not(.dark, .dark-theme), .light, .light-theme`. It is light only. It
  comes after Radix's import, so it also wins over Radix's display-p3 values. The selector beats `:root`, so it does not
  leak into dark mode.
- `frontend/app/components/ui/__tests__/text-step-contrast.test.ts`: TEXTSTEP-6, 6b, 7 (light, dark) and 7b (the
  panel finder on a probe). With Radix's values in place of the block, TEXTSTEP-6, 6b and 7 (light) fail, 7 with 787 use lines; they pass after.
- `frontend/CLAUDE.md` (Styling) states the rule.

## Deviations

- The accent named in `frontend/CLAUDE.md` is jade, but the root theme's `data-accent-color="emerald"` maps `--accent-*`
  to the custom emerald scale in `globals.css`. Emerald-11 (#035e44) already clears D2. The test resolves `accent` as
  emerald.
- A soft tint on a hover or pressed row (olive-4/5) is outside D2. The badge's own tint is the surface its text is
  designed for.

## Owner amendments

None.

## Status

**Accepted** 2026-11-12: decided from first principles, long term, best UX.
