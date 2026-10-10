import { z } from 'zod';
import {
  PRINCIPAL_TYPES,
  objectId,
  principalSchema,
} from '../../../libs/validators/zod-primitives';

import { RESPOND_MODES } from '../services/collaboration/mentions/mention.types';

export const COLLABORATORS_PER_PUT_MAX = 50;
export const HANDOVER_NOTE_MAX = 500;
export const FEED_PAGE_MAX = 100;

const conversationId = objectId('conversation ID');
const agentKey = z.string().min(1, { message: 'Agent key is required' });

const chatParams = z.object({ conversationId });
const agentParams = z.object({ agentKey, conversationId });

const collaboratorSchema = principalSchema.and(
  z.object({ accessLevel: z.enum(['read', 'write']) }),
);

export const putBody = z.object({
  collaborators: z
    .array(collaboratorSchema)
    .min(1)
    .max(COLLABORATORS_PER_PUT_MAX)
    .refine(
      (rows) =>
        new Set(rows.map((r) => `${r.principalType}:${r.principalId}`)).size ===
        rows.length,
      { message: 'Each person or team may appear once' },
    ),
  note: z.string().max(HANDOVER_NOTE_MAX).optional(),
  confirmOrgWide: z.literal(true).optional(),
});

const settingsBody = z
  .object({
    editorsCanInvite: z.boolean().optional(),
    ownerContentShared: z.boolean().optional(),
    respondMode: z.enum(RESPOND_MODES).optional(),
  })
  .refine((v) => Object.keys(v).length > 0, {
    message: 'At least one setting is required',
  });

const transferBody = z.object({ newOwnerUserId: objectId('user ID') });

const feedQuery = z.object({
  afterSeq: z.coerce.number().int().min(-1).default(-1),
  rev: z.coerce.number().int().min(0).optional(),
});

const removeQuery = z.object({
  principalType: z.enum(PRINCIPAL_TYPES).default('user'),
});

type Kind = 'chat' | 'agent';

export interface CollaborationSchemas {
  list: z.AnyZodObject;
  put: z.AnyZodObject;
  remove: z.AnyZodObject;
  settings: z.AnyZodObject;
  transfer: z.AnyZodObject;
  leave: z.AnyZodObject;
  feed: z.AnyZodObject;
  readiness: z.AnyZodObject;
}

const build = (params: z.AnyZodObject): CollaborationSchemas => ({
  list: z.object({ params }),
  put: z.object({ params, body: putBody }),
  remove: z.object({
    params: params.extend({ principalId: z.string().min(1) }),
    query: removeQuery,
  }),
  settings: z.object({ params, body: settingsBody }),
  transfer: z.object({ params, body: transferBody }),
  leave: z.object({ params }),
  feed: z.object({ params, query: feedQuery }),
  readiness: z.object({ params }),
});

export const collaborationSchemas: Record<Kind, CollaborationSchemas> = {
  chat: build(chatParams),
  agent: build(agentParams),
};
