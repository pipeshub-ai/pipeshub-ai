import type { AIModelConfiguration } from '../types/ai-models.types';

/** Placeholder shown in API responses when sensitive values are hidden. */
export const CONFIG_SECRET_PLACEHOLDER = '****************';

export function stripSecretKeys<T extends Record<string, unknown>>(
  config: T,
  secretKeys: readonly string[],
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...config };
  for (const key of secretKeys) {
    delete out[key];
  }
  return out;
}

export function omitInheritedSecrets<T extends Record<string, unknown>>(
  config: T,
  secretKeys: readonly string[],
): Record<string, unknown> {
  return { ...stripSecretKeys(config, secretKeys), inherited: true };
}

/**
 * User-facing GET `configuration` is an allowlist. Provider-specific keys
 * (region, voice, credentials, …) stay out of the response; values are copied
 * as stored. Top-level entry fields are left untouched.
 */
export const AI_PUBLIC_CONFIG_KEYS = [
  'model',
  'modelFriendlyName',
  'dimensions',
] as const;

export function stripAiModelSecrets(entry: AIModelConfiguration): AIModelConfiguration {
  const cfg = entry.configuration;
  if (!cfg || typeof cfg !== 'object' || Array.isArray(cfg)) {
    return entry;
  }

  const src = cfg as Record<string, unknown>;
  const safeCfg: Record<string, unknown> = {};
  for (const key of AI_PUBLIC_CONFIG_KEYS) {
    if (key in src) {
      safeCfg[key] = src[key];
    }
  }

  return { ...entry, configuration: safeCfg as AIModelConfiguration['configuration'] };
}

/**
 * Apply the public `configuration` allowlist to every AI model entry.
 *
 * The stored shape is:
 *   { llm: [...entries], embedding: [...entries], ocr: [...], modelRoles: {…} }
 */
export function stripAiModelsStoredConfig<T extends Record<string, unknown>>(
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
        return stripAiModelSecrets(entry as AIModelConfiguration);
      }
      return entry;
    });
  }
  return result as T;
}

/**
 * GET only returns the public allowlist, so an update legitimately omits
 * credentials and other provider keys the user did not retype. Carry those
 * forward from storage.
 *
 * Only *absent* keys are restored. A key present but empty is an explicit clear
 * (e.g. dropping `awsAccessKeyId` to fall back to the EC2 IAM role) and is left
 * alone.
 */
export function mergeAiModelCredentials<T extends Record<string, unknown>>(
  incoming: T,
  existing: Record<string, unknown> | null | undefined,
): T {
  if (!existing || typeof existing !== 'object' || Array.isArray(existing)) {
    return incoming;
  }
  const out = { ...incoming } as Record<string, unknown>;
  for (const key of Object.keys(existing)) {
    if (!(key in out)) {
      out[key] = existing[key];
    }
  }
  return out as T;
}

/**
 * Mask sensitive fields in an SMTP config object.
 * `host`, `username`, `fromEmail`, and `password` are all considered secrets;
 * all other fields (e.g. `port`) are returned as-is.
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

export const GOOGLE_AUTH_SECRET_KEYS = ['clientId'] as const;


export const MICROSOFT_AUTH_SECRET_KEYS = ['clientId', 'tenantId', 'authority'] as const;

export const OAUTH_SECRET_KEYS = ['clientId', 'clientSecret'] as const;

export const GITHUB_AUTH_SECRET_KEYS = ['clientId', 'clientSecret'] as const;

export const WEB_SEARCH_SECRET_KEYS = ['apiKey'] as const;

function maskKeys<T extends Record<string, unknown>>(config: T, keys: readonly string[]): T {
  if (!config || typeof config !== 'object') {
    return config;
  }
  const out = { ...config } as Record<string, unknown>;
  for (const key of keys) {
    if (typeof out[key] === 'string' && (out[key] as string).length > 0) {
      out[key] = CONFIG_SECRET_PLACEHOLDER;
    }
  }
  return out as T;
}

export function maskGoogleAuthConfig<T extends Record<string, unknown>>(config: T): T {
  return maskKeys(config, GOOGLE_AUTH_SECRET_KEYS);
}

export function maskMicrosoftAuthConfig<T extends Record<string, unknown>>(config: T): T {
  return maskKeys(config, MICROSOFT_AUTH_SECRET_KEYS);
}

export function maskOAuthConfig<T extends Record<string, unknown>>(config: T): T {
  return maskKeys(config, OAUTH_SECRET_KEYS);
}

/** Mask sensitive fields in a GitHub OAuth config object. */
export function maskGithubAuthConfig<T extends Record<string, unknown>>(config: T): T {
  return maskKeys(config, GITHUB_AUTH_SECRET_KEYS);
}

export function maskWebSearchProvider<T extends Record<string, unknown>>(configuration: T): T {
  return maskKeys(configuration, WEB_SEARCH_SECRET_KEYS);
}

export function mergeWebSearchProviderPlaceholders<T extends Record<string, unknown>>(
  incoming: T,
  existing: Record<string, unknown> | null | undefined,
): T {
  if (!existing || typeof existing !== 'object') {
    return incoming;
  }
  const out = { ...incoming } as Record<string, unknown>;
  if (out['apiKey'] === CONFIG_SECRET_PLACEHOLDER && typeof existing['apiKey'] === 'string') {
    out['apiKey'] = existing['apiKey'];
  }
  return out as T;
}
