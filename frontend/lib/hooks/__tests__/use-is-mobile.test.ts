import { describe, it, expect, afterEach, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useIsMobile, useIsBelowDesktop } from '../use-is-mobile';

/** Values returned by every render, so the first one is observable. */
function renders(hook: () => boolean) {
  const seen: boolean[] = [];
  renderHook(() => {
    const v = hook();
    seen.push(v);
    return v;
  });
  return seen;
}

function stubMatchMedia(matches: (q: string) => boolean) {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: matches(q),
    media: q,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }));
}

afterEach(() => vi.unstubAllGlobals());

describe('useIsMobile', () => {
  it('is true on the very first render when the query matches', () => {
    stubMatchMedia((q) => q === '(max-width: 768px)');
    const seen = renders(useIsMobile);
    expect(seen[0]).toBe(true);
  });

  it('is false when the query does not match', () => {
    stubMatchMedia(() => false);
    const seen = renders(useIsMobile);
    expect(seen[0]).toBe(false);
  });
});

describe('useIsBelowDesktop', () => {
  it('uses the 1023px query on the first render', () => {
    stubMatchMedia((q) => q === '(max-width: 1023px)');
    const seen = renders(useIsBelowDesktop);
    expect(seen[0]).toBe(true);
  });
});
