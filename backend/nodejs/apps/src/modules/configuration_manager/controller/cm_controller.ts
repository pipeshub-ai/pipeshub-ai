import { v4 as uuidv4 } from 'uuid';
import { Response, NextFunction } from 'express';
import {
  AuthenticatedServiceRequest,
  AuthenticatedUserRequest,
} from '../../../libs/middlewares/types';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { Logger } from '../../../libs/services/logger.service';
import { configPaths } from '../paths/paths';
import {
  BadRequestError,
  ForbiddenError,
  InternalServerError,
  NotFoundError,
  ServiceUnavailableError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import {
  AIServiceResponse,
  storageTypes,
} from '../constants/constants';
import { EncryptionService } from '../../../libs/encryptor/encryptor';
import { loadConfigurationManagerConfig } from '../config/config';

import { DefaultStorageConfig } from '../../tokens_manager/services/cm.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { generateFetchConfigAuthToken } from '../../auth/utils/generateAuthToken';
import { SamlController } from '../../auth/controller/saml.controller';
import axios from 'axios';
import { ARANGO_DB_NAME, MONGO_DB_NAME } from '../../../libs/enums/db.enum';
import {
  AICommandOptions,
  AIServiceCommand,
} from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { PLATFORM_FEATURE_FLAGS } from '../constants/constants';
import { getPlatformSettingsFromStore } from '../utils/util';
import { AIModelsConfig } from '../types/ai-models.types';
import {
  aiModelMutationResponseSchema,
  aiModelsAvailableByTypeResponseSchema,
  aiModelsByTypeResponseSchema,
  aiModelsConfigResponseSchema,
  aiModelsProvidersResponseSchema,
  arangoDbConfigResponseSchema,
  availablePlatformFlagsResponseSchema,
  azureAdAuthConfigResponseSchema,
  cmMessageResponseSchema,
  customSystemPromptResponseSchema,
  customSystemPromptUpdateResponseSchema,
  frontendPublicUrlResponseSchema,
  googleAuthConfigResponseSchema,
  kafkaConfigResponseSchema,
  metricsCollectionResponseSchema,
  microsoftAuthConfigResponseSchema,
  mongoDbConfigResponseSchema,
  oauthAuthConfigResponseSchema,
  platformSettingsResponseSchema,
  qdrantConfigResponseSchema,
  redisConfigResponseSchema,
  slackBotConfigDeleteResponseSchema,
  slackBotConfigMutationResponseSchema,
  slackBotConfigsResponseSchema,
  smtpConfigResponseSchema,
  ssoAuthConfigResponseSchema,
  storageConfigResponseSchema,
} from '../validator/validators';
import { sendValidatedJson } from '../../../utils/response-validator';

const logger = Logger.getInstance({
  service: 'ConfigurationManagerController',
});

// =============================================================================
// Types
// =============================================================================

type SlackBotConfigEntry = {
  id: string;
  name: string;
  botToken: string;
  signingSecret: string;
  agentId?: string;
  createdAt: string;
  updatedAt: string;
};

type SlackBotStore = {
  configs: SlackBotConfigEntry[];
};

// =============================================================================
// Constants
// =============================================================================

const AI_SERVICE_UNAVAILABLE_MESSAGE =
  'AI Service is currently unavailable. Please check your network connection or try again later.';

const AI_MODEL_TYPE_VALUES = [
  'llm',
  'embedding',
  'ocr',
  'slm',
  'reasoning',
  'multiModal',
] as const;
type AIModelTypeValue = (typeof AI_MODEL_TYPE_VALUES)[number];

// =============================================================================
// Low-level helpers
// =============================================================================

const handleBackendError = (error: any, operation: string): Error => {
  if (
    (error?.cause && error.cause.code === 'ECONNREFUSED') ||
    (typeof error?.message === 'string' && error.message.includes('fetch failed'))
  ) {
    return new ServiceUnavailableError(AI_SERVICE_UNAVAILABLE_MESSAGE, error);
  }

  if (error.response) {
    const { status, data } = error.response;
    const errorDetail = data?.detail || data?.reason || data?.message || 'Unknown error';

    logger.error(`Backend error during ${operation}`, {
      status,
      errorDetail,
      fullResponse: data,
    });

    if (errorDetail === 'ECONNREFUSED') {
      throw new ServiceUnavailableError(AI_SERVICE_UNAVAILABLE_MESSAGE, error);
    }

    switch (status) {
      case 400: return new BadRequestError(errorDetail);
      case 401: return new UnauthorizedError(errorDetail);
      case 403: return new ForbiddenError(errorDetail);
      case 404: return new NotFoundError(errorDetail);
      case 500: return new InternalServerError(errorDetail);
      default:  return new InternalServerError(`Backend error: ${errorDetail}`);
    }
  }

  if (error.request) {
    logger.error(`No response from backend during ${operation}`);
    return new InternalServerError('Backend service unavailable');
  }

  return new InternalServerError(`${operation} failed: ${error.message}`);
};

const createEmptyAIModelsState = () => ({
  ocr: [],
  embedding: [],
  slm: [],
  llm: [],
  reasoning: [],
  multiModal: [],
});

const ensureAIModelsShape = (models: Record<string, any>): Record<string, any> => {
  for (const type of AI_MODEL_TYPE_VALUES) {
    if (!Array.isArray(models[type])) {
      models[type] = [];
    }
  }
  return models;
};

// =============================================================================
// Encryption helpers
// =============================================================================

/** Serialize `data` to JSON and encrypt it. */
const encryptConfig = (data: unknown): string => {
  const { algorithm, secretKey } = loadConfigurationManagerConfig();
  return EncryptionService.getInstance(algorithm, secretKey).encrypt(JSON.stringify(data));
};

/** Decrypt an encrypted string and deserialize it from JSON. */
const decryptConfig = <T = unknown>(encrypted: string): T => {
  const { algorithm, secretKey } = loadConfigurationManagerConfig();
  return JSON.parse(
    EncryptionService.getInstance(algorithm, secretKey).decrypt(encrypted),
  ) as T;
};

// =============================================================================
// AI Models store helpers
// =============================================================================

const loadAIModels = async (
  keyValueStoreService: KeyValueStoreService,
): Promise<Record<string, any> | null> => {
  const encrypted = await keyValueStoreService.get<string>(configPaths.aiModels);
  return encrypted ? decryptConfig<Record<string, any>>(encrypted) : null;
};

const saveAIModels = async (
  keyValueStoreService: KeyValueStoreService,
  aiModels: Record<string, any>,
): Promise<void> => {
  await keyValueStoreService.set<string>(configPaths.aiModels, encryptConfig(aiModels));
};

// =============================================================================
// AI health-check response helper
// =============================================================================

/** Proxies an AI-service health-check failure response back to the client. */
const replyAIHealthCheckFailure = (
  res: Response,
  aiResponseData: AIServiceResponse | undefined,
  modelType: string,
): void => {
  const errData: any = aiResponseData?.data ?? {};
  const reasonMessage =
    errData?.message ??
    errData?.error?.message ??
    `Failed to do health check of ${modelType} configuration, check credentials again`;
  res.status(aiResponseData?.statusCode ?? 500).json({
    error: { status: 'error', message: reasonMessage, details: errData },
  });
};

// =============================================================================
// Storage
// =============================================================================

export const createStorageConfig =
  (
    keyValueStoreService: KeyValueStoreService,
    defaultConfig: DefaultStorageConfig,
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const storageType = req.body.storageType;
      const config = req.body;

      switch (storageType.toLowerCase()) {
        case storageTypes.S3.toLowerCase(): {
          await keyValueStoreService.set<string>(
            configPaths.storageService,
            JSON.stringify({
              storageType: storageTypes.S3,
              s3: encryptConfig({
                accessKeyId: config.s3AccessKeyId,
                secretAccessKey: config.s3SecretAccessKey,
                region: config.s3Region,
                bucketName: config.s3BucketName,
              }),
            }),
          );
          logger.info('S3 storage configuration saved successfully');
          break;
        }

        case storageTypes.AZURE_BLOB.toLowerCase(): {
          if (config.azureBlobConnectionString) {
            // Store connection string JSON-encoded so decryptConfig can always be used on GET.
            await keyValueStoreService.set<string>(
              configPaths.storageService,
              JSON.stringify({
                storageType: storageTypes.AZURE_BLOB,
                azureBlob: encryptConfig({ connectionString: config.azureBlobConnectionString }),
              }),
            );
          } else {
            await keyValueStoreService.set<string>(
              configPaths.storageService,
              JSON.stringify({
                storageType: storageTypes.AZURE_BLOB,
                azureBlob: encryptConfig({
                  endpointProtocol: config.endpointProtocol || 'https',
                  accountName: config.accountName,
                  accountKey: config.accountKey,
                  endpointSuffix: config.endpointSuffix || 'core.windows.net',
                  containerName: config.containerName,
                }),
              }),
            );
          }
          logger.info('Azure Blob storage configuration saved successfully');
          break;
        }

        case storageTypes.LOCAL.toLowerCase(): {
          await keyValueStoreService.set<string>(
            configPaths.storageService,
            JSON.stringify({
              storageType: storageTypes.LOCAL,
              local: JSON.stringify({
                mountName: config.mountName || 'PipesHub',
                baseUrl: config.baseUrl || defaultConfig.endpoint,
              }),
            }),
          );
          logger.info('Local storage configuration saved successfully');
          break;
        }

        default:
          throw new BadRequestError(`Unsupported storage type: ${storageType}`);
      }

      sendValidatedJson(
        res,
        cmMessageResponseSchema,
        { message: 'Storage configuration saved successfully' },
        HTTP_STATUS.CREATED,
      );
    } catch (error: any) {
      logger.error('Error creating storage config', { error });
      next(error);
    }
  };

