/**
 * Toolsets Controllers
 *
 * Controllers for managing toolsets registry, configuration, and OAuth.
 * These controllers act as a proxy layer between the frontend and the Python backend,
 * handling authentication, validation, and error transformation.
 */

import { Response, NextFunction } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import {
  BadRequestError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { AppConfig } from '../../tokens_manager/config/config';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { executeConnectorCommand, handleBackendError, handleValidatedConnectorResponse } from '../../tokens_manager/utils/connector.utils';
import {
  authenticateToolsetInstanceResponseSchema,
  createToolsetInstanceResponseSchema,
  deleteToolsetInstanceResponseSchema,
  deleteToolsetOAuthConfigResponseSchema,
  getToolsetSchemaResponseSchema,
  getToolsetInstanceResponseSchema,
  getToolsetInstancesResponseSchema,
  getMyToolsetsResponseSchema,
  getInstanceOAuthAuthorizationUrlResponseSchema,
  getInstanceStatusResponseSchema,
  oauthCallbackResponseSchema,
  listToolsetOAuthConfigsResponseSchema,
  reauthenticateToolsetInstanceResponseSchema,
  updateToolsetOAuthConfigResponseSchema,
  updateToolsetInstanceResponseSchema,
  removeToolsetCredentialsResponseSchema,
  toolsetListResponseSchema,
  updateUserToolsetCredentialsResponseSchema,
  getAgentToolsetsResponseSchema,
  authenticateAgentToolsetResponseSchema,
  updateAgentToolsetCredentialsResponseSchema,
  removeAgentToolsetCredentialsResponseSchema,
  reauthenticateAgentToolsetResponseSchema,
  getAgentToolsetOAuthUrlResponseSchema,
} from '../validators/toolsets_validator';

const logger = Logger.getInstance({
  service: 'ToolsetsController',
});

// ============================================================================
// Helper Functions
// ============================================================================

/**
 * Validate redirect URL to prevent open redirect vulnerabilities.
 * Only allows URLs from trusted sources (backend response).
 */
function isValidRedirectUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    // Allow http/https protocols only
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
}

// ============================================================================
// Registry Controllers
// ============================================================================

/**
 * Get all available toolsets from registry.
 */
export const getRegistryToolsets =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};
      const { page, limit, search } = req.query;

      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }

      logger.info(`Getting toolset registry for user ${userId}`);

      const queryParams = new URLSearchParams();
      if (page) queryParams.append('page', String(page));
      if (limit) queryParams.append('limit', String(limit));
      if (search) queryParams.append('search', String(search));

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/registry?${queryParams.toString()}`,
        HttpMethod.GET,
        headers
      );

      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Getting toolsets from registry',
        'Toolsets from registry not found',
        toolsetListResponseSchema
      );
    } catch (error: any) {
      logger.error('Error getting toolset registry', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handledError = handleBackendError(error, 'get toolset registry');
      next(handledError);
    }
  };

/**
 * Get toolset schema for a specific type.
 */
export const getToolsetSchema =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { toolsetType } = req.params;

      if (!toolsetType) {
        throw new BadRequestError('Toolset type is required');
      }

      logger.info(`Getting toolset schema for ${toolsetType}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/registry/${toolsetType}/schema`,
        HttpMethod.GET,
        headers
      );

      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Getting toolset schema',
        'Toolset schema not found',
        getToolsetSchemaResponseSchema
      );
    } catch (error: any) {
      logger.error('Error getting toolset schema', {
        error: error.message,
        toolsetType: req.params.toolsetType,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handledError = handleBackendError(error, 'get toolset schema');
      next(handledError);
    }
  };

// ============================================================================
// OAuth Controllers
// ============================================================================

/**
 * Handle OAuth callback for toolset.
 * Same pattern as connectors: return JSON with redirectUrl instead of server-side redirect
 * to prevent CORS issues when the browser follows the redirect.
 */
export const handleOAuthCallback =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { code, state, error: oauthError, base_url } = req.query;

      logger.info('Handling OAuth callback for toolset');

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const queryParams = new URLSearchParams();
      if (code) queryParams.append('code', String(code));
      if (state) queryParams.append('state', String(state));
      if (oauthError) queryParams.append('error', String(oauthError));
      if (base_url) queryParams.append('base_url', String(base_url));

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/oauth/callback?${queryParams.toString()}`,
        HttpMethod.GET,
        headers
      );

      // Handle redirect responses from Python backend (302, 307, etc.)
      if (
        connectorResponse &&
        (connectorResponse.statusCode === 302 || connectorResponse.statusCode === 307) &&
        connectorResponse.headers?.location
      ) {
        const redirectUrl = connectorResponse.headers.location;
        // Validate redirect URL to prevent open redirect vulnerabilities
        if (!isValidRedirectUrl(redirectUrl)) {
          throw new BadRequestError('Invalid redirect URL from backend');
        }
        res.status(200).json({ redirectUrl });
        return;
      }

      // Handle JSON responses with redirect_url from Python backend
      // Return as JSON to let frontend handle navigation (prevents CORS issues)
      if (connectorResponse && connectorResponse.data) {
        const responseData = connectorResponse.data as any;
        const redirectUrlFromJson = responseData.redirect_url as string | undefined;

        if (redirectUrlFromJson) {
          // Validate redirect URL to prevent open redirect vulnerabilities
          if (!isValidRedirectUrl(redirectUrlFromJson)) {
            throw new BadRequestError('Invalid redirect URL from backend');
          }
          res.status(200).json({ redirectUrl: redirectUrlFromJson });
          return;
        }
      }

      // Handle normal response
      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Handling OAuth callback',
        'OAuth callback failed',
        oauthCallbackResponseSchema,
      );
    } catch (error: any) {
      logger.error('Error handling OAuth callback:', {
        error: error.message,
      });
      const handledError = handleBackendError(error, 'handle OAuth callback');
      next(handledError);
    }
  };

