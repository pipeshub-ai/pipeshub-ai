import { NextFunction, Request, Response } from 'express';
import { KeyValueStoreService } from '../../../libs/services/keyValueStore.service';
import { Logger } from '../../../libs/services/logger.service';
import { AIModelConfiguration } from '../../configuration_manager/types/ai-models.types';
import { readStoredAiModelsConfig } from '../../configuration_manager/utils/util';

const logger = Logger.getInstance({ service: 'enterprise-search' });

export interface DefaultChatModel {
  modelKey: string;
  modelName: string;
  modelProvider?: string;
  modelFriendlyName?: string;
}

/**
 * The model the AI backend answers with when a chat names none: the LLM marked
 * default, else the first one, and the first name in its model list
 * (`get_model_config` / `get_llm_for_chat` in `chatbot.py`).
 */
export const pickDefaultChatModel = (
  llms: unknown,
): DefaultChatModel | null => {
  if (!Array.isArray(llms) || llms.length === 0) return null;
  const configs = llms as Array<Partial<AIModelConfiguration> | null>;
  const config = configs.find((llm) => llm?.isDefault === true) ?? configs[0];
  const modelNames = String(config?.configuration?.model ?? '')
    .split(',')
    .map((name) => name.trim())
    .filter(Boolean);
  const [modelName] = modelNames;
  const modelKey = config?.modelKey;
  if (modelKey === undefined || modelKey === '' || modelName === undefined) {
    return null;
  }
  // The models list offers a friendly name only for a single-model entry, so an explicit pick carries it only then.
  const friendlyName =
    modelNames.length === 1 && typeof config?.modelFriendlyName === 'string'
      ? config.modelFriendlyName.trim()
      : '';
  const provider = config?.provider ?? '';
  return {
    modelKey,
    modelName,
    ...(provider !== '' ? { modelProvider: provider } : {}),
    ...(friendlyName !== '' ? { modelFriendlyName: friendlyName } : {}),
  };
};

const namesAModel = (body: Record<string, unknown>): boolean =>
  (typeof body.modelKey === 'string' && body.modelKey !== '') ||
  (typeof body.modelName === 'string' && body.modelName !== '');

/**
 * Names the org's default model on a chat request that names none, so the
 * conversation records which model answered and the AI backend answers with
 * that same model. When it can't be read, the AI backend still picks the
 * default itself; only the record of it is lost.
 */
export const fillDefaultChatModel =
  (keyValueStoreService?: KeyValueStoreService) =>
  async (req: Request, _res: Response, next: NextFunction): Promise<void> => {
    const body = req.body as Record<string, unknown> | undefined;
    if (
      keyValueStoreService === undefined ||
      body === undefined ||
      namesAModel(body)
    ) {
      next();
      return;
    }
    try {
      const aiModels = await readStoredAiModelsConfig(keyValueStoreService);
      const model = pickDefaultChatModel(aiModels?.llm);
      if (model !== null) Object.assign(body, model);
    } catch (error: unknown) {
      logger.warn(
        'Could not read the default chat model; the AI backend will choose it',
        { error: error instanceof Error ? error.message : String(error) },
      );
    }
    next();
  };
