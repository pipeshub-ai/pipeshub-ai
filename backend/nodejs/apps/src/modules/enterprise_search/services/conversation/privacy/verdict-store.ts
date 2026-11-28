import { createHash } from 'crypto';
import type { ICacheService } from '../../../../../libs/services/cache/cacheService.interface';

/**
 * Where the reader check keeps what it decided about one row for one reader. A verdict is
 * stored under everything it was decided from: the reader, the row, the version of the row's text, a digest
 * of the permission epochs and the chat's audience read before the check, and `READER_REV`. When any of
 * them changes the key changes, so nothing is ever deleted: an old verdict is simply never asked for again.
 *
 * A verdict that shows anything (`visible`, `consented`) is never served once its `validUntil` has passed. A
 * `hidden` one may be served after it while the check cannot answer, since it can only hide more. An
 * `unchecked` one is kept for a few seconds, so a failing check is not asked again on every read.
 */

/** Bumped whenever the reader rule or one of its leaf checks changes, so no verdict an older rule made is served. */
export const READER_REV = 5;

export type StoredVerdictKind =
  'visible' | 'consented' | 'hidden' | 'unchecked';

export interface StoredVerdict {
  v: StoredVerdictKind;
  reason?: string;
  /** What a shown answer was shown by (`RowVerdict.basis`). */
  basis?: string;
  /** Epoch milliseconds: the soonest any input of the verdict may have changed. */
  validUntil: number;
}

export interface VerdictKeyParts {
  orgId: string;
  viewerId: string;
  rowId: string;
  /** The version of the row's text the verdict is about. */
  labelVer: string;
  /** `epochDigestOf` over the epochs and audience read before the check. */
  epochDigest: string;
}

export const verdictKey = (p: VerdictKeyParts): string =>
  `collab:rv:v1:${p.orgId}:${p.viewerId}:${p.rowId}:${p.labelVer}:${p.epochDigest}:${String(READER_REV)}`;

/** One short digest of everything a verdict was decided under, in the order given. */
export const epochDigestOf = (parts: readonly unknown[]): string =>
  createHash('sha256')
    .update(JSON.stringify(parts), 'utf8')
    .digest('hex')
    .slice(0, 32);

/** How long an `unchecked` verdict is kept. */
export const UNCHECKED_KEEP_MS = 5_000;
/** How long a `hidden` verdict stays readable after its `validUntil`, for reads while the check cannot answer. */
export const HIDDEN_BACKSTOP_MS = 5 * 60_000;
/** Verdicts this process keeps itself, beside the shared cache, at most. */
const LOCAL_MAX = 20_000;

const isKind = (v: unknown): v is StoredVerdictKind =>
  v === 'visible' || v === 'consented' || v === 'hidden' || v === 'unchecked';

const isStoredVerdict = (value: unknown): value is StoredVerdict => {
  if (value === null || typeof value !== 'object') return false;
  const { v, validUntil, reason, basis } = value as Record<string, unknown>;
  return (
    isKind(v) &&
    typeof validUntil === 'number' &&
    Number.isFinite(validUntil) &&
    (reason === undefined || typeof reason === 'string') &&
    (basis === undefined || typeof basis === 'string')
  );
};

/** Until when an entry may be read at all: past this it is gone from both caches. */
const keepUntil = (verdict: StoredVerdict, now: number): number => {
  if (verdict.v === 'unchecked')
    return Math.min(verdict.validUntil, now + UNCHECKED_KEEP_MS);
  if (verdict.v === 'hidden') return verdict.validUntil + HIDDEN_BACKSTOP_MS;
  return verdict.validUntil;
};

export interface VerdictStoreOptions {
  /** The cache every replica shares; read on each call, so one wired after start is still used. */
  shared?: () => ICacheService | undefined;
  now?: () => number;
  localMax?: number;
}

export interface VerdictReadOptions {
  /** The check cannot answer now: a `hidden` verdict past its `validUntil` may still be served. */
  checkDown?: boolean;
}

/**
 * Verdicts by key: this process's own, then the shared cache. A shared-cache error is a miss and a failed
 * write is dropped: the store only ever saves a check, it never decides one.
 */
export class VerdictStore {
  private readonly local = new Map<string, StoredVerdict>();
  private readonly now: () => number;
  private readonly localMax: number;

  constructor(private readonly opts: VerdictStoreOptions = {}) {
    this.now = opts.now ?? Date.now;
    this.localMax = opts.localMax ?? LOCAL_MAX;
  }

  /** The servable verdicts among `keys`; a key with none is left out. */
  async getMany(
    keys: readonly string[],
    read: VerdictReadOptions = {},
  ): Promise<Map<string, StoredVerdict>> {
    const now = this.now();
    const found = new Map<string, StoredVerdict>();
    const missing: string[] = [];
    for (const key of new Set(keys)) {
      const verdict = this.local.get(key);
      if (verdict === undefined) {
        missing.push(key);
        continue;
      }
      if (keepUntil(verdict, now) <= now) {
        this.local.delete(key);
        missing.push(key);
        continue;
      }
      if (servable(verdict, now, read)) found.set(key, verdict);
    }
    const shared = this.shared();
    if (shared && missing.length > 0) {
      const values = await Promise.all(
        missing.map((key) => shared.get<unknown>(key).catch(() => null)),
      );
      missing.forEach((key, i) => {
        const value = values[i];
        if (!isStoredVerdict(value) || keepUntil(value, now) <= now) return;
        this.remember(key, value);
        if (servable(value, now, read)) found.set(key, value);
      });
    }
    return found;
  }

  async putMany(
    entries: ReadonlyArray<readonly [string, StoredVerdict]>,
  ): Promise<void> {
    const now = this.now();
    const shared = this.shared();
    const writes: Array<Promise<void>> = [];
    for (const [key, verdict] of entries) {
      const until = keepUntil(verdict, now);
      if (until <= now) continue;
      this.remember(key, verdict);
      if (shared) {
        const ttl = Math.max(1, Math.ceil((until - now) / 1000));
        writes.push(shared.set(key, verdict, { ttl }).catch(() => undefined));
      }
    }
    await Promise.all(writes);
  }

  private shared(): ICacheService | undefined {
    try {
      return this.opts.shared?.();
    } catch {
      return undefined;
    }
  }

  private remember(key: string, verdict: StoredVerdict): void {
    this.local.delete(key);
    this.local.set(key, verdict);
    while (this.local.size > this.localMax) {
      const oldest = this.local.keys().next().value;
      if (oldest === undefined) break;
      this.local.delete(oldest);
    }
  }
}

const servable = (
  verdict: StoredVerdict,
  now: number,
  read: VerdictReadOptions,
): boolean => {
  if (now < verdict.validUntil) return true;
  return verdict.v === 'hidden' && read.checkDown === true;
};
