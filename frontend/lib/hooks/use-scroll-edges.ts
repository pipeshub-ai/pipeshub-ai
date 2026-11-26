'use client';

import type React from 'react';
import { useCallback, useEffect, useState } from 'react';

export interface ScrollEdges {
  /** Content is hidden past the start (left) edge. */
  start: boolean;
  /** Content is hidden past the end (right) edge. */
  end: boolean;
}

/** Which sides of a horizontal scroller hide content, from its scroll box. */
export function scrollEdgesOf(el: Pick<HTMLElement, 'scrollLeft' | 'clientWidth' | 'scrollWidth'>): ScrollEdges {
  return { start: el.scrollLeft > 0, end: el.scrollLeft + el.clientWidth < el.scrollWidth - 1 };
}

/**
 * Tracks {@link ScrollEdges} of a horizontal scroller as it scrolls, resizes or its children change. `remount` names
 * what decides whether the scroller is rendered at all, so a scroller that mounts later is still watched.
 */
export function useScrollEdges(ref: React.RefObject<HTMLElement | null>, remount?: unknown): ScrollEdges {
  const [edges, setEdges] = useState<ScrollEdges>({ start: false, end: false });
  const update = useCallback(() => {
    const el = ref.current;
    if (!el) return;
    const next = scrollEdgesOf(el);
    setEdges((prev) => (prev.start === next.start && prev.end === next.end ? prev : next));
  }, [ref]);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    update();
    el.addEventListener('scroll', update, { passive: true });
    // jsdom and old browsers have no ResizeObserver; scrolling and child changes still update.
    const resize = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update);
    resize?.observe(el);
    const children = new MutationObserver(update);
    children.observe(el, { childList: true, subtree: true });
    return () => {
      el.removeEventListener('scroll', update);
      resize?.disconnect();
      children.disconnect();
    };
  }, [ref, update, remount]);

  return edges;
}

/**
 * Fades a scroller's edge where more content is hidden, so a row cut at the screen's edge reads as one that scrolls
 * (a hidden scrollbar gives no other sign).
 */
export function scrollEdgeFade(edges: ScrollEdges, px = 32): React.CSSProperties {
  if (!edges.start && !edges.end) return {};
  const mask = `linear-gradient(to right, ${edges.start ? `transparent, #000 ${px}px` : '#000'}, ${
    edges.end ? `#000 calc(100% - ${px}px), transparent` : '#000'
  })`;
  return { maskImage: mask, WebkitMaskImage: mask };
}
