/**
 * Connector Validators
 *
 * Zod validation schemas for connector routes.
 * Handles request validation for connector instance management,
 * configuration, OAuth, and filter operations.
 *
 * @module connectors/validators
 */

import { z } from 'zod';

// ============================================================================
// Common Schemas (Reusable)
// ============================================================================

/**
 * Connector scope enum (single value - used for instances)
 */
const scopeSchema = z.enum(['team', 'personal']);

/**
 * Connector scope array (used in registry where scope can be multiple values)
 */
const scopeArraySchema = z.array(scopeSchema);

/**
 * Registry scope - can be either single string or array depending on query params
 * When scope query param is passed: returns single string (e.g., "team")
 * When no scope query param: returns array (e.g., ["team", "personal"])
 */
const registryScopeSchema = z.union([scopeSchema, scopeArraySchema]);

/**
 * Connector ID parameter validation
 */
const connectorIdParam = z.object({
  connectorId: z.string().min(1, 'Connector ID is required'),
});

// ============================================================================
// Instance Management Schemas
// ============================================================================

/**
 * Schema for creating a new connector instance
 */
export const createConnectorInstanceSchema = z.object({
  body: z.object({
    connectorType: z.string().min(1, 'Connector type is required'),
    instanceName: z.string().min(1, 'Instance name is required'),
    config: z
      .object({
        auth: z.any().optional(),
        sync: z.any().optional(),
        filters: z.any().optional(),
      })
      .optional(),
    baseUrl: z.string().optional(),
    scope: scopeSchema.refine((val) => val === 'team' || val === 'personal', {
      message: 'Scope must be either team or personal',
    }),
    authType: z.string().optional(),
  }),
});

/**
 * Schema for validating connectorId parameter
 */
export const connectorIdParamSchema = z.object({
  params: connectorIdParam,
});

/**
 * Schema for validating connector list query parameters
 */
export const connectorListSchema = z.object({
  query: z.object({
    scope: scopeSchema
      .refine((val) => val === 'team' || val === 'personal', {
        message: 'Scope must be either team or personal',
      })
      .optional(),
    page: z
      .preprocess(
        (arg) => (arg === '' || arg === undefined ? undefined : Number(arg)),
        z.number().int().min(1).optional(),
      )
      .optional(),
    limit: z
      .preprocess(
        (arg) => (arg === '' || arg === undefined ? undefined : Number(arg)),
        z.number().int().min(1).max(200).optional(),
      )
      .optional(),
    search: z.string().optional(),
  }),
});

/**
 * Schema for validating connector type parameter
 */
export const connectorTypeParamSchema = z.object({
  params: z.object({
    connectorType: z.string().min(1, 'Connector type is required'),
  }),
});

/**
 * Schema for updating connector instance name
 */
export const updateConnectorInstanceNameSchema = z.object({
  body: z.object({
    instanceName: z.string().min(1, 'Instance name is required'),
  }),
  params: connectorIdParam,
});

// ============================================================================
// Configuration Schemas
// ============================================================================

/**
 * Schema for updating connector instance auth configuration
 */
export const updateConnectorInstanceAuthConfigSchema = z.object({
  body: z.object({
    auth: z.any().refine((val) => val !== undefined && val !== null, {
      message: 'Auth configuration is required',
    }),
    baseUrl: z.string().optional(),
  }),
  params: connectorIdParam,
});

/**
 * Schema for updating connector instance filters and sync configuration
 */
export const updateConnectorInstanceFiltersSyncConfigSchema = z.object({
  body: z.object({
    sync: z.any().optional(),
    filters: z.any().optional(),
  }),
  params: connectorIdParam,
});

// ============================================================================
// OAuth Schemas
// ============================================================================

/**
 * Schema for getting OAuth authorization URL
 */
export const getOAuthAuthorizationUrlSchema = z.object({
  params: connectorIdParam,
  query: z.object({
    baseUrl: z.string().optional(),
  }),
});

/**
 * Schema for handling OAuth callback
 */
