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

import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { FileProcessorFactory } from '../../../libs/middlewares/file_processor/fp.factory';
import { FileProcessingType } from '../../../libs/middlewares/file_processor/fp.constant';
import { ConnectorsConfig } from '../../configuration_manager/schema/connectors.schema';
import { Logger } from '../../../libs/services/logger.service';
import { Container } from 'inversify';
import {
  EntitiesEventProducer,
  EventType,
  Event,
  AppDisabledEvent,
  AppEnabledEvent,
} from '../../user_management/services/entity_events.service';
import { GoogleWorkspaceApp, scopeToAppMap } from '../types/connector.types';
import { AppConfig, loadAppConfig } from '../config/config';
import {
  GOOGLE_WORKSPACE_BUSINESS_CREDENTIALS_PATH,
  GOOGLE_WORKSPACE_INDIVIDUAL_CREDENTIALS_PATH,
  GOOGLE_WORKSPACE_TOKEN_EXCHANGE_PATH,
  REFRESH_TOKEN_PATH,
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
} from '../services/connectors-config.service';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { verifyGoogleWorkspaceToken } from '../utils/verifyToken';
import { Org } from '../../user_management/schema/org.schema';
import { googleWorkspaceTypes } from '../../configuration_manager/constants/constants';

const CONNECTORS = [{ key: 'googleWorkspace', name: 'Google Workspace' }];
const logger = Logger.getInstance({
  service: 'Connectors Routes',
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
});

const oAuthValidationSchema = z.object({
  body: oAuthConfigSchema,
  query: z.object({
    service: z.enum(['googleWorkspace']), // Enum validation
  }),
  params: z.object({}),
  headers: z.object({}),
});
const ServiceValidationSchema = z.object({
  body: z.object({}),
  query: z.object({
    service: z.enum(['googleWorkspace']), // Enum validation
  }),
  params: z.object({}),
  headers: z.object({}),
});

