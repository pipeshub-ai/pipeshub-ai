import { StoreType } from '../../../libs/keyValueStore/constants/KeyValueStoreType';
import crypto from 'crypto';
import { Logger } from '../../../libs/services/logger.service';

const logger = Logger.getInstance({ service: 'ConfigurationManagerConfig' });
export interface ConfigurationManagerStoreConfig {
  host: string;
  port: number;
  dialTimeout: number;
}
export interface ConfigurationManagerConfig {
  storeType: string;
  storeConfig: ConfigurationManagerStoreConfig;
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
    return {
      storeType: process.env.STORE_TYPE! || StoreType.Etcd3,
      storeConfig: {
        host: process.env.ETCD_HOST! || 'http://localhost',
        port: parseInt(process.env.STORE_PORT!, 10) || 2379,
        dialTimeout: parseInt(process.env.STORE_DIAL_TIMEOUT!, 10) || 2000,
      },
      secretKey: getHashedSecretKey(),
      algorithm: process.env.ALGORITHM! || 'aes-256-gcm',
    };
  };
