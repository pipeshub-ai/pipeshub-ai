import { DistributedKeyValueStore } from '../keyValueStore';
import { KeyAlreadyExistsError, KeyNotFoundError } from '../../errors/etcd.errors';
import { RedisConfig } from '../../types/redis.types';
import { Logger } from '../../services/logger.service';
import {
  buildRedisClient,
  clusterAwareScan,
  RedisClient,
} from '../../services/redisClientFactory';

export interface RedisStoreConfig extends RedisConfig {
  keyPrefix?: string;
}

const CACHE_INVALIDATION_CHANNEL = 'pipeshub:cache:invalidate';

// Atomic single-key compare-and-set. KEYS[1] is the key; ARGV[1] is "1" when
// the caller expects the key to be absent, "0" otherwise; ARGV[2] is the
// expected serialized value (ignored when ARGV[1] == "1"); ARGV[3] is the new
// serialized value. Returns 1 on success, 0 on mismatch.
const CAS_LUA = `
local current = redis.call('GET', KEYS[1])
if ARGV[1] == '1' then
  if current == false then
    redis.call('SET', KEYS[1], ARGV[3])
    return 1
  end
  return 0
end
if current ~= false and current == ARGV[2] then
  redis.call('SET', KEYS[1], ARGV[3])
  return 1
end
return 0
`;


export class RedisDistributedKeyValueStore<T> implements DistributedKeyValueStore<T> {
  private client: RedisClient;
  private serializer: (value: T) => Buffer;
  private deserializer: (buffer: Buffer) => T;
  private keyPrefix: string;
  private watchers: Map<string, Array<(value: T | null) => void>> = new Map();

  constructor(
    config: RedisStoreConfig,
    serializer: (value: T) => Buffer,
    deserializer: (buffer: Buffer) => T,
  ) {
    this.keyPrefix = config.keyPrefix || 'pipeshub:kv:';
    this.serializer = serializer;
    this.deserializer = deserializer;

    this.client = buildRedisClient(config);
  }

  private buildKey(key: string): string {
    return `${this.keyPrefix}${key}`;
  }

  private stripPrefix(key: string): string {
    if (key.startsWith(this.keyPrefix)) {
      return key.substring(this.keyPrefix.length);
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
    const pattern = `${this.keyPrefix}*`;
    const found = await clusterAwareScan(this.client, pattern, 100);
    return found.map((k) => this.stripPrefix(k));
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
    const pattern = `${this.keyPrefix}${prefix}*`;
    const found = await clusterAwareScan(this.client, pattern, 100);
    return found.map((k) => this.stripPrefix(k));
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
    const expectNilFlag = expectedValue === null ? '1' : '0';

    try {
      // Single-key atomic CAS via Lua. Works identically on standalone and
      // Cluster (single KEY = single hash slot). Replaces WATCH+MULTI, which
      // is unsafe under Cluster because separate awaited commands may take
      // different pooled connections.
      const result = (await this.client.eval(
        CAS_LUA,
        1,
        fullKey,
        expectNilFlag,
        expectedBuffer,
        newBuffer,
      )) as number;

      if (result !== 1) {
        return false;
      }

      this.notifyWatchers(key, newValue);
      return true;
    } catch (error) {
      Logger.getInstance().error(`Error in compareAndSet for key ${key}:`, error);
      return false;
    }
  }

  async publishCacheInvalidation(key: string): Promise<void> {
    try {
      await this.client.publish(
        CACHE_INVALIDATION_CHANNEL,
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
