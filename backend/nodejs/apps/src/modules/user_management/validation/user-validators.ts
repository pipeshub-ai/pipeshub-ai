import { z } from "zod";

//============================================
// Request Schemas for Users Routes
//============================================

export const GetAllUsersQuerySchema = z.object({
  blocked: z
    .enum(['true', 'false'])
    .optional(),
});

export const GetAllUsersValidationSchema = z.object({
  body: z.object({}),
  query: GetAllUsersQuerySchema,
  params: z.object({}),
  headers: z.object({}),
});

export const UserIdUrlParams = z.object({
    id: z.string().regex(/^[a-fA-F0-9]{24}$/, 'Invalid UserId'),
  });
  
export const UserIdValidationSchema = z.object({
body: z.object({}),
query: z.object({}),
params: UserIdUrlParams,
headers: z.object({}),
});

const objectIdHex24 = z
  .string()
  .regex(/^[a-fA-F0-9]{24}$/, 'Invalid ObjectId');

/** POST /users/bulk/invite */
export const BulkInviteBodySchema = z
  .object({
    emails: z
      .array(z.string().email('Invalid email'))
      .min(1, 'At least one email is required'),
    groupIds: z.array(objectIdHex24),
  })
  .strict();

export const BulkInviteValidationSchema = z.object({
  body: BulkInviteBodySchema,
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});

/** GET /users/:id/email */
export const GetUserEmailByUserIdValidationSchema = z.object({
  body: z.object({}),
  query: z.object({}),
  params: UserIdUrlParams,
  headers: z.object({}),
});

export const createUserBody = z.object({
fullName: z.string().min(1, 'Full name is required'),
email: z.string().email('Invalid email'),
mobile: z
    .string()
    .optional()
    .refine((val) => !val || /^\+?[0-9]{10,15}$/.test(val), {
    message: 'Invalid mobile number',
    }),
designation: z.string().optional(),
});

export const updateUserBody = z.object({
fullName: z.string().optional(),
email: z.string().email('Invalid email').optional(),
mobile: z
    .string()
    .optional()
    .refine((val) => !val || /^\+?[0-9]{10,15}$/.test(val), {
    message: 'Invalid mobile number',
    }),
designation: z.string().optional(),
firstName: z.string().optional(),
lastName: z.string().optional(),
middleName: z.string().optional(),
address: z
    .object({
    addressLine1: z.string().optional(),
    city: z.string().optional(),
    state: z.string().optional(),
    postCode: z.string().optional(),
    country: z.string().optional(),
    })
    .optional(),
dataCollectionConsent: z.boolean().optional(),
hasLoggedIn: z.boolean().optional(),
}).strict(); // Use strict mode to reject unknown fields

export const createUserValidationSchema = z.object({
body: createUserBody,
query: z.object({}),
params: z.object({}),
headers: z.object({}),
});

export const updateFullNameBody = z.object({
fullName: z.string().min(1, 'fullName must have at least one character'),
});

export const updateFirstNameBody = z.object({
firstName: z.string().min(1, 'firstName is required'),
});

export const updateLastNameBody = z.object({
lastName: z.string().min(1, 'lastName is required'),
});

export const updateEmailBody = z.object({
email: z.string().email('Valid email is required'),
});

export const updateUserFullNameValidationSchema = z.object({
body: updateFullNameBody,
query: z.object({}),
params: UserIdUrlParams,
headers: z.object({}),
});
export const updateUserFirstNameValidationSchema = z.object({
body: updateFirstNameBody,
query: z.object({}),
params: UserIdUrlParams,
headers: z.object({}),
});
export const updateUserLastNameValidationSchema = z.object({
body: updateLastNameBody,
query: z.object({}),
params: UserIdUrlParams,
headers: z.object({}),
});

export const updateDesignationBody = z.object({
designation: z.string().min(1, 'designation is required'),
});

export const updateUserDesignationValidationSchema = z.object({
body: updateDesignationBody,
query: z.object({}),
params: UserIdUrlParams,
headers: z.object({}),
});

export const updateUserEmailValidationSchema = z.object({
body: updateEmailBody,
query: z.object({}),
params: UserIdUrlParams,
headers: z.object({}),
});

export const updateUserValidationSchema = z.object({
body: updateUserBody,
query: z.object({}),
params: UserIdUrlParams,
headers: z.object({}),
});
export const emailIdValidationSchema = z.object({
body: updateEmailBody,
query: z.object({}),
params: z.object({}),
headers: z.object({}),
});

const displayPictureFileBufferSchema = z.object({
  originalname: z.string(),
  buffer: z.instanceof(Buffer),
  mimetype: z.enum([
    'image/png',
    'image/jpeg',
    'image/jpg',
    'image/webp',
    'image/gif',
  ]),
  size: z.number().nonnegative(),
  lastModified: z.number(),
  filePath: z.string(),
});

/** PUT /users/dp — after buffer upload middleware populates `body.fileBuffer` */
export const UpdateUserDisplayPictureValidationSchema = z.object({
  body: z.object({
    fileBuffer: displayPictureFileBufferSchema,
  }),
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});


