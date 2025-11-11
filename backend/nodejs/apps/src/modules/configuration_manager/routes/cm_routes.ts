import { Router, Response, NextFunction } from 'express';
import { Container } from 'inversify';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import {
  createAIModelsConfig,
  createGoogleWorkspaceCredentials,
  createKafkaConfig,
  createRedisConfig,
  createSmtpConfig,
  createStorageConfig,
  getAIModelsConfig,
  getAzureAdAuthConfig,
  getGoogleAuthConfig,
  getGoogleWorkspaceOauthConfig,
  getGoogleWorkspaceCredentials,
  getKafkaConfig,
  getMicrosoftAuthConfig,
  getOAuthConfig,
  getRedisConfig,
  getSmtpConfig,
  getSsoAuthConfig,
  getStorageConfig,
  setAzureAdAuthConfig,
  setGoogleAuthConfig,
  setMicrosoftAuthConfig,
  setOAuthConfig,
  setSsoAuthConfig,
  setGoogleWorkspaceOauthConfig,
  createArangoDbConfig,
  getArangoDbConfig,
  createMongoDbConfig,
  getMongoDbConfig,
  deleteGoogleWorkspaceCredentials,
  getGoogleWorkspaceBusinessCredentials,
  getQdrantConfig,
  createQdrantConfig,
  getFrontendUrl,
  setFrontendUrl,
  getConnectorPublicUrl,
  setConnectorPublicUrl,
  toggleMetricsCollection,
  getMetricsCollection,
  setMetricsCollectionPushInterval,
  setMetricsCollectionRemoteServer,
  getAvailableModelsByType,
  addAIModelProvider,
  updateAIModelProvider,
  deleteAIModelProvider,
  updateDefaultAIModel,
  getAIModelsProviders,
  getModelsByType,
  getAtlassianOauthConfig,
  setAtlassianOauthConfig,
  getOneDriveCredentials,
  getSharePointCredentials,
  setSharePointCredentials,
  setOneDriveCredentials,
  getConnectorConfig,
  getPlatformSettings,
  setPlatformSettings,
  getAvailablePlatformFeatureFlags,
} from '../controller/cm_controller';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import {
  redisConfigSchema,
  smtpConfigSchema,
  kafkaConfigSchema,
  aiModelsConfigSchema,
  storageValidationSchema,
  azureAdConfigSchema,
  googleAuthConfigSchema,
  oauthConfigSchema,
  ssoConfigSchema,
  googleWorkspaceConfigSchema,
  mongoDBConfigSchema,
  arangoDBConfigSchema,
  qdrantConfigSchema,
  platformSettingsSchema,
  urlSchema,
  metricsCollectionPushIntervalSchema,
  metricsCollectionToggleSchema,
  metricsCollectionRemoteServerSchema,
  modelTypeSchema,
  updateDefaultModelSchema,
  deleteProviderSchema,
  addProviderRequestSchema,
  updateProviderRequestSchema,
  atlassianCredentialsSchema,
  onedriveCredentialsSchema,
  sharepointCredentialsSchema,
} from '../validator/validators';
import { FileProcessorFactory } from '../../../libs/middlewares/file_processor/fp.factory';
import { FileProcessingType } from '../../../libs/middlewares/file_processor/fp.constant';
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';
import {
  checkArangoHealth,
  checkKafkaHealth,
  checkMongoHealth,
  checkQdrantHealth,
  checkRedisHealth,
} from '../middlewares/health.middleware';

import { userAdminCheck } from '../../user_management/middlewares/userAdminCheck';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  AuthenticatedUserRequest,
  AuthenticatedServiceRequest,
} from '../../../libs/middlewares/types';
import { NotFoundError } from '../../../libs/errors/http.errors';
import { ConfigService } from '../services/updateConfig.service';
import {
  EntitiesEventProducer,
  SyncEventProducer,
} from '../services/kafka_events.service';

