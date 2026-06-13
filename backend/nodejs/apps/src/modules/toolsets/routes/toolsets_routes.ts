/**
 * Toolsets Routes
 * 
 * RESTful routes for managing toolsets registry, configuration, and OAuth.
 * Follows the same architectural pattern as connectors routes with DI container integration.
 * 
 * @module toolsets/routes
 */

import { Router } from 'express';
import { Container } from 'inversify';
import { z } from 'zod';

import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  getRegistryToolsets,
  getToolsetSchema,
  handleOAuthCallback,
  // Instance management (new architecture)
  getToolsetInstances,
  createToolsetInstance,
  getToolsetInstance,
  updateToolsetInstance,
  deleteToolsetInstance,
  getMyToolsets,
  authenticateToolsetInstance,
  removeToolsetCredentials,
  reauthenticateToolsetInstance,
  getInstanceOAuthAuthorizationUrl,
  getInstanceStatus,
  listToolsetOAuthConfigs,
  updateToolsetOAuthConfig,
  deleteToolsetOAuthConfig,
  updateUserToolsetInstance,
  // Agent-scoped toolset management
  getAgentToolsets,
  authenticateAgentToolset,
  updateAgentToolsetCredentials,
  removeAgentToolsetCredentials,
  reauthenticateAgentToolset,
  getAgentToolsetOAuthUrl,
} from '../controller/toolsets_controller';

// ============================================================================
// Validation Schemas
// ============================================================================

/**
 * Schema for toolset list query parameters
 */
