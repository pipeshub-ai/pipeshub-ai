import { z } from 'zod'
import { mongoIdRegex } from './oauth.validators'

export const createPatTokenSchema = z.object({
  body: z.object({
    name: z.string().min(1).max(100),
    scopes: z.array(z.string()).min(1).optional(),
    expiryDays: z
      .union([
        z.literal(30),
        z.literal(90),
        z.literal(365),
        z.literal('never'),
      ])
      .optional(),
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
})
