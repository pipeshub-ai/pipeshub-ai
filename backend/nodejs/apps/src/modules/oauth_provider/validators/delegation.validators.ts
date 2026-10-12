import { z } from 'zod';
import { mongoIdRegex } from './oauth.validators';
import { DELEGATION_EVENT_MAX_PAGE_SIZE } from '../services/delegation.service';

const serviceAccountIdQuery = z
  .string()
  .regex(mongoIdRegex, 'Invalid service account ID');

export const listDelegationsQuerySchema = z.object({
  query: z.object({
    serviceAccountId: serviceAccountIdQuery,
  }),
});

export const grantDelegationSchema = z.object({
  body: z.object({
    serviceAccountId: serviceAccountIdQuery,
    // A user id or an email address, so this cannot be narrowed to a mongo id
    // regex. The service resolves it and refuses anything that is neither.
    subject: z.string().trim().min(1).max(320),
    reason: z.string().trim().max(280).optional(),
  }),
});

export const revokeDelegationSchema = z.object({
  params: z.object({
    subjectUserId: z.string().regex(mongoIdRegex, 'Invalid user ID'),
  }),
  query: z.object({
    serviceAccountId: serviceAccountIdQuery,
  }),
});

export const listDelegationEventsQuerySchema = z.object({
  query: z.object({
    // Optional here, unlike the allow-list views: the log is worth reading
    // across the whole organisation, which is how an administrator would
    // notice one account probing for people it may not act as.
    serviceAccountId: serviceAccountIdQuery.optional(),
    limit: z.coerce
      .number()
      .int()
      .min(1)
      .max(DELEGATION_EVENT_MAX_PAGE_SIZE)
      .optional(),
  }),
});