export const handleOAuthCallbackSchema = z.object({
  query: z.object({
    baseUrl: z.string().optional(),
    code: z.string().optional(),
    state: z.string().optional(),
    error: z.string().optional(),
  }),
});

// ============================================================================
// Filter Schemas
// ============================================================================

/**
 * Schema for getting filter field options (dynamic with pagination)
 */
export const getFilterFieldOptionsSchema = z.object({
  params: z.object({
    connectorId: z.string().min(1, 'Connector ID is required'),
    filterKey: z.string().min(1, 'Filter key is required'),
  }),
  query: z.object({
    page: z
      .preprocess(
        (arg) => (arg === '' || arg === undefined ? undefined : Number(arg)),
        z.number().int().min(1).optional(),
      )
      .optional(),
    limit: z
      .preprocess(
        (arg) => (arg === '' || arg === undefined ? undefined : Number(arg)),
        z.number().int().min(1).max(200).optional(),
      )
      .optional(),
    search: z.string().optional(),
    cursor: z.string().optional(),
  }),
});

// ============================================================================
// Toggle Schema
// ============================================================================

/**
 * Schema for validating connector toggle type parameter
 */
export const connectorToggleSchema = z.object({
  body: z.object({
    type: z.enum(['sync', 'agent']),
    fullSync: z.boolean().optional(),
  }),
  params: connectorIdParam,
});




// ============================================================================
// Response Schemas - Shared Primitives
// ============================================================================

/**
 * Base success response schema - reused across many endpoints
 */
export const connectorSuccessSchema = z.object({
  success: z.boolean(),
});

/**
 * POST /:connectorId/toggle response schema
 */
export const connectorToggleResponseSchema = connectorSuccessSchema.extend({
  message: z.string(),
}).strict();

export const paginationResponseSchema = z.object({
  page: z.number(),
  limit: z.number(),
  search: z.string().nullable(),
  totalCount: z.number(),
  totalPages: z.number(),
  hasPrev: z.boolean(),
  hasNext: z.boolean(),
  prevPage: z.number().nullable(),
  nextPage: z.number().nullable(),
}).strict();

export const scopeCountsResponseSchema = z.object({
  personal: z.number(),
  team: z.number(),
}).strict();

// ============================================================================
// Response Schemas - Config Building Blocks
// ============================================================================


const fieldValidationSchema = z.object({
  minLength: z.number().optional(),
  maxLength: z.number().optional(),
}).strict();

const authFieldSchema = z.object({
  name: z.string(),
  displayName: z.string(),
  placeholder: z.string(),       // always set (default "")
  description: z.string(),       // always set (default "")
  fieldType: z.string(),
  required: z.boolean(),
  usage: z.string(),             // always set (default "BOTH")
  defaultValue: z.any(),
  validation: fieldValidationSchema, // always set with both minLength and maxLength
  isSecret: z.boolean(),         // always set
}).strict();

const oauthConfigSchema = z.object({
  authorizeUrl: z.string(),       // always set (lines 222-227 in connector_builder.py)
  tokenUrl: z.string(),           // always set
  scopes: z.array(z.string()),    // always set
  redirectUri: z.string(),        // always set
}).strict();

const authSchemaEntrySchema = z.object({
  fields: z.array(authFieldSchema),          // always set (initialized as [] then fields appended)
  redirectUri: z.string().optional(),        // only set when oauth_config.redirect_uri is truthy
  displayRedirectUri: z.boolean().optional(), // only set alongside redirectUri
}).strict();

/**
 * Auth config schema
 */
const authConfigResponseSchema = z.object({
  supportedAuthTypes: z.array(z.string()), // always set
  schemas: z.record(authSchemaEntrySchema), // always set (may be {})
  oauthConfigs: z.record(oauthConfigSchema).optional(), // stripped by _clean_schema_for_response() on schema endpoint; present on registry list endpoint
  values: z.record(z.any()),               // always set (may be {})
  customFields: z.array(z.any()),          // always set (may be [])
  customValues: z.record(z.any()),         // always set (may be {})
  conditionalDisplay: z.record(z.any()),   // always set (may be {})
}).strict();

