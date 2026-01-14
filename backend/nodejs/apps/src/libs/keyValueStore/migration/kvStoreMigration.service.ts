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
  secretKey: string;
  algorithm: string;
}

export interface MigrationResult {
  success: boolean;
  migratedKeys: string[];
  failedKeys: string[];
  skippedKeys: string[];
  error?: string;
}

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

      const keyPrefix = this.config.redis.keyPrefix || 'kv:';
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

      // Check if Redis already has data
      const redisHasData = await this.hasRedisData();
      if (redisHasData) {
        this.logger.info('Redis already has configuration data. Skipping migration.');
        result.success = true;
        result.skippedKeys.push('*');
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
            const keyPrefix = this.config.redis.keyPrefix || 'kv:';
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
 */
export async function checkAndMigrateIfNeeded(
  config: MigrationConfig,
): Promise<MigrationResult | null> {
  const logger = Logger.getInstance({ service: 'KVStoreMigration' });
  const migrationService = new KVStoreMigrationService(config);

  // Check if Redis already has data
  const redisHasData = await migrationService.hasRedisData();
  if (redisHasData) {
    logger.info('Redis already has configuration data. No migration needed.');
    return null;
  }

  // Check if etcd is available for migration
  const etcdAvailable = await migrationService.isEtcdAvailable();
  if (etcdAvailable) {
    logger.info('etcd is available. Starting migration to Redis...');
    return await migrationService.migrate();
  }

  // Neither Redis has data nor etcd is available - this is an error state
  logger.error(
    'CONFIGURATION ERROR: No configuration data found in Redis and etcd is not available. ' +
    'Please either: (1) Start etcd and run migration, or (2) Reconfigure the application.',
  );

  return {
    success: false,
    migratedKeys: [],
    failedKeys: [],
    skippedKeys: [],
    error:
      'Configuration data not found. etcd is not available for migration. ' +
      'Please reconfigure the application or restore etcd data.',
  };
}
