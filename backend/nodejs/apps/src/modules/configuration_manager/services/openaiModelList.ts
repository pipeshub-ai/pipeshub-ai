import { AI_MODEL_TYPES } from '../constants/constants';
import { AIModelsConfig } from '../types/ai-models.types';

/**
 * Task values an OpenAI-compatible client can filter on. Bucket order matches
 * ``AI_MODEL_TYPES`` so the list is stable across Node and the query service.
 */
const TASK_BY_TYPE: Record<string, string> = {
  llm: 'chat',
  embedding: 'embedding',
  ocr: 'ocr',
  slm: 'chat',
  reasoning: 'chat',
  multiModal: 'chat',
  imageGeneration: 'image',
  tts: 'text-to-speech',
  stt: 'automatic-speech-recognition',
};

export interface OpenAIModelObject {
  id: string;
  object: 'model';
  created: number;
  owned_by: string;
  task: string;
  root: string;
  parent: null;
}

export interface OpenAIModelList {
  object: 'list';
  data: OpenAIModelObject[];
}

function createdSeconds(entry: Record<string, unknown>): number {
  for (const key of ['updatedAt', 'createdAt'] as const) {
    const raw = entry[key];
    if (typeof raw !== 'number' || !Number.isFinite(raw) || raw <= 0) {
      continue;
    }
    return raw > 10_000_000_000 ? Math.floor(raw / 1000) : Math.floor(raw);
  }
  return 0;
}

function modelNames(entry: Record<string, unknown>): string[] {
  const configuration = entry.configuration;
  if (!configuration || typeof configuration !== 'object') {
    return [];
  }
  const raw = (configuration as Record<string, unknown>).model;
  if (typeof raw !== 'string') {
    return [];
  }
  return raw
    .split(',')
    .map((name) => name.trim())
    .filter(Boolean);
}

function toModelObject(
  entry: Record<string, unknown>,
  modelId: string,
  task: string,
): OpenAIModelObject {
  const provider = entry.provider;
  const ownedBy =
    typeof provider === 'string' && provider.trim() ? provider.trim() : 'pipeshub';
  return {
    id: modelId,
    object: 'model',
    created: createdSeconds(entry),
    owned_by: ownedBy,
    task,
    root: modelId,
    parent: null,
  };
}

/** OpenAI ``GET /v1/models`` body. Credentials are not copied onto the objects. */
export function toOpenAIModelList(aiModels: AIModelsConfig | null | undefined): OpenAIModelList {
  const data: OpenAIModelObject[] = [];
  const stored = (aiModels ?? {}) as Record<string, unknown>;
  for (const modelType of AI_MODEL_TYPES) {
    const entries = stored[modelType];
    if (!Array.isArray(entries)) {
      continue;
    }
    const task = TASK_BY_TYPE[modelType] ?? 'chat';
    for (const entry of entries) {
      if (!entry || typeof entry !== 'object') {
        continue;
      }
      const record = entry as Record<string, unknown>;
      for (const modelId of modelNames(record)) {
        data.push(toModelObject(record, modelId, task));
      }
    }
  }
  return { object: 'list', data };
}

export function findOpenAIModel(
  aiModels: AIModelsConfig | null | undefined,
  modelId: string,
): OpenAIModelObject | null {
  const wanted = modelId.trim();
  if (!wanted) {
    return null;
  }
  return toOpenAIModelList(aiModels).data.find((item) => item.id === wanted) ?? null;
}

export function openAIModelNotFound(modelId: string) {
  return {
    error: {
      message: `The model '${modelId}' does not exist`,
      type: 'invalid_request_error',
      param: 'model',
      code: 'model_not_found',
    },
  };
}
