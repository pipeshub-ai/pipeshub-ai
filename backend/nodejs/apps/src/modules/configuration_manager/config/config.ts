import {
  etcdTlsFromEnv,
  type EtcdTlsFiles,
} from '../../../libs/keyValueStore/etcd-tls';
import { StoreType } from '../../../libs/keyValueStore/constants/KeyValueStoreType';
import {
  cipherRing,
  hashedRing,
  secretKeyRing,
} from '../../../libs/encryptor/secret-key-ring';
import { Logger } from '../../../libs/services/logger.service';
import { RedisStoreConfig } from '../../../libs/keyValueStore/providers/RedisDistributedKeyValueStore';

const logger = Logger.getInstance({ service: 'ConfigurationManagerConfig' });

export interface ConfigurationManagerStoreConfig {
  host: string;
  port: number;
  dialTimeout: number;
  /** Set when ETCD_URL is https: the client certificate etcd requires (SEC-D-06). */
  tls?: EtcdTlsFiles;
}

export interface ConfigurationManagerConfig {
  storeType: string;
  storeConfig: ConfigurationManagerStoreConfig;
  redisConfig: RedisStoreConfig;
  secretKey: string;
  algorithm: string;
}

/** The platform cipher's writer key (ADR-KEY-02: HKDF of `SECRET_KEY`). */
export const getHashedSecretKey = (): string =>
  cipherRing(checkedRing())[0] as string;

/** SHA-256 of each ring secret, the writer's first: the KEK's and run-key digests' input keying material. */
export const getHashedSecretKeyRing = (): string[] => hashedRing(checkedRing());

function checkedRing(): ReturnType<typeof secretKeyRing> {
  try {
    return secretKeyRing();
  } catch (error) {
    logger.warn('The deployment secret key ring is not usable', {
      reason: error instanceof Error ? error.message : 'unknown',
    });
    throw error;
  }
}

export const loadConfigurationManagerConfig =
  (): ConfigurationManagerConfig => {
    // Determine store type from KV_STORE_TYPE env variable (defaults to redis)
    const kvStoreType = process.env.KV_STORE_TYPE?.toLowerCase() || 'redis';
    const storeType =
      kvStoreType === 'redis'
        ? StoreType.Redis
        : kvStoreType === 'postgres'
          ? StoreType.Postgres
          : StoreType.Etcd3;

    return {
      storeType: storeType,
      storeConfig: {
        host: process.env.ETCD_HOST || 'http://localhost',
        port: parseInt(process.env.ETCD_PORT || '2379', 10),
        dialTimeout: parseInt(process.env.ETCD_DIAL_TIMEOUT || '2000', 10),
        tls: etcdTlsFromEnv(),
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
      },
      secretKey: getHashedSecretKey(),
      algorithm: process.env.ALGORITHM || 'aes-256-gcm',
    };
  };
