import { StoreType } from '../../../libs/keyValueStore/constants/KeyValueStoreType';
import crypto from 'crypto';
import { Logger } from '../../../libs/services/logger.service';
import { RedisStoreConfig } from '../../../libs/keyValueStore/providers/RedisDistributedKeyValueStore';
import { RedisClusterNode, RedisMode } from '../../../libs/types/redis.types';

const parseRedisNodes = (raw?: string): RedisClusterNode[] | undefined => {
  if (!raw) return undefined;
  const nodes: RedisClusterNode[] = [];
  for (const rawEntry of raw.split(',')) {
    const entry = rawEntry.trim();
    if (!entry) continue;
    // Split on the LAST colon so IPv6 literals like `[::1]:6379` or
    // `fe80::1:6379` parse correctly. parseInt + isNaN guards a non-numeric
    // port from silently becoming NaN.
    const lastColon = entry.lastIndexOf(':');
    const host = lastColon === -1 ? entry : entry.slice(0, lastColon);
    const portStr = lastColon === -1 ? '6379' : entry.slice(lastColon + 1);
    if (!host) continue;
    const port = parseInt(portStr || '6379', 10);
    if (Number.isNaN(port)) {
      throw new Error(
        `REDIS_NODES entry has non-numeric port: '${entry}'`,
      );
    }
    nodes.push({ host, port });
  }
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
    // Determine store type from KV_STORE_TYPE env variable (defaults to etcd)
    const kvStoreType = process.env.KV_STORE_TYPE?.toLowerCase() || 'etcd';
    const storeType = kvStoreType === 'redis' ? StoreType.Redis : StoreType.Etcd3;

    const redisMode: RedisMode =
      (process.env.REDIS_MODE?.toLowerCase() as RedisMode) === 'cluster'
        ? 'cluster'
        : 'standalone';
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
