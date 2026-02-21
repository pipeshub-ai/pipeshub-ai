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
