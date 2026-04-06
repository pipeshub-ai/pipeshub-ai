import { z } from 'zod';


/**
 * Schema for toolset list query parameters
 */
export const toolsetListSchema = z.object({
  query: z.object({
    page: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().int().min(1))
      .optional(),
    limit: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().int().min(1).max(200))
      .optional(),
    search: z.string().optional(),
    include_tools: z
      .preprocess((arg) => arg === 'true', z.boolean())
      .optional(),
  }),
});

/**
 * Schema for toolset type parameter
 */
export const toolsetTypeParamSchema = z.object({
  params: z.object({
    toolsetType: z.string().min(1, 'Toolset type is required'),
  }),
});

/**
 * Schema for toolset ID parameter
 */
export const toolsetIdParamSchema = z.object({
  params: z.object({
    toolsetId: z.string().min(1, 'Toolset ID is required'),
  }),
});

/**
 * Path params for routes that only include :instanceId
 * (GET/DELETE instance, credentials, reauthenticate, status).
 */
export const toolsetInstanceIdParamSchema = z.object({
  params: z.object({
    instanceId: z.string().min(1, 'Instance ID is required'),
  }),
});

/**
 * DELETE /oauth-configs/:toolsetType/:oauthConfigId — path params only
 */
export const deleteToolsetOAuthConfigParamsSchema = z.object({
  params: z.object({
    toolsetType: z.string().min(1, 'Toolset type is required'),
    oauthConfigId: z.string().min(1, 'OAuth config ID is required'),
  }),
});

/**
 * PUT /oauth-configs/:toolsetType/:oauthConfigId — admin update OAuth config body
 */
export const updateToolsetOAuthConfigSchema = z.object({
  params: z.object({
    toolsetType: z.string().min(1, 'Toolset type is required'),
    oauthConfigId: z.string().min(1, 'OAuth config ID is required'),
  }),
  body: z.object({
    authConfig: z
      .object({
        clientId: z.string().min(1),
        clientSecret: z.string().min(1),
        tenantId: z.string().optional(),
      })
      .passthrough(),
    baseUrl: z.string().min(1),
  }),
});

/**
 * Schema for creating a toolset instance
 */
export const createToolsetInstanceSchema = z.object({
  body: z.object({
    instanceName: z.string().min(1, 'Instance name is required'),
    toolsetType: z.string().min(1, 'Toolset type is required'),
    authType: z.string().min(1, 'Auth type is required'),
    authConfig: z.record(z.unknown()).optional(),
    baseUrl: z.string().optional(),
    oauthConfigId: z.string().optional(),
    oauthInstanceName: z.string().optional(),
  }),
});

/**
 * Schema for creating a toolset
 */
export const createToolsetSchema = z.object({
  body: z.object({
    name: z.string().min(1, 'Toolset name is required'),
    displayName: z.string().optional(),
    type: z.string().optional(),
    auth: z.object({
      type: z.string().min(1, 'Auth type is required'),
      clientId: z.string().optional(),
      clientSecret: z.string().optional(),
      apiToken: z.string().optional(),
      oauthAppId: z.string().optional(),
      scopes: z.array(z.string()).optional(),
      tenantId: z.string().optional(),
    }),
    baseUrl: z.string().optional(),
  }),
});

/**
 * Schema for saving toolset configuration
 */
export const saveToolsetConfigSchema = z.object({
  body: z.object({
    auth: z.object({
      type: z.string().min(1, 'Auth type is required'),
      clientId: z.string().optional(),
      clientSecret: z.string().optional(),
      apiToken: z.string().optional(),
      oauthAppId: z.string().optional(),
      scopes: z.array(z.string()).optional(),
      tenantId: z.string().optional(),
    }),
    baseUrl: z.string().optional()
  }),
  params: z.object({
    toolsetId: z.string().min(1, 'Toolset ID is required'),
  }),
});

/**
 * Schema for getting OAuth authorization URL
 */