const webhookConfigSchema = z.object({
  supported: z.boolean(),
  webhookUrl: z.string().optional(),
  events: z.array(z.string()).optional(),
  verificationToken: z.string().optional(),
  secretKey: z.string().optional(),
}).strict();


const scheduledConfigSchema = z.object({
  intervalMinutes: z.number(),   // always set (default 60)
  cronExpression: z.string(),    // always set (default "")
  timezone: z.string(),          // always set (default "UTC")
  startTime: z.number(),         // always set (default 0)
  nextTime: z.number(),          // always set (default 0)
  endTime: z.number(),           // always set (default 0)
  maxRepetitions: z.number(),    // always set (default 0)
  repetitionCount: z.number(),   // always set (default 0)
}).strict();

const realtimeConfigSchema = z.object({
  supported: z.boolean(),
  connectionType: z.string(),    // always set (default "WEBSOCKET")
}).strict();


const syncCustomFieldSchema = z.object({
  name: z.string(),
  displayName: z.string(),
  description: z.string(),       // always set (default "")
  fieldType: z.string(),
  required: z.boolean(),         // always set (default False)
  defaultValue: z.any(),
  validation: fieldValidationSchema, // always set (empty or with minLength/maxLength)
  isSecret: z.boolean(),         // always set (default False)
  nonEditable: z.boolean(),      // always set (default False)
  options: z.array(z.string()).optional(), // conditional: only when field.options is non-empty
}).strict();

/**
 * Sync config schema
 */
const syncConfigResponseSchema = z.object({
  supportedStrategies: z.array(z.string()), // always set
  selectedStrategy: z.string(),             // always set (default "MANUAL")
  webhookConfig: webhookConfigSchema,        // always set (initialized in _reset())
  scheduledConfig: scheduledConfigSchema,    // always set (initialized in _reset())
  realtimeConfig: realtimeConfigSchema,      // always set (initialized in _reset())
  customFields: z.array(syncCustomFieldSchema), // always set (default [])
  customValues: z.record(z.any()),           // always set (default {})
  values: z.record(z.any()),                 // always set (default {})
}).strict();


const filterOptionSchema = z.union([
  z.object({
    id: z.string(),
    label: z.string(),
  }).strict(),
  z.string(),
]);

const filterFieldSchema = z.object({
  name: z.string(),
  displayName: z.string(),
  description: z.string(),          // always set (default "")
  filterType: z.string(),
  category: z.string(),             // always set (default FilterCategory.SYNC)
  required: z.boolean(),            // always set (default False)
  defaultValue: z.any().nullable(), // always set, can be None
  defaultOperator: z.string().nullable(), // always set, can be None → no .optional()
  operators: z.array(z.string()),   // always set (from get_operators_for_type())
  optionSourceType: z.string(),     // always set (default OptionSourceType.MANUAL)
  options: z.array(filterOptionSchema).optional(), // conditional: only if self.options is truthy
}).strict();

const filterCategorySchema = z.object({
  schema: z.object({
    fields: z.array(filterFieldSchema),
  }).strict(),
  values: z.record(z.any()).optional(),
}).strict();

// ============================================================================
// Stored Filter Value Schemas (for saved connector config)
// ============================================================================

const filterValueSchema = z.union([
  z.boolean(),
  z.string(),
  z.number(),
  z.array(z.union([
    z.string(),
    z.object({
      id: z.string(),
      label: z.string(),
    }).strict(),
  ])),
  z.object({
    start: z.number().nullable(), // always present, null when not set
    end: z.number().nullable(),   // always present, null when not set
  }).strict(),
  z.null(),
]);

const storedFilterValueItemSchema = z.object({
  operator: z.string(),
  value: filterValueSchema,
  type: z.string(),
}).strict();

const storedFilterCategorySchema = z.object({
  values: z.record(storedFilterValueItemSchema),
}).strict();

const storedFiltersConfigSchema = z.object({
  sync: storedFilterCategorySchema.optional(),
  indexing: storedFilterCategorySchema.optional(),
}).strict();