const toolsetListSchema = z.object({
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
const toolsetTypeParamSchema = z.object({
  params: z.object({
    toolsetType: z.string().min(1, 'Toolset type is required'),
  }),
});

/**
 * Schema for creating a toolset instance
 */
const createToolsetInstanceSchema = z.object({
  body: z.object({
    instanceName: z.string().min(1, 'Instance name is required'),
    toolsetType: z.string().min(1, 'Toolset type is required'),
    authType: z.string().min(1, 'Auth type is required'),
    authConfig: z.record(z.any()).optional(),
    baseUrl: z.string().optional(),
    oauthConfigId: z.string().optional(),
    oauthInstanceName: z.string().optional(),
  }),
});

/**
 * Schema for handling OAuth callback
 */
const handleOAuthCallbackSchema = z.object({
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
const updateUserToolsetInstanceSchema = z.object({
  body: z.object({
    auth: z.object({
      email: z.string().optional(),
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
 * Schema for updating agent's credentials for a toolset instance.
 * Auth fields are toolset-schema-defined (dynamic keys); do not restrict to a fixed set.
 */
const updateAgentToolsetInstanceSchema = z.object({
  body: z.object({
    auth: z.record(z.string(), z.any()),
  }),
  params: z.object({
    instanceId: z.string().min(1, 'Instance ID is required'),
    agentKey: z.string().min(1, 'Agent Key is required'),
  }),
});


/**
 * Schema for getting my toolsets.
 * HTTP query params always arrive as strings, so numeric fields must be
 * coerced and boolean fields preprocessed — identical to toolsetListSchema.
 */
const getMyToolsetsSchema = z.object({
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
    /** When set, only instances for this toolset type (forwarded to Python). */
    toolsetType: z.string().optional(),
    authStatus: z
      .enum(['authenticated', 'not-authenticated'])
      .optional(),
  }),
});

/**
 * Schema for listing toolset instances query parameters.
 */
const getToolsetInstancesSchema = z.object({
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
 * Same pagination/search/registry options as my-toolsets; no authStatus filter on agent list.
 */
const getAgentToolsetsSchema = z.object({
  params: z.object({
    agentKey: z.string().min(1, 'Agent key is required'),
  }),
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
    toolsetType: z.string().optional(),
  }),
});

const toolsetInstanceIdParamSchema = z.object({
  params: z.object({
    instanceId: z.string().min(1, 'Instance ID is required'),
  }),
});

const updateToolsetInstanceSchema = z.object({
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

const nonOAuthAuthBodySchema = z.object({
  auth: z.object({
    apiToken: z.string().optional(),
    username: z.string().optional(),
    password: z.string().optional(),
    bearerToken: z.string().optional(),
  }),
});

const authenticateToolsetInstanceSchema = z.object({
  params: z.object({
    instanceId: z.string().min(1, 'Instance ID is required'),
  }),
  body: nonOAuthAuthBodySchema,
});

const getInstanceOAuthAuthorizationUrlSchema = z.object({
  params: z.object({
    instanceId: z.string().min(1, 'Instance ID is required'),
  }),
  query: z.object({
    base_url: z.string().optional(),
  }),
});

const updateToolsetOAuthConfigSchema = z.object({
  params: z.object({
    toolsetType: z.string().min(1, 'Toolset type is required'),
    oauthConfigId: z.string().min(1, 'OAuth config ID is required'),
  }),
  body: z.object({
    authConfig: z.object({
      clientId: z.string().min(1),
      clientSecret: z.string().min(1),
      tenantId: z.string().optional(),
      authorizeUrl: z.string().optional(),
      tokenUrl: z.string().optional(),
      scopes: z.array(z.string()).optional(),
      redirectUri: z.string().optional(),
      additionalParams: z.record(z.string()).optional(),
      tokenAccessType: z.string().optional(),
      scopeParameterName: z.string().optional(),
      tokenResponsePath: z.string().optional(),
    }),
    baseUrl: z.string().min(1),
  }),
});

const deleteToolsetOAuthConfigParamsSchema = z.object({
  params: z.object({
    toolsetType: z.string().min(1, 'Toolset type is required'),
    oauthConfigId: z.string().min(1, 'OAuth config ID is required'),
  }),
});

const agentInstanceParamsSchema = z.object({
  params: z.object({
    agentKey: z.string().min(1, 'Agent key is required'),
    instanceId: z.string().min(1, 'Instance ID is required'),
  }),
});

const authenticateAgentToolsetSchema = agentInstanceParamsSchema.extend({
  body: nonOAuthAuthBodySchema,
});

const removeAgentToolsetCredentialsSchema = agentInstanceParamsSchema;

const reauthenticateAgentToolsetSchema = agentInstanceParamsSchema;

export const getAgentToolsetOAuthUrlSchema = z.object({
  params: z.object({
    agentKey: z.string().min(1, 'Agent key is required'),
    instanceId: z.string().min(1, 'Instance ID is required'),
  }),
  query: z.object({
    base_url: z.string().optional(),
  }),
});

// ============================================================================
// Router Factory
// ============================================================================

/**
 * Create and configure the toolsets router.
 * 
 * @param container - Dependency injection container
 * @returns Configured Express router
 */
export function createToolsetsRouter(container: Container): Router {
  const router = Router();
  const config = container.get<AppConfig>('AppConfig');
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');

  // ============================================================================
  // Registry Routes (Read-Only - In-Memory)
  // ============================================================================

  /**
   * GET /registry
   * Get all available toolsets from registry
   */
  router.get(
    '/registry',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(toolsetListSchema),
    getRegistryToolsets(config)
  );

  /**
   * GET /registry/:toolsetType/schema
   * Get toolset schema for a specific type
   */
  router.get(
    '/registry/:toolsetType/schema',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(toolsetTypeParamSchema),
    getToolsetSchema(config)
  );

  // ============================================================================
  // OAuth Routes
  // ============================================================================

  /**
   * GET /oauth/callback
   * Handle OAuth callback (toolset_id is encoded in state parameter)
   */
  router.get(
    '/oauth/callback',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(handleOAuthCallbackSchema),
    handleOAuthCallback(config)
  );

  // ============================================================================
  // Instance Management Routes (Admin-Created Instances)
  // ============================================================================

  /**
   * GET /my-toolsets
   * Merged view: admin instances + current user's auth status
   */
  router.get(
    '/my-toolsets',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(getMyToolsetsSchema),
    getMyToolsets(config)
  );

  /**
   * GET /instances
   * List all toolset instances for the organization
   */
  router.get(
    '/instances',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(getToolsetInstancesSchema),
    getToolsetInstances(config)
  );

  /**
   * POST /instances
   * Create a new toolset instance (admin only)
   */
  router.post(
    '/instances',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(createToolsetInstanceSchema),
    createToolsetInstance(config)
  );

  /**
   * GET /instances/:instanceId
   * Get a specific toolset instance
   */
  router.get(
    '/instances/:instanceId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(toolsetInstanceIdParamSchema),
    getToolsetInstance(config)
  );

  /**
   * PUT /instances/:instanceId
   * Update a toolset instance (admin only)
   */
  router.put(
    '/instances/:instanceId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateToolsetInstanceSchema),
    updateToolsetInstance(config)
  );

  /**
   * DELETE /instances/:instanceId
   * Delete a toolset instance (admin only)
   */
  router.delete(
    '/instances/:instanceId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(toolsetInstanceIdParamSchema),
    deleteToolsetInstance(config)
  );

  /**
   * POST /instances/:instanceId/authenticate
   * User authenticates an instance (non-OAuth: API token, bearer, username/password)
   */
  router.post(
    '/instances/:instanceId/authenticate',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(authenticateToolsetInstanceSchema),
    authenticateToolsetInstance(config)
  );


  /**
   * PUT /instances/:instanceId/credentials
   *  Update user's credentials for an instance (non-OAuth: API token, bearer, username/password)
   */
  router.put(
    '/instances/:instanceId/credentials',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateUserToolsetInstanceSchema),
    updateUserToolsetInstance(config) // Reuse the same controller for both create and update of credentials 
  );
  
  /**
   * DELETE /instances/:instanceId/credentials
   * Remove user's credentials for an instance
   */
  router.delete(
    '/instances/:instanceId/credentials',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(toolsetInstanceIdParamSchema),
    removeToolsetCredentials(config)
  );

  /**
   * POST /instances/:instanceId/reauthenticate
   * Clear user's OAuth tokens, forcing re-authentication
   */
  router.post(
    '/instances/:instanceId/reauthenticate',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(toolsetInstanceIdParamSchema),
    reauthenticateToolsetInstance(config)
  );

  /**
   * GET /instances/:instanceId/oauth/authorize
   * Get OAuth authorization URL for a specific instance
   */
  router.get(
    '/instances/:instanceId/oauth/authorize',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(getInstanceOAuthAuthorizationUrlSchema),
    getInstanceOAuthAuthorizationUrl(config)
  );

  /**
   * GET /instances/:instanceId/status
   * Get authentication status of a specific instance for current user
   */
  router.get(
    '/instances/:instanceId/status',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(toolsetInstanceIdParamSchema),
    getInstanceStatus(config)
  );

  /**
   * GET /oauth-configs/:toolsetType
   * List OAuth configs for a toolset type (admin)
   */
  router.get(
    '/oauth-configs/:toolsetType',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(toolsetTypeParamSchema),
    listToolsetOAuthConfigs(config)
  );

  /**
   * PUT /oauth-configs/:toolsetType/:oauthConfigId
   * Update an OAuth config for a toolset type (admin only).
   * Deauthenticates all affected users in parallel.
   */
  router.put(
    '/oauth-configs/:toolsetType/:oauthConfigId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateToolsetOAuthConfigSchema),
    updateToolsetOAuthConfig(config)
  );

  /**
   * DELETE /oauth-configs/:toolsetType/:oauthConfigId
   * Delete an OAuth config (admin only). Safe delete: blocked if instances use it.
   */
  router.delete(
    '/oauth-configs/:toolsetType/:oauthConfigId',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(deleteToolsetOAuthConfigParamsSchema),
    deleteToolsetOAuthConfig(config)
  );

  // ============================================================================
  // Agent-Scoped Toolset Routes (Service Account Agents)
  // ============================================================================

  /**
   * GET /agents/:agentKey
   * Get all toolset instances with auth status for a service account agent.
   * Requires the requesting user to have edit access to the agent.
   */
  router.get(
    '/agents/:agentKey',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(getAgentToolsetsSchema),
    getAgentToolsets(config)
  );

  /**
   * POST /agents/:agentKey/instances/:instanceId/authenticate
   * Authenticate a toolset instance on behalf of a service account agent
   * (non-OAuth: API token, bearer, username/password).
   */
  router.post(
    '/agents/:agentKey/instances/:instanceId/authenticate',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(authenticateAgentToolsetSchema),
    authenticateAgentToolset(config)
  );

  /**
   * PUT /agents/:agentKey/instances/:instanceId/credentials
   * Update credentials for a toolset instance on behalf of a service account agent.
   */
  router.put(
    '/agents/:agentKey/instances/:instanceId/credentials',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateAgentToolsetInstanceSchema),
    updateAgentToolsetCredentials(config)
  );

  /**
   * DELETE /agents/:agentKey/instances/:instanceId/credentials
   * Remove credentials for a toolset instance on behalf of a service account agent.
   */
  router.delete(
    '/agents/:agentKey/instances/:instanceId/credentials',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(removeAgentToolsetCredentialsSchema),
    removeAgentToolsetCredentials(config)
  );

  /**
   * POST /agents/:agentKey/instances/:instanceId/reauthenticate
   * Clear agent's OAuth tokens for an instance, forcing re-authentication.
   */
  router.post(
    '/agents/:agentKey/instances/:instanceId/reauthenticate',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(reauthenticateAgentToolsetSchema),
    reauthenticateAgentToolset(config)
  );

  /**
   * GET /agents/:agentKey/instances/:instanceId/oauth/authorize
   * Get OAuth authorization URL for a toolset instance scoped to a service account agent.
   */
  router.get(
    '/agents/:agentKey/instances/:instanceId/oauth/authorize',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(getAgentToolsetOAuthUrlSchema),
    getAgentToolsetOAuthUrl(config)
  );

  return router;
}

// Backward compatibility export
export default createToolsetsRouter;