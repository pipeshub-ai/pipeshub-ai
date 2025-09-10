import { z } from 'zod';
import { extensionToMimeType } from '../../storage/mimetypes/mimetypes';


export const getRecordByIdSchema = z.object({
  params: z.object({ recordId: z.string().min(1) }),
});

export const updateRecordSchema = z.object({
  body: z.object({
    fileBuffer: z.any(),
    recordName: z.string().optional(),
  }),
  params: z.object({
    recordId: z.string(),
  }),
});

export const deleteRecordSchema = z.object({
  params: z.object({ recordId: z.string().min(1) }),
});

export const reindexRecordSchema = z.object({
  params: z.object({ recordId: z.string().min(1) }),
});

export const reindexAllRecordSchema = z.object({
  body: z.object({
    app: z.string().min(1),
  }),
});

export const resyncConnectorSchema = z.object({
  body: z.object({
    connectorName: z.string().min(1),
  }),
});

export const getConnectorStatsSchema = z.object({
  params: z.object({ connector: z.string().min(1) }),
});

export const uploadRecordsSchema = z.object({
  body: z
    .object({
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

      file_paths: z.preprocess(
        (val) => (typeof val === 'string' ? [val] : val),
        z
          .array(z.string().min(1, 'File path cannot be empty'))
          .min(1, 'At least one file path is required')
          .max(1000, 'Maximum 1000 files allowed per upload')
          .refine(
            (paths) => {
              // Check for duplicate paths
              const uniquePaths = new Set(paths);
              return uniquePaths.size === paths.length;
            },
            {
              message: 'Duplicate file paths are not allowed',
            },
          )
          .refine(
            (paths) => {
              // Validate path format - no leading/trailing slashes, no double slashes
              const invalidPaths = paths.filter(
                (path) =>
                  path.startsWith('/') ||
                  path.endsWith('/') ||
                  path.includes('//') ||
                  path.includes('\\') ||
                  /[<>:"|?*\x00-\x1f]/.test(path), // Invalid filename characters
              );
              return invalidPaths.length === 0;
            },
            {
              message: 'Invalid file path format detected',
            },
          ),
      ),

      last_modified: z.preprocess(
        (val) => (typeof val === 'string' ? [val] : val),
        z
          .array(
            z
              .string()
              .regex(/^\d+$/, 'Last modified must be a numeric timestamp'),
          )
          .min(1, 'At least one last modified timestamp is required')
          .transform((timestamps) =>
            timestamps.map((ts) => {
              const parsed = parseInt(ts, 10);
              if (isNaN(parsed) || parsed < 0) {
                throw new Error(`Invalid timestamp: ${ts}`);
              }
              return parsed;
            }),
          )
          .refine(
            (timestamps) => {
              // Validate timestamps are reasonable (after 1970, before year 3000)
              const minTimestamp = 0; // 1970-01-01
              const maxTimestamp = 32503680000000; // Year 3000
              return timestamps.every(
                (ts) => ts >= minTimestamp && ts <= maxTimestamp,
              );
            },
            {
              message: 'Timestamps must be valid Unix timestamps',
            },
          ),
      ),
    })
    .refine(
      (data) => {
        // Validate that last_modified array length matches file_paths array length
        if (data.file_paths && data.last_modified) {
          return data.last_modified.length === data.file_paths.length;
        }
        return true;
      },
      {
        message: 'Last modified array must match file paths array length',
        path: ['last_modified'],
      },
    ),
  params: z.object({
    kbId: z.string().uuid(),
  }),
});

export const uploadRecordsToFolderSchema = z.object({
  body: z
    .object({
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

      file_paths: z.preprocess(
        (val) => (typeof val === 'string' ? [val] : val),
        z
          .array(z.string().min(1, 'File path cannot be empty'))
          .min(1, 'At least one file path is required')
          .max(1000, 'Maximum 1000 files allowed per upload')
          .refine(
            (paths) => {
              const uniquePaths = new Set(paths);
              return uniquePaths.size === paths.length;
            },
            {
              message: 'Duplicate file paths are not allowed',
            },
          )
          .refine(
            (paths) => {
              const invalidPaths = paths.filter(
                (path) =>
                  path.startsWith('/') ||
                  path.endsWith('/') ||
                  path.includes('//') ||
                  path.includes('\\') ||
                  /[<>:"|?*\x00-\x1f]/.test(path),
              );
              return invalidPaths.length === 0;
            },
            {
              message: 'Invalid file path format detected',
            },
          ),
      ),

      last_modified: z.preprocess(
        (val) => (typeof val === 'string' ? [val] : val),
        z
          .array(
            z
              .string()
              .regex(/^\d+$/, 'Last modified must be a numeric timestamp'),
          )
          .min(1, 'At least one last modified timestamp is required')
          .transform((timestamps) =>
            timestamps.map((ts) => {
              const parsed = parseInt(ts, 10);
              if (isNaN(parsed) || parsed < 0) {
                throw new Error(`Invalid timestamp: ${ts}`);
              }
              return parsed;
            }),
          )
          .refine(
            (timestamps) => {
              const minTimestamp = 0;
              const maxTimestamp = 32503680000000;
              return timestamps.every(
                (ts) => ts >= minTimestamp && ts <= maxTimestamp,
              );
            },
            {
              message: 'Timestamps must be valid Unix timestamps',
            },
          ),
      ),
    })
    .refine(
      (data) => {
        // Validate that last_modified array length matches file_paths array length
        if (data.file_paths && data.last_modified) {
          return data.last_modified.length === data.file_paths.length;
        }
        return true;
      },
      {
        message: 'Last modified array must match file paths array length',
        path: ['last_modified'],
      },
    ),
  params: z.object({
    kbId: z.string().uuid(),
    folderId: z.string().min(1), // Folder ID validation
  }),
});

export const getAllRecordsSchema = z.object({
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

export const getAllKBRecordsSchema = z.object({
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

export const createKBSchema = z.object({
  body: z.object({
    kbName: z.string().min(1).max(255),
  }),
});

export const getKBSchema = z.object({
  params: z.object({ kbId: z.string().min(1) }),
});

export const listKnowledgeBasesSchema = z.object({
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

    permissions: z
      .string()
      .optional()
      .transform((val) =>
        val
          ? val.split(',').filter((permission) => permission.trim() !== '')
          : undefined,
      ),

    sortBy: z
      .string()
      .optional()
      .refine(
        (val) => {
          const allowedSortFields = [
            'createdAtTimestamp',
            'updatedAtTimestamp',
            'name',
            'userRole',
          ];
          return !val || allowedSortFields.includes(val);
        },
        { message: 'Invalid sort field' },
      ),

    sortOrder: z.enum(['asc', 'desc']).optional(),
  }),
});

export const updateKBSchema = z.object({
  body: z.object({
    kbName: z.string().min(1).max(255).optional(),
  }),
  params: z.object({
    kbId: z.string().uuid(),
  }),
});

export const deleteKBSchema = z.object({
  params: z.object({ kbId: z.string().min(1) }),
});

export const createFolderSchema = z.object({
  body: z.object({
    folderName: z.string().min(1).max(255),
  }),
});

export const kbPermissionSchema = z.object({
  body: z.object({
    userIds: z.array(z.string()).optional(),
    teamIds: z.array(z.string()).optional(),
    role: z.enum(['OWNER', 'WRITER', 'READER', 'COMMENTER']),
  }).refine((data) => (data.userIds && data.userIds.length > 0) || (data.teamIds && data.teamIds.length > 0),
    {
      message: 'At least one user or team ID is required',
      path: ['userIds'],
    },
  ),
  params: z.object({
    kbId: z.string().uuid(),
  }),
});

export const getFolderSchema = z.object({
  params: z.object({ kbId: z.string().min(1), folderId: z.string().min(1) }),
});

export const updateFolderSchema = z.object({
  body: z.object({
    folderName: z.string().min(1).max(255),
  }),
  params: z.object({ kbId: z.string().min(1), folderId: z.string().min(1) }),
});

export const deleteFolderSchema = z.object({
  params: z.object({ kbId: z.string().min(1), folderId: z.string().min(1) }),
});

export const getPermissionsSchema = z.object({
  params: z.object({ kbId: z.string().min(1) }),
});

export const updatePermissionsSchema = z.object({
  body: z.object({
    role: z.enum(['OWNER', 'WRITER', 'READER', 'COMMENTER']),
    userIds: z.array(z.string()).optional(),
    teamIds: z.array(z.string()).optional(),
  }),
  params: z.object({
    kbId: z.string().uuid(),
  }),
});

export const deletePermissionsSchema = z.object({
  body: z.object({
    userIds: z.array(z.string()).optional(),
    teamIds: z.array(z.string()).optional(),
  }),
  params: z.object({
    kbId: z.string().uuid(),
  }),
});