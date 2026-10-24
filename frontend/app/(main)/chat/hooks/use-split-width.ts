'use client';

import { useEffect, useState } from 'react';
import { clampSplitWidth } from '../constants';

/**
 * The chat pane width the user chose, capped to the current viewport so the preview keeps room.
 * Narrowing the window only caps what is shown; widening it again restores the chosen width.
 */
export function useSplitWidth(initialPx: () => number): [number, (px: number) => void] {
  const [preferredPx, setPreferredPx] = useState(initialPx);
  const [viewportPx, setViewportPx] = useState(() =>
    typeof window === 'undefined' ? Number.POSITIVE_INFINITY : window.innerWidth,
  );
  useEffect(() => {
    const onResize = () => setViewportPx(window.innerWidth);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  return [clampSplitWidth(preferredPx, viewportPx), setPreferredPx];
}
