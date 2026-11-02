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

export {
  toKnowledgeLimit,
  sameKnowledgeLimit,
  storedKnowledgeLimitFilter,
  isKnowledgeLimitWithin,
} from '../types/knowledge_limit';
