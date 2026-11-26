import { randomUUID } from 'crypto';
import { Request, Response, NextFunction } from 'express';
import {
  AuthenticatedServiceRequest,
  AuthenticatedUserRequest,
} from '../../../libs/middlewares/types';
import {
  observeTokenRestriction,
  tokenRestrictionsEnforced,
} from '../../../libs/utils/token-restrictions.utils';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { Logger } from '../../../libs/services/logger.service';
import { parseKvObject } from '../../../libs/utils/kv-object.utils';
import { configPaths } from '../paths/paths';
import {
  BadRequestError,
  ConflictError,
  ForbiddenError,
  InternalServerError,
  NotFoundError,
  ServiceUnavailableError,
  UnauthorizedError,
} from '../../../libs/errors/http.errors';
import { handleBackendError, safeCause } from '../../../libs/errors/backend-error';
import { withCause } from '../../../libs/errors/base.error';
import { markClientSafe } from '../../../libs/errors/reader-friendly';
import {
  googleWorkspaceBusinessCredentialsSchema,
  googleWorkspaceIndividualCredentialsSchema,
} from '../validator/validators';
import { HTTP_STATUS } from '../../../libs/enums/http-status.enum';
import {
  aiModelRoute,
  AI_MODEL_TYPES,
  AIServiceResponse,
  googleWorkspaceTypes,
  isAIModelType,
  storageTypes,
} from '../constants/constants';
import { EncryptionService } from '../../../libs/encryptor/encryptor';
import { RENAMED_SIGN_IN_METHODS } from '../../../edition';
import { setMetricCollectionEnabled } from '../../../libs/services/telemetry/modules/collection-metrics';
import { normalizeOrgId } from '../../../libs/services/telemetry/identity';
import { TelemetryService } from '../../../libs/services/telemetry/telemetry.service';
import { loadConfigurationManagerConfig } from '../config/config';
import {
  readMembership,
  savePutsArtifactReadsAtRisk,
} from '../utils/artifactRollout';
import { findActiveOrgById } from '../../user_management/utils/org.utils';
import { createHarnessRollout } from '../../agent-harness/composition';
import { HarnessRolloutPolicy } from '../../agent-harness/ports/harness-rollout';
import {
  agentHarnessSwitchedOn,
  harnessReconcileEnabled,
  isAgentHarnessInfrastructureReady,
  orgHarnessTurnedOn,
} from '../../agent-harness/config';
import { engineRunsServed } from '../../../libs/repositories/agent-harness/engine-runs-probe';
import { EnvFlagEnvironment } from '../../../libs/utils/env-flags';

import {
  DefaultStorageConfig,
  resolveFrontendPublicUrl,
} from '../../tokens_manager/services/cm.service';
import { AppConfig } from '../../tokens_manager/config/config';
import { generateFetchConfigAuthToken } from '../../auth/utils/generateAuthToken';
import axios from 'axios';
import { ARANGO_DB_NAME, MONGO_DB_NAME } from '../../../libs/enums/db.enum';
import { ConfigService } from '../services/updateConfig.service';
import {
  AiConfigEventProducer,
  ConnectorPublicUrlChangedEvent,
  EmbeddingModelConfiguredEvent,
  EntitiesEventProducer,
  Event,
  EventType,
  GmailUpdatesDisabledEvent,
  GmailUpdatesEnabledEvent,
  LLMConfiguredEvent,
  SyncEventProducer,
} from '../services/kafka_events.service';
import {
  AICommandOptions,
  AIServiceCommand,
} from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import {
  actorRefOfUser,
  controlPlaneHttpError,
  ControlPlaneTransaction,
  orgScope,
} from '../../control-plane';
import { canonicalDigest } from '../../agent-harness/contracts/canonical-json';
import {
  aiModelsRepository,
  plainAiModelsSave,
  type AiModelsSave,
} from '../utils/ai-models-save';
import {
  AGENT_HARNESS_LABS_FLAGS,
  PLATFORM_FEATURE_FLAGS,
} from '../constants/constants';
import { DURABLE_AGENT_FLAG } from '../utils/flag-overrides';
import { REASONING_EFFORT_VALUES } from '../../enterprise_search/constants/constants';
import {
  getPlatformSettingsFromStore,
  LABS_CHANGED,
  PlatformSettingsChanged,
  type PlatformSettingsSave,
  readStoredAiModelsConfig,
  savePlatformSettings,
} from '../utils/util';
import { AIModelConfiguration, AIModelsConfig, SystemPromptsConfig } from '../types/ai-models.types';
import {
  AiModelsConfigRepository,
  AiModelsWriteAborted,
  ensureAiModelBuckets,
  findModelEntry,
} from '../services/aiModelsConfig.repository';
import { WebSearchConfig } from '../types/web-search.types';
import {
  WebSearchProviderConfiguration,
  canSearch,
} from '../types/web-search.types';
import { getCurrentOrgId } from '../../../libs/context/orgContext';
import { localStorageReplicaProblem } from '../../storage/utils/replicas';
import {
  CONFIG_SECRET_PLACEHOLDER,
  SMTP_SECRET_KEYS,
  maskSmtpConfig,
  mergeSmtpConfigPlaceholders,
  smtpHostMovedWithSavedLogin,
  webSearchBaseUrlMovedWithSavedKey,
  CREDENTIAL_REENTRY_REQUIRED,
  stripAiModelsStoredConfig,
  stripAiModelSecrets,
  mergeAiModelCredentials,
  AI_MODEL_DESTINATION_KEYS,
  aiModelDestinationChanged,
  aiModelDestinationsDiffer,
  hasForwardingCredentials,
  reusesSavedAiModelCredentials,
  withoutForwardingCredentials,
  maskWebSearchProvider,
  maskWebSearchProviderForMember,
  mergeWebSearchProviderPlaceholders,
  maskSlackBotConfig,
  mergeSlackBotConfigPlaceholders,
  GOOGLE_AUTH_SECRET_KEYS,
  MICROSOFT_AUTH_SECRET_KEYS,
  maskGoogleAuthConfig,
  maskMicrosoftAuthConfig,
  restoreSecretPlaceholders,
} from '../utils/maskConfigSecrets';
import {
  canRevealSecrets,
  isSecretRevealAvailable,
} from '../utils/secretReveal';
import { isUserOrgAdmin } from '../../user_management/services/user-admin.service';
import {
  SMTP_SETTINGS_PROBLEM_MESSAGES,
  smtpSettingsProblem,
} from '../../mail/utils/smtp-transport';
import { SmtpConfig } from '../../mail/middlewares/types';
import {
  buildS3HealthCheckErrorMessage,
  validateS3Capabilities,
} from '../../storage/utils/s3-health-check.util';
import {
  resolveS3Credentials,
  S3_PARTIAL_CREDENTIALS_MESSAGE,
} from '../../storage/utils/s3-credentials.util';
import { modelDisplayName } from '../utils/model-display-name';
import { currentOrgContext } from '../../../libs/context/orgContext';

const logger = Logger.getInstance({
  service: 'ConfigurationManagerController',
});
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


/**
 * Returns true when the HIDE_SECRET_CONFIG env var is set to "true".
 * With TOKEN_RESTRICTIONS_MODE=enforce, an OAuth or personal access token caller never gets them.
 */
function shouldHideSecrets(req: Request): boolean {
  const hidden = process.env.HIDE_SECRET_CONFIG === 'true';
  if (!hidden && (req as AuthenticatedUserRequest).user?.isOAuth === true) {
    if (tokenRestrictionsEnforced()) return true;
    observeTokenRestriction(req as never, 'token_caller_unmasked');
  }
  return hidden;
}

async function requesterIsOrgAdmin(
  req: AuthenticatedUserRequest,
): Promise<boolean> {
  const userId: unknown = req.user?.userId;
  const orgId: unknown = req.user?.orgId;
  if (typeof userId !== 'string' || typeof orgId !== 'string') {
    return false;
  }
  return isUserOrgAdmin(userId, orgId);
}

/** Tells the settings UI whether to offer its "show secrets" button; carries no secret itself. */
export const getSecretRevealStatus = (_req: Request, res: Response): void => {
  res.status(200).json({ available: isSecretRevealAvailable() }).end();
};

const DEFAULT_WEB_SEARCH_SETTINGS = Object.freeze({
  includeImages: false,
  maxImages: 3,
});

// The id the settings UI sends to choose DuckDuckGo: it needs no configuration, but it is stored
// like any provider, so an org that never chose it has no search provider at all.
const DUCKDUCKGO_PROVIDER = 'duckduckgo';

// Health-check refusals that do not stop an admin choosing DuckDuckGo (`errorType` from the AI service).
const WEB_SEARCH_PASSING_REFUSALS: ReadonlySet<unknown> = new Set([
  'blocked',
  'rate_limited',
]);

// Whether web search is set up: a provider that can search is marked default.
const isWebSearchConfigured = (
  providers: WebSearchProviderConfiguration[],
): boolean => providers.some((p) => p.isDefault && canSearch(p.provider));

const normalizeWebSearchSettings = (
  settings?: Partial<{ includeImages: unknown; maxImages: unknown }>,
) => {
  const includeImages =
    typeof settings?.includeImages === 'boolean'
      ? settings.includeImages
      : DEFAULT_WEB_SEARCH_SETTINGS.includeImages;
  const parsedMaxImages = Number(settings?.maxImages);
  const maxImages =
    Number.isInteger(parsedMaxImages) &&
    parsedMaxImages >= 1 &&
    parsedMaxImages <= 500
      ? parsedMaxImages
      : DEFAULT_WEB_SEARCH_SETTINGS.maxImages;

  return {
    includeImages,
    maxImages,
  };
};


const normalizeUrl = (url: unknown): string => {
  if (!url || typeof url !== 'string') return '';
  const trimmed = String(url).trim();
  return trimmed.endsWith('/') ? trimmed.slice(0, -1) : trimmed;
};

function getOrgIdFromRequest(
  req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
): string | undefined {
  return (
    (req as AuthenticatedUserRequest).user?.orgId ||
    (req as AuthenticatedServiceRequest).tokenPayload?.orgId
  );
}

export const createStorageConfig =
  (
    keyValueStoreService: KeyValueStoreService,
    defaultConfig: DefaultStorageConfig,
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const storageType = req.body.storageType;
      let config: Record<string, any> = {};
      // config coming from file
      config = req.body;
      const configManagerConfig = loadConfigurationManagerConfig();
      // Process configuration based on storage type
      switch (storageType.toLowerCase()) {
        case storageTypes.S3.toLowerCase(): {
          // accessKeyId/secretAccessKey are optional: omitting both tells S3
          // (and the AWS SDK) to use the EC2/ECS IAM role via the default
          // credential provider chain instead of explicit IAM user credentials.
          // Supplying only one is rejected rather than silently downgraded to
          // the IAM role, which would run as a different AWS principal.
          const resolvedCredentials = resolveS3Credentials({
            accessKeyId: config.s3AccessKeyId,
            secretAccessKey: config.s3SecretAccessKey,
          });
          if (resolvedCredentials.kind === 'partial') {
            throw new BadRequestError(S3_PARTIAL_CREDENTIALS_MESSAGE, {
              missingFields: { [resolvedCredentials.missingField]: true },
            });
          }
          const s3Config: {
            accessKeyId?: string;
            secretAccessKey?: string;
            region: string;
            bucketName: string;
          } = {
            region: config.s3Region,
            bucketName: config.s3BucketName,
            ...(resolvedCredentials.kind === 'explicit'
              ? {
                  accessKeyId: resolvedCredentials.accessKeyId,
                  secretAccessKey: resolvedCredentials.secretAccessKey,
                }
              : {}),
          };

          const s3HealthCheck = await validateS3Capabilities({
            accessKeyId: s3Config.accessKeyId,
            secretAccessKey: s3Config.secretAccessKey,
            region: s3Config.region,
            bucketName: s3Config.bucketName,
          });

          if (!s3HealthCheck.success) {
            throw new BadRequestError(
              buildS3HealthCheckErrorMessage(s3HealthCheck.checks),
              { s3HealthCheck: s3HealthCheck.checks },
            );
          }

          const encryptedS3Config = EncryptionService.getInstance(
            configManagerConfig.algorithm,
            configManagerConfig.secretKey,
          ).encrypt(JSON.stringify(s3Config));

          await keyValueStoreService.set<string>(
            configPaths.storageService,
            JSON.stringify({
              storageType: storageTypes.S3,
              s3: encryptedS3Config,
            }),
          );
          break;
        }

        case storageTypes.AZURE_BLOB.toLowerCase(): {
          if (config.azureBlobConnectionString) {
            const encryptedAzureBlobConnectionString =
              EncryptionService.getInstance(
                configManagerConfig.algorithm,
                configManagerConfig.secretKey,
              ).encrypt(config.azureBlobConnectionString);

            await keyValueStoreService.set<string>(
              configPaths.storageService,
              JSON.stringify({
                storageType: storageTypes.AZURE_BLOB,
                azureBlob: encryptedAzureBlobConnectionString,
              }),
            );
          } else {
            const azureBlobConfig = {
              endpointProtocol: config.endpointProtocol || 'https',
              accountName: config.accountName,
              accountKey: config.accountKey,
              endpointSuffix: config.endpointSuffix || 'core.windows.net',
              containerName: config.containerName,
            };
            const encryptedAzureBlobConfig = EncryptionService.getInstance(
              configManagerConfig.algorithm,
              configManagerConfig.secretKey,
            ).encrypt(JSON.stringify(azureBlobConfig));

            await keyValueStoreService.set<string>(
              configPaths.storageService,
              JSON.stringify({
                storageType: storageTypes.AZURE_BLOB,
                azureBlob: encryptedAzureBlobConfig,
              }),
            );
          }
          break;
        }

        case storageTypes.LOCAL.toLowerCase(): {
          const replicaProblem = localStorageReplicaProblem();
          if (replicaProblem) {
            throw new BadRequestError(replicaProblem);
          }
          const localConfig = {
            mountName: config.mountName || 'PipesHub',
            baseUrl: config.baseUrl || defaultConfig.endpoint,
          };
          await keyValueStoreService.set<string>(
            configPaths.storageService,
            JSON.stringify({
              storageType: storageTypes.LOCAL,
              local: JSON.stringify(localConfig),
            }),
          );
          break;
        }

        default:
          throw new BadRequestError(`Unsupported storage type: ${storageType}`);
      }
      res.status(200).json({
        message: 'Storage configuration saved successfully',
      });
    } catch (error: any) {
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
      const storageConfig =
        (await keyValueStoreService.get<string>(configPaths.storageService)) ||
        '{}';

      const parsedConfig = parseKvObject(storageConfig);
      const userId =
        (_req as AuthenticatedUserRequest).user?.userId ?? null;

      const storageType = parsedConfig.storageType;

      if (!storageType) {
        throw new BadRequestError('Storage type not found');
      }

      const configManagerConfig = loadConfigurationManagerConfig();

      if (storageType === storageTypes.S3) {
        const encryptedS3Config = parsedConfig.s3;

        if (encryptedS3Config) {
          if (userId) {
            res.status(200).json({}).end();
            return;
          }
          const s3Config = EncryptionService.getInstance(
            configManagerConfig.algorithm,
            configManagerConfig.secretKey,
          ).decrypt(encryptedS3Config);

          const { accessKeyId, secretAccessKey, region, bucketName } =
            JSON.parse(s3Config);
          res
            .status(200)
            .json({
              storageType,
              accessKeyId,
              secretAccessKey,
              useIamRole:
                resolveS3Credentials({ accessKeyId, secretAccessKey }).kind !==
                'explicit',
              region,
              bucketName,
            })
            .end();
          return;
        } else {
          throw new BadRequestError('Storage config not found');
        }
      }

      if (storageType === storageTypes.AZURE_BLOB) {
        const encryptedAzureBlobConfig = parsedConfig.azureBlob;
        if (encryptedAzureBlobConfig) {
          if (userId) {
            res.status(200).json({}).end();
            return;
          }
          const azureBlobConfig = JSON.parse(
            EncryptionService.getInstance(
              configManagerConfig.algorithm,
              configManagerConfig.secretKey,
            ).decrypt(encryptedAzureBlobConfig),
          );

          const {
            endpointProtocol,
            accountName,
            accountKey,
            endpointSuffix,
            containerName,
          } = azureBlobConfig;
          res
            .status(200)
            .json({
              storageType,
              endpointProtocol,
              accountName,
              accountKey,
              endpointSuffix,
              containerName,
            })
            .end();
          return;
        } else {
          throw new BadRequestError('Storage config not found');
        }
      }

      if (storageType === storageTypes.LOCAL) {
        if (userId) {
          res.status(200).json({}).end();
          return;
        }
        const localConfig = parsedConfig.local;
        res
          .status(200)
          .json(JSON.parse(localConfig || '{}'))
          .end();
        return;
      }

      res.status(HTTP_STATUS.BAD_REQUEST).json({
        message: 'Unsupported storage type',
      });
      return;
    } catch (error: any) {
      next(error);
      return;
    }
  };

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
      const configManagerConfig = loadConfigurationManagerConfig();
      const encryptionService = EncryptionService.getInstance(
        configManagerConfig.algorithm,
        configManagerConfig.secretKey,
      );

      let smtpConfig = req.body as Record<string, unknown>;
      const hasPlaceholder = SMTP_SECRET_KEYS.some(
        (key) => smtpConfig[key] === CONFIG_SECRET_PLACEHOLDER,
      );
      // An unreadable stored config must not block saving a complete new one.
      const existing = hasPlaceholder
        ? await getParsedSmtpConfig(keyValueStoreService)
        : await getParsedSmtpConfig(keyValueStoreService).catch(() => null);
      if (hasPlaceholder && existing) {
        if (smtpHostMovedWithSavedLogin(smtpConfig, existing)) {
          res.status(HTTP_STATUS.BAD_REQUEST).json(CREDENTIAL_REENTRY_REQUIRED);
          return;
        }
        smtpConfig = mergeSmtpConfigPlaceholders(smtpConfig, existing);
      }
      // Editing the settings must not quietly turn email back on.
      if (existing?.enabled === false) {
        smtpConfig = { ...smtpConfig, enabled: false };
      }

      // Bad credentials would otherwise surface only when mail starts failing,
      // after invites and resets were already reported as sent.
      // Codes only in the log: the host and username are secret
      // (maskSmtpConfig), and a server's refusal text can repeat the username.
      let refusal: { code?: unknown; responseCode?: unknown } = {};
      const problem = await smtpSettingsProblem(
        smtpConfig as unknown as SmtpConfig,
        (error) => {
          const { code, responseCode } = (error ?? {}) as typeof refusal;
          refusal = { code, responseCode };
        },
      );
      if (problem) {
        logger.warn('SMTP settings refused by the mail server', {
          problem,
          ...refusal,
        });
        throw new BadRequestError(SMTP_SETTINGS_PROBLEM_MESSAGES[problem]);
      }

      const encryptedSmtpConfig = encryptionService.encrypt(
        JSON.stringify(smtpConfig),
      );
      await keyValueStoreService.set<string>(
        configPaths.smtp,
        encryptedSmtpConfig,
      );
      await reloadMailSmtpConfig(
        req.user,
        communicationBackend,
        scopedJwtSecret,
      );

      res
        .status(200)
        .json({ message: 'SMTP config created successfully' })
        .end();
    } catch (error: any) {
      next(error);
    }
  };

/** The mail service caches SMTP settings at startup; tell it to read them again. */
const reloadMailSmtpConfig = async (
  user: NonNullable<AuthenticatedUserRequest['user']>,
  communicationBackend: string,
  scopedJwtSecret: string,
): Promise<void> => {
  const response = await axios({
    method: 'post' as const,
    url: `${communicationBackend}/api/v1/mail/updateSmtpConfig`,
    headers: {
      Authorization: `Bearer ${await generateFetchConfigAuthToken(user, scopedJwtSecret)}`,
      'Content-Type': 'application/json',
    },
  });
  if (response.status != 200) {
    throw new BadRequestError('Error setting smtp config');
  }
};

