import { DistributedKeyValueStore } from '../keyValueStore';
import { KeyAlreadyExistsError, KeyNotFoundError } from '../../errors/etcd.errors';
import { RedisConfig } from '../../types/redis.types';
import { Logger } from '../../services/logger.service';
import { getRedisProvider } from '../../services/redis/connectionProviderFactory';
import { redisConnectionConfigFromHostPort } from '../../services/redis/connectionConfig';
import type { IRedisConnectionProvider, RedisClient } from '../../services/redis/connectionProvider.interface';

export interface RedisStoreConfig extends RedisConfig {
  keyPrefix?: string;
}

const CACHE_INVALIDATION_CHANNEL = 'pipeshub:cache:invalidate';

/**
 * Atomic single-key compare-and-set (R6): `GET` then conditional `SET`,
 * done server-side so no client round-trip can race another writer.
 * `WATCH`/`MULTI`/`EXEC` is what this replaces -- MemoryDB's cluster mode
 * pins a transaction to whichever node the key hashes to, but many
 * `ioredis.Cluster` versions do not route `WATCH` there reliably, so a Lua
 * script (single key, one node, one round trip) is the only comparison
 * primitive that behaves identically on standalone and cluster.
 *
 * `ARGV[3]` disambiguates "expected no existing value" from "expected an
 * empty buffer": Lua's `redis.call('GET', ...)` returns `false` for a
 * missing key, which cannot be produced any other way from a Lua string
 * comparison against `ARGV[1]`.
 */
const COMPARE_AND_SET_SCRIPT = `
local current = redis.call('GET', KEYS[1])
if ARGV[3] == '1' then
  if current == false then
    redis.call('SET', KEYS[1], ARGV[2])
    return 1
  end
else
  if current ~= false and current == ARGV[1] then
    redis.call('SET', KEYS[1], ARGV[2])
    return 1
  end
end
return 0
`;

function isNoScriptError(error: unknown): boolean {
  return error instanceof Error && error.message.includes('NOSCRIPT');
}


export class RedisDistributedKeyValueStore<T> implements DistributedKeyValueStore<T> {
  private client: RedisClient;
  private readonly provider: IRedisConnectionProvider;
  private casSha: string | null = null;
  private serializer: (value: T) => Buffer;
  private deserializer: (buffer: Buffer) => T;
  private keyPrefix: string;
  /** REDIS_KEY_NAMESPACE (R9), resolved once from the provider. */
  private readonly namespacedPrefix: string;
  private watchers: Map<string, Array<(value: T | null) => void>> = new Map();

  constructor(
    config: RedisStoreConfig,
    serializer: (value: T) => Buffer,
    deserializer: (buffer: Buffer) => T,
  ) {
    this.keyPrefix = config.keyPrefix || 'pipeshub:kv:';
    this.serializer = serializer;
    this.deserializer = deserializer;

    this.provider = getRedisProvider(
      redisConnectionConfigFromHostPort({
        host: config.host,
        port: config.port,
        password: config.password,
        db: config.db,
      }),
    );
    this.namespacedPrefix = this.provider.keyNamespace
      ? `${this.provider.keyNamespace}:${this.keyPrefix}`
      : this.keyPrefix;
    this.client = this.provider.createClient({
      connectTimeoutMs: config.connectTimeout,
      maxRetriesPerRequest: config.maxRetriesPerRequest,
      enableOfflineQueue: config.enableOfflineQueue,
    });
  }

  private buildKey(key: string): string {
    return `${this.namespacedPrefix}${key}`;
  }

  private stripPrefix(key: string): string {
    if (key.startsWith(this.namespacedPrefix)) {
      return key.substring(this.namespacedPrefix.length);
    }
    return key;
  }

  async createKey(key: string, value: T): Promise<void> {
    const fullKey = this.buildKey(key);
    const result = await this.client.set(
      fullKey,
      this.serializer(value),
      'NX',
    );

    if (result === null) {
      throw new KeyAlreadyExistsError('Key already exists.');
    }

    this.notifyWatchers(key, value);
  }

  async updateValue(key: string, value: T): Promise<void> {
    const fullKey = this.buildKey(key);
    const result = await this.client.set(fullKey, this.serializer(value), 'XX');

    if (result === null) {
      throw new KeyNotFoundError(`Key "${key}" does not exist.`);
    }

    this.notifyWatchers(key, value);
  }

  async getKey(key: string): Promise<T | null> {
    const fullKey = this.buildKey(key);
    const buffer = await this.client.getBuffer(fullKey);

    if (buffer === null) {
      return null;
    }

    return this.deserializer(buffer);
  }

