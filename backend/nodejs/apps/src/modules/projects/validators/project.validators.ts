import { z } from 'zod';
import {
  PROJECT_CHAT_SHARING_VALUES,
  PROJECT_DESCRIPTION_MAX_LENGTH,
  PROJECT_INSTRUCTIONS_MAX_LENGTH,
  PROJECT_MEMBER_ROLE_VALUES,
  PROJECT_MEMBERS_BATCH_MAX,
  PROJECT_NAME_MAX_LENGTH,
  PROJECT_VISIBILITY_VALUES,
} from '../constants/constants';

const OBJECT_ID_REGEX = /^[0-9a-fA-F]{24}$/;
const objectId = (label: string): z.ZodString =>
  z.string().regex(OBJECT_ID_REGEX, { message: `Invalid ${label} format` });

const pageSchema = z.preprocess(
  (arg) => (arg === undefined || arg === '' ? undefined : Number(arg)),
  z.number().min(1).default(1),
);
const limitSchema = z.preprocess(
  (arg) => (arg === undefined || arg === '' ? undefined : Number(arg)),
  z.number().min(1).max(100).default(20),
);

const appOrKbIdSchema = z.string().min(1).max(200);

const knowledgeScopeSchema = z
  .object({
    apps: z.array(appOrKbIdSchema).optional(),
    kb: z.array(appOrKbIdSchema).optional(),
  })
  .optional();

const appliedFilterNodeSchema = z.object({
  id: z.string(),
  name: z.string(),
  nodeType: z.string(),
  connector: z.string(),
});

const appliedFiltersSchema = z
  .object({
    apps: z.array(appliedFilterNodeSchema).optional(),
    kb: z.array(appliedFilterNodeSchema).optional(),
  })
  .optional();

const projectBodyFieldsSchema = {
  name: z
    .string()
    .trim()
    .min(1, { message: 'Project name is required' })
    .max(PROJECT_NAME_MAX_LENGTH),
  description: z.string().max(PROJECT_DESCRIPTION_MAX_LENGTH).optional(),
  icon: z.string().max(100).optional(),
  color: z.string().max(50).optional(),
  instructions: z.string().max(PROJECT_INSTRUCTIONS_MAX_LENGTH).optional(),
  knowledgeScope: knowledgeScopeSchema,
  appliedFilters: appliedFiltersSchema,
};

export const createProjectSchema = z.object({
  body: z.object(projectBodyFieldsSchema),
});

export const updateProjectSchema = z.object({
  params: z.object({ projectId: objectId('project ID') }),
  body: z
    .object({
      ...projectBodyFieldsSchema,
      visibility: z.enum(PROJECT_VISIBILITY_VALUES).optional(),
      chatSharing: z.enum(PROJECT_CHAT_SHARING_VALUES).optional(),
    })
    .partial(),
});

export const projectIdParamsSchema = z.object({
  params: z.object({ projectId: objectId('project ID') }),
});

export const listProjectsQuerySchema = z.object({
  query: z.object({
    page: pageSchema,
    limit: limitSchema,
    search: z.string().max(200).optional(),
    scope: z.enum(['mine', 'shared', 'all']).optional().default('mine'),
    includeArchived: z
      .enum(['true', 'false'])
      .optional()
      .transform((v) => v === 'true'),
  }),
});

export const listProjectConversationsQuerySchema = z.object({
  params: z.object({ projectId: objectId('project ID') }),
  query: z.object({
    page: pageSchema,
    limit: limitSchema,
  }),
});

export const removeProjectFileParamsSchema = z.object({
  params: z.object({
    projectId: objectId('project ID'),
    recordId: z.string().min(1, { message: 'recordId is required' }),
  }),
});

export const upsertProjectMembersSchema = z.object({
  params: z.object({ projectId: objectId('project ID') }),
  body: z.object({
    members: z
      .array(
        z.object({
          principalId: objectId('user ID'),
          role: z.enum(PROJECT_MEMBER_ROLE_VALUES),
        }),
      )
      .min(1, { message: 'At least one member is required' })
      .max(PROJECT_MEMBERS_BATCH_MAX, {
        message: `At most ${PROJECT_MEMBERS_BATCH_MAX} members may be updated per request`,
      }),
  }),
});

export const removeProjectMemberParamsSchema = z.object({
  params: z.object({
    projectId: objectId('project ID'),
    memberUserId: objectId('member user ID'),
  }),
});
