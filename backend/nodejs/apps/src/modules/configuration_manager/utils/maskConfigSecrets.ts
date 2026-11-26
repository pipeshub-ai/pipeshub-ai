import { EXTRA_AI_PUBLIC_CONFIG_KEYS } from '../../../edition';
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
  'defaultReasoningEffort',
  // Settings only. The service API key stays out and shows up in `configuredFields`.
  'identityForwardingEnabled',
  'identityForwardingMode',
  'identityForwardingPreset',
  'identityForwardingHeaderTemplates',
  'identityForwardingJwtAudience',
  'identityForwardingServiceAuth',
] as const;

/**
 * Header names that may carry a credential. The Python preview redacts by the
 * same pattern (app/services/model_identity/preview.py); a shared contract
 * fixture (tests/contract/credential-header-hints.fixture.json) keeps the two equal.
 */
export const CREDENTIAL_HEADER_HINT =
  /auth|cookie|key|token|secret|password|credential|signature|assertion/i;

/**
 * A header template may carry a literal credential (e.g. a static bearer token
 * on `Authorization`). The value is write-only: a response keeps the header
 * name so the editor can show it, and the placeholder stands in for the value.
 */
export function redactCredentialTemplates(value: unknown): unknown {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return value;
  const out: Record<string, unknown> = {};
  for (const [name, template] of Object.entries(
    value as Record<string, unknown>,
  )) {
    out[name] =
      CREDENTIAL_HEADER_HINT.test(name) &&
      typeof template === 'string' &&
      template.length > 0
        ? CONFIG_SECRET_PLACEHOLDER
        : template;
  }
  return out;
}

/** Swap a redacted template back for the stored one. A placeholder with nothing to restore is dropped. */
export function restoreCredentialTemplates(
  incoming: Record<string, string>,
  stored: unknown,
): Record<string, string> {
  const previous =
    stored && typeof stored === 'object' && !Array.isArray(stored)
      ? (stored as Record<string, unknown>)
      : {};
  const out: Record<string, string> = {};
  for (const [name, template] of Object.entries(incoming)) {
    const storedTemplate = previous[name];
    if (template !== CONFIG_SECRET_PLACEHOLDER) {
      out[name] = template;
    } else if (
      typeof storedTemplate === 'string' &&
      storedTemplate.length > 0
    ) {
      out[name] = storedTemplate;
    }
  }
  return out;
}

export function stripAiModelSecrets(entry: AIModelConfiguration): AIModelConfiguration {
  const cfg = entry.configuration;
  if (!cfg || typeof cfg !== 'object' || Array.isArray(cfg)) {
    return entry;
  }

  const src = cfg as Record<string, unknown>;
  const safeCfg: Record<string, unknown> = {};
  const publicKeys = [...AI_PUBLIC_CONFIG_KEYS, ...EXTRA_AI_PUBLIC_CONFIG_KEYS];
  for (const key of publicKeys) {
    if (key in src) {
      safeCfg[key] = src[key];
    }
  }
  if ('identityForwardingHeaderTemplates' in safeCfg) {
    safeCfg.identityForwardingHeaderTemplates = redactCredentialTemplates(
      safeCfg.identityForwardingHeaderTemplates,
    );
  }

  return {
    ...entry,
    configuration: safeCfg as AIModelConfiguration['configuration'],
    configuredFields: configuredFieldNames(src),
  };
}

/** Names of stored configuration keys that have a value. Never the values. */
export function configuredFieldNames(configuration: Record<string, unknown>): string[] {
  return Object.entries(configuration)
    .filter(([, value]) => value !== undefined && value !== null && value !== '')
    .map(([key]) => key)
    .sort();
}

/**
 * Apply the public `configuration` allowlist to every AI model entry.
 *
 * The stored shape is:
 *   { llm: [...entries], embedding: [...entries], ocr: [...], modelRoles: {…} }
 */
