import { z } from 'zod';

const teamMemberSchema = z.object({
  id: z.string().min(1),
  isOwner: z.boolean(),
  joinedAt: z.number(),
  role: z.string().min(1),
  userEmail: z.string(),
  userId: z.string().min(1),
  userName: z.string(),
});

const currentUserPermissionSchema = z.object({
  createdAtTimestamp: z.number(),
  role: z.string().min(1),
  updatedAtTimestamp: z.number(),
  type: z.string().min(1),
  from_id: z.string().min(1),
  to_id: z.string().min(1),
  from_collection: z.string().min(1),
  to_collection: z.string().min(1),
  _from: z.string().min(1),
  _to: z.string().min(1),
});

export const teamResponseItemSchema = z.object({
  currentUserPermission: currentUserPermissionSchema,
  canEdit: z.boolean(),
  memberCount: z.number().int().nonnegative(),
  description: z.string().nullable().optional(),
  updatedAtTimestamp: z.number(),
  orgId: z.string().min(1),
  createdAtTimestamp: z.number(),
  canManageMembers: z.boolean(),
  createdBy: z.string().min(1),
  members: z.array(teamMemberSchema),
  name: z.string().min(1),
  canDelete: z.boolean(),
  id: z.string().min(1),
  _key: z.string().min(1),
  _id: z.string().min(1),
});

const teamsPaginationSchema = z.object({
  page: z.number().int().min(1),
  limit: z.number().int().min(1),
  total: z.number().int().nonnegative(),
  pages: z.number().int().nonnegative(),
  hasNext: z.boolean().optional(),
  hasPrev: z.boolean().optional(),
});

export const createTeamSuccessResponseSchema = z.object({
  status: z.literal('success'),
  message: z.literal('Team created successfully'),
  data: teamResponseItemSchema,
});

export const updateTeamSuccessResponseSchema = z.object({
  status: z.literal('success'),
  message: z.literal('Team updated successfully'),
  team: teamResponseItemSchema,
});

export const deleteTeamSuccessResponseSchema = z.object({
  status: z.literal('success'),
  message: z.literal('Team deleted successfully'),
});

export const getUserTeamsSuccessResponseSchema = z.object({
  status: z.literal('success'),
  message: z.enum(['User teams fetched successfully', 'No teams found']),
  teams: z.array(teamResponseItemSchema),
  pagination: teamsPaginationSchema,
});

export const createTeamValidationSchema = z.object({
  body: z.object({
    name: z.string().min(1, 'Name is required'),
    description: z.string().optional(),
    userRoles: z
      .array(
        z.object({
          userId: z.string().min(1),
          role: z.string().min(1),
        })
      )
      .optional(),
  }),
});

export const listTeamsValidationSchema = z.object({
  query: z.object({
    search: z.string().optional(),
    limit: z
      .preprocess((arg) => Number(arg), z.number().min(1).max(100).default(10))
      .optional(),
    page: z
      .preprocess((arg) => Number(arg), z.number().min(1).default(1))
      .optional(),
  }),
});

export const updateTeamValidationSchema = z.object({
  params: z.object({
    teamId: z.string().min(1, 'Team ID is required'),
  }),
  body: z.object({
    name: z.string().optional(),
    description: z.string().optional(),
    addUserRoles: z
      .array(
        z.object({
          userId: z.string().min(1),
          role: z.string().min(1),
        })
      )
      .optional(),
    removeUserIds: z.array(z.string()).optional(),
    updateUserRoles: z
      .array(
        z.object({
          userId: z.string().min(1),
          role: z.string().min(1),
        })
      )
      .optional(),
  }),
});

export const deleteTeamValidationSchema = z.object({
  params: z.object({
    teamId: z.string().min(1, 'Team ID is required'),
  }),
});
