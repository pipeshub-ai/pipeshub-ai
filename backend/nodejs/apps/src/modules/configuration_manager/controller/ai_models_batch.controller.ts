import { randomUUID } from 'crypto';
import { Request, Response, NextFunction } from 'express';

import { AppConfig } from '../../tokens_manager/config/config';
import {
  AICommandOptions,
  AIServiceCommand,
} from '../../../libs/commands/ai_service/ai.service.command';
import { HttpMethod } from '../../../libs/enums/http-methods.enum';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { Logger } from '../../../libs/services/logger.service';
import {
  AI_MODEL_TYPES,
  AIServiceResponse,
  aiModelRoute,
  isAIModelType,
} from '../constants/constants';
import {
  AiConfigEventProducer,
  EmbeddingModelConfiguredEvent,
  Event,
  EventType,
  LLMConfiguredEvent,
} from '../services/kafka_events.service';
import {
  AiModelsConfigRepository,
  findModelEntry,
} from '../services/aiModelsConfig.repository';
import { AIModelConfiguration } from '../types/ai-models.types';
import { mergeAiModelCredentials, pickAiModelCredentials } from '../utils/maskConfigSecrets';

const logger = Logger.getInstance({ service: 'AI Models Batch Controller' });
const BATCH_CONCURRENCY = 3;

type BatchModel = {
  model: string;
  modelFriendlyName?: string;
  isMultimodal: boolean;
  isReasoning: boolean;
  contextLength?: number | null;
};

async function publishConfigured(
  eventService: AiConfigEventProducer,
  appConfig: AppConfig,
  modelType: string,
) {
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
  try {
    await eventService.start();
    await eventService.publishEvent(event);
    await eventService.stop();
  } catch (error) {
    logger.error('Error sending AI model event', { error });
  }
}

async function healthCheckModel(
  appConfig: AppConfig,
  headers: Record<string, string>,
  modelType: string,
  provider: string,
  configuration: Record<string, unknown>,
  flags: {
    isMultimodal: boolean;
    isReasoning: boolean;
    isDefault: boolean;
    contextLength?: number | null;
    becomesActive?: boolean;
  },
): Promise<{ ok: boolean; message: string }> {
  const options: AICommandOptions = {
    uri: `${appConfig.aiBackend}/api/v1/health-check/${encodeURIComponent(modelType)}`,
    method: HttpMethod.POST,
    headers,
    body: {
      provider,
      configuration,
      modelType,
      ...flags,
    },
  };
  const response = (await new AIServiceCommand(options).execute()) as AIServiceResponse;
  if (response?.statusCode === 200) {
    return { ok: true, message: 'healthy' };
  }
  const data = (response?.data ?? {}) as { message?: string; error?: { message?: string } };
  return {
    ok: false,
    message: data.message || data.error?.message || 'Health check failed',
  };
}

