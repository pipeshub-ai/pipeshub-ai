import { z } from 'zod';
import { extensionToMimeType } from '../../storage/mimetypes/mimetypes';

// ==================================================================
// Request Schemas
// ==================================================================


export const recordByIdSchema = z.object({
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
    statusFilters: z.array(z.string()).optional(),
  }),
});

export const resyncSchema = z.object({
  body: z.object({
    connectorName: z.string().min(1),
    connectorId: z.string().min(1),
    fullSync: z.boolean().optional(),
  }),
});

export const connectorStatsSchema = z.object({
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

export const allRecordsSchema = z.object({
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

export const createSchema = z.object({
  body: z.object({
    kbName: z.string().min(1).max(255),
  }),
});

export const kbIdParamSchema = z.object({
  params: z.object({ kbId: z.string().min(1) }),
});

export const listSchema = z.object({
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

export const updateSchema = z.object({
  body: z.object({
    kbName: z.string().min(1).max(255).optional(),
  }),
  params: z.object({
    kbId: z.string().uuid(),
  }),
});

export const deleteSchema = z.object({
  params: z.object({ kbId: z.string().min(1) }),
});

export const createFolderSchema = z.object({
  body: z.object({
    folderName: z.string().min(1).max(255),
  }),
});

export const permissionBodySchema = z.object({
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

const hubQuerySchema = z.object({
  page: z
    .string()
    .optional()
    .refine(
      (val) => {
        if (!val) return true;
        const parsed = parseInt(val, 10);
        return !isNaN(parsed) && parsed >= 1;
      },
      { message: 'page must be a positive integer' },
    ),
  limit: z
    .string()
    .optional()
    .refine(
      (val) => {
        if (!val) return true;
        const parsed = parseInt(val, 10);
        return !isNaN(parsed) && parsed >= 1 && parsed <= 200;
      },
      { message: 'limit must be between 1 and 200' },
    ),
  sortBy: z
    .enum(['name', 'createdAt', 'updatedAt', 'size', 'type'])
    .optional(),
  sortOrder: z.enum(['asc', 'desc']).optional(),
  q: z
    .string()
    .min(2, 'Search query must be at least 2 characters')
    .max(500, 'Search query too long (max 500 characters)')
    .optional(),
  nodeTypes: z.string().optional(),
  recordTypes: z.string().optional(),
  origins: z.string().optional(),
  connectorIds: z.string().optional(),
  kbIds: z.string().optional(),
  indexingStatus: z.string().optional(),
  createdAt: z.string().optional(),
  updatedAt: z.string().optional(),
  size: z.string().optional(),
  include: z.string().optional(),
  onlyContainers: z.string().optional(),
});

export const hubNodesSchema = z.object({
  query: hubQuerySchema,
});

export const hubChildNodesSchema = z.object({
  params: z.object({
    parentType: z.enum(['app', 'recordGroup', 'folder', 'record'], {
      errorMap: () => ({
        message:
          'parentType must be one of: app, recordGroup, folder, record',
      }),
    }),
    parentId: z.string().min(1, 'parentId is required'),
  }),
  query: hubQuerySchema,
});

// ============================================================================
// Response Schema - GET /knowledge-hub/nodes  &  GET /knowledge-hub/nodes/:parentType/:parentId
// ============================================================================

const khItemPermissionSchema = z.object({
  role: z.string(),
  canEdit: z.boolean(),
  canDelete: z.boolean(),
}).strict();

const khNodeItemSchema = z.object({
  id: z.string().min(1),
  name: z.string(),
  nodeType: z.string(),               // "app" | "recordGroup" | "folder" | "record"
  parentId: z.string().nullable(),    // null for top-level nodes; "collection/id" format for children
  origin: z.string(),                 // "COLLECTION" | "CONNECTOR"
  connector: z.string(),
  recordType: z.string().nullable(),  // null for non-record nodeTypes
  recordGroupType: z.string().nullable(), // null for non-recordGroup nodeTypes
  indexingStatus: z.string().nullable(),  // null for non-record nodeTypes
  createdAt: z.number().int(),
  updatedAt: z.number().int(),
  sizeInBytes: z.number().int().nullable(),  // null for non-file records
  mimeType: z.string().nullable(),           // null for non-file records
  extension: z.string().nullable(),          // null for non-file records
  webUrl: z.string().nullable(),
  hasChildren: z.boolean(),
  previewRenderable: z.boolean().nullable(), // null for non-previewable nodes
  permission: khItemPermissionSchema.nullable(), // null unless include=permissions
  sharingStatus: z.string().nullable(),      // null for folders
  isInternal: z.boolean(),
}).strict();

const khCurrentNodeSchema = z.object({
  id: z.string().min(1),
  name: z.string(),
  nodeType: z.string(),
  subType: z.string(),
}).strict();

const khBreadcrumbItemSchema = z.object({
  id: z.string().min(1),
  name: z.string(),
  nodeType: z.string(),
  subType: z.string(),
}).strict();

const khPaginationSchema = z.object({
  page: z.number().int().min(1),
  limit: z.number().int().min(1),
  totalItems: z.number().int().min(0),
  totalPages: z.number().int().min(0),
  hasNext: z.boolean(),
  hasPrev: z.boolean(),
}).strict();

const khFilterOptionSchema = z.object({
  id: z.string(),
  label: z.string(),
  type: z.string().nullable(),          // always null currently
  connectorType: z.string().nullable(), // set only for connector entries
}).strict();

const khAvailableFiltersSchema = z.object({
  nodeTypes: z.array(khFilterOptionSchema),
  recordTypes: z.array(khFilterOptionSchema),
  origins: z.array(khFilterOptionSchema),
  connectors: z.array(khFilterOptionSchema),
  indexingStatus: z.array(khFilterOptionSchema),
  sortBy: z.array(khFilterOptionSchema),
  sortOrder: z.array(khFilterOptionSchema),
}).strict();

const khDateRangeSchema = z.object({
  gte: z.number().int().optional(),
  lte: z.number().int().optional(),
}).strict();

const khSizeRangeSchema = z.object({
  gte: z.number().int().optional(),
  lte: z.number().int().optional(),
}).strict();

const khAppliedFiltersSchema = z.object({
  q: z.string().nullable(),
  nodeTypes: z.array(z.string()).nullable(),
  recordTypes: z.array(z.string()).nullable(),
  origins: z.array(z.string()).nullable(),
  connectorIds: z.array(z.string()).nullable(),
  indexingStatus: z.array(z.string()).nullable(),
  createdAt: khDateRangeSchema.nullable(),
  updatedAt: khDateRangeSchema.nullable(),
  size: khSizeRangeSchema.nullable(),
  sortBy: z.string(),
  sortOrder: z.string(),
}).strict();

const khFiltersSchema = z.object({
  applied: khAppliedFiltersSchema,
  available: khAvailableFiltersSchema.nullable(),
}).strict();

const khCountItemSchema = z.object({
  label: z.string(),
  count: z.number().int().min(0),
}).strict();

const khCountsSchema = z.object({
  items: z.array(khCountItemSchema),
  total: z.number().int().min(0),
}).strict();

const khPermissionsSchema = z.object({
  role: z.string(),
  canUpload: z.boolean(),
  canCreateFolders: z.boolean(),
  canEdit: z.boolean(),
  canDelete: z.boolean(),
  canManagePermissions: z.boolean(),
}).strict();

// Top-level response — nullable fields are always present as explicit null (never absent)
export const hubNodesResponseSchema = z.object({
  success: z.boolean(),
  error: z.string().nullable(),                       // null on success
  id: z.string().nullable(),                          // null at root
  currentNode: khCurrentNodeSchema.nullable(),        // null at root
  parentNode: khCurrentNodeSchema.nullable(),         // null when no grandparent
  items: z.array(khNodeItemSchema),
  pagination: khPaginationSchema,
  filters: khFiltersSchema,
  breadcrumbs: z.array(khBreadcrumbItemSchema).nullable(), // null when no parentId
  counts: khCountsSchema.nullable(),                  // null unless include=counts
  permissions: khPermissionsSchema.nullable(),        // null unless include=permissions
}).strict();


// ============================================================================
// Response Schemas - list knowledge bases
// ============================================================================

const kbPaginationSchema = z.object({
  page: z.number().int().min(1),
  limit: z.number().int().min(1).max(100),
  totalCount: z.number().int().nonnegative(),
  totalPages: z.number().int().nonnegative(),
  hasNext: z.boolean(),
  hasPrev: z.boolean(),
}).strict();


// List KB folders: AQL uses RECORDS doc + belongs_to edge; create_folder always sets webUrl and edge timestamps.
const kbFolderSchema = z.object({
  id: z.string(),
  name: z.string(),
  createdAtTimestamp: z.number().int(),
  webUrl: z.string(),
}).strict();

const kbListItemSchema = z.object({
  id: z.string(),
  name: z.string(),
  connectorId: z.string(),
  createdAtTimestamp: z.number().int(),
  updatedAtTimestamp: z.number().int(),
  createdBy: z.string(),
  userRole: z.string(),
  folders: z.array(kbFolderSchema),
}).strict();

const kbListPermissionRoleSchema = z.enum([
  'OWNER',
  'ORGANIZER',
  'FILEORGANIZER',
  'WRITER',
  'COMMENTER',
  'READER',
]);

const kbListSortByFieldSchema = z.enum([
  'name',
  'createdAtTimestamp',
  'updatedAtTimestamp',
  'userRole',
]);

const kbListSortOrderSchema = z.enum(['asc', 'desc']);

const kbListAppliedFiltersSchema = z
  .object({
    search: z.string().optional(),
    permissions: z.array(kbListPermissionRoleSchema).optional(),
    sort_by: kbListSortByFieldSchema.optional(),
    sort_order: kbListSortOrderSchema.optional(),
  })
  .strict();

const kbListAvailableFiltersSchema = z
  .object({
    permissions: z.array(kbListPermissionRoleSchema),
    sortFields: z.array(z.string()),
    sortOrders: z.array(z.string()),
  })
  .strict();

// GET /api/v1/knowledgeBase/
export const listResponseSchema = z.object({
  knowledgeBases: z.array(kbListItemSchema),
  pagination: kbPaginationSchema,
  filters: z.object({
    applied: kbListAppliedFiltersSchema,
    available: kbListAvailableFiltersSchema,
  }).strict(),
}).strict();


// ============================================================================
// Response Schemas - KB CRUD
// ============================================================================

// PUT /api/v1/kb/:kbId and DELETE /api/v1/kb/:kbId
export const successResponseSchema = z.object({
  success: z.boolean(),
  message: z.string().optional(),
}).strict();

// POST /api/v1/kb/ 
export const createResponseSchema = z.object({
  id: z.string(),
  name: z.string(),
  createdAtTimestamp: z.number().int(),
  updatedAtTimestamp: z.number().int(),
  userRole: z.string(), // always "OWNER" — creator is hardcoded as OWNER in kb_service
}).strict();

// GET /api/v1/knowledgeBase/:kbId

const detailFolderItemSchema = z.object({
  id: z.string().min(1),
  name: z.string(),
  createdAtTimestamp: z.number().int().optional(), // absent when null (exclude_none on nested Pydantic model)
  webUrl: z.string().optional(),                   // absent when null (exclude_none on nested Pydantic model)
}).strict();

export const detailResponseSchema = z.object({
  id: z.string().min(1),
  name: z.string(),
  connectorId: z.string().nullable(),      // always present, null for COLLECTION-origin KBs
  createdAtTimestamp: z.number().int(),
  updatedAtTimestamp: z.number().int(),
  createdBy: z.string(),
  userRole: z.string(),                    // always present — user must have a role to access
  folders: z.array(detailFolderItemSchema), // always present, may be empty array
}).strict();

// ============================================================================
// Response Schemas - Children - get children of a knowledge base or folder
// ============================================================================

const kbRecordNullableString = z.string().nullable().optional();
const kbRecordNullableInt = z.number().nullable().optional();

/** The "container" describes what the listing is rooted at (kb or folder). */
const kbChildrenContainerSchema = z.object({
  id: z.string().min(1),
  name: z.string(),
  path: z.string(),
  type: z.string(),
  webUrl: z.string(),
  recordGroupId: z.string().min(1),
}).strict();

const kbChildrenFileRecordSchema = z.object({
  id: z.string().min(1),
  name: z.string(),
  extension: kbRecordNullableString,
  mimeType: kbRecordNullableString,
  sizeInBytes: kbRecordNullableInt,
  webUrl: kbRecordNullableString,
  path: kbRecordNullableString,
  isFile: z.boolean(),
}).strict();

/** A folder item as returned by the children AQL. */

const kbChildrenFolderSchema = z.object({
  id: z.string().min(1),
  name: z.string(),
  path: kbRecordNullableString,
  level: z.number().int(),
  parent_id: z.null(),
  webUrl: z.string(),
  recordGroupId: z.string(),
  type: z.string().min(1),
  createdAtTimestamp: z.number().int(),
  updatedAtTimestamp: z.number().int(),
  counts: z.object({
    subfolders: z.number().int().min(0),
    records: z.number().int().min(0),
    totalItems: z.number().int().min(0),
  }).strict(),
  hasChildren: z.boolean(),
}).strict();

const kbChildrenRecordSchema = z.object({
  id: z.string().min(1),
  recordName: z.string(),
  name: z.string(),
  recordType: z.string(),
  externalRecordId: z.string(),
  origin: z.string(),
  connectorName: z.string(),
  indexingStatus: z.string(),
  version: z.number().int(),
  isLatestVersion: z.boolean(),
  createdAtTimestamp: z.number().int(),
  updatedAtTimestamp: z.number().int(),
  sourceCreatedAtTimestamp: kbRecordNullableInt,
  sourceLastModifiedTimestamp: kbRecordNullableInt,
  webUrl: kbRecordNullableString,
  orgId: z.string(),
  type: z.string().min(1),
  fileRecord: kbChildrenFileRecordSchema.nullable(),
}).strict();

/** Counts returned at the root level of the response. */
const kbChildrenCountsSchema = z.object({
  folders: z.number().int().min(0),
  records: z.number().int().min(0),
  totalItems: z.number().int().min(0),
  totalFolders: z.number().int().min(0),
  totalRecords: z.number().int().min(0),
}).strict();

const kbChildrenStringArrayFilters = z.object({
  recordTypes: z.array(z.string()),
  origins: z.array(z.string()),
  connectors: z.array(z.string()),
  indexingStatus: z.array(z.string()),
}).strict();

const kbChildrenAppliedFiltersSchema = z.object({
  search: z.string().optional(),
  record_types: z.array(z.string()).optional(),
  origins: z.array(z.string()).optional(),
  connectors: z.array(z.string()).optional(),
  indexing_status: z.array(z.string()).optional(),
  sort_by: z.string(),    // always present — default "name" is not None
  sort_order: z.string(), // always present — default "asc" is not None
}).strict();

const kbChildrenUserPermissionSchema = z.object({
  role: z.string(),
  canUpload: z.boolean(),
  canCreateFolders: z.boolean(),
  canEdit: z.boolean(),
  canDelete: z.boolean(),
  canManagePermissions: z.boolean(),
}).strict();

const kbChildrenPaginationSchema = z.object({
  page: z.number().int().min(1),
  limit: z.number().int().min(1),
  totalItems: z.number().int().min(0),
  totalPages: z.number().int().min(0),
  hasNext: z.boolean(),
  hasPrev: z.boolean(),
}).strict();

// GET /knowledgeBase/:kbId/children

export const childrenResponseSchema = z.object({
  success: z.boolean(),
  container: kbChildrenContainerSchema,
  folders: z.array(kbChildrenFolderSchema),
  records: z.array(kbChildrenRecordSchema),
  level: z.number().int().min(0),
  totalCount: z.number().int().min(0),
  counts: kbChildrenCountsSchema,
  availableFilters: kbChildrenStringArrayFilters,
  paginationMode: z.string(),
  userPermission: kbChildrenUserPermissionSchema,
  pagination: kbChildrenPaginationSchema,
  filters: z.object({
    applied: kbChildrenAppliedFiltersSchema,
    available: kbChildrenStringArrayFilters,
  }).strict(),
}).strict();


const folderChildrenFolderSchema = z.object({
  id: z.string().min(1),
  name: z.string(),
  path: kbRecordNullableString,
  level: z.number().int(),
  parent_id: z.string(),
  webUrl: z.string(),
  recordGroupId: z.string(),
  type: z.string().min(1),
  createdAtTimestamp: z.number().int(),
  updatedAtTimestamp: z.number().int(),
  counts: z.object({
    subfolders: z.number().int().min(0),
    records: z.number().int().min(0),
    totalItems: z.number().int().min(0),
  }).strict(),
  hasChildren: z.boolean(),
}).strict();

const folderChildrenCountsSchema = z.object({
  folders: z.number().int().min(0),
  records: z.number().int().min(0),
  totalItems: z.number().int().min(0),
  foldersShown: z.number().int().min(0),
  recordsShown: z.number().int().min(0),
}).strict();

// GET /knowledgeBase/:kbId/folder/:folderId/children

export const folderChildrenResponseSchema = z.object({
  success: z.boolean(),
  folders: z.array(folderChildrenFolderSchema),
  records: z.array(kbChildrenRecordSchema),
  counts: folderChildrenCountsSchema,
  totalCount: z.number().int().min(0),
  availableFilters: kbChildrenStringArrayFilters,
  userPermission: kbChildrenUserPermissionSchema,
  pagination: kbChildrenPaginationSchema,
  filters: z.object({
    applied: kbChildrenAppliedFiltersSchema, // same shape — sort_by/sort_order always present
    available: kbChildrenStringArrayFilters,
  }).strict(),
}).strict();

// ============================================================================
// Response Schema - Permissions - get, create, update, delete
// ============================================================================

// GET /knowledgeBase/:kbId/permissions

const kbPermissionItemSchema = z.object({
  id: z.string().min(1),
  userId: z.string().nullable(),   // null for TEAM type
  email: z.string().nullable(),    // null for TEAM type
  name: z.string(),                 // Always set — user's display name or team name
  role: z.string().nullable(),     // null for TEAM type
  type: z.string(),
  createdAtTimestamp: z.number().int(),
  updatedAtTimestamp: z.number().int(),
}).strict();

export const listPermissionsResponseSchema = z.object({
  kbId: z.string().min(1),
  permissions: z.array(kbPermissionItemSchema),
  totalCount: z.number().int().min(0),
}).strict();

// DELETE /api/v1/knowledgeBase/:kbId/permissions

export const removePermissionResponseSchema = z.object({
  kbId: z.string().min(1),
  userIds: z.array(z.string()),
  teamIds: z.array(z.string()),
}).strict();

// PUT /api/v1/knowledgeBase/:kbId/permissions  (updateKBPermission)

export const updatePermissionResponseSchema = z.object({
  kbId: z.string().min(1),
  userIds: z.array(z.string()),
  teamIds: z.array(z.string()),
  newRole: z.string(),
}).strict();

// POST /api/v1/knowledgeBase/:kbId/permissions  (createKBPermission, status 201)

const createPermissionResultSchema = z.object({
  success: z.boolean(),
  grantedCount: z.number().int().min(0),
  grantedUsers: z.array(z.string()),
  grantedTeams: z.array(z.string()),
  role: z.string(),
  kbId: z.string().min(1),
  details: z.record(z.unknown()),
}).strict();

export const createPermissionResponseSchema = z.object({
  kbId: z.string().min(1),
  permissionResult: createPermissionResultSchema,
}).strict();

// ============================================================================
// Response Schemas - Upload - upload records
// ============================================================================

// POST /api/v1/knowledgeBase/:kbId/upload

// POST /api/v1/knowledgeBase/:kbId/folder/:folderId/upload

const uploadFileRecordSchema = z.object({
  _key: z.string().min(1),
  name: z.string(),
  extension: z.string().nullable(),
  mimeType: z.string(),
  sizeInBytes: z.number().int().min(0),
  webUrl: z.string(),
}).strict();

const uploadRecordItemSchema = z.object({
  _key: z.string().min(1),
  recordName: z.string(),
  externalRecordId: z.string(),
  recordType: z.string(),
  origin: z.string(),
  indexingStatus: z.string(),
  createdAtTimestamp: z.number().int(),
  updatedAtTimestamp: z.number().int(),
  sourceCreatedAtTimestamp: z.number().int(),
  sourceLastModifiedTimestamp: z.number().int(),
  version: z.number().int(),
  webUrl: z.string(),
  mimeType: z.string(),
  fileRecord: uploadFileRecordSchema,
}).strict();

const uploadFailedFileDetailSchema = z.object({
  fileName: z.string(),
  filePath: z.string(),
  error: z.string(),
}).strict();

export const uploadRecordsResponseSchema = z.object({
  message: z.string(),
  totalFiles: z.number().int().min(0),
  successfulFiles: z.number().int().min(0),
  failedFiles: z.number().int().min(0),
  status: z.string().min(1),
  failedFilesDetails: z.array(uploadFailedFileDetailSchema),
  records: z.array(uploadRecordItemSchema),
}).strict();

// ============================================================================
// Response Schemas - Folder - create, update, delete
// ============================================================================

// POST /api/v1/knowledgeBase/:kbId/folder

export const createFolderResponseSchema = z.object({
  id: z.string().min(1),
  name: z.string(),
  webUrl: z.string(),
}).strict();

// PUT  /api/v1/knowledgeBase/:kbId/folder/:folderId  → {"success":true,"message":"Folder updated successfully"}
// DELETE /api/v1/knowledgeBase/:kbId/folder/:folderId → {"success":true,"message":"Folder deleted successfully"}

export const folderSuccessResponseSchema = successResponseSchema;


// ============================================================================
// Response Schemas - record - get, update, delete a record
// ============================================================================


const recordByIdMetadataTagSchema = z
  .object({ id: z.string(), name: z.string() })
  .strict();

const recordByIdMetadataSchema = z
  .object({
    departments: z.array(recordByIdMetadataTagSchema),
    categories: z.array(recordByIdMetadataTagSchema),
    subcategories1: z.array(recordByIdMetadataTagSchema),
    subcategories2: z.array(recordByIdMetadataTagSchema),
    subcategories3: z.array(recordByIdMetadataTagSchema),
    topics: z.array(recordByIdMetadataTagSchema),
    languages: z.array(recordByIdMetadataTagSchema),
  })
  .strict();


const recordByIdFileRecordSchema = z
  .object({
    id: z.string(),
    _key: z.string().optional(),
    _id: z.string(),
    _rev: z.string().optional(),
    name: z.string(),
    orgId: z.string().optional(),
    isFile: z.boolean(),
    extension: kbRecordNullableString,
    etag: kbRecordNullableString,
    ctag: kbRecordNullableString,
    md5Checksum: kbRecordNullableString,
    quickXorHash: kbRecordNullableString,
    crc32Hash: kbRecordNullableString,
    sha1Hash: kbRecordNullableString,
    sha256Hash: kbRecordNullableString,
    path: kbRecordNullableString,
    mimeType: kbRecordNullableString,
    webUrl: kbRecordNullableString,
    sizeInBytes: kbRecordNullableInt,
  })
  .passthrough()


const recordByIdTicketRecordSchema = z
  .object({
    id: z.string(),
    _key: z.string().optional(),
    _id: z.string(),
    _rev: z.string().optional(),
    orgId: kbRecordNullableString,
    type: kbRecordNullableString,
    priority: kbRecordNullableString,
    status: kbRecordNullableString,
    creatorEmail: kbRecordNullableString,
    creatorName: kbRecordNullableString,
    assigneeEmail: kbRecordNullableString,
    assignee: kbRecordNullableString,
    reporterName: kbRecordNullableString,
    reporterEmail: kbRecordNullableString,
    reporter_source_id: kbRecordNullableString,
    deliveryStatus: kbRecordNullableString,
    labels: z.array(z.string()),
    assignee_source_id: z.array(z.string()),
    is_email_hidden: z.boolean(),
    assigneeSourceTimestamp: kbRecordNullableInt,
    creatorSourceTimestamp: kbRecordNullableInt,
    reporterSourceTimestamp: kbRecordNullableInt,
  })
  .passthrough()

const recordByIdMailRecordSchema = z
  .object({
    id: z.string(),
    _key: z.string().optional(),
    _id: z.string(),
    _rev: z.string().optional(),
    threadId: z.string(),
    isParent: z.boolean(),
    internalDate: z.string().optional(),
    subject: z.string().optional(),
    date: z.string().optional(),
    from: z.string().optional(),
    to: z.array(z.string()).optional(),
    cc: z.array(z.string()).optional(),
    bcc: z.array(z.string()).optional(),
    messageIdHeader: z.string().nullable().optional(),
    historyId: z.string().optional(),
    webUrl: z.string().optional(),
    labelIds: z.array(z.string()).optional(),
    conversationIndex: z.string().nullable().optional(),
  })
  .passthrough()

const recordByIdRecordSchema = z
  .object({
    id: z.string(),
    _key: z.string().optional(),
    _id: z.string(),
    _rev: z.string().optional(),
    recordName: z.string(),
    recordType: z.string(),
    origin: z.string(),
    orgId: z.string(),
    externalRecordId: z.string(),
    connectorId: z.string().min(1),
    connectorName: z.string().min(1),
    createdAtTimestamp: z.number(),
    updatedAtTimestamp: z.number(),
    version: z.number().int(),
    indexingStatus: z.string(),
    extractionStatus: z.string().optional(),
    isDeleted: z.boolean(),
    isArchived: z.boolean(),
    isVLMOcrProcessed: z.boolean().optional(),
    isDirty: z.boolean().optional(),
    isLatestVersion: z.boolean().optional(),
    previewRenderable: z.boolean().nullable().optional(),
    hideWeburl: z.boolean().optional(),
    isDependentNode: z.boolean().optional(),
    isInternal: z.boolean().optional(),
    isShared: z.boolean().nullable().optional(),
    webUrl: kbRecordNullableString,
    mimeType: kbRecordNullableString,
    externalGroupId: kbRecordNullableString,
    externalParentId: kbRecordNullableString,
    externalRootGroupId: kbRecordNullableString,
    externalRevisionId: kbRecordNullableString,
    recordGroupId: kbRecordNullableString,
    virtualRecordId: kbRecordNullableString,
    md5Checksum: kbRecordNullableString,
    reason: kbRecordNullableString,
    summaryDocumentId: kbRecordNullableString,
    deletedByUserId: kbRecordNullableString,
    parentNodeId: kbRecordNullableString,
    lastSyncTimestamp: kbRecordNullableInt,
    sourceCreatedAtTimestamp: kbRecordNullableInt,
    sourceLastModifiedTimestamp: kbRecordNullableInt,
    lastExtractionTimestamp: kbRecordNullableInt,
    lastIndexTimestamp: kbRecordNullableInt,
    sizeInBytes: kbRecordNullableInt,
    fileRecord: recordByIdFileRecordSchema.nullable(),
    mailRecord: recordByIdMailRecordSchema.nullable(),
    ticketRecord: recordByIdTicketRecordSchema.nullable(),
  })
  .passthrough();

const recordByIdKnowledgeBaseSchema = z
  .object({
    id: z.string().min(1),
    name: z.string().min(1),
    orgId: z.string().min(1),
  })
  .strict();

const recordByIdFolderSchema = z
  .object({
    id: z.string().min(1),
    name: z.string().min(1),
  })
  .strict();

const recordByIdPermissionSchema = z
  .object({
    id: z.string(),
    name: z.string(),
    type: z.string(),
    relationship: z.string(),
    accessType: z.string(),
  })
  .strict();

// GET /api/v1/records/{record_id}

export const recordByIdResponseSchema = z
  .object({
    record: recordByIdRecordSchema,
    knowledgeBase: recordByIdKnowledgeBaseSchema.nullable(),
    folder: recordByIdFolderSchema.nullable(),
    metadata: recordByIdMetadataSchema.nullable(),
    permissions: z.array(recordByIdPermissionSchema),
  })
  .strict();


const updateRecordResponseRecordSchema = z
  .object({
    _key: z.string(),
    _id: z.string(),
    _rev: z.string(),
    recordName: z.string(),
    recordType: z.string(),
    origin: z.string(),
    orgId: z.string(),
    externalRecordId: z.string(),
    connectorId: z.string().min(1),
    connectorName: z.string().min(1),
    createdAtTimestamp: z.number(),
    updatedAtTimestamp: z.number(),
    version: z.number().int(),
    indexingStatus: z.string(),
    extractionStatus: z.string().optional(),
    isDeleted: z.boolean(),
    isArchived: z.boolean(),
    isVLMOcrProcessed: z.boolean().optional(),
    isDirty: z.boolean().optional(),
    isLatestVersion: z.boolean().optional(),
    previewRenderable: z.boolean().nullable().optional(),
    hideWeburl: z.boolean().optional(),
    isDependentNode: z.boolean().optional(),
    isInternal: z.boolean().optional(),
    isShared: z.boolean().nullable().optional(),
    webUrl: kbRecordNullableString,
    mimeType: kbRecordNullableString,
    externalGroupId: kbRecordNullableString,
    externalParentId: kbRecordNullableString,
    externalRootGroupId: kbRecordNullableString,
    externalRevisionId: kbRecordNullableString,
    recordGroupId: kbRecordNullableString,
    virtualRecordId: kbRecordNullableString,
    md5Checksum: kbRecordNullableString,
    reason: kbRecordNullableString,
    summaryDocumentId: kbRecordNullableString,
    deletedByUserId: kbRecordNullableString,
    parentNodeId: kbRecordNullableString,
    lastSyncTimestamp: kbRecordNullableInt,
    sourceCreatedAtTimestamp: kbRecordNullableInt,
    sourceLastModifiedTimestamp: kbRecordNullableInt,
    lastExtractionTimestamp: kbRecordNullableInt,
    lastIndexTimestamp: kbRecordNullableInt,
    sizeInBytes: kbRecordNullableInt,
  })
  .strict();

// PUT /api/v1/records/{record_id}

export const updateRecordResponseSchema = z
  .object({
    message: z.string(),
    record: updateRecordResponseRecordSchema,
    fileUploaded: z.boolean(),
    meta: z
      .object({
        requestId: z.string(),
        timestamp: z.string(),
      })
      .strict(),
  })
  .strict();

/**
 * DELETE /api/v1/records/{record_id} (connector) body, proxied by Node deleteRecord (2xx).
 */
export const deleteRecordResponseSchema = z
  .object({
    success: z.boolean(),
    message: z.string().min(1),
    recordId: z.string().min(1),
    connector: z.string().min(1).nullable(),
    timestamp: z.union([z.number(), z.string().min(1)]).nullable(),
  })
  .strict();

// ============================================================================
// Response Schemas - Reindex - reindex a record or record group, get stats ,remove failed records and resync connector records
// ============================================================================

// POST /api/v1/records/{record_id}/reindex

export const reindexRecordResponseSchema = z
  .object({
    success: z.boolean(),
    message: z.string().min(1),
    recordId: z.string().min(1),
    recordName: z.string(),
    connector: z.string(),
    eventPublished: z.boolean(),
    userRole: z.string().min(1),
    depth: z.number().int().min(-1).max(100),
  })
  .strict();

// POST /api/v1/record-groups/{record_group_id}/reindex

export const reindexRecordGroupResponseSchema = z
  .object({
    success: z.boolean(),
    message: z.string().min(1),
    recordGroupId: z.string().min(1),
    depth: z.number().int().min(-1).max(100),
    connector: z.string().min(1),
    eventPublished: z.boolean(),
  })
  .strict();

export const statsIndexingStatusCountsSchema = z
  .object({
    NOT_STARTED: z.number().int().min(0),
    PAUSED: z.number().int().min(0),
    IN_PROGRESS: z.number().int().min(0),
    COMPLETED: z.number().int().min(0),
    FAILED: z.number().int().min(0),
    FILE_TYPE_NOT_SUPPORTED: z.number().int().min(0),
    AUTO_INDEX_OFF: z.number().int().min(0),
    EMPTY: z.number().int().min(0),
    ENABLE_MULTIMODAL_MODELS: z.number().int().min(0),
    QUEUED: z.number().int().min(0),
  })
  .strict();

const statsByRecordTypeItemSchema = z
  .object({
    recordType: z.string().min(1),
    total: z.number().int().min(0),
    indexingStatus: statsIndexingStatusCountsSchema,
  })
  .strict();

const statsDataSchema = z
  .object({
    orgId: z.string().min(1),
    connectorId: z.string().min(1),
    origin: z.string().min(1),
    stats: z
      .object({
        total: z.number().int().min(0),
        indexingStatus: statsIndexingStatusCountsSchema,
      })
      .strict(),
    byRecordType: z.array(statsByRecordTypeItemSchema),
  })
  .strict();

// GET /api/v1/stats (connector)

export const connectorStatsResponseSchema = z
  .object({
    success: z.boolean(),
    data: statsDataSchema,
  })
  .strict();

// POST /reindex-failed/connector

export const reindexFailedRecordsResponseSchema = z
  .object({
    reindexResponse: z.discriminatedUnion('success', [
      z.object({ success: z.literal(true) }).strict(),
      z.object({ success: z.literal(false), error: z.string() }).strict(),
    ]),
  })
  .strict();

// POST /resync/connector

export const resyncRecordsResponseSchema = z
  .object({
    resyncConnectorResponse: z.discriminatedUnion('success', [
      z.object({ success: z.literal(true) }).strict(),
      z.object({ success: z.literal(false), error: z.string() }).strict(),
    ]),
  })
  .strict();

// ============================================================================
// Response Schemas - Upload Limits - get upload limits
// ============================================================================

// GET /knowledgeBase/limits

export const uploadLimitsResponseSchema = z
  .object({
    maxFilesPerRequest: z.number().int().positive(),
    maxFileSizeBytes: z.number().int().positive(),
  })
  .strict();

// ============================================================================
// Response Schemas - Move Record - move record to a different location within the same KB
// ============================================================================

// PUT /api/v1/knowledgeBase/:kbId/record/:recordId/move

export const moveRecordResponseSchema = successResponseSchema;

// ============================================================================
// Inferred Response Types
// ============================================================================

export type DetailResponse = z.infer<typeof detailResponseSchema>;
export type ChildrenResponse = z.infer<typeof childrenResponseSchema>;
export type FolderChildrenResponse = z.infer<typeof folderChildrenResponseSchema>;
export type ListPermissionsResponse = z.infer<typeof listPermissionsResponseSchema>;
export type RemovePermissionResponse = z.infer<typeof removePermissionResponseSchema>;
export type CreatePermissionResponse = z.infer<typeof createPermissionResponseSchema>;
export type UpdatePermissionResponse = z.infer<typeof updatePermissionResponseSchema>;
export type UploadRecordsResponse = z.infer<typeof uploadRecordsResponseSchema>;
export type CreateFolderResponse = z.infer<typeof createFolderResponseSchema>;
export type FolderSuccessResponse = z.infer<typeof folderSuccessResponseSchema>;
export type MoveRecordResponse = z.infer<typeof moveRecordResponseSchema>;
export type SuccessResponse = z.infer<typeof successResponseSchema>;
export type CreateResponse = z.infer<typeof createResponseSchema>;
export type ListResponse = z.infer<typeof listResponseSchema>;
export type RecordByIdResponse = z.infer<typeof recordByIdResponseSchema>;
export type UpdateRecordResponse = z.infer<typeof updateRecordResponseSchema>;
export type DeleteRecordResponse = z.infer<typeof deleteRecordResponseSchema>;
export type ReindexRecordResponse = z.infer<typeof reindexRecordResponseSchema>;
export type ReindexRecordGroupResponse = z.infer<typeof reindexRecordGroupResponseSchema>;
export type ConnectorStatsResponse = z.infer<typeof connectorStatsResponseSchema>;
export type ReindexFailedRecordsResponse = z.infer<typeof reindexFailedRecordsResponseSchema>;
export type UploadLimitsResponse = z.infer<typeof uploadLimitsResponseSchema>;
export type HubNodesResponse = z.infer<typeof hubNodesResponseSchema>;
