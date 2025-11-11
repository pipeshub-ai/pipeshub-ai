import { AuthenticatedUserRequest } from './../../../libs/middlewares/types';
import { NextFunction, Response } from 'express';
import { Logger } from '../../../libs/services/logger.service';
import {
  BadRequestError,
  ForbiddenError,
  InternalServerError,
  NotFoundError,
  ServiceUnavailableError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  ConnectorServiceCommand,
  ConnectorServiceCommandOptions,
} from '../../../libs/commands/connector_service/connector.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';

const logger = Logger.getInstance({
  service: 'Connector Controller',
});

const CONNECTOR_SERVICE_UNAVAILABLE_MESSAGE =
  'Connector Service is currently unavailable. Please check your network connection or try again later.';

const handleBackendError = (error: any, operation: string): Error => {
  if (error) {
    if (
      (error?.cause && error.cause.code === 'ECONNREFUSED') ||
      (typeof error?.message === 'string' &&
        error.message.includes('fetch failed'))
    ) {
      return new ServiceUnavailableError(
        CONNECTOR_SERVICE_UNAVAILABLE_MESSAGE,
        error,
      );
    }
    
    const { statusCode, data, message } = error;
    const errorDetail =
      data?.detail || data?.reason || data?.message || message || 'Unknown error';

    logger.error(`Backend error during ${operation}`, {
      statusCode,
      errorDetail,
      fullResponse: data,
    });

    if (errorDetail === 'ECONNREFUSED') {
      throw new ServiceUnavailableError(
        CONNECTOR_SERVICE_UNAVAILABLE_MESSAGE,
        error,
      );
    }

    switch (statusCode) {
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

// Helper function to execute connector service commands
const executeConnectorCommand = async (
  uri: string,
  method: HttpMethod,
  headers: Record<string, string>,
  body?: any,
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

// Helper function to handle common connector response logic
const handleConnectorResponse = (
  connectorResponse: any,
  res: Response,
  operation: string,
  failureMessage: string,
) => {
  if (connectorResponse && connectorResponse.statusCode !== 200) {
    throw handleBackendError(connectorResponse, operation);
  }
  const connectorsData = connectorResponse.data;
  if (!connectorsData) {
    throw new NotFoundError(`${operation} failed: ${failureMessage}`);
  }
  res.status(200).json(connectorsData);
};

export const getConnectors =
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

      logger.info(`Getting all connectors for user ${userId}`);
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors`,
        HttpMethod.GET,
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Connectors not found',
        'get all connectors',
      );
    } catch (error: any) {
      logger.error('Error getting all connectors', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handleError = handleBackendError(error, 'get all connectors');
      next(handleError);
    }
  };

export const getConnectorByName =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorName } = req.params;
      if (!connectorName) {
        throw new BadRequestError('Connector name is required');
      }
      logger.info(`Getting connector by name: ${connectorName}`);
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorName}`,
        HttpMethod.GET,
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Connector not found',
        'get connector by name',
      );
    } catch (error: any) {
      logger.error('Error getting connector by name', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handleError = handleBackendError(error, 'get connector by name');
      next(handleError);
    }
  };

export const getActiveConnectors =
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
      logger.info(`Getting all active connectors for user ${userId}`);
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/active`,
        HttpMethod.GET,
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Active connectors not found',
        'get all active connectors',
      );
    } catch (error: any) {
      logger.error('Error getting all active connectors', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handleError = handleBackendError(
        error,
        'get all active connectors',
      );
      next(handleError);
    }
  };

export const getInactiveConnectors =
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
      logger.info(`Getting all inactive connectors for user ${userId}`);
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/inactive`,
        HttpMethod.GET,
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Inactive connectors not found',
        'get all inactive connectors',
      );
    } catch (error: any) {
      logger.error('Error getting all inactive connectors', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handleError = handleBackendError(
        error,
        'get all inactive connectors',
      );
      next(handleError);
    }
  };

export const getConnectorConfig =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorName } = req.params;
      if (!connectorName) {
        throw new BadRequestError('Connector name is required');
      }
      logger.info(`Getting connector config for ${connectorName}`);
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/config/${connectorName}`,
        HttpMethod.GET,
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Connector config not found',
        'get connector config',
      );
    } catch (error: any) {
      logger.error('Error getting connector config', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handleError = handleBackendError(error, 'get connector config');
      next(handleError);
    }
  };

export const updateConnectorConfig =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorName } = req.params;
      const { auth, sync, filters, baseUrl } = req.body;
      const config = { auth, sync, filters, base_url: baseUrl };
      if (!connectorName) {
        throw new BadRequestError('Connector name is required');
      }
      if (!config) {
        throw new BadRequestError('Config is required');
      }
      logger.info(`Updating connector config for ${connectorName}`);
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/config/${connectorName}`,
        HttpMethod.PUT,
        req.headers as Record<string, string>,
        config,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Connector config not found',
        'update connector config',
      );
    } catch (error: any) {
      logger.error('Error updating connector config', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handleError = handleBackendError(error, 'update connector config');
      next(handleError);
    }
  };