export function stripAiModelsStoredConfig<T extends object>(
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
/** Keys the identity forwarding route owns on a connection's entries. Flat, so the model dialog renders them. */
export const IDENTITY_FORWARDING_KEYS = {
  enabled: 'identityForwardingEnabled',
  mode: 'identityForwardingMode',
  preset: 'identityForwardingPreset',
  headerTemplates: 'identityForwardingHeaderTemplates',
  jwtAudience: 'identityForwardingJwtAudience',
  serviceAuth: 'identityForwardingServiceAuth',
  serviceApiKey: 'identityForwardingServiceApiKey',
} as const;

/** Fields a credential rotation may replace. Endpoints and model settings are not credentials. */
export const AI_MODEL_CREDENTIAL_KEYS = [
  'apiKey',
  'awsAccessKeyId',
  'awsAccessSecretKey',
  'serviceAccountJson',
] as const;

/** Keep only credential keys. A present empty string is an explicit clear. */
export function pickAiModelCredentials(
  incoming: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of AI_MODEL_CREDENTIAL_KEYS) {
    if (!(key in incoming)) continue;
    const value = incoming[key];
    if (typeof value !== 'string') continue;
    out[key] = value;
  }
  return out;
}

/** Fields that choose where a model's credentials are sent. */
export const AI_MODEL_DESTINATION_KEYS = ['endpoint', 'baseUrl'] as const;

function normalizeDestination(value: unknown): string {
  const text = typeof value === 'string' ? value.trim() : '';
  try {
    const url = new URL(text);
    // origin is the string "null" for non-special schemes, so use protocol and host.
    // URL lower-cases both and drops a default port; a trailing dot names the
    // same host. Userinfo and the query can route a request elsewhere (a
    // proxy's target parameter), so they count too.
    const userinfo =
      url.username || url.password ? `${url.username}:${url.password}@` : '';
    const host =
      url.hostname.replace(/\.$/, '') + (url.port ? `:${url.port}` : '');
    return `${url.protocol}//${userinfo}${host}${url.pathname.replace(/\/+$/, '')}${url.search}`;
  } catch {
    // A bare host name (an SMTP host) has no scheme, and host names ignore case.
    return text.replace(/\.$/, '').toLowerCase();
  }
}

/** True when `incoming` names a different destination than `existing`. */
export function destinationValueChanged(
  incoming: unknown,
  existing: unknown,
): boolean {
  return normalizeDestination(incoming) !== normalizeDestination(existing);
}

/**
 * True when two model configurations send their credentials to different
 * places. The one definition of "same endpoint": every check uses it.
 * A blank endpoint matches only a blank one: the runtime does not fill in the
 * provider form's default (it falls back to its own environment), so a blank
 * endpoint and an explicit default can reach different hosts. The provider
 * argument is kept for callers and does not change the answer.
 */
export function aiModelDestinationsDiffer(
  a: Record<string, unknown>,
  b: Record<string, unknown>,
  _provider?: unknown,
  keys: readonly string[] = AI_MODEL_DESTINATION_KEYS,
): boolean {
  return keys.some(
    (key) => normalizeDestination(a[key]) !== normalizeDestination(b[key]),
  );
}

/** True for a header template a forwarding secret may live in. */
export function isCredentialHeaderName(name: string): boolean {
  return CREDENTIAL_HEADER_HINT.test(name);
}

/** The 400 body for a save that would send a saved secret to a different destination. */
export const CREDENTIAL_REENTRY_REQUIRED = {
  status: 'error',
  error_code: 'credential_reentry_required',
  message: 'Enter the credentials again to use this new endpoint.',
} as const;

type ConnectionMember = { modelType: string; entry: AIModelConfiguration };

/**
 * The 400 body for a new connection-wide secret when the connection's models
 * use more than one endpoint, or null when they all share the first one's.
 * Nothing is checked or saved: the secret would reach a host it was not meant for.
 */
