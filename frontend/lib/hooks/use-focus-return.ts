'use client';

import { useCallback, useRef } from 'react';

export interface UseFocusReturnOptions {
  /**
   * Where focus goes when the element that opened the dialog is gone (its row
   * was deleted, its menu closed). Called at close time. Return null to let
   * Radix keep its default.
   */
  fallback?: () => HTMLElement | null;
}

export interface FocusReturnHandlers {
  /** Pass to `onOpenAutoFocus`. Records the opener; never moves focus itself. */
  onOpenAutoFocus: (event: Event) => void;
  /** Pass to `onCloseAutoFocus`. Focuses the opener, else the fallback, else does nothing. */
  onCloseAutoFocus: (event: Event) => void;
}

/**
 * Returns focus to whatever opened a Radix dialog when it closes.
 *
 * Radix already returns focus to the element focused before the dialog
 * opened, but when that element is a menu item, or a row that the dialog's
 * action removed, focus falls to the page body. This hook:
 * - records the opener when the dialog opens; an item inside a menu is
 *   replaced by the menu's trigger, which outlives the menu;
 * - on close, focuses the opener if it can still take focus, else the
 *   fallback, else leaves Radix's default alone.
 *
 * Both handlers keep the same identity across renders.
 */
export function useFocusReturn(options: UseFocusReturnOptions = {}): FocusReturnHandlers {
  const openerRef = useRef<HTMLElement | null>(null);
  const fallbackRef = useRef(options.fallback);
  fallbackRef.current = options.fallback;

  const onOpenAutoFocus = useCallback(() => {
    openerRef.current = resolveOpener(typeof document === 'undefined' ? null : document.activeElement);
  }, []);

  const onCloseAutoFocus = useCallback((event: Event) => {
    const opener = openerRef.current;
    openerRef.current = null;
    const target = isUsable(opener) ? opener : pickFallback(fallbackRef.current);
    if (!target) return;
    event.preventDefault();
    target.focus();
  }, []);

  return { onOpenAutoFocus, onCloseAutoFocus };
}

function pickFallback(fallback: UseFocusReturnOptions['fallback']): HTMLElement | null {
  const el = fallback?.() ?? null;
  return isUsable(el) ? el : null;
}

/** The element to return to: a menu item is swapped for its menu's trigger. */
function resolveOpener(active: Element | null): HTMLElement | null {
  if (!(active instanceof HTMLElement) || active === active.ownerDocument.body) return null;
  const menu = active.closest('[role="menu"]');
  const labelledBy = menu?.getAttribute('aria-labelledby');
  if (labelledBy) {
    for (const id of labelledBy.split(/\s+/)) {
      const trigger = id ? active.ownerDocument.getElementById(id) : null;
      if (trigger instanceof HTMLElement) return trigger;
    }
  }
  return active;
}

/** Still in the page, enabled, not inert and rendered. */
function isUsable(el: HTMLElement | null): el is HTMLElement {
  if (!el || !el.isConnected) return false;
  if (el.matches(':disabled')) return false;
  if (el.closest('[inert]')) return false;
  return el.getClientRects().length > 0;
}