export const getConnectorSchema =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorName } = req.params;
      if (!connectorName) {
        throw new BadRequestError('Connector name is required');
      }
      logger.info(`Getting connector schema for ${connectorName}`);
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/schema/${connectorName}`,
        HttpMethod.GET,
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Connector schema not found',
        'get connector schema',
      );
    } catch (error: any) {
      logger.error('Error getting connector schema', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handleError = handleBackendError(error, 'get connector schema');
      next(handleError);
    }
  };

export const getConnectorConfigAndSchema =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorName } = req.params;
      if (!connectorName) {
        throw new BadRequestError('Connector name is required');
      }
      logger.info(`Getting connector config and schema for ${connectorName}`);
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/config-schema/${connectorName}`,
        HttpMethod.GET,
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Connector config and schema not found',
        'get connector config and schema',
      );
    } catch (error: any) {
      logger.error('Error getting connector config and schema', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handleError = handleBackendError(error, 'get connector config and schema');
      next(handleError);
    }
  };

export const toggleConnector =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorName } = req.params;
      if (!connectorName) {
        throw new BadRequestError('Connector name is required');
      }
      logger.info(`Toggling connector for ${connectorName}`);
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/toggle/${connectorName}`,
        HttpMethod.POST,
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Connector not found',
        'toggle connector',
      );
    } catch (error: any) {
      logger.error('Error toggling connector', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handleError = handleBackendError(error, 'toggle connector');
      next(handleError);
    }
  };

export const getOAuthAuthorizationUrl =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorName } = req.params;
      const { baseUrl } = req.query;
      if (!connectorName) {
        throw new BadRequestError('Connector name is required');
      }
      const queryParams = new URLSearchParams();
      if (baseUrl) queryParams.set('base_url', String(baseUrl));
      const authorizationUrl = `${appConfig.connectorBackend}/api/v1/connectors/${connectorName}/oauth/authorize?${queryParams.toString()}`;

      logger.info(`Getting OAuth authorization url for ${connectorName}`);
      const connectorResponse = await executeConnectorCommand(
        authorizationUrl,
        HttpMethod.GET,
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'OAuth authorization url not found',
        'get OAuth authorization url',
      );
    } catch (error: any) {
      logger.error('Error getting OAuth authorization url', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handleError = handleBackendError(
        error,
        'get OAuth authorization url',
      );
      next(handleError);
    }
  };

export const getConnectorFilterOptions =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorName } = req.params;
      if (!connectorName) {
        throw new BadRequestError('Connector name is required');
      }
      logger.info(`Getting connector filter options for ${connectorName}`);
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/filters/${connectorName}`,
        HttpMethod.GET,
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Connector filter options not found',
        'get connector filter options',
      );
    } catch (error: any) {
      logger.error('Error getting connector filter options', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handleError = handleBackendError(
        error,
        'get connector filter options',
      );
      next(handleError);
    }
  };

export const saveConnectorFilterOptions =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorName } = req.params;
      const { filterOptions } = req.body;
      if (!connectorName) {
        throw new BadRequestError('Connector name is required');
      }
      if (!filterOptions) {
        throw new BadRequestError('Filter options are required');
      }
      logger.info(`Saving connector filter options for ${connectorName}`);
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/filters/${connectorName}`,
        HttpMethod.POST,
        req.headers as Record<string, string>,
        filterOptions,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Connector filter options not found',
        'save connector filter options',
      );
    } catch (error: any) {
      logger.error('Error saving connector filter options', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handleError = handleBackendError(
        error,
        'save connector filter options',
      );
      next(handleError);
    }
  };

export const handleOAuthCallback =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorName } = req.params;
      const { baseUrl } = req.query;
      const { code, state, error } = req.query;
      if (!connectorName) {
        throw new BadRequestError('Connector name is required');
      }
      logger.info(`Handling OAuth callback for ${connectorName}`);
      if (!code || !state) {
        throw new BadRequestError('Code and state are required');
      }

      const queryParams = new URLSearchParams();
      if (code) queryParams.set('code', String(code));
      if (state) queryParams.set('state', String(state));
      if (error) queryParams.set('error', String(error));
      if (baseUrl) queryParams.set('base_url', String(baseUrl));
      const callBackUrl = `${appConfig.connectorBackend}/api/v1/connectors/${connectorName}/oauth/callback?${queryParams.toString()}`;

      // Call Python backend to handle OAuth callback
      const connectorResponse = await executeConnectorCommand(
        callBackUrl,
        HttpMethod.GET,
        req.headers as Record<string, string>,
      );

      // Check if the response is a redirect (from Python backend)
      if (
        connectorResponse &&
        connectorResponse.statusCode === 302 &&
        connectorResponse.headers?.location
      ) {
        // Python backend returned a redirect; send JSON so frontend navigates to avoid CORS
        const redirectUrl = connectorResponse.headers?.location;
        if (redirectUrl) {
          res.status(200).json({ redirectUrl });
          return;
        }
      }

      // Check if Python backend returned JSON response (success/error with redirect URL)
      if (connectorResponse && connectorResponse.data) {
        const responseData = connectorResponse.data as any;
        // Normalize possible string values
        const successFlag = Boolean(responseData.success);
        const redirectUrlFromJson = responseData.redirect_url as
          | string
          | undefined;
        if (responseData.success && redirectUrlFromJson) {
          // Return JSON for frontend navigation
          res.status(200).json({ redirectUrl: redirectUrlFromJson });
          return;
        } else if (!successFlag && redirectUrlFromJson) {
          // Return JSON error with redirect target
          res.status(200).json({ redirectUrl: redirectUrlFromJson });
          return;
        }
      }

      // If not a redirect, handle as normal response
      handleConnectorResponse(
        connectorResponse,
        res,
        'handle OAuth callback failed',
        'handle OAuth callback',
      );
    } catch (error: any) {
      logger.error('Error handling OAuth callback', {
        error: error.message,
        userId: req.user?.userId,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handleError = handleBackendError(error, 'handle OAuth callback');
      next(handleError);
    }
  };
