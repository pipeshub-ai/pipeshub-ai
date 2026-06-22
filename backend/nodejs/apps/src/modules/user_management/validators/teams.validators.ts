import { z } from 'zod';
import {
  validateNoFormatSpecifiers,
  validateNoXSS,
} from '../../../utils/xss-sanitization';

// ---------------------------------------------------------------------------
// Primitive validators
// ---------------------------------------------------------------------------

const OBJECT_ID_REGEX = /^[a-fA-F0-9]{24}$/;

/** UUID team key or system org "All" team `all_{mongoOrgId}`. */
const TEAM_GRAPH_KEY_REGEX =
  /^(all_[a-fA-F0-9]{24}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12})$/;

const mongoUserId = z
  .string()
  .regex(OBJECT_ID_REGEX, { message: 'Invalid user ID format' });

const teamGraphKey = z
  .string()
  .regex(TEAM_GRAPH_KEY_REGEX, { message: 'Invalid team ID format' });

const teamRole = z.enum(['OWNER', 'READER', 'WRITER']);

const pageSchema = z.preprocess(
  (arg) => (arg === undefined || arg === '' ? undefined : Number(arg)),
  z.number().min(1).default(1),
);

const teamLimitSchema = z.preprocess(
  (arg) => (arg === undefined || arg === '' ? undefined : Number(arg)),
  z.number().min(1).max(100).default(10),
);

const timestampQuerySchema = z.preprocess(
  (arg) => (arg === undefined || arg === '' ? undefined : Number(arg)),
  z.number().int().positive().optional(),
);

const normalizeOptionalSearch = (arg: unknown): string | undefined => {
  if (arg === undefined || arg === '') {
    return undefined;
  }
  const trimmed = String(arg).trim();
  return trimmed === '' ? undefined : trimmed;
};

const teamSearchSchema = z.preprocess(
  normalizeOptionalSearch,
  z
    .string()
    .min(1)
    .max(1000, { message: 'Search parameter too long (max 1000 characters)' })
    .optional(),
).superRefine((value, ctx) => {
  if (!value) {
    return;
  }
  try {
    validateNoXSS(value, 'search parameter');
    validateNoFormatSpecifiers(value, 'search parameter');
  } catch (error: unknown) {
    const message =
      error instanceof Error ? error.message : 'Invalid search parameter';
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      message,
    });
  }
});

// ---------------------------------------------------------------------------
// Reusable sub-schemas
// ---------------------------------------------------------------------------

const userRoleEntrySchema = z.object({
  userId: mongoUserId,
  role: teamRole,
});

const teamIdParams = z.object({
  teamId: teamGraphKey,
});

const teamListQueryObject = z.object({
  page: pageSchema.optional(),
  limit: teamLimitSchema.optional(),
  search: teamSearchSchema,
});

const MEMBER_ROLE_ARRAY_KEYS = [
  'userRoles',
  'addUserRoles',
  'updateUserRoles',
] as const;

const sanitizeMemberBody = (data: unknown): unknown => {
  if (!data || typeof data !== 'object') {
    return data;
  }
  const body = { ...(data as Record<string, unknown>) };
  for (const key of MEMBER_ROLE_ARRAY_KEYS) {
    if (!Array.isArray(body[key])) {
      continue;
    }
    body[key] = (body[key] as unknown[]).filter(
      (ur: unknown) =>
        ur &&
        typeof ur === 'object' &&
        typeof (ur as { userId?: string }).userId === 'string' &&
        (ur as { userId: string }).userId.trim() !== '' &&
        (ur as { role?: string }).role,
    );
    if ((body[key] as unknown[]).length === 0) {
      delete body[key];
    }
  }
  return body;
};

const createTeamBodyObject = z.object({
  name: z
    .string()
    .trim()
    .min(1, { message: 'Name is required' })
    .max(100, { message: 'Name must be at most 100 characters' }),
  description: z
    .string()
    .max(500, { message: 'Description must be at most 500 characters' })
    .optional(),
  userRoles: z.array(userRoleEntrySchema).optional(),
});

const updateTeamBodyObject = z.object({
  name: z
    .string()
    .trim()
    .min(1, { message: 'Name must not be empty' })
    .max(100, { message: 'Name must be at most 100 characters' })
    .optional(),
  description: z
    .string()
    .max(500, { message: 'Description must be at most 500 characters' })
    .optional(),
  addUserRoles: z.array(userRoleEntrySchema).optional(),
  removeUserIds: z.array(mongoUserId).optional(),
  updateUserRoles: z.array(userRoleEntrySchema).optional(),
});

// ---------------------------------------------------------------------------
// Route schemas
// ---------------------------------------------------------------------------

export const createTeamSchema = z.object({
  body: z.preprocess(sanitizeMemberBody, createTeamBodyObject),
});

export const teamIdParamsSchema = z.object({
  params: teamIdParams,
});

export const getTeamSchema = teamIdParamsSchema;

export const deleteTeamSchema = teamIdParamsSchema;

export const updateTeamSchema = z.object({
  params: teamIdParams,
  body: z.preprocess(sanitizeMemberBody, updateTeamBodyObject),
});

export const getTeamUsersSchema = z.object({
  params: teamIdParams,
  query: teamListQueryObject,
});

export const getUserTeamsQuerySchema = z.object({
  query: teamListQueryObject.extend({
    created_by: mongoUserId.optional(),
    created_after: timestampQuerySchema,
    created_before: timestampQuerySchema,
  }),
});
