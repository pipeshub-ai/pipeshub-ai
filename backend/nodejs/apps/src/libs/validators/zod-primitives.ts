import { z } from 'zod';

export const OBJECT_ID_REGEX = /^[0-9a-fA-F]{24}$/;

/** UUID team key or system org "All" team `all_{mongoOrgId}`. */
export const TEAM_ID_REGEX =
  /^(all_[a-fA-F0-9]{24}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12})$/;

export const objectId = (
  label: string,
  message: string = `Invalid ${label} format`,
): z.ZodString => z.string().regex(OBJECT_ID_REGEX, { message });

export const teamId = (
  message: string = 'Invalid team ID format',
): z.ZodString => z.string().regex(TEAM_ID_REGEX, { message });

export const PRINCIPAL_TYPES = ['user', 'team'] as const;
export type PrincipalType = (typeof PRINCIPAL_TYPES)[number];

/** A user (ObjectId) or a team (UUID, or the org-wide `all_{orgId}`) by its wire shape. */
export const principalSchema = z
  .object({
    principalType: z.enum(PRINCIPAL_TYPES),
    principalId: z.string().min(1, { message: 'principalId is required' }),
  })
  .superRefine((value, ctx) => {
    const pattern =
      value.principalType === 'user' ? OBJECT_ID_REGEX : TEAM_ID_REGEX;
    if (!pattern.test(value.principalId)) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ['principalId'],
        message: `Invalid ${value.principalType} ID format`,
      });
    }
  });
