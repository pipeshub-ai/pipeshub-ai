import { mutate, type SWRConfiguration } from 'swr';

/** First element of every cached SWR key; invalidation matches on it exactly. */
export const SWR_NS = {
  conversationLists: 'conversations/lists',
  agentLists: 'agents/lists',
  teamConnectorLists: 'connectors/team-lists',
} as const;

export type SwrNamespace = (typeof SWR_NS)[keyof typeof SWR_NS];

/**
 * Freshness windows for cached reads. A response is reused for the window after
 * it arrives, then refetched in the background while the old data stays shown.
 * A failure is not retried on a timer: the next mount (or focus) asks again.
 */
export const SWR_POLICY = {
  /** Lists other people change often (conversations): also refetched on tab focus. */
  list30s: { dedupingInterval: 30_000, shouldRetryOnError: false },
  /** Org lists (agents, team connectors): not refetched on focus. */
  list60s: { dedupingInterval: 60_000, shouldRetryOnError: false, revalidateOnFocus: false },
} as const satisfies Record<string, SWRConfiguration>;

/** Refetches mounted keys of `ns` now; keys not mounted refetch on their next mount. */
export function revalidateSwrNamespace(ns: SwrNamespace): void {
  void mutate((key) => Array.isArray(key) && key[0] === ns);
}
