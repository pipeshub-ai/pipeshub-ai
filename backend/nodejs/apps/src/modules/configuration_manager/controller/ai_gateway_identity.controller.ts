import { NextFunction, Request, Response } from 'express';

import { AppConfig } from '../../tokens_manager/config/config';
import {
  AICommandOptions,
  AIServiceCommand,
} from '../../../libs/commands/ai_service/ai.service.command';
import { EncryptionService } from '../../../libs/encryptor/encryptor';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { Logger } from '../../../libs/services/logger.service';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { loadConfigurationManagerConfig } from '../config/config';
import {
  aiModelRoute,
  AI_MODEL_TYPES,
  AIServiceResponse,
} from '../constants/constants';
import { configPaths } from '../paths/paths';
import {
  AiConfigEventProducer,
  EmbeddingModelConfiguredEvent,
  Event,
  EventType,
  LLMConfiguredEvent,
} from '../services/kafka_events.service';
import { AiModelsConfigRepository } from '../services/aiModelsConfig.repository';
import {
  IDENTITY_FORWARDING_KEYS,
  restoreCredentialTemplates,
} from '../utils/maskConfigSecrets';
import { identityPolicyBodySchema } from '../validator/validators';

const logger = Logger.getInstance({ service: 'AiGatewayIdentityController' });

const DEFAULT_POLICY = identityPolicyBodySchema.parse({});

function cipher(): EncryptionService {
  const config = loadConfigurationManagerConfig();
  return EncryptionService.getInstance(config.algorithm, config.secretKey);
}

type StoredPolicy = ReturnType<typeof identityPolicyBodySchema.parse>;

/**
 * A stored blob that cannot be decrypted or no longer matches the schema (a
 * rotated secret key, say) is reported as damaged. Answering the default would
 * show the admin a looser policy than the one they saved, and the next save
 * would overwrite the blob without warning.
 */
function readPolicy(ciphertext: string | null | undefined): {
  policy: StoredPolicy;
  damaged: boolean;
} {
  if (!ciphertext) return { policy: DEFAULT_POLICY, damaged: false };
  try {
    return {
      policy: identityPolicyBodySchema.parse(
        JSON.parse(cipher().decrypt(ciphertext)),
      ),
      damaged: false,
    };
  } catch {
    return { policy: DEFAULT_POLICY, damaged: true };
  }
}

export function getIdentityPolicy(keyValueStoreService: KeyValueStoreService) {
  return async (
    _req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const stored = await keyValueStoreService.get<string>(
        configPaths.aiGatewayIdentityPolicy,
      );
      res.status(200).json({ status: 'success', ...readPolicy(stored) });
    } catch (error) {
      next(error);
    }
  };
}

export function putIdentityPolicy(keyValueStoreService: KeyValueStoreService) {
  return async (
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const policy = req.body as ReturnType<
        typeof identityPolicyBodySchema.parse
      >;
      const encrypted = cipher().encrypt(JSON.stringify(policy));
      // One compare-and-set against what this save started from. Retrying
      // against the winner's value would overwrite a concurrent admin's edit.
      const current = await keyValueStoreService.get<string>(
        configPaths.aiGatewayIdentityPolicy,
      );
      const wrote = await keyValueStoreService.compareAndSet<string>(
        configPaths.aiGatewayIdentityPolicy,
        current ?? null,
        encrypted,
      );
      if (!wrote) {
        res.status(409).json({
          status: 'error',
          message: 'The policy changed while saving. Reload and try again.',
        });
        return;
      }
      res.status(200).json({ status: 'success', policy });
    } catch (error) {
      next(error);
    }
  };
}

type ConnectionForwardingBody = {
  enabled: boolean;
  mode: string;
  preset: string;
  headerTemplates: Record<string, string>;
  jwtAudience?: string;
  serviceAuth: string;
  serviceApiKey?: string;
};

/**
 * One write for every entry of a connection, inside the repository's
 * compare-and-set loop, so a connection's models never disagree about whether
 * identity is forwarded.
 */
