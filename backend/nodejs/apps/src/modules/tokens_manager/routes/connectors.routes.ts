import { Router, Response, NextFunction } from 'express';
import {
  BadRequestError,
  InternalServerError,
  NotFoundError,
} from '../../../libs/errors/http.errors';

import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import {
  AuthenticatedUserRequest,
  AuthenticatedServiceRequest,
} from '../../../libs/middlewares/types';
import axios from 'axios';
import axiosRetry from 'axios-retry';

import { ConnectorsConfig } from '../../configuration_manager/schema/connectors.schema';
import { Logger } from '../../../libs/services/logger.service';
import { Container } from 'inversify';

import { GoogleWorkspaceApp, scopeToAppMap } from '../types/connector.types';
import { AppConfig, loadAppConfig } from '../config/config';
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
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { verifyGoogleWorkspaceToken } from '../utils/verifyToken';
import {
  AppEnabledEvent,
  EntitiesEventProducer,
  EventType,
  Event,
} from '../services/entity_event.service';
import { userAdminCheck } from '../../user_management/middlewares/userAdminCheck';
import { ConnectorId, ConnectorIdToNameMap } from '../../../libs/types/connector.types';
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';
import { getActiveConnectors, getConnectorConfig, getConnectorConfigAndSchema, getConnectorFilterOptions, getConnectorByName, getConnectors, getConnectorSchema, getInactiveConnectors, getOAuthAuthorizationUrl, handleOAuthCallback, saveConnectorFilterOptions, toggleConnector, updateConnectorConfig } from '../controllers/connector.controllers';
import { z } from 'zod';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';

const logger = Logger.getInstance({
  service: 'Connectors Routes',
});

axiosRetry(axios, {
  retries: 3, // Number of retry attempts
  retryDelay: axiosRetry.exponentialDelay, // Exponential backoff
  retryCondition: (error) => {
    // Make sure to explicitly return a boolean
    return !!(
      axiosRetry.isNetworkOrIdempotentRequestError(error) ||
      (error.response && error.response.status >= 500)
    );
  },
});

export const updateConnectorConfigSchema = z.object({
  body: z.object({
    auth:z.any(),
    sync:z.any(),
    filters:z.any(),
    baseUrl:z.string(),
  }),
  params: z.object({
    connectorName: z.string(),
  }),
});

export const getOAuthAuthorizationUrlSchema = z.object({
  params: z.object({
    connectorName: z.string(),
  }),
  query: z.object({
    baseUrl:z.string(),
  }),
});

export const handleOAuthCallbackSchema = z.object({
  params: z.object({
    connectorName: z.string(),
  }),
  query: z.object({
    baseUrl:z.string(),
    code: z.string().optional(),
    state: z.string().optional(),
    error: z.string().optional(),
  }),
});

