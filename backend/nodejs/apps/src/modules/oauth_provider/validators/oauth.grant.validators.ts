import { z } from 'zod';
import { mongoIdRegex } from './oauth.validators';

export const grantIdParamsSchema = z.object({
  params: z.object({
    grantId: z.string().regex(mongoIdRegex, 'Invalid grant ID'),
  }),
  body: z
    .object({
      reason: z.string().max(500).optional(),
    })
    .optional(),
});

export const listAdminGrantsQuerySchema = z.object({
  query: z.object({
    page: z
      .preprocess(
        (arg) => (arg === '' || arg === undefined ? 1 : Number(arg)),
        z.number().int().min(1),
      )
      .optional(),
    limit: z
      .preprocess(
        (arg) => (arg === '' || arg === undefined ? 100 : Number(arg)),
        z.number().int().min(1).max(100),
      )
      .optional(),
  }),
});
