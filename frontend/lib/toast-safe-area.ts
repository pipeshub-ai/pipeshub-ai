'use client';

import { useEffect } from 'react';
import type { ToastPlacement } from '@/lib/store/toast-store';

/** Room a toast drawn outside the stack takes in the stack's corner: the bottom stack sits above it, on top of the lift. */
export const TOAST_STACKED_VAR = '--ph-toast-stacked';
export const TOAST_SAFE_RIGHT_VAR = '--ph-toast-safe-right';
export const TOAST_SAFE_TOP_VAR = '--ph-toast-safe-top';

/** At or below this viewport width toasts move to the top edge. */
export const TOAST_MOBILE_MAX_WIDTH = 640;
export const TOAST_MOBILE_QUERY = `(max-width: ${TOAST_MOBILE_MAX_WIDTH}px)`;

/** The stack's distance from the screen's edges above the phone width; on a phone it keeps 16 px. */
export const TOAST_EDGE_PX = 24;

/** 37.5rem drawer + its 10px right margin. */
export const DRAWER_TOAST_INSET_PX = 610;

/** The drawer footer's action row on phones; a bottom toast sits above it instead of covering Share. */
export const MOBILE_DRAWER_FOOTER_INSET_PX = 72;
const TOAST_MIN_WIDTH_AFTER_SHIFT = 440;

/**
 * Full-screen drawers on phones cover the top edge (header, form), so toasts drop to the bottom while one is
 * open. A counter, not a flag: drawers can overlap while one closes.
 */
let compactOverlays = 0;
const overlayListeners = new Set<() => void>();
export const subscribeCompactOverlay = (listener: () => void) => {
  overlayListeners.add(listener);
  return () => void overlayListeners.delete(listener);
};
export const getCompactOverlayOpen = () => compactOverlays > 0;
function holdCompactOverlay(): () => void {
  compactOverlays += 1;
  overlayListeners.forEach((l) => l());
  return () => {
    compactOverlays -= 1;
    overlayListeners.forEach((l) => l());
  };
}

export function resolveToastPlacement(
  requested: ToastPlacement | undefined,
  isMobile: boolean,
  overlayOpen = false,
): ToastPlacement {
  if (isMobile) return overlayOpen ? 'bottom' : 'top';
  return requested === 'top' ? 'top' : 'bottom';
}

// Several publishers can be live at once (a drawer, the room's toast): the variable
// carries the largest active contribution per property.
const contributions: Record<string, Map<symbol, number>> = {};

function apply(variable: string) {
  if (typeof document === 'undefined') return;
  const values = [...(contributions[variable]?.values() ?? [])];
  const max = values.length ? Math.max(...values) : 0;
  const next = max > 0 ? `${Math.round(max)}px` : '';
  const root = document.documentElement.style;
  // Any write to the root's style restyles the whole page, so an unchanged value is not written again.
  if (root.getPropertyValue(variable) === next) return;
  if (next) root.setProperty(variable, next);
  else root.removeProperty(variable);
}

/** Publishes `px` to `variable`; the returned function withdraws it. */
export function publishToastInset(variable: string, px: number): () => void {
  const id = Symbol(variable);
  const map = (contributions[variable] ??= new Map());
  map.set(id, Math.max(0, px));
  apply(variable);
  return () => {
    map.delete(id);
    apply(variable);
  };
}

/**
 * Keeps toasts off an open right-side drawer `widthPx` wide: left of it on
 * desktop (skipped when no room is left for a toast), below its header on
 * phones where the drawer is full screen and toasts move to the bottom.
 */
export function useToastDrawerInset(open: boolean, widthPx: number): void {
  useEffect(() => {
    if (!open || typeof window === 'undefined') return;
    let withdraw: (() => void) | null = null;
    const sync = () => {
      withdraw?.();
      if (window.innerWidth <= TOAST_MOBILE_MAX_WIDTH) {
        withdraw = holdCompactOverlay();
      } else if (window.innerWidth - widthPx >= TOAST_MIN_WIDTH_AFTER_SHIFT) {
        withdraw = publishToastInset(TOAST_SAFE_RIGHT_VAR, widthPx);
      } else {
        withdraw = null;
      }
    };
    sync();
    window.addEventListener('resize', sync);
    return () => {
      window.removeEventListener('resize', sync);
      withdraw?.();
    };
  }, [open, widthPx]);
}
