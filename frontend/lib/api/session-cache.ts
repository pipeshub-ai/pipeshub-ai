import { mutate, unload } from 'swr';

/** How the session ended: `refetch` is true when the app stays open on another org (see clearSessionCache). */
export interface SessionEnd {
  refetch: boolean;
}

type SessionResetter = (end: SessionEnd) => void;

const resetters = new Set<SessionResetter>();
let generation = 0;

/** Registers per-user state that must not outlive the session. Returns a function that unregisters it. */
export function onSessionEnd(reset: SessionResetter): () => void {
  resetters.add(reset);
  return () => {
    resetters.delete(reset);
  };
}

/** Captures the current session; the returned check turns false once it ends. */
export function captureSession(): () => boolean {
  const captured = generation;
  return () => captured === generation;
}

interface ResettableStore<T> {
  getInitialState(): T;
  setState(state: T, replace: true): unknown;
}

/** Puts a Zustand store back to the state it was created with when the session ends. */
export function resetStoreOnSessionEnd<T>(store: ResettableStore<T>): () => void {
  return onSessionEnd(() => {
    store.setState(store.getInitialState(), true);
  });
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
  // First, so a resetter or refetch that takes a session token already belongs to the next session.
  generation += 1;
  // Stores next: a refetch started by unload calls its fetcher at once, and a
  // reset after that would throw away the new session's answer.
  resetters.forEach((reset) => {
    try {
      reset({ refetch });
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
  // shortcut: unlike unload, this skips useSWRInfinite/useSWRSubscription keys and keepPreviousData; clear those too once something uses them.
  void mutate(() => true, undefined, { revalidate: true });
}
