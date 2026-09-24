import { injectable } from 'inversify';
import type { RedisClient } from './redis/connectionProvider.interface';
import { Logger } from './logger.service';

import { RedisCacheError } from '../errors/redis.errors';
import { CacheOptions, RedisConfig } from '../types/redis.types';
import { getRedisProvider } from './redis/connectionProviderFactory';
import { redisConnectionConfigFromHostPort } from './redis/connectionConfig';
import { IRedisConnectionProvider } from './redis/connectionProvider.interface';
import { ICacheService } from './cache/cacheService.interface';

@injectable()
export class RedisService implements ICacheService {
  private client!: RedisClient;
  private connected = false;
  private readonly logger: Logger;
  private readonly defaultTTL = 3600; // 1 hour
  private readonly keyPrefix: string;
  private readonly config: RedisConfig;

  constructor(config: RedisConfig, logger: Logger) {
    this.config = config;
    this.logger = logger;
    const provider = getRedisProvider(
      redisConnectionConfigFromHostPort({
        host: this.config.host,
        port: this.config.port,
        username: this.config.username,
        password: this.config.password,
        db: this.config.db,
        tls: this.config.tls,
      }),
    );
    // REDIS_KEY_NAMESPACE (R9) is applied here, not as an ioredis
    // `keyPrefix`, so it also covers `SCAN`/pattern-based lookups.
    this.keyPrefix = provider.keyNamespace
      ? `${provider.keyNamespace}:${config.keyPrefix ?? 'app:'}`
      : config.keyPrefix ?? 'app:';
    this.initializeClient(provider);
  }

  private initializeClient(provider: IRedisConnectionProvider): void {
    // A dedicated, non-shared client (never the provider's `getClient()`):
    // this instance owns its own connect/error/ready lifecycle and is
    // `disconnect()`-ed independently of every other Redis-backed feature
    // in the process.
    this.client = provider.createClient({
      connectTimeoutMs: this.config.connectTimeout,
      maxRetriesPerRequest: this.config.maxRetriesPerRequest,
      enableOfflineQueue: this.config.enableOfflineQueue,
    });

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
    // Evicted before the quit, not after: this instance is shared between
    // containers (see getSharedRedisService), and both dispose paths call
    // disconnect(). Whichever disposes first would otherwise leave a dead
    // client in the shared map for the other container to keep using.
    evictSharedRedisService(this);
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

  /**
   * Whether `other` describes the same connection this instance was built
   * for. Kept as a method rather than exposing the credentials so nothing
   * has to read them back out to make the comparison.
   */
  matchesConnection(other: RedisConfig): boolean {
    return (
      this.config.username === other.username &&
      this.config.password === other.password &&
      (this.config.tls ?? false) === (other.tls ?? false)
    );
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

// --- Process-level shared instance (R11) ------------------------------------
//
// `RedisService` opens its own dedicated client, and both the auth and
// token-manager containers used to build one each -- two connections doing
// identical session/cache traffic, and on Redis Cluster two full topology
// clients with a socket to every node. Cached by connection identity so the
// common case (both containers reading the same stored Redis config)
// collapses onto one instance.

const cacheServices = new Map<string, RedisService>();

/**
 * Endpoint identity only. Credentials are deliberately NOT part of the key:
 * hashing them here protected nothing (the cached `RedisService` retains the
 * cleartext config for its lifetime, so an attacker who can read this map can
 * already read the password through its value), while a plain SHA-256 over a
 * password is what `js/insufficient-password-hash` flags. A KDF would be
 * absurd on a cache lookup. Credential differences are caught by comparing
 * against the cached instance instead — see `getSharedRedisService`.
 */
function endpointKey(config: RedisConfig): string {
  return JSON.stringify([
    config.host,
    config.port,
    config.db ?? 0,
    config.keyPrefix ?? 'app:',
  ]);
}

export function getSharedRedisService(
  config: RedisConfig,
  logger: Logger,
): RedisService {
  const key = endpointKey(config);
  const existing = cacheServices.get(key);
  // Same endpoint is not the same connection: a cached instance built with
  // different credentials or TLS must not be handed out, or the second caller
  // silently inherits the first one's authenticated client. The comparison
  // lives on the instance so its credentials are never copied back out.
  if (existing?.matchesConnection(config)) {
    return existing;
  }
  const service = new RedisService(config, logger);
  cacheServices.set(key, service);
  return service;
}

/**
 * Drop a disconnected instance so the next caller builds a live one rather
 * than inheriting a closed client.
 */
function evictSharedRedisService(service: RedisService): void {
  for (const [key, cached] of cacheServices) {
    if (cached === service) {
      cacheServices.delete(key);
      return;
    }
  }
}

/** Test-only: drop the shared instances between test cases. */
export function resetSharedRedisServices(): void {
  cacheServices.clear();
}
