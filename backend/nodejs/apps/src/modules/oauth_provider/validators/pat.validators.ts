import { z } from 'zod'
import { knowledgeLimitSchema, mongoIdRegex } from './oauth.validators'
import { integerCheck } from '../../../libs/utils/zod-formats';

export const createPatTokenSchema = z.object({
  body: z.object({
    name: z.string().min(1).max(100),
    scopes: z.array(z.string()).min(1).max(100).optional(),
    expiryDays: z
      .literal([7, 30, 90], { error: 'Expiry days must be 7, 30 or 90.' })
      .optional(),
    knowledgeLimit: knowledgeLimitSchema.optional(),
  }),
})

export const tokenIdParamsSchema = z.object({
  params: z.object({
    tokenId: z.string().regex(mongoIdRegex, 'Invalid token ID'),
  }),
})

export const listAdminPatQuerySchema = z.object({
  query: z.object({
    page: z
      .preprocess(
        (arg) => (arg === '' || arg === undefined ? 1 : Number(arg)),
        z.number().check(integerCheck()).min(1),
      )
      .optional(),
    limit: z
      .preprocess(
        (arg) => (arg === '' || arg === undefined ? 100 : Number(arg)),
        z.number().check(integerCheck()).min(1).max(100),
      )
      .optional(),
  }),
})