export function connectionEndpointsDifferBody(
  members: ConnectionMember[],
): Record<string, unknown> | null {
  const home = members[0]?.entry.configuration ?? {};
  const away = members
    .filter(({ entry }) =>
      aiModelDestinationsDiffer(
        entry.configuration,
        home,
        members[0]?.entry.provider,
      ),
    )
    .map(({ modelType, entry }) => ({
      modelKey: entry.modelKey,
      modelType,
      name: String(
        entry.modelFriendlyName || entry.configuration.model || entry.modelKey,
      ),
    }));
  if (away.length === 0) return null;
  return {
    status: 'error',
    error_code: CREDENTIAL_REENTRY_REQUIRED.error_code,
    message: `These models use a different endpoint from the rest of the connection: ${away
      .map((model) => model.name)
      .join(
        ', ',
      )}. Move them back to the connection's endpoint or delete them, then save again.`,
    models: away,
  };
}

/** Like `aiModelDestinationsDiffer`, but a key absent from `incoming` is unchanged. */
export function aiModelDestinationChanged(
  incoming: Record<string, unknown>,
  existing: Record<string, unknown>,
  provider?: unknown,
): boolean {
  return aiModelDestinationsDiffer(
    incoming,
    existing,
    provider,
    AI_MODEL_DESTINATION_KEYS.filter((key) => key in incoming),
  );
}

/**
 * A copy of the saved settings without the identity forwarding credentials: the
 * service API key and any header template that holds one. The dialog never sends
 * these back, so they are dropped instead of retyped when the destination moves.
 */
export function withoutForwardingCredentials(
  existing: Record<string, unknown>,
): Record<string, unknown> {
  const dropFrom = (
    settings: Record<string, unknown>,
    keys: { serviceApiKey: string; headerTemplates: string },
  ): Record<string, unknown> => {
    const out = { ...settings };
    Reflect.deleteProperty(out, keys.serviceApiKey);
    const templates = out[keys.headerTemplates];
    if (
      templates &&
      typeof templates === 'object' &&
      !Array.isArray(templates)
    ) {
      out[keys.headerTemplates] = Object.fromEntries(
        Object.entries(templates).filter(
          ([name]) => !CREDENTIAL_HEADER_HINT.test(name),
        ),
      );
    }
    return out;
  };
  const out = dropFrom(existing, IDENTITY_FORWARDING_KEYS);
  // The nested form of the same settings wins at runtime, so it is cleaned too.
  const nested = out.identityForwarding;
  if (nested && typeof nested === 'object' && !Array.isArray(nested)) {
    out.identityForwarding = dropFrom(nested as Record<string, unknown>, {
      serviceApiKey: 'serviceApiKey',
      headerTemplates: 'headerTemplates',
    });
  }
  return out;
}

/** True when the saved settings hold a forwarding secret that `withoutForwardingCredentials` would drop. */
export function hasForwardingCredentials(
  existing: Record<string, unknown>,
): boolean {
  return (
    JSON.stringify(withoutForwardingCredentials(existing)) !==
    JSON.stringify(existing)
  );
}

/**
 * True when a saved secret would be restored into this update: it has a saved
 * value and the key is absent. A present key, even blank, replaces or clears it.
 */
export function reusesSavedAiModelCredentials(
  incoming: Record<string, unknown>,
  existing: Record<string, unknown>,
): boolean {
  const filled = (value: unknown): boolean =>
    typeof value === 'string'
      ? value.trim() !== ''
      : typeof value === 'object' &&
        value !== null &&
        Object.keys(value).length > 0;
  return AI_MODEL_CREDENTIAL_KEYS.some(
    (key) => filled(existing[key]) && !(key in incoming),
  );
}

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

/**
 * True when a form that sends a masked username or password back would, once
 * those are restored, deliver them to a different host or port. The port also
 * picks TLS (465 is implicit TLS), so a new port could send the login in clear.
 */
