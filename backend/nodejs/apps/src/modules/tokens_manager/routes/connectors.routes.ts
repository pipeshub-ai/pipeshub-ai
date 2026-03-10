/**
 * Connector Routes
 * 
 * RESTful routes for managing connector instances and configurations.
 * These routes follow the new instance-based architecture where multiple
 * instances of the same connector type can be created and managed independently.
 * 
 * @module connectors/routes
 */

import { Router, Response, NextFunction } from 'express';
import { Container } from 'inversify';
import { z } from 'zod';
import axios from 'axios';
import axiosRetry from 'axios-retry';

import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';
import { userAdminCheck } from '../../user_management/middlewares/userAdminCheck';
import { 
  AuthenticatedUserRequest,
  AuthenticatedServiceRequest 
} from '../../../libs/middlewares/types';
import {
  BadRequestError,
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';
import { AppConfig, loadAppConfig } from '../config/config';
import { Logger } from '../../../libs/services/logger.service';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
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
import { ConnectorsConfig } from '../../configuration_manager/schema/connectors.schema';
import { GoogleWorkspaceApp, scopeToAppMap } from '../types/connector.types';
import {
  GOOGLE_WORKSPACE_INDIVIDUAL_CREDENTIALS_PATH,
  GOOGLE_WORKSPACE_TOKEN_EXCHANGE_PATH,
  REFRESH_TOKEN_PATH,
} from '../consts/constants';
import {
  getGoogleWorkspaceConfig,
  setGoogleWorkspaceIndividualCredentials,
  getRefreshTokenCredentials,
  getRefreshTokenConfig,
  setRefreshTokenCredentials,
} from '../services/connectors-config.service';
import { verifyGoogleWorkspaceToken } from '../utils/verifyToken';
import {
  AppEnabledEvent,
  EntitiesEventProducer,
  EventType,
  Event,
} from '../services/entity_event.service';
import { ConnectorId, ConnectorIdToNameMap } from '../../../libs/types/connector.types';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';

const logger = Logger.getInstance({
  service: 'ConnectorRoutes',
});

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
// Validation Schemas
// ============================================================================

/**
 * Schema for creating a new connector instance
 */
const createConnectorInstanceSchema = z.object({
  body: z.object({
    connectorType: z.string().min(1, 'Connector type is required'),
    instanceName: z.string().min(1, 'Instance name is required'),
    config: z.object({
      auth: z.any().optional(),
      sync: z.any().optional(),
      filters: z.any().optional(),
    }).optional(),
    baseUrl: z.string().optional(),
    scope: z.enum(['team', 'personal']).refine((val) => val === 'team' || val === 'personal', {
      message: 'Scope must be either team or personal',
    }),
    authType: z.string().optional(), // Auth type selected by user (required for connectors with multiple auth types)
  }),
});

/**
 * Schema for validating connectorId parameter
 */
const connectorIdParamSchema = z.object({
  params: z.object({
    connectorId: z.string().min(1, 'Connector ID is required'),
  }),
});

/**
 * Schema for updating connector instance configuration
 */
const updateConnectorInstanceConfigSchema = z.object({
  body: z.object({
    auth: z.any().optional(),
    sync: z.any().optional(),
    filters: z.any().optional(),
    baseUrl: z.string().optional(),
  }),
  params: z.object({
    connectorId: z.string().min(1, 'Connector ID is required'),
  }),
});

/**
 * Schema for updating connector instance auth configuration
 */
const updateConnectorInstanceAuthConfigSchema = z.object({
  body: z.object({
    auth: z.any(),
    baseUrl: z.string().optional(),
  }),
  params: z.object({
    connectorId: z.string().min(1, 'Connector ID is required'),
  }),
});

/**
 * Schema for updating connector instance filters and sync configuration
 */
const updateConnectorInstanceFiltersSyncConfigSchema = z.object({
  body: z.object({
    sync: z.any().optional(),
    filters: z.any().optional(),
  }),
  params: z.object({
    connectorId: z.string().min(1, 'Connector ID is required'),
  }),
});

/**
 * Schema for getting OAuth authorization URL
 */
const getOAuthAuthorizationUrlSchema = z.object({
  params: z.object({
    connectorId: z.string().min(1, 'Connector ID is required'),
  }),
  query: z.object({
    baseUrl: z.string().optional(),
  }),
});

/**
 * Schema for handling OAuth callback
 */
const handleOAuthCallbackSchema = z.object({
  query: z.object({
    baseUrl: z.string().optional(),
    code: z.string().optional(),
    state: z.string().optional(),
    error: z.string().optional(),
  }),
});

/**
 * Schema for saving connector instance filter options
 */
const saveConnectorInstanceFilterOptionsSchema = z.object({
  body: z.object({
    filters: z.any(),
  }),
  params: z.object({
    connectorId: z.string().min(1, 'Connector ID is required'),
  }),
});

/**
 * Schema for getting filter field options (dynamic with pagination)
 */
const getFilterFieldOptionsSchema = z.object({
  params: z.object({
    connectorId: z.string().min(1, 'Connector ID is required'),
    filterKey: z.string().min(1, 'Filter key is required'),
  }),
  query: z.object({
    page: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().int().min(1))
      .optional(),
    limit: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().int().min(1).max(200))
      .optional(),
    search: z.string().optional(),
    cursor: z.string().optional(),
  }),
});

