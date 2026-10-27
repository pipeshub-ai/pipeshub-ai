import { useEffect, useLayoutEffect, useRef, type RefObject } from 'react';

const DURATION_MS = 240;
const EASING = 'cubic-bezier(0.22, 1, 0.36, 1)';

/**
 * Grows or shrinks the composer box from its previous size to its new one
 * whenever `shape` changes, the same way in both directions. Size changes that
 * keep the shape (a new line while typing) are left alone.
 */
export function useComposerResizeAnimation(ref: RefObject<HTMLElement | null>, shape: string) {
  const lastSize = useRef<{ w: number; h: number } | null>(null);

  useLayoutEffect(() => {
    const el = ref.current;
    const prev = lastSize.current;
    if (!el || !prev || typeof el.animate !== 'function') return;
    const next = el.getBoundingClientRect();
    if (Math.abs(next.width - prev.w) < 1 && Math.abs(next.height - prev.h) < 1) return;
    if (window.matchMedia?.('(prefers-reduced-motion: reduce)').matches) return;
    el.animate(
      [
        { width: `${prev.w}px`, height: `${prev.h}px` },
        { width: `${next.width}px`, height: `${next.height}px` },
      ],
      { duration: DURATION_MS, easing: EASING },
    );
  }, [ref, shape]);

  // Tracks the size between shape changes, including mid-animation, so the next
  // change starts from wherever the box actually is.
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const record = () => {
      if (!el.isConnected) return;
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0) lastSize.current = { w: r.width, h: r.height };
    };
    record();
    const observer = new ResizeObserver(record);
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref, shape]);
}