export function smtpHostMovedWithSavedLogin(
  incoming: Record<string, unknown>,
  existing: Record<string, unknown>,
): boolean {
  return (
    ((incoming['host'] !== CONFIG_SECRET_PLACEHOLDER &&
      destinationValueChanged(incoming['host'], existing['host'])) ||
      Number(incoming['port'] ?? 0) !== Number(existing['port'] ?? 0)) &&
    (incoming['username'] === CONFIG_SECRET_PLACEHOLDER ||
      incoming['password'] === CONFIG_SECRET_PLACEHOLDER)
  );
}

/** Where a provider sends requests when no `baseUrl` is saved. */
const WEB_SEARCH_DEFAULT_BASE_URLS: Record<string, string> = {
  firecrawl: 'https://api.firecrawl.dev',
};

/**
 * True when the saved API key is kept (the form sent the placeholder back) while
 * the provider or its `baseUrl` changes. A masked or unchanged `baseUrl` is not a
 * move, and a blank one means the provider's default.
 */
export function webSearchBaseUrlMovedWithSavedKey(
  incoming: Record<string, unknown>,
  existing: Record<string, unknown>,
  incomingProvider?: unknown,
  existingProvider?: unknown,
): boolean {
  return (
    incoming['apiKey'] === CONFIG_SECRET_PLACEHOLDER &&
    typeof existing['apiKey'] === 'string' &&
    existing['apiKey'] !== '' &&
    ((incoming['baseUrl'] !== CONFIG_SECRET_PLACEHOLDER &&
      destinationValueChanged(
        incoming['baseUrl'] ||
          WEB_SEARCH_DEFAULT_BASE_URLS[String(incomingProvider)],
        existing['baseUrl'] ||
          WEB_SEARCH_DEFAULT_BASE_URLS[String(existingProvider)],
      )) ||
      incomingProvider !== existingProvider)
  );
}


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

export const SLACK_BOT_SECRET_KEYS = ['botToken', 'signingSecret'] as const;

/**
 * Admin-facing Slack bot responses must not echo the workspace credentials
 * back over HTTP. The bot process reads the real values from the internal
 * route instead.
 */
export function maskSlackBotConfig<T extends Record<string, unknown>>(config: T): T {
  return maskKeys(config, SLACK_BOT_SECRET_KEYS);
}

/** An edit form that never saw the real value re-submits the placeholder. */
export function mergeSlackBotConfigPlaceholders<T extends Record<string, unknown>>(
  incoming: T,
  existing: Record<string, unknown> | null | undefined,
): T {
  if (!existing || typeof existing !== 'object') {
    return incoming;
  }
  const out = { ...incoming } as Record<string, unknown>;
  for (const key of SLACK_BOT_SECRET_KEYS) {
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

/**
 * An edit form that never revealed a secret sends the placeholder back. Swap
 * each placeholder for the stored value; returns the keys that had no stored
 * value to restore, so the caller can refuse to save a literal placeholder.
 */
export function restoreSecretPlaceholders<T extends Record<string, unknown>>(
  incoming: T,
  existing: Record<string, unknown> | null | undefined,
  keys: readonly string[],
): { config: T; unrestored: string[] } {
  const out = { ...incoming } as Record<string, unknown>;
  const unrestored: string[] = [];
  for (const key of keys) {
    if (out[key] !== CONFIG_SECRET_PLACEHOLDER) continue;
    const stored = existing?.[key];
    if (typeof stored === 'string' && stored.length > 0) {
      out[key] = stored;
    } else {
      unrestored.push(key);
    }
  }
  return { config: out as T, unrestored };
}

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

/**
 * What a member who is not an admin sees of a provider: no API key, and no server address either,
 * since a self-hosted server's URL names an internal host. Members only list providers (the agent
 * builder does); every write is admin-only, so a masked address is never sent back to be saved.
 */
export function maskWebSearchProviderForMember<
  T extends Record<string, unknown>,
>(configuration: T): T {
  return maskKeys(configuration, [...WEB_SEARCH_SECRET_KEYS, 'baseUrl']);
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
