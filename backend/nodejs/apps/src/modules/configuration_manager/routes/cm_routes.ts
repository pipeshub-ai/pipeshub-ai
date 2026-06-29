import { Router, Response, NextFunction } from 'express';
import { Container } from 'inversify';
import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import {
  createAIModelsConfig,
  createGoogleWorkspaceCredentials,
  createSmtpConfig,
  createStorageConfig,
  getAIModelsConfig,
  getAzureAdAuthConfig,
  getGoogleAuthConfig,
  getGoogleWorkspaceOauthConfig,
  getGoogleWorkspaceCredentials,
  getMicrosoftAuthConfig,
  getOAuthConfig,
  getSmtpConfig,
  getSsoAuthConfig,
  getStorageConfig,
  setAzureAdAuthConfig,
  setGoogleAuthConfig,
  setMicrosoftAuthConfig,
  setOAuthConfig,
  setSsoAuthConfig,
  setGoogleWorkspaceOauthConfig,
  deleteGoogleWorkspaceCredentials,
  getGoogleWorkspaceBusinessCredentials,
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
  getCustomSystemPrompt,
  setCustomSystemPrompt,
  getWebSearchProviders,
  updateWebSearchSettings,
  addWebSearchProvider,
  updateWebSearchProvider,
  deleteWebSearchProvider,
  updateDefaultWebSearchProvider,
  getSlackBotConfigs,
  createSlackBotConfig,
  updateSlackBotConfig,
  deleteSlackBotConfig,
  getAIModelRegistry,
  getAIModelRegistryCapabilities,
  getAIModelProviderSchema,
  getModelRoles,
  updateModelRoles,
} from '../controller/cm_controller';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import {
  smtpConfigSchema,
  aiModelsConfigSchema,
  storageValidationSchema,
  azureAdConfigSchema,
  googleAuthConfigSchema,
  oauthConfigSchema,
  ssoConfigSchema,
  googleWorkspaceConfigSchema,
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
  addWebSearchProviderSchema,
  updateWebSearchSettingsSchema,
  updateWebSearchProviderSchema,
  deleteWebSearchProviderSchema,
  updateDefaultWebSearchProviderSchema,
  createSlackBotConfigSchema,
  updateSlackBotConfigSchema,
  deleteSlackBotConfigSchema,
} from '../validator/validators';
import { FileProcessorFactory } from '../../../libs/middlewares/file_processor/fp.factory';
import { FileProcessingType } from '../../../libs/middlewares/file_processor/fp.constant';

import { userAdminCheck } from '../../user_management/middlewares/userAdminCheck';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';
import { AppConfig } from '../../tokens_manager/config/config';
import {
  AuthenticatedUserRequest,
  AuthenticatedServiceRequest,
} from '../../../libs/middlewares/types';
import { NotFoundError } from '../../../libs/errors/http.errors';
import { ConfigService } from '../services/updateConfig.service';
import {
  AiConfigEventProducer,
  SyncEventProducer,
} from '../services/kafka_events.service';
import { SamlController } from '../../auth/controller/saml.controller';

