import { Router, Response, NextFunction } from 'express';
import { z } from 'zod';
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

import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { FileProcessorFactory } from '../../../libs/middlewares/file_processor/fp.factory';
import { FileProcessingType } from '../../../libs/middlewares/file_processor/fp.constant';
import { ConnectorsConfig } from '../../configuration_manager/schema/connectors.schema';
import { Logger } from '../../../libs/services/logger.service';
import { Container } from 'inversify';

import { GoogleWorkspaceApp, scopeToAppMap } from '../types/connector.types';
import { AppConfig, loadAppConfig } from '../config/config';
import {
  GOOGLE_WORKSPACE_BUSINESS_CREDENTIALS_PATH,
  GOOGLE_WORKSPACE_INDIVIDUAL_CREDENTIALS_PATH,
  GOOGLE_WORKSPACE_TOKEN_EXCHANGE_PATH,
  ONE_DRIVE_INTERNAL_CONFIG_PATH,
  ATLASIAN_CONFIG_PATH,
  REFRESH_TOKEN_PATH,
  SHAREPOINT_INTERNAL_CONFIG_PATH,
} from '../consts/constants';
import {
  deleteGoogleWorkspaceCredentials,
  getGoogleWorkspaceConfig,
  getGoogleWorkspaceBusinessCredentials,
  setGoogleWorkspaceConfig,
  setGoogleWorkspaceBusinessCredentials,
  setGoogleWorkspaceIndividualCredentials,
  getRefreshTokenCredentials,
  getRefreshTokenConfig,
  setRefreshTokenCredentials,
  getAtlassianOauthConfig,
  setAtlassianOauthConfig,
  getSharePointConfig,
  getOneDriveConfig,
  setOneDriveConfig,
  setSharePointConfig,
} from '../services/connectors-config.service';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { verifyGoogleWorkspaceToken } from '../utils/verifyToken';
import { Org } from '../../user_management/schema/org.schema';
import { googleWorkspaceTypes } from '../../configuration_manager/constants/constants';
import {
  AppDisabledEvent,
  AppEnabledEvent,
  EntitiesEventProducer,
  EventType,
  Event,
} from '../services/entity_event.service';
import { userAdminCheck } from '../../user_management/middlewares/userAdminCheck';
import { ConnectorId, ConnectorIdToNameMap } from '../../../libs/types/connector.types';

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

const oAuthConfigSchema = z.object({
  // Direct fields (when provided directly in the request body)
  clientId: z
    .string()
    .min(1, 'Client ID cannot be empty')
    .max(255, 'Client ID exceeds maximum length of 255 characters'),
  clientSecret: z
    .string()
    .min(1, 'Client Secret cannot be empty')
    .max(255, 'Client Secret exceeds maximum length of 255 characters'),
  tenantId: z.string().optional(),
  enableRealTimeUpdates: z.union([z.boolean(), z.string()]).optional(),
  topicName: z.string().optional(),
  hasAdminConsent: z.boolean().optional(),
});

const oAuthValidationSchema = z.object({
  body: oAuthConfigSchema,
  query: z.object({
    service: z.enum(['googleWorkspace', 'atlassian', 'onedrive', 'sharepoint']), // Enum validation
  }),
  params: z.object({}),
  headers: z.object({}),
});

const ServiceValidationSchema = z.object({
  body: z.object({}),
  query: z.object({
    service: z.enum(['googleWorkspace', 'atlassian', 'onedrive', 'sharepoint']), // Enum validation
  }),
  params: z.object({}),
  headers: z.object({}),
});

