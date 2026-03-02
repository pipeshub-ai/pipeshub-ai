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
  createToolset,
  getConfiguredToolsets,
  checkToolsetStatus,
  getToolsetConfig,
  saveToolsetConfig,
  updateToolsetConfig,
  deleteToolsetConfig,
  reauthenticateToolset,
  getOAuthAuthorizationUrl,
  handleOAuthCallback,
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
 * Schema for toolset ID parameter
 */
const toolsetIdParamSchema = z.object({
  params: z.object({
    toolsetId: z.string().min(1, 'Toolset ID is required'),
  }),
});

/**
 * Schema for creating a toolset
 */
const createToolsetSchema = z.object({
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
const saveToolsetConfigSchema = z.object({
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
const getOAuthAuthorizationUrlSchema = z.object({
  params: z.object({
    toolsetId: z.string().min(1, 'Toolset ID is required'),
  }),
  query: z.object({
    base_url: z.string().optional(),
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
  // Toolset Instance Routes (Database + etcd)
  // ============================================================================

  /**
   * POST /
   * Create a new toolset (creates node and saves config)
   */
  router.post(
    '/',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(createToolsetSchema),
    createToolset(config)
  );

  /**
   * GET /configured
   * Get all configured toolsets for the authenticated user
   * User ID is extracted from auth token
   */
  router.get(
    '/configured',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    getConfiguredToolsets(config)
  );

  /**
   * GET /:toolsetId/status
   * Check toolset authentication status
   */
  router.get(
    '/:toolsetId/status',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(toolsetIdParamSchema),
    checkToolsetStatus(config)
  );

  /**
   * GET /:toolsetId/config
   * Get toolset configuration
   */
  router.get(
    '/:toolsetId/config',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(toolsetIdParamSchema),
    getToolsetConfig(config)
  );

  /**
   * POST /:toolsetId/config
   * Save toolset configuration (OAuth credentials, API tokens, etc)
   * @deprecated Use POST / for create or PUT /:toolsetId/config for update
   */
  router.post(
    '/:toolsetId/config',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(saveToolsetConfigSchema),
    saveToolsetConfig(config)
  );

  /**
   * PUT /:toolsetId/config
   * Update toolset configuration (OAuth credentials, API tokens, etc)
   */
  router.put(
    '/:toolsetId/config',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(saveToolsetConfigSchema),
    updateToolsetConfig(config)
  );

  /**
   * DELETE /:toolsetId/config
   * Delete toolset configuration (with safe delete check)
   */
  router.delete(
    '/:toolsetId/config',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(toolsetIdParamSchema),
    deleteToolsetConfig(config)
  );

  /**
   * POST /:toolsetId/reauthenticate
   * Clear toolset OAuth credentials and mark as unauthenticated, requiring re-authentication
   */
  router.post(
    '/:toolsetId/reauthenticate',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(toolsetIdParamSchema),
    reauthenticateToolset(config)
  );

  // ============================================================================
  // OAuth Routes
  // ============================================================================

  /**
   * GET /:toolsetId/oauth/authorize
   * Get OAuth authorization URL for a toolset
   */
  router.get(
    '/:toolsetId/oauth/authorize',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(getOAuthAuthorizationUrlSchema),
    getOAuthAuthorizationUrl(config)
  );

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

  return router;
}

// Backward compatibility export
export default createToolsetsRouter;