// ============================================================================
// Instance Management Controllers (Admin-Created Instances)
// ============================================================================

/**
 * Get all toolset instances for the organization.
 */
export const getToolsetInstances =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};
      if (!userId) throw new UnauthorizedError('User authentication required');

      const { page, limit, search } = req.query;
      const queryParams = new URLSearchParams();
      if (page) queryParams.append('page', String(page));
      if (limit) queryParams.append('limit', String(limit));
      if (search) queryParams.append('search', String(search));

      logger.info(`Getting toolset instances for user ${userId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/instances?${queryParams.toString()}`,
        HttpMethod.GET,
        headers
      );

      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Getting toolset instances',
        'Toolset instances not found',
        getToolsetInstancesResponseSchema
      );
    } catch (error: any) {
      logger.error('Error getting toolset instances', { error: error.message, userId: req.user?.userId });
      next(handleBackendError(error, 'get toolset instances'));
    }
  };

/**
 * Create a new admin toolset instance.
 */
export const createToolsetInstance =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};
      if (!userId) throw new UnauthorizedError('User authentication required');

      const body = req.body;
      if (!body.instanceName) throw new BadRequestError('instanceName is required');
      if (!body.toolsetType) throw new BadRequestError('toolsetType is required');
      if (!body.authType) throw new BadRequestError('authType is required');

      logger.info(`Creating toolset instance '${body.instanceName}' for user ${userId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/instances`,
        HttpMethod.POST,
        headers,
        body
      );

      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Creating toolset instance',
        'Failed to create toolset instance',
        createToolsetInstanceResponseSchema
      );
    } catch (error: any) {
      logger.error('Error creating toolset instance', { error: error.message, userId: req.user?.userId });
      next(handleBackendError(error, 'create toolset instance'));
    }
  };

/**
 * Get a specific toolset instance.
 */
export const getToolsetInstance =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { instanceId } = req.params;
      if (!instanceId) throw new BadRequestError('instanceId is required');

      logger.info(`Getting toolset instance ${instanceId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/instances/${instanceId}`,
        HttpMethod.GET,
        headers
      );

      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Getting toolset instance',
        'Toolset instance not found',
        getToolsetInstanceResponseSchema
      );
    } catch (error: any) {
      logger.error('Error getting toolset instance', { error: error.message, instanceId: req.params.instanceId });
      next(handleBackendError(error, 'get toolset instance'));
    }
  };

