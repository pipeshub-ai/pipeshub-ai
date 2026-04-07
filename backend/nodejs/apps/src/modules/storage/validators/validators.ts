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

export const DocumentIdParamsWithVersion = z.object({
  params: z.object({
    documentId: z.string(),
  }),
  headers: Headers,
  query: z.object({
    version: z.string()
      .optional()
      .transform((val) => (val ? Number(val) : undefined))
      .refine((num) => num === undefined || num > 0, {
        message: "version must be greater than zero",
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
  }),
});

export const CreateDocumentSchema = z.object({
  body: z.object({
    documentName: z.string(),
    alternateDocumentName: z.string().optional(),
    documentPath: z.string(),
    permissions: z.string().optional(),
    metaData: z.any().optional(),
    isVersionedFile: z.boolean().optional(),
    extension : z.string(),
  }),
  query: z.object({}),
  params: z.object({}),
  headers: Headers,
});
