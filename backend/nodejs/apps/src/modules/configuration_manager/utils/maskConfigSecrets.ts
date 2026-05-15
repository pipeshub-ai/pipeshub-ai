import type { AIModelConfiguration } from '../types/ai-models.types';

/** Placeholder shown in API responses when sensitive values are hidden. */
export const CONFIG_SECRET_PLACEHOLDER = '****************';

/**
 * Keys inside an AI model `configuration` object that are NOT secrets and
 * should always be returned to the client as-is.
 */
const AI_CONFIG_NON_SECRET_KEYS = new Set([
  'model',
  'modelname',
  'modelfriendlyname',
]);

/**
 * Mask every string field inside an AI model entry's `configuration` object,
 * except for the model-name fields (`model`, `modelName`, `modelFriendlyName`).
 * All other top-level entry fields (provider, modelKey, isMultimodal, isDefault,
 * isReasoning, contextLength, …) are left completely untouched.
 *
 * Exported so callers that hold a single entry (e.g. update/delete responses)
 * can mask it directly without going through maskAiModelsStoredConfig.
 */
export function maskAiModelEntry(entry: AIModelConfiguration): AIModelConfiguration {
  const cfg = entry.configuration;
  if (!cfg || typeof cfg !== 'object' || Array.isArray(cfg)) {
    return entry;
  }

  const maskedCfg: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(cfg as Record<string, unknown>)) {
    if (AI_CONFIG_NON_SECRET_KEYS.has(key.toLowerCase())) {
      maskedCfg[key] = value;
    } else if (typeof value === 'string' && value.length > 0) {
      maskedCfg[key] = CONFIG_SECRET_PLACEHOLDER;
    } else {
      maskedCfg[key] = value;
    }
  }

  return { ...entry, configuration: maskedCfg as AIModelConfiguration['configuration'] };
}

/**
 * Mask all AI model entries in the stored config object.
 *
 * The stored shape is:
 *   { llm: [...entries], embedding: [...entries], ocr: [...], … }
 *
 * Each entry looks like:
 *   { provider, configuration: { model, modelFriendlyName, apiKey, … }, modelKey,
 *     isMultimodal, isDefault, isReasoning, contextLength }
 */
export function maskAiModelsStoredConfig<T extends Record<string, unknown>>(
  config: T,
): T {
  if (!config || typeof config !== 'object') {
    return config;
  }

  const result: Record<string, unknown> = {};
  for (const [bucket, entries] of Object.entries(config as Record<string, unknown>)) {
    if (!Array.isArray(entries)) {
      result[bucket] = entries;
      continue;
    }
    result[bucket] = entries.map((entry: unknown) => {
      if (entry && typeof entry === 'object' && !Array.isArray(entry)) {
        return maskAiModelEntry(entry as AIModelConfiguration);
      }
      return entry;
    });
  }
  return result as T;
}

/**
 * Mask sensitive fields in an SMTP config object.
 * Only `password` is considered secret; all other fields are returned as-is.
 */
export function maskSmtpConfig<T extends Record<string, unknown>>(config: T): T {
  if (!config || typeof config !== 'object') {
    return config;
  }
  const out = { ...config } as Record<string, unknown>;
  if (typeof out['password'] === 'string' && out['password'].length > 0) {
    out['password'] = CONFIG_SECRET_PLACEHOLDER;
  }
  return out as T;
}
