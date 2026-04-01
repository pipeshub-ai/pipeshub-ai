import { z } from "zod";

export const UserIdUrlParams = z.object({
    id: z.string().regex(/^[a-fA-F0-9]{24}$/, 'Invalid UserId'),
  });
  
export const UserIdValidationSchema = z.object({
body: z.object({}),
query: z.object({}),
params: UserIdUrlParams,
headers: z.object({}),
});

export const MultipleUserBody = z.object({
userIds: z
    .array(z.string().regex(/^[a-fA-F0-9]{24}$/, 'Invalid MongoDB ObjectId'))
    .min(1, 'At least one userId is required'),
});
export const MultipleUserValidationSchema = z.object({
body: MultipleUserBody,
query: z.object({}),
params: z.object({}),
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

/**
 * Serialized user document for GET /users (default list). Email is omitted by `.select('-email')`.
 */
export const GetAllUsersListItemSchema = z
  .object({
    _id: z.coerce.string(),
    orgId: z.coerce.string(),
    fullName: z.string().optional(),
    hasLoggedIn: z.boolean(),
    isDeleted: z.boolean(),
    createdAt: z.union([z.string(), z.date()]),
    updatedAt: z.union([z.string(), z.date()]),
    slug: z.string(),
    __v: z.number(),
  })
  .passthrough();

/** GET /users?blocked=true — aggregation projects user fields including email. */
export const GetAllUsersBlockedListItemSchema = z
  .object({
    _id: z.coerce.string(),
    email: z.string().email(),
    orgId: z.coerce.string(),
    fullName: z.string().optional(),
    hasLoggedIn: z.boolean(),
    slug: z.string(),
    createdAt: z.union([z.string(), z.date()]),
    updatedAt: z.union([z.string(), z.date()]),
  })
  .passthrough();

export const GetAllUsersResponseSchema = z.array(GetAllUsersListItemSchema);
export const GetAllUsersBlockedResponseSchema = z.array(
  GetAllUsersBlockedListItemSchema,
);