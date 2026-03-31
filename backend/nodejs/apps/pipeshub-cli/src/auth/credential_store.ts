import * as fs from "fs";
import { KeychainBackend } from "./keychain_backend";
import { FernetFileTokenStore } from "./token_store";

export const KEYCHAIN_SERVICE = "com.pipeshub.cli";
export const KEYCHAIN_ACCOUNT = "oauth-tokens";

const STORAGE_KEYS = [
  "access_token",
  "refresh_token",
  "client_id",
  "client_secret",
] as const;

/** Normalize stored OAuth payload (tokens + optional client credentials from login). */
export function sanitizeTokensForStorage(
  data: Record<string, unknown>
): Record<string, string> {
  return {
    access_token: String(data.access_token ?? "").trim(),
    refresh_token: String(data.refresh_token ?? "").trim(),
    client_id: String(data.client_id ?? "").trim(),
    client_secret: String(data.client_secret ?? "").trim(),
  };
}

function needsCompaction(
  raw: Record<string, unknown>,
  sanitized: Record<string, string>
): boolean {
  const allowed = new Set<string>(STORAGE_KEYS);
  if (Object.keys(raw).some((k) => !allowed.has(k))) {
    return true;
  }
  for (const k of STORAGE_KEYS) {
    if (String(raw[k] ?? "").trim() !== (sanitized[k] ?? "")) {
      return true;
    }
  }
  return false;
}

/**
 * OAuth storage: OS keychain when available, else Fernet-encrypted `auth.enc`.
 * Migrates legacy `auth.enc` into the keychain on first load when keychain is available.
 * Persists access + refresh tokens and, after `pipeshub login`, client_id + client_secret
 * so refresh/introspect work without .env.
 */
export class CredentialStore {
  private readonly fileStore: FernetFileTokenStore;
  private keychain: KeychainBackend | null = null;
  private keychainUsable: boolean | null = null;

  constructor(authDir?: string) {
    this.fileStore = new FernetFileTokenStore(authDir);
  }

  /** Path to `auth.enc` when using (or as legacy fallback reference). */
  get filePath(): string {
    return this.fileStore.path;
  }

  private async resolveKeychain(): Promise<boolean> {
    if (this.keychainUsable !== null) {
      return this.keychainUsable;
    }
    const kc = new KeychainBackend();
    const ok = await kc.isAvailable();
    this.keychain = ok ? kc : null;
    this.keychainUsable = ok;
    return ok;
  }

  async load(): Promise<Record<string, unknown> | null> {
    const useKc = await this.resolveKeychain();
    if (useKc && this.keychain) {
      const raw = await this.keychain.get(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT);
      if (raw) {
        try {
          const parsed = JSON.parse(raw) as Record<string, unknown>;
          const sanitized = sanitizeTokensForStorage(parsed);
          if (!sanitized.access_token) return null;
          if (needsCompaction(parsed, sanitized)) {
            await this.save(sanitized);
          }
          return sanitized;
        } catch {
          return null;
        }
      }
      const fileData = this.fileStore.load();
      if (fileData) {
        const sanitized = sanitizeTokensForStorage(fileData);
        if (!sanitized.access_token) {
          return null;
        }
        try {
          await this.keychain.set(
            KEYCHAIN_SERVICE,
            KEYCHAIN_ACCOUNT,
            JSON.stringify(sanitized)
          );
          this.fileStore.clear();
          return sanitized;
        } catch {
          return sanitized;
        }
      }
      return null;
    }
    const fileOnly = this.fileStore.load();
    if (!fileOnly) return null;
    const sanitized = sanitizeTokensForStorage(fileOnly);
    if (!sanitized.access_token) return null;
    if (needsCompaction(fileOnly, sanitized)) {
      await this.save(sanitized);
    }
    return sanitized;
  }

  async save(data: Record<string, unknown>): Promise<void> {
    const payload = sanitizeTokensForStorage(data);
    const useKc = await this.resolveKeychain();
    if (useKc && this.keychain) {
      await this.keychain.set(
        KEYCHAIN_SERVICE,
        KEYCHAIN_ACCOUNT,
        JSON.stringify(payload)
      );
      if (fs.existsSync(this.fileStore.path)) {
        this.fileStore.clear();
      }
      return;
    }
    this.fileStore.save(payload);
  }

  async clear(): Promise<boolean> {
    const useKc = await this.resolveKeychain();
    let removed = false;
    if (useKc && this.keychain) {
      try {
        if (await this.keychain.delete(KEYCHAIN_SERVICE, KEYCHAIN_ACCOUNT)) {
          removed = true;
        }
      } catch {
        /* ignore */
      }
    }
    if (this.fileStore.clear()) {
      removed = true;
    }
    return removed;
  }

  async getStorageDescription(): Promise<string> {
    const useKc = await this.resolveKeychain();
    if (useKc) {
      const label =
        process.platform === "darwin"
          ? "macOS Keychain"
          : process.platform === "win32"
            ? "Windows Credential Manager"
            : "OS keychain (libsecret)";
      return `${label} (${KEYCHAIN_SERVICE} / ${KEYCHAIN_ACCOUNT})`;
    }
    return this.fileStore.path;
  }
}
