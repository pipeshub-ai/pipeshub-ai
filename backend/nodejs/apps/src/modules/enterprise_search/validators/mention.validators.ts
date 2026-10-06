import { z } from 'zod';
import { objectId } from '../../../libs/validators/zod-primitives';
import {
  MENTION_ID_MAX_LENGTH,
  MENTION_TYPES,
  MENTIONS_MAX,
} from '../services/collaboration/mentions/mention.types';

export const NOTE_QUERY_MAX = 10_000;
export const MENTIONABLES_LIMIT_MAX = 20;
const CLIENT_MESSAGE_ID_MAX = 64;

/** Ids only; labels are never accepted. */
export const mentionRefSchema = z.object({
  type: z.enum(MENTION_TYPES),
  id: z.string().min(1).max(MENTION_ID_MAX_LENGTH),
});

export const mentionsFieldSchema = z.array(mentionRefSchema).max(MENTIONS_MAX);

const noteBody = z.object({
  query: z
    .string()
    .min(1, { message: 'Query is required' })
    .max(NOTE_QUERY_MAX),
  // Empty only makes a note in `mention_only`; the service refuses it in the other modes (MESSAGE_NOT_NOTE).
  mentions: mentionsFieldSchema.default([]),
  clientMessageId: z.string().min(1).max(CLIENT_MESSAGE_ID_MAX),
});

const mentionablesQuery = z.object({
  q: z.string().max(100).default(''),
  limit: z.coerce
    .number()
    .int()
    .min(1)
    .max(MENTIONABLES_LIMIT_MAX)
    .default(MENTIONABLES_LIMIT_MAX),
});

const conversationId = objectId('conversation ID');
const agentKey = z.string().min(1, { message: 'Agent key is required' });

const build = (
  params: z.AnyZodObject,
): Record<'notes' | 'mentionables', z.AnyZodObject> => ({
  notes: z.object({ params, body: noteBody }),
  mentionables: z.object({ params, query: mentionablesQuery }),
});

export const mentionSchemas = {
  chat: build(z.object({ conversationId })),
  agent: build(z.object({ agentKey, conversationId })),
};
