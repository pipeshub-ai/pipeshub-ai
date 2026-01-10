/**
 * OAuth Config Controllers
 *
 * Controllers for managing OAuth app configurations.
 * These controllers act as a proxy layer between the frontend and the Python backend,
 * handling authentication, validation, and error transformation.
 *
 * @module oauth/controllers
 */

import { NextFunction, Response } from 'express';
import { AuthenticatedUserRequest } from '../../../libs/middlewares/types';
import { Logger } from '../../../libs/services/logger.service';
import {
  BadRequestError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { AppConfig } from '../config/config';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { executeConnectorCommand, handleBackendError, handleConnectorResponse } from '../utils/connector.utils';
import { isUserAdmin } from './connector.controllers';

const logger = Logger.getInstance({
  service: 'OAuth Controller',
});

// ============================================================================
// Helper Functions
// ============================================================================

/**
 * Higher-order function to create OAuth API handlers.
 * Reduces code duplication by centralizing common validation, header preparation,
 * API call execution, and error handling logic.
 *
 * @param appConfig - Application configuration
 * @param buildUrl - Function to build the API URL
 * @param validateParams - Optional function to validate request parameters
 * @param buildPayload - Optional function to build request payload
 * @param operationName - Human-readable operation name for logging
 * @param method - HTTP method (GET, POST, PUT, DELETE)
 * @returns Express route handler function
 */
const createOAuthApiHandler = (
  appConfig: AppConfig,
  buildUrl: (req: AuthenticatedUserRequest) => string,
  validateParams: (req: AuthenticatedUserRequest) => void,
  buildPayload: (req: AuthenticatedUserRequest) => any | null,
  operationName: string,
  method: HttpMethod,
  notFoundMessage: string,
) => {
  return async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const { userId } = req.user || {};

      if (!userId) {
        throw new UnauthorizedError('User authentication required');
      }

      // Validate parameters
      validateParams(req);

      // Build URL
      const url = buildUrl(req);

      logger.info(`${operationName} for user ${userId}`);

      // Prepare headers with admin flag
      const isAdmin = await isUserAdmin(req);
      const headers: Record<string, string> = {
        ...(req.headers as Record<string, string>),
        'X-Is-Admin': isAdmin ? 'true' : 'false',
      };

      // Build payload (if applicable)
      const payload = buildPayload(req);

      // Execute API call
      const connectorResponse = await executeConnectorCommand(
        `${appConfig.connectorBackend}${url}`,
        method,
        headers,
        payload || undefined,
      );

      // Handle response
      handleConnectorResponse(
        connectorResponse,
        res,
        operationName,
        notFoundMessage,
      );
    } catch (error: any) {
      logger.error(`Error ${operationName.toLowerCase()}`, {
        error: error.message,
        userId: req.user?.userId,
        params: req.params,
        status: error.response?.status,
        data: error.response?.data,
      });
      const handledError = handleBackendError(error, operationName.toLowerCase());
      next(handledError);
    }
  };
};

/**
 * Build query string from request query parameters.
 */
const buildQueryString = (req: AuthenticatedUserRequest): string => {
  const { page, limit, search } = req.query;
  const queryParams = new URLSearchParams();
  
  if (page) queryParams.append('page', String(page));
  if (limit) queryParams.append('limit', String(limit));
  if (search) queryParams.append('search', String(search));
  
  const queryString = queryParams.toString();
  return queryString ? `?${queryString}` : '';
};

// ============================================================================
// OAuth Config Controllers
// ============================================================================

/**
 * Get OAuth config registry (available connector/tool types with OAuth support).
 */
export const getOAuthConfigRegistry = (appConfig: AppConfig) =>
  createOAuthApiHandler(
    appConfig,
    (req) => `/api/v1/oauth/registry${buildQueryString(req)}`,
    () => {}, // No validation needed
    () => null, // No payload
    'Getting OAuth config registry',
    HttpMethod.GET,
    'OAuth config registry not found',
  );

/**
 * Get OAuth config registry information for a specific connector type.
 */
