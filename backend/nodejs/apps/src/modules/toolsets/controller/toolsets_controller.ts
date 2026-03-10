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
import { executeConnectorCommand, handleBackendError, handleConnectorResponse } from '../../tokens_manager/utils/connector.utils';

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

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting toolsets from registry',
        'Toolsets from registry not found'
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
 * Get all configured toolsets for the authenticated user.
 * User ID is extracted from the authenticated request.
 */
export const getConfiguredToolsets =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};

      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }

      logger.info(`Getting configured toolsets for user ${userId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/configured`,
        HttpMethod.GET,
        headers
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting configured toolsets',
        'Configured toolsets not found'
      );
    } catch (error: any) {
      logger.error('Error getting configured toolsets', {
        error: error.message,
        userId: req.user?.userId,
      });
      const handledError = handleBackendError(error, 'get configured toolsets');
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

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting toolset schema',
        'Toolset schema not found'
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
// Instance Status & Configuration Controllers
// ============================================================================

/**
 * Create a new toolset (creates node and saves config).
 */
export const createToolset =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const toolsetData = req.body;

      if (!toolsetData.name) {
        throw new BadRequestError('Toolset name is required');
      }

      logger.info(`Creating toolset ${toolsetData.name}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/`,
        HttpMethod.POST,
        headers,
        toolsetData
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Creating toolset',
        'Failed to create toolset'
      );
    } catch (error: any) {
      logger.error('Error creating toolset:', {
        error: error.message,
        toolsetName: req.body?.name,
        userId: req.user?.userId,
      });
      const handledError = handleBackendError(error, 'create toolset');
      next(handledError);
    }
  };

/**
 * Check toolset authentication status.
 */
export const checkToolsetStatus =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { toolsetId } = req.params;

      if (!toolsetId) {
        throw new BadRequestError('toolsetId is required');
      }

      logger.info(`Checking toolset status for ${toolsetId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/${toolsetId}/status`,
        HttpMethod.GET,
        headers
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Checking toolset status',
        'Toolset status not found'
      );
    } catch (error: any) {
      logger.error('Error checking toolset status:', {
        error: error.message,
        toolsetId: req.params.toolsetId,
      });
      const handledError = handleBackendError(error, 'check toolset status');
      next(handledError);
    }
  };

/**
 * Get toolset configuration.
 */
export const getToolsetConfig =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { toolsetId } = req.params;

      if (!toolsetId) {
        throw new BadRequestError('toolsetId is required');
      }

      logger.info(`Getting toolset config for ${toolsetId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/${toolsetId}/config`,
        HttpMethod.GET,
        headers
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting toolset config',
        'Toolset config not found'
      );
    } catch (error: any) {
      logger.error('Error getting toolset config:', {
        error: error.message,
        toolsetId: req.params.toolsetId,
      });
      const handledError = handleBackendError(error, 'get toolset config');
      next(handledError);
    }
  };

/**
 * Save toolset configuration.
 */
export const saveToolsetConfig =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { toolsetId } = req.params;
      const configData = req.body;

      if (!toolsetId) {
        throw new BadRequestError('toolsetId is required');
      }

      logger.info(`Saving toolset config for ${toolsetId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/${toolsetId}/config`,
        HttpMethod.POST,
        headers,
        configData
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Saving toolset config',
        'Failed to save toolset config'
      );
    } catch (error: any) {
      logger.error('Error saving toolset config:', {
        error: error.message,
        toolsetId: req.params.toolsetId,
      });
      const handledError = handleBackendError(error, 'save toolset config');
      next(handledError);
    }
  };

/**
 * Update toolset configuration.
 */
export const updateToolsetConfig =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { toolsetId } = req.params;
      const configData = req.body;

      if (!toolsetId) {
        throw new BadRequestError('toolsetId is required');
      }

      logger.info(`Updating toolset config for ${toolsetId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/${toolsetId}/config`,
        HttpMethod.PUT,
        headers,
        configData
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Updating toolset config',
        'Failed to update toolset config'
      );
    } catch (error: any) {
      logger.error('Error updating toolset config:', {
        error: error.message,
        toolsetId: req.params.toolsetId,
      });
      const handledError = handleBackendError(error, 'update toolset config');
      next(handledError);
    }
  };

/**
 * Delete toolset configuration.
 */
export const deleteToolsetConfig =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { toolsetId } = req.params;

      if (!toolsetId) {
        throw new BadRequestError('toolsetId is required');
      }

      logger.info(`Deleting toolset config for ${toolsetId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/${toolsetId}/config`,
        HttpMethod.DELETE,
        headers
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Deleting toolset config',
        'Failed to delete toolset config'
      );
    } catch (error: any) {
      logger.error('Error deleting toolset config:', {
        error: error.message,
        toolsetId: req.params.toolsetId,
      });
      const handledError = handleBackendError(error, 'delete toolset config');
      next(handledError);
    }
  };