export function createConfigurationManagerRouter(container: Container): Router {
  const router = Router();
  const keyValueStoreService = container.get<KeyValueStoreService>(
    'KeyValueStoreService',
  );
  const appConfig = container.get<AppConfig>('AppConfig');
  const entityEventService = container.get<EntitiesEventProducer>(
    'EntitiesEventProducer',
  );
  const syncEventService =
    container.get<SyncEventProducer>('SyncEventProducer');
  const configService = container.get<ConfigService>('ConfigService');
  const authMiddleware = container.get<AuthMiddleware>('AuthMiddleware');
  // storage config routes

  /**
   * POST /storageConfig
   * Creates or updates storage configuration in the key-value store
   * Requires authentication
   * @param {Object} req.body.storageConfig - Storage configuration object to store
   * @returns {Object} The stored configuration object
   */
  router.post(
    '/storageConfig',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(storageValidationSchema),
    createStorageConfig(keyValueStoreService, appConfig.storage),
  );

  /**
   * GET /storageConfig
   * Retrieves the current storage configuration from key-value store
   * Requires authentication
   * @returns {Object} The stored configuration object or null if not found
   */
  router.get(
    '/storageConfig',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    getStorageConfig(keyValueStoreService),
  );

  router.get(
    '/internal/storageConfig',
    authMiddleware.scopedTokenValidator(TokenScopes.STORAGE_TOKEN),
    metricsMiddleware(container),
    getStorageConfig(keyValueStoreService),
  );

  // SMTP Config Routes
  /**
   * POST /smtpConfig
   * Creates or updates SMTP configuration in the key-value store
   * Requires authentication
   * @param {Object} req.body.smtpConfig - SMTP configuration object to store
   * @returns {Object} The stored configuration object
   */
  router.post(
    '/smtpConfig',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(smtpConfigSchema),
    createSmtpConfig(
      keyValueStoreService,
      appConfig.communicationBackend,
      appConfig.scopedJwtSecret,
    ),
  );

  router.get(
    '/internal/connectors/atlassian/config',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    metricsMiddleware(container),
    (req: AuthenticatedServiceRequest, res: Response, next: NextFunction) => {
      if (!req.tokenPayload) {
        throw new NotFoundError('User not found');
      }
      return getAtlassianOauthConfig(keyValueStoreService)(req, res, next);
    },
  );

  router.get(
    '/connectors/atlassian/config',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      if (!req.user) {
        throw new NotFoundError('User not found');
      }
      return getAtlassianOauthConfig(keyValueStoreService)(req, res, next);
    },
  );

  router.post(
    '/connectors/atlassian/config',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(atlassianCredentialsSchema),
    (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      if (!req.user) {
        throw new NotFoundError('User not found');
      }
      return setAtlassianOauthConfig(keyValueStoreService)(req, res, next);
    },
  );

  router.post(
    '/internal/connectors/atlassian/config',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    metricsMiddleware(container),
    ValidationMiddleware.validate(atlassianCredentialsSchema),
    (req: AuthenticatedServiceRequest, res: Response, next: NextFunction) => {
      if (!req.tokenPayload) {
        throw new NotFoundError('User not found');
      }
      return setAtlassianOauthConfig(keyValueStoreService)(req, res, next);
    },
  );

  router.get(
    '/connectors/onedrive/config',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      if (!req.user) {
        throw new NotFoundError('User not found');
      }
      return getOneDriveCredentials(keyValueStoreService)(req, res, next);
    },
  );

  router.post(
    '/internal/connectors/onedrive/config',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    metricsMiddleware(container),
    ValidationMiddleware.validate(onedriveCredentialsSchema),
    (req: AuthenticatedServiceRequest, res: Response, next: NextFunction) => {
      if (!req.tokenPayload) {
        throw new NotFoundError('User not found');
      }
      return setOneDriveCredentials(keyValueStoreService)(req, res, next);
    },
  );

  router.get(
    '/connectors/sharepoint/config',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      if (!req.user) {
        throw new NotFoundError('User not found');
      }
      return getSharePointCredentials(keyValueStoreService)(req, res, next);
    },
  );

  router.post(
    '/internal/connectors/sharepoint/config',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    metricsMiddleware(container),
    ValidationMiddleware.validate(sharepointCredentialsSchema),
    (req: AuthenticatedServiceRequest, res: Response, next: NextFunction) => {
      if (!req.tokenPayload) {
        throw new NotFoundError('User not found');
      }
      return setSharePointCredentials(keyValueStoreService)(req, res, next);
    },
  );

  // Generic internal connector config fetch: /internal/connectors/:connector/config
  router.get(
    '/internal/connectors/:connector/config',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    metricsMiddleware(container),
    (req: AuthenticatedServiceRequest, res: Response, next: NextFunction) => {
      if (!req.tokenPayload) {
        throw new NotFoundError('User not found');
      }
      return getConnectorConfig(keyValueStoreService)(req, res, next);
    },
  );

  router.post(
    '/connectors/sharepoint/config',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(sharepointCredentialsSchema),
    (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      if (!req.user) {
        throw new NotFoundError('User not found');
      }
      return setSharePointCredentials(keyValueStoreService)(req, res, next);
    },
  );

  router.post(
    '/connectors/onedrive/config',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(onedriveCredentialsSchema),
    (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      if (!req.user) {
        throw new NotFoundError('User not found');
      }
      return setOneDriveCredentials(keyValueStoreService)(req, res, next);
    },
  );

  /**
   * GET /smtpConfig
   * Retrieves the current SMTP configuration from key-value store
   * Requires authentication
   * @returns {Object} The stored configuration object or null if not found
   */
  router.get(
    '/smtpConfig',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    getSmtpConfig(keyValueStoreService),
  );

  // auth config routes
  router.get(
    '/authConfig/azureAd',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    getAzureAdAuthConfig(keyValueStoreService),
  );

  router.get(
    '/internal/authConfig/azureAd',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    metricsMiddleware(container),
    getAzureAdAuthConfig(keyValueStoreService),
  );

  router.post(
    '/authConfig/azureAd',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(azureAdConfigSchema),
    setAzureAdAuthConfig(keyValueStoreService),
  );

  router.get(
    '/authConfig/microsoft',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    getMicrosoftAuthConfig(keyValueStoreService),
  );
  router.get(
    '/internal/authConfig/microsoft',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    metricsMiddleware(container),
    getMicrosoftAuthConfig(keyValueStoreService),
  );

  router.post(
    '/authConfig/microsoft',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(azureAdConfigSchema),
    setMicrosoftAuthConfig(keyValueStoreService),
  );

  router.get(
    '/authConfig/google',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    getGoogleAuthConfig(keyValueStoreService),
  );

  router.get(
    '/internal/authConfig/google',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    metricsMiddleware(container),
    getGoogleAuthConfig(keyValueStoreService),
  );
  router.post(
    '/authConfig/google',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(googleAuthConfigSchema),
    setGoogleAuthConfig(keyValueStoreService),
  );

  router.get(
    '/authConfig/sso',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    getSsoAuthConfig(keyValueStoreService),
  );
  router.get(
    '/internal/authConfig/sso',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    metricsMiddleware(container),
    getSsoAuthConfig(keyValueStoreService),
  );
  router.post(
    '/authConfig/sso',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(ssoConfigSchema),
    setSsoAuthConfig(keyValueStoreService),
  );

  // OAuth config routes
  router.get(
    '/authConfig/oauth',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    getOAuthConfig(keyValueStoreService),
  );

  router.get(
    '/internal/authConfig/oauth',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    metricsMiddleware(container),
    getOAuthConfig(keyValueStoreService),
  );

  router.post(
    '/authConfig/oauth',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(oauthConfigSchema),
    setOAuthConfig(keyValueStoreService),
  );

  router.post(
    '/mongoDBConfig',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(mongoDBConfigSchema),
    checkMongoHealth,
    createMongoDbConfig(keyValueStoreService),
  );

  router.get(
    '/mongoDBConfig',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    getMongoDbConfig(keyValueStoreService),
  );

  router.post(
    '/arangoDBConfig',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(arangoDBConfigSchema),
    checkArangoHealth,
    createArangoDbConfig(keyValueStoreService),
  );

  router.get(
    '/arangoDBConfig',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    getArangoDbConfig(keyValueStoreService),
  );

  // keyValueStore config routes
  /**
   * POST /keyValueStoreConfig
   * Creates or updates key-value store configuration in the key-value store
   * Requires authentication
   * @param {Object} req.body.keyValueStoreConfig - Key-value store configuration object to store
   * @returns {Object} The stored configuration object
   */
  router.post(
    '/redisConfig',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(redisConfigSchema),
    checkRedisHealth,
    createRedisConfig(keyValueStoreService),
  );

  /**
   * GET /keyValueStoreConfig
   * Retrieves the current key-value store configuration from key-value store
   * Requires authentication
   * @returns {Object} The stored configuration object or null if not found
   */
  router.get(
    '/redisConfig',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    getRedisConfig(keyValueStoreService),
  );

  router.post(
    '/qdrantConfig',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(qdrantConfigSchema),
    checkQdrantHealth,
    createQdrantConfig(keyValueStoreService),
  );

  router.get(
    '/qdrantConfig',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    getQdrantConfig(keyValueStoreService),
  );

  // Platform settings
  router.post(
    '/platform/settings',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(platformSettingsSchema),
    setPlatformSettings(keyValueStoreService),
  );

  router.get(
    '/platform/settings',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    getPlatformSettings(keyValueStoreService),
  );

  router.get(
    '/platform/feature-flags/available',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    getAvailablePlatformFeatureFlags(),
  );

  // message broker config routes
  /**
   * POST /messageBrokerConfig
   * Creates or updates message broker configuration in the key-value store
   * Requires authentication
   * @param {Object} req.body.messageBrokerConfig - Message broker configuration object to store
   * @returns {Object} The stored configuration object
   */
  router.post(
    '/kafkaConfig',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(kafkaConfigSchema),
    checkKafkaHealth,
    createKafkaConfig(keyValueStoreService),
  );

  /**
   * GET /messageBrokerConfig
   * Retrieves the current message broker configuration from key-value store
   * Requires authentication
   * @returns {Object} The stored configuration object or null if not found
   */
  router.get(
    '/kafkaConfig',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    getKafkaConfig(keyValueStoreService),
  );

  // Google Workspace Config Routes
  /**
   * POST /googleWorkspaceConfig
   * Creates or updates Google Workspace configuration in the key-value store
   * Requires authentication
   * @param {Object} req.body.googleWorkspaceConfig - Google Workspace configuration object to store
   * @returns {Object} The stored configuration object
   */
  router.post(
    '/connectors/googleWorkspaceCredentials',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ...FileProcessorFactory.createJSONUploadProcessor({
      fieldName: 'googleWorkspaceCredentials',
      allowedMimeTypes: ['application/json'],
      maxFilesAllowed: 1,
      isMultipleFilesAllowed: false,
      processingType: FileProcessingType.JSON,
      maxFileSize: 1024 * 1024 * 5,
      strictFileUpload: false,
    }).getMiddleware,
    (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      if (!req.user) {
        throw new NotFoundError('User not found');
      }
      return createGoogleWorkspaceCredentials(
        keyValueStoreService,
        req.user.userId,
        req.user.orgId,
        syncEventService,
      )(req, res, next);
    },
  );

  router.post(
    '/internal/connectors/googleWorkspaceCredentials',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),

    metricsMiddleware(container),
    ...FileProcessorFactory.createJSONUploadProcessor({
      fieldName: 'googleWorkspaceCredentials',
      allowedMimeTypes: ['application/json'],
      maxFilesAllowed: 1,
      isMultipleFilesAllowed: false,
      processingType: FileProcessingType.JSON,
      maxFileSize: 1024 * 1024 * 5,
      strictFileUpload: false,
    }).getMiddleware,
    (req: AuthenticatedServiceRequest, res: Response, next: NextFunction) => {
      if (!req.tokenPayload) {
        throw new NotFoundError('User not found');
      }
      return createGoogleWorkspaceCredentials(
        keyValueStoreService,
        req.tokenPayload.userId,
        req.tokenPayload.orgId,
        syncEventService,
      )(req, res, next);
    },
  );
  /**
   * GET /googleWorkspaceConfig
   * Retrieves the current Google Workspace configuration from key-value store
   * Requires authentication
   * @returns {Object} The stored configuration object or null if not found
   */
  router.get(
    '/connectors/googleWorkspaceCredentials',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      if (!req.user) {
        throw new NotFoundError('User not found');
      }
      return getGoogleWorkspaceCredentials(
        keyValueStoreService,
        req.user.userId,
        req.user.orgId,
      )(req, res, next);
    },
  );

  router.get(
    '/internal/connectors/individual/googleWorkspaceCredentials',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    metricsMiddleware(container),
    (req: AuthenticatedServiceRequest, res: Response, next: NextFunction) => {
      if (!req.tokenPayload) {
        throw new NotFoundError('User not found');
      }
      return getGoogleWorkspaceCredentials(
        keyValueStoreService,
        req.tokenPayload.userId,
        req.tokenPayload.orgId,
      )(req, res, next);
    },
  );
  router.get(
    '/internal/connectors/business/googleWorkspaceCredentials',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    metricsMiddleware(container),
    (req: AuthenticatedServiceRequest, res: Response, next: NextFunction) => {
      if (!req.tokenPayload) {
        throw new NotFoundError('User not found');
      }
      return getGoogleWorkspaceBusinessCredentials(
        keyValueStoreService,
        req.tokenPayload.orgId,
      )(req, res, next);
    },
  );

  router.delete(
    '/internal/connectors/business/googleWorkspaceCredentials',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    metricsMiddleware(container),
    (req: AuthenticatedServiceRequest, res: Response, next: NextFunction) => {
      if (!req.tokenPayload) {
        throw new NotFoundError('User not found');
      }
      return deleteGoogleWorkspaceCredentials(
        keyValueStoreService,
        req.tokenPayload.orgId,
      )(req, res, next);
    },
  );

  router.get(
    '/connectors/googleWorkspaceOauthConfig',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    getGoogleWorkspaceOauthConfig(keyValueStoreService),
  );

  router.post(
    '/connectors/googleWorkspaceOauthConfig',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(googleWorkspaceConfigSchema),
    (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
      if (!req.user) {
        throw new NotFoundError('User not found');
      }
      return setGoogleWorkspaceOauthConfig(
        keyValueStoreService,
        syncEventService,
        req.user.orgId,
      )(req, res, next);
    },
  );

  router.get(
    '/internal/connectors/googleWorkspaceOauthConfig',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    metricsMiddleware(container),
    getGoogleWorkspaceOauthConfig(keyValueStoreService),
  );

  router.post(
    '/internal/connectors/googleWorkspaceOauthConfig',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    metricsMiddleware(container),
    ValidationMiddleware.validate(googleWorkspaceConfigSchema),
    (req: AuthenticatedServiceRequest, res: Response, next: NextFunction) => {
      if (!req.tokenPayload) {
        throw new NotFoundError('User not found');
      }
      return setGoogleWorkspaceOauthConfig(
        keyValueStoreService,
        syncEventService,
        req.tokenPayload?.orgId,
      )(req, res, next);
    },
  );

  // ai models config routes
  /**
   * POST /aiModelsConfig
   * Creates or updates ai models configuration in the key-value store
   * Requires authentication
   * @param {Object} req.body.aiModelsConfig - Ai models configuration object to store
   * @returns {Object} The stored configuration object
   */
  router.post(
    '/aiModelsConfig',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(aiModelsConfigSchema),
    createAIModelsConfig(keyValueStoreService, entityEventService, appConfig),
  );

  /**
   * GET /aiModelsConfig
   * Retrieves the current ai models configuration from key-value store
   * Requires authentication
   * @returns {Object} The stored configuration object or null if not found
   */
  router.get(
    '/aiModelsConfig',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    getAIModelsConfig(keyValueStoreService),
  );

  router.get(
    '/internal/aiModelsConfig',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    metricsMiddleware(container),
    getAIModelsConfig(keyValueStoreService),
  );

  /**
   * @route GET /api/v1/conversations/ai-models
   * @desc Get all AI models providers (direct Node.js implementation)
   * @access Private
   */
  router.get(
    '/ai-models',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    getAIModelsProviders(keyValueStoreService),
  );

  /**
   * @route GET /api/v1/conversations/ai-models/:modelType
   * @desc Get all available AI models of a specific type
   * @access Private
   * @param {string} modelType - Type of model (llm, embedding, ocr, slm, reasoning, multiModal)
   */

  router.get(
    '/ai-models/:modelType',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(modelTypeSchema),
    getModelsByType(keyValueStoreService),
  );

  /**
   * @route GET /api/v1/conversations/ai-models/available/:modelType
   * @desc Get available models of a specific type in flattened format
   * @access Private
   * @param {string} modelType - Type of model (llm, embedding, ocr, slm, reasoning, multiModal)
   */
  router.get(
    '/ai-models/available/:modelType',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    ValidationMiddleware.validate(modelTypeSchema),
    getAvailableModelsByType(keyValueStoreService),
  );

  /**
   * @route POST /api/v1/conversations/ai-models/providers
   * @desc Add a new AI model provider (direct Node.js implementation)
   * @access Private
   */
  router.post(
    '/ai-models/providers',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(addProviderRequestSchema),
    addAIModelProvider(keyValueStoreService, entityEventService, appConfig),
  );

  /**
   * @route PUT /api/v1/conversations/ai-models/providers/:modelType/:modelKey
   * @desc Update an AI model provider (direct Node.js implementation)
   * @access Private
   * @param {string} modelType - Type of model (llm, embedding, ocr, slm, reasoning, multiModal)
   * @param {string} modelKey - Unique key for the model configuration
   */
  router.put(
    '/ai-models/providers/:modelType/:modelKey',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateProviderRequestSchema),
    updateAIModelProvider(keyValueStoreService, entityEventService, appConfig),
  );

  /**
   * @route DELETE /api/v1/conversations/ai-models/providers/:modelType/:modelKey
   * @desc Delete an AI model provider (direct Node.js implementation)
   * @access Private
   * @param {string} modelType - Type of model (llm, embedding, ocr, slm, reasoning, multiModal)
   * @param {string} modelKey - Unique key for the model configuration
   */
  router.delete(
    '/ai-models/providers/:modelType/:modelKey',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(deleteProviderSchema),
    deleteAIModelProvider(keyValueStoreService, entityEventService, appConfig),
  );

  /**
   * @route PUT /api/v1/conversations/ai-models/default/:modelType/:modelKey
   * @desc Update the default AI model (direct Node.js implementation)
   * @access Private
   * @param {string} modelType - Type of model (llm, embedding, ocr, slm, reasoning, multiModal)
   * @param {string} modelKey - Unique key for the model configuration
   */
  router.put(
    '/ai-models/default/:modelType/:modelKey',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    ValidationMiddleware.validate(updateDefaultModelSchema),
    updateDefaultAIModel(keyValueStoreService, entityEventService, appConfig),
  );

  router.get(
    '/frontendPublicUrl',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    getFrontendUrl(keyValueStoreService),
  );

  router.post(
    '/frontendPublicUrl',
    authMiddleware.authenticate,
    userAdminCheck,
    ValidationMiddleware.validate(urlSchema),
    metricsMiddleware(container),
    setFrontendUrl(
      keyValueStoreService,
      appConfig.scopedJwtSecret,
      configService,
    ),
  );

  router.get(
    '/connectorPublicUrl',
    authMiddleware.authenticate,
    metricsMiddleware(container),
    getConnectorPublicUrl(keyValueStoreService),
  );

  router.post(
    '/connectorPublicUrl',
    authMiddleware.authenticate,
    userAdminCheck,
    ValidationMiddleware.validate(urlSchema),
    metricsMiddleware(container),
    setConnectorPublicUrl(keyValueStoreService, syncEventService),
  );

  // metrics collection routes
  router.put(
    '/metricsCollection/toggle',
    authMiddleware.authenticate,
    userAdminCheck,
    ValidationMiddleware.validate(metricsCollectionToggleSchema),
    metricsMiddleware(container),
    toggleMetricsCollection(keyValueStoreService),
  );

  router.post(
    '/internal/metricsCollection/toggle',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    ValidationMiddleware.validate(metricsCollectionToggleSchema),
    metricsMiddleware(container),
    toggleMetricsCollection(keyValueStoreService),
  );

  router.get(
    '/metricsCollection',
    authMiddleware.authenticate,
    userAdminCheck,
    metricsMiddleware(container),
    getMetricsCollection(keyValueStoreService),
  );

  router.patch(
    '/metricsCollection/pushInterval',
    authMiddleware.authenticate,
    userAdminCheck,
    ValidationMiddleware.validate(metricsCollectionPushIntervalSchema),
    metricsMiddleware(container),
    setMetricsCollectionPushInterval(keyValueStoreService),
  );

  router.patch(
    '/metricsCollection/serverUrl',
    authMiddleware.authenticate,
    userAdminCheck,
    ValidationMiddleware.validate(metricsCollectionRemoteServerSchema),
    metricsMiddleware(container),
    setMetricsCollectionRemoteServer(keyValueStoreService),
  );

  return router;
}
