/**
 * QA-FINAL: the UX captures' axe pass found soft badges and buttons under WCAG AA's 4.5:1 in light (the sidebar's
 * "Beta", the agent builder's "Live" badge, Schedules' "Active", the red "Delete" and "Cancel run"): Radix's soft
 * variant draws accent-11 text on the accent-a3 fill. globals.css gives every soft badge and button but gray's
 * accent-12 in light; this holds that rule and the reason for it against the Radix tokens the app ships.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

const FRONTEND = path.resolve(__dirname, '../../../..');
const RADIX = readFileSync(path.join(FRONTEND, 'node_modules/@radix-ui/themes/styles.css'), 'utf8');
const GLOBALS = readFileSync(path.join(FRONTEND, 'app/globals.css'), 'utf8');

/** A token's sRGB values in the order styles.css declares them: the light theme's, then the dark theme's. */
function token(name: string): { light: string; dark: string } {
  const values = [...RADIX.matchAll(new RegExp(`--${name}: (#[0-9a-f]{6,8});`, 'g'))].map((m) => m[1]);
  expect(values.length, name).toBeGreaterThanOrEqual(2);
  return { light: values[0], dark: values[1] };
}

function rgba(hex: string): [number, number, number, number] {
  const n = hex.slice(1);
  const at = (i: number) => parseInt(n.slice(i, i + 2), 16);
  return [at(0), at(2), at(4), n.length === 8 ? at(6) / 255 : 1];
}

function over(top: string, under: string): [number, number, number] {
  const [r, g, b, a] = rgba(top);
  const [R, G, B] = rgba(under);
  return [r * a + R * (1 - a), g * a + G * (1 - a), b * a + B * (1 - a)];
}

function luminance([r, g, b]: [number, number, number]): number {
  const lin = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
}

function contrast(text: string, fill: [number, number, number]): number {
  const [r, g, b] = rgba(text);
  const [hi, lo] = [luminance([r, g, b]), luminance(fill)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** The hues the app gives soft badges and buttons (gray stays Radix's: gray-11 passes). */
const HUES = ['red', 'amber', 'jade', 'green', 'blue', 'orange', 'violet'] as const;
/** Radix's own accent-11 is under AA in light for these. */
const UNDER_AA = ['amber', 'jade', 'green', 'blue', 'orange'] as const;

describe('soft badges and buttons', () => {
  const page = { light: '#ffffff', dark: token('gray-1').dark };
  const fills = (hue: string) => {
    const fill = token(`${hue}-a3`);
    return { light: over(fill.light, page.light), dark: over(fill.dark, page.dark) };
  };

  it.each(UNDER_AA)("SOFTVAR-1: Radix's own soft %s text is under AA in light, which is why the app overrides it", (hue) => {
    expect(contrast(token(`${hue}-11`).light, fills(hue).light)).toBeLessThan(4.5);
  });

  it.each(HUES)('SOFTVAR-2: %s-12 on the same fill meets AA in light, and Radix\'s %s-11 does in dark', (hue) => {
    expect(contrast(token(`${hue}-12`).light, fills(hue).light)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(token(`${hue}-11`).dark, fills(hue).dark)).toBeGreaterThanOrEqual(4.5);
  });

  it("SOFTVAR-4: Running now's red Cancel on the settings card (olive-2): Radix's red-11 is under AA in light, red-12 passes", () => {
    // R30-LAT-FU-3 measured #cd282d on #f8e6e5, 4.42:1, before SOFTVAR-3's rule reached the button.
    const card = token('olive-2');
    const fill = { light: over(token('red-a3').light, card.light), dark: over(token('red-a3').dark, card.dark) };
    expect(contrast(token('red-11').light, fill.light)).toBeLessThan(4.5);
    expect(contrast(token('red-12').light, fill.light)).toBeGreaterThanOrEqual(4.5);
    expect(contrast(token('red-11').dark, fill.dark)).toBeGreaterThanOrEqual(4.5);
  });

  it('SOFTVAR-3:globals.css gives every soft badge and button but gray accent-12 in light, never when disabled', () => {
    expect(GLOBALS).toMatch(
      /:is\(\.light, \.light-theme\) :is\(\.rt-Badge, \.rt-Button\)\.rt-variant-soft:not\(\[data-accent-color='gray'\]\):not\(\[data-disabled\]\):not\(:disabled\) \{\s*color: var\(--accent-12\);/,
    );
  });
});
