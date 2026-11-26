'use client';

import { useCallback, useRef } from 'react';

/**
 * A list page sends one request per search keystroke, page or filter change, and the answers can arrive out of
 * order: only the newest request may set the rows, its error or the loading flag, or a late answer for an older
 * query replaces the current results (R46LANE-FU-5, R50-JFIX).
 *
 * `begin()` marks a new request as the newest and returns `isStale`, true once a later request has begun.
 */
export function useNewestRequest(): () => () => boolean {
  const latest = useRef(0);
  return useCallback(() => {
    const id = ++latest.current;
    return () => id !== latest.current;
  }, []);
}
