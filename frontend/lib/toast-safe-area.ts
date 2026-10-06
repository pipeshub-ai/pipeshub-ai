'use client';

import { useEffect, type RefObject } from 'react';
import type { ToastPlacement } from '@/lib/store/toast-store';

export const TOAST_SAFE_BOTTOM_VAR = '--ph-toast-safe-bottom';
export const TOAST_SAFE_RIGHT_VAR = '--ph-toast-safe-right';
export const TOAST_SAFE_TOP_VAR = '--ph-toast-safe-top';

/** At or below this viewport width toasts move to the top edge. */
export const TOAST_MOBILE_MAX_WIDTH = 640;
export const TOAST_MOBILE_QUERY = `(max-width: ${TOAST_MOBILE_MAX_WIDTH}px)`;

/** 37.5rem drawer + its 10px right margin. */
export const DRAWER_TOAST_INSET_PX = 610;

/** Full-screen drawers on phones: keep top toasts below the drawer header (title and close button). */
export const MOBILE_DRAWER_HEADER_INSET_PX = 40;

const TOAST_MIN_WIDTH_AFTER_SHIFT = 440;
const COMPOSER_GAP = 8;

export function resolveToastPlacement(
  requested: ToastPlacement | undefined,
  isMobile: boolean,
): ToastPlacement {
  if (isMobile) return 'top';
  return requested === 'top' ? 'top' : 'bottom';
}

// Several publishers can be live at once (composer + drawer): the variable
// carries the largest active contribution per property.
const contributions: Record<string, Map<symbol, number>> = {};

function apply(variable: string) {
  if (typeof document === 'undefined') return;
  const values = [...(contributions[variable]?.values() ?? [])];
  const max = values.length ? Math.max(...values) : 0;
  if (max > 0) {
    document.documentElement.style.setProperty(variable, `${Math.round(max)}px`);
  } else {
    document.documentElement.style.removeProperty(variable);
  }
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
 * Space the bottom toast stack must clear so it does not cover the composer:
 * the distance from the viewport bottom to the composer's top edge. A composer
 * in the upper half (centered empty state) cannot collide with the stack.
 */
export function composerBottomInset(rect: { top: number }, viewportHeight: number): number {
  if (rect.top <= viewportHeight / 2) return 0;
  return Math.max(0, viewportHeight - rect.top + COMPOSER_GAP);
}

/** Publishes `el`'s bottom inset and keeps it current until the returned function runs. */
function watchBottomInset(el: HTMLElement): () => void {
  let withdraw: (() => void) | null = null;
  let frame = 0;
  const measure = () => {
    withdraw?.();
    withdraw = publishToastInset(
      TOAST_SAFE_BOTTOM_VAR,
      composerBottomInset(el.getBoundingClientRect(), window.innerHeight),
    );
  };
  // Layout moves (centered -> docked) do not always resize the element.
  const schedule = () => {
    cancelAnimationFrame(frame);
    frame = requestAnimationFrame(measure);
  };
  measure();
  const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(schedule);
  observer?.observe(el);
  window.addEventListener('resize', schedule);
  window.addEventListener('transitionend', schedule, true);
  window.addEventListener('animationend', schedule, true);
  return () => {
    cancelAnimationFrame(frame);
    observer?.disconnect();
    window.removeEventListener('resize', schedule);
    window.removeEventListener('transitionend', schedule, true);
    window.removeEventListener('animationend', schedule, true);
    withdraw?.();
  };
}

/** Keeps toasts clear of the chat composer while it is mounted. */
export function useToastComposerInset(ref: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    const el = ref.current;
    return el ? watchBottomInset(el) : undefined;
  }, [ref]);
}

/**
 * Same as `useToastComposerInset` for an element that mounts and unmounts on its own (a popover, a
 * banner): pass it from a callback ref, or null while it is not shown.
 */
export function useToastElementInset(el: HTMLElement | null): void {
  useEffect(() => (el ? watchBottomInset(el) : undefined), [el]);
}

/**
 * Keeps toasts off an open right-side drawer `widthPx` wide: left of it on
 * desktop (skipped when no room is left for a toast), below its header on
 * phones where the drawer is full screen and toasts sit at the top.
 */
export function useToastDrawerInset(open: boolean, widthPx: number): void {
  useEffect(() => {
    if (!open || typeof window === 'undefined') return;
    let withdraw: (() => void) | null = null;
    const sync = () => {
      withdraw?.();
      if (window.innerWidth <= TOAST_MOBILE_MAX_WIDTH) {
        withdraw = publishToastInset(TOAST_SAFE_TOP_VAR, MOBILE_DRAWER_HEADER_INSET_PX);
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