  async deleteKey(key: string): Promise<void> {
    const fullKey = this.buildKey(key);
    await this.client.del(fullKey);
    this.notifyWatchers(key, null);
  }

  async getAllKeys(): Promise<string[]> {
    return this.scanPrefixed(`${this.namespacedPrefix}*`);
  }

  async watchKey(key: string, callback: (value: T | null) => void): Promise<void> {
    // Redis doesn't have native watch support like etcd, so this
    // implementation uses in-memory callbacks that are triggered on
    // create/update/delete operations through this store instance.
    // For cross-process notifications, consider using Redis Pub/Sub.
    if (!this.watchers.has(key)) {
      this.watchers.set(key, []);
    }
    this.watchers.get(key)!.push(callback);
  }

  private notifyWatchers(key: string, value: T | null): void {
    const callbacks = this.watchers.get(key);
    if (callbacks) {
      for (const callback of callbacks) {
        try {
          callback(value);
        } catch (error) {
          // Log error but don't throw to avoid breaking other watchers
          Logger.getInstance().error('Error in watcher callback for key [REDACTED]:', error);
        }
      }
    }
  }

  async listKeysInDirectory(directory: string): Promise<string[]> {
    const prefix = directory.endsWith('/') ? directory : `${directory}/`;
    return this.scanPrefixed(`${this.namespacedPrefix}${prefix}*`);
  }

  /**
   * `provider.scanKeys()`, not `client.scan()` (R2): ioredis routes
   * `Cluster.scan()` to one arbitrary node, so a raw SCAN on a cluster
   * returns whatever fraction of the keyspace that shard happens to hold --
   * silently, with no error to notice. The provider owns the topology and
   * fans out over every master.
   */
  private async scanPrefixed(pattern: string): Promise<string[]> {
    const keys: string[] = [];
    for await (const key of this.provider.scanKeys(pattern)) {
      keys.push(this.stripPrefix(key));
    }
    return keys;
  }

  private async ensureCasScriptLoaded(): Promise<string> {
    if (this.casSha === null) {
      this.casSha = await this.provider.loadScript(COMPARE_AND_SET_SCRIPT);
    }
    return this.casSha;
  }

  async compareAndSet(
    key: string,
    expectedValue: T | null,
    newValue: T,
  ): Promise<boolean> {
    const fullKey = this.buildKey(key);
    const newBuffer = this.serializer(newValue);
    const expectedBuffer =
      expectedValue !== null ? this.serializer(expectedValue) : Buffer.alloc(0);
    const expectNoExisting = expectedValue === null ? '1' : '0';

    try {
      let sha = await this.ensureCasScriptLoaded();
      let result: number;
      try {
        result = (await this.client.evalsha(
          sha,
          1,
          fullKey,
          expectedBuffer,
          newBuffer,
          expectNoExisting,
        )) as number;
      } catch (error) {
        if (!isNoScriptError(error)) {
          throw error;
        }
        // Evicted by a `SCRIPT FLUSH` or a MemoryDB node replacement (R6):
        // reload once and retry rather than failing the whole operation.
        this.casSha = null;
        sha = await this.ensureCasScriptLoaded();
        result = (await this.client.evalsha(
          sha,
          1,
          fullKey,
          expectedBuffer,
          newBuffer,
          expectNoExisting,
        )) as number;
      }

      if (result !== 1) {
        return false;
      }

      this.notifyWatchers(key, newValue);
      return true;
    } catch (error) {
      Logger.getInstance().error(`Error in compareAndSet for key ${key}:`, error);
      // If operation fails, return false
      return false;
    }
  }

  private invalidationChannel(): string {
    // Namespaced (R9): two deployments sharing one Redis/MemoryDB endpoint
    // must not invalidate each other's caches.
    return this.provider.keyNamespace
      ? `${this.provider.keyNamespace}:${CACHE_INVALIDATION_CHANNEL}`
      : CACHE_INVALIDATION_CHANNEL;
  }

  async publishCacheInvalidation(key: string): Promise<void> {
    try {
      await this.client.publish(
        this.invalidationChannel(),
        key,
      );
    } catch (error) {
      Logger.getInstance().warn(
        `Failed to publish cache invalidation for key ${key}:`,
        error,
      );
    }
  }

  async disconnect(): Promise<void> {
    this.watchers.clear();
    await this.client.quit();
  }

  /**
   * Health check for Redis KV store.
   * Pings the Redis server to verify connectivity.
   */
  async healthCheck(): Promise<boolean> {
    try {
      const result = await this.client.ping();
      return result === 'PONG';
    } catch (error) {
      Logger.getInstance().error('Redis KV store health check failed:', error);
      return false;
    }
  }
}
