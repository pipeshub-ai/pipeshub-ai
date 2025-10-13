/**
 * Connector Controllers
 * 
 * Controllers for managing connector instances and configurations.
 * These controllers act as a proxy layer between the frontend and the Python backend,
 * handling authentication, validation, and error transformation.
 */

import { NextFunction, Response } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import {
  BadRequestError,
  ForbiddenError,
  InternalServerError,
  NotFoundError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  ConnectorServiceCommand,
  ConnectorServiceCommandOptions,
} from '../../../libs/commands/connector_service/connector.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';

const logger = Logger.getInstance({
  service: 'ConnectorController',
});

const CONNECTOR_SERVICE_UNAVAILABLE_MESSAGE =
  'Connector Service is currently unavailable. Please check your network connection or try again later.';

/**
 * Handle errors from the backend service and convert to appropriate HTTP errors.
 * 
 * @param error - Error object from backend
 * @param operation - Description of the operation that failed
 * @returns Appropriate HTTP error
 */
const handleBackendError = (error: any, operation: string): Error => {
  if (error.response) {
    const { status, data } = error.response;
    const errorDetail =
      data?.detail || data?.reason || data?.message || 'Unknown error';

    logger.error(`Backend error during ${operation}`, {
      status,
      errorDetail,
      fullResponse: data,
    });

    if (errorDetail === 'ECONNREFUSED') {
      return new InternalServerError(
        CONNECTOR_SERVICE_UNAVAILABLE_MESSAGE,
        error,
      );
    }

    switch (status) {
      case 400:
        return new BadRequestError(errorDetail);
      case 401:
        return new UnauthorizedError(errorDetail);
      case 403:
        return new ForbiddenError(errorDetail);
      case 404:
        return new NotFoundError(errorDetail);
      case 500:
        return new InternalServerError(errorDetail);
      default:
        return new InternalServerError(`Backend error: ${errorDetail}`);
    }
  }

  if (error.request) {
    logger.error(`No response from backend during ${operation}`);
    return new InternalServerError('Backend service unavailable');
  }

  return new InternalServerError(`${operation} failed: ${error.message}`);
};

/**
 * Execute a command against the connector service.
 * 
 * @param uri - Request URI
 * @param method - HTTP method
 * @param headers - Request headers
 * @param body - Optional request body
 * @returns Response from connector service
 */
const executeConnectorCommand = async (
  uri: string,
  method: HttpMethod,
  headers: Record<string, string>,
  body?: any
) => {
  const connectorCommandOptions: ConnectorServiceCommandOptions = {
    uri,
    method,
    headers: {
      ...headers,
      'Content-Type': 'application/json',
    },
    ...(body && { body }),
  };
  
  const connectorCommand = new ConnectorServiceCommand(connectorCommandOptions);
  return await connectorCommand.execute();
};

/**
 * Handle connector response and send appropriate HTTP response.
 * 
 * @param connectorResponse - Response from connector service
 * @param res - Express response object
 * @param notFoundMessage - Message for 404 errors
 * @param failureMessage - Message for other failures
 */
const handleConnectorResponse = (
  connectorResponse: any,
  res: Response,
  notFoundMessage: string,
  failureMessage: string
) => {
  if (connectorResponse && connectorResponse.statusCode !== 200) {
    throw new BadRequestError(failureMessage);
  }
  
  const responseData = connectorResponse.data;
  if (!responseData) {
    throw new NotFoundError(notFoundMessage);
  }
  
  res.status(200).json(responseData);
};

// ============================================================================
// Registry & Instance Controllers
// ============================================================================

/**
 * Get all available connector types from registry.
 */
