import { Redis } from 'ioredis';
import { DistributedKeyValueStore } from '../keyValueStore';
import { KeyAlreadyExistsError, KeyNotFoundError } from '../../errors/etcd.errors';
import { RedisConfig } from '../../types/redis.types';
import { Logger } from '../../services/logger.service';

export interface RedisStoreConfig extends RedisConfig {
  keyPrefix?: string;
}

export class RedisDistributedKeyValueStore<T> implements DistributedKeyValueStore<T> {
  private client: Redis;
  private serializer: (value: T) => Buffer;
  private deserializer: (buffer: Buffer) => T;
  private keyPrefix: string;
  private watchers: Map<string, Array<(value: T | null) => void>> = new Map();

  constructor(
    config: RedisStoreConfig,
    serializer: (value: T) => Buffer,
    deserializer: (buffer: Buffer) => T,
  ) {
    this.keyPrefix = config.keyPrefix || 'kv:';
    this.serializer = serializer;
    this.deserializer = deserializer;

    this.client = new Redis({
      host: config.host,
      port: config.port,
      password: config.password,
      db: config.db || 0,
      connectTimeout: config.connectTimeout || 10000,
      maxRetriesPerRequest: config.maxRetriesPerRequest || 3,
      enableOfflineQueue: config.enableOfflineQueue ?? true,
      retryStrategy: (times: number) => {
        const delay = Math.min(times * 50, 2000);
        return delay;
      },
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
    const existingValue = await this.client.get(fullKey);

    if (existingValue !== null) {
      throw new KeyAlreadyExistsError(`Key "${key}" already exists.`);
    }

    await this.client.set(fullKey, this.serializer(value));
    this.notifyWatchers(key, value);
  }

  async updateValue(key: string, value: T): Promise<void> {
    const fullKey = this.buildKey(key);
    const existingValue = await this.client.get(fullKey);

    if (existingValue === null) {
      throw new KeyNotFoundError(`Key "${key}" does not exist.`);
    }

    await this.client.set(fullKey, this.serializer(value));
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
          Logger.getInstance().error(`Error in watcher callback for key ${key}:`, error);
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
      // Get current value
      const currentBuffer = await this.client.getBuffer(fullKey);

      // Compare buffers directly for exact match
      const valuesMatch =
        (expectedValue === null && currentBuffer === null) ||
        (expectedBuffer !== null &&
          currentBuffer !== null &&
          expectedBuffer.equals(currentBuffer));

      if (!valuesMatch) {
        // Values don't match, CAS failed
        return false;
      }

      await this.client.set(fullKey, newBuffer);

      // Verify the update succeeded by reading back
      const updatedBuffer = await this.client.getBuffer(fullKey);
      const success =
        updatedBuffer !== null && updatedBuffer.equals(newBuffer);

      if (success) {
        this.notifyWatchers(key, newValue);
      }

      return success;
    } catch {
      // If operation fails, return false
      return false;
    }
  }

  async disconnect(): Promise<void> {
    this.watchers.clear();
    await this.client.quit();
  }
}