function writeSse(res: Response, event: string, data: unknown) {
  res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

export function discoverAIModels(
  keyValueStoreService: KeyValueStoreService,
  appConfig: AppConfig,
) {
  return async (req: Request, res: Response, next: NextFunction) => {
    try {
      const body = req.body as {
        provider: string;
        capability?: string;
        configuration?: Record<string, unknown>;
        modelKey?: string;
        query?: string;
      };
      let provider = body.provider;
      let configuration = { ...(body.configuration ?? {}) };
      if (body.modelKey) {
        const stored = await new AiModelsConfigRepository(keyValueStoreService).read();
        const found = stored ? findModelEntry(stored, body.modelKey) : null;
        if (!found) {
          res.status(404).json({ status: 'error', message: 'Model not found' });
          return;
        }
        if (provider && provider !== found.model.provider) {
          res.status(400).json({ status: 'error', message: 'provider does not match saved model' });
          return;
        }
        provider = found.model.provider;
        // Stored credentials must stay on the saved connection. A request
        // endpoint here would send the saved API key to a different host.
        delete configuration.endpoint;
        configuration = mergeAiModelCredentials(configuration, found.model.configuration);
      }
      const options: AICommandOptions = {
        uri: `${appConfig.aiBackend}/api/v1/ai-models/discover`,
        method: HttpMethod.POST,
        headers: req.headers as Record<string, string>,
        body: {
          provider,
          capability: body.capability,
          configuration,
          query: body.query,
        },
      };
      const response = (await new AIServiceCommand(options).execute()) as AIServiceResponse;
      res.status(response?.statusCode ?? 502).json(response?.data ?? { status: 'error' });
    } catch (error) {
      next(error);
    }
  };
}

export function batchAddAIModels(
  keyValueStoreService: KeyValueStoreService,
  eventService: AiConfigEventProducer,
  appConfig: AppConfig,
) {
  return async (req: Request, res: Response, next: NextFunction) => {
    try {
      const {
        modelType,
        provider,
        configuration,
        models,
        defaultModel,
        connectionId: requestedConnectionId,
      } = req.body as {
        modelType: string;
        provider: string;
        configuration: Record<string, unknown>;
        models: BatchModel[];
        defaultModel?: string;
        connectionId?: string;
      };
      if (!isAIModelType(modelType)) {
        res.status(400).json({
          status: 'error',
          message: `Invalid model type. Must be one of: ${AI_MODEL_TYPES.join(', ')}`,
        });
        return;
      }
      const repo = new AiModelsConfigRepository(keyValueStoreService);
      if (requestedConnectionId) {
        const stored = await repo.read();
        const joinable = AI_MODEL_TYPES.some((type) =>
          (stored?.[type] ?? []).some(
            (entry) => entry.connectionId === requestedConnectionId && entry.provider === provider,
          ),
        );
        if (!joinable) {
          res.status(400).json({
            status: 'error',
            message: 'connectionId does not match a saved connection for this provider',
          });
          return;
        }
      }

      // Only this embedding may resize the vector store. A failed check must
      // not leave a different model as the default: that model was not checked
      // as the one that will embed.
      let embeddingActiveId: string | undefined;
      if (modelType === 'embedding') {
        const storedModels = await repo.read();
        const existing = storedModels?.embedding ?? [];
        if (defaultModel || existing.length === 0) {
          embeddingActiveId = defaultModel ?? models[0]?.model;
        }
      }

      res.setHeader('Content-Type', 'text/event-stream');
      res.setHeader('Cache-Control', 'no-cache');
      res.setHeader('Connection', 'keep-alive');
      res.flushHeaders?.();

      let aborted = false;
      // `req` emits `close` when the JSON body has been read, which is before
      // this stream finishes. Watch the response and ignore the close that
      // `res.end()` emits on a normal completion.
      res.on('close', () => {
        if (res.writableEnded || res.writableFinished) return;
        aborted = true;
      });

      const connectionId = requestedConnectionId ?? randomUUID();
      const shared = { ...configuration };
      delete shared.model;
      delete shared.modelFriendlyName;
      let saved = 0;
      let cursor = 0;

      const runOne = async (model: BatchModel) => {
        if (aborted) {
          return;
        }
        writeSse(res, 'progress', { model: model.model, status: 'checking' });
        const modelConfiguration = {
          ...shared,
          model: model.model,
          ...(model.modelFriendlyName ? { modelFriendlyName: model.modelFriendlyName } : {}),
        };
        const isDefault = model.model === defaultModel;
        const health = await healthCheckModel(
          appConfig,
          req.headers as Record<string, string>,
          modelType,
          provider,
          modelConfiguration,
          {
            isMultimodal: model.isMultimodal,
            isReasoning: model.isReasoning,
            isDefault,
            contextLength: model.contextLength,
            ...(modelType === 'embedding'
              ? { becomesActive: model.model === embeddingActiveId }
              : {}),
          },
        );
        if (aborted) {
          return;
        }
        if (!health.ok) {
          writeSse(res, 'progress', {
            model: model.model,
            status: 'failed',
            message: health.message,
          });
          return;
        }
        const modelKey = randomUUID();
        await repo.mutate((aiModels) => {
          const bucket = aiModels[modelType] ?? [];
          aiModels[modelType] = bucket;
          // LLM: the first model of an empty type becomes default even if the
          // chosen one failed. Embeddings do not: only the model checked with
          // becomesActive may take over the vector store.
          const makeDefault =
            modelType === 'embedding'
              ? model.model === embeddingActiveId
              : isDefault || bucket.length === 0;
          if (makeDefault) {
            for (const entry of bucket) {
              entry.isDefault = false;
            }
          }
          const entry: AIModelConfiguration = {
            provider,
            configuration: modelConfiguration,
            modelKey,
            connectionId,
            isMultimodal: model.isMultimodal,
            isDefault: makeDefault,
            isReasoning: model.isReasoning,
            contextLength: model.contextLength,
            ...(model.modelFriendlyName ? { modelFriendlyName: model.modelFriendlyName } : {}),
          };
          bucket.push(entry);
        });
        saved += 1;
        writeSse(res, 'progress', {
          model: model.model,
          status: 'healthy',
          modelKey,
          connectionId,
        });
      };

      const workers = Array.from({ length: Math.min(BATCH_CONCURRENCY, models.length) }, async () => {
        while (!aborted) {
          const index = cursor;
          cursor += 1;
          if (index >= models.length) {
            return;
          }
          const model = models[index];
          if (!model) {
            return;
          }
          await runOne(model);
        }
      });
      await Promise.all(workers);

      if (saved > 0 && !aborted) {
        await publishConfigured(eventService, appConfig, modelType);
      }
      writeSse(res, 'done', { connectionId, saved, aborted });
      res.end();
    } catch (error) {
      if (!res.headersSent) {
        next(error);
        return;
      }
      writeSse(res, 'error', { message: 'Batch add failed' });
      res.end();
    }
  };
}

export function rotateConnectionCredentials(
  keyValueStoreService: KeyValueStoreService,
  appConfig: AppConfig,
) {
  return async (req: Request, res: Response, next: NextFunction) => {
    try {
      const connectionId = req.params.connectionId;
      const incoming = pickAiModelCredentials(
        (req.body.configuration ?? {}) as Record<string, unknown>,
      );
      const repo = new AiModelsConfigRepository(keyValueStoreService);
      const stored = await repo.read();
      const members: { modelType: string; entry: AIModelConfiguration }[] = [];
      if (stored) {
        for (const modelType of AI_MODEL_TYPES) {
          for (const entry of stored[modelType] ?? []) {
            if (entry.connectionId === connectionId) {
              members.push({ modelType, entry });
            }
          }
        }
      }
      const sample = members[0];
      if (!sample) {
        res.status(404).json({ status: 'error', message: 'Connection not found' });
        return;
      }
      const merged = mergeAiModelCredentials(incoming, sample.entry.configuration);
      const health = await healthCheckModel(
        appConfig,
        req.headers as Record<string, string>,
        sample.modelType,
        sample.entry.provider,
        merged,
        {
          isMultimodal: Boolean(sample.entry.isMultimodal),
          isReasoning: Boolean(sample.entry.isReasoning),
          isDefault: Boolean(sample.entry.isDefault),
          contextLength: sample.entry.contextLength,
          becomesActive: false,
        },
      );
      if (!health.ok) {
        res.status(400).json({ status: 'error', message: health.message });
        return;
      }
      let updated = 0;
      await repo.mutate((aiModels) => {
        for (const modelType of AI_MODEL_TYPES) {
          for (const entry of aiModels[modelType] ?? []) {
            if (entry.connectionId !== connectionId) {
              continue;
            }
            entry.configuration = mergeAiModelCredentials(incoming, entry.configuration);
            updated += 1;
          }
        }
      });
      res.status(200).json({ status: 'success', connectionId, updated });
    } catch (error) {
      next(error);
    }
  };
}