export function setConnectionIdentityForwarding(
  keyValueStoreService: KeyValueStoreService,
  eventService: AiConfigEventProducer,
  appConfig: AppConfig,
) {
  return async (
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const connectionId = req.params.connectionId;
      const body = req.body as ConnectionForwardingBody;
      const keys = IDENTITY_FORWARDING_KEYS;
      let updated = 0;
      const updatedTypes = new Set<string>();
      await new AiModelsConfigRepository(keyValueStoreService).mutate(
        (aiModels) => {
          updated = 0;
          updatedTypes.clear();
          for (const modelType of AI_MODEL_TYPES) {
            for (const entry of aiModels[modelType] ?? []) {
              if (entry.connectionId !== connectionId) continue;
              const configuration: Record<string, unknown> = {
                ...entry.configuration,
              };
              configuration[keys.enabled] = body.enabled;
              configuration[keys.mode] = body.mode;
              configuration[keys.preset] = body.preset;
              configuration[keys.headerTemplates] = restoreCredentialTemplates(
                body.headerTemplates,
                entry.configuration[keys.headerTemplates],
              );
              configuration[keys.serviceAuth] = body.serviceAuth;
              if (body.jwtAudience) {
                configuration[keys.jwtAudience] = body.jwtAudience;
              } else {
                Reflect.deleteProperty(configuration, keys.jwtAudience);
              }
              if (body.serviceApiKey !== undefined) {
                if (body.serviceApiKey === '') {
                  Reflect.deleteProperty(configuration, keys.serviceApiKey);
                } else {
                  configuration[keys.serviceApiKey] = body.serviceApiKey;
                }
              }
              entry.configuration = configuration;
              updated += 1;
              updatedTypes.add(modelType);
            }
          }
        },
      );
      if (updated === 0) {
        res
          .status(404)
          .json({ status: 'error', message: 'Connection not found' });
        return;
      }
      await publishModelsReconfigured(eventService, appConfig, updatedTypes);
      res.status(200).json({ status: 'success', connectionId, updated });
    } catch (error) {
      next(error);
    }
  };
}

/**
 * Running query processes keep their LLM and embedding clients until a
 * configured event arrives, so a changed forwarding setting would otherwise
 * apply only after a restart. The settings are already saved, so a failed
 * publish is logged and does not fail the request.
 */
async function publishModelsReconfigured(
  eventService: AiConfigEventProducer,
  appConfig: AppConfig,
  modelTypes: ReadonlySet<string>,
): Promise<void> {
  const credentialsRoute = `${appConfig.cmBackend}/${aiModelRoute}`;
  const llmPayload: LLMConfiguredEvent = { credentialsRoute };
  const embeddingPayload: EmbeddingModelConfiguredEvent = { credentialsRoute };
  const events: Event[] = [];
  if ([...modelTypes].some((type) => type !== 'embedding')) {
    events.push({
      eventType: EventType.LLMConfiguredEvent,
      timestamp: Date.now(),
      payload: llmPayload,
    });
  }
  if (modelTypes.has('embedding')) {
    events.push({
      eventType: EventType.EmbeddingModelConfiguredEvent,
      timestamp: Date.now(),
      payload: embeddingPayload,
    });
  }
  if (events.length === 0) return;
  try {
    await eventService.start();
    for (const event of events) {
      await eventService.publishEvent(event);
    }
    await eventService.stop();
  } catch (error) {
    logger.error('Failed to publish the identity forwarding change', { error });
  }
}

export function previewIdentityForwarding(
  keyValueStoreService: KeyValueStoreService,
  appConfig: AppConfig,
) {
  return async (
    req: Request,
    res: Response,
    next: NextFunction,
  ): Promise<void> => {
    try {
      const body = req.body as {
        userId?: string;
        workload: string;
        configuration: Record<string, unknown>;
        connectionId?: string;
      };
      const keys = IDENTITY_FORWARDING_KEYS;
      let configuration = { ...body.configuration };
      if (body.connectionId) {
        // Stored settings, including the service key the browser never sees.
        const stored = await new AiModelsConfigRepository(
          keyValueStoreService,
        ).read();
        const member = AI_MODEL_TYPES.flatMap(
          (type) => stored?.[type] ?? [],
        ).find((entry) => entry.connectionId === body.connectionId);
        if (!member) {
          res
            .status(404)
            .json({ status: 'error', message: 'Connection not found' });
          return;
        }
        const submitted = configuration[keys.headerTemplates];
        configuration = { ...member.configuration, ...configuration };
        if (
          submitted &&
          typeof submitted === 'object' &&
          !Array.isArray(submitted)
        ) {
          configuration[keys.headerTemplates] = restoreCredentialTemplates(
            submitted as Record<string, string>,
            member.configuration[keys.headerTemplates],
          );
        }
      }
      const options: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/ai-models/identity/preview`,
        method: HttpMethod.POST,
        headers: req.headers as Record<string, string>,
        body: { userId: body.userId, workload: body.workload, configuration },
      };
      const response = (await new AIServiceCommand(options).execute()) as
        | AIServiceResponse
        | undefined;
      res
        .status(response?.statusCode ?? 502)
        .json(response?.data ?? { status: 'error' });
    } catch (error) {
      next(error);
    }
  };
}