export const getStorageConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (
    _req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ) => {
    try {
      const raw =
        (await keyValueStoreService.get<string>(configPaths.storageService)) || '{}';
      const parsedConfig = JSON.parse(raw);
      const { storageType } = parsedConfig;

      if (!storageType) {
        throw new BadRequestError('Storage type not found');
      }

      if (storageType === storageTypes.S3) {
        if (!parsedConfig.s3) throw new BadRequestError('Storage config not found');
        const s3Config = decryptConfig<Record<string, string>>(parsedConfig.s3);
        sendValidatedJson(
          res,
          storageConfigResponseSchema,
          { storageType, ...s3Config },
          HTTP_STATUS.OK,
        );
        return;
      }

      if (storageType === storageTypes.AZURE_BLOB) {
        if (!parsedConfig.azureBlob) throw new BadRequestError('Storage config not found');
        const azureBlobConfig = decryptConfig<Record<string, string>>(parsedConfig.azureBlob);
        sendValidatedJson(
          res,
          storageConfigResponseSchema,
          { storageType, ...azureBlobConfig },
          HTTP_STATUS.OK,
        );
        return;
      }

      if (storageType === storageTypes.LOCAL) {
        // Merge storageType into the payload so the discriminatedUnion schema can validate it
        const localConfig = JSON.parse(parsedConfig.local || '{}');
        sendValidatedJson(
          res,
          storageConfigResponseSchema,
          { storageType: storageTypes.LOCAL, ...localConfig },
          HTTP_STATUS.OK,
        );
        return;
      }

      throw new BadRequestError(`Unsupported storage type: ${storageType}`);
    } catch (error: any) {
      logger.error('Error getting storage config', { error });
      next(error);
    }
  };

// =============================================================================
// SMTP
// =============================================================================

export const createSmtpConfig =
  (
    keyValueStoreService: KeyValueStoreService,
    communicationBackend: string,
    scopedJwtSecret: string,
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      if (!req.user) {
        throw new UnauthorizedError('User not Found');
      }

      await keyValueStoreService.set<string>(configPaths.smtp, encryptConfig(req.body));

      const response = await axios({
        method: 'post' as const,
        url: `${communicationBackend}/api/v1/mail/updateSmtpConfig`,
        headers: {
          Authorization: `Bearer ${await generateFetchConfigAuthToken(req.user, scopedJwtSecret)}`,
          'Content-Type': 'application/json',
        },
      });

      if (response.status !== 200) {
        throw new BadRequestError('Error setting smtp config');
      }

      sendValidatedJson(
        res,
        cmMessageResponseSchema,
        { message: 'SMTP config created successfully' },
        HTTP_STATUS.CREATED,
      );
    } catch (error: any) {
      logger.error('Error creating smtp config', { error });
      next(error);
    }
  };

