/**
 * Connector Controllers
 *
 * Controllers for managing connector instances and configurations.
 * These controllers act as a proxy layer between the frontend and the Python backend,
 * handling authentication, validation, and error transformation.
 */

import { NextFunction, Response } from 'express';
import axios from 'axios';
import FormData from 'form-data';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import {
  BadRequestError,
  NotFoundError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { AppConfig } from '../../tokens_manager/config/config';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { UserGroups } from '../../user_management/schema/userGroup.schema';
import {
  executeConnectorCommand,
  getConnectorErrorLogFields,
  handleBackendError,
  handleConnectorResponse,
} from '../utils/connector-service-http';

const logger = Logger.getInstance({
  service: 'Connector Controller',
});

// Headers we forward to the Python connector backend. Authorization carries
// the verified caller identity (orgId/userId/role); tracing headers preserve
// request correlation. Anything else (cookie, host, user-agent, arbitrary
// x-* headers from the client) is dropped to avoid header-injection paths.
const PROXY_FORWARD_HEADERS: readonly string[] = [
  'authorization',
  'x-request-id',
  'x-correlation-id',
  'x-forwarded-for',
  'accept-language',
];

const buildProxyHeaders = (
  req: AuthenticatedUserRequest,
  isAdmin: boolean,
): Record<string, string> => {
  const headers: Record<string, string> = {};
  for (const name of PROXY_FORWARD_HEADERS) {
    const value = req.headers[name];
    if (typeof value === 'string') {
      headers[name] = value;
    } else if (Array.isArray(value)) {
      headers[name] = value.join(',');
    }
  }
  headers['X-Is-Admin'] = isAdmin ? 'true' : 'false';
  return headers;
};

// Defense-in-depth ownership check at the gateway. Connector instance
// metadata lives in the Python backend, so we cannot do a local
// `findOne({ _id, orgId })`. Instead we probe the connector via GET using
// the caller's auth context — a 4xx means the caller cannot see it (or it
// does not exist), and we refuse to proxy the write. Returns NotFoundError
// (not Forbidden) so cross-tenant probing cannot enumerate IDs by status.
const assertConnectorAccessible = async (
  appConfig: AppConfig,
  connectorId: string,
  headers: Record<string, string>,
): Promise<void> => {
  const probe = await executeConnectorCommand(
    `${appConfig.connectorBackend}/api/v1/connectors/${encodeURIComponent(connectorId)}`,
    HttpMethod.GET,
    headers,
  );
  const status = probe?.statusCode;
  if (typeof status !== 'number' || status < 200 || status >= 300) {
    throw new NotFoundError('Connector not found');
  }
};

const normalizeConnectorFileEventsBody = (body: unknown): unknown => {
  let candidate = body;

  for (let i = 0; i < 3; i += 1) {
    if (typeof candidate === 'string') {
      const trimmed = candidate.trim();
      if (!trimmed) {
        return candidate;
      }
      try {
        candidate = JSON.parse(trimmed);
        continue;
      } catch {
        return candidate;
      }
    }

    if (!candidate || typeof candidate !== 'object' || Array.isArray(candidate)) {
      return candidate;
    }

    const nested = (candidate as Record<string, unknown>).body
      ?? (candidate as Record<string, unknown>).payload
      ?? (candidate as Record<string, unknown>).data;

    if (nested === undefined) {
      return candidate;
    }

    candidate = nested;
  }

  return candidate;
};

/**
 * Higher-order function to create connector config update handlers.
 * Reduces code duplication by centralizing common validation, header preparation,
 * API call execution, and error handling logic.
 *
 * @param appConfig - Application configuration
 * @param endpointPath - API endpoint path segment (e.g., 'auth', 'filters-sync')
 * @param validatePayload - Function to validate the request payload
 * @param createPayload - Function to create the payload from request body
 * @param operationName - Human-readable operation name for logging
 * @returns Express route handler function
 */
const createConnectorConfigUpdateHandler = (
  appConfig: AppConfig,
  endpointPath: string,
  validatePayload: (body: any) => void,
  createPayload: (body: any) => any,
  operationName: string,
) => {
  return async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId } = req.params;

      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }

      // Validate payload
      validatePayload(req.body);

      // Create payload
      const config = createPayload(req.body);

      logger.info(`${operationName} for ${connectorId}`);

      // Prepare headers with admin flag
      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

      // Execute API call
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/config/${endpointPath}`,
        HttpMethod.PUT,
        headers,
        config,
      );

      // Handle response
      handleConnectorResponse(
        connectorResponse,
        res,
        operationName,
        'Connector instance not found',
      );
    } catch (error: unknown) {
      const { message, status, data } = getConnectorErrorLogFields(error);
      logger.error(`Error ${operationName.toLowerCase()}`, {
        error: message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status,
        data,
      });
      const handledError = handleBackendError(error, operationName.toLowerCase());
      next(handledError);
    }
  };
};

export const isUserAdmin = async (req: AuthenticatedUserRequest): Promise<boolean> => {
  const { userId, orgId } = req.user || {};
  if (!userId) {
    throw new UnauthorizedError('User authentication required');
  }
  const groups = await UserGroups.find({
    orgId,
    users: { $in: [userId] },
    isDeleted: false,
  }).select('type');
  const isAdmin = groups.find((userGroup: any) => userGroup.type === 'admin');
  if (!isAdmin) {
    return false;
  }
  return true;
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
      const { scope, page, limit, search } = req.query;

      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }

      logger.info(`Getting connector registry for user ${userId}`);

      const queryParams = new URLSearchParams();
      if (scope) {
        queryParams.append('scope', String(scope));
      }

      if (page) {
        queryParams.append('page', String(page));
      }
      if (limit) {
        queryParams.append('limit', String(limit));
      }
      if (search) {
        queryParams.append('search', String(search));
      }

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/registry?${queryParams.toString()}`,
        HttpMethod.GET,
        headers,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting all connectors from registry',
        'Connectors from registry not found'
      );
    } catch (error: unknown) {
      const { message, status, data } = getConnectorErrorLogFields(error);
      logger.error('Error getting connector registry', {
        error: message,
        userId: req.user?.userId,
        status,
        data,
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
      const { scope, page, limit, search } = req.query;
      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }

      if (!scope) {
        throw new BadRequestError('Scope is required');
      }

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

      const queryParams = new URLSearchParams();
      if (scope) {
        queryParams.append('scope', String(scope));
      }
      if (page) {
        queryParams.append('page', String(page));
      }
      if (limit) {
        queryParams.append('limit', String(limit));
      }
      if (search) {
        queryParams.append('search', String(search));
      }

      logger.info(`Getting connector instances for user ${userId}`);

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/?${queryParams.toString()}`,
        HttpMethod.GET,
        headers,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting connector instances',
        'Connector instances not found'
      );
    } catch (error: unknown) {
      const { message, status, data } = getConnectorErrorLogFields(error);
      logger.error('Error getting connector instances', {
        error: message,
        userId: req.user?.userId,
        status,
        data,
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
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting all active connectors',
        'Active connectors not found'
      );
    } catch (error: unknown) {
      const { message, status, data } = getConnectorErrorLogFields(error);
      logger.error('Error getting active connector instances', {
        error: message,
        userId: req.user?.userId,
        status,
        data,
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
        req.headers as Record<string, string>,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting all inactive connectors',
        'Inactive connectors not found'
      );
    } catch (error: unknown) {
      const { message, status, data } = getConnectorErrorLogFields(error);
      logger.error('Error getting inactive connector instances', {
        error: message,
        userId: req.user?.userId,
        status,
        data,
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
      const { scope, page, limit, search } = req.query;

      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }

      logger.info(`Getting configured connector instances for user ${userId}`);

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

      const queryParams = new URLSearchParams();
      if (scope) {
        queryParams.append('scope', String(scope));
      }
      if (page) {
        queryParams.append('page', String(page));
      }
      if (limit) {
        queryParams.append('limit', String(limit));
      }
      if (search) {
        queryParams.append('search', String(search));
      }


      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/configured?${queryParams.toString()}`,
        HttpMethod.GET,
        headers,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting connector config',
        'Connector config not found'
      );
    } catch (error: unknown) {
      const { message, status, data } = getConnectorErrorLogFields(error);
      logger.error('Error getting configured connector instances', {
        error: message,
        userId: req.user?.userId,
        status,
        data,
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
      const { connectorType, instanceName, config, baseUrl, scope, authType } = req.body;

      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }

      if (!connectorType || !instanceName) {
        throw new BadRequestError(
          'connector_type and instanceName are required',
        );
      }

      logger.info(`Creating connector instance for user ${userId}`, {
        connectorType,
        instanceName,
        authType,
      });

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/`,
        HttpMethod.POST,
        headers,
        { connectorType, instanceName, config, baseUrl, scope, authType },
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Creating connector instance',
        'Connector config not found'
      );
    } catch (error: unknown) {
      const { message, status, data } = getConnectorErrorLogFields(error);
      logger.error('Error creating connector instance', {
        error: message,
        userId: req.user?.userId,
        status,
        data,
      });
      const handledError = handleBackendError(
        error,
        'create connector instance',
      );
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
      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}`,
        HttpMethod.GET,
        headers,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting connector instance',
        'Connector schema not found'
      );
    } catch (error: unknown) {
      const { message, status, data } = getConnectorErrorLogFields(error);
      logger.error('Error getting connector instance', {
        error: message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status,
        data,
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

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/config`,
        HttpMethod.GET,
        headers,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting connector instance config',
        'Connector config and schema not found'
      );
    } catch (error: unknown) {
      const { message, status, data } = getConnectorErrorLogFields(error);
      logger.error('Error getting connector instance config', {
        error: message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status,
        data,
      });
      const handledError = handleBackendError(
        error,
        'get connector instance config',
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

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/config`,
        HttpMethod.PUT,
        headers,
        config,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Updating connector instance config',
        'Connector instance not found',
      );
    } catch (error: unknown) {
      const { message, status, data } = getConnectorErrorLogFields(error);
      logger.error('Error updating connector instance config', {
        error: message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status,
        data,
      });
      const handledError = handleBackendError(
        error,
        'update connector instance config',
      );
      next(handledError);
    }
  };