const storedAuthConfigSchema = z.object({
  authType: z.string(),
  connectorType: z.string(),
  connectorScope: z.string(),
  oauthInstanceName: z.string().optional(),
  oauthConfigId: z.string().optional(),
  redirectUri: z.string().optional(),
  scopes: z.array(z.string()).optional(),
  authorizeUrl: z.string().optional(),
  tokenUrl: z.string().optional(),
}).passthrough();


const scheduledSyncConfigSchema = z
  .object({
    intervalMinutes: z.number(),
    timezone: z.string(),
  })
  .strict();

/** Stored sync section: may be `{}` until configured; passthrough allows registry fields (e.g. webhookConfig). */
const storedSyncConfigSchema = z
  .object({
    selectedStrategy: z.string().optional(),
    scheduledConfig: scheduledSyncConfigSchema.optional(),
  })
  .passthrough();

/** GET /config always includes nested `config` with these three keys (Python defaults missing etcd to empty objects). */
const emptyObjectSchema = z.object({}).strict();

const storedConnectorConfigSchema = z
  .object({
    auth: z.union([emptyObjectSchema, storedAuthConfigSchema]),
    sync: storedSyncConfigSchema,
    filters: storedFiltersConfigSchema,
  })
  .strict();

/**
 * GET /:connectorId/config response schema
 */
export const connectorInstanceConfigResponseSchema = connectorSuccessSchema.extend({
  config: z.object({
    connector_id: z.string(),
    name: z.string(),                     // always present, instanceName required at creation
    type: z.string(),
    appGroup: z.string(),
    authType: z.string(),                 // always present, authType required at creation
    scope: z.string(),
    createdBy: z.string(),     // always present, null for system-created records
    updatedBy: z.string(),     // always present, null until first update
    appDescription: z.string(),
    appCategories: z.array(z.string()),
    supportsRealtime: z.boolean(),
    supportsSync: z.boolean(),
    supportsAgent: z.boolean(),
    iconPath: z.string(),
    config: storedConnectorConfigSchema,
    isActive: z.boolean(),
    isConfigured: z.boolean(),
    isAuthenticated: z.boolean(),
    createdAtTimestamp: z.number(),
    updatedAtTimestamp: z.number(),
  }).strict(),
}).strict();

const storedCredentialsSchema = z.object({
  access_token: z.string(),
  token_type: z.string(),
  expires_in: z.number().nullable(),
  refresh_token: z.string().nullable(),
  refresh_token_expires_in: z.number().nullable(),
  scope: z.string().nullable(),
  id_token: z.string().nullable(),
  created_at: z.string(),
  uid: z.string().nullable(),
  account_id: z.string().nullable(),
  team_id: z.string().nullable(),
}).strict();

const storedOAuthSchema = z.object({
  used_codes: z.array(z.string()).optional(),
}).passthrough();

const configUpdateResponseConfigSchema = z
  .object({
    auth: storedAuthConfigSchema.optional(),
    sync: storedSyncConfigSchema.optional(),
    filters: storedFiltersConfigSchema.optional(),
    credentials: storedCredentialsSchema.nullable().optional(),
    oauth: storedOAuthSchema.nullable().optional(),
  })
  .strict();

/**
 * PUT /:connectorId/config/auth response schema
 */
export const connectorAuthConfigUpdateResponseSchema = connectorSuccessSchema.extend({
  config: configUpdateResponseConfigSchema,
  message: z.string(),
}).strict();

/**
 * PUT /:connectorId/config/filters-sync response schema
 */
export const connectorFiltersSyncConfigUpdateResponseSchema = connectorSuccessSchema.extend({
  config: configUpdateResponseConfigSchema,
  message: z.string(),
  syncFiltersChanged: z.boolean(),
}).strict();

/**
 * PUT /:connectorId/name response schema
 */
export const connectorNameUpdateResponseSchema = connectorSuccessSchema.extend({
  connector: z.object({
    _key: z.string(),
    name: z.string(),
  }).strict(),
}).strict();

/**
 * GET /:connectorId/filters/:filterKey/options response schema
 */
