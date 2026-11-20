'use client';

import { useEffect, useRef, useState } from 'react';

/** A load faster than this shows no skeleton at all, so a fast network never flashes one. */
export const LOADING_SHOW_DELAY_MS = 150;
/** Once shown, a skeleton stays at least this long, so it never blinks. */
export const LOADING_MIN_VISIBLE_MS = 300;
/** The skeleton's exit cross-fade; the same length as the --motion-control role. */
export const LOADING_EXIT_MS = 180;

export interface DeferredLoading {
  /** Render the skeleton. */
  visible: boolean;
  /** The skeleton is fading out (the real rows may already render under it). */
  exiting: boolean;
}

interface DeferredLoadingOptions {
  delayMs?: number;
  minVisibleMs?: number;
  exitMs?: number;
}

const prefersReducedMotion = () =>
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

/**
 * When to show a loading skeleton for `isLoading`: not before the show delay,
 * then for at least the minimum time, then one exit cross-fade. Under reduced
 * motion the exit is immediate.
 */
export function useDeferredLoading(
  isLoading: boolean,
  { delayMs = LOADING_SHOW_DELAY_MS, minVisibleMs = LOADING_MIN_VISIBLE_MS, exitMs = LOADING_EXIT_MS }: DeferredLoadingOptions = {},
): DeferredLoading {
  const [state, setState] = useState<DeferredLoading>({ visible: false, exiting: false });
  const shownAt = useRef<number | null>(null);

  useEffect(() => {
    const timers: ReturnType<typeof setTimeout>[] = [];
    if (isLoading) {
      if (shownAt.current !== null) {
        // Loading again while still on screen (or fading): keep it up.
        setState({ visible: true, exiting: false });
      } else {
        timers.push(
          setTimeout(() => {
            shownAt.current = Date.now();
            setState({ visible: true, exiting: false });
          }, delayMs),
        );
      }
    } else if (shownAt.current !== null) {
      const held = Math.max(0, minVisibleMs - (Date.now() - shownAt.current));
      const fade = prefersReducedMotion() ? 0 : exitMs;
      timers.push(
        setTimeout(() => {
          setState({ visible: true, exiting: true });
          timers.push(
            setTimeout(() => {
              shownAt.current = null;
              setState({ visible: false, exiting: false });
            }, fade),
          );
        }, held),
      );
    } else {
      setState({ visible: false, exiting: false });
    }
    return () => timers.forEach(clearTimeout);
  }, [isLoading, delayMs, minVisibleMs, exitMs]);

  return state;
}
