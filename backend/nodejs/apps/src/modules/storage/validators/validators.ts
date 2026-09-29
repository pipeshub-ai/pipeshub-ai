import { z } from 'zod';

// Common Schema Components
export const Headers = z.object({
  authorization: z.string(),
});

export const DocumentIdParams = z.object({
  params: z.object({
    documentId: z.string(),
  }),
  headers: Headers,
  body: z.object({
    fileBuffer: z.any(),
  }),
});

// Purges delete for good, so only a well-formed id may reach the query.
export const PurgeDocumentParams = z.object({
  params: z.object({
    documentId: z.string().regex(/^[0-9a-f]{24}$/i, 'Not a document id'),
  }),
});

export const PurgeVirtualRecordParams = z.object({
  params: z.object({
    virtualRecordId: z
      .string()
      .regex(/^[A-Za-z0-9_-]{1,128}$/, 'Not a virtual record id'),
  }),
});

export const DocumentIdParamsWithVersion = z.object({
  params: z.object({
    documentId: z.string(),
  }),
  headers: Headers,
  query: z.object({
    version: z.string()
      .optional()
      .transform((val) => (val ? Number(val) : undefined))
      .refine((num) => num === undefined || num >= 0, {
        message: "version must be greater than or equal to zero",
      }),
    expirationTimeInSeconds: z.string()
      .optional()
      .transform((val) => (val ? Number(val) : undefined))
      .refine((num) => num === undefined || num > 0, {
        message: "expirationTimeInSeconds must be greater than zero",
      }),
  }),
});

export const DirectUploadSchema = z.object({
  query: z.object({}),
  params: z.object({
    documentId: z.string(),
  }),
  headers: Headers,
});

export const UploadNewSchema = z.object({
  body: z
    .object({
      documentName: z.string(),
      documentPath: z.string().optional(),
      alternateDocumentName: z.string().optional(),
      permissions: z.string().optional(),
      customMetadata: z.any().optional(),
      isVersionedFile: z.string(),
      fileBuffer: z.any().optional(),
      fileBuffers: z.array(z.any()).optional(),
    })
    .refine(
      (data) => {
        return data.fileBuffer || data.fileBuffers;
      },
      {
        message: 'fileBuffer or fileBuffers must be present',
      },
    ),
  query: z.object({}),
  params: z.object({}),
  headers: Headers,
});

export const UploadNextVersionSchema = z.object({
  params: z.object({
    documentId: z.string(),
  }),
  body: z.object({
    currentVersionNote: z.string().optional(),
    nextVersionNote: z.string().optional(),
    fileBuffer: z.any(),
  }),
});

// Document Operation Schemas
export const GetBufferSchema = z.object({
  body: z.object({}),
  query: z.object({
    version: z
      .string()
      .optional()
      .transform((val) => (val ? Number(val) : undefined))
      .pipe(z.number().min(0).optional()),
  }),
  params: z.object({
    documentId: z.string(),
  }),
  headers: Headers,
});

export const RollBackToPreviousVersionSchema = GetBufferSchema.extend({
  body: z.object({
    note: z.string(),
    version: z
      .number({ invalid_type_error: 'version must be an integer' })
      .int({ message: 'version must be an integer' })
      .min(0, { message: 'version must be >= 0' })
      .optional(),
  }),
});

export const CreateDocumentSchema = z.object({
  body: z.object({
    documentName: z.string(),
    alternateDocumentName: z.string().optional(),
    documentPath: z.string(),
    permissions: z.string().optional(),
    metaData: z.any().optional(),
    customMetadata: z
      .array(z.object({ key: z.string(), value: z.any() }))
      .optional(),
    isVersionedFile: z.boolean().optional(),
    extension : z.string(),
  }),
  query: z.object({}),
  params: z.object({}),
  headers: Headers,
});