export const getOAuthAuthorizationUrlSchema = z.object({
  params: z.object({
    toolsetId: z.string().min(1, 'Toolset ID is required'),
  }),
  query: z.object({
    base_url: z.string().optional(),
  }),
});

/** GET /instances/:instanceId/oauth/authorize */
export const getInstanceOAuthAuthorizationUrlSchema = z.object({
  params: z.object({
    instanceId: z.string().min(1, 'Instance ID is required'),
  }),
  query: z.object({
    base_url: z.string().optional(),
  }),
});

/**
 * Schema for handling OAuth callback
 */
export const handleOAuthCallbackSchema = z.object({
  query: z.object({
    code: z.string().optional(),
    state: z.string().optional(),
    error: z.string().optional(),
    base_url: z.string().optional(),
  }),
});

/**
 * Schema for updating user's credentials for a non-OAuth toolset instance
 */
export const updateUserToolsetInstanceSchema = z.object({
  body: z.object({
    auth: z.object({
      apiToken: z.string().optional(),
      username: z.string().optional(),
      password: z.string().optional(),
    }),
  }),
  params: z.object({
    instanceId: z.string().min(1, 'Instance ID is required'),
  }),
});

/**
 * POST /instances/:instanceId/authenticate — `auth` matches connector expectations; passthrough keeps toolset-specific keys (e.g. bearerToken).
 */
export const authenticateToolsetInstanceSchema = z.object({
  params: z.object({
    instanceId: z.string().min(1, 'Instance ID is required'),
  }),
  body: z.object({
    auth: z
      .object({
        apiToken: z.string().optional(),
        username: z.string().optional(),
        password: z.string().optional(),
        bearerToken: z.string().optional(),
      })
      .passthrough(),
  }),
});

/**
 * Admin PUT /instances/:instanceId — partial body (Python: rename, oauthConfigId switch, authConfig, baseUrl).
 */
export const updateToolsetInstanceSchema = z.object({
  params: z.object({
    instanceId: z.string().min(1, 'Instance ID is required'),
  }),
  body: z.object({
    instanceName: z.string().optional(),
    baseUrl: z.string().optional(),
    oauthConfigId: z.string().optional(),
    authConfig: z.record(z.unknown()).optional(),
  }),
});

/**
 * Schema for getting my toolsets.
 * HTTP query params always arrive as strings, so numeric fields must be
 * coerced and boolean fields preprocessed — identical to toolsetListSchema.
 */
export const getMyToolsetsSchema = z.object({
  query: z.object({
    page: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().int().min(1))
      .optional(),
    limit: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().int().min(1).max(200))
      .optional(),
    search: z.string().optional(),
    includeRegistry: z
      .preprocess((arg) => arg === 'true', z.boolean())
      .optional(),
    authStatus: z
      .enum(['authenticated', 'not-authenticated'])
      .optional(),
  }),
});

/**
 * Schema for listing toolset instances (GET /instances).
 * Query params match getMyToolsetsSchema pagination/search handling.
 */
export const getToolsetInstancesSchema = z.object({
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

/**
 * Schema for getting agent-scoped toolsets (service account agents).
 * Same shape as getMyToolsetsSchema but without authStatus — agent endpoints
 * return all instances merged with agent-level auth status without server-side
 * filtering by auth state (the UI handles that client-side).
 */
export const getAgentToolsetsSchema = z.object({
  query: z.object({
    page: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().int().min(1))
      .optional(),
    limit: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().int().min(1).max(200))
      .optional(),
    search: z.string().optional(),
    includeRegistry: z
      .preprocess((arg) => arg === 'true', z.boolean())
      .optional(),
  }),
});


// ============================================================================
// Validation Schemas for api response
// ============================================================================

const registryToolParameterSchema = z.object({
  name: z.string(),
  type: z.string(),
  description: z.string(),
});

const registryToolSchema = z.object({
  name: z.string(),
  fullName: z.string(),
  displayName: z.string(),
  description: z.string(),
  parameters: z.array(registryToolParameterSchema),
  returns: z.string().nullable(),
  tags: z.array(z.string()),
});

