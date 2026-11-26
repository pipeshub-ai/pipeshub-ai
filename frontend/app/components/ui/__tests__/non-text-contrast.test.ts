/**
 * QAFINAL-FU-AXE-2: WCAG 1.4.11 (non-text contrast, 3:1), which axe does not check. In light, an off switch's
 * track edge was 1.3:1 against its surface, and the frosted dialog and bottom sheet composited to grey over their
 * scrim. This computes both from the tokens the app ships, and holds the rules in globals.css and the sheet.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const FRONTEND = path.resolve(__dirname, '../../../..');
const RADIX = readFileSync(path.join(FRONTEND, 'node_modules/@radix-ui/themes/styles.css'), 'utf8');
const GLOBALS = readFileSync(path.join(FRONTEND, 'app/globals.css'), 'utf8');
const SHEET = readFileSync(path.join(FRONTEND, 'app/components/ui/mobile-bottom-sheet.tsx'), 'utf8');

type RGB = [number, number, number];

/** A Radix token's light and dark sRGB hex, in the order styles.css declares them. */
function radix(name: string): { light: string; dark: string } {
  const values = [...RADIX.matchAll(new RegExp(`--${name}: (#[0-9a-f]{6,8});`, 'g'))].map((m) => m[1]);
  expect(values.length, name).toBeGreaterThanOrEqual(2);
  return { light: values[0], dark: values[1] };
}

/** A token globals.css sets in its light block (`:root, .light, .light-theme`). */
function lightGlobal(name: string): string {
  const block = GLOBALS.slice(GLOBALS.indexOf(':root, .light, .light-theme {'));
  const m = block.match(new RegExp(`\\n\\s*--${name}: ((?:#|rgba\\()[^;]+);`));
  expect(m, name).toBeTruthy();
  return m![1];
}

function parse(color: string): [number, number, number, number] {
  const rgba = color.match(/rgba\((\d+),\s*(\d+),\s*(\d+),\s*([\d.]+)\)/);
  if (rgba) return [+rgba[1], +rgba[2], +rgba[3], +rgba[4]];
  const n = color.slice(1);
  const at = (i: number) => parseInt(n.slice(i, i + 2), 16);
  return [at(0), at(2), at(4), n.length === 8 ? at(6) / 255 : 1];
}

function over(top: string, under: RGB): RGB {
  const [r, g, b, a] = parse(top);
  return [r * a + under[0] * (1 - a), g * a + under[1] * (1 - a), b * a + under[2] * (1 - a)];
}

function luminance([r, g, b]: RGB): number {
  const lin = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

function contrast(a: RGB, b: RGB): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const WHITE: RGB = [255, 255, 255];
// The theme's gray is olive (theme-provider.tsx), so --gray-* is olive's scale.
const gray = (step: string) => radix(`olive-${step}`);
const CARD = over(gray('2').light, WHITE);

describe('non-text contrast in light (WCAG 1.4.11)', () => {
  it("NONTEXT-1: Radix's off-switch edge (gray-a5) is under 3:1 on white, which is why the app overrides it", () => {
    expect(contrast(over(gray('a5').light, WHITE), WHITE)).toBeLessThan(3);
  });

  it('NONTEXT-2: the gray-a9 edge the app gives an enabled off switch is 3:1 or more on white and on a gray-2 card', () => {
    expect(contrast(over(gray('a9').light, WHITE), WHITE)).toBeGreaterThanOrEqual(3);
    expect(contrast(over(gray('a9').light, CARD), CARD)).toBeGreaterThanOrEqual(3);
    expect(GLOBALS).toMatch(
      /:is\(\.light, \.light-theme\) \.rt-SwitchRoot\.rt-variant-surface\[data-state='unchecked'\]:not\(\[data-disabled\]\)::before \{\s*box-shadow: inset 0 0 0 1px var\(--gray-a9\);/,
    );
  });

  it('NONTEXT-3: the old frosted dialog over its scrim was grey; the opaque surface keeps gray-11 lines at full contrast', () => {
    const scrimmed = over(lightGlobal('frost-scrim'), WHITE);
    const frosted = over(lightGlobal('frost-bg-strong'), scrimmed);
    const solid = WHITE;
    const text = (bg: RGB) => contrast(over(gray('11').light, bg), bg);
    expect(text(frosted)).toBeLessThan(text(solid) - 1);
    expect(contrast(frosted, WHITE)).toBeGreaterThan(1.2);
    expect(text(solid)).toBeGreaterThanOrEqual(4.5);
    const dialog = GLOBALS.match(/\.rt-BaseDialogContent\.rt-BaseDialogContent \{([^}]*)\}/);
    expect(dialog?.[1]).toMatch(/background-color: var\(--color-panel-solid\);/);
    expect(dialog?.[1]).not.toMatch(/backdrop-filter|frost-bg/);
  });

  it('NONTEXT-4: the bottom sheet is opaque over its 60% black scrim, where frost had read grey', () => {
    const scrimmed = over('rgba(0, 0, 0, 0.6)', WHITE);
    const frosted = over(lightGlobal('frost-bg'), scrimmed);
    expect(contrast(over(gray('11').light, frosted), frosted)).toBeLessThan(4.5);
    expect(SHEET).toContain("backgroundColor: 'var(--color-panel-solid)'");
    expect(SHEET).not.toMatch(/frost-bg|frost-filter/);
    expect(RADIX).toMatch(/--color-panel-solid: white;/);
  });
});