export function createConfigurationManagerRouter(container: Container): Router {
  const router = Router();
  const keyValueStoreService = container.get<KeyValueStoreService>(
    'KeyValueStoreService',
  );
  const appConfig = container.get<AppConfig>('AppConfig');
  const syncEventService =
    container.get<SyncEventProducer>('SyncEventProducer');
  const aiConfigEventService = container.get<AiConfigEventProducer>(
    'AiConfigEventProducer',
  );
  const samlController = container.get<SamlController>('SamlController');
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
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
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
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    getStorageConfig(keyValueStoreService),
  );

  router.get(
    '/internal/storageConfig',
    authMiddleware.scopedTokenValidator(TokenScopes.STORAGE_TOKEN),
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
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
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
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
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
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
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
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
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
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
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
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
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
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
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
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    getSmtpConfig(keyValueStoreService),
  );

  // auth config routes
  router.get(
    '/authConfig/azureAd',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    getAzureAdAuthConfig(keyValueStoreService),
  );

  router.get(
    '/internal/authConfig/azureAd',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    getAzureAdAuthConfig(keyValueStoreService),
  );

  router.post(
    '/authConfig/azureAd',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
    ValidationMiddleware.validate(azureAdConfigSchema),
    setAzureAdAuthConfig(keyValueStoreService),
  );

  router.get(
    '/authConfig/microsoft',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    getMicrosoftAuthConfig(keyValueStoreService),
  );
  router.get(
    '/internal/authConfig/microsoft',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    getMicrosoftAuthConfig(keyValueStoreService),
  );

  router.post(
    '/authConfig/microsoft',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
    ValidationMiddleware.validate(azureAdConfigSchema),
    setMicrosoftAuthConfig(keyValueStoreService),
  );

  router.get(
    '/authConfig/google',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    getGoogleAuthConfig(keyValueStoreService),
  );

  router.get(
    '/internal/authConfig/google',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    getGoogleAuthConfig(keyValueStoreService),
  );
  router.post(
    '/authConfig/google',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
    ValidationMiddleware.validate(googleAuthConfigSchema),
    setGoogleAuthConfig(keyValueStoreService),
  );

  router.get(
    '/authConfig/sso',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    getSsoAuthConfig(keyValueStoreService),
  );
  router.get(
    '/internal/authConfig/sso',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    getSsoAuthConfig(keyValueStoreService),
  );
  router.post(
    '/authConfig/sso',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
    ValidationMiddleware.validate(ssoConfigSchema),
    setSsoAuthConfig(keyValueStoreService, samlController),
  );

  // OAuth config routes
  router.get(
    '/authConfig/oauth',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    getOAuthConfig(keyValueStoreService),
  );

  router.get(
    '/internal/authConfig/oauth',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    getOAuthConfig(keyValueStoreService),
  );

  router.post(
    '/authConfig/oauth',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
    ValidationMiddleware.validate(oauthConfigSchema),
    setOAuthConfig(keyValueStoreService),
  );

  // Platform settings
  router.post(
    '/platform/settings',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
    ValidationMiddleware.validate(platformSettingsSchema),
    setPlatformSettings(keyValueStoreService),
  );

  router.get(
    '/platform/settings',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    getPlatformSettings(keyValueStoreService),
  );

  router.get(
    '/platform/feature-flags/available',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    getAvailablePlatformFeatureFlags(),
  );

  // Slack Bot configuration
  router.get(
    '/slack-bot',
    authMiddleware.authenticate,
    userAdminCheck,
    getSlackBotConfigs(keyValueStoreService),
  );
  router.get(
    '/internal/slack-bot',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    getSlackBotConfigs(keyValueStoreService),
  );

  router.post(
    '/slack-bot',
    authMiddleware.authenticate,
    userAdminCheck,
    ValidationMiddleware.validate(createSlackBotConfigSchema),
    createSlackBotConfig(keyValueStoreService),
  );

  router.put(
    '/slack-bot/:configId',
    authMiddleware.authenticate,
    userAdminCheck,
    ValidationMiddleware.validate(updateSlackBotConfigSchema),
    updateSlackBotConfig(keyValueStoreService),
  );

  router.delete(
    '/slack-bot/:configId',
    authMiddleware.authenticate,
    userAdminCheck,
    ValidationMiddleware.validate(deleteSlackBotConfigSchema),
    deleteSlackBotConfig(keyValueStoreService),
  );

  // Custom System Prompt routes
  router.get(
    '/prompts/system',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    getCustomSystemPrompt(keyValueStoreService),
  );

  router.put(
    '/prompts/system',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
    setCustomSystemPrompt(keyValueStoreService),
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
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
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
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
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
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    getGoogleWorkspaceOauthConfig(keyValueStoreService),
  );

  router.post(
    '/connectors/googleWorkspaceOauthConfig',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
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
    getGoogleWorkspaceOauthConfig(keyValueStoreService),
  );

  router.post(
    '/internal/connectors/googleWorkspaceOauthConfig',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
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
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
    ValidationMiddleware.validate(aiModelsConfigSchema),
    createAIModelsConfig(keyValueStoreService, aiConfigEventService, appConfig),
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
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    getAIModelsConfig(keyValueStoreService),
  );

  router.get(
    '/internal/aiModelsConfig',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    getAIModelsConfig(keyValueStoreService, false),
  );

  /**
   * @route GET /api/v1/configurationManager/ai-models/registry
   * @desc Get all registered AI model providers from the Python backend registry
   * @access Private (admin)
   */
  router.get(
    '/ai-models/registry/capabilities',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    getAIModelRegistryCapabilities(appConfig),
  );

  router.get(
    '/ai-models/registry/:providerId/schema',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    getAIModelProviderSchema(appConfig),
  );

  router.get(
    '/ai-models/registry',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    getAIModelRegistry(appConfig),
  );

  /**
   * @route GET /api/v1/configurationManager/ai-models
   * @desc Get all AI models providers
   * @access Private (admin)
   */
  router.get(
    '/ai-models',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    getAIModelsProviders(keyValueStoreService),
  );

  /**
   * @route GET /api/v1/configurationManager/ai-models/roles
   * @desc Get all model role assignments (e.g. indexing role)
   * @access Private (admin)
   * NOTE: Must be registered before /ai-models/:modelType to avoid Express
   * matching "roles" as the :modelType param.
   */
  router.get(
    '/ai-models/roles',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    getModelRoles(keyValueStoreService),
  );

  /**
   * @route PUT /api/v1/configurationManager/ai-models/roles
   * @desc Assign models to named roles (e.g. indexing)
   * @access Private (admin)
   * @body { roles: Record<string, { modelType: string; modelKey: string }> }
   * NOTE: Must be registered before /ai-models/default/:modelType/:modelKey.
   */
  router.put(
    '/ai-models/roles',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
    updateModelRoles(keyValueStoreService),
  );

  /**
   * @route GET /api/v1/configurationManager/ai-models/:modelType
   * @desc Get all AI models of a specific type
   * @access Private (admin)
   * @param {string} modelType - Type of model (llm, embedding, ocr, slm, reasoning, multiModal)
   */

  router.get(
    '/ai-models/:modelType',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    ValidationMiddleware.validate(modelTypeSchema),
    getModelsByType(keyValueStoreService),
  );

  /**
   * @route GET /api/v1/configurationManager/ai-models/available/:modelType
   * @desc Get available models of a specific type in flattened format
   * @access Private
   * @param {string} modelType - Type of model (llm, embedding, ocr, slm, reasoning, multiModal)
   */
  router.get(
    '/ai-models/available/:modelType',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
    ValidationMiddleware.validate(modelTypeSchema),
    getAvailableModelsByType(keyValueStoreService),
  );

  /**
   * @route POST /api/v1/configurationManager/ai-models/providers
   * @desc Add a new AI model provider
   * @access Private (admin)
   */
  router.post(
    '/ai-models/providers',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
    ValidationMiddleware.validate(addProviderRequestSchema),
    addAIModelProvider(keyValueStoreService, aiConfigEventService, appConfig),
  );

  /**
   * @route PUT /api/v1/configurationManager/ai-models/providers/:modelType/:modelKey
   * @desc Update an AI model provider
   * @access Private (admin)
   * @param {string} modelType - Type of model (llm, embedding, ocr, slm, reasoning, multiModal)
   * @param {string} modelKey - Unique key for the model configuration
   */
  router.put(
    '/ai-models/providers/:modelType/:modelKey',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
    ValidationMiddleware.validate(updateProviderRequestSchema),
    updateAIModelProvider(keyValueStoreService, aiConfigEventService, appConfig),
  );

  /**
   * @route DELETE /api/v1/configurationManager/ai-models/providers/:modelType/:modelKey
   * @desc Delete an AI model provider
   * @access Private (admin)
   * @param {string} modelType - Type of model (llm, embedding, ocr, slm, reasoning, multiModal)
   * @param {string} modelKey - Unique key for the model configuration
   */
  router.delete(
    '/ai-models/providers/:modelType/:modelKey',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
    ValidationMiddleware.validate(deleteProviderSchema),
    deleteAIModelProvider(keyValueStoreService, aiConfigEventService, appConfig),
  );

  /**
   * @route PUT /api/v1/configurationManager/ai-models/default/:modelType/:modelKey
   * @desc Update the default AI model
   * @access Private (admin)
   * @param {string} modelType - Type of model (llm, embedding, ocr, slm, reasoning, multiModal)
   * @param {string} modelKey - Unique key for the model configuration
   */
  router.put(
    '/ai-models/default/:modelType/:modelKey',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
    ValidationMiddleware.validate(updateDefaultModelSchema),
    updateDefaultAIModel(keyValueStoreService, aiConfigEventService, appConfig),
  );

  // Web Search provider routes
  /**
   * @route GET /api/v1/configurationManager/web-search
   * @desc Get all web search providers
   * @access Private
   */
  router.get(
    '/web-search',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
    getWebSearchProviders(keyValueStoreService),
  );

  /**
   * @route PUT /api/v1/configurationManager/web-search/settings
   * @desc Update web search settings
   * @access Private
   */
  router.put(
    '/web-search/settings',
    authMiddleware.authenticate,
    userAdminCheck,
    ValidationMiddleware.validate(updateWebSearchSettingsSchema),
    updateWebSearchSettings(keyValueStoreService),
  );

  /**
   * @route POST /api/v1/configurationManager/web-search/providers
   * @desc Add a new web search provider
   * @access Private
   */
  router.post(
    '/web-search/providers',
    authMiddleware.authenticate,
    userAdminCheck,
    ValidationMiddleware.validate(addWebSearchProviderSchema),
    addWebSearchProvider(keyValueStoreService, appConfig),
  );

  /**
   * @route PUT /api/v1/configurationManager/web-search/providers/:providerKey
   * @desc Update a web search provider
   * @access Private
   * @param {string} providerKey - Unique key for the provider configuration
   */
  router.put(
    '/web-search/providers/:providerKey',
    authMiddleware.authenticate,
    userAdminCheck,
    ValidationMiddleware.validate(updateWebSearchProviderSchema),
    updateWebSearchProvider(keyValueStoreService, appConfig),
  );

  /**
   * @route DELETE /api/v1/configurationManager/web-search/providers/:providerKey
   * @desc Delete a web search provider
   * @access Private
   * @param {string} providerKey - Unique key for the provider configuration
   */
  router.delete(
    '/web-search/providers/:providerKey',
    authMiddleware.authenticate,
    userAdminCheck,
    ValidationMiddleware.validate(deleteWebSearchProviderSchema),
    deleteWebSearchProvider(keyValueStoreService, appConfig),
  );

  /**
   * @route PUT /api/v1/configurationManager/web-search/default/:providerKey
   * @desc Update the default web search provider
   * @access Private
   * @param {string} providerKey - Unique key for the provider configuration
   */
  router.put(
    '/web-search/default/:providerKey',
    authMiddleware.authenticate,
    userAdminCheck,
    ValidationMiddleware.validate(updateDefaultWebSearchProviderSchema),
    updateDefaultWebSearchProvider(keyValueStoreService, appConfig),
  );

  router.get(
    '/frontendPublicUrl',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
    getFrontendUrl(keyValueStoreService),
  );

  router.post(
    '/frontendPublicUrl',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
    ValidationMiddleware.validate(urlSchema),
    setFrontendUrl(
      keyValueStoreService,
      appConfig.scopedJwtSecret,
      configService,
    ),
  );

  router.get(
    '/connectorPublicUrl',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
    getConnectorPublicUrl(keyValueStoreService),
  );

  router.post(
    '/connectorPublicUrl',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
    ValidationMiddleware.validate(urlSchema),
    setConnectorPublicUrl(keyValueStoreService, syncEventService),
  );

  // metrics collection routes
  router.put(
    '/metricsCollection/toggle',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
    ValidationMiddleware.validate(metricsCollectionToggleSchema),
    toggleMetricsCollection(keyValueStoreService),
  );

  router.post(
    '/internal/metricsCollection/toggle',
    authMiddleware.scopedTokenValidator(TokenScopes.FETCH_CONFIG),
    ValidationMiddleware.validate(metricsCollectionToggleSchema),
    toggleMetricsCollection(keyValueStoreService),
  );

  router.get(
    '/metricsCollection',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_READ),
    userAdminCheck,
    getMetricsCollection(keyValueStoreService),
  );

  router.patch(
    '/metricsCollection/pushInterval',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
    ValidationMiddleware.validate(metricsCollectionPushIntervalSchema),
    setMetricsCollectionPushInterval(keyValueStoreService),
  );

  router.patch(
    '/metricsCollection/serverUrl',
    authMiddleware.authenticate,
    requireScopes(OAuthScopeNames.CONFIG_WRITE),
    userAdminCheck,
    ValidationMiddleware.validate(metricsCollectionRemoteServerSchema),
    setMetricsCollectionRemoteServer(keyValueStoreService),
  );

  return router;
}