export const getConnectorRegistry =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};

      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }

      logger.info(`Getting connector registry for user ${userId}`);
      
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/registry`,
        HttpMethod.GET,
        req.headers as Record<string, string>
      );
      
      handleConnectorResponse(
        connectorResponse,
        res,
        'Connector registry not found',
        'Failed to get connector registry'
      );
    } catch (error: any) {
      logger.error('Error getting connector registry', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handledError = handleBackendError(error, 'get connector registry');
      next(handledError);
    }
  };

/**
 * Get all configured connector instances.
 */
export const getConnectorInstances =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};

      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }

      logger.info(`Getting connector instances for user ${userId}`);
      
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/`,
        HttpMethod.GET,
        req.headers as Record<string, string>
      );
      
      handleConnectorResponse(
        connectorResponse,
        res,
        'Connector instances not found',
        'Failed to get connector instances'
      );
    } catch (error: any) {
      logger.error('Error getting connector instances', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handledError = handleBackendError(error, 'get connector instances');
      next(handledError);
    }
  };

/**
 * Get all active connector instances.
 */
export const getActiveConnectorInstances =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};
      
      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }
      
      logger.info(`Getting active connector instances for user ${userId}`);
      
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/active`,
        HttpMethod.GET,
        req.headers as Record<string, string>
      );
      
      handleConnectorResponse(
        connectorResponse,
        res,
        'Active connector instances not found',
        'Failed to get active connector instances'
      );
    } catch (error: any) {
      logger.error('Error getting active connector instances', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handledError = handleBackendError(
        error,
        'get active connector instances',
      );
      next(handledError);
    }
  };

/**
 * Get all inactive connector instances.
 */
export const getInactiveConnectorInstances =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};
      
      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }
      
      logger.info(`Getting inactive connector instances for user ${userId}`);
      
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/inactive`,
        HttpMethod.GET,
        req.headers as Record<string, string>
      );
      
      handleConnectorResponse(
        connectorResponse,
        res,
        'Inactive connector instances not found',
        'Failed to get inactive connector instances'
      );
    } catch (error: any) {
      logger.error('Error getting inactive connector instances', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handledError = handleBackendError(
        error,
        'get inactive connector instances',
      );
      next(handledError);
    }
  };

/**
 * Get all configured connector instances.
 */
export const getConfiguredConnectorInstances =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};
      
      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }
      
      logger.info(`Getting configured connector instances for user ${userId}`);
      
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/configured`,
        HttpMethod.GET,
        req.headers as Record<string, string>
      );
      
      handleConnectorResponse(
        connectorResponse,
        res,
        'Configured connector instances not found',
        'Failed to get configured connector instances'
      );
    } catch (error: any) {
      logger.error('Error getting configured connector instances', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handledError = handleBackendError(
        error,
        'get configured connector instances',
      );
      next(handledError);
    }
  };

// ============================================================================
// Instance Management Controllers
// ============================================================================

/**
 * Create a new connector instance.
 */
export const createConnectorInstance =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};
      const { connectorType, instanceName, config, baseUrl } = req.body;
      
      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }
      
      if (!connectorType || !instanceName) {
        throw new BadRequestError('connector_type and instanceName are required');
      }
      
      logger.info(`Creating connector instance for user ${userId}`, {
        connectorType,
        instanceName,
      });
      
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/`,
        HttpMethod.POST,
        req.headers as Record<string, string>,
        { connectorType, instanceName, config, baseUrl }
      );
      
      handleConnectorResponse(
        connectorResponse,
        res,
        'Failed to create connector instance',
        'Failed to create connector instance'
      );
    } catch (error: any) {
      logger.error('Error creating connector instance', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handledError = handleBackendError(error, 'create connector instance');
      next(handledError);
    }
  };

/**
 * Get a specific connector instance.
 */
