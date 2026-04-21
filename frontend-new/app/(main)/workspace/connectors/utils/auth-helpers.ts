// ========================================
// Auth type helper utilities
// ========================================

function normalizeAuthTypeKey(authType: string): string {
  return (authType || '').toUpperCase();
}

/** Check if auth type requires no authentication (skip auth card) */
export function isNoneAuthType(authType: string): boolean {
  const u = normalizeAuthTypeKey(authType);
  return u === 'NONE' || u === '';
}

/** Check if auth type uses OAuth redirect flow (show authenticate button) */
export function isOAuthType(authType: string): boolean {
  return ['OAUTH', 'OAUTH_ADMIN_CONSENT', 'OAUTH_CERTIFICATE'].includes(normalizeAuthTypeKey(authType));
}

/**
 * Resolves `authType` for instance UI: prefer GET `/config` (authoritative after save) over catalog/list row.
 *
 * Non-string shapes (numbers, null, objects) yield `''`, which makes {@link isOAuthType} false — so OAuth-only
 * UI gates treat the instance as non-OAuth until a string `authType` is present (typically after `/config` loads).
 */
export function resolveConnectorInstanceAuthType(
  connectorConfig: { authType?: string } | undefined,
  instance: { authType?: string } | undefined,
): string {
  const fromConfig =
    typeof connectorConfig?.authType === 'string' ? connectorConfig.authType.trim() : '';
  if (fromConfig) return fromConfig;
  const fromInstance = typeof instance?.authType === 'string' ? instance.authType.trim() : '';
  return fromInstance;
}

/** Check if auth type uses credential fields (show form fields) */
export function isCredentialAuthType(authType: string): boolean {
  const upper = normalizeAuthTypeKey(authType);
  return [
    'API_TOKEN',
    'USERNAME_PASSWORD',
    'BEARER_TOKEN',
    'BASIC_AUTH',
    'ACCESS_KEY',
    'ACCOUNT_KEY',
    'CONNECTION_STRING',
    'CUSTOM',
  ].includes(upper);
}

/**
 * List/config payloads sometimes serialize `isAuthenticated` loosely.
 * Treat only clear truthy values as authenticated.
 */
export function isConnectorAuthenticatedFlag(value: unknown): boolean {
  return value === true || value === 'true' || value === 1 || value === '1';
}

/**
 * True when the instance is fully configured but still needs OAuth consent (`isAuthenticated`).
 * Credential / no-auth connectors never return true here; they are "live" once `isConfigured`.
 */
function oauthAuthIncompleteForSyncUi(
  authType: string,
  isAuthenticated: unknown,
  isConfigured: boolean,
): boolean {
  return (
    isOAuthType(authType) &&
    isConfigured &&
    !isConnectorAuthenticatedFlag(isAuthenticated)
  );
}

/** Uses {@link resolveConnectorInstanceAuthType} so GET `/config` wins over list-row `authType`. */
export function isConnectorInstanceOAuthAuthIncompleteForSyncUi(
  connectorConfig: { authType?: string } | undefined,
  instance: { authType?: string; isAuthenticated: unknown; isConfigured: boolean },
): boolean {
  return oauthAuthIncompleteForSyncUi(
    resolveConnectorInstanceAuthType(connectorConfig, instance),
    instance.isAuthenticated,
    instance.isConfigured,
  );
}

/**
 * True when saved OAuth credentials look present under `config.auth` (some APIs lag top-level flags).
 * Any non-empty string counts; we do not strip serialized placeholder literals (e.g. the string `"null"`).
 */
function oauthAuthCredentialsLookPresent(auth: unknown): boolean {
  if (!auth || typeof auth !== 'object') return false;
  const a = auth as Record<string, unknown>;
  const creds = a.credentials;
  if (creds && typeof creds === 'object') {
    const c = creds as Record<string, unknown>;
    const token =
      c.access_token ?? c.accessToken ?? c.refresh_token ?? c.refreshToken;
    if (typeof token === 'string' && token.trim().length > 0) return true;
  }
  // Some connectors store tokens directly on `auth` without a `credentials` wrapper.
  const direct =
    a.access_token ?? a.accessToken ?? a.refresh_token ?? a.refreshToken;
  if (typeof direct === 'string' && direct.trim().length > 0) return true;
  return false;
}

/**
 * Active-list / `openPanel` connector row: instance already marked authenticated
 * (GET `/config` can lag flags or nest tokens differently — e.g. Gmail personal).
 */
export function isConnectorListRowAuthenticated(connector: unknown): boolean {
  if (!connector || typeof connector !== 'object') return false;
  return isConnectorAuthenticatedFlag(
    (connector as Record<string, unknown>).isAuthenticated
  );
}

/**
 * True when GET `/config` includes a top-level `isAuthenticated` / `is_authenticated` field that
 * is clearly **not** authenticated. In that case the UI must not fall back to a stale catalog
 * row (`panelConnector.isAuthenticated`) — e.g. after "Next" when the server saved credentials
 * but OAuth consent is still pending (`isAuthenticated: false`).
 */
export function connectorConfigExplicitlyDeniesAuthentication(config: unknown): boolean {
  if (!config || typeof config !== 'object') return false;
  const c = config as Record<string, unknown>;
  const hasI = Object.prototype.hasOwnProperty.call(c, 'isAuthenticated');
  const hasI2 = Object.prototype.hasOwnProperty.call(c, 'is_authenticated');
  if (!hasI && !hasI2) return false;
  return (
    !isConnectorAuthenticatedFlag(c.isAuthenticated) &&
    !isConnectorAuthenticatedFlag(c.is_authenticated)
  );
}

/**
 * Single source for OAuth / Configure gates. Order is load-bearing — do not reorder:
 * 1) {@link isConnectorConfigAuthenticated} (flags + nested tokens),
 * 2) {@link connectorConfigExplicitlyDeniesAuthentication} (stale list row must lose to explicit false),
 * 3) list row when config has no explicit outcome.
 */
export function isConnectorInstanceAuthenticatedForUi(
  panelConnectorId: string | null | undefined,
  panelConnector: unknown,
  connectorConfig: unknown
): boolean {
  if (isConnectorConfigAuthenticated(connectorConfig)) return true;
  if (connectorConfigExplicitlyDeniesAuthentication(connectorConfig)) return false;
  return Boolean(panelConnectorId) && isConnectorListRowAuthenticated(panelConnector);
}

/**
 * Connector instance / GET `.../config` payloads may use `isAuthenticated` or `is_authenticated`.
 * Also infers OAuth completion from nested `config.auth.credentials` when flags are missing.
 */
export function isConnectorConfigAuthenticated(config: unknown): boolean {
  if (!config || typeof config !== 'object') return false;
  const c = config as Record<string, unknown>;
  if (
    isConnectorAuthenticatedFlag(c.isAuthenticated) ||
    isConnectorAuthenticatedFlag(c.is_authenticated)
  ) {
    return true;
  }
  const inner = c.config;
  if (inner && typeof inner === 'object') {
    const cfg = inner as Record<string, unknown>;
    if (oauthAuthCredentialsLookPresent(cfg.auth)) return true;
  }
  return false;
}