export const getOAuthConfigRegistryByType = (appConfig: AppConfig) =>
  createOAuthApiHandler(
    appConfig,
    (req) => `/api/v1/oauth/registry/${req.params.connectorType}`,
    (req) => {
      if (!req.params.connectorType) {
        throw new BadRequestError('Connector type is required');
      }
    },
    () => null,
    'Getting OAuth config registry by type',
    HttpMethod.GET,
    'OAuth config registry not found',
  );

/**
 * Get all OAuth configs across all connector types.
 * This aggregates OAuth configs from all connector types into a single list.
 */
export const getAllOAuthConfigs = (appConfig: AppConfig) =>
  createOAuthApiHandler(
    appConfig,
    (req) => `/api/v1/oauth${buildQueryString(req)}`,
    () => {},
    () => null,
    'Getting all OAuth configs',
    HttpMethod.GET,
    'OAuth configs not found',
  );

/**
 * List OAuth configs for a connector type.
 */
export const listOAuthConfigs = (appConfig: AppConfig) =>
  createOAuthApiHandler(
    appConfig,
    (req) => `/api/v1/oauth/${req.params.connectorType}${buildQueryString(req)}`,
    (req) => {
      if (!req.params.connectorType) {
        throw new BadRequestError('Connector type is required');
      }
    },
    () => null,
    'Listing OAuth configs',
    HttpMethod.GET,
    'OAuth configs not found',
  );

/**
 * Create a new OAuth config.
 */
export const createOAuthConfig = (appConfig: AppConfig) =>
  createOAuthApiHandler(
    appConfig,
    (req) => `/api/v1/oauth/${req.params.connectorType}`,
    (req) => {
      const { connectorType } = req.params;
      const { oauthInstanceName, config } = req.body;

      if (!connectorType) {
        throw new BadRequestError('Connector type is required');
      }
      if (!oauthInstanceName) {
        throw new BadRequestError('OAuth instance name is required');
      }
      if (!config) {
        throw new BadRequestError('Config is required');
      }
    },
    (req) => {
      const { oauthInstanceName, config } = req.body;
      return { oauthInstanceName, config };
    },
    'Creating OAuth config',
    HttpMethod.POST,
    'Failed to create OAuth config',
  );

/**
 * Get a specific OAuth config.
 */
export const getOAuthConfig = (appConfig: AppConfig) =>
  createOAuthApiHandler(
    appConfig,
    (req) => `/api/v1/oauth/${req.params.connectorType}/${req.params.configId}`,
    (req) => {
      if (!req.params.connectorType) {
        throw new BadRequestError('Connector type is required');
      }
      if (!req.params.configId) {
        throw new BadRequestError('Config ID is required');
      }
    },
    () => null,
    'Getting OAuth config',
    HttpMethod.GET,
    'OAuth config not found',
  );

/**
 * Update an OAuth config.
 */
export const updateOAuthConfig = (appConfig: AppConfig) =>
  createOAuthApiHandler(
    appConfig,
    (req) => `/api/v1/oauth/${req.params.connectorType}/${req.params.configId}`,
    (req) => {
      const { connectorType, configId } = req.params;
      const { oauthInstanceName, config } = req.body;

      if (!connectorType) {
        throw new BadRequestError('Connector type is required');
      }
      if (!configId) {
        throw new BadRequestError('Config ID is required');
      }
      if (!oauthInstanceName && !config) {
        throw new BadRequestError('Either oauthInstanceName or config must be provided');
      }
    },
    (req) => {
      const { oauthInstanceName, config } = req.body;
      const payload: any = {};
      if (oauthInstanceName) {
        payload.oauthInstanceName = oauthInstanceName;
      }
      if (config) {
        payload.config = config;
      }
      return payload;
    },
    'Updating OAuth config',
    HttpMethod.PUT,
    'OAuth config not found',
  );

/**
 * Delete an OAuth config.
 */
export const deleteOAuthConfig = (appConfig: AppConfig) =>
  createOAuthApiHandler(
    appConfig,
    (req) => `/api/v1/oauth/${req.params.connectorType}/${req.params.configId}`,
    (req) => {
      if (!req.params.connectorType) {
        throw new BadRequestError('Connector type is required');
      }
      if (!req.params.configId) {
        throw new BadRequestError('Config ID is required');
      }
    },
    () => null,
    'Deleting OAuth config',
    HttpMethod.DELETE,
    'OAuth config not found',
  );

