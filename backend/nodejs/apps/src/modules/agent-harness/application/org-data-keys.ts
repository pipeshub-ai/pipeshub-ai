import { randomBytes, randomUUID } from 'node:crypto';
import {
  DATA_KEY_BYTES,
  DATA_KEY_CACHE_MAX_ORGS,
  DATA_KEY_CACHE_TTL_MS,
  DATA_KEY_ROTATE_AFTER_MS,
  DATA_KEY_ROTATE_AFTER_SEALS,
  DATA_KEY_SEAL_RESERVATION,
  DataKey,
  DataKeyRef,
  WrappedDataKey,
} from '../domain/data-protection';
import { SealedContentError } from '../domain/errors';
import { DataKeyStore, KeyEncryptionKey } from '../ports/data-keys';
import {
  harnessLogMeta,
  HarnessLogger,
  silentHarnessLogger,
} from './harness-log';

export interface OrgDataKeyOptions {
  cacheTtlMs: number;
  maxCachedOrgs: number;
  rotateAfterMs: number;
  rotateAfterSeals: number;
  sealReservation: number;
}

const DEFAULT_OPTIONS: OrgDataKeyOptions = {
  cacheTtlMs: DATA_KEY_CACHE_TTL_MS,
  maxCachedOrgs: DATA_KEY_CACHE_MAX_ORGS,
  rotateAfterMs: DATA_KEY_ROTATE_AFTER_MS,
  rotateAfterSeals: DATA_KEY_ROTATE_AFTER_SEALS,
  sealReservation: DATA_KEY_SEAL_RESERVATION,
};

interface CachedOrg {
  loadedAt: number;
  wrapped: WrappedDataKey[];
  unwrapped: Map<string, DataKey>;
  /** Seals this replica reserved and has not used yet, per key id. */
  reserved: Map<string, number>;
}

const MAX_COUNT_ROTATIONS_PER_SEAL = 8;

type CreateReason = 'first_write' | 'rotation' | 'age' | 'seal_count';

export interface OrgDataKeysOptions {
  store: DataKeyStore;
  kek: KeyEncryptionKey;
  logger?: HarnessLogger;
  now?: () => number;
  limits?: Partial<OrgDataKeyOptions>;
  newKid?: () => string;
  newKey?: () => Buffer;
}

/**
 * Per-org data keys (REPORT 6.16): the newest version seals new writes, every
 * stored version opens what it sealed. Unwrapped keys are cached per replica
 * for at most `cacheTtlMs`, which bounds how long a shred takes to reach
 * every replica.
 */
export class OrgDataKeys {
  private readonly cache = new Map<string, CachedOrg>();
  private readonly loading = new Map<string, Promise<CachedOrg>>();
  private readonly options: OrgDataKeyOptions;

  private readonly store: DataKeyStore;
  private readonly kek: KeyEncryptionKey;
  private readonly logger: HarnessLogger;
  private readonly now: () => number;
  private readonly newKid: () => string;
  private readonly newKey: () => Buffer;

  constructor(options: OrgDataKeysOptions) {
    this.store = options.store;
    this.kek = options.kek;
    this.logger = options.logger ?? silentHarnessLogger;
    this.now = options.now ?? Date.now;
    this.newKid = options.newKid ?? randomUUID;
    this.newKey = options.newKey ?? (() => randomBytes(DATA_KEY_BYTES));
    this.options = { ...DEFAULT_OPTIONS, ...options.limits };
  }

  /**
   * The key for one new seal of `orgId`, counted against that key; created
   * on the org's first write, replaced when old or when its seal count
   * reaches the limit.
   */
  async active(orgId: string): Promise<DataKey> {
    let org = await this.org(orgId);
    let newest = org.wrapped.at(-1);
    if (newest === undefined) {
      org = await this.create(orgId, 1, 'first_write');
    } else if (this.now() - newest.createdAt >= this.options.rotateAfterMs) {
      org = await this.create(orgId, newest.version + 1, 'age');
    }
    newest = this.newest(org);
    // Other replicas may exhaust the version this one just moved to.
    for (let rotations = 0; !(await this.reserveSeal(org, newest));) {
      if (++rotations > MAX_COUNT_ROTATIONS_PER_SEAL) {
        throw new Error('Data keys are exhausted faster than they rotate');
      }
      org = await this.create(orgId, newest.version + 1, 'seal_count');
      newest = this.newest(org);
    }
    return this.unwrap(org, newest);
  }

  /** The key a ciphertext names; `shredded` when no such key exists any more. */
  async byRef(orgId: string, ref: DataKeyRef): Promise<DataKey> {
    const find = (org: CachedOrg): WrappedDataKey | undefined =>
      org.wrapped.find(
        (key) => key.kid === ref.kid && key.version === ref.version,
      );
    let org = await this.org(orgId);
    let wrapped = find(org);
    if (wrapped === undefined) {
      // A version another replica created after this one cached the org.
      org = await this.reload(orgId);
      wrapped = find(org);
    }
    if (wrapped === undefined) {
      throw new SealedContentError(
        'shredded',
        'The data key of this content no longer exists',
      );
    }
    return this.unwrap(org, wrapped);
  }

  /** A new version for new writes; older versions stay readable. */
  async rotate(orgId: string): Promise<DataKeyRef> {
    const org = await this.reload(orgId);
    const next = (org.wrapped.at(-1)?.version ?? 0) + 1;
    const rotated = (await this.create(orgId, next, 'rotation')).wrapped.at(-1);
    if (rotated === undefined) throw new Error('Rotation left no data key');
    return { kid: rotated.kid, version: rotated.version };
  }