/**
 * Update a toolset instance (admin only).
 */
export const updateToolsetInstance =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { instanceId } = req.params;
      if (!instanceId) throw new BadRequestError('instanceId is required');

      logger.info(`Updating toolset instance ${instanceId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/instances/${instanceId}`,
        HttpMethod.PUT,
        headers,
        req.body
      );

      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Updating toolset instance',
        'Failed to update toolset instance',
        updateToolsetInstanceResponseSchema
      );
    } catch (error: any) {
      logger.error('Error updating toolset instance', { error: error.message, instanceId: req.params.instanceId });
      next(handleBackendError(error, 'update toolset instance'));
    }
  };

/**
 * Delete a toolset instance (admin only).
 */
export const deleteToolsetInstance =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { instanceId } = req.params;
      if (!instanceId) throw new BadRequestError('instanceId is required');

      logger.info(`Deleting toolset instance ${instanceId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/instances/${instanceId}`,
        HttpMethod.DELETE,
        headers
      );

      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Deleting toolset instance',
        'Failed to delete toolset instance',
        deleteToolsetInstanceResponseSchema
      );
    } catch (error: any) {
      logger.error('Error deleting toolset instance', { error: error.message, instanceId: req.params.instanceId });
      next(handleBackendError(error, 'delete toolset instance'));
    }
  };

/**
 * Get merged view of toolset instances + user auth status (My Toolsets).
 */
export const getMyToolsets =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};
      if (!userId) throw new UnauthorizedError('User authentication required');

      const { search, includeRegistry, page, limit, authStatus } = req.query as {
        search?: string;
        includeRegistry?: boolean | string;
        page?: number | string;
        limit?: number | string;
        authStatus?: string;
      };
      const queryParams = new URLSearchParams();
      if (search) queryParams.append('search', String(search));
      if (page !== undefined && page !== null) queryParams.append('page', String(page));
      if (limit !== undefined && limit !== null) queryParams.append('limit', String(limit));
      if (authStatus) queryParams.append('authStatus', String(authStatus));
      // includeRegistry is boolean true after Zod coercion; also accept legacy string 'true'
      if (includeRegistry === true || includeRegistry === 'true') {
        queryParams.append('includeRegistry', 'true');
      }

      logger.info(`Getting my toolsets for user ${userId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/my-toolsets?${queryParams.toString()}`,
        HttpMethod.GET,
        headers
      );

      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Getting my toolsets',
        'My toolsets not found',
        getMyToolsetsResponseSchema
      );
    } catch (error: any) {
      logger.error('Error getting my toolsets', { error: error.message, userId: req.user?.userId });
      next(handleBackendError(error, 'get my toolsets'));
    }
  };

/**
 * Authenticate user against a toolset instance (non-OAuth).
 */
export const authenticateToolsetInstance =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { instanceId } = req.params;
      if (!instanceId) throw new BadRequestError('instanceId is required');

      logger.info(`Authenticating toolset instance ${instanceId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/instances/${instanceId}/authenticate`,
        HttpMethod.POST,
        headers,
        req.body
      );

      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Authenticating toolset instance',
        'Failed to authenticate toolset instance',
        authenticateToolsetInstanceResponseSchema
      );
    } catch (error: any) {
      logger.error('Error authenticating toolset instance', { error: error.message, instanceId: req.params.instanceId });
      next(handleBackendError(error, 'authenticate toolset instance'));
    }
  };


/**
 * Update user's information for a toolset instance.
 */
export const updateUserToolsetInstance =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { instanceId } = req.params;
      if (!instanceId) throw new BadRequestError('instanceId is required');

      logger.info(`Updating toolset instance ${instanceId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/instances/${instanceId}/credentials`,
        HttpMethod.PUT,
        headers,
        req.body
      );

      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Updating toolset instance credentials',
        'Failed to update toolset instance',
        updateUserToolsetCredentialsResponseSchema
      );
    } catch (error: any) {
      logger.error('Error updating toolset instance', { error: error.message, instanceId: req.params.instanceId });
      next(handleBackendError(error, 'update toolset instance'));
    }
  };

