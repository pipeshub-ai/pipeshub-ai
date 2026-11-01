import { mutate, unload } from 'swr';

const resetters = new Set<() => void>();

/** Registers per-user state that must not outlive the session. Returns a function that unregisters it. */
export function onSessionEnd(reset: () => void): () => void {
  resetters.add(reset);
  return () => {
    resetters.delete(reset);
  };
}

export interface ClearSessionCacheOptions {
  /**
   * Refetch what is on screen right away. For a switch to another org while
   * the app stays open; a sign-out leaves the signed-in screens, so it does not.
   */
  refetch?: boolean;
}

/**
 * Drops every cached response, including ones still in flight, and resets the
 * registered per-user state. Plain function, callable outside React (sign-out,
 * org switch). Never throws, so signing out always completes.
 */
export function clearSessionCache({ refetch = false }: ClearSessionCacheOptions = {}): void {
  // Stores first: a refetch started by unload calls its fetcher at once, and a
  // reset after that would throw away the new session's answer.
  resetters.forEach((reset) => {
    try {
      reset();
    } catch (error) {
      console.error('Session reset failed:', error);
    }
  });
  if (!refetch) {
    unload({ revalidate: false });
    return;
  }
  // unload() would delete each entry with the original key that namespace
  // revalidation matches on, and hooks still on screen write it back only on
  // remount. Overwriting every entry keeps that key, clears data and error, and
  // still drops answers in flight (a mutation newer than their start wins).
  void mutate(() => true, undefined, { revalidate: true });
}
