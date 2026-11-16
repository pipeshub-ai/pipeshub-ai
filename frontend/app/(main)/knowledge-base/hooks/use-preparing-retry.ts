'use client';

import { useCallback, useEffect, useRef } from 'react';

export const PREPARING_RETRY_FIRST_MS = 2_000;
export const PREPARING_RETRY_MAX_MS = 30_000;

/** The key of a listing: All Records (of a node, or of everything), or a node of the Collections view (none without one). */
export function preparingKey(allRecords: boolean, nodeType?: string | null, nodeId?: string | null): string | null {
  if (allRecords) return `all-records:${nodeType ?? ''}:${nodeId ?? ''}`;
  return nodeType && nodeId ? `collections:${nodeType}:${nodeId}` : null;
}

/**
 * Asks for a listing again while its source is still being prepared (`preparing` on the answer): after 2 s, then
 * twice as long each time, at most 30 s apart, until an answer is no longer preparing.
 *
 * `currentKey` names the listing on screen (view mode, node type and id; null when none waits on a source). There is
 * one timer, for one key: it is cancelled as soon as the key on screen changes or the page goes away, and its retry
 * does nothing unless its key is still the one on screen, so a retry never fetches for, or replaces, a listing the
 * user has left. `settle` takes each answer with the key it was asked for; `isCurrent` tells a retry still in flight
 * whether its answer may be shown.
 */
export function usePreparingRetry(currentKey: string | null) {
  const timer = useRef<{ id: ReturnType<typeof setTimeout>; key: string } | null>(null);
  const tries = useRef(0);
  const current = useRef(currentKey);

  const stop = useCallback(() => {
    if (timer.current) clearTimeout(timer.current.id);
    timer.current = null;
  }, []);

  useEffect(() => {
    current.current = currentKey;
    tries.current = 0;
    // On a new key and on unmount: no listing is current until the next key is set, so a retry still in flight neither shows its answer nor starts a timer.
    return () => {
      current.current = null;
      stop();
    };
  }, [currentKey, stop]);

  const isCurrent = useCallback((key: string) => current.current === key, []);

  const settle = useCallback(
    (key: string, preparing: boolean | undefined, retry: () => void) => {
      if (key !== current.current) return;      // an answer for a listing the user has left starts nothing
      stop();
      if (!preparing) {
        tries.current = 0;
        return;
      }
      const wait = Math.min(PREPARING_RETRY_FIRST_MS * 2 ** tries.current, PREPARING_RETRY_MAX_MS);
      tries.current += 1;
      const id = setTimeout(() => {
        timer.current = null;
        if (current.current === key) retry();
      }, wait);
      timer.current = { id, key };
    },
    [stop]
  );

  return { settle, isCurrent };
}