/**
 * Remove user's credentials for a toolset instance.
 */
export const removeToolsetCredentials =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { instanceId } = req.params;
      if (!instanceId) throw new BadRequestError('instanceId is required');

      logger.info(`Removing credentials for toolset instance ${instanceId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/instances/${instanceId}/credentials`,
        HttpMethod.DELETE,
        headers
      );

      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Removing toolset credentials',
        'Failed to remove credentials',
        removeToolsetCredentialsResponseSchema
      );
    } catch (error: any) {
      logger.error('Error removing toolset credentials', { error: error.message, instanceId: req.params.instanceId });
      next(handleBackendError(error, 'remove toolset credentials'));
    }
  };

/**
 * Re-authenticate (clear credentials) for a toolset instance.
 */
export const reauthenticateToolsetInstance =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { instanceId } = req.params;
      if (!instanceId) throw new BadRequestError('instanceId is required');

      logger.info(`Re-authenticating toolset instance ${instanceId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/instances/${instanceId}/reauthenticate`,
        HttpMethod.POST,
        headers
      );

      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Re-authenticating toolset instance',
        'Failed to re-authenticate',
        reauthenticateToolsetInstanceResponseSchema
      );
    } catch (error: any) {
      logger.error('Error re-authenticating toolset instance', { error: error.message, instanceId: req.params.instanceId });
      next(handleBackendError(error, 'reauthenticate toolset instance'));
    }
  };

/**
 * Get OAuth authorization URL for a toolset instance.
 */
export const getInstanceOAuthAuthorizationUrl =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { instanceId } = req.params;
      const { base_url } = req.query;

      if (!instanceId) throw new BadRequestError('instanceId is required');

      logger.info(`Getting OAuth authorization URL for toolset instance ${instanceId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const queryParams = new URLSearchParams();
      if (base_url) queryParams.append('base_url', String(base_url));

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/instances/${instanceId}/oauth/authorize?${queryParams.toString()}`,
        HttpMethod.GET,
        headers
      );

      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Getting instance OAuth URL',
        'Failed to get OAuth authorization URL',
        getInstanceOAuthAuthorizationUrlResponseSchema
      );
    } catch (error: any) {
      logger.error('Error getting instance OAuth authorization URL', { error: error.message, instanceId: req.params.instanceId });
      next(handleBackendError(error, 'get instance OAuth authorization URL'));
    }
  };

/**
 * Get instance authentication status for current user.
 */
export const getInstanceStatus =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { instanceId } = req.params;
      if (!instanceId) throw new BadRequestError('instanceId is required');

      logger.info(`Getting status for toolset instance ${instanceId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/instances/${instanceId}/status`,
        HttpMethod.GET,
        headers
      );

      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Getting instance status',
        'Instance status not found',
        getInstanceStatusResponseSchema
      );
    } catch (error: any) {
      logger.error('Error getting instance status', { error: error.message, instanceId: req.params.instanceId });
      next(handleBackendError(error, 'get instance status'));
    }
  };

/**
 * List OAuth configs for a toolset type (admin).
 */
export const listToolsetOAuthConfigs =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { toolsetType } = req.params;
      if (!toolsetType) throw new BadRequestError('toolsetType is required');

      logger.info(`Listing OAuth configs for toolset type ${toolsetType}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/oauth-configs/${toolsetType}`,
        HttpMethod.GET,
        headers
      );

      handleValidatedConnectorResponse(connectorResponse, res, 'Listing toolset OAuth configs', 'OAuth configs not found', listToolsetOAuthConfigsResponseSchema);
    } catch (error: any) {
      logger.error('Error listing toolset OAuth configs', { error: error.message, toolsetType: req.params.toolsetType });
      next(handleBackendError(error, 'list toolset OAuth configs'));
    }
  };

/**
 * Update an OAuth configuration for a toolset type (admin only).
 * Deauthenticates all users of affected instances in parallel.
 */
