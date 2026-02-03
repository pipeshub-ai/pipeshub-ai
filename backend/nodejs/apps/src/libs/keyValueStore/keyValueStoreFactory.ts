import { Etcd3DistributedKeyValueStore } from './providers/Etcd3DistributedKeyValueStore';
import { DistributedKeyValueStore } from './keyValueStore';
import { InMemoryKeyValueStore } from './providers/InMemoryKeyValueStore';
import {
  RedisDistributedKeyValueStore,
  RedisStoreConfig,
} from './providers/RedisDistributedKeyValueStore';
import { StoreType } from './constants/KeyValueStoreType';
import { ConfigurationManagerStoreConfig } from '../../modules/configuration_manager/config/config';
import { DeserializationFailedError, SerializationFailedError } from '../errors/serialization.error';
import { BadRequestError } from '../errors/http.errors';

/**
 * Type guard to check if config is an etcd configuration.
 * Etcd config has 'dialTimeout' which is not present in Redis config.
 */
function isEtcdConfig(
  config: ConfigurationManagerStoreConfig | RedisStoreConfig,
): config is ConfigurationManagerStoreConfig {
  return 'dialTimeout' in config;
}

/**
 * Type guard to check if config is a Redis configuration.
 * Redis config has optional 'db', 'password', or 'keyPrefix' properties.
 */
function isRedisConfig(
  config: ConfigurationManagerStoreConfig | RedisStoreConfig,
): config is RedisStoreConfig {
  return 'db' in config || 'password' in config || 'keyPrefix' in config || !('dialTimeout' in config);
}

export class KeyValueStoreFactory {
  static createStore<T>(
    type: StoreType,
    config: ConfigurationManagerStoreConfig | RedisStoreConfig,
    serializer?: (value: T) => Buffer,
    deserializer?: (buffer: Buffer) => T,
  ): DistributedKeyValueStore<T> {

    switch (type) {
      case StoreType.Etcd3:
        if (!serializer) {
          throw new SerializationFailedError('Serializer and deserializer functions must be provided for Etcd3 store.');
        }
        if (!deserializer) {
          throw new DeserializationFailedError('Deserializer function must be provided for Etcd3 store.');
        }
        if (!isEtcdConfig(config)) {
          throw new BadRequestError('Invalid config for Etcd3 store: expected ConfigurationManagerStoreConfig with dialTimeout');
        }
        return new Etcd3DistributedKeyValueStore<T>(
          config,
          serializer,
          deserializer,
        );
      case StoreType.InMemory:
        return new InMemoryKeyValueStore<T>();
      case StoreType.Redis:
        if (!serializer) {
          throw new SerializationFailedError('Serializer function must be provided for Redis store.');
        }
        if (!deserializer) {
          throw new DeserializationFailedError('Deserializer function must be provided for Redis store.');
        }
        if (!isRedisConfig(config)) {
          throw new BadRequestError('Invalid config for Redis store: expected RedisStoreConfig');
        }
        return new RedisDistributedKeyValueStore<T>(
          config,
          serializer,
          deserializer,
        );
      default:
        throw new BadRequestError(`Unsupported store type: ${type}`);
    }
  }
}
