import { Etcd3 } from 'etcd3';
import { Redis } from 'ioredis';
import { Logger } from '../../services/logger.service';

export interface MigrationConfig {
  etcd: {
    host: string;
    port: number;
    dialTimeout?: number;
  };
  redis: {
    host: string;
    port: number;
    password?: string;
    db?: number;
    keyPrefix?: string;
  };
}

export interface MigrationResult {
  success: boolean;
  migratedKeys: string[];
  failedKeys: string[];
  skippedKeys: string[];
  error?: string;
}

// Migration flag key (without prefix - prefix is added when accessing)
const MIGRATION_FLAG_KEY = '/migrations/etcd_to_redis';

/**
 * Service to migrate configuration data from etcd to Redis.
 * This is used when transitioning from etcd to Redis as the KV store backend.
 */
export class KVStoreMigrationService {
  private logger = Logger.getInstance({ service: 'KVStoreMigrationService' });
  private etcdClient: Etcd3 | null = null;
  private redisClient: Redis | null = null;
  private config: MigrationConfig;

  constructor(config: MigrationConfig) {
    this.config = config;
  }

  /**
   * Check if migration has already been completed
   */
  async isMigrationCompleted(): Promise<boolean> {
    let redis: Redis | null = null;
    try {
      redis = new Redis({
        host: this.config.redis.host,
        port: this.config.redis.port,
        password: this.config.redis.password,
        db: this.config.redis.db || 0,
      });

      const keyPrefix = this.config.redis.keyPrefix || 'pipeshub:kv:';
      const flagKey = `${keyPrefix}${MIGRATION_FLAG_KEY}`;
      const value = await redis.get(flagKey);

      return value === 'true';
    } catch (error) {
      this.logger.error('Failed to check migration flag', { error });
      return false;
    } finally {
      if (redis) {
        await redis.quit();
      }
    }
  }

  /**
   * Set the migration completed flag
   */
  private async setMigrationCompleted(): Promise<void> {
    const keyPrefix = this.config.redis.keyPrefix || 'pipeshub:kv:';
    const flagKey = `${keyPrefix}${MIGRATION_FLAG_KEY}`;
    await this.redisClient!.set(flagKey, 'true');
    this.logger.info(`Migration flag set at ${flagKey}`);
  }

  /**
   * Publish cache invalidation message to clear all in-memory caches.
   * This notifies all services (including Python) to clear their local caches
   * so they can pick up the new configuration from Redis.
   */
  private async publishCacheInvalidation(): Promise<void> {
    try {
      const channel = 'pipeshub:cache:invalidate';
      const message = '__CLEAR_ALL__';
      await this.redisClient!.publish(channel, message);
      this.logger.info('Published cache invalidation message to clear all caches');
    } catch (error) {
      this.logger.error('Failed to publish cache invalidation message', { error });
    }
  }

  /**
   * Check if etcd is available and has data
   */
  async isEtcdAvailable(): Promise<boolean> {
    try {
      const hostWithPort = `${this.config.etcd.host}:${this.config.etcd.port}`;
      const client = new Etcd3({
        hosts: [hostWithPort],
        dialTimeout: this.config.etcd.dialTimeout || 5000,
      });

      // Try to get maintenance status
      await client.maintenance.status();
      await client.close();
      return true;
    } catch (error) {
      this.logger.warn('etcd is not available', { error });
      return false;
    }
  }

  /**
   * Check if Redis already has configuration data
   */
  async hasRedisData(): Promise<boolean> {
    try {
      const redis = new Redis({
        host: this.config.redis.host,
        port: this.config.redis.port,
        password: this.config.redis.password,
        db: this.config.redis.db || 0,
      });

      const keyPrefix = this.config.redis.keyPrefix || 'pipeshub:kv:';
      const keys = await redis.keys(`${keyPrefix}*`);
      await redis.quit();

      return keys.length > 0;
    } catch (error) {
      this.logger.error('Failed to check Redis data', { error });
      return false;
    }
  }