/** Loads, decrypts, and parses the stored SMTP config. Returns `null` when none is set. */
const getParsedSmtpConfig = async (
  keyValueStoreService: KeyValueStoreService,
): Promise<Record<string, unknown> | null> => {
  const configManagerConfig = loadConfigurationManagerConfig();
  const encryptedSmtpConfig = await keyValueStoreService.get<string>(
    configPaths.smtp,
  );
  if (!encryptedSmtpConfig) {
    return null;
  }
  return JSON.parse(
    EncryptionService.getInstance(
      configManagerConfig.algorithm,
      configManagerConfig.secretKey,
    ).decrypt(encryptedSmtpConfig),
  ) as Record<string, unknown>;
};

export const getSmtpConfig =
  (keyValueStoreService: KeyValueStoreService, applyMasking = true) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const smtpConfig = await getParsedSmtpConfig(keyValueStoreService);
      if (smtpConfig) {
        const hideSecrets =
          applyMasking && shouldHideSecrets(req) && !canRevealSecrets(req);
        res
          .status(200)
          .json(hideSecrets ? maskSmtpConfig(smtpConfig) : smtpConfig)
          .end();
        return;
      }
      res.status(200).json({}).end();
    } catch (error: any) {
      next(error);
    }
  };

/**
 * GET /smtpConfig/status — boolean-only, no secrets. Unlike `getSmtpConfig`
 * this is intentionally open to any authenticated org member (not just
 * admins): non-admins can invite users (`USER_INVITE` scope) and need to know
 * whether that will succeed without being able to read/manage the SMTP
 * credentials themselves. Mirrors the gate `smtpConfigCheck`
 * (user_management) actually enforces before sending invite emails.
 */
export const getSmtpConfigStatus =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const smtpConfig = await getParsedSmtpConfig(keyValueStoreService);
      const configured = Boolean(
        smtpConfig?.host &&
        smtpConfig.port &&
        smtpConfig.fromEmail &&
        smtpConfig.enabled !== false,
      );
      res.status(200).json({ configured }).end();
    } catch (error: any) {
      next(error);
    }
  };
/** DELETE /smtpConfig — removes the stored settings; the org can no longer send email. */
export const deleteSmtpConfig =
  (
    keyValueStoreService: KeyValueStoreService,
    communicationBackend: string,
    scopedJwtSecret: string,
  ): ((
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ) => Promise<void>) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      if (!req.user) {
        throw new UnauthorizedError('User not Found');
      }
      await keyValueStoreService.delete(configPaths.smtp);
      await reloadMailSmtpConfig(
        req.user,
        communicationBackend,
        scopedJwtSecret,
      );
      res
        .status(200)
        .json({ message: 'SMTP config deleted successfully' })
        .end();
    } catch (error: unknown) {
      next(error);
    }
  };

/** PUT /smtpConfig/enabled — turns sending off or back on without losing the settings. */
export const setSmtpConfigEnabled =
  (
    keyValueStoreService: KeyValueStoreService,
    communicationBackend: string,
    scopedJwtSecret: string,
  ): ((
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ) => Promise<void>) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      if (!req.user) {
        throw new UnauthorizedError('User not Found');
      }
      const { enabled } = req.body as { enabled: boolean };
      const existing = await getParsedSmtpConfig(keyValueStoreService);
      if (!existing) {
        throw new NotFoundError('SMTP is not configured');
      }
      const configManagerConfig = loadConfigurationManagerConfig();
      const encrypted = EncryptionService.getInstance(
        configManagerConfig.algorithm,
        configManagerConfig.secretKey,
      ).encrypt(JSON.stringify({ ...existing, enabled }));
      await keyValueStoreService.set<string>(configPaths.smtp, encrypted);
      await reloadMailSmtpConfig(
        req.user,
        communicationBackend,
        scopedJwtSecret,
      );
      res.status(200).json({ enabled }).end();
    } catch (error: unknown) {
      next(error);
    }
  };

const SLACK_BOT_CAS_MAX_RETRIES = 5;
export const SLACK_BOT_SETTINGS_UNREADABLE =
  "The saved Slack bot settings couldn't be read, so nothing was shown or changed. This usually means the server's encryption key (the SECRET_KEY setting) changed after the bots were saved. Ask whoever runs your PipesHub server to restore the original key, then try again.";

const parseSlackBotStore = (
  encrypted: string | null | undefined,
  configManagerConfig: ReturnType<typeof loadConfigurationManagerConfig>,
): SlackBotStore => {
  if (!encrypted) {
    return { configs: [] };
  }

  try {
    const decrypted = EncryptionService.getInstance(
      configManagerConfig.algorithm,
      configManagerConfig.secretKey,
    ).decrypt(encrypted);

    const parsed = JSON.parse(decrypted) as Partial<SlackBotStore>;
    return {
      configs: Array.isArray(parsed.configs) ? parsed.configs : [],
    };
  } catch (error) {
    // Answering "no bots" here would let the next save overwrite every stored bot.
    throw withCause(
      new InternalServerError(SLACK_BOT_SETTINGS_UNREADABLE),
      safeCause(error),
    );
  }
};

export const getSlackBotStore = async (
  keyValueStoreService: KeyValueStoreService,
): Promise<SlackBotStore> => {
  const configManagerConfig = loadConfigurationManagerConfig();
  const encrypted = await keyValueStoreService.get<string>(configPaths.slackBot);
  return parseSlackBotStore(encrypted, configManagerConfig);
};

/** Inbound events are matched to a bot by signing secret, so a second row with
 * the same secret or token makes the answering bot (and its agent) depend on
 * store order. Checked inside the CAS write. */
const assertSlackAppNotAlreadyConfigured = (
  configs: SlackBotConfigEntry[],
  credentials: { botToken: string; signingSecret: string },
  exceptConfigId?: string,
): void => {
  const duplicate = configs.some(
    (entry) =>
      entry.id !== exceptConfigId &&
      (entry.signingSecret === credentials.signingSecret ||
        entry.botToken === credentials.botToken),
  );
  if (duplicate) {
    throw new BadRequestError(
      'This Slack app is already connected to another bot in this organization',
    );
  }
};

const updateSlackBotStoreWithCAS = async <T>(
  keyValueStoreService: KeyValueStoreService,
  updater: (store: SlackBotStore) => T,
): Promise<T> => {
  const configManagerConfig = loadConfigurationManagerConfig();

  for (let attempt = 0; attempt < SLACK_BOT_CAS_MAX_RETRIES; attempt++) {
    const encryptedCurrent = await keyValueStoreService.get<string>(configPaths.slackBot);
    const store = parseSlackBotStore(encryptedCurrent, configManagerConfig);
    const result = updater(store);

    const encryptedUpdated = EncryptionService.getInstance(
      configManagerConfig.algorithm,
      configManagerConfig.secretKey,
    ).encrypt(JSON.stringify(store));

    const casSuccess = await keyValueStoreService.compareAndSet<string>(
      configPaths.slackBot,
      encryptedCurrent ?? null,
      encryptedUpdated,
    );

    if (casSuccess) {
      return result;
    }

    if (attempt === SLACK_BOT_CAS_MAX_RETRIES - 1) {
      throw new Error(
        'Failed to update Slack bot config due to concurrent modification. Please try again.',
      );
    }

    await new Promise((resolve) => setTimeout(resolve, 50 * (attempt + 1)));
  }

  throw new Error('Failed to update Slack bot config.');
};

/** Admin-facing shape. Credentials are masked; an edit re-submitting the
 * placeholder is restored from storage by mergeSlackBotConfigPlaceholders. */
const slackBotConfig = (config: SlackBotConfigEntry, reveal = false) => {
  const entry = {
    id: config.id,
    name: config.name,
    agentId: config.agentId ?? null,
    createdAt: config.createdAt,
    updatedAt: config.updatedAt,
    botToken: config.botToken,
    signingSecret: config.signingSecret,
  };
  return reveal ? entry : maskSlackBotConfig(entry);
};

/** Everything the bot process needs to verify a signature and act as the bot,
 * and nothing else. Served only to SLACK_BOT_VERIFY holders. */
const slackBotInternalConfig = (config: SlackBotConfigEntry) => ({
  id: config.id,
  agentId: config.agentId ?? null,
  botToken: config.botToken,
  signingSecret: config.signingSecret,
});

export const getSlackBotConfigs =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const store = await getSlackBotStore(keyValueStoreService);
      const reveal = canRevealSecrets(req);
      res
        .status(HTTP_STATUS.OK)
        .json({
          status: 'success',
          configs: store.configs.map((config) => slackBotConfig(config, reveal)),
        })
        .end();
    } catch (error: any) {
      next(error);
    }
  };

export const getInternalSlackBotConfigs =
  (keyValueStoreService: KeyValueStoreService) =>
  async (
    _req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ) => {
    try {
      const store = await getSlackBotStore(keyValueStoreService);
      res
        .status(HTTP_STATUS.OK)
        .json({
          status: 'success',
          configs: store.configs.map(slackBotInternalConfig),
        })
        .end();
    } catch (error: any) {
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
          assertSlackAppNotAlreadyConfigured(store.configs, {
            botToken: botToken as string,
            signingSecret: signingSecret as string,
          });
          if (normalizedAgentId) {
            const duplicate = store.configs.find(
              (configItem) => configItem.agentId === normalizedAgentId,
            );
            if (duplicate) {
              throw new BadRequestError(
                'Selected agent is already linked to another Slack Bot configuration',
              );
            }
          }

          const timestamp = new Date().toISOString();
          const createdConfig: SlackBotConfigEntry = {
            id: randomUUID(),
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

      res
        .status(HTTP_STATUS.OK)
        .json({
          status: 'success',
          config: slackBotConfig(config),
        })
        .end();
    } catch (error: any) {
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
          const configIndex = store.configs.findIndex((config) => config.id === configId);

          if (configIndex === -1) {
            throw new NotFoundError('Slack Bot configuration not found');
          }

          if (normalizedAgentId) {
            const duplicate = store.configs.find(
              (configItem) =>
                configItem.agentId === normalizedAgentId && configItem.id !== configId,
            );
            if (duplicate) {
              throw new BadRequestError(
                'Selected agent is already linked to another Slack Bot configuration',
              );
            }
          }

          const previousConfig = store.configs[configIndex];
          if (!previousConfig) {
            throw new Error("Config not found");
          }
          const credentials = mergeSlackBotConfigPlaceholders(
            { botToken, signingSecret },
            previousConfig,
          );
          assertSlackAppNotAlreadyConfigured(
            store.configs,
            credentials,
            configId,
          );
          const nextConfig: SlackBotConfigEntry = {
            ...previousConfig,
            name,
            botToken: credentials.botToken,
            signingSecret: credentials.signingSecret,
            agentId: normalizedAgentId,
            updatedAt: new Date().toISOString(),
          };

          store.configs[configIndex] = nextConfig;
          return nextConfig;
        },
      );

      res
        .status(HTTP_STATUS.OK)
        .json({
          status: 'success',
          config: slackBotConfig(updatedConfig),
        })
        .end();
    } catch (error: any) {
      next(error);
    }
  };

export const deleteSlackBotConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { configId } = req.params;
      await updateSlackBotStoreWithCAS(keyValueStoreService, (store) => {
        const configIndex = store.configs.findIndex((config) => config.id === configId);

        if (configIndex === -1) {
          throw new NotFoundError('Slack Bot configuration not found');
        }

        store.configs.splice(configIndex, 1);
      });

      res
        .status(HTTP_STATUS.OK)
        .json({
          status: 'success',
          message: 'Slack Bot configuration deleted',
        })
        .end();
    } catch (error: any) {
      next(error);
    }
  };

// Platform settings
/** The flags without the agent harness's, as a release without the harness has them (ADR-OPS-06). */
const withoutHarnessFlags = <T>(flags: Record<string, T>): Record<string, T> =>
  Object.fromEntries(
    Object.entries(flags).filter(
      ([key]) => !AGENT_HARNESS_LABS_FLAGS.includes(key.toUpperCase()),
    ),
  );

export const AGENT_HARNESS_OFF_MESSAGE =
  'The agent harness is not enabled on this install. Set AGENT_HARNESS_WORKER_ENABLED=true and restart to use it.';

/** What a Labs save decides, for its audit entry: the settings it sends, by path; never `expected`. */
const labsDecision = (save: {
  fileUploadMaxSizeBytes?: unknown;
  featureFlags?: Record<string, unknown>;
  notices?: unknown;
  agentHarness?: unknown;
}): { decided: Record<string, unknown>; paths: string[] } => {
  const decided = Object.fromEntries(
    Object.entries(save).filter(([, value]) => value !== undefined),
  );
  const paths = Object.keys(decided).flatMap((key) =>
    key === 'featureFlags' && save.featureFlags !== undefined
      ? Object.keys(save.featureFlags).map((flag) => `featureFlags.${flag}`)
      : [key],
  );
  return { decided, paths: [...new Set(paths)].sort() };
};

/**
 * One Labs save is one audited control-plane write (CPD-1, R2 CP1): UX2's
 * compare-and-set save is the transaction's effect, so `config.changed` is
 * recorded only when the save landed; a refused save records nothing.
 */
export const setPlatformSettings =
  (
    keyValueStoreService: KeyValueStoreService,
    controlPlane: ControlPlaneTransaction,
    env: EnvFlagEnvironment = process.env,
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const {
        fileUploadMaxSizeBytes,
        featureFlags,
        notices,
        agentHarness,
        expected,
      } = req.body as PlatformSettingsSave;
      if (
        !agentHarnessSwitchedOn(env) &&
        (agentHarness !== undefined ||
          Object.keys(featureFlags ?? {}).some((key) =>
            AGENT_HARNESS_LABS_FLAGS.includes(key.toUpperCase()),
          ))
      ) {
        throw new BadRequestError(AGENT_HARNESS_OFF_MESSAGE, {
          reasonCode: 'agent_harness_off',
        });
      }
      const orgId = String(currentOrgContext().orgId);
      const current = await getPlatformSettingsFromStore(keyValueStoreService);
      // Turning artifact reads on while an indexing member that does not follow the stored-write
      // protocol may run, or while the membership check cannot be seen, could serve a mismatch.
      // A save sends only the flags it changes, so the flags after it are the stored ones with those applied.
      if (
        savePutsArtifactReadsAtRisk(current.featureFlags, {
          ...current.featureFlags,
          ...(featureFlags as Record<string, unknown> | undefined),
        })
      ) {
        const verdict = await readMembership(keyValueStoreService);
        if (!verdict.ok) {
          throw new ConflictError(
            verdict.reason === 'untagged'
              ? `Artifact reads cannot be turned on while older indexing members run: ${verdict.untagged.join(', ')}`
              : 'Artifact reads cannot be turned on: the indexing membership check is not available. Try again in a minute.',
            { untagged: verdict.untagged, reason: verdict.reason },
          );
        }
      }
      const { decided, paths } = labsDecision({
        fileUploadMaxSizeBytes,
        featureFlags,
        notices,
        agentHarness,
      });
      const { result } = await controlPlane.run(
        { orgId, actor: actorRefOfUser(String(req.user?.userId)) },
        (tx) =>
          Promise.resolve({
            // UX1: only the flags the admin changed are recorded; UX2: as a compare-and-set, never over a newer choice.
            result: tx.external(() =>
              savePlatformSettings(keyValueStoreService, {
                fileUploadMaxSizeBytes,
                featureFlags,
                notices,
                agentHarness,
                expected,
              }),
            ),
            change: {
              scope: orgScope(orgId),
              object: { type: 'platform-settings', id: 'labs' },
              revisionFrom: null,
              revisionTo: null,
              changeDigest: canonicalDigest(decided),
              changed: paths,
            },
          }),
      );
      const settings = result.value;
      res
        .status(200)
        .json({ message: 'Platform settings saved', settings })
        .end();
      // ADR-OPS-08: the org's harness just turned on; its control-plane grants catch up in the background.
      if (
        agentHarnessSwitchedOn(env) &&
        current.featureFlags[DURABLE_AGENT_FLAG] !== true &&
        settings.featureFlags[DURABLE_AGENT_FLAG] === true
      ) {
        orgHarnessTurnedOn(orgId);
      }
    } catch (error: any) {
      // The error middleware's line is the one failure line (#6353): no log here.
      if (error instanceof PlatformSettingsChanged) {
        next(
          new ConflictError(
            'Someone else changed these settings since you opened them. Review them and save again.',
            { reasonCode: LABS_CHANGED, stale: error.stale },
          ),
        );
        return;
      }
      if (error instanceof BadRequestError) {
        next(error);
        return;
      }
      next(controlPlaneHttpError(error));
    }
  };

export const getPlatformSettings =
  (
    keyValueStoreService: KeyValueStoreService,
    env: EnvFlagEnvironment = process.env,
  ) =>
  async (
    _req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ) => {
    try {
      const settings = await getPlatformSettingsFromStore(keyValueStoreService);
      if (!agentHarnessSwitchedOn(env)) {
        const { agentHarness: _harness, ...rest } = settings;
        res
          .status(200)
          .json({
            ...rest,
            featureFlags: withoutHarnessFlags(settings.featureFlags),
            featureFlagOverrides: withoutHarnessFlags(
              settings.featureFlagOverrides ?? {},
            ),
          })
          .end();
        return;
      }
      res.status(200).json(settings).end();
    } catch (error: any) {
      next(error);
    }
  };

export const getAvailablePlatformFeatureFlags =
  (env: EnvFlagEnvironment = process.env) =>
  async (
    _req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    _next: NextFunction,
  ) => {
    // Only expose user-toggleable flags. Hidden flags are still seeded with
    // their defaults by getPlatformSettingsFromStore but never surface in the
    // Labs UI.
    // `previousDefault` only reads settings saved before overrides (UX1); the page never needs it.
    const harnessOn = agentHarnessSwitchedOn(env);
    const flags = PLATFORM_FEATURE_FLAGS.filter(
      (f) => !f.hidden && (harnessOn || f.requiresAgentHarness !== true),
    ).map(({ previousDefault: _previous, ...flag }) => flag);
    res.status(200).json({ flags }).end();
  };

export const getEffectivePlatformFeatureFlags =
  (
    keyValueStoreService: KeyValueStoreService,
    harnessRollout: HarnessRolloutPolicy = createHarnessRollout(
      keyValueStoreService,
    ),
    env: EnvFlagEnvironment = process.env,
    engineRuns: () => Promise<boolean> = engineRunsServed,
  ) =>
  async (
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ) => {
    // Unlike getPlatformSettings/getAvailablePlatformFeatureFlags (admin-only),
    // this is callable by every authenticated user: flag values are just
    // booleans (no secrets), and non-admin UI (chat, agent builder, personal
    // pages) needs them to decide whether to render flag-gated features.
    try {
      const { featureFlags } = await getPlatformSettingsFromStore(
        keyValueStoreService,
      );
      if (!agentHarnessSwitchedOn(env)) {
        // ADR-OPS-06: the browser shows nothing of the harness; the fields stay for clients that read them.
        res
          .status(200)
          .json({
            featureFlags: withoutHarnessFlags(featureFlags),
            agentHarness: null,
            harnessReconcile: false,
            continuePrivately: false,
            engineRuns: false,
          })
          .end();
        return;
      }
      // The browser follows the rollout policy's answer instead of reading
      // the raw Labs flag, so kill switch and readiness apply to it as well.
      const orgId = getOrgIdFromRequest(req);
      const agentHarness = orgId
        ? await harnessRollout.decideForOrg(orgId)
        : null;
      featureFlags.ENABLE_AGENT_HARNESS = agentHarness?.useHarness === true;
      // Each harness surface follows the org's harness (ADR-OPS-08): with it off the org reads what an install with
      // the switch off answers.
      const harnessOn = agentHarness?.harnessOn === true;
      const harnessReconcile = harnessOn && harnessReconcileEnabled(env);
      // Personal AI accounts serve only engine runs (UX3): the settings offer
      // them while a worker runs those.
      const engineRunsOn = harnessOn && (await engineRuns().catch(() => false));
      // The fork route (SS3) serves exactly when this holds; the browser
      // offers "Continue privately" from it instead of a button that answers 503.
      const continuePrivately =
        harnessOn && isAgentHarnessInfrastructureReady(env);
      res
        .status(200)
        .json({
          featureFlags,
          agentHarness,
          harnessReconcile,
          continuePrivately,
          engineRuns: engineRunsOn,
        })
        .end();
    } catch (error: any) {
      next(error);
    }
  };

/**
 * Auth-config setters accept the masked placeholder for a secret the admin did
 * not reveal, and keep the stored value for it.
 */