export function createConnectorRouter(container: Container) {
  const router = Router();
  const eventService = container.get<EntitiesEventProducer>(
    'EntitiesEventProducer',
  );
  let config = container.get<AppConfig>('AppConfig');
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');

  router.get(
    '/credentials',
    authMiddleware.authenticate,
    userAdminCheck,
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const response = await getGoogleWorkspaceBusinessCredentials(
          req,
          config.cmBackend,
          config.scopedJwtSecret,
        );
        if (response.statusCode !== 200) {
          throw new InternalServerError(
            'Error getting credentials',
            response?.data,
          );
        } else {
          if (response.data.client_id) {
            res.status(200).json({
              adminEmail: response?.data?.adminEmail,
              enableRealTimeUpdates: response?.data?.enableRealTimeUpdates,
              topicName: response?.data?.topicName,
              isConfigured: true,
            });
          } else {
            res.status(200).json({ isConfigured: false });
          }
        }
      } catch (err) {
        next(err);
      }
    },
  );
  router.post(
    '/credentials',
    authMiddleware.authenticate,
    userAdminCheck,
    ...FileProcessorFactory.createJSONUploadProcessor({
      fieldName: 'googleWorkspaceCredentials',
      allowedMimeTypes: ['application/json'],
      maxFilesAllowed: 1,
      isMultipleFilesAllowed: false,
      processingType: FileProcessingType.JSON,
      maxFileSize: 1024 * 1024 * 5,
      strictFileUpload: false,
    }).getMiddleware,
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const response = await setGoogleWorkspaceBusinessCredentials(
          req,
          config.cmBackend,
          config.scopedJwtSecret,
        );
        if (response.statusCode !== 200) {
          throw new BadRequestError(response?.data?.error?.message);
        } else {
          res.status(200).json({ isConfigured: true });
        }
      } catch (err) {
        next(err);
      }
    },
  );

  router.delete(
    '/credentials',
    authMiddleware.authenticate,
    userAdminCheck,
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const response = await deleteGoogleWorkspaceCredentials(
          req,
          config.cmBackend,
          config.scopedJwtSecret,
        );
        if (response.statusCode !== 200) {
          throw new InternalServerError(
            'Error updating access token',
            response?.data,
          );
        } else {
          res.status(200).json({ message: 'Credentials uccessfully deleted' });
        }
      } catch (err) {
        next(err);
      }
    },
  );

  router.get(
    '/credentials/download',
    authMiddleware.authenticate,
    userAdminCheck,
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        const response = await getGoogleWorkspaceBusinessCredentials(
          req,
          config.cmBackend,
          config.scopedJwtSecret,
        );
        if (response.statusCode !== 200) {
          throw new InternalServerError(
            'Error updating access token',
            response?.data,
          );
        } else {
          if (!response.data.client_id) {
            throw new NotFoundError('No file found for credentials');
          }
          const filteredData = { ...response.data };

          // Remove unwanted fields
          delete filteredData.adminEmail;
          delete filteredData.enableRealTimeUpdates;
          delete filteredData.topicName;

          res.setHeader('Content-Type', 'application/json');
          res.setHeader(
            'Content-Disposition',
            'attachment; filename="credentials.json"',
          );

          // Send JSON response as a downloadable file
          res.status(200).send(JSON.stringify(filteredData, null, 2));
        }
      } catch (err) {
        next(err);
      }
    },
  );
  router.get(
    '/status',
    authMiddleware.authenticate,
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        if (!req.user) {
          throw new NotFoundError('User not Found');
        }
        const orgId = req.user.orgId;
        const connectors = await ConnectorsConfig.find({ orgId }).select(
          'name isEnabled',
        );

        const statuses = connectors.map((connector) => {
          // Get ConnectorId from connector name using reverse mapping
          const connectorId = Object.entries(ConnectorIdToNameMap).find(
            ([_, name]) => name === connector.name
          )?.[0] as ConnectorId;
          
          return { id: connectorId, name: connector.name, isEnabled: connector.isEnabled };
        });

        res.status(200).json(statuses);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/config',
    authMiddleware.authenticate,
    userAdminCheck,
    ValidationMiddleware.validate(ServiceValidationSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        if (!req.user) {
          throw new NotFoundError('User not found');
        }
        const orgId = req.user.orgId;
        const org = await Org.findOne({ orgId, isDeleted: false });
        if (!org) {
          throw new BadRequestError('Organisaton not found');
        }
        const userType = org.accountType;

        let response;
        const { service } = req.query;
        const serviceStr = service as string;
        
        if (serviceStr === ConnectorId.ATLASSIAN.toLowerCase()) {
          response = await getAtlassianOauthConfig(
            req,
            config.cmBackend,
          );
          if (response.statusCode !== 200) {
            throw new InternalServerError('Error getting config', response?.data);
          }
          res.status(200).json(response.data);
          return;
        } 
        else if (serviceStr.toLowerCase() == ConnectorId.ONEDRIVE.toLowerCase()) {
          response = await getOneDriveConfig(req, config.cmBackend);
          if (response.statusCode !== 200) {
            throw new InternalServerError('Error getting config', response?.data);
          }
          res.status(200).json(response.data);
          return;
        }
        else if (serviceStr.toLowerCase() == ConnectorId.SHAREPOINT.toLowerCase()) {
          response = await getSharePointConfig(req, config.cmBackend);
          if (response.statusCode !== 200) {
            throw new InternalServerError('Error getting config', response?.data);
          }
          res.status(200).json(response.data);
          return;
        }
        else if (serviceStr.toLowerCase() == ConnectorId.GOOGLE_WORKSPACE.toLowerCase()) {
          switch (userType.toLowerCase()) {
            case googleWorkspaceTypes.INDIVIDUAL.toLowerCase():
              response = await getGoogleWorkspaceConfig(
                req,
                config.cmBackend,
                config.scopedJwtSecret,
              );
              if (response.statusCode !== 200) {
                if (
                  response.data &&
                  typeof response.data === 'object' &&
                  Object.keys(response.data).length === 0
                ) {
                  res.status(204).end();
                  return;
                }
                throw new InternalServerError(
                  'Error getting config',
                  response?.data,
                );
              }
              const configData = response.data;
              if (
                response.data &&
                typeof response.data === 'object' &&
                Object.keys(response.data).length === 0
              ) {
                res.status(204).end();
                return;
              }
              if (!configData.clientId) {
                throw new NotFoundError('Client Id is missing');
              }
              if (!configData.clientSecret) {
                throw new NotFoundError('Client Secret is missing');
              }

              res.status(200).json({
                googleClientId: configData.clientId,
                googleClientSecret: configData.clientSecret,
                enableRealTimeUpdates: configData?.enableRealTimeUpdates,
                topicName: configData?.topicName,
              });

              break;

            case googleWorkspaceTypes.BUSINESS.toLowerCase():
              response = await getGoogleWorkspaceBusinessCredentials(
                req,
                config.cmBackend,
                config.scopedJwtSecret,
              );
              logger.error('Config response', response);
              if (response.statusCode !== 200) {
                throw new InternalServerError(
                  'Error getting credentials',
                  response?.data,
                );
              } else {
                if (response.data.client_id) {
                  res.status(200).json({
                    adminEmail: response?.data?.adminEmail,
                    isConfigured: true,
                  });
                } else {
                  if (
                    response.data &&
                    typeof response.data === 'object' &&
                    Object.keys(response.data).length === 0
                  ) {
                    res.status(204).end();
                    return;
                  }
                  throw new InternalServerError(
                    'Error getting config',
                    response?.data,
                  );
                }
              }
              break;

            default:
              throw new BadRequestError(
                `Unsupported google workspace type: ${userType}`,
              );
          }
        }
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/config',
    authMiddleware.authenticate,
    userAdminCheck,
    ValidationMiddleware.validate(oAuthValidationSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        let service = req.query.service;
        let response;
        console.log('req.body', req.body);
        if (service === ConnectorId.ATLASSIAN.toLowerCase()) {
          response = await setAtlassianOauthConfig(
            req,
            config.cmBackend,
          );
        } else if (service === ConnectorId.GOOGLE_WORKSPACE.toLowerCase()) { 
          response = await setGoogleWorkspaceConfig(
            req,
            config.cmBackend,
            config.scopedJwtSecret,
          );
        }
        else if (service === ConnectorId.ONEDRIVE.toLowerCase()) {
          response = await setOneDriveConfig(
            req,
            config.cmBackend,
          );
        }
        else if (service === ConnectorId.SHAREPOINT.toLowerCase()) {
          response = await setSharePointConfig(
            req,
            config.cmBackend,
          );
        }
        else {
          throw new BadRequestError('Invalid service name');
        }
        if (response?.statusCode !== 200) {
          throw new InternalServerError('Error setting config', response?.data);
        }
        res.status(200).json({
          message: 'config successfully updated',
        });
      } catch (error) {
        next(error);
      }
    },
  );
  router.post(
    '/disable',
    authMiddleware.authenticate,
    userAdminCheck,
    ValidationMiddleware.validate(ServiceValidationSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        if (!req.user) {
          throw new NotFoundError('User not found');
        }
        const { service } = req.query;
        const connectorId = service as ConnectorId;
        if (!connectorId) {
          throw new NotFoundError('Invalid service name');
        }

        let connector = await ConnectorsConfig.findOne({
          name: ConnectorIdToNameMap[connectorId],
          orgId: req.user.orgId,
        });
        await eventService.start();
        if (connector) {
          connector.isEnabled = false;
          connector.lastUpdatedBy = req.user.userId;
          let apps: string[] = [];
          const connectorName = ConnectorIdToNameMap[connectorId];

          if (connectorName === ConnectorId.GOOGLE_WORKSPACE) {
            apps = [
              GoogleWorkspaceApp.Drive.toLowerCase(),
              GoogleWorkspaceApp.Gmail.toLowerCase(),
              GoogleWorkspaceApp.Calendar.toLowerCase(),
            ];
          }
          const event: Event = {
            eventType: EventType.AppDisabledEvent,
            timestamp: Date.now(),
            payload: {
              orgId: req.user.orgId,
              appGroup: connector.name,
              appGroupId: connector._id,
              apps: apps,
            } as AppDisabledEvent,
          };

          await eventService.publishEvent(event);

          await eventService.stop();
          await connector.save();

          res.status(200).json({
            message: `Connector ${service} is now disabled`,
            connector,
          });
        } else {
          throw new NotFoundError('Connector not found');
        }
      } catch (err) {
        try {
          await eventService.stop();
        } catch (error) {
          next(error);
        }
        next(err);
      }
    },
  );
  router.post(
    '/enable',
    authMiddleware.authenticate,
    userAdminCheck,
    ValidationMiddleware.validate(ServiceValidationSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        if (!req.user) {
          throw new NotFoundError('User not found');
        }
        const { service } = req.query;
        const connectorId = service as ConnectorId;
        if (!connectorId) {
          throw new NotFoundError('Invalid service name');
        }

        let connector = await ConnectorsConfig.findOne({
          name: connectorId,
          orgId: req.user.orgId,
        });
        await eventService.start();
        let event: Event;
        let status = 200;
        let message = `Connector ${service} is now enabled`;
        if (connector) {
          connector.isEnabled = true;
          connector.lastUpdatedBy = req.user.userId;
          await connector.save();

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

          status = 201;
          message = `Connector ${connectorId} created and enabled`;
        }

        let apps: string[] = [];
        let credentialsRoute = "";
        if (connector.name === ConnectorIdToNameMap[ConnectorId.GOOGLE_WORKSPACE]) {
          apps = [GoogleWorkspaceApp.Drive, GoogleWorkspaceApp.Gmail, GoogleWorkspaceApp.Calendar];
          credentialsRoute = `${config.cmBackend}/${GOOGLE_WORKSPACE_BUSINESS_CREDENTIALS_PATH}`;
        }
        else if (connector.name === ConnectorIdToNameMap[ConnectorId.ONEDRIVE]) {
          apps = ["onedrive"];
          credentialsRoute = `${config.cmBackend}/${ONE_DRIVE_INTERNAL_CONFIG_PATH}`;
        }
        else if (connector.name === ConnectorIdToNameMap[ConnectorId.SHAREPOINT]) {
          apps = ["sharepoint"];
          credentialsRoute = `${config.cmBackend}/${SHAREPOINT_INTERNAL_CONFIG_PATH}`;
        }
        else if (connector.name === ConnectorIdToNameMap[ConnectorId.ATLASSIAN]) {
          apps = ["jira", "confluence"];
          credentialsRoute = `${config.cmBackend}/${ATLASIAN_CONFIG_PATH}`;
        }
        event = {
          eventType: EventType.AppEnabledEvent,
          timestamp: Date.now(),
          
          payload: {
            orgId: req.user.orgId,
            appGroup: connector.name,
            appGroupId: connector._id,
            credentialsRoute: credentialsRoute,
            apps: apps,
            syncAction: 'immediate',
          } as AppEnabledEvent,
        };
        await eventService.publishEvent(event);
        await eventService.stop();
        res.status(status).json({
          message: message,
          connector,
        });
      } catch (err) {
        try {
          await eventService.stop();
        } catch (error) {
          next(error);
        }
        next(err);
      }
    },
  );
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
