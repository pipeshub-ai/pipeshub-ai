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

export const SMTP_SECRET_KEYS = ['host', 'username', 'fromEmail', 'password'] as const;


export function maskSmtpConfig<T extends Record<string, unknown>>(config: T): T {
  if (!config || typeof config !== 'object') {
    return config;
  }
  const out = { ...config } as Record<string, unknown>;
  for (const key of SMTP_SECRET_KEYS) {
    if (typeof out[key] === 'string' && (out[key] as string).length > 0) {
      out[key] = CONFIG_SECRET_PLACEHOLDER;
    }
  }
  return out as T;
}

/**
 * When a client re-submits masked placeholders, restore values from the stored config.
 */

export function mergeSmtpConfigPlaceholders<T extends Record<string, unknown>>(
  incoming: T,
  existing: Record<string, unknown> | null | undefined,
): T {
  if (!existing || typeof existing !== 'object') {
    return incoming;
  }
  const out = { ...incoming } as Record<string, unknown>;
  for (const key of SMTP_SECRET_KEYS) {
    if (out[key] === CONFIG_SECRET_PLACEHOLDER && typeof existing[key] === 'string') {
      out[key] = existing[key];
    }
  }
  return out as T;
}

/**
 * Mask sensitive fields in a Google auth config object.
 * `clientId` is the only credential; `enableJit` is left as-is.
 */
export function maskGoogleAuthConfig<T extends Record<string, unknown>>(config: T): T {
  if (!config || typeof config !== 'object') {
    return config;
  }
  const out = { ...config } as Record<string, unknown>;
  if (typeof out['clientId'] === 'string' && out['clientId'].length > 0) {
    out['clientId'] = CONFIG_SECRET_PLACEHOLDER;
  }
  return out as T;
}

/**
 * Mask sensitive fields in a Microsoft / Azure AD auth config object.
 * `clientId`, `tenantId`, and `authority` (which embeds the tenantId) are
 * considered secrets; `enableJit` is left as-is.
 */
export function maskMicrosoftAuthConfig<T extends Record<string, unknown>>(config: T): T {
  if (!config || typeof config !== 'object') {
    return config;
  }
  const out = { ...config } as Record<string, unknown>;
  for (const key of ['clientId', 'tenantId', 'authority'] as const) {
    if (typeof out[key] === 'string' && (out[key] as string).length > 0) {
      out[key] = CONFIG_SECRET_PLACEHOLDER;
    }
  }
  return out as T;
}

/**
 * Mask sensitive fields in a generic OAuth 2.0 config object.
 * `clientId` and `clientSecret` are secrets; all other fields
 * (providerName, authorizationUrl, tokenEndpoint, etc.) are left as-is.
 */
export function maskOAuthConfig<T extends Record<string, unknown>>(config: T): T {
  if (!config || typeof config !== 'object') {
    return config;
  }
  const out = { ...config } as Record<string, unknown>;
  for (const key of ['clientId', 'clientSecret'] as const) {
    if (typeof out[key] === 'string' && (out[key] as string).length > 0) {
      out[key] = CONFIG_SECRET_PLACEHOLDER;
    }
  }
  return out as T;
}

/**
 * Mask sensitive fields in a GitHub OAuth config object.
 * Both `clientId` and `clientSecret` are considered secrets.
 */
export function maskGithubAuthConfig<T extends Record<string, unknown>>(config: T): T {
  if (!config || typeof config !== 'object') {
    return config;
  }
  const out = { ...config } as Record<string, unknown>;
  for (const key of ['clientId', 'clientSecret'] as const) {
    if (typeof out[key] === 'string' && (out[key] as string).length > 0) {
      out[key] = CONFIG_SECRET_PLACEHOLDER;
    }
  }
  return out as T;
}
