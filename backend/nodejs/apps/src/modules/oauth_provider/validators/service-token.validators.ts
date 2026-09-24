import { z } from 'zod';
import { mongoIdRegex } from './oauth.validators';
import { SERVICE_TOKEN_MAX_EXPIRY_DAYS } from '../services/service-token.service';

export const createServiceTokenSchema = z.object({
  body: z.object({
    serviceAccountId: z
      .string()
      .regex(mongoIdRegex, 'Invalid service account ID'),
    name: z.string().trim().min(1).max(100),
    // Required, and required to be non-empty. Unlike a personal access token,
    // omitting scopes here is not a shorthand for granting all of them.
    scopes: z.array(z.string()).min(1, 'Choose at least one scope'),
    expiryDays: z
      .number()
      .int()
      .min(1)
      .max(SERVICE_TOKEN_MAX_EXPIRY_DAYS)
      .optional(),
  }),
});

export const listServiceTokensQuerySchema = z.object({
  query: z.object({
    serviceAccountId: z
      .string()
      .regex(mongoIdRegex, 'Invalid service account ID'),
  }),
});

export const revokeServiceTokenSchema = z.object({
  params: z.object({
    tokenId: z.string().regex(mongoIdRegex, 'Invalid token ID'),
  }),
  query: z.object({
    serviceAccountId: z
      .string()
      .regex(mongoIdRegex, 'Invalid service account ID'),
  }),
});