/**
 * Reauthenticate toolset - clears credentials and marks as unauthenticated.
 * Only applicable to OAuth-configured toolsets.
 */
export const reauthenticateToolset =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { toolsetId } = req.params;

      if (!toolsetId) {
        throw new BadRequestError('toolsetId is required');
      }

      logger.info(`Reauthenticating toolset ${toolsetId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/${toolsetId}/reauthenticate`,
        HttpMethod.POST,
        headers
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Reauthenticating toolset',
        'Failed to reauthenticate toolset'
      );
    } catch (error: any) {
      logger.error('Error reauthenticating toolset:', {
        error: error.message,
        toolsetId: req.params.toolsetId,
      });
      const handledError = handleBackendError(error, 'reauthenticate toolset');
      next(handledError);
    }
  };

// ============================================================================
// OAuth Controllers
// ============================================================================

/**
 * Get OAuth authorization URL for a toolset.
 */
export const getOAuthAuthorizationUrl =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction
  ): Promise<void> => {
    try {
      const { toolsetId } = req.params;
      const { base_url } = req.query;

      if (!toolsetId) {
        throw new BadRequestError('toolsetId is required');
      }

      logger.info(`Getting OAuth authorization URL for toolset ${toolsetId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const queryParams = new URLSearchParams();
      if (base_url) queryParams.append('base_url', String(base_url));

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/${toolsetId}/oauth/authorize?${queryParams.toString()}`,
        HttpMethod.GET,
        headers
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting OAuth authorization URL',
        'Failed to get OAuth authorization URL'
      );
    } catch (error: any) {
      logger.error('Error getting OAuth authorization URL:', {
        error: error.message,
        toolsetId: req.params.toolsetId,
      });
      const handledError = handleBackendError(error, 'get OAuth authorization URL');
      next(handledError);
    }
  };

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
      handleConnectorResponse(
        connectorResponse,
        res,
        'Handling OAuth callback',
        'OAuth callback failed'
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

      handleConnectorResponse(connectorResponse, res, 'Getting toolset instances', 'Toolset instances not found');
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

      handleConnectorResponse(connectorResponse, res, 'Creating toolset instance', 'Failed to create toolset instance');
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

      handleConnectorResponse(connectorResponse, res, 'Getting toolset instance', 'Toolset instance not found');
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

      handleConnectorResponse(connectorResponse, res, 'Updating toolset instance', 'Failed to update toolset instance');
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

      handleConnectorResponse(connectorResponse, res, 'Deleting toolset instance', 'Failed to delete toolset instance');
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

      const { search } = req.query;
      const queryParams = new URLSearchParams();
      if (search) queryParams.append('search', String(search));

      logger.info(`Getting my toolsets for user ${userId}`);

      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/toolsets/my-toolsets?${queryParams.toString()}`,
        HttpMethod.GET,
        headers
      );

      handleConnectorResponse(connectorResponse, res, 'Getting my toolsets', 'My toolsets not found');
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

      handleConnectorResponse(connectorResponse, res, 'Authenticating toolset instance', 'Failed to authenticate toolset instance');
    } catch (error: any) {
      logger.error('Error authenticating toolset instance', { error: error.message, instanceId: req.params.instanceId });
      next(handleBackendError(error, 'authenticate toolset instance'));
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

      handleConnectorResponse(connectorResponse, res, 'Removing toolset credentials', 'Failed to remove credentials');
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

      handleConnectorResponse(connectorResponse, res, 'Re-authenticating toolset instance', 'Failed to re-authenticate');
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

      handleConnectorResponse(connectorResponse, res, 'Getting instance OAuth URL', 'Failed to get OAuth authorization URL');
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

      handleConnectorResponse(connectorResponse, res, 'Getting instance status', 'Instance status not found');
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

      handleConnectorResponse(connectorResponse, res, 'Listing toolset OAuth configs', 'OAuth configs not found');
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

      handleConnectorResponse(connectorResponse, res, 'Updating toolset OAuth config', 'Failed to update OAuth configuration');
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

      handleConnectorResponse(connectorResponse, res, 'Deleting toolset OAuth config', 'Failed to delete OAuth configuration');
    } catch (error: any) {
      logger.error('Error deleting toolset OAuth config', { error: error.message, toolsetType: req.params.toolsetType, oauthConfigId: req.params.oauthConfigId });
      next(handleBackendError(error, 'delete toolset OAuth config'));
    }
  };