/**
 * OAuth Config Routes
 * 
 * RESTful routes for managing OAuth app configurations.
 * These routes proxy to the Python backend for OAuth config management.
 * 
 * @module oauth/routes
 */

import { Router } from 'express';
import { Container } from 'inversify';

import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';
import { AppConfig } from '../config/config';
import {
  getOAuthConfigRegistry,
  getOAuthConfigRegistryByType,
  getAllOAuthConfigs,
  listOAuthConfigs,
  createOAuthConfig,
  getOAuthConfig,
  updateOAuthConfig,
  deleteOAuthConfig,
} from '../controllers/oauth.controllers';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { oauthConfigRegistrySchema, getAllOAuthConfigsSchema, oauthConfigListSchema, createOAuthConfigSchema, oauthConfigIdSchema, updateOAuthConfigSchema, oauthConfigRegistryByTypeSchema } from '../validators/oauth.validator';
// ============================================================================
// Validation Schemas
// ============================================================================



// ============================================================================
// Router Factory
// ============================================================================

/**
 * Create and configure the OAuth config router.
 * 
 * @param container - Dependency injection container
 * @returns Configured Express router
 */
export function createOAuthRouter(container: Container): Router {
  const router = Router();
  const config = container.get<AppConfig>('AppConfig');
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');

  // ============================================================================
  // OAuth Config Routes
  // ============================================================================

  /**
   * GET /registry
   * Get OAuth config registry (available connector/tool types with OAuth support)
   */
  router.get(
    '/registry',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(oauthConfigRegistrySchema),
    getOAuthConfigRegistry(config)
);

  /**
   * GET /registry/:connectorType
   * Get OAuth config registry information for a specific connector type
   * This must come before /:connectorType to avoid route conflicts
   */
  router.get(
    '/registry/:connectorType',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(oauthConfigRegistryByTypeSchema),
    getOAuthConfigRegistryByType(config)
  );

  /**
   * GET /
   * Get all OAuth configs across all connector types
   * This must come before /:connectorType to avoid route conflicts
   */
  router.get(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(getAllOAuthConfigsSchema),
    getAllOAuthConfigs(config)
  );

  /**
   * GET /:connectorType
   * List OAuth configs for a connector type
   */
  router.get(
    '/:connectorType',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(oauthConfigListSchema),
    listOAuthConfigs(config)
  );

  /**
   * POST /:connectorType
   * Create a new OAuth config
   */
  router.post(
    '/:connectorType',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(createOAuthConfigSchema),
    createOAuthConfig(config)
  );

  /**
   * GET /:connectorType/:configId
   * Get a specific OAuth config
   */
  router.get(
    '/:connectorType/:configId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(oauthConfigIdSchema),
    getOAuthConfig(config)
  );

  /**
   * PUT /:connectorType/:configId
   * Update an OAuth config
   */
  router.put(
    '/:connectorType/:configId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateOAuthConfigSchema),
    updateOAuthConfig(config)
  );

  /**
   * DELETE /:connectorType/:configId
   * Delete an OAuth config
   */
  router.delete(
    '/:connectorType/:configId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_DELETE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(oauthConfigIdSchema),
    deleteOAuthConfig(config)
  );

  return router;
}