/**
 * Schema for validating connector toggle type parameter
 */
const connectorToggleSchema = z.object({
  body: z.object({
    type: z.enum(['sync', 'agent']),
  }),
  params: z.object({
    connectorId: z.string().min(1, 'Connector ID is required'),
  }),
});
/**
 * Schema for validating connector type parameter
 */
const connectorTypeParamSchema = z.object({
  params: z.object({
    connectorType: z.string().min(1, 'Connector type is required'),
  }),
});

/**
 * Schema for validating connector list query parameters
 */
const connectorListSchema = z.object({
  query: z.object({
    scope: z
      .enum(['team', 'personal'])
      .refine((val) => val === 'team' || val === 'personal', {
        message: 'Scope must be either team or personal',
      })
      .optional(),
    page: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().int().min(1))
      .optional(),
    limit: z
      .preprocess((arg) => (arg === '' || arg === undefined ? undefined : Number(arg)), z.number().int().min(1).max(200))
      .optional(),
    search: z.string().optional(),
  }),
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
  const eventService = container.get<EntitiesEventProducer>('EntitiesEventProducer');

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
    ValidationMiddleware.validate(
      z.object({
        body: z.object({ instanceName: z.string().min(1, 'Instance name is required') }),
        params: z.object({ connectorId: z.string().min(1, 'Connector ID is required') })
      })
    ),
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

  // ============================================================================
  // Legacy Routes (Backward Compatibility)
  // ============================================================================

  /**
   * @deprecated Use / instead
   * GET /
   * Get all connector instances (backward compatibility)
   */
  router.get(
    '/',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    metricsMiddleware(container),
    getConnectorInstances(config)
  );

  /**
   * @deprecated Use /active instead
   * GET /active
   * Get active connector instances (backward compatibility)
   */
  router.get(
    '/active',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    metricsMiddleware(container),
    getActiveConnectorInstances(config)
  );

  /**
   * @deprecated Use /inactive instead
   * GET /inactive
   * Get inactive connector instances (backward compatibility)
   */
  router.get(
    '/inactive',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_READ),
    metricsMiddleware(container),
    getInactiveConnectorInstances(config)
  );

  // ============================================================================
  // Legacy Google Workspace Routes
  // ============================================================================

  /**
   * @deprecated Legacy Google Workspace token exchange endpoint
   * POST /getTokenFromCode
   * Exchange authorization code for access token (Google Workspace specific)
   */
  router.post(
    '/getTokenFromCode',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONNECTOR_WRITE),
    metricsMiddleware(container),
    userAdminCheck,
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        if (!req.user) {
          throw new NotFoundError('User not found');
        }

        logger.info('Processing Google Workspace token exchange', {
          userId: req.user.userId,
          orgId: req.user.orgId,
        });

        // Get Google Workspace configuration
        const configResponse = await getGoogleWorkspaceConfig(
          req,
          config.cmBackend,
          config.scopedJwtSecret,
        );

        if (configResponse.statusCode !== 200) {
          throw new InternalServerError(
            'Error getting Google Workspace config',
            configResponse?.data
          );
        }

        const configData = configResponse.data;
        if (!configData.clientId) {
          throw new NotFoundError('Client ID is missing');
        }
        if (!configData.clientSecret) {
          throw new NotFoundError('Client secret is missing');
        }

        const enableRealTimeUpdates = configData?.enableRealTimeUpdates;
        const topicName = configData?.topicName;

        // Get frontend base URL and construct redirect URI
        const appConfig = await loadAppConfig();
        const frontendBaseUrl = appConfig.frontendUrl;
        const redirectUri = frontendBaseUrl.endsWith('/')
          ? `${frontendBaseUrl}account/individual/settings/connector/googleWorkspace`
          : `${frontendBaseUrl}/account/individual/settings/connector/googleWorkspace`;

        // Exchange authorization code for tokens
        const googleResponse = await axios.post(
          GOOGLE_WORKSPACE_TOKEN_EXCHANGE_PATH,
          {
            code: req.body.tempCode,
            client_id: configData.clientId,
            client_secret: configData.clientSecret,
            redirect_uri: redirectUri,
            grant_type: 'authorization_code',
          },
        );

        if (googleResponse.status !== 200) {
          throw new BadRequestError('Error exchanging authorization code');
        }

        const tokenData = googleResponse.data;

        // Verify ID token
        verifyGoogleWorkspaceToken(req, tokenData?.id_token);

        // Calculate refresh token expiry
        const refreshTokenExpiryDate = tokenData.refresh_token_expires_in
          ? tokenData.refresh_token_expires_in * 1000 + Date.now()
          : undefined;

        // Store credentials
        const credentialsResponse = await setGoogleWorkspaceIndividualCredentials(
          req,
          config.cmBackend,
          config.scopedJwtSecret,
          tokenData.access_token,
          tokenData.refresh_token,
          tokenData.expires_in * 1000 + Date.now(),
          refreshTokenExpiryDate,
          enableRealTimeUpdates,
          topicName,
        );

        if (credentialsResponse.statusCode !== 200) {
          throw new InternalServerError(
            'Error storing access token',
            credentialsResponse?.data,
          );
        }

        const connectorId = ConnectorId.GOOGLE_WORKSPACE;
        if (!connectorId) {
          throw new NotFoundError(
            'Google Workspace connector not found in config',
          );
        }

        // Find or create connector in database
        let connector = await ConnectorsConfig.findOne({
          name: ConnectorIdToNameMap[connectorId],
          orgId: req.user.orgId,
        });

        // Extract received scopes and filter enabled apps
        const receivedScopes = tokenData.scope.split(' ');
        const enabledApps = Object.keys(scopeToAppMap)
          .filter((scope) => receivedScopes.includes(scope))
          .map((scope) => scopeToAppMap[scope]);

        // Prepare event for sync service
        await eventService.start();
        let event: Event;

        if (connector) {
          // Update existing connector
          connector.isEnabled = true;
          connector.lastUpdatedBy = req.user.userId;

          event = {
            eventType: EventType.AppEnabledEvent,
            timestamp: Date.now(),
            payload: {
              orgId: req.user.orgId,
              appGroup: connector.name,
              appGroupId: connector._id,
              credentialsRoute: `${config.cmBackend}/${GOOGLE_WORKSPACE_INDIVIDUAL_CREDENTIALS_PATH}`,
              refreshTokenRoute: `${config.cmBackend}/${REFRESH_TOKEN_PATH}`,
              apps: enabledApps,
              syncAction: 'immediate',
            } as AppEnabledEvent,
          };

          await eventService.publishEvent(event);
          await eventService.stop();
          await connector.save();

          logger.info('Google Workspace connector enabled', {
            connectorId: connector._id,
            apps: enabledApps,
          });

          res.status(200).json({
            message: 'Connector is now enabled',
            connector,
          });
        } else {
          // Create new connector
          connector = new ConnectorsConfig({
            orgId: req.user.orgId,
            name: ConnectorIdToNameMap[connectorId],
            lastUpdatedBy: req.user.userId,
            isEnabled: true,
          });

          await connector.save();

          connector = await ConnectorsConfig.findOne({
            name: ConnectorIdToNameMap[connectorId],
            orgId: req.user.orgId,
          });

          if (!connector) {
            throw new InternalServerError('Error creating connector');
          }

          event = {
            eventType: EventType.AppEnabledEvent,
            timestamp: Date.now(),
            payload: {
              orgId: req.user.orgId,
              appGroup: connector.name,
              appGroupId: connector._id,
              credentialsRoute: `${config.cmBackend}/${GOOGLE_WORKSPACE_INDIVIDUAL_CREDENTIALS_PATH}`,
              refreshTokenRoute: `${config.cmBackend}/${REFRESH_TOKEN_PATH}`,
              apps: [
                GoogleWorkspaceApp.Drive,
                GoogleWorkspaceApp.Gmail,
                GoogleWorkspaceApp.Calendar,
              ],
              syncAction: 'immediate',
            } as AppEnabledEvent,
          };

          await eventService.publishEvent(event);
          await eventService.stop();

          logger.info('Google Workspace connector created and enabled', {
            connectorId: connector._id,
          });

          res.status(201).json({
            message: `Connector ${connectorId} created and enabled`,
            connector,
          });
        }
      } catch (error) {
        logger.error('Error in Google Workspace token exchange', {
          error: error instanceof Error ? error.message : String(error),
          userId: req.user?.userId,
        });
        next(error);
      }
    },
  );

  /**
   * @deprecated Legacy endpoint for refreshing individual connector tokens
   * POST /internal/refreshIndividualConnectorToken
   * Refresh access token using refresh token (internal use only)
   */
  router.post(
    '/internal/refreshIndividualConnectorToken',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        logger.info('Refreshing individual connector token');

        // Get refresh token from storage
        const refreshTokenResponse = await getRefreshTokenCredentials(
          req,
          config.cmBackend,
        );

        if (
          refreshTokenResponse.statusCode !== 200 ||
          !refreshTokenResponse.data.refresh_token
        ) {
          throw new InternalServerError(
            'Error getting refresh token from storage',
            refreshTokenResponse?.data,
          );
        }

        // Get connector configuration
        const configResponse = await getRefreshTokenConfig(req, config.cmBackend);

        if (configResponse.statusCode !== 200) {
          throw new InternalServerError(
            'Error getting connector config',
            configResponse?.data
          );
        }

        const configData = configResponse.data;
        if (!configData.clientId) {
          throw new NotFoundError('Client ID is missing');
        }
        if (!configData.clientSecret) {
          throw new NotFoundError('Client secret is missing');
        }

        const enableRealTimeUpdates = configData?.enableRealTimeUpdates;
        const topicName = configData?.topicName;

        // Retry logic for token refresh
        let retryCount = 0;
        let tokenExchangeSuccessful = false;
        let tokenData: any;

        while (retryCount < 3 && !tokenExchangeSuccessful) {
          try {
            const { data } = await axios.post(
              GOOGLE_WORKSPACE_TOKEN_EXCHANGE_PATH,
              {
                refresh_token: refreshTokenResponse.data.refresh_token,
                client_id: configData.clientId,
                client_secret: configData.clientSecret,
                grant_type: 'refresh_token',
              },
            );

            tokenData = data;
            tokenExchangeSuccessful = true;
          } catch (error) {
            retryCount++;
            
            if (error instanceof Error) {
              logger.error('Error refreshing individual connector token', {
                error: error.message,
                stack: error.stack,
                retryCount,
              });
            } else {
              logger.error('Error refreshing individual connector token', {
                unknownError: String(error),
                retryCount,
              });
            }

            if (retryCount < 3) {
              // Exponential backoff with jitter
              const delayMs =
                Math.pow(2, retryCount) * 1000 + Math.random() * 1000;
              await new Promise((resolve) => setTimeout(resolve, delayMs));
            } else {
              throw error;
            }
          }
        }

        if (!tokenExchangeSuccessful) {
          throw new Error('Failed to exchange token after multiple retries');
        }

        // Store new access token
        const updateResponse = await setRefreshTokenCredentials(
          req,
          config.cmBackend,
          tokenData.access_token,
          refreshTokenResponse.data.refresh_token,
          tokenData.expires_in * 1000 + Date.now(),
          refreshTokenResponse.data?.refresh_token_expiry_time || undefined,
          enableRealTimeUpdates,
          topicName,
        );

        if (updateResponse.statusCode !== 200) {
          throw new InternalServerError(
            'Error updating access token',
            updateResponse?.data,
          );
        }

        logger.info('Access token refreshed successfully');

        res.status(200).json({
          message: 'Access token updated successfully',
        });
      } catch (error) {
        logger.error('Error refreshing individual connector token', {
          error: error instanceof Error ? error.message : String(error),
        });
        next(error);
      }
    },
  );

  /**
   * @deprecated Internal endpoint for updating app configuration
   * POST /updateAppConfig
   * Update application configuration (internal use only)
   */
  router.post(
    '/updateAppConfig',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    async (
      _req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ): Promise<void> => {
      try {
        logger.info('Updating connector configuration');

        config = await loadAppConfig();
        container.rebind<AppConfig>('AppConfig').toDynamicValue(() => config);

        logger.info('Connector configuration updated successfully');

        res.status(200).json({
          message: 'Connectors configuration updated successfully',
          config,
        });
      } catch (error) {
        logger.error('Error updating connector configuration', {
          error: error instanceof Error ? error.message : String(error),
        });
        next(error);
      }
    },
  );

  return router;
}