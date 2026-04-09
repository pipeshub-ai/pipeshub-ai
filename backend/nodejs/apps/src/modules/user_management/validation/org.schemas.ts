import { z } from "zod";

const UpdatePermanentAddressBody = z.object({
  addressLine1: z.string().optional(),
  city: z.string().optional(),
  state: z.string().optional(),
  country: z.string().optional(),
  postCode: z.string().optional(),
});

export const CreationBody = z.object({
  accountType: z.enum(['business']),
  shortName: z.string().optional(),
  contactEmail: z.string().email('Invalid email format'),
  registeredName: z.string().min(1, 'Registered Name is required for business accounts'),
  adminFullName: z.string().min(1, 'Admin full name required'),
  password: z.string().min(8, 'Minimum 8 characters password required'),
  sendEmail: z.boolean().optional(),
  permanentAddress: UpdatePermanentAddressBody.optional(),
});

const OnboardingStatusUpdateBody = z.object({
    status: z.enum(['configured', 'notConfigured', 'skipped']),
});
  
export const OnboardingStatusUpdateValidationSchema = z.object({
  body: OnboardingStatusUpdateBody,
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});

export const CreationValidationSchema = z.object({
  body: CreationBody,
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});

// fields are optional to allow partial updates
export const UpdateBody = z
  .object({
    registeredName: z.string().optional(),
    shortName: z.string().optional(),
    contactEmail: z.string().email('Invalid email format').optional(),
    permanentAddress: UpdatePermanentAddressBody.optional(),
    dataCollectionConsent: z.boolean().optional(),
  })

export const UpdateValidationSchema = z.object({
  body: UpdateBody,
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});

/** Same shape as request `permanentAddress`, plus subdocument `_id` from Mongo. */
const UpdateResponsePermanentAddress =
  UpdatePermanentAddressBody.extend({
    _id: z.coerce.string().optional(),
  });

/**
 * Serialized org document (GET /org, POST /org create success body, and
 * `data` in PUT/DELETE org responses).
 */
export const DocumentResponseSchema = z
  .object({
    _id: z.coerce.string(),
    registeredName: z.string(),
    shortName: z.string().optional(),
    domain: z.string(),
    contactEmail: z.string().email(),
    accountType: z.enum(['business']),
    permanentAddress: UpdateResponsePermanentAddress.optional(),
    onBoardingStatus: z.enum(['configured', 'notConfigured', 'skipped']),
    isDeleted: z.boolean(),
    createdAt: z.union([z.string(), z.date()]),
    updatedAt: z.union([z.string(), z.date()]),
    slug: z.string(),
    __v: z.number(),
  })

/** Validates JSON for GET /org/exists (checkOrgExistence). */
export const CheckExistenceResponseSchema = z.object({
  exists: z.boolean(),
});

/** Validates JSON for PUT /org (update organization details). */
export const UpdateDetailsResponseSchema = z.object({
  message: z.string().min(1),
  data: DocumentResponseSchema,
});

/** Validates JSON for DELETE /org (soft delete organization). */
export const DeleteResponseSchema = z.object({
  message: z.string().min(1),
  data: DocumentResponseSchema,
});

/** Validates JSON for GET /org/onboarding-status. */
export const GetOnboardingStatusResponseSchema = z.object({
  status: z.enum(['configured', 'notConfigured', 'skipped']),
});

/** Validates JSON for PUT /org/onboarding-status. */
export const UpdateOnboardingStatusResponseSchema = z.object({
  message: z.string().min(1),
  status: z.enum(['configured', 'notConfigured', 'skipped']),
});

/** Validates JSON for GET /org/health. */
export const HealthResponseSchema = z.object({
  status: z.literal('healthy'),
  timestamp: z.string().datetime(),
});

/** MIME types allowed for org logo upload (matches PUT /org/logo + multer fileFilter). */
export const LogoUploadMimeType = z.enum([
  'image/png',
  'image/jpeg',
  'image/jpg',
  'image/webp',
  'image/gif',
  'image/svg+xml',
]);

/**
 * Request body after buffer upload middleware (PUT /org/logo).
 * Extra `fileBuffer` fields from the processor are allowed via passthrough.
 */
export const LogoPutBodySchema = z
  .object({
    fileBuffer: z
      .object({
        buffer: z.instanceof(Buffer),
        mimetype: LogoUploadMimeType,
        originalname: z.string().optional(),
        size: z.number().int().nonnegative().optional(),
        lastModified: z.number().optional(),
        filePath: z.string().optional(),
      })
      .passthrough(),
  })
  .passthrough();

export const LogoPutValidationSchema = z.object({
  body: LogoPutBodySchema,
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});

/** GET/DELETE /org/logo — no meaningful body/query/params. */
export const LogoReadDeleteValidationSchema = z.object({
  body: z.object({}),
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});

/** Validates JSON for PUT /org/logo (after successful save). */
export const UpdateLogoResponseSchema = z.object({
  message: z.string().min(1),
  mimeType: z.enum(['image/jpeg', 'image/svg+xml']),
});

/**
 * Validates JSON for DELETE /org/logo (updated org-logo document; logo cleared).
 * GET /org/logo success is raw bytes or 204 — not JSON.
 */
export const RemoveLogoResponseSchema = z
  .object({
    logo: z.null(),
    mimeType: z.null(),
    /** Mongoose `toJSON()` may leave ObjectIds as instances until JSON.stringify. */
    _id: z.coerce.string(),
    orgId: z.coerce.string(),
    __v: z.number(),
  })
