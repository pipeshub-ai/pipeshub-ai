/**
 * Connector Routes
 *
 * RESTful routes for managing connector instances and configurations.
 * These routes follow the new instance-based architecture where multiple
 * instances of the same connector type can be created and managed independently.
 *
 * @module connectors/routes
 */

import { Router } from 'express';
import { Container } from 'inversify';
import axios from 'axios';
import axiosRetry from 'axios-retry';

import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';
import { AppConfig } from '../config/config';
import {
  getConnectorRegistry,
  getConnectorInstances,
  getActiveConnectorInstances,
  getInactiveConnectorInstances,
  getConfiguredConnectorInstances,
  createConnectorInstance,
  getConnectorInstance,
  getConnectorInstanceConfig,
  updateConnectorInstanceConfig,
  updateConnectorInstanceAuthConfig,
  updateConnectorInstanceFiltersSyncConfig,
  deleteConnectorInstance,
  updateConnectorInstanceName,
  getOAuthAuthorizationUrl,
  handleOAuthCallback,
  getConnectorInstanceFilterOptions,
  getFilterFieldOptions,
  saveConnectorInstanceFilterOptions,
  toggleConnectorInstance,
  getConnectorSchema,
  getActiveAgentInstances,
} from '../controllers/connector.controllers';
import {
  createConnectorInstanceSchema,
  connectorIdParamSchema,
  connectorListSchema,
  connectorTypeParamSchema,
  updateConnectorInstanceNameSchema,
  updateConnectorInstanceConfigSchema,
  updateConnectorInstanceAuthConfigSchema,
  updateConnectorInstanceFiltersSyncConfigSchema,
  getOAuthAuthorizationUrlSchema,
  handleOAuthCallbackSchema,
  saveConnectorInstanceFilterOptionsSchema,
  getFilterFieldOptionsSchema,
  connectorToggleSchema,
} from '../validators/connector.validators';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';

// Configure axios retry logic
axiosRetry(axios, {
  retries: 3,
  retryDelay: axiosRetry.exponentialDelay,
  retryCondition: (error) => {
    return !!(
      axiosRetry.isNetworkOrIdempotentRequestError(error) ||
      (error.response && error.response.status >= 500)
    );
  },
});

// ============================================================================
// Router Factory
// ============================================================================

/**
 * Create and configure the connector router.
 * 
 * @param container - Dependency injection container
 * @returns Configured Express router
 */