/**
 * Update authentication configuration for a connector instance.
 * Clears credentials and OAuth state, marks connector as not authenticated.
 */
export const updateConnectorInstanceAuthConfig = (appConfig: AppConfig) =>
  createConnectorConfigUpdateHandler(
    appConfig,
    'auth',
    (body) => {
      if (!body.auth) {
        throw new BadRequestError('Auth configuration is required');
      }
    },
    (body) => ({
      auth: body.auth,
      baseUrl: body.baseUrl,
    }),
    'Updating connector instance auth config',
  );

/**
 * Update filters and sync configuration for a connector instance.
 * Validates that connector is not active and authentication is valid.
 */
export const updateConnectorInstanceFiltersSyncConfig = (appConfig: AppConfig) =>
  createConnectorConfigUpdateHandler(
    appConfig,
    'filters-sync',
    (body) => {
      if (!body.sync && !body.filters) {
        throw new BadRequestError('Sync or filters configuration is required');
      }
    },
    (body) => ({
      sync: body.sync,
      filters: body.filters,
      baseUrl: body.baseUrl,
    }),
    'Updating connector instance filters-sync config',
  );

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

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}`,
        HttpMethod.DELETE,
        headers,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Deleting connector instance',
        'Connector instance not found'
      );
    } catch (error: unknown) {
      const { message, status, data } = getConnectorErrorLogFields(error);
      logger.error('Error deleting connector instance', {
        error: message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status,
        data,
      });
      const handledError = handleBackendError(
        error,
        'delete connector instance',
      );
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

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/name`,
        HttpMethod.PUT,
        headers,
        { instanceName: instanceName },
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Updating connector instance name',
        'Connector instance not found'
      );
    } catch (error: unknown) {
      const handledError = handleBackendError(
        error,
        'update connector instance name',
      );
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

      const authorizationUrl = `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/oauth/authorize?${queryParams.toString()}`;

      logger.info(
        `Getting OAuth authorization URL for instance ${connectorId}`,
      );

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

      const connectorResponse = await executeConnectorCommand(
        authorizationUrl,
        HttpMethod.GET,
        headers,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting OAuth authorization URL',
        'OAuth authorization URL not found'
      );
    } catch (error: unknown) {
      const { message, status, data } = getConnectorErrorLogFields(error);
      logger.error('Error getting OAuth authorization URL', {
        error: message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status,
        data,
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

      const callbackUrl = `${appConfig.connectorBackend}/api/v1/connectors/oauth/callback?${queryParams.toString()}`;

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

      const connectorResponse = await executeConnectorCommand(
        callbackUrl,
        HttpMethod.GET,
        headers,
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
        const redirectUrlFromJson = responseData.redirect_url as
          | string
          | undefined;

        if (redirectUrlFromJson) {
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
    } catch (error: unknown) {
      const { message, status, data } = getConnectorErrorLogFields(error);
      logger.error('Error handling OAuth callback', {
        error: message,
        userId: req.user?.userId,
        status,
        data,
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

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/filters`,
        HttpMethod.GET,
        headers,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting connector instance filter options',
        'Connector instance filter options not found'
      );
    } catch (error: unknown) {
      const { message, status, data } = getConnectorErrorLogFields(error);
      logger.error('Error getting connector instance filter options', {
        error: message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status,
        data,
      });
      const handledError = handleBackendError(
        error,
        'get connector instance filter options',
      );
      next(handledError);
    }
  };

/**
 * Get dynamic filter field options for a connector instance.
 */
export const getFilterFieldOptions =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId, filterKey } = req.params;
      const { page, limit, search, cursor } = req.query;

      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }

      if (!filterKey) {
        throw new BadRequestError('Filter key is required');
      }

      logger.info(`Getting filter field options for instance ${connectorId}, filter ${filterKey}`);

      const isAdmin = await isUserAdmin(req);
      logger.info(`User admin status: ${isAdmin} for userId: ${req.user?.userId}, orgId: ${req.user?.orgId}`);
      
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };
      
      logger.info(`Forwarding to Python with X-Is-Admin header: ${headers['X-Is-Admin']}`);

      // Build query string with cursor support
      const queryParams = new URLSearchParams();
      if (page) queryParams.append('page', String(page));
      if (limit) queryParams.append('limit', String(limit));
      if (search) queryParams.append('search', String(search));
      if (cursor) queryParams.append('cursor', String(cursor));
      const queryString = queryParams.toString() ? `?${queryParams.toString()}` : '';

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/filters/${filterKey}/options${queryString}`,
        HttpMethod.GET,
        headers,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting filter field options',
        'Filter field options not found'
      );
    } catch (error: unknown) {
      const { message, status, data } = getConnectorErrorLogFields(error);
      logger.error('Error getting filter field options', {
        error: message,
        connectorId: req.params.connectorId,
        filterKey: req.params.filterKey,
        userId: req.user?.userId,
        status,
        data,
      });
      const handledError = handleBackendError(
        error,
        'get filter field options',
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

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/filters`,
        HttpMethod.POST,
        headers,
        { filters },
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Saving connector instance filter options',
        'Connector instance filter options not found'
      );
    } catch (error: unknown) {
      const { message, status, data } = getConnectorErrorLogFields(error);
      logger.error('Error saving connector instance filter options', {
        error: message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status,
        data,
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
      const { type, fullSync } = req.body;

      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }

      if (!type) {
        throw new BadRequestError('Toggle type is required');
      }

      logger.info(`Toggling connector instance ${connectorId} with type ${type}`);

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };
      const body: { type: string; fullSync?: boolean } = { type };
      if (typeof fullSync === 'boolean') {
        body.fullSync = fullSync;
      }
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${connectorId}/toggle`,
        HttpMethod.POST,
        headers,
        body,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Toggling connector instance',
        'Connector instance not found'
      );
    } catch (error: unknown) {
      const { message, status, data } = getConnectorErrorLogFields(error);
      logger.error('Error toggling connector instance', {
        error: message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status,
        data,
      });
      const handledError = handleBackendError(
        error,
        'toggle connector instance',
      );
      next(handledError);
    }
  };

export const submitConnectorFileEvents =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId } = req.params;
      const { userId } = req.user || {};

      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }
      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }

      const isAdmin = await isUserAdmin(req);
      const headers = buildProxyHeaders(req, isAdmin);
      await assertConnectorAccessible(appConfig, connectorId, headers);
      const payload = normalizeConnectorFileEventsBody(req.body);

      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/${encodeURIComponent(connectorId)}/file-events`,
        HttpMethod.POST,
        headers,
        payload,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Submitting connector file events',
        'Failed to submit connector file events',
      );
    } catch (error: unknown) {
      const { message, status, data } = getConnectorErrorLogFields(error);
      logger.error('Error submitting connector file events', {
        error: message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status,
        data,
      });
      const handledError = handleBackendError(
        error,
        'submit connector file events',
      );
      next(handledError);
    }
  };

export const submitConnectorFileEventUploads =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { connectorId } = req.params;
      const { userId } = req.user || {};

      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }
      if (!connectorId) {
        throw new BadRequestError('Connector ID is required');
      }
      if (!req.body?.manifest) {
        throw new BadRequestError("Multipart field 'manifest' is required");
      }

      const isAdmin = await isUserAdmin(req);
      const headers = buildProxyHeaders(req, isAdmin);
      await assertConnectorAccessible(appConfig, connectorId, headers);

      const form = new FormData();
      form.append('manifest', String(req.body.manifest));

      const files = ((req as AuthenticatedUserRequest & { files?: Express.Multer.File[] }).files || []);
      for (const file of files) {
        form.append(file.fieldname, file.buffer, {
          filename: file.originalname || file.fieldname,
          contentType: file.mimetype || 'application/octet-stream',
          knownLength: file.size,
        });
      }

      const response = await axios.post(
        `${appConfig.connectorBackend}/api/v1/connectors/${encodeURIComponent(connectorId)}/file-events/upload`,
        form,
        {
          headers: { ...headers, ...form.getHeaders() },
          timeout: 120_000,
          maxBodyLength: Infinity,
          maxContentLength: Infinity,
          validateStatus: () => true,
        },
      );

      res.status(response.status).json(response.data);
    } catch (error: unknown) {
      const { message, status, data } = getConnectorErrorLogFields(error);
      logger.error('Error submitting connector file event uploads', {
        error: message,
        connectorId: req.params.connectorId,
        userId: req.user?.userId,
        status,
        data,
      });
      const handledError = handleBackendError(
        error,
        'submit connector file event uploads',
      );
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

      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}/api/v1/connectors/registry/${connectorType}/schema`,
        HttpMethod.GET,
        headers,
      );

      handleConnectorResponse(
        connectorResponse,
        res,
        'Getting connector schema',
        'Connector schema not found'
      );
    } catch (error: unknown) {
      const { message, status, data } = getConnectorErrorLogFields(error);
      logger.error('Error getting connector schema', {
        error: message,
        connectorType: req.params.connectorType,
        userId: req.user?.userId,
        status,
        data,
      });
      const handledError = handleBackendError(error, 'get connector schema');
      next(handledError);
    }
  };


  /**
 * Get all active agent instances.
 */