export const connectorFilterFieldOptionsResponseSchema = connectorSuccessSchema.extend({
  options: z.array(z.object({
    id: z.string(),
    label: z.string(),
  }).strict()),
  page: z.number(),
  limit: z.number(),
  hasMore: z.boolean(),
  cursor: z.string().nullable().optional(),
  message: z.string().optional(),
}).strict();

/**
 * GET /:connectorId/oauth/authorize response schema
 */
export const connectorOAuthAuthorizeResponseSchema = connectorSuccessSchema.extend({
  authorizationUrl: z.string(),
  state: z.string(),
}).strict();

/**
 * GET /oauth/callback response schema (from Python backend)
 */
export const connectorOAuthCallbackResponseSchema = connectorSuccessSchema.extend({
  redirect_url: z.string(),
}).strict();

const filtersConfigResponseSchema = z.object({
  sync: filterCategorySchema.optional(),
  indexing: filterCategorySchema.optional(),
}).strict();

const documentationLinkSchema = z.object({
  title: z.string(),
  url: z.string(),
  type: z.string().optional(),
}).strict();

const connectorConfigResponseSchema = z.object({
  iconPath: z.string(),
  supportsRealtime: z.boolean(),
  supportsSync: z.boolean(),
  supportsAgent: z.boolean(),
  documentationLinks: z.array(documentationLinkSchema),
  hideConnector: z.boolean(),
  auth: authConfigResponseSchema,
  sync: syncConfigResponseSchema,
  filters: filtersConfigResponseSchema,
}).strict();

// ============================================================================
// Response Schemas - Registry
// ============================================================================

export const connectorRegistryItemSchema = z.object({
  name: z.string(),
  type: z.string(),
  appGroup: z.string(),
  supportedAuthTypes: z.array(z.string()),
  appDescription: z.string(),
  appCategories: z.array(z.string()),
  iconPath: z.string(),
  supportsRealtime: z.boolean(),
  supportsSync: z.boolean(),
  supportsAgent: z.boolean(),
  config: connectorConfigResponseSchema,
  scope: registryScopeSchema,
  connectorInfo: z.string().nullable().optional(),
}).strict();

/**
 * GET /registry response schema
 */
export const connectorRegistryResponseSchema = connectorSuccessSchema.extend({
  connectors: z.array(connectorRegistryItemSchema),
  pagination: paginationResponseSchema,
  registryCountsByScope: scopeCountsResponseSchema.optional(),
}).strict();

/**
 * GET /registry/:connectorType/schema response schema
 */
export const connectorSchemaResponseSchema = connectorSuccessSchema.extend({
  schema: connectorConfigResponseSchema,
}).strict();

// ============================================================================
// Response Schemas - Connector Instances
// ============================================================================

export const connectorInstanceItemSchema = connectorRegistryItemSchema.extend({
  scope: scopeSchema,
  _key: z.string(),                  // always set: str(uuid4()) at creation
  authType: z.string(),              // always set: validated non-null at creation (ValueError if missing)
  isActive: z.boolean(),
  isAgentActive: z.boolean(),
  isConfigured: z.boolean(),
  isAuthenticated: z.boolean(),
  isLocked: z.boolean(),
  status: z.string().nullable(),     // null in normal state; set to e.g. "DELETING" during lock operations
  createdAtTimestamp: z.number(),    // always set: get_epoch_timestamp_in_ms() at creation
  updatedAtTimestamp: z.number(),    // always set: get_epoch_timestamp_in_ms() at creation and every update
  createdBy: z.string(),             // always set: user_id at creation
  updatedBy: z.string(),             // always set: user_id at creation and every update
}).strict();

/**
 * Connector instance item schema without config (used for active/inactive endpoints)
 */
export const connectorInstanceItemWithoutConfigSchema = connectorInstanceItemSchema.omit({
  config: true,
}).strict();

/**
 * GET / (list connector instances) response schema
 */
export const connectorInstancesResponseSchema = connectorSuccessSchema.extend({
  connectors: z.array(connectorInstanceItemSchema),
  pagination: paginationResponseSchema,
  scopeCounts: scopeCountsResponseSchema.optional(),
}).strict();

