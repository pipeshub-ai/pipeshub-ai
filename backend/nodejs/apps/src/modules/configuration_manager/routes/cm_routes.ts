import { Router } from 'express';
import { Container } from 'inversify';

import { AuthMiddleware } from '../../../libs/middlewares/auth.middleware';
import { ValidationMiddleware } from '../../../libs/middlewares/validation.middleware';
import { metricsMiddleware } from '../../../libs/middlewares/prometheus.middleware';
import { requireScopes } from '../../../libs/middlewares/require-scopes.middleware';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { TokenScopes } from '../../../libs/enums/token-scopes.enum';
import { OAuthScopeNames } from '../../../libs/enums/oauth-scopes.enum';

import { userAdminCheck } from '../../user_management/middlewares/userAdminCheck';
import { AppConfig } from '../../tokens_manager/config/config';

import { CMContainerToken } from '../container/cm_container';
import {
  getFrontendPublicUrl,
  createSlackBotConfig,
  createSmtpConfig,
  createStorageConfig,
  addAIModelProvider,
  deleteAIModelProvider,
  deleteSlackBotConfig,
  getAIModelsProviders,
  getAvailableModelsByType,
  getAvailablePlatformFeatureFlags,
  getAzureAdAuthConfig,
  getCustomSystemPrompt,
  getGoogleAuthConfig,
  getMetricsCollection,
  getMicrosoftAuthConfig,
  getModelsByType,
  getOAuthConfig,
  getPlatformSettings,
  getSlackBotConfigs,
  getSmtpConfig,
  getSsoAuthConfig,
  getStorageConfig,
  setAzureAdAuthConfig,
  setCustomSystemPrompt,
  setGoogleAuthConfig,
  setMetricsCollectionPushInterval,
  setMetricsCollectionRemoteServer,
  setMicrosoftAuthConfig,
  setOAuthConfig,
  setPlatformSettings,
  setSsoAuthConfig,
  toggleMetricsCollection,
  updateAIModelProvider,
  updateDefaultAIModel,
  updateSlackBotConfig,
} from '../controller/cm_controller';
import {
  addProviderRequestSchema,
  azureAdConfigSchema,
  createSlackBotConfigSchema,
  customSystemPromptSchema,
  deleteProviderSchema,
  deleteSlackBotConfigSchema,
  googleAuthConfigSchema,
  metricsCollectionPushIntervalSchema,
  metricsCollectionRemoteServerSchema,
  metricsCollectionToggleSchema,
  microsoftAuthConfigSchema,
  modelTypeSchema,
  oauthConfigSchema,
  platformSettingsSchema,
  smtpConfigSchema,
  ssoConfigSchema,
  storageValidationSchema,
  updateDefaultModelSchema,
  updateProviderRequestSchema,
  updateSlackBotConfigSchema,
} from '../validator/validators';
import { SamlController } from '../../auth/controller/saml.controller';

type MiddlewareStack = ReturnType<typeof metricsMiddleware>[];

/**
 * Admin-only write stack:
 * authenticate -> CONFIG_WRITE scope -> admin check -> metrics
 */
const adminWrite = (
  auth: AuthMiddleware,
  container: Container,
): MiddlewareStack => [
  auth.authenticate,
  requireScopes(OAuthScopeNames.CONFIG_WRITE),
  userAdminCheck,
  metricsMiddleware(container),
];

/**
 * Admin-only read stack:
 * authenticate -> CONFIG_READ scope -> admin check -> metrics
 */
const adminRead = (
  auth: AuthMiddleware,
  container: Container,
): MiddlewareStack => [
  auth.authenticate,
  requireScopes(OAuthScopeNames.CONFIG_READ),
  userAdminCheck,
  metricsMiddleware(container),
];

/**
 * Authenticated read stack (no admin check).
 */
const authenticatedRead = (
  auth: AuthMiddleware,
  container: Container,
): MiddlewareStack => [
  auth.authenticate,
  requireScopes(OAuthScopeNames.CONFIG_READ),
  metricsMiddleware(container),
];

/**
 * Internal read stack via scoped token.
 */
const internalRead = (
  auth: AuthMiddleware,
  scope: TokenScopes,
  container: Container,
): MiddlewareStack => [auth.scopedTokenValidator(scope), metricsMiddleware(container)];

