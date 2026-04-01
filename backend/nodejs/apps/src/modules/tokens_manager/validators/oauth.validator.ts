import { z } from 'zod';

// ============================================================================
// Shared Schemas
// ============================================================================

const oauthConfigBodySchema = z
  .object({
    clientId: z.string().min(1, 'Client ID is required'),
    clientSecret: z.string().min(1, 'Client Secret is required'),
  })
  .catchall(z.unknown());

// ============================================================================
// Request Schemas
// ============================================================================

export const oauthConfigRegistrySchema = z.object({
  query: z.object({
    page: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().int().min(1))
      .optional(),
    limit: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().int().min(1).max(200))
      .optional(),
    search: z.string().optional(),
  }),
});

export const oauthConfigRegistryByTypeSchema = z.object({
  params: z.object({
    connectorType: z.string().min(1, 'Connector type is required'),
  }),
});

export const getAllOAuthConfigsSchema = z.object({
  query: z.object({
    page: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().int().min(1))
      .optional(),
    limit: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().int().min(1).max(200))
      .optional(),
    search: z.string().optional(),
  }),
});

export const oauthConfigListSchema = z.object({
  params: z.object({
    connectorType: z.string().min(1, 'Connector type is required'),
  }),
  query: z.object({
    page: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().int().min(1))
      .optional(),
    limit: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().int().min(1).max(200))
      .optional(),
    search: z.string().optional(),
  }),
});

export const createOAuthConfigSchema = z.object({
  params: z.object({
    connectorType: z.string().min(1, 'Connector type is required'),
  }),
  body: z.object({
    oauthInstanceName: z.string().min(1, 'OAuth instance name is required'),
    config: oauthConfigBodySchema,
    baseUrl: z.string().url('baseUrl must be a valid URL'),
  }),
});

export const oauthConfigIdSchema = z.object({
  params: z.object({
    connectorType: z.string().min(1, 'Connector type is required'),
    configId: z.string().min(1, 'Config ID is required'),
  }),
});

export const updateOAuthConfigSchema = z.object({
  params: z.object({
    connectorType: z.string().min(1, 'Connector type is required'),
    configId: z.string().min(1, 'Config ID is required'),
  }),
  body: z.object({
    oauthInstanceName: z.string().min(1, 'OAuth instance name is required'),
    config: oauthConfigBodySchema,
    baseUrl: z.string().url('baseUrl must be a valid URL'),
  }),
});

// ============================================================================
// Response Schemas
// ============================================================================

// --- Pagination ---

const paginationWithCursorSchema = z.object({
  page: z.number().int(),
  limit: z.number().int(),
  search: z.string().nullable(),
  totalCount: z.number().int(),
  totalPages: z.number().int(),
  hasPrev: z.boolean(),
  hasNext: z.boolean(),
  prevPage: z.number().int().nullable(),
  nextPage: z.number().int().nullable(),
});

const paginationSchema = z.object({
  page: z.number().int(),
  limit: z.number().int(),
  search: z.string().nullable(),
  totalItems: z.number().int(),
  totalPages: z.number().int(),
  hasNext: z.boolean(),
  hasPrev: z.boolean(),
});

// --- Registry connector ---

const authFieldSchema = z.object({
  name: z.string(),
  displayName: z.string(),
  placeholder: z.string(),
  description: z.string(),
  fieldType: z.string(),
  required: z.boolean(),
  usage: z.string(),
  defaultValue: z.string(),
  validation: z.object({
    minLength: z.number().int(),
    maxLength: z.number().int(),
  }),
  isSecret: z.boolean(),
});

const documentationLinkSchema = z.object({
  title: z.string(),
  url: z.string(),
  type: z.string(),
});

const registryConnectorSchema = z.object({
  name: z.string(),
  type: z.string(),
  appGroup: z.string(),
  authType: z.string(),
  appDescription: z.string(),
  appCategories: z.array(z.string()),
  iconPath: z.string(),
  oauthScopes: z.object({
    personalSync: z.array(z.string()),
    teamSync: z.array(z.string()),
    agent: z.array(z.string()),
  }),
  redirectUri: z.string(),
  authorizeUrl: z.string(),
  tokenUrl: z.string(),
  additionalParams: z.record(z.string(), z.string()).optional(),
  authFields: z.array(authFieldSchema),
  documentationLinks: z.array(documentationLinkSchema).optional(),
});

// --- OAuth config items ---

const oauthConfigResponseBodySchema = z
  .object({
    clientId: z.string(),
    clientSecret: z.string(),
  })
  .catchall(z.string());

const oauthConfigSummarySchema = z.object({
  _id: z.string(),
  oauthInstanceName: z.string(),
  iconPath: z.string(),
  appGroup: z.string(),
  appDescription: z.string(),
  appCategories: z.array(z.string()),
  connectorType: z.string(),
  createdAtTimestamp: z.number(),
  updatedAtTimestamp: z.number(),
});

const oauthConfigWithCredentialsSchema = oauthConfigSummarySchema.extend({
  config: oauthConfigResponseBodySchema,
});

const oauthConfigDetailSchema = oauthConfigWithCredentialsSchema.extend({
  userId: z.string(),
  orgId: z.string(),
  authorizeUrl: z.string(),
  tokenUrl: z.string(),
  redirectUri: z.string(),
  scopes: z.object({
    personal_sync: z.array(z.string()),
    team_sync: z.array(z.string()),
    agent: z.array(z.string()),
  }),
});

// --- Exported response schemas ---

/** GET /registry */
export const getOAuthConfigRegistryResponseSchema = z.object({
  success: z.literal(true),
  connectors: z.array(registryConnectorSchema),
  pagination: paginationWithCursorSchema,
});

/** GET /registry/:connectorType */
export const getOAuthConfigRegistryByTypeResponseSchema = z.object({
  success: z.literal(true),
  connector: registryConnectorSchema,
});

/** GET / */
export const getAllOAuthConfigsResponseSchema = z.object({
  success: z.literal(true),
  oauthConfigs: z.array(oauthConfigSummarySchema),
  pagination: paginationSchema,
});

/** GET /:connectorType */
export const listOAuthConfigsResponseSchema = z.object({
  success: z.literal(true),
  oauthConfigs: z.array(oauthConfigDetailSchema),
  pagination: paginationWithCursorSchema,
});

/** POST /:connectorType */
export const createOAuthConfigResponseSchema = z.object({
  success: z.literal(true),
  oauthConfig: oauthConfigSummarySchema,
  message: z.string(),
});

/** GET /:connectorType/:configId */
export const getOAuthConfigResponseSchema = z.object({
  success: z.literal(true),
  oauthConfig: oauthConfigWithCredentialsSchema,
});

/** PUT /:connectorType/:configId */
export const updateOAuthConfigResponseSchema = z.object({
  success: z.literal(true),
  oauthConfig: oauthConfigSummarySchema,
  message: z.string(),
});

/** DELETE /:connectorType/:configId */
export const deleteOAuthConfigResponseSchema = z.object({
  success: z.literal(true),
  message: z.string(),
});
