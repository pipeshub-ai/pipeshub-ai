import { z } from 'zod';
import { extensionToMimeType } from '../../storage/mimetypes/mimetypes';


export const getRecordByIdSchema = z.object({
  params: z.object({ recordId: z.string().min(1) }),
  query: z.object({ convertTo: z.string().optional() }),
});

export const updateRecordSchema = z.object({
  body: z.object({
    fileBuffer: z.any().optional(),
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
  body: z
    .object({
      depth: z.number().int().min(-1).max(100).optional(),
    })
    .optional(),
});

export const reindexRecordGroupSchema = z.object({
  params: z.object({ recordGroupId: z.string().min(1) }),
  body: z
    .object({
      depth: z.number().int().min(-1).max(100).optional(),
    })
    .optional(),
});

export const reindexFailedRecordSchema = z.object({
  body: z.object({
    app: z.string().min(1),
    connectorId: z.string().min(1),
  }),
});

export const resyncConnectorSchema = z.object({
  body: z.object({
    connectorName: z.string().min(1),
    connectorId: z.string().min(1),
  }),
});

export const getConnectorStatsSchema = z.object({
  params: z.object({ connectorId: z.string().min(1) }),
});

/**
 * Schema for the processed file buffer with metadata attached.
 * This is set by the file processor middleware after parsing files_metadata.
 */
const fileBufferSchema = z.object({
  buffer: z.any(),
  mimetype: z.string().refine(
    (value) => Object.values(extensionToMimeType).includes(value),
    { message: 'Invalid MIME type' },
  ),
  originalname: z.string(),
  size: z.number(),
  lastModified: z.number(),
  filePath: z.string(),
});

export const uploadRecordsSchema = z.object({
  body: z.object({
    recordName: z.string().min(1).optional(),
    recordType: z.string().min(1).default('FILE').optional(),
    origin: z.string().min(1).default('UPLOAD').optional(),
    isVersioned: z
      .union([
        z.boolean(),
        z.string().transform((val) => {
          if (val === '' || val === 'false' || val === '0') return false;
          if (val === 'true' || val === '1') return true;
          throw new Error('Invalid boolean string value');
        }),
      ])
      .default(false)
      .optional(),

    // Processed file buffers (set by file processor middleware)
    fileBuffers: z.array(fileBufferSchema).optional(),
    fileBuffer: fileBufferSchema.optional(),

    // Files metadata JSON string - parsed by file processor
    // Format: [{ file_path: string, last_modified: number }, ...]
    files_metadata: z
      .string()
      .optional()
      .refine(
        (val) => {
          if (!val) return true;
          try {
            const parsed = JSON.parse(val);
            if (!Array.isArray(parsed)) return false;
            // Validate each entry has required fields
            return parsed.every(
              (entry: any) =>
                typeof entry.file_path === 'string' &&
                typeof entry.last_modified === 'number',
            );
          } catch {
            return false;
          }
        },
        {
          message:
            'files_metadata must be a valid JSON array with { file_path, last_modified } objects',
        },
      ),
  }),
  params: z.object({
    kbId: z.string().uuid(),
  }),
});

export const uploadRecordsToFolderSchema = z.object({
  body: z.object({
    recordName: z.string().min(1).optional(),
    recordType: z.string().min(1).default('FILE').optional(),
    origin: z.string().min(1).default('UPLOAD').optional(),
    isVersioned: z
      .union([
        z.boolean(),
        z.string().transform((val) => {
          if (val === '' || val === 'false' || val === '0') return false;
          if (val === 'true' || val === '1') return true;
          throw new Error('Invalid boolean string value');
        }),
      ])
      .default(false)
      .optional(),

    // Processed file buffers (set by file processor middleware)
    fileBuffers: z.array(fileBufferSchema).optional(),
    fileBuffer: fileBufferSchema.optional(),

    // Files metadata JSON string - parsed by file processor
    // Format: [{ file_path: string, last_modified: number }, ...]
    files_metadata: z
      .string()
      .optional()
      .refine(
        (val) => {
          if (!val) return true;
          try {
            const parsed = JSON.parse(val);
            if (!Array.isArray(parsed)) return false;
            return parsed.every(
              (entry: any) =>
                typeof entry.file_path === 'string' &&
                typeof entry.last_modified === 'number',
            );
          } catch {
            return false;
          }
        },
        {
          message:
            'files_metadata must be a valid JSON array with { file_path, last_modified } objects',
        },
      ),
  }),
  params: z.object({
    kbId: z.string().uuid(),
    folderId: z.string().min(1),
  }),
});

export const getAllRecordsSchema = z.object({
  query: z
    .object({
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
      .refine(
        (val) => {
          if (!val) return true;
          const trimmed = val.trim();
          
          // Check for HTML tags and XSS patterns
          // Optimized to prevent ReDoS: limit quantifiers
          const htmlTagPattern = /<[^>]{0,10000}>/i;
          // Pattern matches: <script...>...</script> including </script > with spaces
          // Updated to match any characters (except >) between script and > in closing tag
          const scriptTagPattern = /<[\s]*script[\s\S]{0,10000}?>[\s\S]{0,10000}?<\/[\s]*script[^>]{0,10000}>/gi;
          // Explicit pattern for closing script tags - matches any characters (except >) between script and >
          // This catches </script >, </script\t\n bar>, </script xyz>, etc.
          // Limited to 10000 chars to prevent ReDoS
          const scriptClosingTagPattern = /<\/[\s]*script[^>]{0,10000}>/gi;
          // Optimized to prevent ReDoS: limit attribute value length
          const eventHandlerPattern = /\b(on\w+\s*=\s*["']?[^"'>]{0,1000}["']?)/i;
          const javascriptProtocolPattern = /javascript:/i;
          
          if (
            htmlTagPattern.test(trimmed) ||
            scriptTagPattern.test(trimmed) ||
            scriptClosingTagPattern.test(trimmed) ||
            eventHandlerPattern.test(trimmed) ||
            javascriptProtocolPattern.test(trimmed)
          ) {
            return false;
          }
          
          // Check for format string specifiers (e.g., %s, %x, %n, %1$s, %1!s, etc.)
          // More aggressive pattern to catch both standard and non-standard format specifiers
          // Matches: % followed by digits and ! or $, or standard format specifiers, or %digits+letter
          // Optimized to prevent ReDoS: limited quantifiers
          const formatSpecifierPattern = /%(?:\d{1,10}[!$]|[#0\-+ ]{0,10}\d{0,10}\.?\d{0,10}[diouxXeEfFgGaAcspn%]|\d{1,10}[a-zA-Z])/;
          if (formatSpecifierPattern.test(trimmed)) {
            return false;
          }
          return true;
        },
        { message: 'Search parameter contains potentially dangerous content (HTML tags, scripts, or format specifiers are not allowed)' },
      )
      .transform((val) => {
        if (!val) return undefined;
        const trimmed = val.trim();
        if (trimmed.length > 1000) {
          throw new Error('Search term too long (max 1000 characters)');
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
  })
    .strict(), // Reject unknown query parameters
});

export const getAllKBRecordsSchema = z.object({
  query: z
    .object({
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
      .refine(
        (val) => {
          if (!val) return true;
          const trimmed = val.trim();
          
          // Check for HTML tags and XSS patterns
          // Optimized to prevent ReDoS: limit quantifiers
          const htmlTagPattern = /<[^>]{0,10000}>/i;
          // Pattern matches: <script...>...</script> including </script > with spaces
          // Updated to match any characters (except >) between script and > in closing tag
          const scriptTagPattern = /<[\s]*script[\s\S]{0,10000}?>[\s\S]{0,10000}?<\/[\s]*script[^>]{0,10000}>/gi;
          // Explicit pattern for closing script tags - matches any characters (except >) between script and >
          // This catches </script >, </script\t\n bar>, </script xyz>, etc.
          // Limited to 10000 chars to prevent ReDoS
          const scriptClosingTagPattern = /<\/[\s]*script[^>]{0,10000}>/gi;
          // Optimized to prevent ReDoS: limit attribute value length
          const eventHandlerPattern = /\b(on\w+\s*=\s*["']?[^"'>]{0,1000}["']?)/i;
          const javascriptProtocolPattern = /javascript:/i;
          
          if (
            htmlTagPattern.test(trimmed) ||
            scriptTagPattern.test(trimmed) ||
            scriptClosingTagPattern.test(trimmed) ||
            eventHandlerPattern.test(trimmed) ||
            javascriptProtocolPattern.test(trimmed)
          ) {
            return false;
          }
          
          // Check for format string specifiers (e.g., %s, %x, %n, %1$s, %1!s, etc.)
          // More aggressive pattern to catch both standard and non-standard format specifiers
          // Matches: % followed by digits and ! or $, or standard format specifiers, or %digits+letter
          // Optimized to prevent ReDoS: limited quantifiers
          const formatSpecifierPattern = /%(?:\d{1,10}[!$]|[#0\-+ ]{0,10}\d{0,10}\.?\d{0,10}[diouxXeEfFgGaAcspn%]|\d{1,10}[a-zA-Z])/;
          if (formatSpecifierPattern.test(trimmed)) {
            return false;
          }
          return true;
        },
        { message: 'Search parameter contains potentially dangerous content (HTML tags, scripts, or format specifiers are not allowed)' },
      )
      .transform((val) => {
        if (!val) return undefined;
        const trimmed = val.trim();
        if (trimmed.length > 1000) {
          throw new Error('Search term too long (max 1000 characters)');
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
  query: z
    .object({
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
      .refine(
        (val) => {
          if (!val) return true;
          const trimmed = val.trim();
          
          // Check for HTML tags and XSS patterns
          // Optimized to prevent ReDoS: limit quantifiers
          const htmlTagPattern = /<[^>]{0,10000}>/i;
          // Pattern matches: <script...>...</script> including </script > with spaces
          // Updated to match any characters (except >) between script and > in closing tag
          const scriptTagPattern = /<[\s]*script[\s\S]{0,10000}?>[\s\S]{0,10000}?<\/[\s]*script[^>]{0,10000}>/gi;
          // Explicit pattern for closing script tags - matches any characters (except >) between script and >
          // This catches </script >, </script\t\n bar>, </script xyz>, etc.
          // Limited to 10000 chars to prevent ReDoS
          const scriptClosingTagPattern = /<\/[\s]*script[^>]{0,10000}>/gi;
          // Optimized to prevent ReDoS: limit attribute value length
          const eventHandlerPattern = /\b(on\w+\s*=\s*["']?[^"'>]{0,1000}["']?)/i;
          const javascriptProtocolPattern = /javascript:/i;
          
          if (
            htmlTagPattern.test(trimmed) ||
            scriptTagPattern.test(trimmed) ||
            scriptClosingTagPattern.test(trimmed) ||
            eventHandlerPattern.test(trimmed) ||
            javascriptProtocolPattern.test(trimmed)
          ) {
            return false;
          }
          
          // Check for format string specifiers (e.g., %s, %x, %n, %1$s, %1!s, etc.)
          // More aggressive pattern to catch both standard and non-standard format specifiers
          // Matches: % followed by digits and ! or $, or standard format specifiers, or %digits+letter
          // Optimized to prevent ReDoS: limited quantifiers
          const formatSpecifierPattern = /%(?:\d{1,10}[!$]|[#0\-+ ]{0,10}\d{0,10}\.?\d{0,10}[diouxXeEfFgGaAcspn%]|\d{1,10}[a-zA-Z])/;
          if (formatSpecifierPattern.test(trimmed)) {
            return false;
          }
          return true;
        },
        { message: 'Search parameter contains potentially dangerous content (HTML tags, scripts, or format specifiers are not allowed)' },
      )
      .transform((val) => {
        if (!val) return undefined;
        const trimmed = val.trim();
        if (trimmed.length > 1000) {
          throw new Error('Search term too long (max 1000 characters)');
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
  })
    .strict(), // Reject unknown query parameters
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
    role: z.enum(['OWNER', 'WRITER', 'READER', 'COMMENTER']).optional(), // Optional for teams
  }).refine((data) => (data.userIds && data.userIds.length > 0) || (data.teamIds && data.teamIds.length > 0),
    {
      message: 'At least one user or team ID is required',
      path: ['userIds'],
    },
  ).refine((data) => {
    // Role is required if users are provided
    if (data.userIds && data.userIds.length > 0) {
      return data.role !== undefined && data.role !== null;
    }
    return true;
  }, {
    message: 'Role is required when adding users',
    path: ['role'],
  }),
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
    teamIds: z.array(z.string()).optional(), // Teams don't have roles, so this will be ignored
  }).refine((data) => {
    // Only users can be updated (teams don't have roles)
    if (data.teamIds && data.teamIds.length > 0) {
      return false;
    }
    return true;
  }, {
    message: 'Teams do not have roles. Only user permissions can be updated.',
    path: ['teamIds'],
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

export const moveRecordSchema = z.object({
  body: z.object({
    newParentId: z.string().nullable(),
  }),
  params: z.object({
    kbId: z.string().uuid(),
    recordId: z.string().min(1),
  }),
});