const registryToolsetEntrySchema = z.object({
  name: z.string(),
  displayName: z.string(),
  description: z.string(),
  category: z.string(),
  group: z.string(),
  iconPath: z.string(),
  supportedAuthTypes: z.array(z.string()),
  toolCount: z.number(),
  tools: z.array(registryToolSchema),
});

export const toolsetListResponseSchema = z.object({
  status: z.literal('success'),
  toolsets: z.array(registryToolsetEntrySchema),
  categorizedToolsets: z.record(z.string(), z.array(registryToolsetEntrySchema)),
  pagination: z.object({
    page: z.number(),
    limit: z.number(),
    total: z.number(),
    totalPages: z.number(),
  }),
});

/** Single row from GET /instances (connector) — etcd fields plus registry enrichment */
const toolsetInstanceListItemSchema = z
  .object({
    _id: z.string(),
    instanceName: z.string(),
    toolsetType: z.string(),
    authType: z.string(),
    orgId: z.string(),
    createdBy: z.string(),
    createdAtTimestamp: z.number(),
    updatedAtTimestamp: z.number(),
    oauthConfigId: z.string().optional(),
    displayName: z.string(),
    description: z.string(),
    iconPath: z.string(),
    toolCount: z.number(),
  })
  .passthrough();

/** GET /instances success body from connector */
export const getToolsetInstancesResponseSchema = z.object({
  status: z.literal('success'),
  instances: z.array(toolsetInstanceListItemSchema),
  pagination: z.object({
    page: z.number(),
    limit: z.number(),
    total: z.number(),
    totalPages: z.number(),
    hasNext: z.boolean(),
    hasPrev: z.boolean(),
  }),
});

/** GET /toolsets/oauth/callback success body from connector */
export const oauthCallbackResponseSchema = z.object({
  success: z.literal(true),
  redirect_url: z.string().url(),
});

/** Stored instance document returned by POST /instances (connector) */
const toolsetInstanceStoredSchema = z
  .object({
    _id: z.string(),
    instanceName: z.string(),
    toolsetType: z.string(),
    authType: z.string(),
    orgId: z.string(),
    createdBy: z.string(),
    createdAtTimestamp: z.number(),
    updatedAtTimestamp: z.number(),
    oauthConfigId: z.string().optional(),
  })
  .passthrough();

/** POST /instances success body from connector */
export const createToolsetInstanceResponseSchema = z.object({
  status: z.literal('success'),
  instance: toolsetInstanceStoredSchema,
  message: z.string(),
});

/** PUT /instances/:instanceId success body from connector */
export const updateToolsetInstanceResponseSchema = z.object({
  status: z.literal('success'),
  instance: toolsetInstanceStoredSchema,
  message: z.string(),
  deauthenticatedUserCount: z.number(),
});

/** DELETE /instances/:instanceId success body from connector */
export const deleteToolsetInstanceResponseSchema = z.object({
  status: z.literal('success'),
  message: z.string(),
  instanceId: z.string(),
  deletedCredentialsCount: z.number(),
});

/** Tool row inside GET /my-toolsets `toolsets[].tools` */
const myToolsetToolEntrySchema = z.object({
  name: z.string(),
  fullName: z.string(),
  description: z.string(),
});

/** Single row in GET /my-toolsets `toolsets` — instance-backed or synthetic registry entry */
const myToolsetEntrySchema = z
  .object({
    instanceId: z.string(),
    instanceName: z.string(),
    toolsetType: z.string(),
    authType: z.string(),
    oauthConfigId: z.string().nullable().optional(),
    displayName: z.string(),
    description: z.string(),
    iconPath: z.string(),
    category: z.string(),
    supportedAuthTypes: z.array(z.string()),
    toolCount: z.number(),
    tools: z.array(myToolsetToolEntrySchema),
    isConfigured: z.boolean(),
    isAuthenticated: z.boolean(),
    isFromRegistry: z.boolean(),
    createdBy: z.string().optional(),
    createdAtTimestamp: z.number().optional(),
    updatedAtTimestamp: z.number().optional(),
    auth: z.record(z.unknown()).nullable().optional(), // Shape varies per toolset type and auth type.
  })
  .passthrough();

