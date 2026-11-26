import { KnowledgeLimit } from './oauth.types';

/** The stored limit as the plain object a token claim carries. */
export const toKnowledgeLimit = (
  stored: KnowledgeLimit | null | undefined,
): KnowledgeLimit | undefined =>
  stored
    ? {
        apps: [...stored.apps],
        recordGroups: [...stored.recordGroups],
        records: [...stored.records],
      }
    : undefined;

const LIMIT_KEYS = ['apps', 'recordGroups', 'records'] as const;

/** How many items a limit names; none (everything) is 0. */
export const knowledgeLimitSize = (
  limit: KnowledgeLimit | null | undefined,
): number =>
  limit
    ? limit.apps.length + limit.recordGroups.length + limit.records.length
    : 0;

/**
 * A limit read back from storage, where nothing has validated it. Absent or
 * null is everything. Anything else must be an object whose lists hold only
 * ids and name at least one between them, or it is `'unreadable'`: a caller
 * must refuse it rather than fall back to everything.
 */
export const readStoredKnowledgeLimit = (
  stored: unknown,
): KnowledgeLimit | undefined | 'unreadable' => {
  if (stored === undefined || stored === null) return undefined;
  if (typeof stored !== 'object' || Array.isArray(stored)) return 'unreadable';
  const raw = stored as Record<string, unknown>;
  const limit: KnowledgeLimit = { apps: [], recordGroups: [], records: [] };
  for (const key of LIMIT_KEYS) {
    const ids = raw[key];
    if (ids === undefined || ids === null) continue;
    if (
      !Array.isArray(ids) ||
      !ids.every((id) => typeof id === 'string' && id !== '')
    ) {
      return 'unreadable';
    }
    limit[key] = [...(ids as string[])];
  }
  return knowledgeLimitSize(limit) > 0 ? limit : 'unreadable';
};

/** Two limits name the same items, in any order; absent equals only absent. */
export const sameKnowledgeLimit = (
  a: KnowledgeLimit | null | undefined,
  b: KnowledgeLimit | null | undefined,
): boolean => {
  const sorted = (
    limit: KnowledgeLimit | null | undefined,
  ): KnowledgeLimit | undefined => {
    const plain = toKnowledgeLimit(limit);
    return (
      plain && {
        apps: plain.apps.sort(),
        recordGroups: plain.recordGroups.sort(),
        records: plain.records.sort(),
      }
    );
  };
  return JSON.stringify(sorted(a)) === JSON.stringify(sorted(b));
};

/** A query filter that matches an app whose stored limit is `limit`, with
 * each list in the order it was stored; none matches an absent limit. */
export const storedKnowledgeLimitFilter = (
  limit: KnowledgeLimit | null | undefined,
): Record<string, unknown> =>
  limit
    ? {
        'knowledgeLimit.apps': limit.apps,
        'knowledgeLimit.recordGroups': limit.recordGroups,
        'knowledgeLimit.records': limit.records,
      }
    : { knowledgeLimit: null };

/** Whether everything `inner` reaches is still inside `outer`. A limit
 * reaches what any of its lists names, so each list of `inner` must sit in
 * the same list of `outer`. Absent `outer` holds anything; absent `inner`
 * reaches everything, so only an absent `outer` holds it. An item moved from
 * one list to another (a folder dropped once its whole app is named) counts
 * as outside: answering that needs the graph, and refusing is the safe
 * side. */
export const isKnowledgeLimitWithin = (
  inner: KnowledgeLimit | null | undefined,
  outer: KnowledgeLimit | null | undefined,
): boolean => {
  if (!outer) {
    return true;
  }
  if (!inner) {
    return false;
  }
  const contains = (ids: string[], allowed: string[]): boolean => {
    const set = new Set(allowed);
    return ids.every((id) => set.has(id));
  };
  return (
    contains(inner.apps, outer.apps) &&
    contains(inner.recordGroups, outer.recordGroups) &&
    contains(inner.records, outer.records)
  );
};
