'use client';

import { useState, useEffect } from 'react';

/** Matches Radix Themes v3 — https://www.radix-ui.com/themes/docs/theme/breakpoints */
export const BREAKPOINT_MIN_PX = {
  xs: 520,
  sm: 768,
  md: 1024,
  lg: 1280,
  xl: 1640,
} as const;

export type Breakpoint = 'initial' | 'xs' | 'sm' | 'md' | 'lg' | 'xl';

export function getBreakpoint(width: number): Breakpoint {
  if (width >= BREAKPOINT_MIN_PX.xl) return 'xl';
  if (width >= BREAKPOINT_MIN_PX.lg) return 'lg';
  if (width >= BREAKPOINT_MIN_PX.md) return 'md';
  if (width >= BREAKPOINT_MIN_PX.sm) return 'sm';
  if (width >= BREAKPOINT_MIN_PX.xs) return 'xs';
  return 'initial';
}

/**
 * Current viewport tier (Radix Themes breakpoints, min-width based).
 * First paint: `'initial'` (mobile-first) until measured after mount.
 */
export function useBreakpoint(): Breakpoint {
  const [bp, setBp] = useState<Breakpoint>('initial');

  useEffect(() => {
    const sync = () => setBp(getBreakpoint(window.innerWidth));
    sync();
    window.addEventListener('resize', sync);
    return () => window.removeEventListener('resize', sync);
  }, []);

  return bp;
}

/**
 * Side-by-side auth hero + form from Radix `md` (1024px) upward.
 *
 * Important: initialize from `window.innerWidth` on the client so the first paint
 * matches the real viewport. Defaulting to `false` left AuthHero + loading form both
 * null (narrow + step=loading), producing a blank white screen — especially visible
 * after logout → /login in the Electron app.
 */
export function useAuthWideLayout(): boolean {
  const [wide, setWide] = useState(() =>
    typeof window !== 'undefined' && window.innerWidth >= BREAKPOINT_MIN_PX.md
  );

  useEffect(() => {
    const sync = () => setWide(window.innerWidth >= BREAKPOINT_MIN_PX.md);
    sync();
    window.addEventListener('resize', sync);
    return () => window.removeEventListener('resize', sync);
  }, []);

  return wide;
}