export const getSmtpConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const encrypted = await keyValueStoreService.get<string>(configPaths.smtp);
      sendValidatedJson(
        res,
        smtpConfigResponseSchema,
        encrypted ? decryptConfig(encrypted) : {},
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error getting smtp config', { error });
      next(error);
    }
  };

// =============================================================================
// Slack Bot
// =============================================================================

const SLACK_BOT_CAS_MAX_RETRIES = 5;

const parseSlackBotStore = (encrypted: string | null | undefined): SlackBotStore => {
  if (!encrypted) return { configs: [] };
  try {
    const parsed = decryptConfig<Partial<SlackBotStore>>(encrypted);
    return { configs: Array.isArray(parsed.configs) ? parsed.configs : [] };
  } catch (error) {
    logger.warn('Failed to parse slack bot settings, using empty config', { error });
    return { configs: [] };
  }
};

export const getSlackBotStore = async (
  keyValueStoreService: KeyValueStoreService,
): Promise<SlackBotStore> => {
  const encrypted = await keyValueStoreService.get<string>(configPaths.slackBot);
  return parseSlackBotStore(encrypted);
};

const updateSlackBotStoreWithCAS = async <T>(
  keyValueStoreService: KeyValueStoreService,
  updater: (store: SlackBotStore) => T,
): Promise<T> => {
  for (let attempt = 0; attempt < SLACK_BOT_CAS_MAX_RETRIES; attempt++) {
    const encryptedCurrent = await keyValueStoreService.get<string>(configPaths.slackBot);
    const store = parseSlackBotStore(encryptedCurrent);
    const result = updater(store);

    const casSuccess = await keyValueStoreService.compareAndSet<string>(
      configPaths.slackBot,
      encryptedCurrent ?? null,
      encryptConfig(store),
    );

    if (casSuccess) return result;

    if (attempt === SLACK_BOT_CAS_MAX_RETRIES - 1) {
      throw new Error(
        'Failed to update Slack bot config due to concurrent modification. Please try again.',
      );
    }

    await new Promise((resolve) => setTimeout(resolve, 50 * (attempt + 1)));
  }

  throw new Error('Failed to update Slack bot config.');
};

const slackBotConfig = (config: SlackBotConfigEntry) => ({
  id: config.id,
  name: config.name,
  agentId: config.agentId ?? null,
  createdAt: config.createdAt,
  updatedAt: config.updatedAt,
  botToken: config.botToken,
  signingSecret: config.signingSecret,
});

export const getSlackBotConfigs =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const store = await getSlackBotStore(keyValueStoreService);
      sendValidatedJson(
        res,
        slackBotConfigsResponseSchema,
        { status: 'success', configs: store.configs.map(slackBotConfig) },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error getting slack bot configs', { error });
      next(error);
    }
  };

export const createSlackBotConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { name, botToken, signingSecret, agentId } = req.body;
      const normalizedAgentId =
        typeof agentId === 'string' && agentId.trim().length > 0
          ? agentId.trim()
          : undefined;

      const config = await updateSlackBotStoreWithCAS(
        keyValueStoreService,
        (store): SlackBotConfigEntry => {
          if (normalizedAgentId) {
            const duplicate = store.configs.find((c) => c.agentId === normalizedAgentId);
            if (duplicate) {
              throw new BadRequestError(
                'Selected agent is already linked to another Slack Bot configuration',
              );
            }
          }

          const timestamp = new Date().toISOString();
          const createdConfig: SlackBotConfigEntry = {
            id: uuidv4(),
            name,
            botToken,
            signingSecret,
            agentId: normalizedAgentId,
            createdAt: timestamp,
            updatedAt: timestamp,
          };

          store.configs.push(createdConfig);
          return createdConfig;
        },
      );

      sendValidatedJson(
        res,
        slackBotConfigMutationResponseSchema,
        { status: 'success', config: slackBotConfig(config) },
        HTTP_STATUS.CREATED,
      );
    } catch (error: any) {
      logger.error('Error creating slack bot config', { error });
      next(error);
    }
  };

export const updateSlackBotConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { configId } = req.params;
      const { name, botToken, signingSecret, agentId } = req.body;
      const normalizedAgentId =
        typeof agentId === 'string' && agentId.trim().length > 0
          ? agentId.trim()
          : undefined;

      const updatedConfig = await updateSlackBotStoreWithCAS(
        keyValueStoreService,
        (store): SlackBotConfigEntry => {
          const configIndex = store.configs.findIndex((c) => c.id === configId);
          if (configIndex === -1) {
            throw new NotFoundError('Slack Bot configuration not found');
          }

          if (normalizedAgentId) {
            const duplicate = store.configs.find(
              (c) => c.agentId === normalizedAgentId && c.id !== configId,
            );
            if (duplicate) {
              throw new BadRequestError(
                'Selected agent is already linked to another Slack Bot configuration',
              );
            }
          }

          const previousConfig = store.configs[configIndex];
          if (!previousConfig) throw new Error('Config not found');

          const nextConfig: SlackBotConfigEntry = {
            ...previousConfig,
            name,
            botToken,
            signingSecret,
            agentId: normalizedAgentId,
            updatedAt: new Date().toISOString(),
          };

          store.configs[configIndex] = nextConfig;
          return nextConfig;
        },
      );

      sendValidatedJson(
        res,
        slackBotConfigMutationResponseSchema,
        { status: 'success', config: slackBotConfig(updatedConfig) },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error updating slack bot config', { error });
      next(error);
    }
  };

export const deleteSlackBotConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { configId } = req.params;

      await updateSlackBotStoreWithCAS(keyValueStoreService, (store) => {
        const configIndex = store.configs.findIndex((c) => c.id === configId);
        if (configIndex === -1) {
          throw new NotFoundError('Slack Bot configuration not found');
        }
        store.configs.splice(configIndex, 1);
      });

      sendValidatedJson(
        res,
        slackBotConfigDeleteResponseSchema,
        { status: 'success', message: 'Slack Bot configuration deleted' },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error deleting slack bot config', { error });
      next(error);
    }
  };

