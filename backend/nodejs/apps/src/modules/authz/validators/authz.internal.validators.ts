import { z } from 'zod';
import { objectId } from '../../../libs/validators/zod-primitives';

const idString = z.string().min(1).max(64);

export const chatContentCheckSchema = z.object({
  body: z.object({
    userId: idString,
    orgId: objectId('orgId'),
    action: z.literal('read'),
    resource: z.object({
      type: z.enum(['chatAttachment', 'chatArtifact']),
      recordId: idString,
      ownerUserId: idString,
      conversationId: idString.nullish().transform((v) => v ?? undefined),
      runId: idString.nullish().transform((v) => v ?? undefined),
      kind: z
        .object({
          visibility: z
            .string()
            .max(32)
            .nullish()
            .transform((v) => v ?? undefined),
          isTemporary: z
            .boolean()
            .nullish()
            .transform((v) => v ?? undefined),
        })
        .nullish()
        .transform((v) => v ?? undefined),
    }),
  }),
});

export type ChatContentCheckBody = z.infer<
  typeof chatContentCheckSchema
>['body'];
