import { Schema } from 'mongoose';
import { KnowledgeLimit } from '../types/oauth.types';

/** Stored where a grant waits to become tokens, and on each access token so
 * its owner can see what it may reach. Absent means everything the user can
 * see; `default: undefined` keeps it absent rather than three empty lists,
 * which would mean nothing at all. */
export const knowledgeLimitField = {
  type: new Schema<KnowledgeLimit>(
    {
      apps: { type: [String], default: [] },
      recordGroups: { type: [String], default: [] },
      records: { type: [String], default: [] },
    },
    { _id: false },
  ),
  default: undefined,
};

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