// =============================================================================
// Platform Settings
// =============================================================================

export const setPlatformSettings =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { fileUploadMaxSizeBytes, featureFlags } = req.body;
      await keyValueStoreService.set<string>(
        configPaths.platform.settings,
        encryptConfig({
          fileUploadMaxSizeBytes,
          featureFlags,
          updatedAt: new Date().toISOString(),
        }),
      );
      sendValidatedJson(
        res,
        cmMessageResponseSchema,
        { message: 'Platform settings saved' },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error setting platform settings', { error });
      next(error);
    }
  };

export const getPlatformSettings =
  (keyValueStoreService: KeyValueStoreService) =>
  async (
    _req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ) => {
    try {
      const settings = await getPlatformSettingsFromStore(keyValueStoreService);
      sendValidatedJson(res, platformSettingsResponseSchema, settings, HTTP_STATUS.OK);
    } catch (error: any) {
      logger.error('Error getting platform settings', { error });
      next(error);
    }
  };

export const getAvailablePlatformFeatureFlags =
  () =>
  async (
    _req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    _next: NextFunction,
  ) => {
    sendValidatedJson(
      res,
      availablePlatformFlagsResponseSchema,
      { flags: PLATFORM_FEATURE_FLAGS },
      HTTP_STATUS.OK,
    );
  };

// =============================================================================
// Auth Configs
// =============================================================================

export const getAzureAdAuthConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const encrypted = await keyValueStoreService.get<string>(configPaths.auth.azureAD);
      sendValidatedJson(
        res,
        azureAdAuthConfigResponseSchema,
        encrypted ? decryptConfig(encrypted) : {},
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error getting auth config', { error });
      next(error);
    }
  };

export const setAzureAdAuthConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { clientId, tenantId, enableJit } = req.body;
      const authority = `https://login.microsoftonline.com/${tenantId}`;
      const encrypted = encryptConfig({ clientId, tenantId, authority, enableJit: enableJit ?? true });

      await keyValueStoreService.set<string>(
        configPaths.auth.azureAD,
        encrypted,
      );

      sendValidatedJson(
        res,
        cmMessageResponseSchema,
        { message: 'Azure AD config created successfully' },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error creating Azure AD auth config', { error });
      next(error);
    }
  };

  export const getMicrosoftAuthConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const encrypted = await keyValueStoreService.get<string>(configPaths.auth.microsoft);
      sendValidatedJson(
        res,
        microsoftAuthConfigResponseSchema,
        encrypted ? decryptConfig(encrypted) : {},
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error getting auth config', { error });
      next(error);
    }
  };

export const setMicrosoftAuthConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { clientId, tenantId, enableJit } = req.body;
      const authority = `https://login.microsoftonline.com/${tenantId}`;

      const encrypted = encryptConfig({ clientId, tenantId, authority, enableJit: enableJit ?? true });

      await keyValueStoreService.set<string>(
        configPaths.auth.microsoft,
        encrypted,
      );

      sendValidatedJson(
        res,
        cmMessageResponseSchema,
        { message: 'Microsoft Auth config created successfully' },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error creating Microsoft Auth config', { error });
      next(error);
    }
  };

export const getGoogleAuthConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const encrypted = await keyValueStoreService.get<string>(configPaths.auth.google);
      sendValidatedJson(
        res,
        googleAuthConfigResponseSchema,
        encrypted ? decryptConfig(encrypted) : {},
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error getting auth config', { error });
      next(error);
    }
  };

export const setGoogleAuthConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { clientId, enableJit } = req.body;
      const encrypted = encryptConfig({ clientId, enableJit: enableJit ?? true });
      await keyValueStoreService.set<string>(configPaths.auth.google, encrypted);
      sendValidatedJson(
        res,
        cmMessageResponseSchema,
        { message: 'Google Auth config created successfully' },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error creating Google Auth config', { error });
      next(error);
    }
  };

  export const getSsoAuthConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const encrypted = await keyValueStoreService.get<string>(configPaths.auth.sso);
      sendValidatedJson(
        res,
        ssoAuthConfigResponseSchema,
        encrypted ? decryptConfig(encrypted) : {},
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error getting SsoConfig', { error });
      next(error);
    }
  };


export const getOAuthConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const encrypted = await keyValueStoreService.get<string>(configPaths.auth.oauth);
      sendValidatedJson(
        res,
        oauthAuthConfigResponseSchema,
        encrypted ? decryptConfig(encrypted) : {},
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error getting OAuth config', { error });
      next(error);
    }
  };


  export const setOAuthConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { providerName, clientId, clientSecret, authorizationUrl, tokenEndpoint, userInfoEndpoint, scope, redirectUri, enableJit } = req.body;

      const oauthConfig = {
        providerName,
        clientId,
        ...(clientSecret && { clientSecret }),
        ...(authorizationUrl && { authorizationUrl }),
        ...(tokenEndpoint && { tokenEndpoint }),
        ...(userInfoEndpoint && { userInfoEndpoint }),
        ...(scope && { scope }),
        ...(redirectUri && { redirectUri }),
        enableJit: enableJit ?? true,
      };

      const encrypted = encryptConfig(oauthConfig);

      await keyValueStoreService.set<string>(
        configPaths.auth.oauth,
        encrypted,
      );

      sendValidatedJson(
        res,
        cmMessageResponseSchema,
        { message: 'OAuth config created successfully' },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error creating OAuth config', { error });
      next(error);
    }
  };


export const setSsoAuthConfig =
  (
    keyValueStoreService: KeyValueStoreService,
    samlController: SamlController,
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { entryPoint, emailKey, enableJit , samlPlatform} = req.body;
      let { certificate } = req.body;
      certificate = certificate
        .replace(/\\n/g, '') // Remove \n
        .replace(/\n/g, '') // Remove newline characters
        .replace(/\s+/g, '') // Remove all whitespace
        .replace(/\\/g, ''); // Remove any remaining backslashes

      // Step 2: Remove BEGIN and END certificate markers if present
      certificate = certificate
        .replace(/-----BEGINCERTIFICATE-----/g, '')
        .replace(/-----ENDCERTIFICATE-----/g, '');

      certificate = certificate
        .replace(/-----BEGIN CERTIFICATE-----/g, '')
        .replace(/-----END ERTIFICATE-----/g, '');
      // Step 3: Ensure the certificate content is clean
      certificate = certificate.trim();

      const encrypted = encryptConfig({ certificate, entryPoint, emailKey, enableJit: enableJit ?? true , samlPlatform });
      await keyValueStoreService.set<string>(
        configPaths.auth.sso,
        encrypted,
      );
      await samlController.updateSamlStrategiesWithCallback();
      sendValidatedJson(
        res,
        cmMessageResponseSchema,
        { message: 'Sso config created successfully' },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error creating Sso config', { error });
      next(error);
    }
  };

