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
        return new Etcd3DistributedKeyValueStore<T>(
          config as ConfigurationManagerStoreConfig,
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
        return new RedisDistributedKeyValueStore<T>(
          config as RedisStoreConfig,
          serializer,
          deserializer,
        );
      default:
        throw new BadRequestError(`Unsupported store type: ${type}`);
    }
  }
}
