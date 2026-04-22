'use client';

import { useCallback, useState } from 'react';
import {
  CITATIONS_MAX_PX,
  CITATIONS_MIN_PX,
  CITATIONS_WIDTH_LS_KEY,
  clamp,
  readSavedCitationsWidthPx,
} from './resize-storage';

/** Shared state + pointer drag handler for the citations column (sidebar + fullscreen). */
export function useCitationsColumnResize() {
  const [citationsWidthPx, setCitationsWidthPx] = useState(() => readSavedCitationsWidthPx());

  const beginCitationsSplitResize = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    const startX = e.clientX;
    const startW = citationsWidthPx;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    let finalW = startW;
    const move = (ev: PointerEvent) => {
      finalW = clamp(startW + (ev.clientX - startX), CITATIONS_MIN_PX, CITATIONS_MAX_PX);
      setCitationsWidthPx(finalW);
    };
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
      try {
        localStorage.setItem(CITATIONS_WIDTH_LS_KEY, String(finalW));
      } catch {
        /* ignore */
      }
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  }, [citationsWidthPx]);

  return { citationsWidthPx, beginCitationsSplitResize };
}