// =============================================================================
// Legacy Database Configs (not exposed via cm_routes.ts)
// =============================================================================

export const createArangoDbConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { url, username, password } = req.body;
      await keyValueStoreService.set<string>(
        configPaths.db.arangodb,
        encryptConfig({ url, username, password, db: ARANGO_DB_NAME }),
      );
      sendValidatedJson(
        res,
        cmMessageResponseSchema,
        { message: 'Arango DB config created successfully' },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error creating ArangoDB config', { error });
      next(error);
    }
  };

export const getArangoDbConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const encrypted = await keyValueStoreService.get<string>(configPaths.db.arangodb);
      sendValidatedJson(
        res,
        arangoDbConfigResponseSchema,
        encrypted ? decryptConfig(encrypted) : { url: '', username: '', password: '', db: '' },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error getting ArangoDB config', { error });
      next(error);
    }
  };

export const createMongoDbConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { uri } = req.body;
      await keyValueStoreService.set<string>(
        configPaths.db.mongodb,
        encryptConfig({ uri, db: MONGO_DB_NAME }),
      );
      sendValidatedJson(
        res,
        cmMessageResponseSchema,
        { message: 'Mongo DB config created successfully' },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error creating MongoDB config', { error });
      next(error);
    }
  };

export const getMongoDbConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const encrypted = await keyValueStoreService.get<string>(configPaths.db.mongodb);
      sendValidatedJson(
        res,
        mongoDbConfigResponseSchema,
        encrypted ? decryptConfig(encrypted) : { uri: '', db: '' },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error getting MongoDB config', { error });
      next(error);
    }
  };

export const createRedisConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { host, port, password, tls } = req.body;
      await keyValueStoreService.set<string>(
        configPaths.keyValueStore.redis,
        encryptConfig({ host, port, password, tls }),
      );
      sendValidatedJson(
        res,
        cmMessageResponseSchema,
        { message: 'Redis config created successfully' },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error creating Redis config', { error });
      next(error);
    }
  };

export const getRedisConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const encrypted = await keyValueStoreService.get<string>(configPaths.keyValueStore.redis);
      sendValidatedJson(
        res,
        redisConfigResponseSchema,
        encrypted ? decryptConfig(encrypted) : { host: '', port: 0, password: '', tls: false },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error getting Redis config', { error });
      next(error);
    }
  };

export const createKafkaConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { brokers, sasl } = req.body;
      await keyValueStoreService.set<string>(
        configPaths.broker.kafka,
        encryptConfig({ brokers, sasl }),
      );
      sendValidatedJson(
        res,
        cmMessageResponseSchema,
        { message: 'Kafka config created successfully', warningMessage: res.getHeader('warning') },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error creating Kafka config', { error });
      next(error);
    }
  };

export const getKafkaConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const encrypted = await keyValueStoreService.get<string>(configPaths.broker.kafka);
      sendValidatedJson(
        res,
        kafkaConfigResponseSchema,
        encrypted ? decryptConfig(encrypted) : { brokers: [], sasl: {} },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error getting Kafka config', { error });
      next(error);
    }
  };

export const createQdrantConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { port, apiKey, host, grpcPort } = req.body;
      await keyValueStoreService.set<string>(
        configPaths.db.qdrant,
        encryptConfig({ port, apiKey, host, grpcPort }),
      );
      sendValidatedJson(
        res,
        cmMessageResponseSchema,
        { message: 'Qdrant config created successfully', warningMessage: res.getHeader('warning') },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error creating Qdrant config', { error });
      next(error);
    }
  };

export const getQdrantConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const encrypted = await keyValueStoreService.get<string>(configPaths.db.qdrant);
      sendValidatedJson(
        res,
        qdrantConfigResponseSchema,
        encrypted ? decryptConfig(encrypted) : { port: 0, apiKey: '', host: '', grpcPort: 0 },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error getting Qdrant config', { error });
      next(error);
    }
  };

export const getFrontendPublicUrl =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const url =
        (await keyValueStoreService.get<string>(configPaths.endpoint)) || '{}';
      const parsedUrl = JSON.parse(url);
      if (parsedUrl?.frontend?.publicEndpoint) {
        sendValidatedJson(
          res,
          frontendPublicUrlResponseSchema,
          { url: parsedUrl?.frontend?.publicEndpoint },
          HTTP_STATUS.OK,
        );
      } else {
        sendValidatedJson(
          res,
          frontendPublicUrlResponseSchema,
          {},
          HTTP_STATUS.OK,
        );
      }
    } catch (error: any) {
      logger.error('Error getting Frontend Public Url', { error });
      next(error);
    }
  };


// =============================================================================
// Metrics Collection
// =============================================================================

export const toggleMetricsCollection =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { enableMetricCollection } = req.body;
      const metricsCollection = JSON.parse(
        (await keyValueStoreService.get<string>(configPaths.metricsCollection)) || '{}',
      );

      if (enableMetricCollection !== metricsCollection.enableMetricCollection) {
        metricsCollection.enableMetricCollection = enableMetricCollection;
        await keyValueStoreService.set<string>(
          configPaths.metricsCollection,
          JSON.stringify(metricsCollection),
        );
      }

      sendValidatedJson(
        res,
        cmMessageResponseSchema,
        { message: 'Metrics collection toggled successfully' },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error toggling metrics collection', { error });
      next(error);
    }
  };

