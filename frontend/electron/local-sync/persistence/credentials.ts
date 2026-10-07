import type { DeviceIdentity } from './device-identity';

/** Stop presenting a token this far before its `exp`, to cover clock skew. */
const TOKEN_EXPIRY_SKEW_MS = 60_000;
/** Used when the pushed token carries no readable `exp`. */
const TOKEN_FALLBACK_TTL_MS = 10 * 60_000;

/**
 * `apiBaseUrl` is concatenated into the socket.io handshake URL in
 * `desktop-socket.ts`, so an unparseable or dangerous scheme
 * (`javascript:`, `data:`, `file:`, ...) must never be used. Self-hosted
 * PipesHub servers are commonly reached over plain `http:` (see
 * `env.template`'s defaults and `server-url-setup.tsx`, which accepts any
 * http(s) origin), so this only restricts the scheme, matching that same
 * policy rather than requiring https or a loopback host. Exported so call
 * sites that read `apiBaseUrl` back out of this store can re-check it with
 * the same rule instead of duplicating it.
 */
export function isValidApiBaseUrl(rawUrl: string): boolean {
  let parsed: URL;
  try {
    parsed = new URL(rawUrl);
  } catch {
    return false;
  }
  return parsed.protocol === 'https:' || parsed.protocol === 'http:';
}

function assertValidApiBaseUrl(rawUrl: string): void {
  if (!isValidApiBaseUrl(rawUrl)) {
    throw new Error('apiBaseUrl must be an http(s) URL');
  }
}

export interface DesktopAccessTokenInput {
  accessToken: string;
  apiBaseUrl: string;
}

export interface SetAccessTokenResult {
  deviceId: string;
  /** False when the renderer re-pushed the token main already held. */
  changed: boolean;
}

/** Read `exp` out of a JWT without verifying it — only used to stop presenting a dead token. */
function readJwtExpiryMs(token: string): number | null {
  const parts = String(token || '').split('.');
  if (parts.length < 2) return null;
  try {
    const payload = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8'));
    const exp = Number(payload?.exp);
    return Number.isFinite(exp) && exp > 0 ? exp * 1000 : null;
  } catch {
    return null;
  }
}

/**
 * The desktop's identity and its current access token.
 *
 * Nothing is written to disk. The identity is derived from the OS machine id
 * at startup; the access token and the server it belongs to are pushed in by
 * the renderer on every token change and held in memory for this process
 * only, so Local FS syncs while the app runs rather than as a background daemon.
 */
export class DesktopCredentialsStore {
  private readonly identity: DeviceIdentity;
  private accessToken: string | null = null;
  private accessTokenExpiresAt = 0;
  private baseUrl: string | null = null;

  constructor(identity: DeviceIdentity) {
    this.identity = { deviceId: identity.deviceId, deviceName: identity.deviceName };
  }

  get deviceId(): string {
    return this.identity.deviceId;
  }

  get deviceName(): string {
    return this.identity.deviceName;
  }

  get apiBaseUrl(): string | null {
    return this.baseUrl;
  }

  getAccessToken(): string | null {
    if (!this.accessToken) return null;
    if (Date.now() >= this.accessTokenExpiresAt) return null;
    return this.accessToken;
  }

  hasCredential(): boolean {
    return Boolean(this.getAccessToken() && this.baseUrl);
  }

  /**
   * Accept the access token the renderer holds. Called on sign-in and again on
   * every refresh, so the socket always reads a token main did not have to mint.
   */
  setAccessToken({ accessToken, apiBaseUrl }: DesktopAccessTokenInput): SetAccessTokenResult {
    const token = String(accessToken || '').trim();
    const baseUrl = String(apiBaseUrl || '').replace(/\/$/, '');
    if (!token || !baseUrl) {
      throw new Error('accessToken and apiBaseUrl are both required');
    }
    assertValidApiBaseUrl(baseUrl);

    const changed = token !== this.accessToken || baseUrl !== this.baseUrl;
    const expiresAt = readJwtExpiryMs(token);
    this.accessToken = token;
    this.accessTokenExpiresAt = expiresAt
      ? expiresAt - TOKEN_EXPIRY_SKEW_MS
      : Date.now() + TOKEN_FALLBACK_TTL_MS;
    this.baseUrl = baseUrl;

    return { deviceId: this.deviceId, changed };
  }

  clear(): void {
    this.accessToken = null;
    this.accessTokenExpiresAt = 0;
    this.baseUrl = null;
  }
}