export const updateToolsetOAuthConfig =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { toolsetType, oauthConfigId } = req.params;
      if (!toolsetType) throw new BadRequestError('toolsetType is required');
      if (!oauthConfigId) throw new BadRequestError('oauthConfigId is required');

      logger.info(`Admin updating OAuth config ${oauthConfigId} for toolset type ${toolsetType}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/oauth-configs/${toolsetType}/${oauthConfigId}`,
        HttpMethod.PUT,
        headers,
        req.body
      );

      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Updating toolset OAuth config',
        'Failed to update OAuth configuration',
        updateToolsetOAuthConfigResponseSchema
      );
    } catch (error: any) {
      logger.error('Error updating toolset OAuth config', { error: error.message, toolsetType: req.params.toolsetType, oauthConfigId: req.params.oauthConfigId });
      next(handleBackendError(error, 'update toolset OAuth config'));
    }
  };

/**
 * Delete an OAuth configuration for a toolset type (admin only).
 * Safe delete: rejected if any instance references this config.
 */
export const deleteToolsetOAuthConfig =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { toolsetType, oauthConfigId } = req.params;
      if (!toolsetType) throw new BadRequestError('toolsetType is required');
      if (!oauthConfigId) throw new BadRequestError('oauthConfigId is required');

      logger.info(`Admin deleting OAuth config ${oauthConfigId} for toolset type ${toolsetType}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/oauth-configs/${toolsetType}/${oauthConfigId}`,
        HttpMethod.DELETE,
        headers
      );

      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Deleting toolset OAuth config',
        'Failed to delete OAuth configuration',
        deleteToolsetOAuthConfigResponseSchema
      );
    } catch (error: any) {
      logger.error('Error deleting toolset OAuth config', { error: error.message, toolsetType: req.params.toolsetType, oauthConfigId: req.params.oauthConfigId });
      next(handleBackendError(error, 'delete toolset OAuth config'));
    }
  };

// ============================================================================
// Agent-Scoped Toolset Controllers
// ============================================================================
// These controllers manage per-agent toolset credentials for service account agents.
// Credentials are stored at /services/toolsets/{instanceId}/{agentKey} (ETCD).
// All endpoints require the authenticated user to have edit access to the agent.
// ============================================================================

/**
 * Get all toolset instances merged with agent's authentication status.
 */
export const getAgentToolsets =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { agentKey } = req.params;
      const { search, page, limit, includeRegistry } = req.query;

      if (!agentKey) throw new BadRequestError('agentKey is required');

      logger.info(`Getting agent toolsets for agent ${agentKey}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const queryParams = new URLSearchParams();
      if (search) queryParams.append('search', String(search));
      if (page) queryParams.append('page', String(page));
      if (limit) queryParams.append('limit', String(limit));
      if (includeRegistry) queryParams.append('includeRegistry', String(includeRegistry));

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/agents/${agentKey}?${queryParams.toString()}`,
        HttpMethod.GET,
        headers
      );

      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Getting agent toolsets',
        'Agent toolsets not found',
        getAgentToolsetsResponseSchema
      );
    } catch (error: any) {
      logger.error('Error getting agent toolsets', { error: error.message, agentKey: req.params.agentKey });
      next(handleBackendError(error, 'get agent toolsets'));
    }
  };

/**
 * Authenticate an agent against a toolset instance (API key / Basic auth).
 */
export const authenticateAgentToolset =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { agentKey, instanceId } = req.params;
      if (!agentKey) throw new BadRequestError('agentKey is required');
      if (!instanceId) throw new BadRequestError('instanceId is required');

      logger.info(`Authenticating agent ${agentKey} against toolset instance ${instanceId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/agents/${agentKey}/instances/${instanceId}/authenticate`,
        HttpMethod.POST,
        headers,
        req.body
      );

      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Authenticating agent toolset',
        'Failed to authenticate agent toolset',
        authenticateAgentToolsetResponseSchema
      );
    } catch (error: any) {
      logger.error('Error authenticating agent toolset', { error: error.message, agentKey: req.params.agentKey, instanceId: req.params.instanceId });
      next(handleBackendError(error, 'authenticate agent toolset'));
    }
  };

