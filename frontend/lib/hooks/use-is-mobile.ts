'use client';

import { useCallback, useSyncExternalStore } from 'react';

export const MOBILE_MAX_PX = 768;
export const BELOW_DESKTOP_MAX_PX = 1023;

const getServerSnapshot = () => false;

/**
 * Tracks a max-width media query. A client-side mount reads matchMedia on its first render (no
 * desktop-first flash); hydration starts from the prerendered desktop value, then switches.
 */
function useMaxWidth(px: number): boolean {
  const query = `(max-width: ${px}px)`;
  const subscribe = useCallback((onChange: () => void) => {
    const mq = window.matchMedia?.(query);
    if (!mq) return () => {};
    mq.addEventListener('change', onChange);
    return () => mq.removeEventListener('change', onChange);
  }, [query]);
  const getSnapshot = useCallback(() => !!window.matchMedia?.(query)?.matches, [query]);
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}

/** True when the viewport width is ≤768px. */
export const useIsMobile = (): boolean => useMaxWidth(MOBILE_MAX_PX);

/** True when the viewport width is ≤1023px (phones and tablets). */
export const useIsBelowDesktop = (): boolean => useMaxWidth(BELOW_DESKTOP_MAX_PX);