async function restoreStoredAuthSecrets<T extends Record<string, unknown>>(
  keyValueStoreService: KeyValueStoreService,
  path: string,
  incoming: T,
  keys: readonly string[],
): Promise<T> {
  if (!keys.some((key) => incoming[key] === CONFIG_SECRET_PLACEHOLDER)) {
    return incoming;
  }
  const configManagerConfig = loadConfigurationManagerConfig();
  const encrypted = await keyValueStoreService.get<string>(path);
  const existing = encrypted
    ? (JSON.parse(
        EncryptionService.getInstance(
          configManagerConfig.algorithm,
          configManagerConfig.secretKey,
        ).decrypt(encrypted),
      ) as Record<string, unknown>)
    : null;
  const { config, unrestored } = restoreSecretPlaceholders(incoming, existing, keys);
  if (unrestored.length > 0) {
    throw new BadRequestError(`Enter a value for ${unrestored.join(', ')}`);
  }
  return config;
}

export const getMicrosoftAuthConfig =
  (keyValueStoreService: KeyValueStoreService, applyMasking = true) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const configManagerConfig = loadConfigurationManagerConfig();
      const readAuthConfig = async (
        path: string,
      ): Promise<Record<string, unknown> | null> => {
        const encrypted = await keyValueStoreService.get<string>(path);
        if (!encrypted) return null;
        return JSON.parse(
          EncryptionService.getInstance(
            configManagerConfig.algorithm,
            configManagerConfig.secretKey,
          ).decrypt(encrypted),
        ) as Record<string, unknown>;
      };

      let authConfig = await readAuthConfig(configPaths.auth.microsoft);
      // Azure AD sign-in became Microsoft sign-in; until the startup migration
      // copies its settings over, serve those.
      if (!authConfig?.clientId && RENAMED_SIGN_IN_METHODS.azureAd === 'microsoft') {
        const azureAd = await readAuthConfig(configPaths.auth.azureAD);
        if (azureAd?.clientId) authConfig = azureAd;
      }

      if (authConfig) {
        const hideSecrets =
          applyMasking && shouldHideSecrets(req) && !canRevealSecrets(req);
        res
          .status(200)
          .json(hideSecrets ? maskMicrosoftAuthConfig(authConfig) : authConfig)
          .end();
      } else {
        res.status(200).json({}).end();
      }
    } catch (error: any) {
      next(error);
    }
  };

export const setMicrosoftAuthConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const configManagerConfig = loadConfigurationManagerConfig();

      const { clientId, tenantId, enableJit } = await restoreStoredAuthSecrets(
        keyValueStoreService,
        configPaths.auth.microsoft,
        req.body,
        MICROSOFT_AUTH_SECRET_KEYS,
      );
      const authority = `https://login.microsoftonline.com/${tenantId}`;

      const encryptedAuthConfig = EncryptionService.getInstance(
        configManagerConfig.algorithm,
        configManagerConfig.secretKey,
      ).encrypt(JSON.stringify({ clientId, tenantId, authority, enableJit: enableJit ?? true }));

      await keyValueStoreService.set<string>(
        configPaths.auth.microsoft,
        encryptedAuthConfig,
      );

      res
        .status(200)
        .json({ message: 'Microsoft Auth config created successfully' })
        .end();
    } catch (error: any) {
      next(error);
    }
  };

export const getGoogleAuthConfig =
  (keyValueStoreService: KeyValueStoreService, applyMasking = true) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const configManagerConfig = loadConfigurationManagerConfig();

      const encryptedAuthConfig = await keyValueStoreService.get<string>(
        configPaths.auth.google,
      );

      if (encryptedAuthConfig) {
        const authConfig = JSON.parse(
          EncryptionService.getInstance(
            configManagerConfig.algorithm,
            configManagerConfig.secretKey,
          ).decrypt(encryptedAuthConfig),
        );
        const hideSecrets =
          applyMasking && shouldHideSecrets(req) && !canRevealSecrets(req);
        res
          .status(200)
          .json(hideSecrets ? maskGoogleAuthConfig(authConfig) : authConfig)
          .end();
      } else {
        res.status(200).json({}).end();
      }
    } catch (error: any) {
      next(error);
    }
  };

export const setGoogleAuthConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const configManagerConfig = loadConfigurationManagerConfig();

      const { clientId, enableJit } = await restoreStoredAuthSecrets(
        keyValueStoreService,
        configPaths.auth.google,
        req.body,
        GOOGLE_AUTH_SECRET_KEYS,
      );

      const encryptedAuthConfig = EncryptionService.getInstance(
        configManagerConfig.algorithm,
        configManagerConfig.secretKey,
      ).encrypt(JSON.stringify({ clientId, enableJit: enableJit ?? true }));

      await keyValueStoreService.set<string>(
        configPaths.auth.google,
        encryptedAuthConfig,
      );

      res
        .status(200)
        .json({ message: 'Google Auth config created successfully' })
        .end();
    } catch (error: any) {
      next(error);
    }
  };

export const createArangoDbConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { url, username, password } = req.body;
      const db = ARANGO_DB_NAME;
      const configManagerConfig = loadConfigurationManagerConfig();
      const encryptedArangoDBConfig = EncryptionService.getInstance(
        configManagerConfig.algorithm,
        configManagerConfig.secretKey,
      ).encrypt(JSON.stringify({ url, username, password, db }));
      await keyValueStoreService.set<string>(
        configPaths.db.arangodb,
        encryptedArangoDBConfig,
      );

      res
        .status(200)
        .json({
          message: 'Arango DB config created successfully',
        })
        .end();
    } catch (error: any) {
      next(error);
    }
  };

export const getArangoDbConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const configManagerConfig = loadConfigurationManagerConfig();
      const encryptedArangoDBConfig = await keyValueStoreService.get<string>(
        configPaths.db.arangodb,
      );
      if (encryptedArangoDBConfig) {
        const arangoDBConfig = JSON.parse(
          EncryptionService.getInstance(
            configManagerConfig.algorithm,
            configManagerConfig.secretKey,
          ).decrypt(encryptedArangoDBConfig),
        );
        res.status(200).json(arangoDBConfig).end();
        return;
      }
      res.status(200).json({}).end();
    } catch (error: any) {
      next(error);
    }
  };

export const createMongoDbConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { uri } = req.body;
      const db = MONGO_DB_NAME;
      const configManagerConfig = loadConfigurationManagerConfig();
      const encryptedMongoDBConfig = EncryptionService.getInstance(
        configManagerConfig.algorithm,
        configManagerConfig.secretKey,
      ).encrypt(JSON.stringify({ uri, db }));
      await keyValueStoreService.set<string>(
        configPaths.db.mongodb,
        encryptedMongoDBConfig,
      );

      res
        .status(200)
        .json({
          message: 'Mongo DB config created successfully',
        })
        .end();
    } catch (error: any) {
      next(error);
    }
  };

export const getMongoDbConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const configManagerConfig = loadConfigurationManagerConfig();

      const encryptedMongoDBConfig = await keyValueStoreService.get<string>(
        configPaths.db.mongodb,
      );
      if (encryptedMongoDBConfig) {
        const mongoDBConfig = JSON.parse(
          EncryptionService.getInstance(
            configManagerConfig.algorithm,
            configManagerConfig.secretKey,
          ).decrypt(encryptedMongoDBConfig),
        );

        res.status(200).json(mongoDBConfig).end();
        return;
      }
      res.status(200).json({}).end();
    } catch (error: any) {
      next(error);
    }
  };

export const createRedisConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { host, port, password, tls } = req.body;
      const configManagerConfig = loadConfigurationManagerConfig();
      const encryptedRedisConfig = EncryptionService.getInstance(
        configManagerConfig.algorithm,
        configManagerConfig.secretKey,
      ).encrypt(JSON.stringify({ host, port, password, tls }));
      await keyValueStoreService.set<string>(
        configPaths.keyValueStore.redis,
        encryptedRedisConfig,
      );
      res.status(200).json({ message: 'Redis config created successfully' });
    } catch (error: any) {
      next(error);
    }
  };

export const getRedisConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const configManagerConfig = loadConfigurationManagerConfig();
      const encryptedRedisConfig = await keyValueStoreService.get<string>(
        configPaths.keyValueStore.redis,
      );
      if (encryptedRedisConfig) {
        const redisConfig = JSON.parse(
          EncryptionService.getInstance(
            configManagerConfig.algorithm,
            configManagerConfig.secretKey,
          ).decrypt(encryptedRedisConfig),
        );

        res.status(200).json(redisConfig).end();
      } else {
        res.status(200).json({}).end();
      }
    } catch (error: any) {
      next(error);
    }
  };

export const createKafkaConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { brokers, sasl } = req.body;
      const configManagerConfig = loadConfigurationManagerConfig();
      const encryptedKafkaConfig = EncryptionService.getInstance(
        configManagerConfig.algorithm,
        configManagerConfig.secretKey,
      ).encrypt(JSON.stringify({ brokers, sasl }));
      await keyValueStoreService.set<string>(
        configPaths.broker.kafka,
        encryptedKafkaConfig,
      );
      const warningMessage = res.getHeader('warning');
      res
        .status(200)
        .json({ message: 'Kafka config created successfully', warningMessage })
        .end();
    } catch (error: any) {
      next(error);
    }
  };

export const getKafkaConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const configManagerConfig = loadConfigurationManagerConfig();
      const encryptedKafkaConfig = await keyValueStoreService.get<string>(
        configPaths.broker.kafka,
      );
      if (encryptedKafkaConfig) {
        const kafkaConfig = JSON.parse(
          EncryptionService.getInstance(
            configManagerConfig.algorithm,
            configManagerConfig.secretKey,
          ).decrypt(encryptedKafkaConfig),
        );

        res.status(200).json(kafkaConfig).end();
      } else {
        res.status(200).json({}).end();
      }
    } catch (error: any) {
      next(error);
    }
  };

export const createGoogleWorkspaceCredentials =
  (
    keyValueStoreService: KeyValueStoreService,
    userId: string,
    orgId: string,
    eventService: SyncEventProducer,
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const org = await findActiveOrgById(orgId);
      if (!org) {
        throw new BadRequestError('Organisaton not found');
      }
      const userType = org.accountType;

      let configData;
      const configManagerConfig = loadConfigurationManagerConfig();
      let encryptedGoogleWorkspaceConfig: string;
      switch (userType.toLowerCase()) {
        case googleWorkspaceTypes.INDIVIDUAL.toLowerCase():
          {
            configData = req.body;

            // validate config schema

            const validationResult =
              googleWorkspaceIndividualCredentialsSchema.safeParse(configData);
            if (!validationResult.success) {
              throw new BadRequestError(validationResult.error.message);
            }
            const enableRealTimeUpdates = req.body.enableRealTimeUpdates;
            let topicName = '';
            const realTimeUpdatesEnabled =
              typeof enableRealTimeUpdates === 'string'
                ? enableRealTimeUpdates.toLowerCase() === 'true'
                : !!enableRealTimeUpdates;

            if (realTimeUpdatesEnabled) {
              if (!req.body.topicName) {
                throw new BadRequestError(
                  'Topic name is required when real-time updates are enabled',
                );
              }
              topicName = req.body.topicName;
            }
            const {
              access_token,
              refresh_token,
              access_token_expiry_time,
              refresh_token_expiry_time,
            } = configData;

            encryptedGoogleWorkspaceConfig = EncryptionService.getInstance(
              configManagerConfig.algorithm,
              configManagerConfig.secretKey,
            ).encrypt(
              JSON.stringify({
                access_token,
                refresh_token,
                access_token_expiry_time,
                refresh_token_expiry_time,
                enableRealTimeUpdates: realTimeUpdatesEnabled,
                topicName,
              }),
            );
          }
          await keyValueStoreService.set<string>(
            `${configPaths.connectors.googleWorkspace.credentials.individual}/${userId}`,
            encryptedGoogleWorkspaceConfig,
          );
          break;
        case googleWorkspaceTypes.BUSINESS.toLowerCase(): {
          const fileChanged =
            req.body.fileChanged === true || req.body.fileChanged === 'true';
          let existingConfig = null;
          // validate config schema
          if (!fileChanged) {
            try {
              const encryptedExistingConfig =
                await keyValueStoreService.get<string>(
                  `${configPaths.connectors.googleWorkspace.credentials.business}/${orgId}`,
                );

              if (encryptedExistingConfig) {
                existingConfig = JSON.parse(
                  EncryptionService.getInstance(
                    configManagerConfig.algorithm,
                    configManagerConfig.secretKey,
                  ).decrypt(encryptedExistingConfig),
                );

                // We'll use this existing config later
                logger.debug('Using existing config, file not changed');
              } else {
                // No existing config found, need to validate the new file
                throw new BadRequestError('File Not found');
              }
            } catch (error) {
              throw error;
            }
          }

          // Validate admin email regardless of whether file changed
          if (!req.body.adminEmail) {
            throw new BadRequestError(
              'Google Workspace Admin Email is required',
            );
          }
          const adminEmail = req.body.adminEmail;

          // Process real-time updates settings
          const enableRealTimeUpdates = req.body.enableRealTimeUpdates;
          let topicName = '';
          const realTimeUpdatesEnabled =
            enableRealTimeUpdates === undefined
              ? false
              : typeof enableRealTimeUpdates === 'string'
                ? enableRealTimeUpdates.toLowerCase() === 'true'
                : Boolean(enableRealTimeUpdates);

          if (realTimeUpdatesEnabled) {
            if (!req.body.topicName) {
              throw new BadRequestError(
                'Topic name is required when real-time updates are enabled',
              );
            }
            topicName = req.body.topicName;
          }

          let configData;

          if (existingConfig) {
            if (
              existingConfig.topicName != topicName ||
              existingConfig.enableRealTimeUpdates != realTimeUpdatesEnabled
            ) {
              if (realTimeUpdatesEnabled) {
                await eventService.start();
                const event: Event = {
                  eventType: EventType.GmailUpdatesEnabledEvent,
                  timestamp: Date.now(),
                  payload: {
                    orgId,
                    topicName: req.body.topicName,
                  } as GmailUpdatesEnabledEvent,
                };
                await eventService.publishEvent(event);
                await eventService.stop();
              } else {
                await eventService.start();
                const event: Event = {
                  eventType: EventType.GmailUpdatesDisabledEvent,
                  timestamp: Date.now(),
                  payload: {
                    orgId,
                  } as GmailUpdatesDisabledEvent,
                };
                await eventService.publishEvent(event);
                await eventService.stop();
              }
            }
          } else {
            if (realTimeUpdatesEnabled) {
              await eventService.start();
              const event: Event = {
                eventType: EventType.GmailUpdatesEnabledEvent,
                timestamp: Date.now(),
                payload: {
                  orgId,
                  topicName: req.body.topicName,
                } as GmailUpdatesEnabledEvent,
              };
              await eventService.publishEvent(event);
              await eventService.stop();
            }
          }

          if (fileChanged) {
            // Only validate the file if it's changed
            configData = req.body.fileContent;

            const validationResult =
              googleWorkspaceBusinessCredentialsSchema.safeParse(configData);

            if (!validationResult.success) {
              const formattedErrors = validationResult.error.issues
                .map((err) => {
                  const fieldName = err.path[0] || 'Unknown field';
                  return `  • ${String(fieldName)}: ${err.message}  `;
                })
                .join('');

              const errorMessage = `Google Workspace validation failed:\n${formattedErrors}`;
              throw new BadRequestError(errorMessage);
            }
          } else {
            // Use existing file data but with updated settings
            configData = {
              type: existingConfig.type,
              project_id: existingConfig.project_id,
              private_key_id: existingConfig.private_key_id,
              private_key: existingConfig.private_key,
              client_email: existingConfig.client_email,
              client_id: existingConfig.client_id,
              auth_uri: existingConfig.auth_uri,
              token_uri: existingConfig.token_uri,
              auth_provider_x509_cert_url:
                existingConfig.auth_provider_x509_cert_url,
              client_x509_cert_url: existingConfig.client_x509_cert_url,
              universe_domain: existingConfig.universe_domain,
            };
          }

          // Combine file data with updated settings
          const {
            type,
            project_id,
            private_key_id,
            private_key,
            client_email,
            client_id,
            auth_uri,
            token_uri,
            auth_provider_x509_cert_url,
            client_x509_cert_url,
            universe_domain,
          } = configData;

          // Encrypt and store the updated config
          encryptedGoogleWorkspaceConfig = EncryptionService.getInstance(
            configManagerConfig.algorithm,
            configManagerConfig.secretKey,
          ).encrypt(
            JSON.stringify({
              type,
              project_id,
              private_key_id,
              private_key,
              client_email,
              client_id,
              auth_uri,
              token_uri,
              auth_provider_x509_cert_url,
              client_x509_cert_url,
              universe_domain,
              adminEmail,
              enableRealTimeUpdates: realTimeUpdatesEnabled,
              topicName,
            }),
          );

          await keyValueStoreService.set<string>(
            `${configPaths.connectors.googleWorkspace.credentials.business}/${orgId}`,
            encryptedGoogleWorkspaceConfig,
          );
          break;
        }
        default: {
          throw new BadRequestError(
            `Unsupported google workspace type: ${userType}`,
          );
        }
      }
      res.status(200).json({ message: 'Successfully updated' });
    } catch (error: any) {
      next(error);
    }
  };

export const getGoogleWorkspaceCredentials =
  (keyValueStoreService: KeyValueStoreService, userId: string, orgId: string) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const org = await findActiveOrgById(orgId);
      if (!org) {
        throw new BadRequestError('Organisaton not found');
      }
      const userType = org.accountType;
      const configManagerConfig = loadConfigurationManagerConfig();
      let path;
      let googleWorkspaceCredentials: any;
      let encryptedGoogleWorkspaceCredentials;
      switch (userType.toLowerCase()) {
        case googleWorkspaceTypes.INDIVIDUAL.toLowerCase():
          path = `${configPaths.connectors.googleWorkspace.credentials.individual}/${userId}`;
          const oauthPath = `${configPaths.connectors.googleWorkspace.config}`;

          encryptedGoogleWorkspaceCredentials =
            await keyValueStoreService.get<string>(path);
          const encryptedGoogleWorkspaceOauthConfig =
            await keyValueStoreService.get<string>(oauthPath);

          if (encryptedGoogleWorkspaceOauthConfig) {
            const googleWorkspaceOauthConfig = JSON.parse(
              EncryptionService.getInstance(
                configManagerConfig.algorithm,
                configManagerConfig.secretKey,
              ).decrypt(encryptedGoogleWorkspaceOauthConfig),
            );
            if (encryptedGoogleWorkspaceCredentials) {
              googleWorkspaceCredentials = JSON.parse(
                EncryptionService.getInstance(
                  configManagerConfig.algorithm,
                  configManagerConfig.secretKey,
                ).decrypt(encryptedGoogleWorkspaceCredentials),
              );

              const combinedResponse = {
                ...googleWorkspaceCredentials,
                ...googleWorkspaceOauthConfig,
              };

              res.status(200).json(combinedResponse).end();
            } else {
              res.status(200).json({}).end();
            }
          } else {
            res.status(200).json({}).end();
          }

          break;

        case googleWorkspaceTypes.BUSINESS.toLowerCase():
          path = `${configPaths.connectors.googleWorkspace.credentials.business}/${orgId}`;
          encryptedGoogleWorkspaceCredentials =
            await keyValueStoreService.get<string>(path);
          if (encryptedGoogleWorkspaceCredentials) {
            googleWorkspaceCredentials = JSON.parse(
              EncryptionService.getInstance(
                configManagerConfig.algorithm,
                configManagerConfig.secretKey,
              ).decrypt(encryptedGoogleWorkspaceCredentials),
            );
            res.status(200).json(googleWorkspaceCredentials).end();
          } else {
            res.status(200).json({}).end();
          }
          break;

        default:
          throw new BadRequestError(
            `Unsupported google workspace type: ${userType}`,
          );
      }
    } catch (error: any) {
      next(error);
    }
  };