/** GET /my-toolsets success body from connector */
export const getMyToolsetsResponseSchema = z.object({
  status: z.literal('success'),
  toolsets: z.array(myToolsetEntrySchema),
  pagination: z.object({
    page: z.number(),
    limit: z.number(),
    total: z.number(),
    totalPages: z.number(),
    hasNext: z.boolean(),
    hasPrev: z.boolean(),
  }),
  filterCounts: z.object({
    all: z.number(),
    authenticated: z.number(),
    notAuthenticated: z.number(),
  }),
});

/**
 * Admin-only oauthConfig object on a toolset instance (GET /instances/:id).
 *
 * Fixed fields: _id, oauthInstanceName, clientSecretSet.
 * Core OAuth infrastructure fields are always present when an OAuth config exists.
 * Toolset-specific extras (e.g. tenantId for Microsoft) are allowed via passthrough().
 */
const instanceOAuthConfigSchema = z
  .object({
    _id: z.string(),
    oauthInstanceName: z.string(),
    clientId: z.string(),
    clientSecret: z.string(),
    clientSecretSet: z.boolean().optional(),
    authorizeUrl: z.string(),
    tokenUrl: z.string(),
    redirectUri: z.string(),
    scopes: z.array(z.string()),
    additionalParams: z.record(z.unknown()).optional(),
  })
  .passthrough();

/** GET /instances/:id — stored instance plus registry enrichment; optional admin-only OAuth fields */
const toolsetInstanceDetailSchema = z
  .object({
    _id: z.string(),
    instanceName: z.string(),
    toolsetType: z.string(),
    authType: z.string(),
    orgId: z.string(),
    createdBy: z.string(),
    createdAtTimestamp: z.number(),
    updatedAtTimestamp: z.number(),
    oauthConfigId: z.string().optional(),
    displayName: z.string(),
    description: z.string(),
    iconPath: z.string(),
    supportedAuthTypes: z.array(z.string()),
    toolCount: z.number(),
    auth: z.record(z.unknown()).optional(),
    oauthConfig: instanceOAuthConfigSchema.optional(),
    authenticatedUserCount: z.number().optional(),
  })
  .passthrough();

/** GET /instances/:instanceId success body from connector */
export const getToolsetInstanceResponseSchema = z.object({
  status: z.literal('success'),
  instance: toolsetInstanceDetailSchema,
});

/**
 * Tool row in GET /registry/:toolsetType/schema — same parameter shape as registry list tools,
 * but without fullName/displayName on the tool itself.
 */
const toolsetSchemaToolEntrySchema = z.object({
  name: z.string(),
  description: z.string(),
  parameters: z.array(registryToolParameterSchema),
  returns: z.string().nullable(),
  examples: z.array(z.unknown()).optional(),
  tags: z.array(z.string()),
});

/**
 * A single auth field as serialized by auth_field_to_dict() in Python.
 * Shape is fixed regardless of toolset or auth type.
 */
const toolsetAuthFieldSchema = z.object({
  name: z.string(),
  displayName: z.string(),
  placeholder: z.string(),
  description: z.string(),
  fieldType: z.string(),
  required: z.boolean(),
  usage: z.enum(['CONFIGURE', 'AUTHENTICATE', 'BOTH']),
  defaultValue: z.unknown(),
  validation: z.object({
    minLength: z.number(),
    maxLength: z.number(),
  }),
  isSecret: z.boolean(),
});

/**
 * One entry under config.auth.schemas keyed by auth type (e.g. "API_TOKEN", "BASIC_AUTH", "OAUTH").
 */
const toolsetAuthSchemaEntrySchema = z.object({
  fields: z.array(toolsetAuthFieldSchema),
  redirectUri: z.string().optional(),
  displayRedirectUri: z.boolean().optional(),
}).passthrough();

/**
 * The config object on a toolset schema response.
 * auth.schemas is keyed by auth type; extra top-level config keys are allowed via passthrough.
 */
