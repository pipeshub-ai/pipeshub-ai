import * as fs from 'fs';
import * as path from 'path';

/** Electron's `safeStorage`: the OS keychain (Keychain, DPAPI, libsecret) seals the bytes. */
export interface SealingKeychain {
  isEncryptionAvailable(): boolean;
  encryptString(plainText: string): Buffer;
  decryptString(encrypted: Buffer): string;
}

/**
 * A key names one of several sessions the app holds at once (an edition with a session per organisation); without
 * one, the app's one session. Anything else names no session: it reads nothing and writes nothing.
 */
const SESSION_KEY = /^[A-Za-z0-9_-]{1,64}$/;
const ONE_SESSION = '';

/**
 * The desktop app's refresh tokens (ADR-WEB-02 D5). The app's pages are
 * `app://`, a different site from the server, so they cannot hold the web
 * app's SameSite cookie; each token is sealed by the OS keychain in a file only
 * the main process reads, instead of sitting in the renderer's localStorage.
 * The one session is `file`; a keyed session is `file.<key>` beside it.
 *
 * Without a keychain the tokens are kept in memory: the person stays signed in
 * while the app runs and signs in again after a restart, rather than a token
 * being written to disk unsealed.
 */
export class SessionVault {
  private readonly memory = new Map<string, string>();

  constructor(
    private readonly file: string,
    private readonly keychain: SealingKeychain,
  ) {}

  private sealed(): boolean {
    try {
      return this.keychain.isEncryptionAvailable();
    } catch {
      return false;
    }
  }

  /** The slot a key names, or null when it names none. */
  private slot(key: unknown): string | null {
    if (key === undefined || key === null) return ONE_SESSION;
    return typeof key === 'string' && SESSION_KEY.test(key) ? key : null;
  }

  private fileOf(slot: string): string {
    return slot === ONE_SESSION ? this.file : `${this.file}.${slot}`;
  }

  read(key?: unknown): string | null {
    const slot = this.slot(key);
    if (slot === null) return null;
    if (!this.sealed()) return this.memory.get(slot) ?? null;
    try {
      const token = this.keychain.decryptString(fs.readFileSync(this.fileOf(slot))).trim();
      return token || null;
    } catch {
      return null;
    }
  }

  write(refreshToken: unknown, key?: unknown): boolean {
    const token = typeof refreshToken === 'string' ? refreshToken.trim() : '';
    const slot = this.slot(key);
    if (!token || slot === null) return false;
    if (!this.sealed()) {
      this.memory.set(slot, token);
      return true;
    }
    const file = this.fileOf(slot);
    fs.mkdirSync(path.dirname(file), { recursive: true });
    const temporary = `${file}.${process.pid}.tmp`;
    fs.writeFileSync(temporary, this.keychain.encryptString(token), { mode: 0o600 });
    fs.renameSync(temporary, file);
    return true;
  }

  clear(key?: unknown): void {
    const slot = this.slot(key);
    if (slot === null) return;
    this.memory.delete(slot);
    fs.rmSync(this.fileOf(slot), { force: true });
  }

  /** The keys that hold a session, besides the one session. */
  keys(): string[] {
    const keys = new Set([...this.memory.keys()].filter((k) => k !== ONE_SESSION));
    const prefix = `${path.basename(this.file)}.`;
    try {
      for (const name of fs.readdirSync(path.dirname(this.file))) {
        const key = name.startsWith(prefix) ? name.slice(prefix.length) : null;
        if (key && SESSION_KEY.test(key)) keys.add(key);
      }
    } catch {
      // No directory yet: no keyed session either.
    }
    return [...keys].sort();
  }

  /** Every session: the one session and each keyed one (a sign-out that ends them all). */
  clearAll(): void {
    for (const key of this.keys()) this.clear(key);
    this.clear();
  }
}