//============================================
// Response Schemas for Users Routes
//============================================
const getAllUsersListAddressSchema = z.object({
  addressLine1: z.string().optional(),
  city: z.string().optional(),
  state: z.string().optional(),
  postCode: z.string().optional(),
  country: z.string().optional(),
});

/**
 * Shared user fields for GET /users list responses. Blocked-user aggregation does not
 * project `isDeleted` or `__v` — those are required only on {@link GetAllUsersListItemSchema}.
 */
const getAllUsersListItemFieldsBase = {
  _id: z.coerce.string(),
  orgId: z.coerce.string(),
  slug: z.string(),
  hasLoggedIn: z.boolean(),
  createdAt: z.union([z.string(), z.date()]),
  updatedAt: z.union([z.string(), z.date()]),
  fullName: z.string().optional(),
  email: z.string().email().optional(),
  firstName: z.string().optional(),
  lastName: z.string().optional(),
  middleName: z.string().optional(),
  mobile: z.string().optional(),
  designation: z.string().optional(),
  address: getAllUsersListAddressSchema.optional(),
  deletedBy: z.string().optional(),
};

/** GET /users — lean documents with `.select('-email')`; always include version flags. */
export const GetAllUsersListItemSchema = z.object({
  ...getAllUsersListItemFieldsBase,
  isDeleted: z.boolean(),
  __v: z.number(),
});

/** GET /users?blocked=true — narrow projection; email required, no `isDeleted` / `__v`. */
export const GetAllUsersBlockedListItemSchema = z.object({
  ...getAllUsersListItemFieldsBase,
  email: z.string().email(),
});

export const GetAllUsersResponseSchema = z.array(GetAllUsersListItemSchema);
export const GetAllUsersBlockedResponseSchema = z.array(
  GetAllUsersBlockedListItemSchema,
);

/** GET /users/fetch/with-groups — aggregation projects user + mapped group names/types. */
const GetAllUsersWithGroupsGroupSchema = z.object({
  name: z.string(),
  type: z.string(),
});

const GetAllUsersWithGroupsItemSchema = z.object({
  _id: z.coerce.string(),
  orgId: z.coerce.string(),
  fullName: z.string().optional(),
  hasLoggedIn: z.boolean(),
  groups: z.array(GetAllUsersWithGroupsGroupSchema),
  userId: z.coerce.string().optional(),
});

export const GetAllUsersWithGroupsResponseSchema = z.array(
  GetAllUsersWithGroupsItemSchema,
);

/** GET /users/:id/email — `{ email }` from `.select('email')` */
export const GetUserEmailByUserIdResponseSchema = z.object({
  email: z.string().email(),
});

/** Lean ObjectId fields — avoid `z.coerce.string()` so missing keys fail (coerce maps `undefined` → `"undefined"`). */
const leanObjectIdString = z.preprocess(
  (v) => (v == null ? v : String(v)),
  z.string().min(1),
);

/**
 * GET /users/:id — lean user JSON shape. `fullName` may be absent; `email` is omitted when `HIDE_EMAIL=true`.
 */
export const GetUserByIdResponseSchema = z
  .object({
    _id: leanObjectIdString,
    orgId: leanObjectIdString,
    email: z.string().email().optional(),
    fullName: z.string().optional(),
    hasLoggedIn: z.boolean(),
    slug: z.string(),
    isDeleted: z.boolean(),
    createdAt: z.union([z.string(), z.date()]),
    updatedAt: z.union([z.string(), z.date()]),
    __v: z.number(),
  });

/**
 * GET /users/internal/:id — lean user (scoped USER_LOOKUP). Email always present on success.
 */
export const InternalLookupUserResponseSchema = GetUserByIdResponseSchema.extend({
  email: z.string().email(),
});

/** GET /users/email/exists — matching non-deleted users for the given email (full lean documents). */
export const CheckUserExistsByEmailResponseSchema = z.array(
  InternalLookupUserResponseSchema,
);

/**
 * POST /users — created user (201). Same wire shape as GET /users/:id with email always present.
 */
export const CreateUserResponseSchema = z.object({
  _id: leanObjectIdString,
  orgId: leanObjectIdString,
  email: z.string().email(),
  fullName: z.string(),
  hasLoggedIn: z.boolean(),
  slug: z.string(),
  isDeleted: z.boolean(),
  createdAt: z.union([z.string(), z.date()]),
  updatedAt: z.union([z.string(), z.date()]),
  __v: z.number(),
});

const PatchUserDocumentBaseSchema = z.object({
  _id: leanObjectIdString,
  orgId: leanObjectIdString,
  fullName: z.string(),
  email: z.string().email(),
  hasLoggedIn: z.boolean(),
  isDeleted: z.boolean(),
  createdAt: z.union([z.string(), z.date()]),
  updatedAt: z.union([z.string(), z.date()]),
  slug: z.string(),
  __v: z.number(),
});