const toolsetConfigSchema = z.object({
  auth: z.object({
    schemas: z.record(toolsetAuthSchemaEntrySchema),
  }).passthrough(),
}).passthrough();

/**
 * The oauthConfig field returned by OAuthConfigRegistry.get_metadata() —
 * display-only metadata (no secrets).
 */
const toolsetOAuthConfigMetadataSchema = z.object({
  iconPath: z.string(),
  appGroup: z.string(),
  appDescription: z.string(),
  appCategories: z.array(z.string()),
}).passthrough();

/** GET /registry/:toolsetType/schema success body from connector */
export const getToolsetSchemaResponseSchema = z.object({
  status: z.literal('success'),
  toolset: z
    .object({
      name: z.string(),
      displayName: z.string(),
      description: z.string(),
      category: z.string(),
      supportedAuthTypes: z.array(z.string()),
      config: toolsetConfigSchema,
      oauthConfig: toolsetOAuthConfigMetadataSchema.nullable(),
      tools: z.array(toolsetSchemaToolEntrySchema),
    })
    .passthrough(),
});

/** POST /instances/:id/authenticate success body from connector */
export const authenticateToolsetInstanceResponseSchema = z.object({
  status: z.literal('success'),
  message: z.string(),
  isAuthenticated: z.boolean(),
});

/** PUT /instances/:id/credentials success body from connector */
export const updateUserToolsetCredentialsResponseSchema = z.object({
  status: z.literal('success'),
  message: z.string(),
});

/** DELETE /instances/:id/credentials success body from connector */
export const removeToolsetCredentialsResponseSchema = z.object({
  status: z.literal('success'),
  message: z.string(),
});

/** POST /instances/:id/reauthenticate success body from connector */
export const reauthenticateToolsetInstanceResponseSchema = z.object({
  status: z.literal('success'),
  message: z.string(),
});

/** GET /instances/:id/oauth/authorize success body from connector */
export const getInstanceOAuthAuthorizationUrlResponseSchema = z.object({
  success: z.literal(true),
  authorizationUrl: z.string(),
  state: z.string(),
});

/** GET /instances/:id/status success body from connector */
export const getInstanceStatusResponseSchema = z.object({
  status: z.literal('success'),
  instanceId: z.string(),
  instanceName: z.string().nullable(),
  toolsetType: z.string().nullable(),
  authType: z.string().nullable(),
  isConfigured: z.boolean(),
  isAuthenticated: z.boolean(),
});

/** Single OAuth config entry from GET /toolsets/oauth-configs/:toolsetType (connector) */
const toolsetOAuthConfigItemSchema = z.object({
  _id: z.string(),
  oauthInstanceName: z.string(),
  toolsetType: z.string(),
  userId: z.string(),
  orgId: z.string(),
  createdAtTimestamp: z.number(),
  updatedAtTimestamp: z.number(),
  tenantId: z.string().optional(),
  clientId: z.string(),
  clientSecret: z.string(),
  redirectUri: z.string(),
  authorizeUrl: z.string(),
  tokenUrl: z.string(),
  scopes: z.array(z.string()),
  additionalParams: z.record(z.string()).nullable().optional(),
  clientSecretSet: z.boolean(),
});

/** GET /toolsets/oauth-configs/:toolsetType success body from connector */
export const listToolsetOAuthConfigsResponseSchema = z.object({
  status: z.literal('success'),
  oauthConfigs: z.array(toolsetOAuthConfigItemSchema),
  total: z.number(),
});

/** PUT /oauth-configs/:toolsetType/:oauthConfigId success body from connector */
export const updateToolsetOAuthConfigResponseSchema = z.object({
  status: z.literal('success'),
  oauthConfigId: z.string(),
  message: z.string(),
  deauthenticatedUserCount: z.number(),
});

/** DELETE /oauth-configs/:toolsetType/:oauthConfigId success body from connector */
export const deleteToolsetOAuthConfigResponseSchema = z.object({
  status: z.literal('success'),
  message: z.string(),
});