export const getGoogleWorkspaceBusinessCredentials =
  (keyValueStoreService: KeyValueStoreService, orgId: string) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const configManagerConfig = loadConfigurationManagerConfig();
      let path;
      let googleWorkspaceConfig: any;
      let encryptedGoogleWorkspaceConfig;

      path = `${configPaths.connectors.googleWorkspace.credentials.business}/${orgId}`;
      encryptedGoogleWorkspaceConfig =
        await keyValueStoreService.get<string>(path);
      if (encryptedGoogleWorkspaceConfig) {
        googleWorkspaceConfig = JSON.parse(
          EncryptionService.getInstance(
            configManagerConfig.algorithm,
            configManagerConfig.secretKey,
          ).decrypt(encryptedGoogleWorkspaceConfig),
        );
        res.status(200).json(googleWorkspaceConfig).end();
      } else {
        res.status(200).json({}).end();
      }
    } catch (error: any) {
      next(error);
    }
  };

export const deleteGoogleWorkspaceCredentials =
  (keyValueStoreService: KeyValueStoreService, orgId: string) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const org = await findActiveOrgById(orgId);
      if (!org) {
        throw new BadRequestError('Organisaton not found');
      }
      const userType = org.accountType;
      let path;
      switch (userType.toLowerCase()) {
        case googleWorkspaceTypes.INDIVIDUAL.toLowerCase():
          throw new UnauthorizedError(
            'Deleting credentials fro individual type not allowed',
          );

        case googleWorkspaceTypes.BUSINESS.toLowerCase():
          path = `${configPaths.connectors.googleWorkspace.credentials.business}/${orgId}`;
          await keyValueStoreService.delete(path);
          res.status(200).json({}).end();
          break;

        default:
          throw new BadRequestError(
            `Unsupported google workspace type: ${userType}`,
          );
      }
    } catch (error: any) {
      next(error);
    }
  };
export const setGoogleWorkspaceOauthConfig =
  (
    keyValueStoreService: KeyValueStoreService,
    eventService: SyncEventProducer,
    orgId: string,
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { clientId, clientSecret, enableRealTimeUpdates } = req.body;
      let topicName = '';
      const realTimeUpdatesEnabled =
        enableRealTimeUpdates === undefined
          ? false
          : typeof enableRealTimeUpdates === 'string'
            ? enableRealTimeUpdates.toLowerCase() === 'true'
            : Boolean(enableRealTimeUpdates);

      if (realTimeUpdatesEnabled) {
        if (!req.body.topicName) {
          throw new BadRequestError(
            'Topic name is required when real-time updates are enabled',
          );
        }
        topicName = req.body.topicName;
      }
      const configManagerConfig = loadConfigurationManagerConfig();
      const existingGoogleWorkSpaceConfig =
        await keyValueStoreService.get<string>(
          configPaths.connectors.googleWorkspace.config,
        );
      if (existingGoogleWorkSpaceConfig) {
        const googleWorkSpaceConfig = JSON.parse(
          EncryptionService.getInstance(
            configManagerConfig.algorithm,
            configManagerConfig.secretKey,
          ).decrypt(existingGoogleWorkSpaceConfig),
        );
        if (
          googleWorkSpaceConfig.topicName != topicName ||
          googleWorkSpaceConfig.enableRealTimeUpdates != realTimeUpdatesEnabled
        ) {
          if (realTimeUpdatesEnabled) {
            await eventService.start();
            const event: Event = {
              eventType: EventType.GmailUpdatesEnabledEvent,
              timestamp: Date.now(),
              payload: {
                orgId,
                topicName: req.body.topicName,
              } as GmailUpdatesEnabledEvent,
            };
            await eventService.publishEvent(event);
            await eventService.stop();
          } else {
            await eventService.start();
            const event: Event = {
              eventType: EventType.GmailUpdatesDisabledEvent,
              timestamp: Date.now(),
              payload: {
                orgId,
              } as GmailUpdatesDisabledEvent,
            };
            await eventService.publishEvent(event);
            await eventService.stop();
          }
        }
      } else {
        if (realTimeUpdatesEnabled) {
          await eventService.start();
          const event: Event = {
            eventType: EventType.GmailUpdatesEnabledEvent,
            timestamp: Date.now(),
            payload: {
              orgId,
              topicName: req.body.topicName,
            } as GmailUpdatesEnabledEvent,
          };
          await eventService.publishEvent(event);
          await eventService.stop();
        }
      }

      const encryptedGoogleWorkSpaceConfig = EncryptionService.getInstance(
        configManagerConfig.algorithm,
        configManagerConfig.secretKey,
      ).encrypt(
        JSON.stringify({
          clientId,
          clientSecret,
          enableRealTimeUpdates: realTimeUpdatesEnabled,
          topicName,
        }),
      );
      await keyValueStoreService.set<string>(
        configPaths.connectors.googleWorkspace.config,
        encryptedGoogleWorkSpaceConfig,
      );

      res
        .status(200)
        .json({ message: 'Google Workspace credentials created successfully' });
    } catch (error: any) {
      next(error);
    }
  };

export const getGoogleWorkspaceOauthConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const configManagerConfig = loadConfigurationManagerConfig();
      const encryptedGoogleWorkSpaceConfig =
        await keyValueStoreService.get<string>(
          configPaths.connectors.googleWorkspace.config,
        );
      if (encryptedGoogleWorkSpaceConfig) {
        const googleWorkSpaceConfig = JSON.parse(
          EncryptionService.getInstance(
            configManagerConfig.algorithm,
            configManagerConfig.secretKey,
          ).decrypt(encryptedGoogleWorkSpaceConfig),
        );
        res.status(200).json(googleWorkSpaceConfig).end();
      } else {
        res.status(200).json({}).end();
      }
    } catch (error: any) {
      next(error);
    }
  };

export const getAtlassianOauthConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ) => {
    try {
      const configManagerConfig = loadConfigurationManagerConfig();
      const orgId = getOrgIdFromRequest(req);
      if (!orgId) {
        throw new BadRequestError('Organisaton not found');
      }
      const encryptedAtlassianConfig = await keyValueStoreService.get<string>(
        `${configPaths.connectors.atlassian.config}/${orgId}`,
      );
      if (encryptedAtlassianConfig) {
        const atlassianConfig = JSON.parse(
          EncryptionService.getInstance(
            configManagerConfig.algorithm,
            configManagerConfig.secretKey,
          ).decrypt(encryptedAtlassianConfig),
        );
        res.status(200).json(atlassianConfig).end();
      } else {
        res.status(200).json({}).end();
      }
    } catch (error: any) {
      next(error);
    }
  };

export const setAtlassianOauthConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ) => {
    try {
      const oauthConfig = req.body;
      const orgId = getOrgIdFromRequest(req);
      if (!orgId) {
        throw new BadRequestError('Organisation not found');
      }
      const configManagerConfig = loadConfigurationManagerConfig();
      const encryptedAtlassianConfig = EncryptionService.getInstance(
        configManagerConfig.algorithm,
        configManagerConfig.secretKey,
      ).encrypt(JSON.stringify(oauthConfig));
      await keyValueStoreService.set<string>(
        `${configPaths.connectors.atlassian.config}/${orgId}`,
        encryptedAtlassianConfig,
      );
      res
        .status(200)
        .json({ message: 'Atlassian config created successfully' });
    } catch (error: any) {
      next(error);
    }
  };

export const getAtlassianCredentials =
  (keyValueStoreService: KeyValueStoreService) =>
  async (
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ) => {
    try {
      const configManagerConfig = loadConfigurationManagerConfig();
      const orgId = getOrgIdFromRequest(req);
      if (!orgId) {
        throw new BadRequestError('Organisation not found');
      }
      const encryptedAtlassianCredentials =
        await keyValueStoreService.get<string>(
          `${configPaths.connectors.atlassian.credentials}/${orgId}`,
        );
      if (encryptedAtlassianCredentials) {
        const atlassianCredentials = JSON.parse(
          EncryptionService.getInstance(
            configManagerConfig.algorithm,
            configManagerConfig.secretKey,
          ).decrypt(encryptedAtlassianCredentials),
        );
        res.status(200).json(atlassianCredentials).end();
      } else {
        res.status(200).json({}).end();
      }
    } catch (error: any) {
      next(error);
    }
  };

export const setAtlassianCredentials =
  (keyValueStoreService: KeyValueStoreService) =>
  async (
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ) => {
    try {
      const credentials = req.body;
      const configManagerConfig = loadConfigurationManagerConfig();
      const orgId = getOrgIdFromRequest(req);
      if (!orgId) {
        throw new BadRequestError('Organisation not found');
      }
      // Todo: Do a health check for the credentials
      const encryptedAtlassianCredentials = EncryptionService.getInstance(
        configManagerConfig.algorithm,
        configManagerConfig.secretKey,
      ).encrypt(JSON.stringify(credentials));
      await keyValueStoreService.set<string>(
        `${configPaths.connectors.atlassian.credentials}/${orgId}`,
        encryptedAtlassianCredentials,
      );
      res
        .status(200)
        .json({ message: 'Atlassian credentials created successfully' });
    } catch (error: any) {
      next(error);
    }
  };

export const getOneDriveCredentials =
  (keyValueStoreService: KeyValueStoreService) =>
  async (
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ) => {
    try {
      const configManagerConfig = loadConfigurationManagerConfig();
      const orgId = getOrgIdFromRequest(req);
      if (!orgId) {
        throw new BadRequestError('Organisation not found');
      }
      const encryptedOneDriveCredentials =
        await keyValueStoreService.get<string>(
          `${configPaths.connectors.onedrive.config}/${orgId}`,
        );
      if (encryptedOneDriveCredentials) {
        const oneDriveCredentials = JSON.parse(
          EncryptionService.getInstance(
            configManagerConfig.algorithm,
            configManagerConfig.secretKey,
          ).decrypt(encryptedOneDriveCredentials),
        );
        res.status(200).json(oneDriveCredentials).end();
      } else {
        res.status(200).json({}).end();
      }
    } catch (error: any) {
      next(error);
    }
  };

export const setOneDriveCredentials =
  (keyValueStoreService: KeyValueStoreService) =>
  async (
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ) => {
    try {
      const credentials = req.body;
      const configManagerConfig = loadConfigurationManagerConfig();
      const orgId = getOrgIdFromRequest(req);
      if (!orgId) {
        throw new BadRequestError('Organisation not found');
      }
      // Todo: Do a health check for the credentials
      const encryptedOneDriveCredentials = EncryptionService.getInstance(
        configManagerConfig.algorithm,
        configManagerConfig.secretKey,
      ).encrypt(JSON.stringify(credentials));
      await keyValueStoreService.set<string>(
        `${configPaths.connectors.onedrive.config}/${orgId}`,
        encryptedOneDriveCredentials,
      );
      res
        .status(200)
        .json({ message: 'OneDrive credentials created successfully' });
    } catch (error: any) {
      next(error);
    }
  };

export const getSharePointCredentials =
  (keyValueStoreService: KeyValueStoreService) =>
  async (
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ) => {
    try {
      const configManagerConfig = loadConfigurationManagerConfig();
      const orgId = getOrgIdFromRequest(req);
      if (!orgId) {
        throw new BadRequestError('Organisation not found');
      }
      const encryptedSharePointCredentials =
        await keyValueStoreService.get<string>(
          `${configPaths.connectors.sharepoint.config}/${orgId}`,
        );
      if (encryptedSharePointCredentials) {
        const sharePointCredentials = JSON.parse(
          EncryptionService.getInstance(
            configManagerConfig.algorithm,
            configManagerConfig.secretKey,
          ).decrypt(encryptedSharePointCredentials),
        );
        res.status(200).json(sharePointCredentials).end();
      } else {
        res.status(200).json({}).end();
      }
    } catch (error: any) {
      next(error);
    }
  };

export const setSharePointCredentials =
  (keyValueStoreService: KeyValueStoreService) =>
  async (
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ) => {
    try {
      const credentials = req.body;
      const configManagerConfig = loadConfigurationManagerConfig();
      const orgId = getOrgIdFromRequest(req);
      if (!orgId) {
        throw new BadRequestError('Organisation not found');
      }
      // Todo: Do a health check for the credentials
      const encryptedSharePointCredentials = EncryptionService.getInstance(
        configManagerConfig.algorithm,
        configManagerConfig.secretKey,
      ).encrypt(JSON.stringify(credentials));
      await keyValueStoreService.set<string>(
        `${configPaths.connectors.sharepoint.config}/${orgId}`,
        encryptedSharePointCredentials,
      );
      res
        .status(200)
        .json({ message: 'SharePoint credentials created successfully' });
    } catch (error: any) {
      next(error);
    }
  };

export const createQdrantConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { port, apiKey, host, grpcPort } = req.body;
      const configManagerConfig = loadConfigurationManagerConfig();
      const encryptedQdrantConfig = EncryptionService.getInstance(
        configManagerConfig.algorithm,
        configManagerConfig.secretKey,
      ).encrypt(JSON.stringify({ port, apiKey, host, grpcPort }));
      await keyValueStoreService.set<string>(
        configPaths.db.qdrant,
        encryptedQdrantConfig,
      );
      const warningMessage = res.getHeader('Warning');
      res.status(200).json({
        message: 'Qdrant config created successfully',
        warningMessage,
      });
    } catch (error: any) {
      next(error);
    }
  };

export const getQdrantConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const configManagerConfig = loadConfigurationManagerConfig();
      const encryptedQdrantConfig = await keyValueStoreService.get<string>(
        configPaths.db.qdrant,
      );
      if (encryptedQdrantConfig) {
        const qdrantConfig = JSON.parse(
          EncryptionService.getInstance(
            configManagerConfig.algorithm,
            configManagerConfig.secretKey,
          ).decrypt(encryptedQdrantConfig),
        );
        res.status(200).json(qdrantConfig).end();
        return;
      } else {
        res.status(200).json({}).end();
      }
    } catch (error: any) {
      next(error);
    }
  };
export const getFrontendUrl =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const url =
        (await keyValueStoreService.get<string>(configPaths.endpoint)) || '{}';
      const parsedUrl = parseKvObject(url);
      if (parsedUrl?.frontend?.publicEndpoint) {
        res
          .status(200)
          .json({ url: parsedUrl?.frontend?.publicEndpoint })
          .end();
        return;
      } else {
        res.status(200).json({}).end();
      }
    } catch (error: any) {
      next(error);
    }
  };

/**
 * The configured public frontend URL, for the desktop app only.
 *
 * The desktop app's OAuth redirect URI has to point at the frontend origin,
 * but the app knows only the API base URL the user typed, and those are
 * different origins whenever the UI is served separately from the API. It
 * calls this once as the sign-in screen loads, before any session exists.
 *
 * The `client-name: desktop` check is not a security boundary: the header is
 * client-supplied, and the desktop app is a public client, so nothing it
 * ships could prove its identity. It only keeps the value off responses to
 * browsers, which have `window.location` and never need it.
 *
 * Resolved the same way as `AppConfig.frontendUrl`, which GitHub's
 * authorization code is redeemed against, so the two cannot drift apart. Read
 * per request because this module's `AppConfig` is not reloaded when an admin
 * changes the URL.
 */
export const getDesktopFrontendUrl =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: Request, res: Response, next: NextFunction) => {
    try {
      if (req.headers['client-name'] !== 'desktop') {
        throw new ForbiddenError(
          'This endpoint is only available to the PipesHub desktop app.',
        );
      }
      const urls =
        (await keyValueStoreService.get<string>(configPaths.endpoint)) || '{}';
      const parsedUrls = parseKvObject(urls);
      res.status(200).json({
        frontendUrl: resolveFrontendPublicUrl(
          parsedUrls?.frontend?.publicEndpoint,
        ),
      });
    } catch (error) {
      next(error);
    }
  };

export const setFrontendUrl =
  (
    keyValueStoreService: KeyValueStoreService,
    scopedJwtSecret: string,
    configService: ConfigService,
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      if (!req.user) {
        throw new NotFoundError('User not found');
      }
      const { url } = req.body;
      const normalizedUrl = normalizeUrl(url);
      if (!normalizedUrl) {
        throw new BadRequestError('Invalid URL');
      }
      try {
        new URL(normalizedUrl);
      } catch (e) {
        throw new BadRequestError(
          'Invalid URL format. A protocol (e.g., http://) is required.',
        );
      }
      const urls =
        (await keyValueStoreService.get<string>(configPaths.endpoint)) || '{}';
      let parsedUrls = parseKvObject(urls);
      // Preserve existing `auth` object if it exists, otherwise create a new one
      parsedUrls.frontend = {
        ...parsedUrls.frontend,
        publicEndpoint: normalizedUrl,
      };
      // Save the updated object back to configPaths.endpoint
      await keyValueStoreService.set<string>(
        configPaths.endpoint,
        JSON.stringify(parsedUrls),
      );

      const scopedToken = await generateFetchConfigAuthToken(
        req.user,
        scopedJwtSecret,
      );
      const response = await configService.updateConfig(scopedToken);
      if (response.statusCode != 200) {
        throw new BadRequestError('Error updating configs');
      }
      res.status(200).json({
        message: 'Frontend Url saved successfully',
      });
    } catch (error: any) {
      next(error);
    }
  };

export const getConnectorPublicUrl =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const url =
        (await keyValueStoreService.get<string>(configPaths.endpoint)) || '{}';
      const parsedUrl = parseKvObject(url);
      if (parsedUrl?.connectors?.publicEndpoint) {
        res
          .status(200)
          .json({ url: parsedUrl?.connectors?.publicEndpoint })
          .end();
        return;
      } else {
        res.status(200).json({}).end();
      }
    } catch (error: any) {
      next(error);
    }
  };

export const setConnectorPublicUrl =
  (
    keyValueStoreService: KeyValueStoreService,
    eventService: SyncEventProducer,
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      if (!req.user) {
        throw new NotFoundError('User not found');
      }
      const { url } = req.body;
      const normalizedUrl = normalizeUrl(url);
      if (!normalizedUrl) {
        throw new BadRequestError('Invalid URL');
      }
      try {
        new URL(normalizedUrl);
      } catch (e) {
        throw new BadRequestError(
          'Invalid URL format. A protocol (e.g., http://) is required.',
        );
      }
      const urls =
        (await keyValueStoreService.get<string>(configPaths.endpoint)) || '{}';

      let parsedUrls = parseKvObject(urls);

      // Preserve existing `auth` object if it exists, otherwise create a new one
      parsedUrls.connectors = {
        ...parsedUrls.connectors,
        publicEndpoint: normalizedUrl,
      };

      // Save the updated object back to configPaths.endpoint
      await keyValueStoreService.set<string>(
        configPaths.endpoint,
        JSON.stringify(parsedUrls),
      );

      await eventService.start();
      let event: Event = {
        eventType: EventType.ConnectorPublicUrlChangedEvent,
        timestamp: Date.now(),
        payload: {
          url,
          orgId: req.user.orgId,
        } as ConnectorPublicUrlChangedEvent,
      };
      await eventService.publishEvent(event);

      res.status(200).json({
        message: 'Connector Url saved successfully',
      });
    } catch (error: any) {
      next(error);
    }
  };

const getMetricsCollectionConfig = async (
  keyValueStoreService: KeyValueStoreService,
): Promise<Record<string, any>> => {
  const configManagerConfig = loadConfigurationManagerConfig();
  const encrypted = await keyValueStoreService.get<string>(
    configPaths.metricsCollection,
  );
  if (!encrypted) {
    return {};
  }
  let decrypted: string;
  try {
    decrypted = EncryptionService.getInstance(
      configManagerConfig.algorithm,
      configManagerConfig.secretKey,
    ).decrypt(encrypted);
  } catch {
    // Value may predate encryption (migration/local dev); try it as-is.
    decrypted = encrypted;
  }
  try {
    const parsed = JSON.parse(decrypted);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (error) {
    logger.warn('Failed to parse metrics collection config, using empty config', {
      error: safeCause(error),
    });
    return {};
  }
};

const setMetricsCollectionConfig = async (
  keyValueStoreService: KeyValueStoreService,
  config: Record<string, any>,
): Promise<void> => {
  const configManagerConfig = loadConfigurationManagerConfig();
  const encrypted = EncryptionService.getInstance(
    configManagerConfig.algorithm,
    configManagerConfig.secretKey,
  ).encrypt(JSON.stringify(config));
  await keyValueStoreService.set<string>(
    configPaths.metricsCollection,
    encrypted,
  );
};

export const toggleMetricsCollection =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { enableMetricCollection } = req.body;
      const metricsCollection = await getMetricsCollectionConfig(
        keyValueStoreService,
      );

      const enabled = !(
        enableMetricCollection === false || enableMetricCollection === 'false'
      );
      setMetricCollectionEnabled(normalizeOrgId(req.user?.orgId), enabled);

      if (!enabled) {
        await TelemetryService.current()?.flush();
      }

      if (enableMetricCollection !== metricsCollection.enableMetricCollection) {
        metricsCollection.enableMetricCollection = enableMetricCollection;
        await setMetricsCollectionConfig(keyValueStoreService, metricsCollection);
      }

      res
        .status(200)
        .json({ message: 'Metrics collection toggled successfully' });
    } catch (error: any) {
      next(error);
    }
  };

