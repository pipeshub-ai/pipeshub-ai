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
    this.keyPrefix = config.keyPrefix ?? 'app:';
    this.initializeClient();
  }

  private initializeClient(): void {
    const redisOptions: RedisOptions = {
      host: this.config.host,
      port: this.config.port,
      username: this.config.username,
      password: this.config.password,
      db: this.config.db ?? 0,
      connectTimeout: this.config.connectTimeout ?? 10000,
      maxRetriesPerRequest: this.config.maxRetriesPerRequest ?? 3,
      enableOfflineQueue: this.config.enableOfflineQueue ?? true,
      retryStrategy: (times: number) => {
        const delay = Math.min(times * 50, 2000);
        return delay;
      },
    };

    // Add TLS configuration if enabled
    if (this.config.tls) {
      redisOptions.tls = {};
      this.logger.info('Redis TLS enabled');
    }

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
    const namespacePrefix =
      namespace !== undefined && namespace !== '' ? `${namespace}:` : '';
    return `${this.keyPrefix}${namespacePrefix}${key}`;
  }

  async get<T>(key: string, options: CacheOptions = {}): Promise<T | null> {
    try {
      const fullKey = this.buildKey(key, options.namespace);
      const value = await this.client.get(fullKey);

      if (value === null) {
        return null;
      }

      return JSON.parse(value) as T;
    } catch (error) {
      throw new RedisCacheError('Failed to get cached value', {
        key,
        error: error instanceof Error ? error.message : 'Unknown error',
      });
    }
  }

  async set(
    key: string,
    value: unknown,
    options: CacheOptions = {},
  ): Promise<void> {
    try {
      const fullKey = this.buildKey(key, options.namespace);
      const serializedValue = JSON.stringify(value);
      const ttl = options.ttl ?? this.defaultTTL;

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

  async increment(key: string, options: CacheOptions = {}): Promise<number> {
    try {
      const fullKey = this.buildKey(key, options.namespace);
      const result = await this.client.incr(fullKey);

      if (options.ttl !== undefined) {
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
}
