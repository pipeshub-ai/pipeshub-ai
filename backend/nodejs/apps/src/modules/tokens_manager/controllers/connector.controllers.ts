import { AuthenticatedUserRequest } from './../../../libs/middlewares/types';
import { NextFunction, Response } from 'express';
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
  service: 'Connector Controller',
});

const CONNECTOR_SERVICE_UNAVAILABLE_MESSAGE =
  'Connector Service is currently unavailable. Please check your network connection or try again later.';

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
      throw new InternalServerError(
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
      const connectorCommandOptions: ConnectorServiceCommandOptions = {
        uri: `${appConfig.connectorBackend}/api/v1/connectors`,
        method: HttpMethod.GET,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const connectorCommand = new ConnectorServiceCommand(
        connectorCommandOptions,
      );
      const connectorResponse = await connectorCommand.execute();
      if (connectorResponse && connectorResponse.statusCode !== 200) {
        throw new BadRequestError('Failed to get all connectors');
      }
      const connectorsData = connectorResponse.data;

      if (!connectorsData) {
        throw new NotFoundError('Connectors not found');
      }

      res.status(200).json(connectorsData);
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
      const connectorCommandOptions: ConnectorServiceCommandOptions = {
        uri: `${appConfig.connectorBackend}/api/v1/connectors/active`,
        method: HttpMethod.GET,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const connectorCommand = new ConnectorServiceCommand(
        connectorCommandOptions,
      );
      const connectorResponse = await connectorCommand.execute();
      if (connectorResponse && connectorResponse.statusCode !== 200) {
        throw new BadRequestError('Failed to get all active connectors');
      }
      const connectorsData = connectorResponse.data;
      if (!connectorsData) {
        throw new NotFoundError('Active connectors not found');
      }
      res.status(200).json(connectorsData);
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
      const connectorCommandOptions: ConnectorServiceCommandOptions = {
        uri: `${appConfig.connectorBackend}/api/v1/connectors/inactive`,
        method: HttpMethod.GET,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const connectorCommand = new ConnectorServiceCommand(
        connectorCommandOptions,
      );
      const connectorResponse = await connectorCommand.execute();
      if (connectorResponse && connectorResponse.statusCode !== 200) {
        throw new BadRequestError('Failed to get all inactive connectors');
      }
      const connectorsData = connectorResponse.data;
      if (!connectorsData) {
        throw new NotFoundError('Inactive connectors not found');
      }
      res.status(200).json(connectorsData);
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
      const connectorCommandOptions: ConnectorServiceCommandOptions = {
        uri: `${appConfig.connectorBackend}/api/v1/connectors/config/${connectorName}`,
        method: HttpMethod.GET,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const connectorCommand = new ConnectorServiceCommand(
        connectorCommandOptions,
      );
      const connectorResponse = await connectorCommand.execute();
      if (connectorResponse && connectorResponse.statusCode !== 200) {
        throw new BadRequestError('Failed to get connector config');
      }
      const connectorsData = connectorResponse.data;
      if (!connectorsData) {
        throw new NotFoundError('Connector config not found');
      }
      res.status(200).json(connectorsData);
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
      const { auth, sync, filters } = req.body;
      const config = { auth, sync, filters };
      if (!connectorName) {
        throw new BadRequestError('Connector name is required');
      }
      if (!config) {
        throw new BadRequestError('Config is required');
      }
      logger.info(`Updating connector config for ${connectorName}`);
      const connectorCommandOptions: ConnectorServiceCommandOptions = {
        uri: `${appConfig.connectorBackend}/api/v1/connectors/config/${connectorName}`,
        method: HttpMethod.PUT,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        body: config,
      };
      const connectorCommand = new ConnectorServiceCommand(
        connectorCommandOptions,
      );
      const connectorResponse = await connectorCommand.execute();
      if (connectorResponse && connectorResponse.statusCode !== 200) {
        throw new BadRequestError('Failed to update connector config');
      }
      const connectorsData = connectorResponse.data;
      if (!connectorsData) {
        throw new NotFoundError('Connector config not found');
      }
      res.status(200).json(connectorsData);
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
      const connectorCommandOptions: ConnectorServiceCommandOptions = {
        uri: `${appConfig.connectorBackend}/api/v1/connectors/schema/${connectorName}`,
        method: HttpMethod.GET,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const connectorCommand = new ConnectorServiceCommand(
        connectorCommandOptions,
      );
      const connectorResponse = await connectorCommand.execute();
      if (connectorResponse && connectorResponse.statusCode !== 200) {
        throw new InternalServerError('Failed to get connector schema');
      }
      const connectorsData = connectorResponse.data;
      if (!connectorsData) {
        throw new NotFoundError('Connector schema not found');
      }
      res.status(200).json(connectorsData);
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
      const connectorCommandOptions: ConnectorServiceCommandOptions = {
        uri: `${appConfig.connectorBackend}/api/v1/connectors/toggle/${connectorName}`,
        method: HttpMethod.POST,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const connectorCommand = new ConnectorServiceCommand(
        connectorCommandOptions,
      );
      const connectorResponse = await connectorCommand.execute();
      if (connectorResponse && connectorResponse.statusCode !== 200) {
        throw new InternalServerError('Failed to toggle connector');
      }
      const connectorsData = connectorResponse.data;
      if (!connectorsData) {
        throw new NotFoundError('Connector not found');
      }
      res.status(200).json(connectorsData);
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
      if (!connectorName) {
        throw new BadRequestError('Connector name is required');
      }
      logger.info(`Getting OAuth authorization url for ${connectorName}`);
      const connectorCommandOptions: ConnectorServiceCommandOptions = {
        uri: `${appConfig.connectorBackend}/api/v1/connectors/${connectorName}/oauth/authorize`,
        method: HttpMethod.GET,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const connectorCommand = new ConnectorServiceCommand(
        connectorCommandOptions,
      );
      const connectorResponse = await connectorCommand.execute();
      if (connectorResponse && connectorResponse.statusCode !== 200) {
        throw new InternalServerError('Failed to get OAuth authorization url');
      }
      const connectorsData = connectorResponse.data;
      if (!connectorsData) {
        throw new NotFoundError('OAuth authorization url not found');
      }
      res.status(200).json(connectorsData);
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
      const connectorCommandOptions: ConnectorServiceCommandOptions = {
        uri: `${appConfig.connectorBackend}/api/v1/connectors/filters/${connectorName}`,
        method: HttpMethod.GET,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const connectorCommand = new ConnectorServiceCommand(
        connectorCommandOptions,
      );
      const connectorResponse = await connectorCommand.execute();
      if (connectorResponse && connectorResponse.statusCode !== 200) {
        throw new InternalServerError('Failed to get connector filter options');
      }
      const connectorsData = connectorResponse.data;
      if (!connectorsData) {
        throw new NotFoundError('Connector filter options not found');
      }
      res.status(200).json(connectorsData);
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
      const connectorCommandOptions: ConnectorServiceCommandOptions = {
        uri: `${appConfig.connectorBackend}/api/v1/connectors/filters/${connectorName}`,
        method: HttpMethod.POST,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
        body: filterOptions,
      };
      const connectorCommand = new ConnectorServiceCommand(
        connectorCommandOptions,
      );
      const connectorResponse = await connectorCommand.execute();
      if (connectorResponse && connectorResponse.statusCode !== 200) {
        throw new InternalServerError(
          'Failed to save connector filter options',
        );
      }
      const connectorsData = connectorResponse.data;
      if (!connectorsData) {
        throw new NotFoundError('Connector filter options not found');
      }
      res.status(200).json(connectorsData);
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
      if (!connectorName) {
        throw new BadRequestError('Connector name is required');
      }
      logger.info(`Handling OAuth callback for ${connectorName}`);
      const connectorCommandOptions: ConnectorServiceCommandOptions = {
        uri: `${appConfig.connectorBackend}/api/v1/connectors/${connectorName}/oauth/callback`,
        method: HttpMethod.POST,
        headers: {
          ...(req.headers as Record<string, string>),
          'Content-Type': 'application/json',
        },
      };
      const connectorCommand = new ConnectorServiceCommand(
        connectorCommandOptions,
      );
      const connectorResponse = await connectorCommand.execute();
      if (connectorResponse && connectorResponse.statusCode !== 200) {
        throw new InternalServerError('Failed to handle OAuth callback');
      }
      const connectorsData = connectorResponse.data;
      if (!connectorsData) {
        throw new NotFoundError('OAuth callback not found');
      }
      res.status(200).json(connectorsData);
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
