import { injectable } from 'inversify';
import { Redis, RedisOptions } from 'ioredis';
import { Logger } from './logger.service';

import { RedisCacheError } from '../errors/redis.errors';
import { CacheOptions, RedisConfig } from '../types/redis.types';

@injectable()
export class RedisService {
  private client!: Redis;
  private connected = false;
  private readonly logger: Logger;
  private readonly defaultTTL = 3600; // 1 hour
  private readonly keyPrefix: string;
  private readonly config: RedisConfig;

  constructor(config: RedisConfig, logger: Logger) {
    this.config = config;
    this.logger = logger;
    this.keyPrefix = config.keyPrefix || 'app:';
    this.initializeClient();
  }

  private initializeClient(): void {
    const redisOptions: RedisOptions = {
      host: this.config.host,
      port: this.config.port,
      password: this.config.password,
      db: this.config.db || 0,
      connectTimeout: this.config.connectTimeout || 10000,
      maxRetriesPerRequest: this.config.maxRetriesPerRequest || 3,
      enableOfflineQueue: this.config.enableOfflineQueue || true,
      retryStrategy: (times: number) => {
        const delay = Math.min(times * 50, 2000);
        return delay;
      },
    };

    this.client = new Redis(redisOptions);

    this.client.on('connect', () => {
      this.connected = true;
      this.logger.info('Redis client connected');
    });

    this.client.on('error', (error) => {
      this.connected = false;
      this.logger.error('Redis client error', { error });
    });

    this.client.on('ready', () => {
      this.logger.info('Redis client ready');
    });
  }

  async disconnect(): Promise<void> {
    try {
      await this.client.quit();
      this.connected = false;
      this.logger.info('Redis client disconnected');
    } catch (error) {
      this.logger.error('Error disconnecting Redis client', { error });
    }
  }
  isConnected(): boolean {
    return this.connected;
  }

  private buildKey(key: string, namespace?: string): string {
    return `${this.keyPrefix}${namespace ? `${namespace}:` : ''}${key}`;
  }

  async get<T>(key: string, options: CacheOptions = {}): Promise<T | null> {
    try {
      const fullKey = this.buildKey(key, options.namespace);
      const value = await this.client.get(fullKey);

      if (!value) {
        return null;
      }

      return JSON.parse(value);
    } catch (error) {
      throw new RedisCacheError('Failed to get cached value', {
        key,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async set<T>(
    key: string,
    value: T,
    options: CacheOptions = {},
  ): Promise<void> {
    try {
      const fullKey = this.buildKey(key, options.namespace);
      const serializedValue = JSON.stringify(value);
      const ttl = options.ttl || this.defaultTTL;

      await this.client.set(fullKey, serializedValue, 'EX', ttl);
    } catch (error) {
      throw new RedisCacheError('Failed to set cached value', {
        key,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async delete(key: string, options: CacheOptions = {}): Promise<void> {
    try {
      const fullKey = this.buildKey(key, options.namespace);
      await this.client.del(fullKey);
    } catch (error) {
      throw new RedisCacheError('Failed to delete cached value', {
        key,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async exists(key: string, options: CacheOptions = {}): Promise<boolean> {
    try {
      const fullKey = this.buildKey(key, options.namespace);
      const result = await this.client.exists(fullKey);
      return result === 1;
    } catch (error) {
      throw new RedisCacheError('Failed to check key existence', {
        key,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async increment(key: string, options: CacheOptions = {}): Promise<number> {
    try {
      const fullKey = this.buildKey(key, options.namespace);
      const result = await this.client.incr(fullKey);

      if (options.ttl) {
        await this.client.expire(fullKey, options.ttl);
      }

      return result;
    } catch (error) {
      throw new RedisCacheError('Failed to increment value', {
        key,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async setHash(
    key: string,
    field: string,
    value: any,
    options: CacheOptions = {},
  ): Promise<void> {
    try {
      const fullKey = this.buildKey(key, options.namespace);
      const serializedValue = JSON.stringify(value);

      await this.client.hset(fullKey, field, serializedValue);

      if (options.ttl) {
        await this.client.expire(fullKey, options.ttl);
      }
    } catch (error) {
      throw new RedisCacheError('Failed to set hash field', {
        key,
        field,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async getHash<T>(
    key: string,
    field: string,
    options: CacheOptions = {},
  ): Promise<T | null> {
    try {
      const fullKey = this.buildKey(key, options.namespace);
      const value = await this.client.hget(fullKey, field);

      if (!value) {
        return null;
      }

      return JSON.parse(value);
    } catch (error) {
      throw new RedisCacheError('Failed to get hash field', {
        key,
        field,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async getAllHash<T>(
    key: string,
    options: CacheOptions = {},
  ): Promise<Record<string, T> | null> {
    try {
      const fullKey = this.buildKey(key, options.namespace);
      const values = await this.client.hgetall(fullKey);

      if (!values) {
        return null;
      }

      return Object.entries(values).reduce(
        (acc, [field, value]) => ({
          ...acc,
          [field]: JSON.parse(value),
        }),
        {},
      );
    } catch (error) {
      throw new RedisCacheError('Failed to get all hash fields', {
        key,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async deleteHash(
    key: string,
    field: string,
    options: CacheOptions = {},
  ): Promise<void> {
    try {
      const fullKey = this.buildKey(key, options.namespace);
      await this.client.hdel(fullKey, field);
    } catch (error) {
      throw new RedisCacheError('Failed to delete hash field', {
        key,
        field,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async acquireLock(
    lockName: string,
    ttl: number = 30,
  ): Promise<string | null> {
    const token = Math.random().toString(36).substring(2);
    const locked = await this.client.set(
      `lock:${lockName}`,
      token,
      'EX',
      ttl,
      'NX',
    );

    return locked ? token : null;
  }

  async releaseLock(lockName: string, token: string): Promise<boolean> {
    const script = `
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("del", KEYS[1])
            else
                return 0
            end
        `;

    const result = await this.client.eval(script, 1, `lock:${lockName}`, token);

    return result === 1;
  }
}