export const getConnectorInstance =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId } = req.params;
      
      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }
      
      logger.info(`Getting connector instance ${connectorId}`);
      
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}`,
        HttpMethod.GET,
        req.headers as Record<string, string>
      );
      
      handleConnectorResponse(
        connectorResponse,
        res,
        'Connector instance not found',
        'Failed to get connector instance'
      );
    } catch (error: any) {
      logger.error('Error getting connector instance', {
        error: error.message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handledError = handleBackendError(error, 'get connector instance');
      next(handledError);
    }
  };

/**
 * Get connector instance configuration.
 */
export const getConnectorInstanceConfig =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId } = req.params;
      
      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }
      
      logger.info(`Getting connector instance config for ${connectorId}`);
      
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/config`,
        HttpMethod.GET,
        req.headers as Record<string, string>
      );
      
      handleConnectorResponse(
        connectorResponse,
        res,
        'Connector instance config not found',
        'Failed to get connector instance config'
      );
    } catch (error: any) {
      logger.error('Error getting connector instance config', {
        error: error.message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handledError = handleBackendError(
        error,
        'get connector instance config'
      );
      next(handledError);
    }
  };

/**
 * Update connector instance configuration.
 */
export const updateConnectorInstanceConfig =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId } = req.params;
      const { auth, sync, filters, baseUrl } = req.body;
      
      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }
      
      const config = {
        auth,
        sync,
        filters,
        baseUrl: baseUrl,
      };
      
      logger.info(`Updating connector instance config for ${connectorId}`);
      
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/config`,
        HttpMethod.PUT,
        req.headers as Record<string, string>,
        config
      );
      
      handleConnectorResponse(
        connectorResponse,
        res,
        'Connector instance not found',
        'Failed to update connector instance config'
      );
    } catch (error: any) {
      logger.error('Error updating connector instance config', {
        error: error.message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handledError = handleBackendError(
        error,
        'update connector instance config'
      );
      next(handledError);
    }
  };

/**
 * Delete a connector instance.
 */
export const deleteConnectorInstance =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId } = req.params;
      
      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }
      
      logger.info(`Deleting connector instance ${connectorId}`);
      
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}`,
        HttpMethod.DELETE,
        req.headers as Record<string, string>
      );
      
      handleConnectorResponse(
        connectorResponse,
        res,
        'Connector instance not found',
        'Failed to delete connector instance'
      );
    } catch (error: any) {
      logger.error('Error deleting connector instance', {
        error: error.message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handledError = handleBackendError(error, 'delete connector instance');
      next(handledError);
    }
  };

/**
 * Update connector instance name.
 */
export const updateConnectorInstanceName =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId } = req.params;
      const { instanceName } = req.body as { instanceName: string };

      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }
      if (!instanceName || !instanceName.trim()) {
        throw new BadRequestError('instanceName is required');
      }

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/name`,
        HttpMethod.PUT,
        req.headers as Record<string, string>,
        { instanceName: instanceName }
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Connector instance not found',
        'Failed to update connector instance name'
      );
    } catch (error: any) {
      const handledError = handleBackendError(error, 'update connector instance name');
      next(handledError);
    }
  };

// ============================================================================
// OAuth Controllers
// ============================================================================

/**
 * Get OAuth authorization URL for a connector instance.
 */
export const getOAuthAuthorizationUrl =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId } = req.params;
      const { baseUrl } = req.query;
      
      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }
      
      const queryParams = new URLSearchParams();
      if (baseUrl) {
        queryParams.set('base_url', String(baseUrl));
      }
      
      const authorizationUrl = 
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/oauth/authorize?${queryParams.toString()}`;

      logger.info(`Getting OAuth authorization URL for instance ${connectorId}`);
      
      const connectorResponse = await executeConnectorCommand(
        authorizationUrl,
        HttpMethod.GET,
        req.headers as Record<string, string>,
      );
      
      handleConnectorResponse(
        connectorResponse,
        res,
        'OAuth authorization URL not found',
        'Failed to get OAuth authorization URL'
      );
    } catch (error: any) {
      logger.error('Error getting OAuth authorization URL', {
        error: error.message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handledError = handleBackendError(
        error,
        'get OAuth authorization URL',
      );
      next(handledError);
    }
  };

/**
 * Handle OAuth callback.
 */
export const handleOAuthCallback =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { baseUrl, code, state, error } = req.query;
      
      if (!code || !state) {
        throw new BadRequestError('Code and state are required');
      }
      
      logger.info('Handling OAuth callback');
      
      const queryParams = new URLSearchParams();
      if (code) queryParams.set('code', String(code));
      if (state) queryParams.set('state', String(state));
      if (error) queryParams.set('error', String(error));
      if (baseUrl) queryParams.set('base_url', String(baseUrl));
      
      const callbackUrl = 
        `${appConfig.connectorBackend}/api/v1/connectors/oauth/callback?${queryParams.toString()}`;

      const connectorResponse = await executeConnectorCommand(
        callbackUrl,
        HttpMethod.GET,
        req.headers as Record<string, string>,
      );
      
      // Handle redirect responses
      if (
        connectorResponse &&
        connectorResponse.statusCode === 302 &&
        connectorResponse.headers?.location
      ) {
        const redirectUrl = connectorResponse.headers.location;
        res.status(200).json({ redirectUrl });
        return;
      }
      
      // Handle JSON responses with redirect URL
      if (connectorResponse && connectorResponse.data) {
        const responseData = connectorResponse.data as any;
        const redirectUrlFromJson = responseData.redirect_url as string | undefined;
        
        if (redirectUrlFromJson) {
          res.status(200).json({ redirectUrl: redirectUrlFromJson });
          return;
        }
      }
      
      // Handle normal response
      handleConnectorResponse(
        connectorResponse,
        res,
        'OAuth callback failed',
        'Failed to handle OAuth callback'
      );
    } catch (error: any) {
      logger.error('Error handling OAuth callback', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handledError = handleBackendError(error, 'handle OAuth callback');
      next(handledError);
    }
  };

