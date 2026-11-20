import { ensurePgRuntime } from '../../../../../libs/db/runtime-boot';
import {
  FAIL_FAST_CACHE_RETRY_MS,
  FAIL_FAST_CACHE_TIMEOUT_MS,
  withTimeout,
} from '../../../../../libs/services/cache/fail-fast-cache';
import type { IRedisConnectionProvider } from '../../../../../libs/services/redis/connectionProvider.interface';
import { defaultRedisMode } from '../../../../../libs/services/redis/connectionProviderFactory';

/**
 * Permission epochs: counters the AI backend moves after a write that changes who may open something commits, so a
 * verdict saved under the values read before it stops being used. One per connector, one per person and one for the
 * collections of an organisation. The AI backend builds the same keys (`app/services/permissions/epochs.py`).
 */

export const EPOCH_KEY_PREFIX = 'state:epoch:';

const part = (what: string, id: string): string => {
  // A ':' would let two different ids name one key.
  if (typeof id !== 'string' || id === '' || id.includes(':')) {
    throw new Error(`a ${what} id cannot name a permission epoch`);
  }
  return id;
};

/** Moves when the grants, groups or records of one connector change. */
export const connectorEpochKey = (orgId: string, connectorId: string): string =>
  `${EPOCH_KEY_PREFIX}ge:${part('organisation', orgId)}:${part('connector', connectorId)}`;

/** Moves when who one person is changes: their account, their teams, their app access. `userId` is the person's id. */
export const principalEpochKey = (orgId: string, userId: string): string =>
  `${EPOCH_KEY_PREFIX}pe:${part('organisation', orgId)}:${part('user', userId)}`;

/** Moves when a collection of the organisation is granted, its grants change, or records are added to or removed from it. */
export const kbEpochKey = (orgId: string): string =>
  `${EPOCH_KEY_PREFIX}ge:${part('organisation', orgId)}:kb`;

/** Where the counters live: each key's raw value, null for a key nobody moved yet. */
export interface EpochSource {
  values(keys: readonly string[]): Promise<ReadonlyArray<string | null>>;
}

const COUNTER = /^\d{1,16}$/;

/**
 * The value of each counter, a counter nobody moved yet as 0. Null when any of them cannot be read: whoever asked
 * must then treat every one as changed.
 */
export async function readEpochs(
  source: EpochSource,
  keys: readonly string[],
): Promise<Map<string, number> | null> {
  if (keys.length === 0) return new Map();
  try {
    const values = await source.values(keys);
    if (values.length !== keys.length) return null;
    const epochs = new Map<string, number>();
    for (const [i, key] of keys.entries()) {
      const raw = values[i];
      if (raw === null || raw === undefined) {
        epochs.set(key, 0);
        continue;
      }
      const value = Number(raw);
      if (!COUNTER.test(raw) || !Number.isSafeInteger(value)) return null;
      epochs.set(key, value);
    }
    return epochs;
  } catch {
    return null;
  }
}

/** The counters on the deployment's Redis, under the key namespace both sides apply. */
export const redisEpochSource = (
  provider: Pick<IRedisConnectionProvider, 'getClient' | 'keyNamespace'>,
): EpochSource => ({
  values: (keys) =>
    provider
      .getClient()
      .mget(
        ...keys.map((key) =>
          provider.keyNamespace !== ''
            ? `${provider.keyNamespace}:${key}`
            : key,
        ),
      ),
});

/** The counters in PostgreSQL (`REDIS_MODE=postgres`), where the AI backend keeps its runtime state. */
export const pgEpochSource: EpochSource = {
  values: (keys) =>
    ensurePgRuntime().withUow({ preOrg: true, write: false }, async (tx) => {
      const { rows } = await tx.query<{ key: string; v: string }>(
        `/* pipeshub:epoch.read */ SELECT key, convert_from(value, 'UTF8') AS v FROM platform.kv_entries
          WHERE key = ANY($1::text[]) AND (expires_at IS NULL OR expires_at > now())`,
        [keys],
      );
      const byKey = new Map(rows.map((row) => [row.key, row.v]));
      return keys.map((key) => byKey.get(key) ?? null);
    }),
};

/** No store the two sides share (one process keeping its state in memory): nothing can be read. */
export const unavailableEpochSource: EpochSource = {
  values: () =>
    Promise.reject(new Error('no store is shared for permission epochs')),
};

/** A read of the counters holds a request no longer than this, as a cache call; past it the read is unknown. */
export const EPOCH_READ_TIMEOUT_MS = FAIL_FAST_CACHE_TIMEOUT_MS;
/** After a failed read the store is skipped this long, so an outage costs one timeout, not one per read. */
export const EPOCH_READ_RETRY_MS = FAIL_FAST_CACHE_RETRY_MS;

/**
 * `source` read fast or not at all, as the caches are: each read races `EPOCH_READ_TIMEOUT_MS`, and after a failure
 * the store is skipped for `EPOCH_READ_RETRY_MS`, then one read tries it again. A skipped or failed read rejects, which
 * `readEpochs` makes "unknown", so no verdict is kept; the reader check itself goes on.
 */
export function failFastEpochSource(
  source: EpochSource,
  now: () => number = Date.now,
): EpochSource {
  let skipUntil = 0;
  let probing = false;
  return {
    async values(keys) {
      if (now() < skipUntil || probing) {
        throw new Error('permission epochs skipped after a recent failure');
      }
      const probe = skipUntil !== 0;
      probing = probe;
      try {
        const values = await withTimeout(
          source.values(keys),
          EPOCH_READ_TIMEOUT_MS,
        );
        if (probe) skipUntil = 0;
        return values;
      } catch (error) {
        skipUntil = now() + EPOCH_READ_RETRY_MS;
        throw error;
      } finally {
        if (probe) probing = false;
      }
    },
  };
}

/** Where this deployment's counters are, read fast or not at all. */
export const epochSourceFor = (
  provider:
    | Pick<IRedisConnectionProvider, 'getClient' | 'keyNamespace'>
    | undefined,
): EpochSource => {
  const mode = defaultRedisMode().toLowerCase();
  if (mode === 'postgres') return failFastEpochSource(pgEpochSource);
  if (mode === 'inprocess' || !provider) return unavailableEpochSource;
  return failFastEpochSource(redisEpochSource(provider));
};
