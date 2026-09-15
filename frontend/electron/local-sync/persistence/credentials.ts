import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';

const CREDENTIALS_VERSION = 1;
const CREDENTIALS_FILE = 'desktop-credentials.json';
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

interface StoredDeviceIdentity {
  version: number;
  deviceId: string;
  updatedAt: number;
}

const IDENTITY_FIELDS = ['version', 'deviceId', 'updatedAt'];

function readJsonFile<T>(filePath: string): T | null {
  if (!fs.existsSync(filePath)) return null;
  try {
    return JSON.parse(fs.readFileSync(filePath, 'utf8')) as T;
  } catch {
    return null;
  }
}

function writeFileAtomic(filePath: string, content: string): void {
  const tmp = `${filePath}.tmp`;
  fs.writeFileSync(tmp, content, 'utf8');
  fs.renameSync(tmp, filePath);
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
 * Nothing secret is written to disk: the file holds only a stable per-install
 * `deviceId`, which the Local FS sync point is pinned to. The access token and
 * the server it belongs to are pushed in by the renderer on every token change
 * and held in memory for this process only, so Local FS syncs while the app
 * runs rather than as a background daemon.
 */
export class DesktopCredentialsStore {
  private readonly filePath: string;
  private stored: StoredDeviceIdentity;
  private accessToken: string | null = null;
  private accessTokenExpiresAt = 0;
  private baseUrl: string | null = null;

  constructor(baseDir: string) {
    fs.mkdirSync(baseDir, { recursive: true });
    this.filePath = path.join(baseDir, CREDENTIALS_FILE);
    this.stored = this.load();
  }

  private load(): StoredDeviceIdentity {
    const raw = readJsonFile<Record<string, unknown>>(this.filePath);
    // Any readable deviceId is kept, whatever wrote it: minting a fresh one
    // orphans the device recorded on the server's sync point, which costs a
    // full re-walk and a manual sync-point reset to recover from.
    const deviceId = typeof raw?.deviceId === 'string' ? raw.deviceId : '';
    if (raw && deviceId) {
      // Pre-release builds of this branch also wrote an encrypted refresh
      // token here; rewrite so no token material is left behind on a machine
      // that ran one.
      const isIdentityOnly =
        raw.version === CREDENTIALS_VERSION &&
        typeof raw.updatedAt === 'number' &&
        Object.keys(raw).every((key) => IDENTITY_FIELDS.includes(key));
      if (isIdentityOnly) {
        return { version: CREDENTIALS_VERSION, deviceId, updatedAt: raw.updatedAt as number };
      }
      const identity: StoredDeviceIdentity = {
        version: CREDENTIALS_VERSION,
        deviceId,
        updatedAt: Date.now(),
      };
      this.persist(identity);
      return identity;
    }
    const fresh: StoredDeviceIdentity = {
      version: CREDENTIALS_VERSION,
      deviceId: crypto.randomUUID(),
      updatedAt: Date.now(),
    };
    this.persist(fresh);
    return fresh;
  }

  private persist(next: StoredDeviceIdentity): void {
    this.stored = next;
    try {
      writeFileAtomic(this.filePath, JSON.stringify(next, null, 2));
    } catch (error) {
      console.warn('[desktop-credentials] could not write device identity file:', error);
    }
  }

  get deviceId(): string {
    return this.stored.deviceId;
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
