import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { contrastRatio, WCAG_AA_NORMAL_TEXT } from '@/lib/utils/contrast';

const css = readFileSync(path.join(__dirname, '..', 'globals.css'), 'utf8');

function block(selectorStart: string): string {
  const i = css.indexOf(selectorStart);
  expect(i).toBeGreaterThan(-1);
  return css.slice(i, css.indexOf('}', i));
}

function token(scope: string, name: string): string {
  const m = new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`).exec(scope);
  expect(m, `${name} in scope`).not.toBeNull();
  return m![1];
}

const light = block(':root, .light, .light-theme');
const dark = block('.dark, .dark-theme');
const white = '#ffffff';

describe('jade accent contrast', () => {
  it('maps the whole jade ramp to emerald, on the root, in portals and on a color="jade" control', () => {
    const map = block('.radix-themes[data-accent-color="emerald"],');
    // One rule covers the Theme root, every portal Theme and the bare attribute Radix writes on a control.
    expect(map).toContain('.radix-themes[data-accent-color="jade"],');
    expect(map).toMatch(/\n\[data-accent-color="jade"\] \{/);
    for (let n = 1; n <= 12; n += 1) {
      expect(map).toContain(`--accent-${n}: var(--emerald-${n});`);
      expect(map).toContain(`--accent-a${n}: var(--emerald-a${n});`);
    }
    expect(map).toContain('--accent-contrast: var(--emerald-contrast)');
    expect(map).toContain('--accent-indicator: var(--emerald-indicator)');
    expect(map).toContain('--accent-track: var(--emerald-track)');
    expect(map).toContain('--focus-8: var(--focus-ring)');
    // The old partial remap (steps 9 and 10 only) is gone.
    expect(css.match(/\[data-accent-color="jade"\]/g)).toHaveLength(2);
  });

  it('draws the focus ring and checked indicators from emerald in both modes', () => {
    // Light uses the solid step: emerald-8 is 2.63:1 on white, emerald-9 is 5.48:1.
    expect(light).toContain('--focus-ring: var(--emerald-9);');
    expect(dark).toContain('--focus-ring: var(--emerald-8);');
    for (const scope of [light, dark]) {
      expect(scope).toContain('--emerald-indicator: var(--emerald-9);');
      expect(scope).toContain('--emerald-track: var(--emerald-9);');
    }
  });

  it('pins the token values', () => {
    expect(token(light, '--emerald-9')).toBe('#047857');
    expect(token(light, '--emerald-10')).toBe('#10674C');
    expect(token(dark, '--emerald-9')).toBe('#047857');
    expect(token(dark, '--emerald-10')).toBe('#10674C');
  });

  it.each([
    ['light', light],
    ['dark', dark],
  ])('white on solid and hover accent meets AA in %s mode', (_n, scope) => {
    const solid = contrastRatio(white, token(scope, '--emerald-9'));
    const hover = contrastRatio(white, token(scope, '--emerald-10'));
    expect(solid).toBeGreaterThanOrEqual(WCAG_AA_NORMAL_TEXT);
    expect(hover).toBeGreaterThanOrEqual(WCAG_AA_NORMAL_TEXT);
    expect(solid).toBeCloseTo(5.48, 2);
    expect(hover).toBeCloseTo(6.85, 2);
  });

  it('keeps hover visibly distinct from solid', () => {
    expect(token(light, '--emerald-10')).not.toBe(token(light, '--emerald-9'));
    expect(token(dark, '--emerald-10')).not.toBe(token(dark, '--emerald-9'));
  });
});
