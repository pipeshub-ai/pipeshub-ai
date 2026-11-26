import { randomBytes } from 'crypto';
import { z } from 'zod/v3';
import { EncryptionService } from '../encryptor/encryptor';
import { KeyValueStoreService } from '../services/keyValueStore.service';

/**
 * The credential vault's versioned envelope (P2.4a/P2.4b), in the format the
 * Python vault reads (`app/harness/credentials/vault.py`): each version
 * encrypted with a data key of the vault keyring, the document and the
 * keyring encrypted again with the platform cipher. Writes are
 * compare-and-set on the stored ciphertext, so concurrent writers never lose
 * a version, and the keyring is provisioned the same way, so replicas
 * converge on one. Model accounts and control-plane bindings both store
 * their secrets in it.
 */

export const VAULT_KEYRING_PATH = '/services/agentHarness/credentialKeys';
/** How long a replaced version stays readable for handles issued before it (the handle TTL). */
export const VAULT_RETENTION_MS = 600_000;

export type VaultKeyValueStore = Pick<
  KeyValueStoreService,
  'get' | 'compareAndSet' | 'delete' | 'listKeysInDirectory'
>;

export type SecretCipher = Pick<EncryptionService, 'encrypt' | 'decrypt'>;

const VAULT_ALGORITHM = 'aes-256-gcm';
const DATA_KEY_BYTES = 32;
const CAS_ATTEMPTS = 5;

const keyringSchema = z
  .object({ active: z.string().min(1), keys: z.record(z.string().min(1)) })
  .passthrough();
type Keyring = z.infer<typeof keyringSchema>;

const versionSchema = z
  .object({
    version: z.string(),
    keyVersion: z.string(),
    ciphertext: z.string(),
    supersededAt: z.string().nullable(),
  })
  .passthrough();
export type VaultVersion = z.infer<typeof versionSchema>;

// passthrough: metadata of the document's owner (`account`, `binding`) and
// fields another writer (the Python vault) adds survive a rewrite here.
export const vaultDocumentSchema = z
  .object({
    versions: z.array(versionSchema),
    lastVersion: z.number().int().nonnegative().optional(),
    revoked: z.object({ at: z.string(), by: z.string() }).optional(),
  })
  .passthrough();
export type VaultDocument = z.infer<typeof vaultDocumentSchema>;

export interface Stored<T> {
  value: T | null;
  revision: string | null;
}

/**
 * The ciphertext of a stored configuration value. The Python store writes
 * `json.dumps(ciphertext)` (a quoted JSON string) where Node writes the
 * ciphertext as is; both must read here.
 */
export function storedCiphertext(stored: string): string {
  if (stored.startsWith('"')) {
    const parsed: unknown = JSON.parse(stored);
    if (typeof parsed === 'string') return parsed;
  }
  return stored;
}

export const lastVersionOf = (document: VaultDocument): number =>
  Math.max(
    document.lastVersion ?? 0,
    ...document.versions.map((entry) => Number(entry.version)),
  );

export interface VersionedSecretDocumentsOptions {
  store: VaultKeyValueStore;
  cipher: SecretCipher;
  /** The error a store failure or exhausted compare-and-set becomes (the caller's 503). */
  unavailable: (message: string) => Error;
  now: () => number;
  dataCipher?: (keyHex: string) => SecretCipher;
  newKey?: () => string;
}

export class VersionedSecretDocuments {
  private readonly store: VaultKeyValueStore;
  private readonly cipher: SecretCipher;
  private readonly unavailable: (message: string) => Error;
  readonly now: () => number;
  private readonly dataCipher: (keyHex: string) => SecretCipher;
  private readonly newKey: () => string;

  constructor(options: VersionedSecretDocumentsOptions) {
    this.store = options.store;
    this.cipher = options.cipher;
    this.unavailable = options.unavailable;
    this.now = options.now;
    this.dataCipher =
      options.dataCipher ??
      ((key) => EncryptionService.withKey(VAULT_ALGORITHM, key));
    this.newKey =
      options.newKey ?? (() => randomBytes(DATA_KEY_BYTES).toString('hex'));
  }

  /** A KV failure is unavailable, never "absent": callers act on absence. */
  async kv<T>(operation: () => Promise<T>): Promise<T> {
    try {
      return await operation();
    } catch {
      throw this.unavailable('The credential vault is unavailable');
    }
  }

