import { z } from 'zod'
import { mongoIdRegex } from './oauth.validators'

// Personal Access Token Validators

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