/** Core lean user fields for PATCH /email and PUT / — all required on success responses */
const PatchUserEmailPutCoreSchema = z.object({
  _id: leanObjectIdString,
  orgId: leanObjectIdString,
  fullName: z.string().min(1).optional(),
  email: z.string().email(),
  hasLoggedIn: z.boolean(),
  isDeleted: z.boolean(),
  createdAt: z.union([z.string(), z.date()]),
  updatedAt: z.union([z.string(), z.date()]),
  slug: z.string().min(1),
  __v: z.number(),
});

/** PATCH /users/:id/fullname — lean user; first/last may be absent */
export const UpdateUserFullNameResponseSchema = PatchUserDocumentBaseSchema.extend({
  firstName: z.string().optional(),
  lastName: z.string().optional(),
});

/** PATCH /users/:id/firstName */
export const UpdateUserFirstNameResponseSchema = PatchUserDocumentBaseSchema.extend({
  firstName: z.string().min(1),
  lastName: z.string().optional(),
});

/** PATCH /users/:id/lastName */
export const UpdateUserLastNameResponseSchema = PatchUserDocumentBaseSchema.extend({
  firstName: z.string().optional(),
  lastName: z.string().min(1),
});

/** PATCH /users/:id/designation — lean user + designation */
export const UpdateUserDesignationResponseSchema = PatchUserDocumentBaseSchema.extend({
  designation: z.string().min(1),
});

/** PATCH /email and PUT / — profile fields may all be absent on the wire */
const emailPutProfileFields = {
  firstName: z.string().optional(),
  lastName: z.string().optional(),
  designation: z.string().optional(),
  middleName: z.string().optional(),
  mobile: z.string().optional(),
  dataCollectionConsent: z.boolean().optional(),
  address: z
    .object({
      addressLine1: z.string().optional(),
      city: z.string().optional(),
      state: z.string().optional(),
      postCode: z.string().optional(),
      country: z.string().optional(),
    })
    .passthrough()
    .optional(),
};

/** PATCH /users/:id/email — full lean user document */
export const UpdateUserEmailResponseSchema =
  PatchUserEmailPutCoreSchema.extend(emailPutProfileFields);

/** PUT /users/:id — lean user + email-change meta */
export const UpdateUserPutResponseSchema = PatchUserEmailPutCoreSchema.extend({
  ...emailPutProfileFields,
  meta: z.object({
    emailChangeMailStatus: z.enum(['notNeeded', 'failed', 'sent']),
  }),
});

/** `{ message }` body shared by several user routes (delete, unblock, admin check, invite sent). */
export const UserMessageResponseSchema = z.object({
  message: z.string().min(1),
});

/** DELETE /users/:id */
export const DeleteUserResponseSchema = UserMessageResponseSchema;

/** PUT /users/:id/unblock */
export const UnblockUserResponseSchema = UserMessageResponseSchema;

/**
 * GET /users/dp — JSON when no picture.
 * DELETE /users/dp — JSON when document not found.
 */
export const UserDisplayPictureErrorResponseSchema = z.object({
  errorMessage: z.string().min(1),
});

/**
 * DELETE /users/dp — 200 JSON body after clearing stored picture (`pic` / `mimeType` null).
 */
export const RemoveUserDisplayPictureResponseSchema = z.object({
  _id: leanObjectIdString,
  userId: leanObjectIdString,
  orgId: leanObjectIdString,
  pic: z.null(),
  mimeType: z.null(),
  __v: z.number(),
  picUrl: z.union([z.string(), z.null()]).optional(),
});

/** GET /users/health */
export const UsersHealthResponseSchema = z.object({
  status: z.literal('healthy'),
  timestamp: z.string().datetime(),
});

/** GET /users/internal/admin-users — `{ adminUserIds }` (Mongo ObjectId hex strings) */
export const InternalAdminUsersResponseSchema = z.object({
  adminUserIds: z.array(objectIdHex24),
});

/** GET /users/graph/list — connector `entity/user/list` payload */
const GraphListUserItemSchema = z.object({
  id: z.string().min(1),
  userId: z.string().min(1),
  name: z.string(),
  email: z.string().email(),
  isActive: z.boolean(),
  createdAtTimestamp: z.number(),
  updatedAtTimestamp: z.number(),
});

const GraphListPaginationSchema = z.object({
  page: z.number().int().nonnegative(),
  limit: z.number().int().nonnegative(),
  total: z.number().int().nonnegative(),
  pages: z.number().int().nonnegative(),
  hasNext: z.boolean(),
  hasPrev: z.boolean(),
});

export const GraphListUsersResponseSchema = z.object({
  status: z.enum(['success']),
  message: z.string().min(1),
  users: z.array(GraphListUserItemSchema),
  pagination: GraphListPaginationSchema,
});

/** GET /users/:id/adminCheck — user is admin for org */
export const UserAdminCheckResponseSchema = UserMessageResponseSchema;

/** POST /users/:id/resend-invite — success */
export const InviteSentResponseSchema = UserMessageResponseSchema;

/**
 * POST /users/bulk/invite — 200 responses:
 * success / SMTP warning, or all emails already active (`errorMessage`).
 */
export const BulkInviteResponseSchema = z.union([
  InviteSentResponseSchema,
  z.object({ errorMessage: z.string().min(1) }),
]);