export function createConnectorRouter(container: Container) {
  const router = Router();
  const eventService = container.get<EntitiesEventProducer>(
    'EntitiesEventProducer',
  );
  const config = container.get<AppConfig>('AppConfig');
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');

  router.get(
    '/credentials',
    authMiddleware.authenticate,
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
          res.setHeader('Content-Type', 'application/json');
          res.setHeader(
            'Content-Disposition',
            'attachment; filename="credentials.json"',
          );

          // Send JSON response as a downloadable file
          res.status(200).send(JSON.stringify(response.data, null, 2));
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

        const statuses = CONNECTORS.map(({ key, name }) => {
          const connector = connectors.find((c) => c.name === name);
          return { key, isEnabled: connector ? connector.isEnabled : false };
        });
        logger.info('statuses', statuses);

        res.status(200).json(statuses);
      } catch (error) {
        next(error);
      }
    },
  );

  router.get(
    '/config',
    authMiddleware.authenticate,
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
        switch (userType.toLowerCase()) {
          case googleWorkspaceTypes.INDIVIDUAL.toLowerCase():
            response = await getGoogleWorkspaceConfig(
              req,
              config.cmBackend,
              config.scopedJwtSecret,
            );
            if (response.statusCode !== 200) {
              throw new InternalServerError(
                'Error getting config',
                response?.data,
              );
            }
            const configData = response.data;
            if (!configData.clientId) {
              throw new NotFoundError('Client Id is missing');
            }
            if (!configData.clientSecret) {
              throw new NotFoundError('Client Secret is missing');
            }

            res.status(200).json({
              googleClientId: configData.clientId,
              googleClientSecret: configData.clientSecret,
            });

            break;

          case googleWorkspaceTypes.BUSINESS.toLowerCase():
            response = await getGoogleWorkspaceBusinessCredentials(
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
                  isConfigured: true,
                });
              } else {
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
      } catch (error) {
        next(error);
      }
    },
  );

  router.post(
    '/config',
    authMiddleware.authenticate,
    ValidationMiddleware.validate(oAuthValidationSchema),
    async (
      req: AuthenticatedUserRequest,
      res: Response,
      next: NextFunction,
    ) => {
      try {
        let response = await setGoogleWorkspaceConfig(
          req,
          config.cmBackend,
          config.scopedJwtSecret,
        );

        if (response.statusCode !== 200) {
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
        const connectorData = CONNECTORS.find((c) => c.key === service);
        if (!connectorData) {
          throw new NotFoundError('Invalid service name');
        }

        let connector = await ConnectorsConfig.findOne({
          name: connectorData.name,
        });
        await eventService.start();
        if (connector) {
          connector.isEnabled = false;
          connector.lastUpdatedBy = req.user.userId;

          const event: Event = {
            eventType: EventType.AppDisabledEvent,
            timestamp: Date.now(),
            payload: {
              orgId: req.user.orgId,
              appGroup: connector.name,
              appGroupId: connector._id,
              apps: [
                GoogleWorkspaceApp.Drive,
                GoogleWorkspaceApp.Gmail,
                GoogleWorkspaceApp.Calendar,
              ],
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
        const connectorData = CONNECTORS.find((c) => c.key === service);
        if (!connectorData) {
          throw new NotFoundError('Invalid service name');
        }

        let connector = await ConnectorsConfig.findOne({
          name: connectorData.name,
        });
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
              credentialsRoute: `${config.cmBackend}/${GOOGLE_WORKSPACE_BUSINESS_CREDENTIALS_PATH}`,
              apps: [
                GoogleWorkspaceApp.Drive,
                GoogleWorkspaceApp.Gmail,
                GoogleWorkspaceApp.Calendar,
              ],
              syncAction: 'immediate',
            } as AppEnabledEvent,
          };
          await eventService.publishEvent(event);
          await connector.save();
          await eventService.stop();
          res.status(200).json({
            message: `Connector ${service} is now enabled`,
            connector,
          });
        } else {
          connector = new ConnectorsConfig({
            orgId: req.user.orgId,
            name: connectorData.name,
            lastUpdatedBy: req.user.userId,
            isEnabled: true,
          });

          await connector.save();
          connector = await ConnectorsConfig.findOne({
            name: connectorData.name,
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
              credentialsRoute: `${config.cmBackend}/${GOOGLE_WORKSPACE_BUSINESS_CREDENTIALS_PATH}`,
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
            message: `Connector ${connectorData.name} created and enabled`,
            connector,
          });
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
    '/getTokenFromCode',
    authMiddleware.authenticate,
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
        );
        if (response.statusCode !== 200) {
          throw new InternalServerError(
            'Error updating access token',
            response?.data,
          );
        }
        const connectorData = CONNECTORS.find(
          (c) => c.key === 'googleWorkspace',
        );
        if (!connectorData) {
          throw new NotFoundError(
            'Google Workspace connector not found in config',
          );
        }

        let connector = await ConnectorsConfig.findOne({
          name: connectorData.name,
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
            name: connectorData.name,
            lastUpdatedBy: req.user.userId,
            isEnabled: true,
          });

          await connector.save();
          connector = await ConnectorsConfig.findOne({
            name: connectorData.name,
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
            message: `Connector ${connectorData.name} created and enabled`,
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
      const refreshTokenCommandResponse = await getRefreshTokenCredentials(
        req,
        config.cmBackend,
      );
      if (refreshTokenCommandResponse.statusCode !== 200) {
        throw new InternalServerError(
          'Error getting refresh token from etcd',
          refreshTokenCommandResponse?.data,
        );
      }
      try {
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

        const { data } = await axios.post(
          GOOGLE_WORKSPACE_TOKEN_EXCHANGE_PATH,
          {
            refresh_token: refreshTokenCommandResponse?.data.refresh_token,
            client_id: configData.clientId,
            client_secret: configData.clientSecret,
            grant_type: 'refresh_token',
          },
        );

        const accessTokenCommandResponse = (response =
          await setRefreshTokenCredentials(
            req,
            config.cmBackend,
            data.access_token,
            refreshTokenCommandResponse?.data.refresh_token,
            data.expires_in * 1000 + Date.now(),
            refreshTokenCommandResponse?.data?.refresh_token_expiry_time ||
              undefined,
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
  return router;
}