export const getMetricsCollection =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const metricsCollection = JSON.parse(
        (await keyValueStoreService.get<string>(configPaths.metricsCollection)) || '{}',
      );
      sendValidatedJson(res, metricsCollectionResponseSchema, metricsCollection, HTTP_STATUS.OK);
    } catch (error: any) {
      logger.error('Error getting metrics collection', { error });
      next(error);
    }
  };

export const setMetricsCollectionPushInterval =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { pushIntervalMs } = req.body;
      const metricsCollection = JSON.parse(
        (await keyValueStoreService.get<string>(configPaths.metricsCollection)) || '{}',
      );

      if (pushIntervalMs !== metricsCollection.pushIntervalMs) {
        metricsCollection.pushIntervalMs = pushIntervalMs;
        await keyValueStoreService.set<string>(
          configPaths.metricsCollection,
          JSON.stringify(metricsCollection),
        );
      }

      sendValidatedJson(
        res,
        cmMessageResponseSchema,
        { message: 'Metrics collection push interval set successfully' },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error setting metrics collection push interval', { error });
      next(error);
    }
  };

export const setMetricsCollectionRemoteServer =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { serverUrl } = req.body;
      const metricsCollection = JSON.parse(
        (await keyValueStoreService.get<string>(configPaths.metricsCollection)) || '{}',
      );

      if (serverUrl !== metricsCollection.serverUrl) {
        metricsCollection.serverUrl = serverUrl;
        await keyValueStoreService.set<string>(
          configPaths.metricsCollection,
          JSON.stringify(metricsCollection),
        );
      }

      sendValidatedJson(
        res,
        cmMessageResponseSchema,
        { message: 'Metrics collection remote server set successfully' },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error setting metrics collection remote server', { error });
      next(error);
    }
  };

// =============================================================================
// AI Models - Legacy
// =============================================================================

export const createAIModelsConfig =
  (
    keyValueStoreService: KeyValueStoreService,
    appConfig: AppConfig,
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const aiConfig = req.body;
      if (!aiConfig) {
        throw new BadRequestError('Invalid configuration passed');
      }

      if (aiConfig.llm.length > 0) {
        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/llm-health-check`,
          method: HttpMethod.POST,
          headers: req.headers as Record<string, string>,
          body: aiConfig.llm,
        };

        logger.debug('Health Check for AI llm Config API calling');

        const aiServiceCommand = new AIServiceCommand(aiCommandOptions);
        const aiResponseData = (await aiServiceCommand.execute()) as AIServiceResponse;

        if (!aiResponseData?.data || aiResponseData.statusCode !== 200) {
          throw new InternalServerError(
            'Failed to do health check of llm configuration, check credentials again',
            aiResponseData?.data,
          );
        }
      }

      if (aiConfig.embedding.length > 0) {
        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/embedding-health-check`,
          method: HttpMethod.POST,
          headers: req.headers as Record<string, string>,
          body: aiConfig.embedding,
        };

        logger.debug('Health Check for AI embedding Config API calling');

        const aiServiceCommand = new AIServiceCommand(aiCommandOptions);
        const aiResponseData = (await aiServiceCommand.execute()) as AIServiceResponse;

        if (!aiResponseData?.data || aiResponseData.statusCode !== 200) {
          throw new InternalServerError(
            'Failed to do health check of embedding configuration, check credentials again',
            aiResponseData?.data,
          );
        }
      }

      if (aiConfig.llm.length > 0) {
        aiConfig.llm.forEach((llm: any, index: number) => {
          llm.modelKey = uuidv4();
          llm.isMultimodal = false;
          llm.isReasoning = false;
          llm.isDefault = index === 0;
        });
      }

      if (aiConfig.embedding.length > 0) {
        aiConfig.embedding.forEach((embedding: any, index: number) => {
          embedding.modelKey = uuidv4();
          embedding.isMultimodal = false;
          embedding.isDefault = index === 0;
        });
      }

      await saveAIModels(keyValueStoreService, aiConfig);

      sendValidatedJson(
        res,
        cmMessageResponseSchema,
        { message: 'AI config created successfully' },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error creating ai models config', { error });
      next(error);
    }
  };

export const getAIModelsConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const aiModels = await loadAIModels(keyValueStoreService);
      sendValidatedJson(res, aiModelsConfigResponseSchema, aiModels ?? {}, HTTP_STATUS.OK);
    } catch (error: any) {
      logger.error('Error getting ai models config', { error });
      next(error);
    }
  };

// =============================================================================
// AI Models - Provider Management
// =============================================================================

export const getAIModelsProviders =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const aiModels = await loadAIModels(keyValueStoreService);

      if (!aiModels) {
        sendValidatedJson(
          res,
          aiModelsProvidersResponseSchema,
          { status: 'success', models: createEmptyAIModelsState(), message: 'No AI models found' },
          HTTP_STATUS.OK,
        );
        return;
      }

      ensureAIModelsShape(aiModels);

      sendValidatedJson(
        res,
        aiModelsProvidersResponseSchema,
        { status: 'success', models: aiModels, message: 'AI models retrieved successfully' },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error getting AI models providers', { error });
      next(error);
    }
  };

export const getModelsByType =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      // modelType is already validated by the Zod middleware (modelTypeSchema)
      const { modelType } = req.params;
      const aiModels = await loadAIModels(keyValueStoreService);

      if (!aiModels || !Array.isArray(aiModels[modelType as AIModelTypeValue])) {
        sendValidatedJson(
          res,
          aiModelsByTypeResponseSchema,
          { status: 'success', models: [], message: `No ${modelType} models found` },
          HTTP_STATUS.OK,
        );
        return;
      }

      const configs = aiModels[modelType as AIModelTypeValue];
      sendValidatedJson(
        res,
        aiModelsByTypeResponseSchema,
        { status: 'success', models: configs, message: `Found ${configs.length} ${modelType} models` },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error getting models by type', { error });
      next(error);
    }
  };

