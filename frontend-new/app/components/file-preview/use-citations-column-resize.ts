'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  CITATIONS_MAX_PX,
  CITATIONS_MIN_PX,
  CITATIONS_WIDTH_BROADCAST,
  CITATIONS_WIDTH_LS_KEY,
  clamp,
  readSavedCitationsWidthPx,
} from './resize-storage';

/** Shared state + pointer drag handler for the citations column (sidebar + fullscreen). */
export function useCitationsColumnResize() {
  const [citationsWidthPx, setCitationsWidthPx] = useState(() => readSavedCitationsWidthPx());

  // Other surfaces (e.g. sidebar ↔ fullscreen) and other tabs: stay aligned with `localStorage`.
  useEffect(() => {
    const apply = (w: number) => {
      setCitationsWidthPx(clamp(w, CITATIONS_MIN_PX, CITATIONS_MAX_PX));
    };
    const onStorage = (e: StorageEvent) => {
      if (e.key === CITATIONS_WIDTH_LS_KEY && e.newValue) {
        const n = parseInt(e.newValue, 10);
        if (Number.isFinite(n)) apply(n);
      }
    };
    const onBroadcast = (e: Event) => {
      const w = (e as CustomEvent<{ width?: number }>).detail?.width;
      if (typeof w === 'number' && Number.isFinite(w)) apply(w);
    };
    window.addEventListener('storage', onStorage);
    window.addEventListener(CITATIONS_WIDTH_BROADCAST, onBroadcast);
    return () => {
      window.removeEventListener('storage', onStorage);
      window.removeEventListener(CITATIONS_WIDTH_BROADCAST, onBroadcast);
    };
  }, []);

  const beginCitationsSplitResize = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = citationsWidthPx;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    let finalW = startW;
    const move = (ev: PointerEvent) => {
      // Handle is the left edge of the citations column: drag right (larger clientX) narrows it.
      finalW = clamp(startW + (startX - ev.clientX), CITATIONS_MIN_PX, CITATIONS_MAX_PX);
      setCitationsWidthPx(finalW);
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      try {
        localStorage.setItem(CITATIONS_WIDTH_LS_KEY, String(finalW));
        window.dispatchEvent(
          new CustomEvent(CITATIONS_WIDTH_BROADCAST, { detail: { width: finalW } }),
        );
      } catch {
        /* ignore */
      }
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }, [citationsWidthPx]);

  return { citationsWidthPx, beginCitationsSplitResize };
}
