import * as crypto from 'crypto';
import * as fs from 'fs';
import * as path from 'path';

const CREDENTIALS_VERSION = 1;
const CREDENTIALS_FILE = 'desktop-credentials.json';
/** Re-mint this far before the access token's own `exp`, to cover clock skew. */
const TOKEN_REFRESH_SKEW_MS = 60_000;
/** Used when the minted token carries no readable `exp`. */
const TOKEN_FALLBACK_TTL_MS = 10 * 60_000;
const MINT_TIMEOUT_MS = 20_000;
const REFRESH_TOKEN_ROUTE = '/api/v1/userAccount/refresh/token';

/** The slice of Electron's `safeStorage` this store needs, injected for testability. */
export interface SafeStorageLike {
  isEncryptionAvailable(): boolean;
  encryptString(plainText: string): Buffer;
  decryptString(encrypted: Buffer): string;
}

export interface DesktopCredentialsInput {
  refreshToken: string;
  apiBaseUrl: string;
}

export interface SetCredentialsResult {
  /** False when the refresh token is held in memory only for this session. */
  persisted: boolean;
  deviceId: string;
  reason?: string;
}

interface StoredCredentials {
  version: number;
  deviceId: string;
  apiBaseUrl: string | null;
  /** base64 of safeStorage.encryptString output; absent when encryption is unavailable. */
  refreshTokenEnc?: string;
  updatedAt: number;
}

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

/** Read `exp` out of a JWT without verifying it — only used to schedule re-minting. */
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
 * The desktop's own credential, independent of any renderer window: the
 * refresh token, the server it belongs to, and a stable per-install device id.
 *
 * `apiBaseUrl` lives here rather than only in per-connector journal meta
 * because a machine that has signed in but not yet configured a connector
 * still needs somewhere to send its socket handshake.
 */
export class DesktopCredentialsStore {
  private readonly filePath: string;
  private readonly safeStorage: SafeStorageLike | null;
  private stored: StoredCredentials;
  /** Set when the token could not be encrypted at rest — session-scoped only. */
  private volatileRefreshToken: string | null = null;
  private accessToken: string | null = null;
  private accessTokenExpiresAt = 0;
  private mintInFlight: Promise<string | null> | null = null;

  constructor(baseDir: string, safeStorage?: SafeStorageLike | null) {
    fs.mkdirSync(baseDir, { recursive: true });
    this.filePath = path.join(baseDir, CREDENTIALS_FILE);
    this.safeStorage = safeStorage ?? null;
    this.stored = this.load();
  }

  private load(): StoredCredentials {
    const raw = readJsonFile<StoredCredentials>(this.filePath);
    if (raw && raw.version === CREDENTIALS_VERSION && typeof raw.deviceId === 'string' && raw.deviceId) {
      return raw;
    }
    const fresh: StoredCredentials = {
      version: CREDENTIALS_VERSION,
      deviceId: crypto.randomUUID(),
      apiBaseUrl: null,
      updatedAt: Date.now(),
    };
    this.persist(fresh);
    return fresh;
  }

  private persist(next: StoredCredentials): void {
    this.stored = next;
    try {
      writeFileAtomic(this.filePath, JSON.stringify(next, null, 2));
    } catch (error) {
      console.warn('[desktop-credentials] could not write credential file:', error);
    }
  }

  get deviceId(): string {
    return this.stored.deviceId;
  }

  get apiBaseUrl(): string | null {
    return this.stored.apiBaseUrl;
  }

  getRefreshToken(): string | null {
    if (this.volatileRefreshToken) return this.volatileRefreshToken;
    const enc = this.stored.refreshTokenEnc;
    if (!enc || !this.safeStorage) return null;
    try {
      return this.safeStorage.decryptString(Buffer.from(enc, 'base64')) || null;
    } catch {
      return null;
    }
  }

  hasCredential(): boolean {
    return Boolean(this.getRefreshToken() && this.stored.apiBaseUrl);
  }