export const getAvailableModelsByType =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      // modelType is already validated by the Zod middleware (modelTypeSchema)
      const { modelType } = req.params;
      const aiModels = await loadAIModels(keyValueStoreService);

      if (!aiModels || !Array.isArray(aiModels[modelType as AIModelTypeValue])) {
        sendValidatedJson(
          res,
          aiModelsAvailableByTypeResponseSchema,
          { status: 'success', models: [], message: `No ${modelType} models found` },
          HTTP_STATUS.OK,
        );
        return;
      }

      const flattenedModels = [];

      for (const config of aiModels[modelType as AIModelTypeValue]) {
        const modelNames: string[] = config.configuration?.model
          ? config.configuration.model
              .split(',')
              .map((n: string) => n.trim())
              .filter(Boolean)
          : [];

        // Only include modelFriendlyName when there is exactly one model name
        const shouldIncludeFriendlyName = modelNames.length === 1 && config.modelFriendlyName;
        let markDefault = config.isDefault;

        for (const modelName of modelNames) {
          flattenedModels.push({
            modelType,
            provider: config.provider,
            modelName,
            modelKey: config.modelKey,
            isMultimodal: config.isMultimodal || false,
            isReasoning: config.isReasoning || false,
            isDefault: markDefault,
            ...(shouldIncludeFriendlyName && { modelFriendlyName: config.modelFriendlyName }),
          });
          markDefault = false; // Only the first model in the entry is marked default
        }
      }

      sendValidatedJson(
        res,
        aiModelsAvailableByTypeResponseSchema,
        {
          status: 'success',
          models: flattenedModels,
          message: `Found ${flattenedModels.length} ${modelType} models`,
        },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error getting available models by type', { error });
      next(error);
    }
  };

export const addAIModelProvider =
  (
    keyValueStoreService: KeyValueStoreService,
    appConfig: AppConfig,
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const {
        modelType,
        provider,
        configuration,
        isMultimodal = false,
        isDefault = false,
        isReasoning = false,
        contextLength,
      } = req.body;

      // modelType, provider, and configuration are guaranteed valid by Zod middleware
      const modelTypeKey = modelType as AIModelTypeValue;

      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/health-check/${modelType}`,
        method: HttpMethod.POST,
        headers: req.headers as Record<string, string>,
        body: { provider, configuration, modelType, isMultimodal, isDefault, isReasoning, contextLength },
      };

      logger.debug('Health Check for AI model Config API calling');

      const aiServiceCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponseData = (await aiServiceCommand.execute()) as AIServiceResponse;

      if (!aiResponseData?.data || aiResponseData.statusCode !== 200) {
        replyAIHealthCheckFailure(res, aiResponseData, modelType);
        return;
      }

      const aiModels = (await loadAIModels(keyValueStoreService)) ?? {};
      ensureAIModelsShape(aiModels);

      // Generate unique model key with collision check
      let modelKey: string;
      do {
        modelKey = uuidv4();
      } while (aiModels[modelTypeKey].some((c: any) => c.modelKey === modelKey));

      const modelFriendlyName = configuration.modelFriendlyName;

      if (isDefault) {
        for (const config of aiModels[modelTypeKey]) {
          config.isDefault = false;
        }
      }

      aiModels[modelTypeKey].push({
        provider,
        configuration,
        modelKey,
        isMultimodal,
        isDefault,
        isReasoning,
        contextLength,
        ...(modelFriendlyName && { modelFriendlyName }),
      });

      await saveAIModels(keyValueStoreService, aiModels);

      sendValidatedJson(
        res,
        aiModelMutationResponseSchema,
        {
          status: 'success',
          message: `${modelTypeKey.toUpperCase()} provider added successfully`,
          details: {
            modelKey,
            modelType: modelTypeKey,
            provider,
            model: configuration.model,
            isDefault,
            contextLength,
          },
        },
        HTTP_STATUS.CREATED,
      );
    } catch (error: any) {
      logger.error('Error adding AI model provider', { error });
      next(handleBackendError(error, 'add AI model provider'));
    }
  };

export const updateAIModelProvider =
  (
    keyValueStoreService: KeyValueStoreService,
    appConfig: AppConfig,
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { modelType, modelKey } = req.params;
      const {
        provider,
        configuration,
        isMultimodal = false,
        isReasoning = false,
        isDefault = false,
        contextLength,
      } = req.body;

      logger.debug('updateAIModelProvider', {
        modelType, modelKey, provider, configuration, isMultimodal, isReasoning, isDefault, contextLength,
      });

      // provider and configuration are guaranteed valid by Zod middleware
      const healthCheckPayload = {
        provider,
        configuration,
        modelType,
        isMultimodal,
        isReasoning,
        isDefault,
        contextLength,
      };

      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/health-check/${modelType}`,
        method: HttpMethod.POST,
        headers: req.headers as Record<string, string>,
        body: healthCheckPayload,
      };

      logger.debug('Health Check for AI model Config API calling');

      const aiServiceCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponseData = (await aiServiceCommand.execute()) as AIServiceResponse;

      if (!aiResponseData?.data || aiResponseData.statusCode !== 200) {
        replyAIHealthCheckFailure(res, aiResponseData, modelType as string);
        return;
      }

      const aiModels = await loadAIModels(keyValueStoreService);
      if (!aiModels) {
        throw new NotFoundError('No AI models configuration found');
      }

      let targetModel: any = null;
      let targetModelType: string | null = null;

      for (const [mType, mConfigs] of Object.entries(aiModels)) {
        for (const config of mConfigs as any[]) {
          if (config.modelKey === modelKey) {
            targetModel = config;
            targetModelType = mType;
            break;
          }
        }
        if (targetModel) break;
      }

      if (!targetModel || !targetModelType) {
        throw new NotFoundError(
          `Model with key '${modelKey}' not found or model type not found`,
        );
      }

      if (modelType && targetModelType !== modelType) {
        throw new BadRequestError(
          `Model key '${modelKey}' belongs to type '${targetModelType}', not '${modelType}'`,
        );
      }

      const modelFriendlyName = configuration.modelFriendlyName;

      targetModel.configuration = configuration;
      targetModel.isMultimodal = isMultimodal;
      targetModel.isDefault = isDefault;
      targetModel.isReasoning = isReasoning;
      targetModel.contextLength = contextLength || null;
      if (modelFriendlyName !== undefined) {
        targetModel.modelFriendlyName = modelFriendlyName;
      }

      if (isDefault) {
        for (const config of aiModels[targetModelType as AIModelTypeValue]) {
          if (config.modelKey !== modelKey) config.isDefault = false;
        }
      }

      await saveAIModels(keyValueStoreService, aiModels);

      sendValidatedJson(
        res,
        aiModelMutationResponseSchema,
        {
          status: 'success',
          message: `${targetModelType.toUpperCase()} provider updated successfully`,
          details: {
            modelKey,
            modelType: targetModelType,
            provider: targetModel.provider,
            model: targetModel.configuration?.model,
            contextLength: targetModel.contextLength,
            isMultimodal: targetModel.isMultimodal,
            isReasoning: targetModel.isReasoning,
            isDefault: targetModel.isDefault,
          },
        },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error updating AI model provider', { error });
      next(handleBackendError(error, 'update AI model provider'));
    }
  };

