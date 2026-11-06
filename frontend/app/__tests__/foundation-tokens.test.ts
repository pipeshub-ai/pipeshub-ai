import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const css = readFileSync(path.join(__dirname, '..', 'globals.css'), 'utf8');

function block(selectorStart: string): string {
  const i = css.indexOf(selectorStart);
  expect(i, selectorStart).toBeGreaterThan(-1);
  return css.slice(i, css.indexOf('}', i));
}

const ROLES: Record<string, string> = {
  '--text-primary': 'var(--gray-12)',
  '--text-secondary': 'var(--gray-11)',
  '--text-tertiary': 'var(--gray-a10)',
  '--text-placeholder': 'var(--gray-a9)',
  '--surface-raised': 'var(--gray-1)',
  '--surface-subtle': 'var(--gray-2)',
  '--fill-muted': 'var(--gray-3)',
  '--fill-hover': 'var(--gray-4)',
  '--fill-active': 'var(--gray-5)',
  '--fill-ghost-hover': 'var(--gray-a3)',
  '--border-subtle': 'var(--gray-3)',
  '--border-default': 'var(--gray-4)',
  '--border-strong': 'var(--gray-5)',
};

describe('foundation tokens', () => {
  it('aliases the theme gray to olive outside any Theme, in both modes', () => {
    const alias = block(':root, .light, .light-theme, .dark, .dark-theme {');
    for (let n = 1; n <= 12; n += 1) {
      expect(alias).toContain(`--gray-${n}: var(--olive-${n});`);
      expect(alias).toContain(`--gray-a${n}: var(--olive-a${n});`);
    }
  });

  it('defines the thirteen colour roles on :root and on every Theme', () => {
    const roles = block(':root,\n.radix-themes {');
    for (const [role, step] of Object.entries(ROLES)) expect(roles).toContain(`${role}: ${step};`);
  });

  it('points the legacy --neutral-* names at the theme gray, with no slate value left', () => {
    const roles = block(':root,\n.radix-themes {');
    for (let n = 1; n <= 12; n += 1) {
      expect(roles).toContain(`--neutral-${n}: var(--gray-${n});`);
      expect(roles).toContain(`--neutral-a${n}: var(--gray-a${n});`);
    }
    expect(css).not.toMatch(/--neutral-[a0-9]+:\s*#/);
  });

  it('derives the translucent glass from the theme gray', () => {
    expect(block(':root, .light, .light-theme')).toContain(
      '--effects-translucent: color-mix(in oklab, var(--gray-1) 80%, transparent);',
    );
    expect(block('.dark, .dark-theme')).toContain(
      '--effects-translucent: color-mix(in oklab, var(--gray-2) 70%, transparent);',
    );
  });

  it('has the motion roles, zeroed under reduced motion', () => {
    const motion = block(':root {\n  --motion-micro');
    expect(motion).toContain('--motion-micro: 120ms;');
    expect(motion).toContain('--motion-control: 180ms;');
    expect(motion).toContain('--motion-surface: 240ms;');
    expect(motion).toContain('--motion-page: 320ms;');
    expect(motion).toContain('--motion-ease: cubic-bezier(0.25, 0.1, 0.25, 1);');
    expect(css).toMatch(
      /@media \(prefers-reduced-motion: reduce\) \{\s*:root \{\s*--motion-micro: 0ms;\s*--motion-control: 0ms;\s*--motion-surface: 0ms;\s*--motion-page: 0ms;/,
    );
  });

  it('hides scrollbars everywhere except the escape hatches, and keeps no per-surface bars', () => {
    expect(css).toContain(':where(*:not(.keep-scrollbar):not([data-table-scroll-area]))::-webkit-scrollbar {\n  display: none;');
    expect(css).toMatch(/:where\(\*:not\(\.keep-scrollbar\):not\(\[data-table-scroll-area\]\)\) \{[^}]*scrollbar-width: none;/);
    expect(css).toContain('.rt-ScrollAreaScrollbar:not(.keep-scrollbar .rt-ScrollAreaScrollbar) {\n  display: none !important;');
    for (const gone of ['.no-scrollbar {', '[data-upload-scroll-area]', '.workspace-menu-scroll', '.team-member-scroll-area', '.chat-message-scroll', '.response-tabs-scroll', '.file-preview-scroll-area', 'scrollbar-gutter']) {
      expect(css, gone).not.toContain(gone);
    }
  });

  it('paints the selected segment as the sliding indicator, without !important', () => {
    expect(css).not.toContain('--segment-on');
    expect(block('.radix-themes .rt-SegmentedControlRoot {')).toContain('background-color: var(--fill-ghost-hover);');
    expect(block('.dark .rt-SegmentedControlRoot,')).toContain('linear-gradient(var(--black-a5), var(--black-a5))');
    expect(block('.radix-themes .rt-SegmentedControlIndicator::before {')).toContain('border: 1px solid var(--gray-a4);');
    const darkIndicator = block('.dark .rt-SegmentedControlIndicator::before,');
    expect(darkIndicator).toContain('background-color: var(--fill-muted);');
    expect(darkIndicator).toContain('border-color: var(--border-strong);');
  });

  it('draws icons with Material Symbols defaults and no !important', () => {
    const icon = block('.material-icons-outlined {');
    expect(icon).toContain("font-family: 'Material Symbols Outlined';");
    expect(icon).toContain("font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 20;");
    expect(icon).not.toContain('!important');
  });

  it('stops the shimmer loops under reduced motion', () => {
    expect(block('.skeleton-shimmer {')).toContain('animation: shimmer-sweep 1.5s linear infinite;');
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\) \{\s*\.skeleton-shimmer,[^}]*animation: none !important;/);
  });

  it('names no raw slate or neutral step outside the token definitions', () => {
    expect(css).not.toMatch(/var\(--(slate|neutral)-/);
    const olive = css.match(/var\(--olive-[a0-9]+\)/g) ?? [];
    // Only the 24 lines of the gray alias.
    expect(olive).toHaveLength(24);
  });
});