export const getMetricsCollection =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const metricsCollection = await getMetricsCollectionConfig(
        keyValueStoreService,
      );
      res.status(200).json(metricsCollection).end();
    } catch (error: any) {
      next(error);
    }
  };

export const setMetricsCollectionPushInterval =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { pushIntervalMs } = req.body;

      const metricsCollection = await getMetricsCollectionConfig(
        keyValueStoreService,
      );

      if (pushIntervalMs !== metricsCollection.pushIntervalMs) {
        metricsCollection.pushIntervalMs = pushIntervalMs;
        await setMetricsCollectionConfig(keyValueStoreService, metricsCollection);
      }
      res
        .status(200)
        .json({ message: 'Metrics collection push interval set successfully' });
    } catch (error: any) {
      next(error);
    }
  };

export const setMetricsCollectionRemoteServer =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { serverUrl } = req.body;
      const metricsCollection = await getMetricsCollectionConfig(
        keyValueStoreService,
      );
      if (serverUrl !== metricsCollection.serverUrl) {
        metricsCollection.serverUrl = serverUrl;
        await setMetricsCollectionConfig(keyValueStoreService, metricsCollection);
      }
      res
        .status(200)
        .json({ message: 'Metrics collection remote server set successfully' });
    } catch (error: any) {
      next(error);
    }
  };

async function sendEvent(eventService: EntitiesEventProducer | AiConfigEventProducer, event: Event) {
  try {
    await eventService.start();
    await eventService.publishEvent(event);
    await eventService.stop();
  } catch (error) {
    logger.error('broker.event.publish_failed', {
      event_type: event.eventType,
      error,
    });
  }
}

const MODEL_TYPES_WITHOUT_BULK_HEALTH_CHECK = [
  'ocr',
  'slm',
  'reasoning',
  'multiModal',
  'imageGeneration',
  'tts',
  'stt',
  'decision',
] as const;

/**
 * Throws unless a bulk health check answered 200. A 400 means the settings are
 * wrong (an empty or rejected key, an endpoint the deployment may not call), so
 * its message, written for the admin, is passed on as a 400; anything else is
 * ours and stays a 500.
 */
const throwUnlessHealthy = (
  response: AIServiceResponse | undefined,
  fallback: string,
): void => {
  if (response?.data && response.statusCode === 200) return;
  if (response?.statusCode === 400) {
    throw new BadRequestError(
      healthCheckFailureMessage(response.data, fallback),
    );
  }
  throw withCause(new InternalServerError(fallback), {
    status: response?.statusCode,
  });
};

export const createAIModelsConfig =
  (
    keyValueStoreService: KeyValueStoreService,
    eventService: AiConfigEventProducer,
    appConfig: AppConfig,
    save: AiModelsSave = plainAiModelsSave(keyValueStoreService),
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const aiConfig = req.body;
      if (!aiConfig) {
        throw new BadRequestError('Invalid configuration passed');
      }
      aiConfig.llm = aiConfig.llm ?? [];
      aiConfig.embedding = aiConfig.embedding ?? [];

      // Handle LLM health check
      if (aiConfig.llm.length > 0) {
        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/llm-health-check`,
          method: HttpMethod.POST,
          headers: req.headers as Record<string, string>,
          body: aiConfig.llm,
        };

        // Don't use nested try/catch with next() inside
        const aiServiceCommand = new AIServiceCommand(aiCommandOptions);
        const aiResponseData =
          (await aiServiceCommand.execute()) as AIServiceResponse;

        throwUnlessHealthy(
          aiResponseData,
          'Failed to do health check of llm configuration, check credentials again',
        );
      }

      // Handle embedding health check
      if (aiConfig.embedding.length > 0) {
        const aiCommandOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/embedding-health-check`,
          method: HttpMethod.POST,
          headers: req.headers as Record<string, string>,
          body: aiConfig.embedding,
        };

        // Don't use nested try/catch with next() inside
        const aiServiceCommand = new AIServiceCommand(aiCommandOptions);
        const aiResponseData =
          (await aiServiceCommand.execute()) as AIServiceResponse;

        if (isEmbeddingInUseRefusal(aiResponseData)) {
          throw new BadRequestError(embeddingRefusalMessage(aiResponseData));
        }
        throwUnlessHealthy(
          aiResponseData,
          'Failed to do health check of embedding configuration, check credentials again',
        );
      }

      // The llm and embedding health checks above refuse an endpoint the
      // deployment may not call; every other model type is checked here.
      const otherModels = MODEL_TYPES_WITHOUT_BULK_HEALTH_CHECK.flatMap(
        (modelType) => aiConfig[modelType] ?? [],
      );
      if (otherModels.length > 0) {
        const endpointCheck = (await new AIServiceCommand({
          uri: `${appConfig.aiBackend}/api/v1/model-endpoint-check`,
          method: HttpMethod.POST,
          headers: req.headers as Record<string, string>,
          body: otherModels,
        }).execute()) as AIServiceResponse;

        throwUnlessHealthy(
          endpointCheck,
          'Failed to check the model endpoints, try again',
        );
      }

      if (aiConfig.llm.length > 0) {
        aiConfig.llm.forEach((llm: any, index: number) => {
          const modelKey = randomUUID();
          llm.modelKey = modelKey;
          // Keep what the caller declared and the health check just verified.
          // These used to be forced to false here, so a vision model onboarded
          // through this route was registered text-only and never sent an
          // image, whatever the health check had proved.
          llm.isMultimodal = llm.isMultimodal ?? false;
          llm.isReasoning = llm.isReasoning ?? false;
          llm.isDefault = index === 0;
        });
      }

      if (aiConfig.embedding.length > 0) {
        aiConfig.embedding.forEach((embedding: any, index: number) => {
          const modelKey = randomUUID();
          embedding.modelKey = modelKey;
          embedding.isMultimodal = embedding.isMultimodal ?? false;
          embedding.isDefault = index === 0;
        });
      }

      await aiModelsRepository(keyValueStoreService, save, req, [
        'embedding',
        'llm',
      ]).replace(aiConfig);

      // Notify other services about the new AI config. The initial config
      // may include LLM and/or embedding models; fire a separate event per
      // model type so downstream caches (e.g. the Python retrieval service's
      // embedding instance) get invalidated appropriately.
      if (aiConfig.llm.length > 0) {
        const llmEvent: Event = {
          eventType: EventType.LLMConfiguredEvent,
          timestamp: Date.now(),
          payload: {
            credentialsRoute: `${appConfig.cmBackend}/${aiModelRoute}`,
          } as LLMConfiguredEvent,
        };
        await sendEvent(eventService, llmEvent);
      }

      if (aiConfig.embedding.length > 0) {
        const embeddingEvent: Event = {
          eventType: EventType.EmbeddingModelConfiguredEvent,
          timestamp: Date.now(),
          payload: {
            credentialsRoute: `${appConfig.cmBackend}/${aiModelRoute}`,
          } as EmbeddingModelConfiguredEvent,
        };
        await sendEvent(eventService, embeddingEvent);
      }

      res.status(200).json({ message: 'AI config created successfully' }).end();
    } catch (error: any) {
      next(error);
    }
  };

export const getAIModelsConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const aiConfig = await readStoredAiModelsConfig(keyValueStoreService);
      res
        .status(200)
        // A config saved before a model type existed has no bucket for it.
        .json(
          aiConfig
            ? stripAiModelsStoredConfig(ensureAiModelBuckets(aiConfig))
            : {},
        )
        .end();
    } catch (error: any) {
      next(error);
    }
  };

export const getInternalAIModelsConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedServiceRequest, res: Response, next: NextFunction) => {
    try {
      const aiConfig = await readStoredAiModelsConfig(keyValueStoreService);
      res.status(200).json(aiConfig ?? {}).end();
    } catch (error: any) {
      next(error);
    }
  };

// AI Models Provider Management Functions (Direct Node.js Implementation)
export const getAIModelsProviders =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const aiModels = await new AiModelsConfigRepository(keyValueStoreService).read();

      if (!aiModels) {
        res.status(200).json({
          status: 'success',
          models: {
            ocr: [],
            embedding: [],
            slm: [],
            llm: [],
            reasoning: [],
            multiModal: [],
            imageGeneration: [],
            tts: [],
            stt: [],
            decision: [],
          },
          message: 'No AI models found',
        });
        return;
      }

      for (const key of AI_MODEL_TYPES) {
        if (!aiModels[key]) {
          aiModels[key] = [];
        }
      }
      if (!aiModels.modelRoles) {
        aiModels.modelRoles = {};
      }

      res.status(200).json({
        status: 'success',
        models: stripAiModelsStoredConfig(aiModels),
        message: 'AI models retrieved successfully',
      });
    } catch (error: any) {
      next(error);
    }
  };

export const getModelsByType =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { modelType } = req.params;
      if (!modelType) {
        res.status(400).json({
          status: 'error',
          message: 'modelType is required',
        });
        return;
      }
      if (!isAIModelType(modelType)) {
        res.status(400).json({
          status: 'error',
          message: `Invalid model type. Must be one of: ${AI_MODEL_TYPES.join(', ')}`,
        });
        return;
      }
      const aiModels = await new AiModelsConfigRepository(keyValueStoreService).read();

      if (!aiModels) {
        res.status(200).json({
          status: 'success',
          models: [],
          message: `No ${modelType} models found`,
        });
        return;
      }

      if (!aiModels[modelType]) {
        res.status(200).json({
          status: 'success',
          models: [],
          message: `No ${modelType} models found`,
        });
        return;
      }
      const configs = aiModels[modelType] as AIModelConfiguration[];
      const reveal = canRevealSecrets(req);
      res.status(200).json({
        status: 'success',
        models: reveal ? configs : configs.map((c) => stripAiModelSecrets(c)),
        message: `Found ${configs.length} ${modelType} models`,
      });
    } catch (error: any) {
      next(error);
    }
  };

// Read the way the query service's `model_default_reasoning_effort` reads it:
// `configuration` first, then a top-level key. Blank or unknown values aren't returned.
function storedDefaultReasoningEffort(config: Record<string, unknown>): string | undefined {
  const configuration = config.configuration as Record<string, unknown> | undefined;
  for (const raw of [configuration?.defaultReasoningEffort, config.defaultReasoningEffort]) {
    const value = typeof raw === 'string' ? raw.trim() : '';
    if (value) {
      return (REASONING_EFFORT_VALUES as readonly string[]).includes(value) ? value : undefined;
    }
  }
  return undefined;
}

export const getAvailableModelsByType =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { modelType } = req.params;
      if (!modelType) {
        res.status(400).json({
          status: 'error',
          message: 'modelType is required',
        });
        return;
      }
      if (!isAIModelType(modelType)) {
        res.status(400).json({
          status: 'error',
          message: `Invalid model type. Must be one of: ${AI_MODEL_TYPES.join(', ')}`,
        });
        return;
      }

      const aiModels = await new AiModelsConfigRepository(keyValueStoreService).read();

      if (!aiModels) {
        res.status(200).json({
          status: 'success',
          models: [],
          message: `No ${modelType} models found`,
        });
        return;
      }

      if (!aiModels[modelType]) {
        res.status(200).json({
          status: 'success',
          models: [],
          message: `No ${modelType} models found`,
        });
        return;
      }

      const configs = aiModels[modelType];
      const flattenedModels = [];

      // This response is built from name/flag fields only — no `configuration`
      // object is ever emitted, so there is nothing to strip here.
      for (const config of configs) {
        // Extract individual model names from comma-separated string
        let modelNames: string[] = [];
        const configurationObj = config.configuration as Record<string, unknown> | undefined;
        if (configurationObj?.model && typeof configurationObj.model === 'string') {
          modelNames = configurationObj.model
            .split(',')
            .map((name: string) => name.trim())
            .filter(Boolean);
        }

        // Create a flattened entry for each individual model
        let markDefault = config.isDefault === true;

        // Only when the admin named it: a client falls back to the model name itself.
        const hasFriendlyName =
          typeof config.modelFriendlyName === 'string' &&
          config.modelFriendlyName.trim().length > 0;
        // Unlike the friendly name, the default applies to every model in the entry.
        const defaultReasoningEffort = storedDefaultReasoningEffort(config);

        for (const modelName of modelNames) {
          const flattenedModel = {
            modelType,
            provider: config.provider,
            modelName,
            modelKey: config.modelKey,
            isMultimodal: config.isMultimodal || false,
            isReasoning: config.isReasoning || false,
            isDefault: markDefault,
            ...(hasFriendlyName && {
              modelFriendlyName: modelDisplayName(config, modelName),
            }),
            ...(defaultReasoningEffort && { defaultReasoningEffort }),
          };
          markDefault = false; // Only mark first model as default
          flattenedModels.push(flattenedModel);
        }
      }

      res.status(200).json({
        status: 'success',
        models: flattenedModels,
        message: `Found ${flattenedModels.length} ${modelType} models`,
      });
      return;
    } catch (error: any) {
      next(error);
    }
  };

// Mirrors the huggingface_hub repo-id shape (`owner/name` or a bare model
// id) that the embedding server accepts, so a malformed value fails fast in
// Node.js instead of reaching the Python service or the filesystem cache.
const MODEL_NAME_PATTERN = /^[\w.-]+(\/[\w.-]+)?$/;

function resolveEmbeddingServerUrl(): string {
  return (process.env.EMBEDDING_SERVER_URL || 'http://localhost:8002').replace(
    /\/v1\/?$/,
    '',
  );
}

// Kicks off a non-blocking download/load on the embedding server and
// returns immediately with its status. Callers then poll
// `streamEmbeddingDownloadProgress` instead of blocking a single request on
// a multi-GB download — that is what previously tripped both the axios
// timeout here and the health-check timeout downstream.
export const prepareEmbeddingModel =
  () =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { model, trustRemoteCode = false } = req.body;

      if (!model || typeof model !== 'string' || !MODEL_NAME_PATTERN.test(model)) {
        res.status(400).json({
          status: 'error',
          message: 'A valid model name is required',
        });
        return;
      }

      const embeddingServerUrl = resolveEmbeddingServerUrl();
      const response = await axios.post(
        `${embeddingServerUrl}/prepare-model`,
        { model, trust_remote_code: Boolean(trustRemoteCode) },
        { timeout: 5000 },
      );

      res.status(response.status).json(response.data);
    } catch (error: any) {
      if (error.response) {
        res.status(error.response.status).json(error.response.data);
        return;
      }
      next(error);
    }
  };

// Server-Sent Events proxy: polls the embedding server's download-progress
// endpoint and forwards each snapshot to the browser so the config dialog
// can render a live progress bar instead of an opaque spinner.
export const streamEmbeddingDownloadProgress =
  () =>
  async (req: AuthenticatedUserRequest, res: Response, _next: NextFunction) => {
    const model = req.query.model;
    if (!model || typeof model !== 'string' || !MODEL_NAME_PATTERN.test(model)) {
      res.status(400).json({
        status: 'error',
        message: 'A valid model query parameter is required',
      });
      return;
    }

    res.writeHead(200, {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-cache',
      Connection: 'keep-alive',
      'X-Accel-Buffering': 'no',
    });

    const embeddingServerUrl = resolveEmbeddingServerUrl();
    const encodedModel = encodeURIComponent(model);
    let closed = false;

    req.on('close', () => {
      closed = true;
    });

    while (!closed) {
      try {
        const response = await axios.get(
          `${embeddingServerUrl}/download-progress/${encodedModel}`,
          { timeout: 5000 },
        );
        const payload = { ...response.data, timestamp: Date.now() };
        if (!closed) {
          res.write(`event: progress\ndata: ${JSON.stringify(payload)}\n\n`);
        }

        if (payload.status === 'ready' || payload.status === 'failed') {
          break;
        }
      } catch (error: any) {
        logger.warn('Lost connection to embedding server', { error });
        if (!closed) {
          res.write(
            `event: progress\ndata: ${JSON.stringify({
              model,
              status: 'failed',
              progress: 0,
              downloaded_bytes: 0,
              total_bytes: 0,
              error: 'Lost connection to embedding server',
              timestamp: Date.now(),
            })}\n\n`,
          );
        }
        break;
      }

      await new Promise((resolve) => setTimeout(resolve, 3000));
    }

    if (!closed) {
      res.end();
    }
  };

// The model the Python services embed with: the default one, else the first
// (retrieval_service.get_embedding_model_instance, VectorStore). null means
// none is configured and the built-in model embeds.
const activeEmbeddingModel = (
  configs: unknown,
): AIModelConfiguration | null => {
  if (!Array.isArray(configs)) return null;
  const models = configs as AIModelConfiguration[];
  return models.find((config) => config.isDefault) ?? models[0] ?? null;
};

const EMBEDDING_MODEL_IN_USE_MESSAGE =
  'This model is embedding your indexed content. Delete the embeddings in Labs first, then change or delete the model and re-embed.';

// Asks the AI service whether `nextActive` (null: the built-in model) may take
// over embedding. It answers 400 while the vector store holds vectors from
// another model, and rebuilds an empty store at the new model's size.
const checkEmbeddingModelTakeover = async (
  nextActive: AIModelConfiguration | null,
  req: AuthenticatedUserRequest,
  appConfig: AppConfig,
): Promise<AIServiceResponse> => {
  const body = nextActive
    ? [
        {
          provider: nextActive.provider,
          configuration: nextActive.configuration,
          modelType: 'embedding',
          isMultimodal: nextActive.isMultimodal ?? false,
          isReasoning: nextActive.isReasoning ?? false,
          isDefault: true,
          contextLength: nextActive.contextLength ?? null,
          ...(nextActive.modelFriendlyName && {
            modelFriendlyName: nextActive.modelFriendlyName,
          }),
        },
      ]
    : [];
  const command = new AIServiceCommand({
    uri: `${appConfig.aiBackend}/api/v1/embedding-health-check`,
    method: HttpMethod.POST,
    headers: req.headers as Record<string, string>,
    body,
  });
  return (await command.execute()) as AIServiceResponse;
};

// The AI service refuses an embedding change with a 400 marked "not healthy"
// only while the vector store holds vectors from another model. Its other 400s
// ("error": a bad model name, a dimensions override, a failed image probe) are
// the model's own setup problems and keep their own text.
const isEmbeddingInUseRefusal = (
  response: AIServiceResponse | undefined,
): boolean =>
  response?.statusCode === 400 && response.data?.status === 'not healthy';

// A vector store that cannot change model in place (pgvector) says so with its
// own text; delete-and-re-embed would not help there, so that text is shown.
export const embeddingRefusalMessage = (
  response: AIServiceResponse | undefined,
): string => {
  const data = response?.data as { code?: string; error?: unknown } | undefined;
  return data?.code === 'embedding_change_unsupported' &&
    typeof data.error === 'string'
    ? data.error
    : EMBEDDING_MODEL_IN_USE_MESSAGE;
};

// Deleting the only embedding model hands embedding to the built-in model, a
// different model. Adding the same model from another provider first keeps the
// stored vectors, so that comes first; only a different model needs Labs cleared,
// because the takeover check refuses it while its vectors are stored.
const LAST_EMBEDDING_MODEL_MESSAGE =
  'This is the only embedding model, so deleting it would switch embedding to the built-in model, which cannot use the vectors already stored. To keep your embeddings, first add the same model from another provider, at the same size, then delete this one. To use a different model, delete the embeddings in Labs first, then change or delete this model and re-embed.';

const lastEmbeddingModelRefusalMessage = (
  response: AIServiceResponse | undefined,
): string => {
  const message = embeddingRefusalMessage(response);
  return message === EMBEDDING_MODEL_IN_USE_MESSAGE
    ? LAST_EMBEDDING_MODEL_MESSAGE
    : message;
};

const takeoverRefusalMessage = (
  takeover: AIServiceResponse,
  fallback: string,
): string =>
  isEmbeddingInUseRefusal(takeover)
    ? embeddingRefusalMessage(takeover)
    : healthCheckFailureMessage(takeover.data, fallback);

