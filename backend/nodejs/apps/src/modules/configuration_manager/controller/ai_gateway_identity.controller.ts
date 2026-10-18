import { NextFunction, Request, Response } from 'express';

import { AppConfig } from '../../tokens_manager/config/config';
import {
  AICommandOptions,
  AIServiceCommand,
} from '../../../libs/commands/ai_service/ai.service.command';
import { EncryptionService } from '../../../libs/encryptor/encryptor';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { loadConfigurationManagerConfig } from '../config/config';
import { AI_MODEL_TYPES, AIServiceResponse } from '../constants/constants';
import { configPaths } from '../paths/paths';
import { AiModelsConfigRepository } from '../services/aiModelsConfig.repository';
import { IDENTITY_FORWARDING_KEYS } from '../utils/maskConfigSecrets';
import { identityPolicyBodySchema } from '../validator/validators';

const POLICY_CAS_MAX_RETRIES = 5;

const DEFAULT_POLICY = identityPolicyBodySchema.parse({});

function cipher(): EncryptionService {
  const config = loadConfigurationManagerConfig();
  return EncryptionService.getInstance(config.algorithm, config.secretKey);
}

function readPolicy(ciphertext: string | null | undefined) {
  if (!ciphertext) return DEFAULT_POLICY;
  // Unreadable or out-of-shape data answers the default. The default is the
  // most restrictive policy, so a bad blob never widens what is forwarded.
  try {
    return identityPolicyBodySchema.parse(JSON.parse(cipher().decrypt(ciphertext)));
  } catch {
    return DEFAULT_POLICY;
  }
}

export function getIdentityPolicy(keyValueStoreService: KeyValueStoreService) {
  return async (_req: Request, res: Response, next: NextFunction) => {
    try {
      const stored = await keyValueStoreService.get<string>(configPaths.aiGatewayIdentityPolicy);
      res.status(200).json({ status: 'success', policy: readPolicy(stored) });
    } catch (error) {
      next(error);
    }
  };
}

export function putIdentityPolicy(keyValueStoreService: KeyValueStoreService) {
  return async (req: Request, res: Response, next: NextFunction) => {
    try {
      const policy = req.body as ReturnType<typeof identityPolicyBodySchema.parse>;
      const encrypted = cipher().encrypt(JSON.stringify(policy));
      for (let attempt = 0; attempt < POLICY_CAS_MAX_RETRIES; attempt++) {
        const current = await keyValueStoreService.get<string>(configPaths.aiGatewayIdentityPolicy);
        const wrote = await keyValueStoreService.compareAndSet<string>(
          configPaths.aiGatewayIdentityPolicy,
          current ?? null,
          encrypted,
        );
        if (wrote) {
          res.status(200).json({ status: 'success', policy });
          return;
        }
      }
      res.status(409).json({
        status: 'error',
        message: 'The policy changed while saving. Reload and try again.',
      });
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
export function setConnectionIdentityForwarding(keyValueStoreService: KeyValueStoreService) {
  return async (req: Request, res: Response, next: NextFunction) => {
    try {
      const connectionId = req.params.connectionId;
      const body = req.body as ConnectionForwardingBody;
      const keys = IDENTITY_FORWARDING_KEYS;
      let updated = 0;
      await new AiModelsConfigRepository(keyValueStoreService).mutate((aiModels) => {
        updated = 0;
        for (const modelType of AI_MODEL_TYPES) {
          for (const entry of aiModels[modelType] ?? []) {
            if (entry.connectionId !== connectionId) continue;
            const configuration = { ...(entry.configuration ?? {}) } as Record<string, unknown>;
            configuration[keys.enabled] = body.enabled;
            configuration[keys.mode] = body.mode;
            configuration[keys.preset] = body.preset;
            configuration[keys.headerTemplates] = body.headerTemplates;
            configuration[keys.serviceAuth] = body.serviceAuth;
            if (body.jwtAudience) {
              configuration[keys.jwtAudience] = body.jwtAudience;
            } else {
              delete configuration[keys.jwtAudience];
            }
            if (body.serviceApiKey !== undefined) {
              if (body.serviceApiKey === '') {
                delete configuration[keys.serviceApiKey];
              } else {
                configuration[keys.serviceApiKey] = body.serviceApiKey;
              }
            }
            entry.configuration = configuration;
            updated += 1;
          }
        }
      });
      if (updated === 0) {
        res.status(404).json({ status: 'error', message: 'Connection not found' });
        return;
      }
      res.status(200).json({ status: 'success', connectionId, updated });
    } catch (error) {
      next(error);
    }
  };
}

export function previewIdentityForwarding(
  keyValueStoreService: KeyValueStoreService,
  appConfig: AppConfig,
) {
  return async (req: Request, res: Response, next: NextFunction) => {
    try {
      const body = req.body as {
        userId?: string;
        workload: string;
        configuration: Record<string, unknown>;
        connectionId?: string;
      };
      let configuration = { ...body.configuration };
      if (body.connectionId) {
        // Stored settings, including the service key the browser never sees.
        const stored = await new AiModelsConfigRepository(keyValueStoreService).read();
        const member = AI_MODEL_TYPES.flatMap((type) => stored?.[type] ?? []).find(
          (entry) => entry.connectionId === body.connectionId,
        );
        if (!member) {
          res.status(404).json({ status: 'error', message: 'Connection not found' });
          return;
        }
        configuration = { ...member.configuration, ...configuration };
      }
      const options: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/ai-models/identity/preview`,
        method: HttpMethod.POST,
        headers: req.headers as Record<string, string>,
        body: { userId: body.userId, workload: body.workload, configuration },
      };
      const response = (await new AIServiceCommand(options).execute()) as AIServiceResponse;
      res.status(response?.statusCode ?? 502).json(response?.data ?? { status: 'error' });
    } catch (error) {
      next(error);
    }
  };
}
