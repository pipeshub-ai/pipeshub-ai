'use client';

import { useCallback, useEffect, useRef } from 'react';

export const PREPARING_RETRY_FIRST_MS = 2_000;
export const PREPARING_RETRY_MAX_MS = 30_000;

/**
 * Asks for a listing again while its source is still being prepared (`preparing` on the answer): after 2 s, then
 * twice as long each time, at most 30 s apart, until an answer is no longer preparing. `settle` takes each answer;
 * `reset` starts the waits over, for a request the user made rather than a retry.
 */
export function usePreparingRetry() {
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const tries = useRef(0);

  const stop = useCallback(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = null;
  }, []);

  useEffect(() => stop, [stop]);

  const reset = useCallback(() => {
    stop();
    tries.current = 0;
  }, [stop]);

  const settle = useCallback(
    (preparing: boolean | undefined, retry: () => void) => {
      stop();
      if (!preparing) {
        tries.current = 0;
        return;
      }
      const wait = Math.min(PREPARING_RETRY_FIRST_MS * 2 ** tries.current, PREPARING_RETRY_MAX_MS);
      tries.current += 1;
      timer.current = setTimeout(retry, wait);
    },
    [stop]
  );

  return { settle, reset };
}
