import { Cluster } from 'ioredis';
import { DistributedKeyValueStore } from '../keyValueStore';
import { KeyAlreadyExistsError, KeyNotFoundError } from '../../errors/etcd.errors';
import { RedisConfig } from '../../types/redis.types';
import { Logger } from '../../services/logger.service';

export interface RedisStoreConfig extends RedisConfig {
  keyPrefix?: string;
}

const CACHE_INVALIDATION_CHANNEL = 'pipeshub:cache:invalidate';


export class RedisDistributedKeyValueStore<T> implements DistributedKeyValueStore<T> {
  private client: Cluster;
  private serializer: (value: T) => Buffer;
  private deserializer: (buffer: Buffer) => T;
  private keyPrefix: string;
  private watchers: Map<string, Array<(value: T | null) => void>> = new Map();
  private logger: Logger;
  private config: RedisStoreConfig;

  constructor(
    config: RedisStoreConfig,
    logger: Logger,
    serializer: (value: T) => Buffer,
    deserializer: (buffer: Buffer) => T,
  ) {
    this.keyPrefix = config.keyPrefix || 'pipeshub:kv:';
    this.serializer = serializer;
    this.deserializer = deserializer;
    this.logger = logger;
    this.config = config;
    this.client = new Cluster(
    [
      {
        host: this.config.host, // clustercfg.redis-test.bdswez.memorydb.us-east-1.amazonaws.com
        port: this.config.port || 6379,
      },
    ],
    {
      // MemoryDB specific settings
      dnsLookup: (address, callback) => callback(null, address),

      redisOptions: {
        username: this.config.username || 'default', // MemoryDB ACL username
        password: this.config.password,
        tls: {},
        connectTimeout: this.config.connectTimeout || 10000,
        maxRetriesPerRequest: this.config.maxRetriesPerRequest || 3,
      },

      // Cluster-specific options
      slotsRefreshTimeout: 2000,
      slotsRefreshInterval: 5000,
    }
  );

  this.client.on('connect', () => {
    this.logger.info('Redis client connected');
  });

  this.client.on('error', (error) => {
    this.logger.error('Redis client error', {
      message: error.message,
      code: (error as any).code,
      lastNodeError: (error as any).lastNodeError?.message,
    });
  });

  this.client.on('node error', (error, address) => {
    this.logger.error('Redis node error', {
      message: error.message,
      address,
    });
  });
    this.client.on('ready', () => {
      this.logger.info('Redis client ready');
    });

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
    const keys: string[] = [];

    let cursor = '0';
    do {
      const [newCursor, foundKeys] = await this.client.scan(
        cursor,
        'MATCH',
        pattern,
        'COUNT',
        100,
      );
      cursor = newCursor;
      keys.push(...foundKeys.map((k) => this.stripPrefix(k)));
    } while (cursor !== '0');

    return keys;
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
    const keys: string[] = [];

    let cursor = '0';
    do {
      const [newCursor, foundKeys] = await this.client.scan(
        cursor,
        'MATCH',
        pattern,
        'COUNT',
        100,
      );
      cursor = newCursor;
      keys.push(...foundKeys.map((k) => this.stripPrefix(k)));
    } while (cursor !== '0');

    return keys;
  }

  async compareAndSet(
    key: string,
    expectedValue: T | null,
    newValue: T,
  ): Promise<boolean> {
    const fullKey = this.buildKey(key);
    const newBuffer = this.serializer(newValue);
    const expectedBuffer =
      expectedValue !== null ? this.serializer(expectedValue) : null;

    try {
      // Watch the key for any changes.
      await this.client.watch(fullKey);

      const currentBuffer = await this.client.getBuffer(fullKey);

      // Compare buffers for an exact match.
      const valuesMatch =
        (expectedValue === null && currentBuffer === null) ||
        (expectedBuffer !== null &&
          currentBuffer !== null &&
          expectedBuffer.equals(currentBuffer));

      if (!valuesMatch) {
        // Values don't match, abort.
        await this.client.unwatch();
        return false;
      }

      // Atomically set the new value.
      const result = await this.client.multi().set(fullKey, newBuffer).exec();

      // If result is null, it means the key was modified by another client
      // after we started watching it. The transaction was aborted.
      if (result === null) {
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