export function createConnectorRouter(container: Container) {
  const router = Router();
  const eventService = container.get<EntitiesEventProducer>(
    'EntitiesEventProducer',
  );
  let config = container.get<AppConfig>('AppConfig');
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
   // Old api for streaming records 
   router.get(
    '/',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    userAdminCheck,
    getConnectors(config)
  );

  router.get(
    '/active',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    getActiveConnectors(config)
  );
  
  router.get(
    '/inactive',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    getInactiveConnectors(config)
  );

  router.get(
    '/config/:connectorName',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    userAdminCheck,
    getConnectorConfig(config)
  );

  router.put(
    '/config/:connectorName',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    userAdminCheck,
    ValidationMiddleware.validate(updateConnectorConfigSchema),
    updateConnectorConfig(config)
  )

  router.get(
    '/schema/:connectorName',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    userAdminCheck,
    getConnectorSchema(config)
  );

  router.get(
    '/config-schema/:connectorName',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    userAdminCheck,
    getConnectorConfigAndSchema(config)
  );

  router.post(
    '/toggle/:connectorName',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    userAdminCheck,
    toggleConnector(config)
  );

  // Get single connector by name - must come after specific routes but before OAuth routes
  router.get(
    '/:connectorName',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    userAdminCheck,
    getConnectorByName(config)
  );

  router.get(
    '/:connectorName/oauth/authorize',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    userAdminCheck,
    ValidationMiddleware.validate(getOAuthAuthorizationUrlSchema),
    getOAuthAuthorizationUrl(config)
  );

  router.get(
    '/:connectorName/oauth/callback',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    userAdminCheck,
    ValidationMiddleware.validate(handleOAuthCallbackSchema),
    handleOAuthCallback(config)
  );

  router.get(
    '/filters/:connectorName',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    userAdminCheck,
    getConnectorFilterOptions(config)
  );
  
  router.post(
    '/filters/:connectorName',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    userAdminCheck,
    saveConnectorFilterOptions(config)
  );

  ////// old apis //////


  router.post(
    '/getTokenFromCode',
    authMiddleware.authenticate,
    userAdminCheck,
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        if (!req.user) {
          throw new NotFoundError('User Not Found');
        }
        let response = await getGoogleWorkspaceConfig(
          req,
          config.cmBackend,
          config.scopedJwtSecret,
        );

        if (response.statusCode !== 200) {
          throw new InternalServerError('Error getting config', response?.data);
        }
        const configData = response.data;
        if (!configData.clientId) {
          throw new NotFoundError('Client Id is missing');
        }
        if (!configData.clientSecret) {
          throw new NotFoundError('Client Secret is missing');
        }
        const enableRealTimeUpdates = configData?.enableRealTimeUpdates;
        const topicName = configData?.topicName;
        const appConfig = loadAppConfig();
        const frontendBaseUrl = (await appConfig).frontendUrl;
        const redirectUri = frontendBaseUrl.endsWith('/')
          ? `${frontendBaseUrl}account/individual/settings/connector/googleWorkspace`
          : `${frontendBaseUrl}/account/individual/settings/connector/googleWorkspace`;
        let googleResponse = await axios.post(
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
          throw new BadRequestError('Error getting code');
        }
        const data = googleResponse.data;

        verifyGoogleWorkspaceToken(req, data?.id_token);
        const refreshTokenExpiryDate = data.refresh_token_expires_in
          ? data.refresh_token_expires_in * 1000 + Date.now()
          : undefined;
        response = await setGoogleWorkspaceIndividualCredentials(
          req,
          config.cmBackend,
          config.scopedJwtSecret,
          data.access_token,
          data.refresh_token,
          data.expires_in * 1000 + Date.now(),
          refreshTokenExpiryDate,
          enableRealTimeUpdates,
          topicName,
        );
        if (response.statusCode !== 200) {
          throw new InternalServerError(
            'Error updating access token',
            response?.data,
          );
        }
        const connectorId = ConnectorId.GOOGLE_WORKSPACE;
        if (!connectorId) {
          throw new NotFoundError(
            'Google Workspace connector not found in config',
          );
        }

        let connector = await ConnectorsConfig.findOne({
          name: ConnectorIdToNameMap[connectorId],
          orgId: req.user.orgId,
        });

        // Extract received scopes from the request
        const receivedScopes = data.scope.split(' ');

        // Filter apps based on received scopes
        const enabledApps = Object.keys(scopeToAppMap)
          .filter((scope) => receivedScopes.includes(scope)) // Check if the received scope is in our map
          .map((scope) => scopeToAppMap[scope]);

        await eventService.start();
        let event: Event;

        if (connector) {
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
          res.status(200).json({
            message: `Connector is now enabled`,
            connector,
          });
        } else {
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
            throw new InternalServerError('Error in creating connector');
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
          res.status(201).json({
            message: `Connector ${connectorId} created and enabled`,
            connector,
          });
        }
      } catch (err) {
        next(err);
      }
      // Check if connector exists in MongoDB
    },
  );

  router.post(
    '/internal/refreshIndividualConnectorToken',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    async (
      req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const refreshTokenCommandResponse = await getRefreshTokenCredentials(
          req,
          config.cmBackend,
        );
        if (
          refreshTokenCommandResponse.statusCode !== 200 ||
          !refreshTokenCommandResponse.data.refresh_token
        ) {
          throw new InternalServerError(
            'Error getting refresh token from etcd',
            refreshTokenCommandResponse?.data,
          );
        }

        let response = await getRefreshTokenConfig(req, config.cmBackend);

        if (response.statusCode !== 200) {
          throw new InternalServerError('Error getting config', response?.data);
        }
        const configData = response.data;
        if (!configData.clientId) {
          throw new NotFoundError('Client Id is missing');
        }
        if (!configData.clientSecret) {
          throw new NotFoundError('Client Secret is missing');
        }
        const enableRealTimeUpdates = configData?.enableRealTimeUpdates;
        const topicName = configData?.topicName;
        let retryCount = 0;
        let tokenExchangeSuccessful = false;
        let tokenData;

        while (retryCount < 3 && !tokenExchangeSuccessful) {
          try {
            const { data } = await axios.post(
              GOOGLE_WORKSPACE_TOKEN_EXCHANGE_PATH,
              {
                refresh_token: refreshTokenCommandResponse?.data.refresh_token,
                client_id: configData.clientId,
                client_secret: configData.clientSecret,
                grant_type: 'refresh_token',
              },
            );

            tokenData = data;
            tokenExchangeSuccessful = true;
          } catch (err) {
            retryCount++;
            if (err instanceof Error) {
              logger.error('Error refreshing individual connector token', {
                error: err.message,
                stack: err.stack,
              });
            } else {
              logger.error('Error refreshing individual connector token', {
                unknownError: String(err),
              });
            }

            if (retryCount < 3) {
              // Wait before retrying (exponential backoff)
              const delayMs =
                Math.pow(2, retryCount) * 1000 + Math.random() * 1000;
              await new Promise((resolve) => setTimeout(resolve, delayMs));
            } else {
              // Rethrow the error after all retries failed
              throw err;
            }
          }
        }

        if (!tokenExchangeSuccessful) {
          throw new Error('Failed to exchange token after multiple retries');
        }

        const accessTokenCommandResponse = (response =
          await setRefreshTokenCredentials(
            req,
            config.cmBackend,

            tokenData.access_token,
            refreshTokenCommandResponse?.data.refresh_token,
            tokenData.expires_in * 1000 + Date.now(),
            refreshTokenCommandResponse?.data?.refresh_token_expiry_time ||
              undefined,
            enableRealTimeUpdates,
            topicName,
          ));
        if (accessTokenCommandResponse.statusCode !== 200) {
          throw new InternalServerError(
            'Error updating access token',
            accessTokenCommandResponse?.data,
          );
        }
        res.status(200).json({ message: 'accesstoken updated Successfully' });
      } catch (err) {
        logger.error('Error refreshing individual connector token', err);
        next(err);
      }
    },
  );

  router.post(
    '/updateAppConfig',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    async (
      _req: AuthenticatedServiceRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        config = await loadAppConfig();

        container.rebind<AppConfig>('AppConfig').toDynamicValue(() => config);

        res.status(200).json({
          message: 'Connectors configuration updated successfully',
          config,
        });
        return;
      } catch (error) {
        next(error);
      }
    },
  );
  return router;
}
