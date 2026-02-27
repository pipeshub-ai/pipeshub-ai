import { injectable } from 'inversify';
import { Cluster } from 'ioredis';
import { Logger } from './logger.service';

import { RedisCacheError } from '../errors/redis.errors';
import { CacheOptions, RedisConfig } from '../types/redis.types';

@injectable()
export class RedisService {
  private client!: Cluster;
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
    this.connected = true;
    this.logger.info('Redis client connected');
  });

  this.client.on('error', (error) => {
    this.connected = false;
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