export const getActiveAgentInstances =
(appConfig: AppConfig) =>
async (
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
): Promise<void> => {
  try {
    const { userId } = req.user || {};
    const { scope, page, limit, search } = req.query;

    if (!userId) {
      throw new UnauthorizedError('User authentication required');
    }

    logger.info(`Getting connector registry for user ${userId}`);

    const queryParams = new URLSearchParams();
    if (scope) {
      queryParams.append('scope', String(scope));
    }

    if (page) {
      queryParams.append('page', String(page));
    }
    if (limit) {
      queryParams.append('limit', String(limit));
    }
    if (search) {
      queryParams.append('search', String(search));
    }

    const isAdmin = await isUserAdmin(req);
    const headers: Record<string, string> = {
      ...(req.headers as Record<string, string>),
      'X-Is-Admin': isAdmin ? 'true' : 'false',
    };
    const connectorResponse = await executeConnectorCommand(
      `${appConfig.connectorBackend}/api/v1/connectors/agents/active?${queryParams.toString()}`,
      HttpMethod.GET,
      headers,
    );

    handleConnectorResponse(
      connectorResponse,
      res,
      'Getting active agent instances',
      'Failed to get active agent instances',
    );
  } catch (error: unknown) {
    const { message, status, data } = getConnectorErrorLogFields(error);
    logger.error('Error getting active agent instances', {
      error: message,
      userId: req.user?.userId,
      status,
      data,
    });
    const handledError = handleBackendError(
      error,
      'get active agent instances',
    );
    next(handledError);
  }
};
