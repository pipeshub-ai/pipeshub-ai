import { describe, it, expect } from 'vitest';
import { overlayWidth, touchTarget, PAGE_PADDING_X, pagePaddingX } from '../responsive';
import { pageInlinePadding } from '@/app/(main)/workspace/components/settings-page-container';

describe('responsive helpers', () => {
  it('overlayWidth caps at the viewport minus gutters', () => {
    expect(overlayWidth(600)).toBe('min(600px, calc(100vw - 32px))');
  });

  it('touchTarget is undefined when disabled and 44px when enabled', () => {
    expect(touchTarget(false)).toBeUndefined();
    expect(touchTarget(true)).toEqual({ minWidth: 44, minHeight: 44, boxSizing: 'border-box' });
  });

  it('exposes the page padding token', () => {
    expect(PAGE_PADDING_X).toBe('clamp(var(--space-4), 4vw, 100px)');
  });

  it('pagePaddingX is fluid below 1024px and fixed from there up', () => {
    expect(pagePaddingX(40)).toBe(
      'max(clamp(var(--space-4), 4vw, 40px), min(40px, calc((100vw - 1023px) * 1000)))',
    );
  });

  it('pageInlinePadding keeps the page gutter and centres content capped at maxWidth', () => {
    expect(pageInlinePadding(960)).toBe(`max(${PAGE_PADDING_X}, calc((100% - 960px) / 2))`);
    expect(pageInlinePadding(1280)).toContain('calc((100% - 1280px) / 2)');
  });
});
