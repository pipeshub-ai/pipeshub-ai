import { ensurePgRuntime } from '../../db/runtime-boot';
import type { Tx } from '../../db/uow';
import { RedisCacheError } from '../../errors/redis.errors';
import type { CacheOptions } from '../../types/redis.types';
import type { ICacheService } from './cacheService.interface';

const DEFAULT_TTL_SECONDS = 3600; // as RedisService
const PURGE_EVERY_MS = 60_000;
const PURGE_RETRY_MS = 5_000;
const PURGE_BATCH = 1000;
const PURGE_BACKLOG_MS = 1_000; // a full batch means more are waiting: purge again a second later, not a minute

/**
 * The Node API's cache on PostgreSQL (`platform.cache_entries`), for a deployment with no Redis server (`REDIS_MODE=postgres`).
 * Holds what RedisService holds: sign-in sessions, the SAML desktop hand-off and the e-mail rate-limit counters. Unlike the
 * in-process cache, every API process sees the same entries and they survive a restart, so a restart does not sign everyone out
 * and two replicas count one address's requests together.
 *
 * Same keys and the same semantics as RedisService: values are JSON, `set` expires after `ttl` seconds (one hour by default),
 * `increment` is INCR and, given a `ttl`, EXPIRE on every call. An expired row reads as absent and is replaced by the next write;
 * expired rows are purged in small batches, at most once a minute per process.
 */
export class PostgresCacheService implements ICacheService {
  private connected = true;
  private lastPurge = 0;

  constructor(private readonly keyPrefix = 'app:') {}

  private buildKey(key: string, namespace?: string): string {
    const ns =
      namespace !== undefined && namespace !== '' ? `${namespace}:` : '';
    return `${this.keyPrefix}${ns}${key}`;
  }

  private async unit<R>(
    write: boolean,
    fn: (tx: Tx) => Promise<R>,
  ): Promise<R> {
    return ensurePgRuntime().withUow({ preOrg: true, write }, async (tx) => {
      const out = await fn(tx);
      if (write && Date.now() - this.lastPurge > PURGE_EVERY_MS) {
        await this.purge(tx);
      }
      return out;
    });
  }

  /**
   * Housekeeping after a write: delete a batch of expired rows. In its own savepoint, so a purge that fails (a timeout, a lost
   * lock) never fails the caller's write; rows another replica is purging are skipped, not waited for. A failed purge is tried
   * again a few seconds later, not on every write. A purge that removes a full batch is followed by another a second later, so a
   * backlog (many counters expiring at once) drains at up to a batch a second instead of a batch a minute; each write still
   * carries at most one batch.
   */
  private async purge(tx: Tx): Promise<void> {
    try {
      const r = await tx.savepoint('cache_purge', () =>
        tx.query(
          '/* pipeshub:cache.purge */ DELETE FROM platform.cache_entries WHERE key IN (SELECT key FROM platform.cache_entries WHERE expires_at <= now() LIMIT $1 FOR UPDATE SKIP LOCKED)',
          [PURGE_BATCH],
        ),
      );
      this.lastPurge =
        (r.rowCount ?? 0) >= PURGE_BATCH
          ? Date.now() - PURGE_EVERY_MS + PURGE_BACKLOG_MS
          : Date.now();
    } catch {
      this.lastPurge = Date.now() - PURGE_EVERY_MS + PURGE_RETRY_MS;
    }
  }

  private static fail(
    what: string,
    key: string,
    error: unknown,
  ): RedisCacheError {
    // the same error type as RedisService, so callers that tell a cache failure apart keep working
    return new RedisCacheError(what, {
      key,
      error: error instanceof Error ? error.message : 'Unknown error',
    });
  }

  async get<T>(key: string, options: CacheOptions = {}): Promise<T | null> {
    try {
      const r = await this.unit(false, (tx) =>
        tx.query<{ value: string }>(
          `/* pipeshub:cache.get */ SELECT value FROM platform.cache_entries WHERE key = $1 AND (expires_at IS NULL OR expires_at > now())`,
          [this.buildKey(key, options.namespace)],
        ),
      );
      const row = r.rows[0];
      return row === undefined ? null : (JSON.parse(row.value) as T);
    } catch (error) {
      throw PostgresCacheService.fail('Failed to get cached value', key, error);
    }
  }

  async set(
    key: string,
    value: unknown,
    options: CacheOptions = {},
  ): Promise<void> {
    try {
      const ttl = options.ttl ?? DEFAULT_TTL_SECONDS;
      await this.unit(true, (tx) =>
        tx.query(
          // SET ... EX: the value and a new expiry, whether or not the key existed
          `/* pipeshub:cache.set */ INSERT INTO platform.cache_entries (key, value, expires_at, updated_at)
           VALUES ($1, $2, now() + make_interval(secs => $3::double precision), now())
           ON CONFLICT (key) DO UPDATE SET value = excluded.value, expires_at = excluded.expires_at, updated_at = now()`,
          [this.buildKey(key, options.namespace), JSON.stringify(value), ttl],
        ),
      );
    } catch (error) {
      throw PostgresCacheService.fail('Failed to set cached value', key, error);
    }
  }

  async delete(key: string, options: CacheOptions = {}): Promise<void> {
    try {
      await this.unit(true, (tx) =>
        tx.query(
          '/* pipeshub:cache.delete */ DELETE FROM platform.cache_entries WHERE key = $1',
          [this.buildKey(key, options.namespace)],
        ),
      );
    } catch (error) {
      throw PostgresCacheService.fail(
        'Failed to delete cached value',
        key,
        error,
      );
    }
  }

  async increment(key: string, options: CacheOptions = {}): Promise<number> {
    try {
      const ttl = options.ttl ?? null;
      const r = await this.unit(true, (tx) =>
        tx.query<{ value: string }>(
          // INCR (an expired counter starts again at 1), then EXPIRE when a ttl is given; without one an existing expiry stays,
          // as INCR alone keeps it. A value that is not an integer fails, as INCR does.
          `/* pipeshub:cache.incr */ INSERT INTO platform.cache_entries AS c (key, value, expires_at, updated_at)
           VALUES ($1, '1', CASE WHEN $2::double precision IS NULL THEN NULL ELSE now() + make_interval(secs => $2::double precision) END, now())
           ON CONFLICT (key) DO UPDATE SET
             value = CASE WHEN c.expires_at <= now() THEN '1' ELSE (c.value::bigint + 1)::text END,
             expires_at = CASE WHEN $2::double precision IS NOT NULL THEN excluded.expires_at
                               WHEN c.expires_at <= now() THEN NULL ELSE c.expires_at END,
             updated_at = now()
           RETURNING value`,
          [this.buildKey(key, options.namespace), ttl],
        ),
      );
      return Number(r.rows[0]?.value);
    } catch (error) {
      throw PostgresCacheService.fail('Failed to increment value', key, error);
    }
  }

  /** The connection pool is the process's PostgreSQL runtime, shared with the key-value store and the queue: it is not closed here. */
  disconnect(): Promise<void> {
    this.connected = false;
    return Promise.resolve();
  }

  isConnected(): boolean {
    return this.connected;
  }
}