// The AI service answers a failed check as `{ message }`, `{ error: text | { message } }`, or
// FastAPI's `{ detail: text | { message } }`, and a body of plain text in places. Each of them
// carries the reason the admin needs; a shape not read here showed only the fallback.
const healthCheckFailureMessage = (data: unknown, fallback: string): string => {
  const textOf = (value: unknown): string | undefined => {
    const text =
      value !== null && typeof value === 'object'
        ? (value as { message?: unknown }).message
        : value;
    return typeof text === 'string' && text.trim() !== ''
      ? text.trim()
      : undefined;
  };
  if (data === null || typeof data !== 'object') {
    return textOf(data) ?? fallback;
  }
  const body = data as { message?: unknown; error?: unknown; detail?: unknown };
  return (
    textOf(body.message) ??
    textOf(body.error) ??
    textOf(body.detail) ??
    fallback
  );
};

export const addAIModelProvider =
  (
    keyValueStoreService: KeyValueStoreService,
    eventService: AiConfigEventProducer,
    appConfig: AppConfig,
    save: AiModelsSave = plainAiModelsSave(keyValueStoreService),
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

      // Validate required fields
      if (!modelType || !provider || !configuration) {
        res.status(400).json({
          status: 'error',
          message: 'modelType, provider, and configuration are required',
        });
        return;
      }

      if (!isAIModelType(modelType)) {
        res.status(400).json({
          status: 'error',
          message: `Invalid model type. Must be one of: ${AI_MODEL_TYPES.join(', ')}`,
        });
        return;
      }

      const healthCheckPayload: Record<string, unknown> = {
        provider,
        configuration,
        modelType,
        isMultimodal,
        isDefault,
        isReasoning,
        contextLength,
      };
      if (modelType === 'embedding') {
        // Only the model that will embed may reshape the vector store; any
        // other is checked for health alone.
        healthCheckPayload.becomesActive =
          Boolean(isDefault) ||
          activeEmbeddingModel(
            (await readStoredAiModelsConfig(keyValueStoreService))?.embedding,
          ) === null;
      }

      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/health-check/${encodeURIComponent(String(modelType))}`,
        method: HttpMethod.POST,
        headers: req.headers as Record<string, string>,
        body: healthCheckPayload,
      };

      // Don't use nested try/catch with next() inside
      const aiServiceCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponseData =
        (await aiServiceCommand.execute()) as AIServiceResponse;

      if (!aiResponseData?.data || aiResponseData.statusCode !== 200) {
        const errData: any = aiResponseData?.data ?? {};
        const reasonMessage =
          modelType === 'embedding' && isEmbeddingInUseRefusal(aiResponseData)
            ? embeddingRefusalMessage(aiResponseData)
            : healthCheckFailureMessage(
                errData,
                `Failed to do health check of ${modelType} configuration, check credentials again`,
              );

        // The reason is written for the admin filling in the dialog ("Incorrect
        // API key provided"); the provider's text is never logged.
        logger.warn('ai_model.health_check.failed', {
          model_type: modelType,
          provider,
          status: aiResponseData?.statusCode,
        });
        res.status(aiResponseData?.statusCode ?? 500).json({
          error: {
            status: 'error',
            message: reasonMessage,
          },
        });
        return;
      }

      const modelFriendlyName = configuration.modelFriendlyName;
      let modelKey = '';
      await aiModelsRepository(keyValueStoreService, save, req, [
        modelType,
      ]).mutate((aiModels) => {
        const existingKeys = new Set(
          AI_MODEL_TYPES.flatMap((type) =>
            (aiModels[type] ?? []).map((config) => config.modelKey),
          ),
        );
        do {
          modelKey = randomUUID();
        } while (existingKeys.has(modelKey));

        const newConfig = {
          provider,
          configuration,
          modelKey,
          isMultimodal,
          isDefault,
          isReasoning,
          contextLength,
          ...(modelFriendlyName && { modelFriendlyName }),
        };

        if (isDefault) {
          for (const config of aiModels[modelType] ?? []) {
            config.isDefault = false;
          }
        }
        aiModels[modelType]!.push(newConfig);
      });

      // Emit an event specific to the model type so downstream services
      // refresh the right cache. Embedding changes MUST NOT use the LLM
      // event because the Python retrieval service only invalidates its
      // embedding instance on `embeddingModelConfigured`.
      const event: Event =
        modelType === 'embedding'
          ? {
              eventType: EventType.EmbeddingModelConfiguredEvent,
              timestamp: Date.now(),
              payload: {
                credentialsRoute: `${appConfig.cmBackend}/${aiModelRoute}`,
              } as EmbeddingModelConfiguredEvent,
            }
          : {
              eventType: EventType.LLMConfiguredEvent,
              timestamp: Date.now(),
              payload: {
                credentialsRoute: `${appConfig.cmBackend}/${aiModelRoute}`,
              } as LLMConfiguredEvent,
            };
      await sendEvent(eventService, event);

      res.status(200).json({
        status: 'success',
        message: `${modelType.toUpperCase()} provider added successfully`,
        details: {
          modelKey,
          modelType,
          provider,
          model: configuration.model,
          isDefault,
          contextLength,
        },
      });
    } catch (error: any) {
      const handleError = handleBackendError(error, 'add AI model provider');
      next(handleError);
    }
  };

function destinationHost(
  incoming: Record<string, unknown>,
  existing: Record<string, unknown>,
  provider: unknown,
): string | undefined {
  // A key the request leaves out is unchanged, so it is never the moved one.
  const key = AI_MODEL_DESTINATION_KEYS.find(
    (k) =>
      k in incoming &&
      aiModelDestinationChanged({ [k]: incoming[k] }, existing, provider),
  );
  try {
    return new URL(String(incoming[key as string])).host;
  } catch {
    return undefined;
  }
}

export const updateAIModelProvider =
  (
    keyValueStoreService: KeyValueStoreService,
    eventService: AiConfigEventProducer,
    appConfig: AppConfig,
    save: AiModelsSave = plainAiModelsSave(keyValueStoreService),
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { modelType, modelKey } = req.params;
      if (!modelType || !modelKey) {
        res.status(400).json({
          status: 'error',
          message: 'modelType and modelKey are required',
        });
        return;
      }
      const {
        provider,
        configuration,
        isMultimodal = false,
        isReasoning = false,
        isDefault = false,
        contextLength,
      } = req.body;

      // Validate required fields
      if (!provider || !configuration) {
        res.status(400).json({
          status: 'error',
          message: 'provider and configuration are required',
        });
        return;
      }

      const aiModels = await new AiModelsConfigRepository(keyValueStoreService).read();

      if (!aiModels) {
        res.status(404).json({
          status: 'error',
          message: 'No AI models configuration found',
        });
        return;
      }

      const located = findModelEntry(aiModels, modelKey);
      if (!located) {
        res.status(404).json({
          status: 'error',
          message: `Model with key '${modelKey}' not found or model type not found`,
        });
        return;
      }
      const targetModel = located.model;
      const targetModelType = located.modelType;

      // Verify the model type matches if provided
      if (modelType && targetModelType !== modelType) {
        res.status(400).json({
          status: 'error',
          message: `Model key '${modelKey}' belongs to type '${targetModelType}', not '${modelType}'`,
        });
        return;
      }

      // A deployment serves one model: carrying the stored one to a new model
      // saves a label that disagrees with the traffic, and the health check
      // passes on the old deployment.
      const incoming = configuration as Record<string, unknown>;
      const stored = targetModel.configuration as
        Record<string, unknown> | undefined;
      const trimmed = (value: unknown): string =>
        typeof value === 'string' ? value.trim() : '';
      if (
        trimmed(stored?.deploymentName) !== '' &&
        'model' in incoming &&
        trimmed(incoming.model) !== trimmed(stored?.model) &&
        trimmed(incoming.deploymentName) === ''
      ) {
        res.status(400).json({
          status: 'error',
          message: 'Changing the model needs its deployment name.',
        });
        return;
      }

      // Saved credentials are restored below, so a new destination must come
      // with credentials typed again or they would be sent to it.
      const savedConfiguration: Record<string, unknown> = stored ?? {};
      const destinationChanged = aiModelDestinationChanged(
        incoming,
        savedConfiguration,
        targetModel.provider,
      );
      const reusesCredentials = reusesSavedAiModelCredentials(
        incoming,
        savedConfiguration,
      );
      // The save keeps the stored provider, so a check run as another provider
      // would not describe what is saved.
      if (
        targetModel.provider &&
        String(provider).toLowerCase() !== targetModel.provider.toLowerCase()
      ) {
        res.status(400).json({
          status: 'error',
          message: 'Provider does not match saved model',
        });
        return;
      }

      const securityContext: Record<string, unknown> = {
        orgId: getCurrentOrgId(),
        userId: req.user?.userId,
        modelKey,
        host: destinationChanged
          ? destinationHost(incoming, savedConfiguration, targetModel.provider)
          : undefined,
      };
      if (destinationChanged && reusesCredentials) {
        logger.warn('AI model destination change refused', {
          securityEvent: 'model_destination_change_refused',
          ...securityContext,
        });
        res.status(400).json({
          status: 'error',
          error_code: 'credential_reentry_required',
          message: 'Enter the credentials again to use this new endpoint.',
        });
        return;
      }

      // GET only returns the public allowlist, so the client can only send back
      // keys it retyped. Restore the rest from storage before the health check.
      // Forwarding credentials are not retyped, so a new destination does not get them.
      const restorable = (
        saved: Record<string, unknown>,
      ): Record<string, unknown> =>
        destinationChanged ? withoutForwardingCredentials(saved) : saved;
      const mergedConfiguration = mergeAiModelCredentials(
        configuration,
        restorable(targetModel.configuration as Record<string, unknown>),
      );

      const healthCheckPayload: Record<string, unknown> = {
        provider,
        configuration: mergedConfiguration,
        modelType,
        isMultimodal,
        isReasoning,
        isDefault,
        contextLength,
      };
      const activeEmbeddingBefore =
        targetModelType === 'embedding'
          ? activeEmbeddingModel(aiModels.embedding)
          : null;
      const activeEmbeddingAfter =
        targetModelType === 'embedding'
          ? activeEmbeddingModel(
              (aiModels.embedding as AIModelConfiguration[]).map((config) => ({
                ...config,
                isDefault:
                  config.modelKey === modelKey
                    ? isDefault
                    : !isDefault && config.isDefault,
              })),
            )
          : null;
      if (targetModelType === 'embedding') {
        healthCheckPayload.becomesActive =
          activeEmbeddingAfter?.modelKey === modelKey;
      }

      const aiCommandOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/health-check/${encodeURIComponent(targetModelType)}`,
        method: HttpMethod.POST,
        headers: req.headers as Record<string, string>,
        body: healthCheckPayload,
      };

      // Don't use nested try/catch with next() inside
      const aiServiceCommand = new AIServiceCommand(aiCommandOptions);
      const aiResponseData =
        (await aiServiceCommand.execute()) as AIServiceResponse;

      if (!aiResponseData?.data || aiResponseData.statusCode !== 200) {
        const errData: any = aiResponseData?.data ?? {};
        const reasonMessage =
          modelType === 'embedding' && isEmbeddingInUseRefusal(aiResponseData)
            ? embeddingRefusalMessage(aiResponseData)
            : healthCheckFailureMessage(
                errData,
                `Failed to do health check of ${modelType} configuration, check credentials again`,
              );

        // The reason is written for the admin filling in the dialog ("Incorrect
        // API key provided"); the provider's text is never logged.
        logger.warn('ai_model.health_check.failed', {
          model_type: modelType,
          provider,
          status: aiResponseData?.statusCode,
        });
        res.status(aiResponseData?.statusCode ?? 500).json({
          error: {
            status: 'error',
            message: reasonMessage,
          },
        });
        return;
      }

      // Taking the default off the model that embeds hands embedding to
      // another model, which has to fit the vector store too.
      if (
        activeEmbeddingBefore?.modelKey === modelKey &&
        activeEmbeddingAfter?.modelKey !== modelKey
      ) {
        const takeover = await checkEmbeddingModelTakeover(
          activeEmbeddingAfter,
          req,
          appConfig,
        );
        if (!takeover.data || takeover.statusCode !== 200) {
          res.status(takeover.statusCode).json({
            error: {
              status: 'error',
              message: takeoverRefusalMessage(
                takeover,
                'The model that would take over embedding failed its health check, so nothing was changed. Check its settings and try again.',
              ),
            },
          });
          return;
        }
      }

      const modelFriendlyName = configuration.modelFriendlyName;
      let saved: AIModelsConfig;
      // Set inside the mutate callback, so assert boolean to stop narrowing to false.
      let forwardingCredentialsCleared = false as boolean;
      let movedDuringCheck = false as boolean;
      try {
        saved = await aiModelsRepository(keyValueStoreService, save, req, [
          targetModelType,
        ]).mutate((latest) => {
          movedDuringCheck = false;
          const current = findModelEntry(latest, modelKey);
          if (!current || current.modelType !== targetModelType) {
            throw new AiModelsWriteAborted();
          }
          // A credential rotation can land during the health check; keep its keys.
          const latestSaved =
            (current.model.configuration as
              Record<string, unknown> | undefined) ?? {};
          // Another save can also move the model during the check. Its stored
          // credentials belong to that endpoint, which this check never used,
          // so compare against the latest document, not the earlier read.
          const latestDestinationChanged = aiModelDestinationChanged(
            incoming,
            latestSaved,
            current.model.provider,
          );
          // The check ran against the endpoint read before it, which also
          // covers a request that leaves the endpoint out.
          if (
            aiModelDestinationsDiffer(savedConfiguration, latestSaved) ||
            (latestDestinationChanged &&
              reusesSavedAiModelCredentials(incoming, latestSaved))
          ) {
            movedDuringCheck = true;
            throw new AiModelsWriteAborted();
          }
          const moved = destinationChanged || latestDestinationChanged;
          forwardingCredentialsCleared =
            moved && hasForwardingCredentials(latestSaved);
          current.model.configuration = mergeAiModelCredentials(
            configuration,
            moved ? withoutForwardingCredentials(latestSaved) : latestSaved,
          );
          current.model.isMultimodal = isMultimodal;
          current.model.isDefault = isDefault;
          current.model.isReasoning = isReasoning;
          current.model.contextLength = contextLength || null;
          if (modelFriendlyName !== undefined) {
            current.model.modelFriendlyName = modelFriendlyName;
          }
          if (isDefault) {
            for (const config of latest[current.modelType] ?? []) {
              if (config.modelKey !== modelKey) {
                config.isDefault = false;
              }
            }
          }
        });
      } catch (error) {
        if (error instanceof AiModelsWriteAborted && movedDuringCheck) {
          logger.warn('AI model destination change refused', {
            securityEvent: 'model_destination_change_refused',
            ...securityContext,
            reason: 'moved_during_check',
          });
          res.status(409).json({
            status: 'error',
            message:
              'This model was moved to another endpoint while it was being checked. Reload it and enter the credentials again.',
          });
          return;
        }
        if (error instanceof AiModelsWriteAborted) {
          res.status(404).json({
            status: 'error',
            message: `Model with key '${modelKey}' not found or model type not found`,
          });
          return;
        }
        throw error;
      }
      const updatedModel = findModelEntry(saved, modelKey)?.model ?? targetModel;
      if (destinationChanged) {
        logger.warn('AI model destination changed', {
          securityEvent: 'model_destination_changed',
          ...securityContext,
        });
      }

      const event: Event =
        targetModelType === 'embedding'
          ? {
              eventType: EventType.EmbeddingModelConfiguredEvent,
              timestamp: Date.now(),
              payload: {
                credentialsRoute: `${appConfig.cmBackend}/${aiModelRoute}`,
              } as EmbeddingModelConfiguredEvent,
            }
          : {
              eventType: EventType.LLMConfiguredEvent,
              timestamp: Date.now(),
              payload: {
                credentialsRoute: `${appConfig.cmBackend}/${aiModelRoute}`,
              } as LLMConfiguredEvent,
            };
      await sendEvent(eventService, event);
      const modelForResponse = stripAiModelSecrets(updatedModel);
      res.status(200).json({
        status: 'success',
        message: `${targetModelType.toUpperCase()} provider updated successfully`,
        details: {
          modelKey,
          modelType: targetModelType,
          provider: modelForResponse.provider,
          model: (modelForResponse.configuration as Record<string, unknown>)?.model,
          contextLength: modelForResponse.contextLength,
          isMultimodal: modelForResponse.isMultimodal,
          isReasoning: modelForResponse.isReasoning,
          ...(forwardingCredentialsCleared
            ? { forwardingCredentialsCleared: true }
            : {}),
        },
      });
    } catch (error: any) {
      const handleError = handleBackendError(error, 'update AI model provider');
      next(handleError);
    }
  };

const MODEL_USAGE_UNVERIFIABLE =
  "We couldn't check which agents use this model, so it wasn't deleted. Please try again in a few seconds.";
const WEB_SEARCH_USAGE_UNVERIFIABLE =
  "We couldn't check which agents use this web search provider, so it wasn't deleted. Please try again in a few seconds.";
// No usage check in this module sets a budget to match; AIServiceCommand's default (620s) is sized for model downloads.
// shortcut: a usage read slower than 30s refuses the delete; raise it if large orgs hit that.
const AGENT_USAGE_TIMEOUT_MS = 30_000;

type AgentUsage = { name?: string; _key?: string; creatorName?: string | null };

/**
 * The agents the AI service says use a model or web search provider (`path` under /api/v1/agent/).
 * Anything but a well-formed 200 refuses the delete with a 503 in `refusal`'s words: a check that
 * cannot be made must never read as "no agents".
 */
async function readAgentUsage(
  path: string,
  req: AuthenticatedUserRequest,
  appConfig: AppConfig,
  refusal: string,
): Promise<AgentUsage[]> {
  const unverifiable = (cause: unknown): ServiceUnavailableError =>
    withCause(markClientSafe(new ServiceUnavailableError(refusal)), cause);
  const answer = await new AIServiceCommand<{ success?: boolean; agents?: AgentUsage[] }>({
    uri: `${appConfig.aiBackend}/api/v1/agent/${path}`,
    method: HttpMethod.GET,
    headers: {
      ...(req.headers as Record<string, string>),
      'Content-Type': 'application/json',
    },
    timeoutMs: AGENT_USAGE_TIMEOUT_MS,
  })
    .execute()
    // safeCause: a non-JSON body's SyntaxError quotes the body, which must not reach the log
    .catch((error: unknown) => {
      throw unverifiable(safeCause(error));
    });
  if (
    answer.statusCode !== 200 ||
    answer.data?.success !== true ||
    !Array.isArray(answer.data.agents)
  ) {
    throw unverifiable({ status: answer.statusCode });
  }
  return answer.data.agents;
}