/**
 * GET /active and GET /inactive response schema
 */
export const connectorActiveInactiveResponseSchema = connectorSuccessSchema.extend({
  connectors: z.array(connectorInstanceItemWithoutConfigSchema),
}).strict();

/**
 * GET /agents/active response schema
 */
export const connectorActiveAgentInstancesResponseSchema = connectorSuccessSchema.extend({
  connectors: z.array(connectorInstanceItemWithoutConfigSchema),
  pagination: paginationResponseSchema,
}).strict();

/**
 * GET /configured response schema
 */
export const connectorConfiguredResponseSchema = connectorSuccessSchema.extend({
  connectors: z.object({
    connectors: z.array(connectorInstanceItemSchema),
    pagination: paginationResponseSchema,
    scopeCounts: scopeCountsResponseSchema,
  }).strict(),
}).strict();

/**
 * Created connector item schema (simplified, returned after creation)
 */
const createdConnectorItemSchema = z.object({
  connectorId: z.string(),
  connectorType: z.string(),
  instanceName: z.string(),
  created: z.boolean(),
  scope: scopeSchema,
  createdBy: z.string(),
  isAuthenticated: z.boolean(),
  isConfigured: z.boolean(),
}).strict();

/**
 * POST / (create connector instance) response schema
 */
export const createConnectorResponseSchema = connectorSuccessSchema.extend({
  connector: createdConnectorItemSchema,
  message: z.string(),
}).strict();

/**
 * GET /:connectorId (single connector instance) response schema
 */
export const connectorInstanceDetailResponseSchema = connectorSuccessSchema.extend({
  connector: connectorInstanceItemSchema,
}).strict();

/**
 * DELETE /:connectorId response schema
 */
export const connectorDeleteResponseSchema = connectorSuccessSchema.extend({
  message: z.string(),
  connectorId: z.string(),
  status: z.string(),
}).strict();

// ============================================================================
// Inferred Response Types
// ============================================================================

export type ConnectorRegistryResponse = z.infer<typeof connectorRegistryResponseSchema>;
export type ConnectorSchemaResponse = z.infer<typeof connectorSchemaResponseSchema>;
export type ConnectorInstanceItem = z.infer<typeof connectorInstanceItemSchema>;
export type ConnectorInstancesResponse = z.infer<typeof connectorInstancesResponseSchema>;
export type ConnectorActiveInactiveResponse = z.infer<typeof connectorActiveInactiveResponseSchema>;
export type ConnectorActiveAgentInstancesResponse = z.infer<typeof connectorActiveAgentInstancesResponseSchema>;
export type ConnectorConfiguredResponse = z.infer<typeof connectorConfiguredResponseSchema>;
export type ConnectorInstanceDetailResponse = z.infer<typeof connectorInstanceDetailResponseSchema>;
export type ConnectorDeleteResponse = z.infer<typeof connectorDeleteResponseSchema>;
export type ConnectorInstanceConfigResponse = z.infer<typeof connectorInstanceConfigResponseSchema>;
export type ConnectorAuthConfigUpdateResponse = z.infer<typeof connectorAuthConfigUpdateResponseSchema>;
export type ConnectorFiltersSyncConfigUpdateResponse = z.infer<typeof connectorFiltersSyncConfigUpdateResponseSchema>;
export type ConnectorNameUpdateResponse = z.infer<typeof connectorNameUpdateResponseSchema>;
export type ConnectorOAuthAuthorizeResponse = z.infer<typeof connectorOAuthAuthorizeResponseSchema>;
export type ConnectorOAuthCallbackResponse = z.infer<typeof connectorOAuthCallbackResponseSchema>;
export type CreateConnectorResponse = z.infer<typeof createConnectorResponseSchema>;
export type ConnectorFilterFieldOptionsResponse = z.infer<typeof connectorFilterFieldOptionsResponseSchema>;
export type ConnectorToggleResponse = z.infer<typeof connectorToggleResponseSchema>;