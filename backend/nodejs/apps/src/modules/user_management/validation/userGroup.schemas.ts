import { z } from "zod";

export const IdUrlParams = z.object({
    groupId: z.string().min(1, "Group ID is required")
      .regex(/^[0-9a-fA-F]{24}$/, "Invalid group ID format")
  });
  
export const IdValidationSchema = z.object({
  body: z.object({}),
  query: z.object({}),
  params: IdUrlParams,
  headers: z.object({}),
});

export const GroupValidationSchema = z.object({
  body: z.object({
      type: z.string().min(1, 'type is required'),
      name: z.string().min(1, 'name is required'),
  }),
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});

const userGroupTypeEnum = z.enum(['admin', 'standard', 'everyone', 'custom']);

/**
 * Serialized user group document for POST create (201).
 */
export const CreateResponseSchema = z
  .object({
    name: z.string(),
    type: userGroupTypeEnum,
    orgId: z.coerce.string(),
    users: z.array(z.coerce.string()),
    isDeleted: z.boolean(),
    deletedBy: z.coerce.string().optional(),
    _id: z.coerce.string(),
    createdAt: z.union([z.string(), z.date()]),
    updatedAt: z.union([z.string(), z.date()]),
    slug: z.string(),
    __v: z.number(),
  })

/** GET /user-groups — array of group documents (same shape as create response per item). */
export const GetAllResponseSchema = z.array(CreateResponseSchema);

/** GET/PUT/DELETE /user-groups/:groupId — single group document (same shape as create). */
export const DocumentResponseSchema = CreateResponseSchema;

export const mongoObjectIdString = z
  .string()
  .regex(/^[a-fA-F0-9]{24}$/, 'Invalid MongoDB ObjectId');

/** GET /user-groups/:groupId/users */
export const GetUsersInGroupResponseSchema = z.object({
  users: z.array(mongoObjectIdString),
});

const groupSummaryForUserSchema = z
  .object({
    _id: z.coerce.string(),
    name: z.string(),
    type: userGroupTypeEnum,
  })

/** GET /user-groups/users/:userId */
export const GetGroupsForUserResponseSchema = z.array(groupSummaryForUserSchema);

const addRemoveUsersToGroupsBody = z.object({
  userIds: z
    .array(mongoObjectIdString)
    .min(1, 'At least one userId is required'),
  groupIds: z
    .array(mongoObjectIdString)
    .min(1, 'At least one groupId is required'),
});

/** POST /user-groups/add-users */
export const AddUsersToGroupsValidationSchema = z.object({
  body: addRemoveUsersToGroupsBody,
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});

/** POST /user-groups/remove-users */
export const RemoveUsersFromGroupsValidationSchema = z.object({
  body: addRemoveUsersToGroupsBody,
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});

export const AddUsersToGroupsResponseSchema = z.object({
  message: z.string().min(1, 'Message is required'),
});

export const RemoveUsersFromGroupsResponseSchema = z.object({
  message: z.string().min(1, 'Message is required'),
});

/** GET /user-groups/stats/list — aggregate by group name. */
const StatisticsRowSchema = z
  .object({
    _id: z.string(),
    count: z.number(),
    totalUsers: z.number(),
    avgUsers: z.number(),
  })

export const GetStatisticsResponseSchema = z.array(StatisticsRowSchema);
