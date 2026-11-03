import { mutate, type SWRConfiguration } from 'swr';
import { isGoneError } from './api-error';

/** First element of every cached SWR key; invalidation matches on it exactly. */
export const SWR_NS = {
  conversationLists: 'conversations/lists',
  agentLists: 'agents/lists',
  teamConnectorLists: 'connectors/team-lists',
  agentDetails: 'agents/detail',
  projectDetails: 'projects/detail',
  org: 'org',
  aiModelConfig: 'ai-models/config',
  aiModelRegistry: 'ai-models/registry',
  profile: 'profile',
} as const;

export type SwrNamespace = (typeof SWR_NS)[keyof typeof SWR_NS];

/** A cache key: its namespace, then whatever identifies the response. */
export type SwrKey = readonly [SwrNamespace, ...unknown[]];

/**
 * Freshness windows for cached reads. A response is reused for the window after
 * it arrives, then refetched in the background while the old data stays shown.
 * A failure is not retried on a timer: the next mount (or, for list30s, focus) asks again.
 */
export const SWR_POLICY = {
  /** Lists other people change often (conversations): also refetched on tab focus. */
  list30s: { dedupingInterval: 30_000, shouldRetryOnError: false },
  /** Org lists (agents, team connectors): not refetched on focus. */
  list60s: { dedupingInterval: 60_000, shouldRetryOnError: false, revalidateOnFocus: false },
  /**
   * Read once per session (org, model config, profile): only this user's own
   * writes change it, and they update or invalidate the cache.
   */
  sessionStable: {
    // The dedupe marker is the "fresh" flag: invalidation and sign-out delete it, so the
    // next mount refetches. Largest timer delay; Infinity would fire at once.
    dedupingInterval: 2_147_483_647,
    shouldRetryOnError: false,
    revalidateOnFocus: false,
    revalidateOnReconnect: false,
  },
} as const satisfies Record<string, SWRConfiguration>;

const revalidateListeners = new Map<SwrNamespace, Set<() => void>>();

/**
 * Lets a cache kept outside SWR (a store) drop its copy whenever `ns` is
 * invalidated. Returns a function that unsubscribes.
 */
export function onSwrNamespaceRevalidate(ns: SwrNamespace, listener: () => void): () => void {
  const listeners = revalidateListeners.get(ns) ?? new Set<() => void>();
  listeners.add(listener);
  revalidateListeners.set(ns, listeners);
  return () => {
    listeners.delete(listener);
  };
}

/** Refetches mounted keys of `ns` now; keys not mounted refetch on their next mount. */
export function revalidateSwrNamespace(ns: SwrNamespace): void {
  void mutate((key) => Array.isArray(key) && key[0] === ns);
  revalidateListeners.get(ns)?.forEach((listener) => listener());
}

/** Refetches `key` now if it is mounted, otherwise on its next mount, even inside its window. */
export function revalidateSwrKey(key: SwrKey): void {
  void mutate(key);
}

/**
 * Drops `key`'s data and fetches it again: now if mounted, otherwise on the
 * next mount, which then waits for the answer instead of showing the old one.
 * For the user's own edit when the write's answer is not what the read returns.
 */
export function refreshSwrKey(key: SwrKey): void {
  void mutate(key, undefined);
}

/**
 * Whether a cached read means the entity is not available: it is missing or
 * forbidden, or it failed before anything was shown. A failed background
 * refresh keeps the data already on screen.
 */
export function isSwrReadUnavailable(hasData: boolean, error: unknown): boolean {
  if (!error) return false;
  return !hasData || isGoneError(error);
}

/**
 * Stores what the user's own edit produced; an older response still in flight
 * for `key` is discarded. For something just deleted, write what "gone" looks
 * like rather than clearing the key: inside the window a cleared key would
 * replay the last response on its next mount.
 */
export function writeSwrKey<T>(key: SwrKey, data: T): void {
  void mutate(key, data, { revalidate: false });
}