export function createConnectorRouter(container: Container): Router {
  const router = Router();
  let config = container.get<AppConfig>('AppConfig');
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');

  // ============================================================================
  // Registry Routes
  // ============================================================================

  /**
   * GET /registry
   * Get all available connector types from registry
   */
  router.get(
    '/registry',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(connectorListSchema),
    getConnectorRegistry(config)
  );

  /**
   * GET /registry/:connectorType/schema
   * Get connector schema for a specific type
   */
  router.get(
    '/registry/:connectorType/schema',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(connectorTypeParamSchema),
    getConnectorSchema(config)
  );

  // ============================================================================
  // Instance Management Routes
  // ============================================================================

  /**
   * GET /instances
   * Get all configured connector instances
   */
  router.get(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(connectorListSchema),
    getConnectorInstances(config)
  );

  /**
   * POST /instances
   * Create a new connector instance
   */
  router.post(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(createConnectorInstanceSchema),
    createConnectorInstance(config)
  );

  /**
   * GET /instances/active
   * Get all active connector instances
   */
  router.get(
    '/active',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    metricsMiddleware(container),
    getActiveConnectorInstances(config)
  );

  /**
   * GET /instances/inactive
   * Get all inactive connector instances
   */
  router.get(
    '/inactive',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    metricsMiddleware(container),
    getInactiveConnectorInstances(config)
  );

  /**
   * GET /instances/agents/active
   * Get all active agent instances
   */
    //======================= NOT USED FOR NOW ===============================================
  router.get(
    '/agents/active',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(connectorListSchema),
    getActiveAgentInstances(config)
  );
  /**
   * GET /instances/configured
   * Get all configured connector instances
   */
  router.get(
    '/configured',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(connectorListSchema),
    getConfiguredConnectorInstances(config)
  );

  /**
   * GET /instances/:connectorId
   * Get a specific connector instance
   */
  router.get(
    '/:connectorId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(connectorIdParamSchema),
    getConnectorInstance(config)
  );

  /**
   * DELETE /instances/:connectorId
   * Delete a connector instance
   */
  router.delete(
    '/:connectorId',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_DELETE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(connectorIdParamSchema),
    deleteConnectorInstance(config)
  );

  // ============================================================================
  // Configuration Routes
  // ============================================================================

  /**
   * GET /instances/:connectorId/config
   * Get configuration for a connector instance
   */
  router.get(
    '/:connectorId/config',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(connectorIdParamSchema),
    getConnectorInstanceConfig(config)
  );

  /**
   * PUT /instances/:connectorId/config
   * Update configuration for a connector instance
   */
    //======================= NOT USED FOR NOW ===============================================
  router.put(
    '/:connectorId/config',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateConnectorInstanceConfigSchema),
    updateConnectorInstanceConfig(config)
  );

  /**
   * PUT /instances/:connectorId/config/auth
   * Update authentication configuration for a connector instance
   */
  router.put(
    '/:connectorId/config/auth',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateConnectorInstanceAuthConfigSchema),
    updateConnectorInstanceAuthConfig(config)
  );

  /**
   * PUT /instances/:connectorId/config/filters-sync
   * Update filters and sync configuration for a connector instance
   */
  router.put(
    '/:connectorId/config/filters-sync',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateConnectorInstanceFiltersSyncConfigSchema),
    updateConnectorInstanceFiltersSyncConfig(config)
  );

  /**
   * PUT /instances/:connectorId/name
   * Update connector instance name
   */
  router.put(
    '/:connectorId/name',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateConnectorInstanceNameSchema),
    updateConnectorInstanceName(config)
  );

  // ============================================================================
  // OAuth Routes
  // ============================================================================

  /**
   * GET /instances/:connectorId/oauth/authorize
   * Get OAuth authorization URL for a connector instance
   */
  router.get(
    '/:connectorId/oauth/authorize',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(getOAuthAuthorizationUrlSchema),
    getOAuthAuthorizationUrl(config)
  );

  /**
   * GET /oauth/callback
   * Handle OAuth callback (connector_id is encoded in state parameter)
   */
  router.get(
    '/oauth/callback',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(handleOAuthCallbackSchema),
    handleOAuthCallback(config)
  );

  // ============================================================================
  // Filter Routes
  // ============================================================================

  /**
   * GET /instances/:connectorId/filters
   * Get filter options for a connector instance
   */
//======================= NOT USED FOR NOW ================================================
  router.get(
    '/:connectorId/filters',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(connectorIdParamSchema),
    getConnectorInstanceFilterOptions(config)
  );

  /**
   * POST /instances/:connectorId/filters
   * Save filter selections for a connector instance
   */
//======================= NOT USED FOR NOW ===============================================
  router.post(
    '/:connectorId/filters',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    metricsMiddleware(container),
    ValidationMiddleware.validate(saveConnectorInstanceFilterOptionsSchema),
    saveConnectorInstanceFilterOptions(config)
  );

  /**
   * GET /instances/:connectorId/filters/:filterKey/options
   * Get dynamic filter field options with pagination for a specific filter
   */
  router.get(
    '/:connectorId/filters/:filterKey/options',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    metricsMiddleware(container),
    ValidationMiddleware.validate(getFilterFieldOptionsSchema),
    getFilterFieldOptions(config)
  );

  // ============================================================================
  // Toggle Route
  // ============================================================================

  /**
   * POST /instances/:connectorId/toggle
   * Toggle connector instance active status
   */
  router.post(
    '/:connectorId/toggle',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_SYNC),
    metricsMiddleware(container),
    ValidationMiddleware.validate(connectorToggleSchema),
    toggleConnectorInstance(config)
  );

  return router;
}