export const deleteAIModelProvider =
  (
    keyValueStoreService: KeyValueStoreService,
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { modelType, modelKey } = req.params;

      const aiModels = await loadAIModels(keyValueStoreService);
      if (!aiModels) {
        throw new NotFoundError('No AI models configuration found');
      }

      let deletedModel: any = null;
      let targetModelType: string | null = null;
      let modelIndex = -1;

      for (const [mType, mConfigs] of Object.entries(aiModels)) {
        if (!Array.isArray(mConfigs)) continue;
        for (let i = 0; i < mConfigs.length; i++) {
          const config = mConfigs[i];
          if (
            config &&
            typeof config === 'object' &&
            'modelKey' in config &&
            config.modelKey === modelKey
          ) {
            deletedModel = config;
            targetModelType = mType;
            modelIndex = i;
            break;
          }
        }
        if (deletedModel) break;
      }

      if (!deletedModel || !targetModelType) {
        throw new NotFoundError(`Model with key '${modelKey}' not found`);
      }

      if (modelType && targetModelType !== modelType) {
        throw new BadRequestError(
          `Model key '${modelKey}' belongs to type '${targetModelType}', not '${modelType}'`,
        );
      }

      const wasDefault = deletedModel.isDefault || false;
      aiModels[targetModelType as AIModelTypeValue].splice(modelIndex, 1);

      if (wasDefault && aiModels[targetModelType as AIModelTypeValue].length > 0) {
        aiModels[targetModelType][0].isDefault = true;
      }

      await saveAIModels(keyValueStoreService, aiModels);

      sendValidatedJson(
        res,
        aiModelMutationResponseSchema,
        {
          status: 'success',
          message: `${targetModelType.toUpperCase()} provider deleted successfully`,
          details: {
            modelKey,
            modelType: targetModelType,
            provider: deletedModel.provider,
            model: deletedModel.configuration?.model,
            wasDefault,
            contextLength: deletedModel.contextLength,
          },
        },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error deleting AI model provider', { error });
      next(error);
    }
  };

export const updateDefaultAIModel =
  (
    keyValueStoreService: KeyValueStoreService,
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { modelType, modelKey } = req.params;

      const aiModels = await loadAIModels(keyValueStoreService);
      if (!aiModels) {
        throw new NotFoundError('No AI models configuration found');
      }

      let targetModel: any = null;
      let targetModelType: string | null = null;

      for (const [mType, mConfigs] of Object.entries(aiModels)) {
        for (const config of mConfigs as any[]) {
          if (config.modelKey === modelKey) {
            targetModel = config;
            targetModelType = mType;
            break;
          }
        }
        if (targetModel) break;
      }

      if (!targetModel || !targetModelType) {
        throw new NotFoundError(`Model with key '${modelKey}' not found`);
      }

      if (modelType && targetModelType !== modelType) {
        throw new BadRequestError(
          `Model key '${modelKey}' belongs to type '${targetModelType}', not '${modelType}'`,
        );
      }

      for (const config of aiModels[targetModelType]) {
        config.isDefault = false;
      }
      targetModel.isDefault = true;

      await saveAIModels(keyValueStoreService, aiModels);

      sendValidatedJson(
        res,
        aiModelMutationResponseSchema,
        {
          status: 'success',
          message: `Default ${targetModelType} model updated successfully`,
          details: {
            modelKey,
            modelType: targetModelType,
            provider: targetModel.provider,
            model: targetModel.configuration?.model,
            contextLength: targetModel.contextLength,
          },
        },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error updating default AI model', { error });
      next(error);
    }
  };


// =============================================================================
// Custom System Prompt
// =============================================================================

export const getCustomSystemPrompt =
  (keyValueStoreService: KeyValueStoreService) =>
  async (
    _req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ) => {
    try {
      const aiModels = (await loadAIModels(keyValueStoreService)) as AIModelsConfig | null;
      sendValidatedJson(
        res,
        customSystemPromptResponseSchema,
        { customSystemPrompt: aiModels?.customSystemPrompt ?? '' },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error getting custom system prompt', { error });
      next(error);
    }
  };

export const setCustomSystemPrompt =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { customSystemPrompt } = req.body;

      if (typeof customSystemPrompt !== 'string') {
        throw new BadRequestError('customSystemPrompt must be a string');
      }

      // Use Compare-and-Set (CAS) pattern with retries to prevent race conditions
      const MAX_RETRIES = 5;
      let success = false;

      for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
        const encryptedAIConfig = await keyValueStoreService.get<string>(configPaths.aiModels);

        const aiModels: AIModelsConfig = encryptedAIConfig
          ? decryptConfig<AIModelsConfig>(encryptedAIConfig)
          : {};

        aiModels.customSystemPrompt = customSystemPrompt;

        const casSuccess = await keyValueStoreService.compareAndSet<string>(
          configPaths.aiModels,
          encryptedAIConfig,
          encryptConfig(aiModels),
        );

        if (casSuccess) {
          success = true;
          break;
        }

        if (attempt === MAX_RETRIES - 1) {
          throw new Error(
            'Failed to update custom system prompt due to persistent concurrent modification. Please try again.',
          );
        }

        await new Promise((resolve) => setTimeout(resolve, 50 * (attempt + 1)));
      }

      if (!success) {
        throw new Error('Failed to update custom system prompt after maximum retries.');
      }

      sendValidatedJson(
        res,
        customSystemPromptUpdateResponseSchema,
        { message: 'Custom system prompt updated successfully', customSystemPrompt },
        HTTP_STATUS.OK,
      );
    } catch (error: any) {
      logger.error('Error setting custom system prompt', { error });
      next(error);
    }
  };
