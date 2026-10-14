import { z } from 'zod';
import {
  SERVICE_ACCOUNT_SLUG_MAX_LENGTH,
  SERVICE_ACCOUNT_SLUG_MIN_LENGTH,
} from '../constants/service-account.constants';

const mongoIdRegex = /^[0-9a-fA-F]{24}$/;

/**
 * The slug becomes the local part of the account's address, so it is checked
 * here as well as in the service. This one produces the friendlier message on
 * the way in; the service's check is the one that cannot be bypassed.
 */
const slugSchema = z
  .string()
  .trim()
  .toLowerCase()
  .min(SERVICE_ACCOUNT_SLUG_MIN_LENGTH)
  .max(SERVICE_ACCOUNT_SLUG_MAX_LENGTH)
  .regex(
    /^[a-z0-9]+(?:-[a-z0-9]+)*$/,
    'Use lowercase letters, digits and single hyphens, not starting or ending with a hyphen',
  );

export const createServiceAccountSchema = z.object({
  body: z.object({
    slug: slugSchema,
    fullName: z.string().trim().min(1).max(100),
    description: z.string().trim().max(500).optional(),
  }),
});

export const updateServiceAccountSchema = z.object({
  params: z.object({
    id: z.string().regex(mongoIdRegex, 'Invalid service account ID'),
  }),
  body: z
    .object({
      fullName: z.string().trim().min(1).max(100).optional(),
      description: z.string().trim().max(500).optional(),
      isDisabled: z.boolean().optional(),
    })
    // A PATCH with an empty body would otherwise be accepted and do nothing.
    .refine((body) => Object.keys(body).length > 0, {
      message: 'Provide at least one field to update',
    }),
});

export const serviceAccountIdParamsSchema = z.object({
  params: z.object({
    id: z.string().regex(mongoIdRegex, 'Invalid service account ID'),
  }),
});