export const deleteAIModelProvider =
  (
    keyValueStoreService: KeyValueStoreService,
    eventService: AiConfigEventProducer,
    appConfig: AppConfig,
    save: AiModelsSave = plainAiModelsSave(keyValueStoreService),
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { modelType, modelKey } = req.params;
      if (!modelType || !modelKey) {
        res.status(400).json({
          status: 'error',
          message: 'modelType and modelKey are required',
        });
        return;
      }

      const aiModels = await new AiModelsConfigRepository(keyValueStoreService).read();

      if (!aiModels) {
        res.status(404).json({
          status: 'error',
          message: 'No AI models configuration found',
        });
        return;
      }

      const locatedForDelete = findModelEntry(aiModels, modelKey);
      const deletedModel = locatedForDelete?.model ?? null;
      const targetModelType = locatedForDelete?.modelType ?? null;

      if (!deletedModel || !targetModelType) {
        res.status(404).json({
          status: 'error',
          message: `Model with key '${modelKey}' not found`,
        });
        return;
      }

      // Verify the model type matches if provided
      if (modelType && targetModelType !== modelType) {
        res.status(400).json({
          status: 'error',
          message: `Model key '${modelKey}' belongs to type '${targetModelType}', not '${modelType}'`,
        });
        return;
      }

      // Before the delete: a model agents still name leaves them unable to chat.
      const agentsUsing = await readAgentUsage(
        `model-usage/${encodeURIComponent(deletedModel.modelKey)}`,
        req,
        appConfig,
        MODEL_USAGE_UNVERIFIABLE,
      );

      if (agentsUsing.length > 0) {
        // Bake agent names into the message string itself. The error middleware
        // strips `metadata` in production (dev-only), so anything kept only in
        // metadata.agents would never reach the user. Wording mirrors the
        // connector delete path (see _format_connector_in_use_detail in router.py)
        // so 409 copy is identical across resource types.
        const MAX_AGENT_NAMES_DISPLAY = 3;
        const agentNames: string[] = agentsUsing
          .map((a: { name?: string }) => a?.name)
          .filter((n: unknown): n is string => typeof n === 'string' && n.length > 0);
        // Prefer the user-defined friendly name (what the UI card shows, e.g. "gpt").
        // Fall back through the technical model id (e.g. "gpt-5.6-luna") and finally
        // the opaque modelKey so the message is never empty.
        const modelDisplayName: string =
          (deletedModel?.modelFriendlyName as string | undefined) ||
          (deletedModel?.configuration?.modelFriendlyName as string | undefined) ||
          (deletedModel?.configuration?.model as string | undefined) ||
          (deletedModel?.modelKey as string | undefined) ||
          'model';
        let message: string;
        if (agentNames.length === 1) {
          message = `Cannot delete model '${modelDisplayName}': currently in use by agent '${agentNames[0]}'. Remove it from the agent first.`;
        } else {
          const displayed = agentNames.slice(0, MAX_AGENT_NAMES_DISPLAY).map((n) => `'${n}'`).join(', ');
          const remainder = agentNames.length - MAX_AGENT_NAMES_DISPLAY;
          const namesDisplay = remainder > 0 ? `${displayed} and ${remainder} more` : displayed;
          message = `Cannot delete model '${modelDisplayName}': currently in use by ${agentNames.length} agents (${namesDisplay}). Remove it from all agents first.`;
        }
        throw new ConflictError(message, { agents: agentsUsing });
      }

      // Deleting the model that embeds hands embedding to the next one (or the
      // built-in model). The AI service refuses that while vectors from this
      // model are stored, the same check that guards changing the default.
      if (
        targetModelType === 'embedding' &&
        activeEmbeddingModel(aiModels.embedding)?.modelKey === modelKey
      ) {
        const nextActive = activeEmbeddingModel(
          (aiModels.embedding as AIModelConfiguration[]).filter(
            (config) => config.modelKey !== modelKey,
          ),
        );
        const takeover = await checkEmbeddingModelTakeover(
          nextActive,
          req,
          appConfig,
        );
        if (isEmbeddingInUseRefusal(takeover)) {
          throw new BadRequestError(
            nextActive
              ? embeddingRefusalMessage(takeover)
              : lastEmbeddingModelRefusalMessage(takeover),
          );
        }
        if (!takeover.data || takeover.statusCode !== 200) {
          res.status(takeover.statusCode).json({
            error: {
              status: 'error',
              message: healthCheckFailureMessage(
                takeover.data,
                "The model that would take over embedding failed its health check, so this model was not deleted. Check that model's settings and try again.",
              ),
            },
          });
          return;
        }
      }

      let wasDefault = deletedModel.isDefault || false;

      try {
        await aiModelsRepository(keyValueStoreService, save, req, [
          targetModelType,
        ]).mutate((latest) => {
          const current = findModelEntry(latest, modelKey);
          if (!current || current.modelType !== targetModelType) {
            throw new AiModelsWriteAborted();
          }
          // The agent-usage check can take seconds. A concurrent default
          // change lands in this document, so the flag has to come from it.
          const removedWasDefault = current.model.isDefault === true;
          wasDefault = removedWasDefault;
          latest[current.modelType]?.splice(current.index, 1);
          const remaining = latest[current.modelType] ?? [];
          if (removedWasDefault && remaining.length > 0) {
            const nextDefault = remaining[0];
            if (nextDefault) nextDefault.isDefault = true;
          }
          if (latest.modelRoles) {
            for (const roleName of Object.keys(latest.modelRoles)) {
              const role = latest.modelRoles[roleName];
              if (role?.modelKey === modelKey) {
                delete latest.modelRoles[roleName];
              }
            }
          }
        });
      } catch (error) {
        if (error instanceof AiModelsWriteAborted) {
          res.status(404).json({
            status: 'error',
            message: `Model with key '${modelKey}' not found`,
          });
          return;
        }
        throw error;
      }

      const event: Event =
        targetModelType === 'embedding'
          ? {
              eventType: EventType.EmbeddingModelConfiguredEvent,
              timestamp: Date.now(),
              payload: {
                credentialsRoute: `${appConfig.cmBackend}/${aiModelRoute}`,
              } as EmbeddingModelConfiguredEvent,
            }
          : {
              eventType: EventType.LLMConfiguredEvent,
              timestamp: Date.now(),
              payload: {
                credentialsRoute: `${appConfig.cmBackend}/${aiModelRoute}`,
              } as LLMConfiguredEvent,
            };
      await sendEvent(eventService, event);
      const modelForResponse = stripAiModelSecrets(
        deletedModel as AIModelConfiguration,
      );
      res.status(200).json({
        status: 'success',
        message: `${targetModelType.toUpperCase()} provider deleted successfully`,
        details: {
          modelKey,
          modelType: targetModelType,
          provider: modelForResponse.provider,
          model: (modelForResponse.configuration as Record<string, unknown>)?.model,
          wasDefault,
          contextLength: modelForResponse.contextLength,
        },
      });
    } catch (error: any) {
      next(error);
    }
  };

export const updateDefaultAIModel =
  (
    keyValueStoreService: KeyValueStoreService,
    eventService: AiConfigEventProducer,
    appConfig: AppConfig,
    save: AiModelsSave = plainAiModelsSave(keyValueStoreService),
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { modelType, modelKey } = req.params;
      if (!modelType || !modelKey) {
        res.status(400).json({
          status: 'error',
          message: 'modelType and modelKey are required',
        });
        return;
      }

      const aiModels = await new AiModelsConfigRepository(keyValueStoreService).read();

      if (!aiModels) {
        res.status(404).json({
          status: 'error',
          message: 'No AI models configuration found',
        });
        return;
      }

      const locatedDefault = findModelEntry(aiModels, modelKey);
      if (!locatedDefault) {
        res.status(404).json({
          status: 'error',
          message: `Model with key '${modelKey}' not found`,
        });
        return;
      }
      const targetModel = locatedDefault.model;
      const targetModelType = locatedDefault.modelType;

      // Verify the model type matches if provided
      if (modelType && targetModelType !== modelType) {
        res.status(400).json({
          status: 'error',
          message: `Model key '${modelKey}' belongs to type '${targetModelType}', not '${modelType}'`,
        });
        return;
      }

      // If the model is already the default, no-op: nothing to check or update.
      if (targetModel.isDefault) {
        res.status(200).json({
          status: 'success',
          message: `Default ${targetModelType} model unchanged`,
          details: {
            modelKey,
            modelType: targetModelType,
            provider: targetModel.provider,
            model: targetModel.configuration?.model,
            contextLength: targetModel.contextLength,
          },
        });
        return;
      }

      // Run a health check on the target model BEFORE promoting it to default.
      // For `embedding` we use the `/embedding-health-check` endpoint which
      // validates the model AND manages the Qdrant collection lifecycle
      // (deletes + recreates when the vector dimension or model identity
      // changes). For other model types we use the generic per-model
      // `/health-check/{type}` endpoint which validates reachability and
      // credentials.
      if ((AI_MODEL_TYPES as readonly string[]).includes(targetModelType)) {
        let aiResponseData: AIServiceResponse;
        if (targetModelType === 'embedding') {
          // Embedding uses the collection-managing endpoint.
          aiResponseData = await checkEmbeddingModelTakeover(
            targetModel as AIModelConfiguration,
            req,
            appConfig,
          );
        } else {
          const aiServiceCommand = new AIServiceCommand({
            uri: `${appConfig.aiBackend}/api/v1/health-check/${encodeURIComponent(targetModelType)}`,
            method: HttpMethod.POST,
            headers: req.headers as Record<string, string>,
            body: {
              provider: targetModel.provider,
              configuration: targetModel.configuration,
              modelType: targetModelType,
              isMultimodal: targetModel.isMultimodal ?? false,
              isReasoning: targetModel.isReasoning ?? false,
              isDefault: true,
              contextLength: targetModel.contextLength ?? null,
              ...(targetModel.modelFriendlyName && {
                modelFriendlyName: targetModel.modelFriendlyName,
              }),
            },
          });
          aiResponseData =
            (await aiServiceCommand.execute()) as AIServiceResponse;
        }

        if (!aiResponseData?.data || aiResponseData?.statusCode !== 200) {
          const errData: any = aiResponseData?.data ?? {};
          const fallback =
            `Failed health check while setting default ${targetModelType} model. ` +
            `Refusing to change default to prevent breaking the system.`;
          const reasonMessage =
            targetModelType === 'embedding'
              ? takeoverRefusalMessage(aiResponseData, fallback)
              : healthCheckFailureMessage(errData, fallback);

          res.status(aiResponseData?.statusCode ?? 500).json({
            error: {
              status: 'error',
              message: reasonMessage,
              details: errData,
            },
          });
          return;
        }
      }

      try {
        await aiModelsRepository(keyValueStoreService, save, req, [
          targetModelType,
        ]).mutate((latest) => {
          const current = findModelEntry(latest, modelKey);
          if (!current || current.modelType !== targetModelType) {
            throw new AiModelsWriteAborted();
          }
          for (const config of latest[current.modelType] ?? []) {
            config.isDefault = false;
          }
          current.model.isDefault = true;
        });
      } catch (error) {
        if (error instanceof AiModelsWriteAborted) {
          res.status(404).json({
            status: 'error',
            message: `Model with key '${modelKey}' not found`,
          });
          return;
        }
        throw error;
      }

      const event: Event =
        targetModelType === 'embedding'
          ? {
              eventType: EventType.EmbeddingModelConfiguredEvent,
              timestamp: Date.now(),
              payload: {
                credentialsRoute: `${appConfig.cmBackend}/${aiModelRoute}`,
              } as EmbeddingModelConfiguredEvent,
            }
          : {
              eventType: EventType.LLMConfiguredEvent,
              timestamp: Date.now(),
              payload: {
                credentialsRoute: `${appConfig.cmBackend}/${aiModelRoute}`,
              } as LLMConfiguredEvent,
            };
      await sendEvent(eventService, event);

      res.status(200).json({
        status: 'success',
        message: `Default ${targetModelType} model updated successfully`,
        details: {
          modelKey,
          modelType: targetModelType,
          provider: targetModel.provider,
          model: targetModel.configuration?.model,
          contextLength: targetModel.contextLength,
        },
      });
    } catch (error: any) {
      next(error);
    }
  };

// Generic, parameterized connector config getter
export const getConnectorConfig =
  (keyValueStoreService: KeyValueStoreService) =>
  async (
    req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ) => {
    try {
      const { connector } = req.params as { connector: string };
      if (!connector || typeof connector !== 'string') {
        throw new BadRequestError('connector path parameter is required');
      }

      const configManagerConfig = loadConfigurationManagerConfig();
      const key = `/services/connectors/${connector}/config`;
      const encryptedConfig = await keyValueStoreService.get<string>(key);

      if (!encryptedConfig) {
        res.status(200).json({}).end();
        return;
      }

      const config = JSON.parse(
        EncryptionService.getInstance(
          configManagerConfig.algorithm,
          configManagerConfig.secretKey,
        ).decrypt(encryptedConfig),
      );

      res.status(200).json(config).end();
    } catch (error: any) {
      next(error);
    }
  };

// Custom System Prompt Management
export const getCustomSystemPrompt =
  (keyValueStoreService: KeyValueStoreService) =>
  async (
    _req: AuthenticatedUserRequest | AuthenticatedServiceRequest,
    res: Response,
    next: NextFunction,
  ) => {
    try {
      const configManagerConfig = loadConfigurationManagerConfig();
      const decrypt = (enc: string): string =>
        EncryptionService.getInstance(
          configManagerConfig.algorithm,
          configManagerConfig.secretKey,
        ).decrypt(enc);

      // 1. Try the new dedicated key first
      const encryptedPrompts = await keyValueStoreService.get<string>(configPaths.systemPrompts);
      if (encryptedPrompts) {
        const p = JSON.parse(decrypt(encryptedPrompts)) as SystemPromptsConfig;
        res
          .status(200)
          .json({
            customSystemPrompt:          p.customSystemPrompt          || '',
            customSystemPromptWebSearch: p.customSystemPromptWebSearch || '',
            customSystemPromptAgent:     p.customSystemPromptAgent     || '',
          })
          .end();
        return;
      }

      // 2. OSS backward compat: prompts were previously stored inside the aiModels blob
      const encryptedAIConfig = await keyValueStoreService.get<string>(configPaths.aiModels);
      if (encryptedAIConfig) {
        const aiModels = JSON.parse(decrypt(encryptedAIConfig)) as AIModelsConfig;
        res
          .status(200)
          .json({
            customSystemPrompt:          aiModels.customSystemPrompt          || '',
            customSystemPromptWebSearch: aiModels.customSystemPromptWebSearch || '',
            customSystemPromptAgent:     aiModels.customSystemPromptAgent     || '',
          })
          .end();
        return;
      }

      res
        .status(200)
        .json({ customSystemPrompt: '', customSystemPromptWebSearch: '', customSystemPromptAgent: '' })
        .end();
    } catch (error: any) {
      next(error);
    }
  };

export const setCustomSystemPrompt =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const {
        customSystemPrompt,
        customSystemPromptWebSearch,
        // Older clients may not send this field yet -- default to '' rather
        // than rejecting the request, so agent-mode prompt rollout doesn't
        // break existing Internal Search / Web Search saves.
        customSystemPromptAgent = '',
      } = req.body;

      if (typeof customSystemPrompt !== 'string') {
        throw new BadRequestError('customSystemPrompt must be a string');
      }
      if (typeof customSystemPromptWebSearch !== 'string') {
        throw new BadRequestError('customSystemPromptWebSearch must be a string');
      }
      if (typeof customSystemPromptAgent !== 'string') {
        throw new BadRequestError('customSystemPromptAgent must be a string');
      }

      const configManagerConfig = loadConfigurationManagerConfig();
      const encrypt = (val: string): string =>
        EncryptionService.getInstance(
          configManagerConfig.algorithm,
          configManagerConfig.secretKey,
        ).encrypt(val);

      const MAX_RETRIES = 5;
      let success = false;

      for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
        const existing = await keyValueStoreService.get<string>(configPaths.systemPrompts);
        const promptsConfig: SystemPromptsConfig = {
          customSystemPrompt,
          customSystemPromptWebSearch,
          customSystemPromptAgent,
        };
        const encrypted = encrypt(JSON.stringify(promptsConfig));

        const casSuccess = await keyValueStoreService.compareAndSet<string>(
          configPaths.systemPrompts,
          existing,
          encrypted,
        );

        if (casSuccess) {
          success = true;
          break;
        } else if (attempt === MAX_RETRIES - 1) {
          throw new Error(
            'Failed to update custom system prompts due to persistent concurrent modification. Please try again.',
          );
        }
        await new Promise((resolve) => setTimeout(resolve, 50 * (attempt + 1)));
      }

      if (!success) {
        throw new Error('Failed to update custom system prompts after maximum retries.');
      }

      res.status(200).json({
        message: 'Custom system prompts updated successfully',
        customSystemPrompt,
        customSystemPromptWebSearch,
        customSystemPromptAgent,
      });
    } catch (error: any) {
      next(error);
    }
  };

// ---------------------------------------------------------------------------
// AI Model Registry proxy (forwards to Python backend)
// ---------------------------------------------------------------------------

async function proxyAiBackendGet(
  appConfig: AppConfig,
  req: AuthenticatedUserRequest,
  res: Response,
  next: NextFunction,
  path: string,
  query?: URLSearchParams,
): Promise<void> {
  try {
    const qs = query?.toString();
    const url = `${appConfig.aiBackend}${path}${qs ? `?${qs}` : ''}`;

    const headers: Record<string, string> = {};
    if (req.headers.authorization) {
      headers['Authorization'] = req.headers.authorization;
    }

    const response = await axios.get(url, { timeout: 10000, headers });
    res.status(response.status).json(response.data);
  } catch (error: any) {
    if (error?.response) {
      res.status(error.response.status).json(error.response.data);
    } else {
      next(
        withCause(
          new ServiceUnavailableError('AI model registry service is unavailable'),
          error,
        ),
      );
    }
  }
}

export const getAIModelRegistry =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    const { search, capability } = req.query;
    const params = new URLSearchParams();
    if (search) params.set('search', String(search));
    if (capability) params.set('capability', String(capability));

    await proxyAiBackendGet(
      appConfig,
      req,
      res,
      next,
      '/api/v1/ai-models/registry',
      params,
    );
  };

export const getAIModelRegistryCapabilities =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    await proxyAiBackendGet(
      appConfig,
      req,
      res,
      next,
      '/api/v1/ai-models/registry/capabilities',
    );
  };

export const getAIModelProviderSchema =
  (appConfig: AppConfig) =>
  async (
    req: AuthenticatedUserRequest,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    const providerId = req.params.providerId ?? '';
    const { capability } = req.query;
    const params = new URLSearchParams();
    if (capability) params.set('capability', String(capability));

    await proxyAiBackendGet(
      appConfig,
      req,
      res,
      next,
      `/api/v1/ai-models/registry/${encodeURIComponent(providerId)}/schema`,
      params,
    );
  };

// Web Search Provider Management Functions
export const getWebSearchProviders =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const configManagerConfig = loadConfigurationManagerConfig();
      const encryptedWebSearchConfig = await keyValueStoreService.get<string>(
        configPaths.webSearch,
      );

      if (!encryptedWebSearchConfig) {
        res.status(200).json({
          status: 'success',
          providers: [],
          configured: false,
          settings: DEFAULT_WEB_SEARCH_SETTINGS,
          message: 'No web search providers found',
        });
        return;
      }

      const webSearchConfig = JSON.parse(
        EncryptionService.getInstance(
          configManagerConfig.algorithm,
          configManagerConfig.secretKey,
        ).decrypt(encryptedWebSearchConfig),
      ) as WebSearchConfig;

      const storedProviders = Array.isArray(webSearchConfig.providers)
        ? webSearchConfig.providers
        : [];
      // Members may list providers (the agent builder does), but only admins
      // may read their API keys or server addresses.
      const isAdmin = await requesterIsOrgAdmin(req);
      const hideSecrets =
        !isAdmin || (shouldHideSecrets(req) && !canRevealSecrets(req));
      const mask = isAdmin
        ? maskWebSearchProvider
        : maskWebSearchProviderForMember;
      const providers = storedProviders.map((p) =>
        hideSecrets && p.configuration
          ? { ...p, configuration: mask(p.configuration) }
          : p,
      );
      const settings = normalizeWebSearchSettings(webSearchConfig.settings);

      res.status(200).json({
        status: 'success',
        providers,
        configured: isWebSearchConfigured(storedProviders),
        settings,
        message: 'Web search providers retrieved successfully',
      });
    } catch (error: any) {
      next(error);
    }
  };

