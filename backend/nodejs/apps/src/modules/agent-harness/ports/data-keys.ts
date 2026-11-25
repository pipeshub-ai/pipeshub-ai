import { DataKeyRef, WrappedDataKey } from '../domain/data-protection';

/** The binding a wrapped data key is authenticated against. */
export interface DataKeyContext extends DataKeyRef {
  orgId: string;
}

/**
 * The key-encryption key. Self-hosted: derived with HKDF from the secret the
 * platform already holds. Managed: a KMS key that never leaves the KMS.
 */
export interface KeyEncryptionKey {
  /** Stored with each wrapped key, so a KEK change is reported, not guessed. */
  readonly id: string;
  wrap(dataKey: Buffer, context: DataKeyContext): Promise<string>;
  /**
   * Throws SealedContentError('unwrap_failed') when this KEK cannot unwrap it.
   * `kekId` is the id stored with the wrap: a ring opens it with that member.
   */
  unwrap(
    wrapped: string,
    context: DataKeyContext,
    kekId?: string,
  ): Promise<Buffer>;
}

/**
 * The current KEK plus the one being rotated out or in (ADR-KEY-01): `id` and
 * `wrap` are the current key's; `unwrap` opens a wrap made under any member.
 */
export interface KeyEncryptionKeyRing extends KeyEncryptionKey {
  /** Every member's id, the current one first. */
  readonly ids: readonly string[];
}

/** Wrapped per-org data keys, every version, until they are shredded. */
export interface DataKeyStore {
  list(orgId: string): Promise<WrappedDataKey[]>;
  /** false when the org already has that version (a concurrent creator won). */
  insert(key: WrappedDataKey): Promise<boolean>;
  /**
   * Atomically adds `count` to the seals counted against a key; the count
   * after the increment, or null when the key no longer exists.
   */
  reserveSeals(
    orgId: string,
    kid: string,
    count: number,
  ): Promise<number | null>;
  /** Crypto-shredding: deletes every version; returns how many. */
  deleteAll(orgId: string): Promise<number>;
  /** Every org that holds a data key (the key rotation's walk, read across orgs). */
  orgIds(): Promise<string[]>;
  /**
   * Replaces one version's wrap only while it is still under `expectedKekId`;
   * false when it is not (another rotation got there first) or is gone.
   */
  replaceWrap(
    orgId: string,
    kid: string,
    expectedKekId: string,
    next: { kekId: string; wrapped: string },
  ): Promise<boolean>;
}