  /** Crypto-shredding: every version of the org's key is destroyed. */
  async shred(orgId: string): Promise<number> {
    const deleted = await this.store.deleteAll(orgId);
    this.cache.delete(orgId);
    this.logger.info(
      'data_key.shredded',
      harnessLogMeta('data_key.shredded', {
        orgId,
        versions: deleted,
        replicaCacheTtlMs: this.options.cacheTtlMs,
      }),
    );
    return deleted;
  }

  private newest(org: CachedOrg): WrappedDataKey {
    const newest = org.wrapped.at(-1);
    if (newest === undefined) {
      throw new Error('A data key was created but is not listed');
    }
    return newest;
  }

  /**
   * Takes one seal from this replica's reservation, reserving a new block
   * from the key document when it is used up. false when the block would
   * pass the limit: the key must not seal again.
   */
  private async reserveSeal(
    org: CachedOrg,
    key: WrappedDataKey,
  ): Promise<boolean> {
    const left = org.reserved.get(key.kid) ?? 0;
    if (left > 0) {
      org.reserved.set(key.kid, left - 1);
      return true;
    }
    const block = this.options.sealReservation;
    const total = await this.store.reserveSeals(key.orgId, key.kid, block);
    if (total === null) {
      throw new SealedContentError(
        'shredded',
        'The data key was shredded while sealing',
      );
    }
    if (total > this.options.rotateAfterSeals) return false;
    org.reserved.set(key.kid, (org.reserved.get(key.kid) ?? 0) + block - 1);
    return true;
  }

  private async create(
    orgId: string,
    version: number,
    reason: CreateReason,
  ): Promise<CachedOrg> {
    const kid = this.newKid();
    const key = this.newKey();
    const createdAt = this.now();
    const wrapped: WrappedDataKey = {
      orgId,
      kid,
      version,
      kekId: this.kek.id,
      wrapped: await this.kek.wrap(key, { orgId, kid, version }),
      createdAt,
    };
    const inserted = await this.store.insert(wrapped);
    // A concurrent creator of the same version won; everyone uses its key.
    const org = await this.reload(orgId);
    if (inserted) {
      org.unwrapped.set(kid, { orgId, kid, version, key, createdAt });
      this.logger.info(
        'data_key.created',
        harnessLogMeta('data_key.created', {
          orgId,
          kid,
          version,
          kekId: wrapped.kekId,
          reason,
        }),
      );
    }
    return org;
  }

  private async unwrap(
    org: CachedOrg,
    wrapped: WrappedDataKey,
  ): Promise<DataKey> {
    const cached = org.unwrapped.get(wrapped.kid);
    if (cached !== undefined) return cached;
    let key: Buffer;
    try {
      key = await this.kek.unwrap(
        wrapped.wrapped,
        { orgId: wrapped.orgId, kid: wrapped.kid, version: wrapped.version },
        wrapped.kekId,
      );
    } catch (error) {
      this.logger.error(
        'data_key.unwrap_failed',
        harnessLogMeta('data_key.unwrap_failed', {
          orgId: wrapped.orgId,
          kid: wrapped.kid,
          version: wrapped.version,
          kekId: wrapped.kekId,
          currentKekId: this.kek.id,
          errorType: error instanceof Error ? error.name : typeof error,
        }),
      );
      throw error instanceof SealedContentError
        ? error
        : new SealedContentError(
            'unwrap_failed',
            'The data key did not unwrap',
          );
    }
    const dataKey: DataKey = {
      orgId: wrapped.orgId,
      kid: wrapped.kid,
      version: wrapped.version,
      key,
      createdAt: wrapped.createdAt,
    };
    org.unwrapped.set(wrapped.kid, dataKey);
    return dataKey;
  }

  private org(orgId: string): Promise<CachedOrg> {
    const cached = this.cache.get(orgId);
    if (
      cached !== undefined &&
      this.now() - cached.loadedAt < this.options.cacheTtlMs
    ) {
      return Promise.resolve(cached);
    }
    return this.reload(orgId);
  }

  /** One store read per org at a time; concurrent callers share it. */
  private reload(orgId: string): Promise<CachedOrg> {
    let pending = this.loading.get(orgId);
    if (pending === undefined) {
      pending = this.load(orgId).finally(() => {
        this.loading.delete(orgId);
      });
      this.loading.set(orgId, pending);
    }
    return pending;
  }

  private async load(orgId: string): Promise<CachedOrg> {
    const wrapped = (await this.store.list(orgId)).sort(
      (left, right) => left.version - right.version,
    );
    const previous = this.cache.get(orgId);
    const unwrapped = new Map<string, DataKey>();
    const reserved = new Map<string, number>();
    // Keep what is still stored; a shredded version must not survive a reload.
    for (const key of wrapped) {
      const known = previous?.unwrapped.get(key.kid);
      if (known !== undefined) unwrapped.set(key.kid, known);
      const left = previous?.reserved.get(key.kid);
      if (left !== undefined) reserved.set(key.kid, left);
    }
    const org: CachedOrg = {
      loadedAt: this.now(),
      wrapped,
      unwrapped,
      reserved,
    };
    this.cache.delete(orgId);
    this.cache.set(orgId, org);
    while (this.cache.size > this.options.maxCachedOrgs) {
      const oldest = this.cache.keys().next().value;
      if (oldest === undefined) break;
      this.cache.delete(oldest);
    }
    return org;
  }
}