export function createConfigurationManagerRouter(container: Container): Router {
  const router = Router();

  const keyValueStoreService = container.get<KeyValueStoreService>(
    CMContainerToken.KeyValueStoreService,
  );
  const appConfig = container.get<AppConfig>(CMContainerToken.AppConfig);

  const authMiddleware = container.get<AuthMiddleware>(CMContainerToken.AuthMiddleware);
  const samlController = container.get<SamlController>(CMContainerToken.SamlController);
  // ==========================================================================
  // Storage
  // ==========================================================================
  router.post(
    '/storageConfig',
    ...adminWrite(authMiddleware, container),
    ValidationMiddleware.validate(storageValidationSchema),
    createStorageConfig(keyValueStoreService, appConfig.storage),
  );
  router.get(
    '/storageConfig',
    ...adminRead(authMiddleware, container),
    getStorageConfig(keyValueStoreService),
  );
  router.get(
    '/internal/storageConfig',
    ...internalRead(authMiddleware, TokenScopes.STORAGE_TOKEN, container),
    getStorageConfig(keyValueStoreService),
  );

  // ==========================================================================
  // SMTP
  // ==========================================================================
  router.post(
    '/smtpConfig',
    ...adminWrite(authMiddleware, container),
    ValidationMiddleware.validate(smtpConfigSchema),
    createSmtpConfig(
      keyValueStoreService,
      appConfig.communicationBackend,
      appConfig.scopedJwtSecret,
    ),
  );
  router.get(
    '/smtpConfig',
    ...adminRead(authMiddleware, container),
    getSmtpConfig(keyValueStoreService),
  );

  // ==========================================================================
  // Auth Configs
  // ==========================================================================
  router.get(
    '/authConfig/azureAd',
    ...adminRead(authMiddleware, container),
    getAzureAdAuthConfig(keyValueStoreService),
  );
  router.get(
    '/internal/authConfig/azureAd',
    ...internalRead(authMiddleware, TokenScopes.FETCH_CONFIG, container),
    getAzureAdAuthConfig(keyValueStoreService),
  );
  router.post(
    '/authConfig/azureAd',
    ...adminWrite(authMiddleware, container),
    ValidationMiddleware.validate(azureAdConfigSchema),
    setAzureAdAuthConfig(keyValueStoreService),
  );

  router.get(
    '/authConfig/microsoft',
    ...adminRead(authMiddleware, container),
    getMicrosoftAuthConfig(keyValueStoreService),
  );
  router.get(
    '/internal/authConfig/microsoft',
    ...internalRead(authMiddleware, TokenScopes.FETCH_CONFIG, container),
    getMicrosoftAuthConfig(keyValueStoreService),
  );
  router.post(
    '/authConfig/microsoft',
    ...adminWrite(authMiddleware, container),
    ValidationMiddleware.validate(microsoftAuthConfigSchema),
    setMicrosoftAuthConfig(keyValueStoreService),
  );

  router.get(
    '/authConfig/google',
    ...adminRead(authMiddleware, container),
    getGoogleAuthConfig(keyValueStoreService),
  );
  router.get(
    '/internal/authConfig/google',
    ...internalRead(authMiddleware, TokenScopes.FETCH_CONFIG, container),
    getGoogleAuthConfig(keyValueStoreService),
  );
  router.post(
    '/authConfig/google',
    ...adminWrite(authMiddleware, container),
    ValidationMiddleware.validate(googleAuthConfigSchema),
    setGoogleAuthConfig(keyValueStoreService),
  );

  router.get(
    '/authConfig/sso',
    ...adminRead(authMiddleware, container),
    getSsoAuthConfig(keyValueStoreService),
  );
  router.get(
    '/internal/authConfig/sso',
    ...internalRead(authMiddleware, TokenScopes.FETCH_CONFIG, container),
    getSsoAuthConfig(keyValueStoreService),
  );
  router.post(
    '/authConfig/sso',
    ...adminWrite(authMiddleware, container),
    ValidationMiddleware.validate(ssoConfigSchema),
    setSsoAuthConfig(keyValueStoreService, samlController),
  );

  router.get(
    '/authConfig/oauth',
    ...adminRead(authMiddleware, container),
    getOAuthConfig(keyValueStoreService),
  );
  router.get(
    '/internal/authConfig/oauth',
    ...internalRead(authMiddleware, TokenScopes.FETCH_CONFIG, container),
    getOAuthConfig(keyValueStoreService),
  );
  router.post(
    '/authConfig/oauth',
    ...adminWrite(authMiddleware, container),
    ValidationMiddleware.validate(oauthConfigSchema),
    setOAuthConfig(keyValueStoreService),
  );

  router.get(
    '/frontendPublicUrl',
    ...authenticatedRead(authMiddleware, container),
    getFrontendPublicUrl(keyValueStoreService),
  );

  // ==========================================================================
  // Platform
  // ==========================================================================
  router.post(
    '/platform/settings',
    ...adminWrite(authMiddleware, container),
    ValidationMiddleware.validate(platformSettingsSchema),
    setPlatformSettings(keyValueStoreService),
  );
  router.get(
    '/platform/settings',
    ...adminRead(authMiddleware, container),
    getPlatformSettings(keyValueStoreService),
  );
  router.get(
    '/platform/feature-flags/available',
    ...adminRead(authMiddleware, container),
    getAvailablePlatformFeatureFlags(),
  );

  // ==========================================================================
  // Slack Bot
  // ==========================================================================
  router.get(
    '/slack-bot',
    ...adminRead(authMiddleware, container),
    getSlackBotConfigs(keyValueStoreService),
  );
  router.get(
    '/internal/slack-bot',
    ...internalRead(authMiddleware, TokenScopes.FETCH_CONFIG, container),
    getSlackBotConfigs(keyValueStoreService),
  );
  router.post(
    '/slack-bot',
    ...adminWrite(authMiddleware, container),
    ValidationMiddleware.validate(createSlackBotConfigSchema),
    createSlackBotConfig(keyValueStoreService),
  );
  router.put(
    '/slack-bot/:configId',
    ...adminWrite(authMiddleware, container),
    ValidationMiddleware.validate(updateSlackBotConfigSchema),
    updateSlackBotConfig(keyValueStoreService),
  );
  router.delete(
    '/slack-bot/:configId',
    ...adminWrite(authMiddleware, container),
    ValidationMiddleware.validate(deleteSlackBotConfigSchema),
    deleteSlackBotConfig(keyValueStoreService),
  );

  // ==========================================================================
  // Custom System Prompt
  // ==========================================================================
  router.get(
    '/prompts/system',
    ...adminRead(authMiddleware, container),
    getCustomSystemPrompt(keyValueStoreService),
  );
  router.put(
    '/prompts/system',
    ...adminWrite(authMiddleware, container),
    ValidationMiddleware.validate(customSystemPromptSchema),
    setCustomSystemPrompt(keyValueStoreService),
  );

  // ==========================================================================
  // AI Models - Provider Management
  // Route ordering:
  // - Specific paths before /:modelType
  // - Public/internal variants are kept adjacent
  // ==========================================================================
  router.get(
    '/ai-models',
    ...adminRead(authMiddleware, container),
    getAIModelsProviders(keyValueStoreService),
  );
  router.get(
    '/ai-models/available/:modelType',
    ...authenticatedRead(authMiddleware, container),
    ValidationMiddleware.validate(modelTypeSchema),
    getAvailableModelsByType(keyValueStoreService),
  );
  router.post(
    '/ai-models/providers',
    ...adminWrite(authMiddleware, container),
    ValidationMiddleware.validate(addProviderRequestSchema),
    addAIModelProvider(keyValueStoreService, appConfig),
  );
  router.put(
    '/ai-models/providers/:modelType/:modelKey',
    ...adminWrite(authMiddleware, container),
    ValidationMiddleware.validate(updateProviderRequestSchema),
    updateAIModelProvider(keyValueStoreService, appConfig),
  );
  router.delete(
    '/ai-models/providers/:modelType/:modelKey',
    ...adminWrite(authMiddleware, container),
    ValidationMiddleware.validate(deleteProviderSchema),
    deleteAIModelProvider(keyValueStoreService),
  );
  router.put(
    '/ai-models/default/:modelType/:modelKey',
    ...adminWrite(authMiddleware, container),
    ValidationMiddleware.validate(updateDefaultModelSchema),
    updateDefaultAIModel(keyValueStoreService),
  );
  router.get(
    '/ai-models/:modelType',
    ...adminRead(authMiddleware, container),
    ValidationMiddleware.validate(modelTypeSchema),
    getModelsByType(keyValueStoreService),
  );


  // ==========================================================================
  // Metrics Collection
  // ==========================================================================
  router.put(
    '/metricsCollection/toggle',
    ...adminWrite(authMiddleware, container),
    ValidationMiddleware.validate(metricsCollectionToggleSchema),
    toggleMetricsCollection(keyValueStoreService),
  );
  router.post(
    '/internal/metricsCollection/toggle',
    ...internalRead(authMiddleware, TokenScopes.FETCH_CONFIG, container),
    ValidationMiddleware.validate(metricsCollectionToggleSchema),
    toggleMetricsCollection(keyValueStoreService),
  );
  router.get(
    '/metricsCollection',
    ...adminRead(authMiddleware, container),
    getMetricsCollection(keyValueStoreService),
  );
  router.patch(
    '/metricsCollection/pushInterval',
    ...adminWrite(authMiddleware, container),
    ValidationMiddleware.validate(metricsCollectionPushIntervalSchema),
    setMetricsCollectionPushInterval(keyValueStoreService),
  );
  router.patch(
    '/metricsCollection/serverUrl',
    ...adminWrite(authMiddleware, container),
    ValidationMiddleware.validate(metricsCollectionRemoteServerSchema),
    setMetricsCollectionRemoteServer(keyValueStoreService),
  );

  return router;
}
