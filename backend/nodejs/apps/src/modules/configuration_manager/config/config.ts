import { StoreType } from '../../../libs/keyValueStore/constants/KeyValueStoreType';
import crypto from 'crypto';
import { Logger } from '../../../libs/services/logger.service';
import { RedisStoreConfig } from '../../../libs/keyValueStore/providers/RedisDistributedKeyValueStore';

const logger = Logger.getInstance({ service: 'ConfigurationManagerConfig' });

export interface ConfigurationManagerStoreConfig {
  host: string;
  port: number;
  dialTimeout: number;
}

export interface ConfigurationManagerConfig {
  storeType: string;
  storeConfig: ConfigurationManagerStoreConfig;
  redisConfig: RedisStoreConfig;
  secretKey: string;
  algorithm: string;
}

export const getHashedSecretKey = (): string => {
  const secretKey = process.env.SECRET_KEY;
  if (!secretKey) {
    logger.warn('SECRET_KEY environment variable is not set. It is required');
    throw new Error('SECRET_KEY environment variable is required');
  }
  const hashedKey = crypto.createHash('sha256').update(secretKey).digest();
  return hashedKey.toString('hex');
};

export const loadConfigurationManagerConfig =
  (): ConfigurationManagerConfig => {
    // Determine store type from KV_STORE_TYPE env variable (defaults to etcd)
    const kvStoreType = process.env.KV_STORE_TYPE?.toLowerCase() || 'etcd';
    const storeType = kvStoreType === 'redis' ? StoreType.Redis : StoreType.Etcd3;

    return {
      storeType: storeType,
      storeConfig: {
        host: process.env.ETCD_HOST || 'http://localhost',
        port: parseInt(process.env.ETCD_PORT || '2379', 10),
        dialTimeout: parseInt(process.env.ETCD_DIAL_TIMEOUT || '2000', 10),
      },
      redisConfig: {
        host: process.env.REDIS_HOST || 'localhost',
        port: parseInt(process.env.REDIS_PORT || '6379', 10),
        password: process.env.REDIS_PASSWORD || undefined,
        db: parseInt(process.env.REDIS_DB || '0', 10),
        keyPrefix: process.env.REDIS_KV_PREFIX || 'pipeshub:kv:',
        connectTimeout: parseInt(process.env.REDIS_TIMEOUT || '10000', 10),
      },
      secretKey: getHashedSecretKey(),
      algorithm: process.env.ALGORITHM || 'aes-256-gcm',
    };
  };