// ============================================================================
// Filter Controllers
// ============================================================================

/**
 * Get filter options for a connector instance.
 */
export const getConnectorInstanceFilterOptions =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId } = req.params;
      
      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }
      
      logger.info(`Getting filter options for instance ${connectorId}`);
      
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/filters`,
        HttpMethod.GET,
        req.headers as Record<string, string>
      );
      
      handleConnectorResponse(
        connectorResponse,
        res,
        'Connector instance filter options not found',
        'Failed to get connector instance filter options'
      );
    } catch (error: any) {
      logger.error('Error getting connector instance filter options', {
        error: error.message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handledError = handleBackendError(
        error,
        'get connector instance filter options',
      );
      next(handledError);
    }
  };

/**
 * Save filter options for a connector instance.
 */
export const saveConnectorInstanceFilterOptions =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId } = req.params;
      const { filters } = req.body;
      
      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }
      
      if (!filters) {
        throw new BadRequestError('Filters are required');
      }
      
        logger.info(`Saving filter options for instance ${connectorId}`);
      
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/filters`,
        HttpMethod.POST,
        req.headers as Record<string, string>,
        { filters }
      );
      
      handleConnectorResponse(
        connectorResponse,
        res,
        'Connector instance not found',
        'Failed to save connector instance filter options'
      );
    } catch (error: any) {
      logger.error('Error saving connector instance filter options', {
        error: error.message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handledError = handleBackendError(
        error,
        'save connector instance filter options',
      );
      next(handledError);
    }
  };

// ============================================================================
// Toggle Controller
// ============================================================================

/**
 * Toggle connector instance active status.
 */
export const toggleConnectorInstance =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId } = req.params;
      
      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }
      
      logger.info(`Toggling connector instance ${connectorId}`);
      
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/toggle`,
        HttpMethod.POST,
        req.headers as Record<string, string>
      );
      
      handleConnectorResponse(
        connectorResponse,
        res,
        'Connector instance not found',
        'Failed to toggle connector instance'
      );
    } catch (error: any) {
      logger.error('Error toggling connector instance', {
        error: error.message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handledError = handleBackendError(error, 'toggle connector instance');
      next(handledError);
    }
  };

// ============================================================================
// Schema Controller
// ============================================================================

/**
 * Get connector schema from registry.
 */
export const getConnectorSchema =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorType } = req.params;
      
      if (!connectorType) {
        throw new BadRequestError('Connector type is required');
      }
      
      logger.info(`Getting connector schema for ${connectorType}`);
      
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/registry/${connectorType}/schema`,
        HttpMethod.GET,
        req.headers as Record<string, string>
      );
      
      handleConnectorResponse(
        connectorResponse,
        res,
        'Connector schema not found',
        'Failed to get connector schema'
      );
    } catch (error: any) {
      logger.error('Error getting connector schema', {
        error: error.message,
        connectorType: req.params.connectorType,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handledError = handleBackendError(error, 'get connector schema');
      next(handledError);
    }
  };