  /**
   * Accept a refresh token from the renderer at login.
   *
   * `safeStorage.isEncryptionAvailable()` is false on some Linux desktops
   * (no keyring), where encryptString silently degrades to obfuscation. Writing
   * a bare refresh token to disk there is worse than losing sync when the app
   * closes, so keep it in memory for this session instead.
   */
  setCredentials({ refreshToken, apiBaseUrl }: DesktopCredentialsInput): SetCredentialsResult {
    const token = String(refreshToken || '').trim();
    const baseUrl = String(apiBaseUrl || '').replace(/\/$/, '');
    if (!token || !baseUrl) {
      throw new Error('refreshToken and apiBaseUrl are both required');
    }
    // A different server means the stored token is for a different account.
    if (this.stored.apiBaseUrl && this.stored.apiBaseUrl !== baseUrl) {
      this.invalidateAccessToken();
    }

    const canEncrypt = Boolean(this.safeStorage?.isEncryptionAvailable());
    if (!canEncrypt) {
      this.volatileRefreshToken = token;
      this.persist({
        ...this.stored,
        apiBaseUrl: baseUrl,
        refreshTokenEnc: undefined,
        updatedAt: Date.now(),
      });
      this.invalidateAccessToken();
      return {
        persisted: false,
        deviceId: this.deviceId,
        reason: 'OS credential encryption is unavailable; sync stops when the app closes',
      };
    }

    this.volatileRefreshToken = null;
    this.persist({
      ...this.stored,
      apiBaseUrl: baseUrl,
      refreshTokenEnc: this.safeStorage!.encryptString(token).toString('base64'),
      updatedAt: Date.now(),
    });
    this.invalidateAccessToken();
    return { persisted: true, deviceId: this.deviceId };
  }

  clear(): void {
    this.volatileRefreshToken = null;
    this.invalidateAccessToken();
    this.persist({
      version: CREDENTIALS_VERSION,
      deviceId: this.stored.deviceId,
      apiBaseUrl: this.stored.apiBaseUrl,
      updatedAt: Date.now(),
    });
  }

  invalidateAccessToken(): void {
    this.accessToken = null;
    this.accessTokenExpiresAt = 0;
  }

  /**
   * Mint (or reuse) an access token from the stored refresh token. Concurrent
   * callers share one in-flight request — a reconnect storm must not fan out
   * into one refresh call per attempt.
   */
  async getAccessToken(force = false): Promise<string | null> {
    if (!force && this.accessToken && Date.now() < this.accessTokenExpiresAt) {
      return this.accessToken;
    }
    if (this.mintInFlight) return this.mintInFlight;
    const promise = this.mintAccessToken().finally(() => {
      if (this.mintInFlight === promise) this.mintInFlight = null;
    });
    this.mintInFlight = promise;
    return promise;
  }

  private async mintAccessToken(): Promise<string | null> {
    const refreshToken = this.getRefreshToken();
    const baseUrl = this.stored.apiBaseUrl;
    if (!refreshToken || !baseUrl) return null;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), MINT_TIMEOUT_MS);
    let response: Response;
    try {
      response = await fetch(`${baseUrl}${REFRESH_TOKEN_ROUTE}`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${refreshToken}`,
          'Content-Type': 'application/json',
        },
        signal: controller.signal,
      });
    } catch (error) {
      console.warn('[desktop-credentials] token mint failed:', error);
      return null;
    } finally {
      clearTimeout(timer);
    }

    if (!response.ok) {
      // A rejected refresh token never recovers on retry; drop it so the
      // socket stops reconnecting against a 401 until the user signs in again.
      if (response.status === 401 || response.status === 403) {
        console.warn('[desktop-credentials] refresh token rejected; clearing');
        this.clear();
      }
      return null;
    }

    let accessToken: string | null = null;
    try {
      const body = (await response.json()) as { accessToken?: string };
      accessToken = typeof body?.accessToken === 'string' ? body.accessToken : null;
    } catch {
      accessToken = null;
    }
    if (!accessToken) return null;

    const expiresAt = readJwtExpiryMs(accessToken);
    this.accessToken = accessToken;
    this.accessTokenExpiresAt = expiresAt
      ? expiresAt - TOKEN_REFRESH_SKEW_MS
      : Date.now() + TOKEN_FALLBACK_TTL_MS;
    return accessToken;
  }
}