export const updateWebSearchSettings =
  (keyValueStoreService: KeyValueStoreService) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { includeImages, maxImages } = req.body;
      const configManagerConfig = loadConfigurationManagerConfig();

      // Use Compare-and-Set (CAS) pattern with retries to prevent race conditions
      const MAX_RETRIES = 5;
      let success = false;
      let normalizedSettings: ReturnType<typeof normalizeWebSearchSettings> | null =
        null;

      for (let attempt = 0; attempt < MAX_RETRIES; attempt++) {
        const encryptedWebSearchConfig = await keyValueStoreService.get<string>(
          configPaths.webSearch,
        );

        let webSearchConfig: WebSearchConfig = { providers: [] };
        if (encryptedWebSearchConfig) {
          webSearchConfig = JSON.parse(
            EncryptionService.getInstance(
              configManagerConfig.algorithm,
              configManagerConfig.secretKey,
            ).decrypt(encryptedWebSearchConfig),
          ) as WebSearchConfig;
        }

        if (!Array.isArray(webSearchConfig.providers)) {
          webSearchConfig.providers = [];
        }

        const existingSettings = normalizeWebSearchSettings(
          webSearchConfig.settings,
        );
        normalizedSettings = normalizeWebSearchSettings({
          includeImages,
          maxImages:
            typeof maxImages === 'number'
              ? maxImages
              : existingSettings.maxImages,
        });

        webSearchConfig.settings = normalizedSettings;

        const encryptedUpdatedConfig = EncryptionService.getInstance(
          configManagerConfig.algorithm,
          configManagerConfig.secretKey,
        ).encrypt(JSON.stringify(webSearchConfig));

        const casSuccess = await keyValueStoreService.compareAndSet<string>(
          configPaths.webSearch,
          encryptedWebSearchConfig ?? null,
          encryptedUpdatedConfig,
        );

        if (casSuccess) {
          success = true;
          break;
        } else if (attempt === MAX_RETRIES - 1) {
          throw new Error(
            'Failed to update web search settings due to persistent concurrent modification. Please try again.',
          );
        }
        // If CAS failed, retry with exponential backoff
        await new Promise((resolve) => setTimeout(resolve, 50 * (attempt + 1)));
      }

      if (!success || normalizedSettings === null) {
        throw new Error(
          'Failed to update web search settings after maximum retries.',
        );
      }

      res.status(200).json({
        status: 'success',
        message: 'Web search settings updated successfully',
        settings: normalizedSettings,
      });
    } catch (error: any) {
      next(error);
    }
  };

export const addWebSearchProvider =
  (keyValueStoreService: KeyValueStoreService, appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { provider, configuration, isDefault = false } = req.body;

      // Validate required fields
      if (!provider || !configuration) {
        res.status(400).json({
          status: 'error',
          message: 'provider and configuration are required',
        });
        return;
      }

      // Health check: verify provider credentials before saving
      const webSearchHealthCheckOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/web-search-health-check`,
        method: HttpMethod.POST,
        headers: req.headers as Record<string, string>,
        body: { provider, configuration },
      };

      const webSearchHealthCheckCommand = new AIServiceCommand(webSearchHealthCheckOptions);
      const webSearchHealthCheckResponse = (await webSearchHealthCheckCommand.execute()) as AIServiceResponse;

      if (!webSearchHealthCheckResponse?.data || webSearchHealthCheckResponse.statusCode !== 200) {
        const errData: any = webSearchHealthCheckResponse?.data ?? {};
        res.status(webSearchHealthCheckResponse?.statusCode ?? 500).json({
          status: 'error',
          message: errData.error ?? 'Failed to validate web search provider configuration',
          details: errData,
        });
        return;
      }

      const configManagerConfig = loadConfigurationManagerConfig();
      const encryptedWebSearchConfig = await keyValueStoreService.get<string>(
        configPaths.webSearch,
      );

      let webSearchConfig: WebSearchConfig = { providers: [] };
      if (encryptedWebSearchConfig) {
        webSearchConfig = JSON.parse(
          EncryptionService.getInstance(
            configManagerConfig.algorithm,
            configManagerConfig.secretKey,
          ).decrypt(encryptedWebSearchConfig),
        );
      }

      // Ensure providers array exists
      if (!webSearchConfig.providers) {
        webSearchConfig.providers = [];
      }

      // Generate a unique providerKey
      const providerKey = randomUUID();

      // If this is set as default, remove default flag from other providers
      if (isDefault) {
        for (const config of webSearchConfig.providers) {
          config.isDefault = false;
        }
      }

      // The first provider that can search becomes the default; one that only reads pages never does.
      const shouldBeDefault =
        canSearch(provider) &&
        (isDefault ||
          !webSearchConfig.providers.some((p) => canSearch(p.provider)));

      // Add the new configuration
      const newConfig = {
        provider,
        configuration,
        providerKey,
        isDefault: shouldBeDefault,
      };

      webSearchConfig.providers.push(newConfig);

      // Encrypt and save the updated configuration
      const encryptedUpdatedConfig = EncryptionService.getInstance(
        configManagerConfig.algorithm,
        configManagerConfig.secretKey,
      ).encrypt(JSON.stringify(webSearchConfig));

      await keyValueStoreService.set<string>(
        configPaths.webSearch,
        encryptedUpdatedConfig,
      );

      res.status(200).json({
        status: 'success',
        message: 'Web search provider added successfully',
        details: {
          providerKey,
          provider,
          isDefault: shouldBeDefault,
        },
      });
    } catch (error: any) {
      next(error);
    }
  };

export const updateWebSearchProvider =
  (keyValueStoreService: KeyValueStoreService, appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { providerKey } = req.params;
      const { provider, configuration, isDefault = false } = req.body;

      // Validate required fields
      if (!provider || !configuration) {
        res.status(400).json({
          status: 'error',
          message: 'provider and configuration are required',
        });
        return;
      }

      const configManagerConfig = loadConfigurationManagerConfig();
      const encryptedWebSearchConfig = await keyValueStoreService.get<string>(
        configPaths.webSearch,
      );

      if (!encryptedWebSearchConfig) {
        res.status(404).json({
          status: 'error',
          message: 'No web search configuration found',
        });
        return;
      }

      const webSearchConfig = JSON.parse(
        EncryptionService.getInstance(
          configManagerConfig.algorithm,
          configManagerConfig.secretKey,
        ).decrypt(encryptedWebSearchConfig),
      );

      // Find the provider with the specified key
      const targetProvider = webSearchConfig.providers?.find(
        (p: WebSearchProviderConfiguration) => p.providerKey === providerKey,
      );

      if (!targetProvider) {
        res.status(404).json({
          status: 'error',
          message: `Provider with key '${providerKey}' not found`,
        });
        return;
      }

      // A saved key must not follow a changed server address.
      const savedProvider = targetProvider as {
        configuration?: Record<string, unknown>;
        provider?: unknown;
      };
      if (
        webSearchBaseUrlMovedWithSavedKey(
          configuration as Record<string, unknown>,
          savedProvider.configuration ?? {},
          provider,
          savedProvider.provider,
        )
      ) {
        res.status(400).json(CREDENTIAL_REENTRY_REQUIRED);
        return;
      }

      // Restore any resubmitted "****************" placeholder from the
      // provider's own stored config before it reaches the health check.
      const effectiveConfiguration = mergeWebSearchProviderPlaceholders(
        configuration,
        targetProvider.configuration,
      );

      // Health check: verify provider credentials before updating
      const webSearchHealthCheckOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/web-search-health-check`,
        method: HttpMethod.POST,
        headers: req.headers as Record<string, string>,
        body: { provider, configuration: effectiveConfiguration },
      };

      const webSearchHealthCheckCommand = new AIServiceCommand(webSearchHealthCheckOptions);
      const webSearchHealthCheckResponse = (await webSearchHealthCheckCommand.execute()) as AIServiceResponse;

      if (!webSearchHealthCheckResponse?.data || webSearchHealthCheckResponse.statusCode !== 200) {
        const errData: any = webSearchHealthCheckResponse?.data ?? {};
        res.status(webSearchHealthCheckResponse?.statusCode ?? 500).json({
          status: 'error',
          message: errData.error ?? 'Failed to validate web search provider configuration',
          details: errData,
        });
        return;
      }

      // Update the provider configuration
      targetProvider.provider = provider;
      targetProvider.configuration = effectiveConfiguration;
      targetProvider.isDefault = isDefault;

      // If this is set as default, remove default flag from other providers
      if (isDefault) {
        for (const config of webSearchConfig.providers) {
          if (config.providerKey !== providerKey) {
            config.isDefault = false;
          }
        }
      }

      // Encrypt and save the updated configuration
      const encryptedUpdatedConfig = EncryptionService.getInstance(
        configManagerConfig.algorithm,
        configManagerConfig.secretKey,
      ).encrypt(JSON.stringify(webSearchConfig));

      const casSuccess = await keyValueStoreService.compareAndSet<string>(
        configPaths.webSearch,
        encryptedWebSearchConfig,
        encryptedUpdatedConfig,
      );
      
      if (!casSuccess) {
        res.status(409).json({
          status: 'error',
          message: 'Unable to save changes. Please retry.',
        });
        return;
      }

      res.status(200).json({
        status: 'success',
        message: 'Web search provider updated successfully',
        details: {
          providerKey,
          provider: targetProvider.provider,
        },
      });
    } catch (error: any) {
      next(error);
    }
  };

export const deleteWebSearchProvider =
  (keyValueStoreService: KeyValueStoreService, appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { providerKey } = req.params;

      const configManagerConfig = loadConfigurationManagerConfig();
      const encryptedWebSearchConfig = await keyValueStoreService.get<string>(
        configPaths.webSearch,
      );

      if (!encryptedWebSearchConfig) {
        res.status(404).json({
          status: 'error',
          message: 'No web search configuration found',
        });
        return;
      }

      const webSearchConfig = JSON.parse(
        EncryptionService.getInstance(
          configManagerConfig.algorithm,
          configManagerConfig.secretKey,
        ).decrypt(encryptedWebSearchConfig),
      );

      // Find the provider index
      const providerIndex = webSearchConfig.providers?.findIndex(
        (p: any) => p.providerKey === providerKey,
      );

      if (providerIndex === -1 || providerIndex === undefined) {
        res.status(404).json({
          status: 'error',
          message: `Provider with key '${providerKey}' not found`,
        });
        return;
      }

      const deletedProvider = webSearchConfig.providers[providerIndex];

      const agents = await readAgentUsage(
        `web-search-usage/${encodeURIComponent(deletedProvider.provider)}`,
        req,
        appConfig,
        WEB_SEARCH_USAGE_UNVERIFIABLE,
      );
      if (agents.length > 0) {
        res.status(409).json({
          status: 'error',
          message: `Cannot delete this provider because it is currently used by ${agents.length} ${agents.length === 1 ? 'agent' : 'agents'}. Please remove the web search configuration from these agents first.`,
          agents,
        });
        return;
      }

      const wasDefault = deletedProvider.isDefault || false;

      // Remove the provider from the configuration
      webSearchConfig.providers.splice(providerIndex, 1);

      // If the deleted provider was default, the first remaining one that can search takes over;
      // with none, web search is not set up until an admin chooses a provider again.
      if (wasDefault) {
        const next = (webSearchConfig as WebSearchConfig).providers.find((p) =>
          canSearch(p.provider),
        );
        if (next) {
          next.isDefault = true;
        }
      }

      // Encrypt and save the updated configuration
      const encryptedUpdatedConfig = EncryptionService.getInstance(
        configManagerConfig.algorithm,
        configManagerConfig.secretKey,
      ).encrypt(JSON.stringify(webSearchConfig));

      await keyValueStoreService.set<string>(
        configPaths.webSearch,
        encryptedUpdatedConfig,
      );

      res.status(200).json({
        status: 'success',
        message: 'Web search provider deleted successfully',
        details: {
          providerKey,
          provider: deletedProvider.provider,
          wasDefault,
        },
      });
    } catch (error: any) {
      next(error);
    }
  };

export const updateDefaultWebSearchProvider =
  (keyValueStoreService: KeyValueStoreService, appConfig: AppConfig) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { providerKey } = req.params;

      const configManagerConfig = loadConfigurationManagerConfig();
      const encryptedWebSearchConfig = await keyValueStoreService.get<string>(
        configPaths.webSearch,
      );

      // DuckDuckGo needs no settings, so choosing it is the one case where the key names a provider
      // that may not be stored yet: store it, then make it the default. Once stored it has a
      // generated key like any provider, and choosing it by that key follows the same rule.
      let storedDuckDuckGo = false;
      if (encryptedWebSearchConfig !== null) {
        const current = JSON.parse(
          EncryptionService.getInstance(
            configManagerConfig.algorithm,
            configManagerConfig.secretKey,
          ).decrypt(encryptedWebSearchConfig),
        ) as WebSearchConfig;
        storedDuckDuckGo =
          Array.isArray(current.providers) &&
          current.providers.some(
            (p) =>
              p.providerKey === providerKey &&
              p.provider === DUCKDUCKGO_PROVIDER,
          );
      }
      if (providerKey === DUCKDUCKGO_PROVIDER || storedDuckDuckGo) {
        const duckduckgoCheckOptions: AICommandOptions = {
          uri: `${appConfig.aiBackend}/api/v1/web-search-health-check`,
          method: HttpMethod.POST,
          headers: req.headers as Record<string, string>,
          body: { provider: DUCKDUCKGO_PROVIDER, configuration: {} },
        };
        const duckduckgoHealth = (await new AIServiceCommand(
          duckduckgoCheckOptions,
        ).execute()) as AIServiceResponse;
        // Choosing DuckDuckGo stays possible while it refuses the test search (blocked, rate limited):
        // that is how a shared server looks, not a broken setup, and it often passes later. The admin
        // is told. Any other failure still refuses, as it does for every other provider.
        let warning: string | undefined;
        if (duckduckgoHealth.statusCode !== 200) {
          const errData = (duckduckgoHealth.data ?? {}) as {
            error?: string;
            errorType?: string;
          };
          if (!WEB_SEARCH_PASSING_REFUSALS.has(errData.errorType)) {
            res.status(duckduckgoHealth.statusCode).json({
              status: 'error',
              message:
                errData.error ??
                'Failed to validate web search provider configuration',
              details: errData,
            });
            return;
          }
          warning = `${errData.error ?? 'DuckDuckGo refused the test search.'} DuckDuckGo was saved anyway; if search keeps failing, add a provider that uses an API key.`;
        }

        const encryption = EncryptionService.getInstance(
          configManagerConfig.algorithm,
          configManagerConfig.secretKey,
        );
        const stored: WebSearchConfig = encryptedWebSearchConfig
          ? JSON.parse(encryption.decrypt(encryptedWebSearchConfig))
          : { providers: [] };
        if (!Array.isArray(stored.providers)) {
          stored.providers = [];
        }
        for (const config of stored.providers) {
          config.isDefault = false;
        }
        let duckduckgo = stored.providers.find(
          (p) => p.provider === DUCKDUCKGO_PROVIDER,
        );
        if (!duckduckgo) {
          duckduckgo = {
            provider: DUCKDUCKGO_PROVIDER,
            configuration: {},
            providerKey: randomUUID(),
            isDefault: true,
          };
          stored.providers.push(duckduckgo);
        }
        duckduckgo.isDefault = true;

        const saved = await keyValueStoreService.compareAndSet<string>(
          configPaths.webSearch,
          encryptedWebSearchConfig ?? null,
          encryption.encrypt(JSON.stringify(stored)),
        );
        if (!saved) {
          res.status(409).json({
            status: 'error',
            message: 'Unable to save changes. Please retry.',
          });
          return;
        }

        res.status(200).json({
          status: 'success',
          message: 'Default web search provider updated successfully',
          ...(warning !== undefined ? { warning } : {}),
          details: {
            providerKey: duckduckgo.providerKey,
            provider: DUCKDUCKGO_PROVIDER,
          },
        });
        return;
      }

      if (!encryptedWebSearchConfig) {
        res.status(404).json({
          status: 'error',
          message: 'No web search configuration found',
        });
        return;
      }

      const webSearchConfig = JSON.parse(
        EncryptionService.getInstance(
          configManagerConfig.algorithm,
          configManagerConfig.secretKey,
        ).decrypt(encryptedWebSearchConfig),
      );

      // Find the provider with the specified key
      const targetProvider = webSearchConfig.providers?.find(
        (p: any) => p.providerKey === providerKey,
      );

      if (!targetProvider) {
        res.status(404).json({
          status: 'error',
          message: `Provider with key '${providerKey}' not found`,
        });
        return;
      }

      if (
        !canSearch((targetProvider as WebSearchProviderConfiguration).provider)
      ) {
        res.status(400).json({
          status: 'error',
          message:
            'This provider only reads pages, so it cannot be the default web search provider',
        });
        return;
      }

      // Health check: verify provider is reachable before setting as default
      const webSearchHealthCheckOptions: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/web-search-health-check`,
        method: HttpMethod.POST,
        headers: req.headers as Record<string, string>,
        body: { provider: targetProvider.provider, configuration: targetProvider.configuration },
      };

      const webSearchHealthCheckCommand = new AIServiceCommand(webSearchHealthCheckOptions);
      const webSearchHealthCheckResponse = (await webSearchHealthCheckCommand.execute()) as AIServiceResponse;

      if (!webSearchHealthCheckResponse?.data || webSearchHealthCheckResponse.statusCode !== 200) {
        const errData: any = webSearchHealthCheckResponse?.data ?? {};
        res.status(webSearchHealthCheckResponse?.statusCode ?? 500).json({
          status: 'error',
          message: errData.error ?? 'Failed to validate web search provider configuration',
          details: errData,
        });
        return;
      }

      // Remove default flag from all providers
      for (const config of webSearchConfig.providers) {
        config.isDefault = false;
      }

      // Set the target provider as default
      targetProvider.isDefault = true;

      // Encrypt and save the updated configuration
      const encryptedUpdatedConfig = EncryptionService.getInstance(
        configManagerConfig.algorithm,
        configManagerConfig.secretKey,
      ).encrypt(JSON.stringify(webSearchConfig));

      const savedDefault = await keyValueStoreService.compareAndSet<string>(
        configPaths.webSearch,
        encryptedWebSearchConfig,
        encryptedUpdatedConfig,
      );
      if (!savedDefault) {
        res.status(409).json({
          status: 'error',
          message: 'Unable to save changes. Please retry.',
        });
        return;
      }

      res.status(200).json({
        status: 'success',
        message: 'Default web search provider updated successfully',
        details: {
          providerKey,
          provider: targetProvider.provider,
        },
      });
    } catch (error: any) {
      next(error);
    }
  };

// ---------------------------------------------------------------------------
// Model Roles endpoints
// ---------------------------------------------------------------------------

export const getModelRoles =
  (keyValueStoreService: KeyValueStoreService) =>
  async (_req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const aiModels = await new AiModelsConfigRepository(keyValueStoreService).read();

      if (!aiModels) {
        res.status(200).json({ status: 'success', modelRoles: {} });
        return;
      }

      res.status(200).json({
        status: 'success',
        modelRoles: aiModels.modelRoles ?? {},
      });
    } catch (error: any) {
      next(error);
    }
  };

export const updateModelRoles =
  (
    keyValueStoreService: KeyValueStoreService,
    save: AiModelsSave = plainAiModelsSave(keyValueStoreService),
  ) =>
  async (req: AuthenticatedUserRequest, res: Response, next: NextFunction) => {
    try {
      const { roles } = req.body as {
        roles: Record<string, { modelType: string; modelKey: string }>;
      };

      if (!roles || typeof roles !== 'object' || Array.isArray(roles)) {
        res.status(400).json({
          status: 'error',
          message: 'Request body must contain a "roles" object',
        });
        return;
      }

      let rejection: string | null = null;
      let savedRoles: AIModelsConfig['modelRoles'] = roles;
      try {
        const saved = await aiModelsRepository(
          keyValueStoreService,
          save,
          req,
          ['roles'],
        ).mutate((aiModels) => {
          for (const [roleName, assignment] of Object.entries(roles)) {
            if (!assignment || typeof assignment !== 'object' || Array.isArray(assignment)) {
              rejection = `Role "${roleName}" assignment must be an object`;
              throw new AiModelsWriteAborted();
            }

            const { modelType, modelKey } = assignment;

            if (!modelType || !modelKey) {
              rejection = `Role "${roleName}" must have both modelType and modelKey`;
              throw new AiModelsWriteAborted();
            }

            if (!isAIModelType(modelType)) {
              rejection = `Role "${roleName}": modelType "${modelType}" is not valid`;
              throw new AiModelsWriteAborted();
            }

            const bucket = aiModels[modelType];
            const exists = Array.isArray(bucket) && bucket.some((m) => m.modelKey === modelKey);
            if (!exists) {
              rejection = `Role "${roleName}": no model with key "${modelKey}" found in "${modelType}"`;
              throw new AiModelsWriteAborted();
            }
          }

          aiModels.modelRoles = roles;
        });
        savedRoles = saved.modelRoles;
      } catch (error) {
        if (error instanceof AiModelsWriteAborted) {
          res.status(400).json({
            status: 'error',
            message: rejection ?? 'Invalid model role assignment',
          });
          return;
        }
        throw error;
      }

      res.status(200).json({
        status: 'success',
        message: 'Model roles updated successfully',
        modelRoles: savedRoles,
      });
    } catch (error: any) {
      next(error);
    }
  };
