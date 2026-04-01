import { z } from "zod";


export const OrgCreationBody = z
  .object({
    accountType: z.enum(['business']),
    shortName: z.string().optional(),
    contactEmail: z.string().email('Invalid email format'),
    registeredName: z.string().optional(), // Will be enforced conditionally
    adminFullName: z.string().min(1, 'Admin full name required'),
    password: z.string().min(8, 'Minimum 8 characters password required'),
    sendEmail: z.boolean().optional(), //TODO:Check if this is needed
    permanentAddress: z
      .object({
        addressLine1: z.string().optional(),
        city: z.string().optional(),
        state: z.string().optional(),
        country: z.string().optional(),
        postCode: z.string().optional(),
      })
      .optional(),
  })
  .refine(
    (data) => {
      // If accountType is 'business', registeredName must be present
      return data.accountType === 'business' ? !!data.registeredName : true;
    },
    {
      message: 'Registered Name is required for business accounts',
      path: ['registeredName'], // This ensures the error is associated with registeredName
    },
  );

const OnboardingStatusUpdateBody = z.object({
    status: z.enum(['configured', 'notConfigured', 'skipped']),
  });
  
export const OnboardingStatusUpdateValidationSchema = z.object({
body: OnboardingStatusUpdateBody,
query: z.object({}),
params: z.object({}),
headers: z.object({}),
});

export const OrgCreationValidationSchema = z.object({
body: OrgCreationBody,
query: z.object({}),
params: z.object({}),
headers: z.object({}),
});

const OrgUpdatePermanentAddressBody = z.object({
  addressLine1: z.string().optional(),
  city: z.string().optional(),
  state: z.string().optional(),
  country: z.string().optional(),
  postCode: z.string().optional(),
});

export const OrgUpdateBody = z
  .object({
    registeredName: z.string().optional(),
    shortName: z.string().optional(),
    contactEmail: z.string().email('Invalid email format').optional(),
    permanentAddress: OrgUpdatePermanentAddressBody.optional(),
    dataCollectionConsent: z.boolean().optional(),
  })
  .passthrough();

export const OrgUpdateValidationSchema = z.object({
  body: OrgUpdateBody,
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});

const OrgUpdateResponsePermanentAddress = z
  .object({
    addressLine1: z.string().optional(),
    city: z.string().optional(),
    state: z.string().optional(),
    postCode: z.string().optional(),
    country: z.string().optional(),
    _id: z.coerce.string().optional(),
  })
  .passthrough();

/**
 * Serialized org document (GET /org, POST /org create success body, and
 * `data` in PUT/DELETE org responses).
 */
export const OrgDocumentResponseSchema = z
  .object({
    _id: z.coerce.string(),
    registeredName: z.string(),
    shortName: z.string().optional(),
    domain: z.string(),
    contactEmail: z.string().email(),
    accountType: z.enum(['business']),
    permanentAddress: OrgUpdateResponsePermanentAddress.optional(),
    onBoardingStatus: z.enum(['configured', 'notConfigured', 'skipped']),
    isDeleted: z.boolean(),
    createdAt: z.union([z.string(), z.date()]),
    updatedAt: z.union([z.string(), z.date()]),
    slug: z.string(),
    __v: z.number(),
  })
  .passthrough();

/** Validates JSON for GET /org/exists (checkOrgExistence). */
export const CheckOrgExistenceResponseSchema = z.object({
  exists: z.boolean(),
});

/** Validates JSON for PUT /org (update organization details). */
export const UpdateOrganizationDetailsResponseSchema = z.object({
  message: z.string().min(1),
  data: OrgDocumentResponseSchema,
});

/** Validates JSON for DELETE /org (soft delete organization). */
export const DeleteOrganizationResponseSchema = z.object({
  message: z.string().min(1),
  data: OrgDocumentResponseSchema,
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
export const OrgHealthResponseSchema = z.object({
  status: z.literal('healthy'),
  timestamp: z.string().datetime(),
});

/** MIME types allowed for org logo upload (matches PUT /org/logo + multer fileFilter). */
export const OrgLogoUploadMimeType = z.enum([
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
export const OrgLogoPutBodySchema = z
  .object({
    fileBuffer: z
      .object({
        buffer: z.instanceof(Buffer),
        mimetype: OrgLogoUploadMimeType,
        originalname: z.string().optional(),
        size: z.number().int().nonnegative().optional(),
        lastModified: z.number().optional(),
        filePath: z.string().optional(),
      })
      .passthrough(),
  })
  .passthrough();

export const OrgLogoPutValidationSchema = z.object({
  body: OrgLogoPutBodySchema,
  query: z.object({}),
  params: z.object({}),
  headers: z.object({}),
});

/** GET/DELETE /org/logo — no meaningful body/query/params. */
export const OrgLogoReadDeleteValidationSchema = z.object({
  body: z.object({}).passthrough(),
  query: z.object({}).passthrough(),
  params: z.object({}).passthrough(),
  headers: z.object({}),
});

/** Validates JSON for PUT /org/logo (after successful save). */
export const UpdateOrgLogoResponseSchema = z.object({
  message: z.string().min(1),
  mimeType: z.enum(['image/jpeg', 'image/svg+xml']),
});

/**
 * Validates JSON for DELETE /org/logo (updated org-logo document; logo cleared).
 * GET /org/logo success is raw bytes or 204 — not JSON.
 */
export const RemoveOrgLogoResponseSchema = z
  .object({
    logo: z.null(),
    mimeType: z.null(),
  })
  .passthrough();