'use client';

import { useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react';

/** Every toast sits this far from the screen's bottom and right edges (or the device's safe area, if larger). */
export const TOAST_EDGE = 'var(--space-4)';

/** What sits under a toast in the corner: the cards' measured heights, and a --space-3 gap above each. */
export interface CornerStack {
  px: number;
  gaps: number;
}

const EMPTY: CornerStack = { px: 0, gaps: 0 };

/** The bottom edge of a toast with `below` under it in the corner. */
export function toastBottom(below: CornerStack = EMPTY): string {
  const edge = `max(${TOAST_EDGE}, env(safe-area-inset-bottom, 0px))`;
  return below.gaps > 0 ? `calc(${edge} + ${below.px}px + ${below.gaps} * var(--space-3))` : edge;
}

/** The right edge every toast shares. */
export const TOAST_RIGHT = `max(${TOAST_EDGE}, env(safe-area-inset-right, 0px))`;

/*
 * Toast cards drawn outside the app's toast store share its bottom-right corner. While one shows it is registered here
 * with its height; they stack upward from the corner in the order they arrived, and the app's own toast sits above
 * them all, so no toast covers another.
 */
type Entries = ReadonlyArray<readonly [symbol, number]>;
const cards = new Map<symbol, number>();
const listeners = new Set<() => void>();
let entries: Entries = [];

function emit(): void {
  entries = [...cards];
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

const snapshot = () => entries;

/** The cards before `id` in the corner, or every card when `id` is not given. */
function stackBefore(list: Entries, id?: symbol): CornerStack {
  let px = 0;
  let gaps = 0;
  for (const [key, height] of list) {
    if (key === id) break;
    px += height;
    gaps += 1;
  }
  return gaps ? { px, gaps } : EMPTY;
}

/** What the app's toast sits above: every card registered in the corner. */
export function useToastCornerStack(): CornerStack {
  return stackBefore(useSyncExternalStore(subscribe, snapshot, snapshot));
}

/**
 * Puts `card` in the corner's stack while it is set (pass null once it hides) and returns what it sits above: the
 * cards that came before it. A card on its way out keeps the place it had.
 */
export function useToastCornerCard(card: HTMLElement | null): CornerStack {
  const [id] = useState(() => Symbol('toast-corner-card'));
  const list = useSyncExternalStore(subscribe, snapshot, snapshot);
  const last = useRef(EMPTY);
  useLayoutEffect(() => {
    if (!card) return undefined;
    const measure = () => {
      const height = card.offsetHeight;
      if (cards.get(id) === height) return;
      cards.set(id, height);
      emit();
    };
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(card);
    return () => {
      observer?.disconnect();
      cards.delete(id);
      emit();
    };
  }, [card, id]);
  if (list.some(([key]) => key === id)) last.current = stackBefore(list, id);
  return last.current;
}