  /**
   * Migrate all data from etcd to Redis
   */
  async migrate(): Promise<MigrationResult> {
    const result: MigrationResult = {
      success: false,
      migratedKeys: [],
      failedKeys: [],
      skippedKeys: [],
    };

    try {
      this.logger.info('Starting etcd to Redis migration...');

      // Check if etcd is available
      const etcdAvailable = await this.isEtcdAvailable();
      if (!etcdAvailable) {
        result.error = 'etcd is not available. Cannot migrate data.';
        this.logger.error(result.error);
        return result;
      }

      // Connect to both stores
      await this.connect();

      // Get all keys from etcd
      const allKeys = await this.etcdClient!.getAll().keys();
      this.logger.info(`Found ${allKeys.length} keys in etcd`);

      // Migrate each key
      for (const key of allKeys) {
        try {
          // Get value from etcd (raw bytes)
          const value = await this.etcdClient!.get(key).string();

          if (value !== null) {
            // Store in Redis with the same key (preserving encryption)
            const keyPrefix = this.config.redis.keyPrefix || 'pipeshub:kv:';
            const fullKey = `${keyPrefix}${key}`;
            await this.redisClient!.set(fullKey, value);

            result.migratedKeys.push(key);
            this.logger.debug(`Migrated key: ${key}`);
          } else {
            result.skippedKeys.push(key);
            this.logger.debug(`Skipped key (null value): ${key}`);
          }
        } catch (keyError) {
          result.failedKeys.push(key);
          this.logger.error(`Failed to migrate key: ${key}`, { error: keyError });
        }
      }

      result.success = result.failedKeys.length === 0;

      // Set migration flag if successful
      if (result.success) {
        await this.setMigrationCompleted();
        await this.publishCacheInvalidation();
      }

      this.logger.info('Migration completed', {
        migrated: result.migratedKeys.length,
        failed: result.failedKeys.length,
        skipped: result.skippedKeys.length,
      });

      return result;
    } catch (error: any) {
      result.error = error.message;
      this.logger.error('Migration failed', { error });
      return result;
    } finally {
      await this.disconnect();
    }
  }

  private async connect(): Promise<void> {
    // Connect to etcd
    const hostWithPort = `${this.config.etcd.host}:${this.config.etcd.port}`;
    this.etcdClient = new Etcd3({
      hosts: [hostWithPort],
      dialTimeout: this.config.etcd.dialTimeout || 5000,
    });

    // Connect to Redis
    this.redisClient = new Redis({
      host: this.config.redis.host,
      port: this.config.redis.port,
      password: this.config.redis.password,
      db: this.config.redis.db || 0,
    });
  }

  private async disconnect(): Promise<void> {
    if (this.etcdClient) {
      await this.etcdClient.close();
      this.etcdClient = null;
    }
    if (this.redisClient) {
      await this.redisClient.quit();
      this.redisClient = null;
    }
  }
}

/**
 * Check if migration is needed and perform it if necessary.
 * This should be called during application startup when using Redis as KV store.
 *
 * Migration is only performed if:
 * 1. Migration has not already been completed (flag not set)
 * 2. etcd is available and has data
 *
 * When KV_STORE_TYPE=redis, Redis is expected to already have configuration data
 * (either from admin panel setup or previous migration).
 */
export async function checkAndMigrateIfNeeded(
  config: MigrationConfig,
): Promise<MigrationResult | null> {
  const logger = Logger.getInstance({ service: 'KVStoreMigration' });
  const migrationService = new KVStoreMigrationService(config);

  // Check if migration has already been completed
  const migrationCompleted = await migrationService.isMigrationCompleted();
  if (migrationCompleted) {
    logger.info('Migration already completed. Skipping.');
    return null;
  }

  // Check if etcd is available for migration
  const etcdAvailable = await migrationService.isEtcdAvailable();
  if (!etcdAvailable) {
    logger.info('etcd is not available. Skipping migration check.');
    return null;
  }

  // etcd is available
  logger.info('etcd is available. Checking for data to migrate...');
  return await migrationService.migrate();
}
