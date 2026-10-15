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
  it('maps jade --accent-9/10 to the emerald brand steps', () => {
    const jade = block('.radix-themes[data-accent-color="jade"]');
    expect(jade).toContain('--accent-9: var(--emerald-9)');
    expect(jade).toContain('--accent-10: var(--emerald-10)');
    expect(jade).toContain('--accent-contrast: var(--emerald-contrast)');
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