  async read<T>(
    path: string,
    schema: z.ZodType<T, z.ZodTypeDef, unknown>,
  ): Promise<Stored<T>> {
    const stored = await this.kv(() => this.store.get<string>(path));
    if (typeof stored !== 'string' || stored.length === 0) {
      return { value: null, revision: null };
    }
    const plaintext = this.cipher.decrypt(storedCiphertext(stored));
    return { value: schema.parse(JSON.parse(plaintext)), revision: stored };
  }

  /** Stores `value` at `path` if nothing is there yet; false when something is. */
  async create(path: string, value: unknown): Promise<boolean> {
    return this.kv(() =>
      this.store.compareAndSet<string>(
        path,
        null,
        this.cipher.encrypt(JSON.stringify(value)),
      ),
    );
  }

  async delete(path: string): Promise<void> {
    await this.kv(() => this.store.delete(path));
  }

  async list(directory: string): Promise<string[]> {
    return this.kv(() => this.store.listKeysInDirectory(directory));
  }

  private async keyring(): Promise<Keyring> {
    for (let attempt = 0; attempt < CAS_ATTEMPTS; attempt += 1) {
      const { value } = await this.read(VAULT_KEYRING_PATH, keyringSchema);
      if (value !== null) return value;
      const fresh: Keyring = { active: '1', keys: { '1': this.newKey() } };
      await this.create(VAULT_KEYRING_PATH, fresh);
    }
    throw this.unavailable(
      'The credential vault keyring could not be provisioned',
    );
  }

  /** `material` encrypted under the keyring's active key, ready to append as a version. */
  async seal(
    material: unknown,
  ): Promise<{ keyVersion: string; ciphertext: string }> {
    const keyring = await this.keyring();
    const dataKey = keyring.keys[keyring.active];
    if (dataKey === undefined) {
      throw this.unavailable('The credential vault keyring has no active key');
    }
    return {
      keyVersion: keyring.active,
      ciphertext: this.dataCipher(dataKey).encrypt(JSON.stringify(material)),
    };
  }

  /**
   * Compare-and-set loop: `change` sees the current document (or null) and
   * answers the next one, or null to write nothing.
   */
  async update<D extends VaultDocument>(
    path: string,
    schema: z.ZodType<D, z.ZodTypeDef, unknown>,
    change: (current: D | null) => D | null,
    conflict = 'A vault document changed concurrently',
  ): Promise<D | null> {
    for (let attempt = 0; attempt < CAS_ATTEMPTS; attempt += 1) {
      const { value, revision } = await this.read(path, schema);
      const next = change(value);
      if (next === null) return null;
      if (
        await this.kv(() =>
          this.store.compareAndSet<string>(
            path,
            revision,
            this.cipher.encrypt(JSON.stringify(next)),
          ),
        )
      ) {
        return next;
      }
    }
    throw this.unavailable(conflict);
  }

  /** The versions still readable at `now`: the current one and those superseded within the retention. */
  live(versions: VaultVersion[], now: number): VaultVersion[] {
    return versions.filter(
      (entry) =>
        entry.supersededAt === null ||
        Date.parse(entry.supersededAt) > now - VAULT_RETENTION_MS,
    );
  }

  /**
   * The document with `sealed` appended as the next version: the previous one
   * superseded now, versions past the retention pruned, a tombstone lifted
   * (numbering continues after it).
   */
  appended<D extends VaultDocument>(
    current: D | null,
    sealed: { keyVersion: string; ciphertext: string },
    now: number,
  ): { document: D; version: number } {
    const at = new Date(now).toISOString();
    const document = (current ?? { versions: [] }) as D;
    const version = lastVersionOf(document) + 1;
    const versions = document.versions.map((entry, index, all) =>
      index === all.length - 1 && entry.supersededAt === null
        ? { ...entry, supersededAt: at }
        : entry,
    );
    const { revoked: _revoked, ...rest } = document;
    return {
      document: {
        ...rest,
        versions: [
          ...this.live(versions, now),
          { version: String(version), ...sealed, supersededAt: null },
        ],
        lastVersion: version,
      } as unknown as D,
      version,
    };
  }

  /** A tombstone: every version unreadable, `lastVersion` kept so numbering continues. */
  tombstoned(document: VaultDocument, by: string, now: number): VaultDocument {
    return {
      versions: [],
      lastVersion: lastVersionOf(document),
      revoked: { at: new Date(now).toISOString(), by },
    };
  }
}
