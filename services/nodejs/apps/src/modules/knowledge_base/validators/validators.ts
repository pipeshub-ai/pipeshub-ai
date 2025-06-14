import { z } from 'zod';
import { extensionToMimeType } from '../../storage/mimetypes/mimetypes';

export const createRecordSchema = z.object({
  body: z.object({
    recordName: z.string().min(1).optional(),
    recordType: z.string().min(1).default('FILE').optional(),
    origin: z.string().min(1).default('UPLOAD').optional(),
    isVersioned: z
      .union([
        z.boolean(),
        z.string().transform((val) => {
          if (val === '') return false;
          if (val === 'true' || val === '1') return true;
          if (val === 'false' || val === '0') return false;
          throw new Error('Invalid boolean string value');
        }),
      ])
      .default(false)
      .optional(),
    fileBuffers: z
      .array(
        z.object({
          buffer: z.any(),
          mimetype: z
            .string()
            .refine(
              (value) => Object.values(extensionToMimeType).includes(value),
              { message: 'Invalid MIME type' },
            ),
          originalname: z.string(),
          size: z.number(),
          lastModified: z.number().optional(),
        }),
      )
      .optional(),
    fileBuffer: z
      .object({
        buffer: z.any(),
        mimetype: z
          .string()
          .refine(
            (value) => Object.values(extensionToMimeType).includes(value),
            { message: 'Invalid MIME type' },
          ),
        originalname: z.string(),
        size: z.number(),
        lastModified: z.number().optional(),
      })
      .optional(),
  }),
});

export const getRecordByIdSchema = z.object({
  params: z.object({ recordId: z.string().min(1) }),
});

export const getRecordsSchema = z.object({
  query: z.object({
    page: z
      .string()
      .optional()
      .refine(
        (val) => {
          const parsed = parseInt(val || '1', 10);
          return !isNaN(parsed) && parsed > 0;
        },
        { message: 'Page must be a positive number' },
      ),

    limit: z
      .string()
      .optional()
      .refine(
        (val) => {
          const parsed = parseInt(val || '20', 10);
          return !isNaN(parsed) && parsed > 0 && parsed <= 100;
        },
        { message: 'Limit must be a number between 1 and 100' },
      ),

    search: z
      .string()
      .optional()
      .transform((val) => {
        if (!val) return undefined;
        const trimmed = val.trim();
        if (trimmed.length > 100) {
          throw new Error('Search term too long');
        }
        return trimmed || undefined;
      }),

    recordTypes: z
      .string()
      .optional()
      .transform((val) =>
        val ? val.split(',').filter((type) => type.trim() !== '') : undefined,
      ),

    origins: z
      .string()
      .optional()
      .transform((val) =>
        val
          ? val.split(',').filter((origin) => origin.trim() !== '')
          : undefined,
      ),

    connectors: z
      .string()
      .optional()
      .transform((val) =>
        val
          ? val.split(',').filter((connector) => connector.trim() !== '')
          : undefined,
      ),

    indexingStatus: z
      .string()
      .optional()
      .transform((val) =>
        val
          ? val.split(',').filter((status) => status.trim() !== '')
          : undefined,
      ),

    permissions: z
      .string()
      .optional()
      .transform((val) =>
        val
          ? val.split(',').filter((permission) => permission.trim() !== '')
          : undefined,
      ),

    dateFrom: z
      .string()
      .optional()
      .refine(
        (val) => {
          if (!val) return true;
          const parsed = parseInt(val, 10);
          return !isNaN(parsed) && parsed > 0;
        },
        { message: 'Invalid date from' },
      ),

    dateTo: z
      .string()
      .optional()
      .refine(
        (val) => {
          if (!val) return true;
          const parsed = parseInt(val, 10);
          return !isNaN(parsed) && parsed > 0;
        },
        { message: 'Invalid date to' },
      ),

    sortBy: z
      .string()
      .optional()
      .refine(
        (val) => {
          const allowedSortFields = [
            'createdAtTimestamp',
            'updatedAtTimestamp',
            'recordName',
            'recordType',
            'origin',
            'indexingStatus',
          ];
          return !val || allowedSortFields.includes(val);
        },
        { message: 'Invalid sort field' },
      ),

    sortOrder: z.enum(['asc', 'desc']).optional(),

    source: z.enum(['all', 'local', 'connector']).optional().default('all'),
  }),
});

export const updateRecordSchema = z.object({
  params: z.object({ recordId: z.string().min(1) }),
  body: z.object({
    fileBuffer: z.any(),
    recordName : z.string().optional(),
  }),
});

export const deleteRecordSchema = z.object({
  params: z.object({ recordId: z.string().min(1) }),
});

export const archiveRecordSchema = z.object({
  params: z.object({ recordId: z.string().min(1) }),
});

export const unarchiveRecordSchema = z.object({
  params: z.object({ recordId: z.string().min(1) }),
});

export const restoreRecordSchema = z.object({
  recordId: z.string().min(1),
});

export const setRecordExpirationTimeSchema = z.object({
  recordId: z.string().min(1),
  expirationTime: z.number().min(1),
});

export const getOCRDataSchema = z.object({
  recordId: z.string().min(1),
});

export const uploadNextVersionSchema = z.object({
  recordId: z.string().min(1),
});

export const searchInKBSchema = z.object({
  query: z.string().min(1),
});

export const answerQueryFromKBSchema = z.object({
  query: z.string().min(1),
});

export const reindexAllRecordSchema = z.object({
  body: z.object({
    app : z.string().min(1),
  }),
})

export const resyncConnectorSchema = z.object({
  body: z.object({
    connectorName : z.string().min(1),
  }),
})
