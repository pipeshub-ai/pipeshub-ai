import { StoreType } from '../../../libs/keyValueStore/constants/KeyValueStoreType';
import crypto from 'crypto';

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

export const generateAndStoreSecretKey = (): string => {
  // Generate a random 32-byte hex string

  const secretKey = process.env.SECRET_KEY;
  if (!secretKey) {
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
      secretKey: generateAndStoreSecretKey(),
      algorithm: process.env.ALGORITHM! || 'aes-256-gcm',
    };
  };
