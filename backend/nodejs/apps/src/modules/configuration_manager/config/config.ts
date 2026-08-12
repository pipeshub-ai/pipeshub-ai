import { StoreType } from '../../../libs/keyValueStore/constants/KeyValueStoreType';
import crypto from 'crypto';
import { Logger } from '../../../libs/services/logger.service';
import { RedisStoreConfig } from '../../../libs/keyValueStore/providers/RedisDistributedKeyValueStore';
import { RedisMode } from '../../../libs/types/redis.types';
import { parseRedisNodes as parseRedisNodesShared } from '../../../libs/services/redisClientFactory';

// Thin wrapper over the shared parser: this caller wants `undefined` (not an
// empty array) when REDIS_NODES is unset, to leave the field absent in config.
const parseRedisNodes = (raw?: string) => {
  const nodes = parseRedisNodesShared(raw);
  return nodes.length > 0 ? nodes : undefined;
};

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
    // Determine store type from KV_STORE_TYPE env variable (defaults to redis)
    const kvStoreType = process.env.KV_STORE_TYPE?.toLowerCase() || 'redis';
    const storeType = kvStoreType === 'redis' ? StoreType.Redis : StoreType.Etcd3;

    // Reject anything that is not a known mode: silently treating a typo like
    // `cluser` as standalone would point the app at the wrong topology.
    const rawRedisMode = process.env.REDIS_MODE?.trim().toLowerCase();
    if (rawRedisMode && rawRedisMode !== 'cluster' && rawRedisMode !== 'standalone') {
      throw new Error(
        `Invalid REDIS_MODE '${process.env.REDIS_MODE}'. Must be 'standalone' or 'cluster'.`,
      );
    }
    const redisMode: RedisMode =
      rawRedisMode === 'cluster' ? 'cluster' : 'standalone';
    const redisNodes = parseRedisNodes(process.env.REDIS_NODES);
    if (redisMode === 'cluster' && (!redisNodes || redisNodes.length === 0)) {
      throw new Error(
        'REDIS_MODE=cluster requires REDIS_NODES to be set (comma-separated host:port list).',
      );
    }

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
        username: process.env.REDIS_USERNAME || undefined,
        password: process.env.REDIS_PASSWORD || undefined,
        tls: process.env.REDIS_TLS === 'true',
        db: parseInt(process.env.REDIS_DB || '0', 10),
        keyPrefix: process.env.REDIS_KV_PREFIX || 'pipeshub:kv:',
        connectTimeout: parseInt(process.env.REDIS_TIMEOUT || '10000', 10),
        mode: redisMode,
        nodes: redisNodes,
      },
      secretKey: getHashedSecretKey(),
      algorithm: process.env.ALGORITHM || 'aes-256-gcm',
    };
  };
