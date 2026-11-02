import { DistributedKeyValueStore } from '../keyValueStore';
import {
  KeyAlreadyExistsError,
  KeyNotFoundError,
} from '../../errors/etcd.errors';
import { Logger } from '../../services/logger.service';
import { ensurePgRuntime } from '../../db/runtime-boot';
import { PgListener, listenUrl } from '../../db/listen';
import type { Tx } from '../../db/uow';

export const KV_CHANNEL = 'pipeshub_kv_changed';
const CLEAR_ALL = '__CLEAR_ALL__';
const MAX_NOTIFY_PAYLOAD = 7000; // NOTIFY payloads are limited to 8000 bytes
const PURGE_EVERY_MS = 60_000;

/**
 * Key-value store on `platform.kv_entries` (`KV_STORE_TYPE=postgres`). Same entries, same bytes and the same
 * semantics as the Redis store (createKey is SET NX, updateValue is SET XX), and the same table the Python
 * services use, so a value written here is read there.
 */
export class PostgresDistributedKeyValueStore<
  T,
> implements DistributedKeyValueStore<T> {
  private watchers = new Map<string, Array<(value: T | null) => void>>();
  private listener: PgListener | undefined;
  private lastPurge = 0;
  /** Called with the changed key (or CLEAR_ALL) when another process publishes a change. */
  onRemoteChange: ((key: string) => void) | undefined;

  constructor(
    private readonly serializer: (value: T) => Buffer,
    private readonly deserializer: (buffer: Buffer) => T,
  ) {}

  private async unit<R>(
    write: boolean,
    fn: (tx: Tx) => Promise<R>,
  ): Promise<R> {
    return ensurePgRuntime().withUow({ preOrg: true, write }, async (tx) => {
      const out = await fn(tx);
      if (write && Date.now() - this.lastPurge > PURGE_EVERY_MS) {
        this.lastPurge = Date.now();
        await tx.query(
          '/* pipeshub:kv.purge */ DELETE FROM platform.kv_entries WHERE key IN (SELECT key FROM platform.kv_entries WHERE expires_at <= now() LIMIT 1000)',
        );
      }
      return out;
    });
  }

  async createKey(key: string, value: T): Promise<void> {
    const r = await this.unit(true, (tx) =>
      tx.query(
        // an expired row counts as absent: only a dead row may be replaced
        `/* pipeshub:kv.setnx */ INSERT INTO platform.kv_entries (key, value, updated_at) VALUES ($1, $2, now())
         ON CONFLICT (key) DO UPDATE SET value = excluded.value, expires_at = NULL, updated_at = now()
         WHERE platform.kv_entries.expires_at <= now() RETURNING true AS ok`,
        [key, this.serializer(value)],
      ),
    );
    if (r.rows.length === 0)
      throw new KeyAlreadyExistsError('Key already exists.');
    this.notifyWatchers(key, value);
  }

  async updateValue(key: string, value: T): Promise<void> {
    const r = await this.unit(true, (tx) =>
      tx.query(
        // like SET XX without EX: the new value carries no expiry
        `/* pipeshub:kv.update */ UPDATE platform.kv_entries SET value = $2, expires_at = NULL, updated_at = now()
         WHERE key = $1 AND (expires_at IS NULL OR expires_at > now()) RETURNING true AS ok`,
        [key, this.serializer(value)],
      ),
    );
    if (r.rows.length === 0)
      throw new KeyNotFoundError(`Key "${key}" does not exist.`);
    this.notifyWatchers(key, value);
  }

  async getKey(key: string): Promise<T | null> {
    const r = await this.unit(false, (tx) =>
      tx.query<{ value: Buffer }>(
        '/* pipeshub:kv.get */ SELECT value FROM platform.kv_entries WHERE key = $1 AND (expires_at IS NULL OR expires_at > now())',
        [key],
      ),
    );
    const row = r.rows[0];
    return row === undefined ? null : this.deserializer(row.value);
  }

  async deleteKey(key: string): Promise<void> {
    await this.unit(true, (tx) =>
      tx.query(
        '/* pipeshub:kv.delete */ DELETE FROM platform.kv_entries WHERE key = $1',
        [key],
      ),
    );
    this.notifyWatchers(key, null);
  }

  private async keys(prefix: string): Promise<string[]> {
    const r = await this.unit(false, (tx) =>
      tx.query<{ key: string }>(
        `/* pipeshub:kv.range */ SELECT key FROM platform.kv_entries
         WHERE starts_with(key, $1) AND (expires_at IS NULL OR expires_at > now()) ORDER BY key`,
        [prefix],
      ),
    );
    return r.rows.map((x) => x.key);
  }

  getAllKeys(): Promise<string[]> {
    return this.keys('');
  }

  listKeysInDirectory(directory: string): Promise<string[]> {
    return this.keys(directory.endsWith('/') ? directory : `${directory}/`);
  }

  watchKey(key: string, callback: (value: T | null) => void): Promise<void> {
    // as with Redis: callbacks fire for writes made through this instance; other processes use onRemoteChange
    const list = this.watchers.get(key) ?? [];
    list.push(callback);
    this.watchers.set(key, list);
    return Promise.resolve();
  }

  private notifyWatchers(key: string, value: T | null): void {
    for (const cb of this.watchers.get(key) ?? []) {
      try {
        cb(value);
      } catch (error) {
        Logger.getInstance().error(
          'Error in watcher callback for key [REDACTED]:',
          error,
        );
      }
    }
  }

  async compareAndSet(
    key: string,
    expectedValue: T | null,
    newValue: T,
  ): Promise<boolean> {
    try {
      const next = this.serializer(newValue);
      const r = await this.unit(true, (tx) =>
        expectedValue === null
          ? tx.query(
              `/* pipeshub:kv.casnew */ INSERT INTO platform.kv_entries (key, value, updated_at) VALUES ($1, $2, now())
               ON CONFLICT (key) DO UPDATE SET value = excluded.value, expires_at = NULL, updated_at = now()
               WHERE platform.kv_entries.expires_at <= now() RETURNING true AS ok`,
              [key, next],
            )
          : tx.query(
              `/* pipeshub:kv.cas */ UPDATE platform.kv_entries SET value = $3, updated_at = now()
               WHERE key = $1 AND value = $2 AND (expires_at IS NULL OR expires_at > now()) RETURNING true AS ok`,
              [key, this.serializer(expectedValue), next],
            ),
      );
      if (r.rows.length === 0) return false;
      this.notifyWatchers(key, newValue);
      return true;
    } catch (error) {
      Logger.getInstance().error(
        `Error in compareAndSet for key ${key}:`,
        error,
      );
      return false;
    }
  }

  async publishCacheInvalidation(key: string): Promise<void> {
    const payload =
      Buffer.byteLength(key) <= MAX_NOTIFY_PAYLOAD ? key : CLEAR_ALL;
    try {
      await this.unit(true, (tx) =>
        tx.query(
          `/* pipeshub:kv.notify */ SELECT pg_notify('${KV_CHANNEL}', $1)`,
          [payload],
        ),
      );
    } catch (error) {
      Logger.getInstance().warn(
        `Failed to publish cache invalidation for key ${key}:`,
        error,
      );
    }
  }

  /** Start hearing changes other processes publish. A no-op (with a warning) when no direct address is configured. */
  async subscribeChanges(onChange: (key: string) => void): Promise<void> {
    const url = listenUrl();
    if (!url) {
      Logger.getInstance().warn(
        'No direct PostgreSQL address for LISTEN (set PG_LISTEN_URL or PG_POOLER=none): changes made by other processes are not pushed',
      );
      return;
    }
    this.onRemoteChange = onChange;
    this.listener = new PgListener(url, KV_CHANNEL, onChange, () => {
      onChange(CLEAR_ALL);
    });
    await this.listener.start();
  }

  async healthCheck(): Promise<boolean> {
    try {
      await this.unit(false, (tx) =>
        tx.query(
          '/* pipeshub:kv.health */ SELECT 1 FROM platform.kv_entries WHERE false',
        ),
      );
      return true;
    } catch (error) {
      Logger.getInstance().error(
        'PostgreSQL KV store health check failed:',
        error,
      );
      return false;
    }
  }

  async disconnect(): Promise<void> {
    this.watchers.clear();
    await this.listener?.stop();
  }
}
