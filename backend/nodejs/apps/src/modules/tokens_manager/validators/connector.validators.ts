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
        z.number().int().min(1),
      )
      .optional(),
    limit: z
      .preprocess(
        (arg) => (arg === '' || arg === undefined ? undefined : Number(arg)),
        z.number().int().min(1).max(200),
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
 * Schema for updating connector instance configuration
 */
export const updateConnectorInstanceConfigSchema = z.object({
  body: z.object({
    auth: z.any().optional(),
    sync: z.any().optional(),
    filters: z.any().optional(),
    baseUrl: z.string().optional(),
  }),
  params: connectorIdParam,
});

/**
 * Schema for updating connector instance auth configuration
 */
export const updateConnectorInstanceAuthConfigSchema = z.object({
  body: z.object({
    auth: z.any(),
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
 * Schema for saving connector instance filter options
 */
export const saveConnectorInstanceFilterOptionsSchema = z.object({
  body: z.object({
    filters: z.any(),
  }),
  params: connectorIdParam,
});

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
        z.number().int().min(1),
      )
      .optional(),
    limit: z
      .preprocess(
        (arg) => (arg === '' || arg === undefined ? undefined : Number(arg)),
        z.number().int().min(1).max(200),
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

/**
 * Pagination response schema - reused for list endpoints
 */
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

/**
 * Scope counts response schema
 */
export const scopeCountsResponseSchema = z.object({
  personal: z.number(),
  team: z.number(),
}).strict();

// ============================================================================
// Response Schemas - Config Building Blocks (Reusable)
// ============================================================================

/**
 * Field validation config schema
 */
const fieldValidationSchema = z.object({
  minLength: z.number().optional(),
  maxLength: z.number().optional(),
}).strict();

/**
 * Auth field schema - used in auth schemas
 */
const authFieldSchema = z.object({
  name: z.string(),
  displayName: z.string(),
  placeholder: z.string().optional(),
  description: z.string().optional(),
  fieldType: z.string(),
  required: z.boolean(),
  usage: z.string().optional(),
  defaultValue: z.any(),
  validation: fieldValidationSchema.optional(),
  isSecret: z.boolean().optional(),
  nonEditable: z.boolean().optional(),
  options: z.array(z.string()).optional(),
}).strict();

/**
 * OAuth config schema
 */
const oauthConfigSchema = z.object({
  authorizeUrl: z.string().optional(),
  tokenUrl: z.string().optional(),
  scopes: z.array(z.string()).optional(),
  redirectUri: z.string().optional(),
}).strict();

/**
 * Auth schema entry
 */
const authSchemaEntrySchema = z.object({
  fields: z.array(authFieldSchema).optional(),
  redirectUri: z.string().optional(),
  displayRedirectUri: z.boolean().optional(),
}).strict();

/**
 * Auth config schema
 */
const authConfigResponseSchema = z.object({
  supportedAuthTypes: z.array(z.string()),
  schemas: z.record(authSchemaEntrySchema).optional(),
  oauthConfigs: z.record(oauthConfigSchema).optional(),
  values: z.record(z.any()).optional(),
  customFields: z.array(z.any()).optional(),
  customValues: z.record(z.any()).optional(),
  conditionalDisplay: z.record(z.any()).optional(),
}).strict();

/**
 * Webhook config schema
 */
const webhookConfigSchema = z.object({
  supported: z.boolean(),
  webhookUrl: z.string().optional(),
  events: z.array(z.string()).optional(),
  verificationToken: z.string().optional(),
  secretKey: z.string().optional(),
}).strict();

/**
 * Scheduled config schema
 */
const scheduledConfigSchema = z.object({
  intervalMinutes: z.number().optional(),
  cronExpression: z.string().optional(),
  timezone: z.string().optional(),
  startTime: z.number().optional(),
  nextTime: z.number().optional(),
  endTime: z.number().optional(),
  maxRepetitions: z.number().optional(),
  repetitionCount: z.number().optional(),
}).strict();

/**
 * Realtime config schema
 */
const realtimeConfigSchema = z.object({
  supported: z.boolean(),
  connectionType: z.string().optional(),
}).strict();

/**
 * Sync custom field schema
 */
const syncCustomFieldSchema = z.object({
  name: z.string(),
  displayName: z.string(),
  description: z.string().optional(),
  fieldType: z.string(),
  required: z.boolean().optional(),
  defaultValue: z.any(),
  validation: z.record(z.any()).optional(),
  isSecret: z.boolean().optional(),
  nonEditable: z.boolean().optional(),
  options: z.array(z.string()).optional(),
}).strict();

/**
 * Sync config schema
 */
const syncConfigResponseSchema = z.object({
  supportedStrategies: z.array(z.string()),
  selectedStrategy: z.string().optional(),
  webhookConfig: webhookConfigSchema.optional(),
  scheduledConfig: scheduledConfigSchema.optional(),
  realtimeConfig: realtimeConfigSchema.optional(),
  customFields: z.array(syncCustomFieldSchema).optional(),
  customValues: z.record(z.any()).optional(),
  values: z.record(z.any()).optional(),
}).strict();

/**
 * Filter option schema - can be object with id/label or just a string
 */
const filterOptionSchema = z.union([
  z.object({
    id: z.string(),
    label: z.string(),
  }).strict(),
  z.string(),
]);

/**
 * Filter field schema (used in registry schema definitions)
 */
const filterFieldSchema = z.object({
  name: z.string(),
  displayName: z.string(),
  description: z.string().optional(),
  filterType: z.string(),
  category: z.string().optional(),
  required: z.boolean().optional(),
  defaultValue: z.any().nullable(),
  defaultOperator: z.string().nullable().optional(),
  operators: z.array(z.string()).optional(),
  optionSourceType: z.string().optional(),
  options: z.array(filterOptionSchema).optional(),
}).strict();

/**
 * Filter category schema (sync or indexing) - used in registry schema
 */
const filterCategorySchema = z.object({
  schema: z.object({
    fields: z.array(filterFieldSchema),
  }).strict(),
  values: z.record(z.any()).optional(),
}).strict();

// ============================================================================
// Stored Filter Value Schemas (for saved connector config)
// ============================================================================

/**
 * Filter value - can be various types depending on filter type
 *
 * Supported filter types (from Python FilterType):
 *   string, boolean, datetime, list, number, multiselect
 *
 * Supported operators (from Python FilterOperator):
 *   String: is, is_not, contains, not_contains, starts_with, ends_with
 *   Boolean: is, is_not
 *   Datetime: is_before, is_after, between, last_7_days, last_30_days, last_90_days, last_365_days
 *   List/Multiselect: in, not_in
 *   Number: equal, not_equal, greater_than, less_than, greater_than_or_equal, less_than_or_equal
 *   Legacy: equals (backward compatibility)
 * - boolean: true/false
 * - list/multiselect: array of strings or objects with id/label
 * - datetime: object with start/end epoch milliseconds
 * - string: string value
 * - number: numeric value
 */
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
    start: z.number().nullable().optional(),
    end: z.number().nullable().optional(),
  }).strict(),
  z.null(),
]);

/**
 * Stored filter value item schema (individual filter value)
 */
const storedFilterValueItemSchema = z.object({
  operator: z.string(),
  value: filterValueSchema,
  type: z.string(),
}).strict();

/**
 * Stored filter values category schema (sync or indexing)
 * Contains only values, no schema definition
 */
const storedFilterCategorySchema = z.object({
  values: z.record(storedFilterValueItemSchema),
}).strict();

/**
 * Stored filters config schema (for saved connector config)
 */
const storedFiltersConfigSchema = z.object({
  sync: storedFilterCategorySchema.optional(),
  indexing: storedFilterCategorySchema.optional(),
}).strict();

/**
 * Stored auth config schema (saved authentication values)
 * authType, connectorType, connectorScope are always set by _prepare_connector_config().
 * OAUTH-only fields (redirectUri, scopes, authorizeUrl, tokenUrl, oauthConfigId, oauthInstanceName)
 * are optional — absent for non-OAUTH connectors.
 * Uses passthrough since non-OAUTH connectors may include arbitrary extra auth fields.
 */
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

/**
 * Scheduled sync config schema
 */
const scheduledSyncConfigSchema = z.object({
  intervalMinutes: z.number().optional(),
  timezone: z.string().optional(),
}).passthrough();

/**
 * Stored sync config schema (saved sync values)
 */
const storedSyncConfigSchema = z.object({
  selectedStrategy: z.string().optional(),
  scheduledConfig: scheduledSyncConfigSchema.optional(),
}).passthrough();

/**
 * Stored connector inner config schema (auth, sync, filters values)
 * Used for GET /:connectorId/config endpoint
 */
const storedConnectorConfigSchema = z.object({
  auth: storedAuthConfigSchema.optional(),
  sync: storedSyncConfigSchema.optional(),
  filters: storedFiltersConfigSchema.optional(),
}).passthrough();

/**
 * GET /:connectorId/config response schema
 */
export const connectorInstanceConfigResponseSchema = connectorSuccessSchema.extend({
  config: z.object({
    connector_id: z.string(),
    name: z.string().nullable(),
    type: z.string(),
    appGroup: z.string(),
    authType: z.string().nullable().optional(),
    scope: z.string(),
    createdBy: z.string().nullable().optional(),
    updatedBy: z.string().nullable().optional(),
    appDescription: z.string(),
    appCategories: z.array(z.string()),
    supportsRealtime: z.boolean(),
    supportsSync: z.boolean(),
    supportsAgent: z.boolean(),
    iconPath: z.string(),
    config: storedConnectorConfigSchema.optional(),
    isActive: z.boolean(),
    isConfigured: z.boolean(),
    isAuthenticated: z.boolean(),
    createdAtTimestamp: z.number().nullable().optional(),
    updatedAtTimestamp: z.number().nullable().optional(),
  }).strict(),
}).strict();

/**
 * OAuth credentials stored after authentication
 * Mirrors OAuthToken.to_dict() in oauth_service.py — all fields always present.
 * Optional[T] fields in Python serialize as null in JSON, hence .nullable() (not .optional()).
 */
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

/**
 * OAuth state stored after authentication
 */
const storedOAuthSchema = z.object({
  used_codes: z.array(z.string()).optional(),
}).passthrough();

/**
 * Config update response config schema (shared by auth and filters-sync updates)
 * Contains auth, sync, filters, credentials, oauth
 */
const configUpdateResponseConfigSchema = z.object({
  auth: storedAuthConfigSchema.optional(),
  sync: storedSyncConfigSchema.optional(),
  filters: storedFiltersConfigSchema.optional(),
  credentials: storedCredentialsSchema.nullable(),
  oauth: storedOAuthSchema.nullable(),
}).passthrough();

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
 * Returns dynamic filter field options with pagination
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
 * Returns OAuth authorization URL for connector authentication
 */
export const connectorOAuthAuthorizeResponseSchema = connectorSuccessSchema.extend({
  authorizationUrl: z.string(),
  state: z.string(),
}).strict();

/**
 * GET /oauth/callback response schema (from Python backend)
 * Returns redirect URL after successful OAuth callback
 */
export const connectorOAuthCallbackResponseSchema = connectorSuccessSchema.extend({
  redirect_url: z.string(),
}).strict();

/**
 * Filters config schema
 */
const filtersConfigResponseSchema = z.object({
  sync: filterCategorySchema.optional(),
  indexing: filterCategorySchema.optional(),
}).strict();

/**
 * Documentation link schema
 */
const documentationLinkSchema = z.object({
  title: z.string(),
  url: z.string(),
  type: z.string().optional(),
}).strict();

/**
 * Full connector config schema (used in registry)
 */
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

/**
 * Connector registry item schema (for /registry endpoint)
 * Note: scope can be either:
 *   - Single string when scope query param is passed (e.g., "team")
 *   - Array when no scope query param (e.g., ["team", "personal"])
 */
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
 * Returns connector config schema for a specific connector type
 * Note: auth.oauthConfigs is removed by backend, but since it's optional, validation passes
 */
export const connectorSchemaResponseSchema = connectorSuccessSchema.extend({
  schema: connectorConfigResponseSchema,
}).strict();

// ============================================================================
// Response Schemas - Connector Instances
// ============================================================================

/**
 * Connector instance item schema (extends registry item with instance-specific fields)
 * Used for configured connector instances from database
 * Note: scope is overridden to be a single string (instances have single scope)
 */
export const connectorInstanceItemSchema = connectorRegistryItemSchema.extend({
  scope: scopeSchema,
  _key: z.string().nullable().optional(),
  authType: z.string().nullable().optional(),
  isActive: z.boolean(),
  isAgentActive: z.boolean(),
  isConfigured: z.boolean(),
  isAuthenticated: z.boolean(),
  isLocked: z.boolean(),
  status: z.string().nullable().optional(),
  createdAtTimestamp: z.number().nullable().optional(),
  updatedAtTimestamp: z.number().nullable().optional(),
  createdBy: z.string().nullable().optional(),
  updatedBy: z.string().nullable().optional(),
}).strict();

/**
 * Connector instance item schema without config (used for active/inactive endpoints)
 * The backend removes config field for these endpoints
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
 * Note: config field is removed by backend for these endpoints
 */
export const connectorActiveInactiveResponseSchema = connectorSuccessSchema.extend({
  connectors: z.array(connectorInstanceItemWithoutConfigSchema),
}).strict();

/**
 * GET /configured response schema
 * Note: This endpoint returns a nested structure where connectors contains
 * another object with connectors array, pagination, and scopeCounts
 */
export const connectorConfiguredResponseSchema = connectorSuccessSchema.extend({
  connectors: z.object({
    connectors: z.array(connectorInstanceItemSchema),
    pagination: paginationResponseSchema,
    scopeCounts: scopeCountsResponseSchema.optional(),
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
export type ConnectorConfiguredResponse = z.infer<typeof connectorConfiguredResponseSchema>;
export type ConnectorInstanceDetailResponse = z.infer<typeof connectorInstanceDetailResponseSchema>;
export type ConnectorDeleteResponse = z.infer<typeof connectorDeleteResponseSchema>;
export type ConnectorToggleResponse = z.infer<typeof connectorToggleResponseSchema>;
export type ConnectorInstanceConfigResponse = z.infer<typeof connectorInstanceConfigResponseSchema>;
export type ConnectorAuthConfigUpdateResponse = z.infer<typeof connectorAuthConfigUpdateResponseSchema>;
export type ConnectorFiltersSyncConfigUpdateResponse = z.infer<typeof connectorFiltersSyncConfigUpdateResponseSchema>;
export type ConnectorNameUpdateResponse = z.infer<typeof connectorNameUpdateResponseSchema>;
export type ConnectorFilterFieldOptionsResponse = z.infer<typeof connectorFilterFieldOptionsResponseSchema>;
export type ConnectorOAuthAuthorizeResponse = z.infer<typeof connectorOAuthAuthorizeResponseSchema>;
export type ConnectorOAuthCallbackResponse = z.infer<typeof connectorOAuthCallbackResponseSchema>;
export type CreateConnectorResponse = z.infer<typeof createConnectorResponseSchema>;