/**
 * Update agent credentials for a toolset instance.
 */
export const updateAgentToolsetCredentials =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { agentKey, instanceId } = req.params;
      if (!agentKey) throw new BadRequestError('agentKey is required');
      if (!instanceId) throw new BadRequestError('instanceId is required');

      logger.info(`Updating agent ${agentKey} credentials for toolset instance ${instanceId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/agents/${agentKey}/instances/${instanceId}/credentials`,
        HttpMethod.PUT,
        headers,
        req.body
      );

      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Updating agent toolset credentials',
        'Failed to update agent toolset credentials',
        updateAgentToolsetCredentialsResponseSchema
      );
    } catch (error: any) {
      logger.error('Error updating agent toolset credentials', { error: error.message, agentKey: req.params.agentKey, instanceId: req.params.instanceId });
      next(handleBackendError(error, 'update agent toolset credentials'));
    }
  };

/**
 * Remove agent credentials for a toolset instance.
 */
export const removeAgentToolsetCredentials =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { agentKey, instanceId } = req.params;
      if (!agentKey) throw new BadRequestError('agentKey is required');
      if (!instanceId) throw new BadRequestError('instanceId is required');

      logger.info(`Removing agent ${agentKey} credentials for toolset instance ${instanceId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/agents/${agentKey}/instances/${instanceId}/credentials`,
        HttpMethod.DELETE,
        headers
      );

      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Removing agent toolset credentials',
        'Failed to remove agent toolset credentials',
        removeAgentToolsetCredentialsResponseSchema
      );
    } catch (error: any) {
      logger.error('Error removing agent toolset credentials', { error: error.message, agentKey: req.params.agentKey, instanceId: req.params.instanceId });
      next(handleBackendError(error, 'remove agent toolset credentials'));
    }
  };

/**
 * Re-authenticate (clear credentials) for an agent toolset instance.
 */
export const reauthenticateAgentToolset =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { agentKey, instanceId } = req.params;
      if (!agentKey) throw new BadRequestError('agentKey is required');
      if (!instanceId) throw new BadRequestError('instanceId is required');

      logger.info(`Re-authenticating agent ${agentKey} for toolset instance ${instanceId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/agents/${agentKey}/instances/${instanceId}/reauthenticate`,
        HttpMethod.POST,
        headers
      );

      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Re-authenticating agent toolset',
        'Failed to re-authenticate agent toolset',
        reauthenticateAgentToolsetResponseSchema
      );
    } catch (error: any) {
      logger.error('Error re-authenticating agent toolset', { error: error.message, agentKey: req.params.agentKey, instanceId: req.params.instanceId });
      next(handleBackendError(error, 'reauthenticate agent toolset'));
    }
  };

/**
 * Get OAuth authorization URL for an agent toolset instance.
 * The state encodes agentKey so the callback stores credentials under the agent's path.
 */
export const getAgentToolsetOAuthUrl =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { agentKey, instanceId } = req.params;
      const { base_url } = req.query;

      if (!agentKey) throw new BadRequestError('agentKey is required');
      if (!instanceId) throw new BadRequestError('instanceId is required');

      logger.info(`Getting OAuth URL for agent ${agentKey}, instance ${instanceId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const queryParams = new URLSearchParams();
      if (base_url) queryParams.append('base_url', String(base_url));

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/agents/${agentKey}/instances/${instanceId}/oauth/authorize?${queryParams.toString()}`,
        HttpMethod.GET,
        headers
      );

      handleValidatedConnectorResponse(
        connectorResponse,
        res,
        'Getting agent toolset OAuth URL',
        'Failed to get OAuth authorization URL for agent toolset',
        getAgentToolsetOAuthUrlResponseSchema
      );
    } catch (error: any) {
      logger.error('Error getting agent toolset OAuth URL', { error: error.message, agentKey: req.params.agentKey, instanceId: req.params.instanceId });
      next(handleBackendError(error, 'get agent toolset OAuth URL'));
    }
  };
