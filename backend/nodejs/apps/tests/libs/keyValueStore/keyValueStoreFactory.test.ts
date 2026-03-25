import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { KeyValueStoreFactory } from '../../../src/libs/keyValueStore/keyValueStoreFactory';
import { StoreType } from '../../../src/libs/keyValueStore/constants/KeyValueStoreType';
import { Etcd3DistributedKeyValueStore } from '../../../src/libs/keyValueStore/providers/Etcd3DistributedKeyValueStore';
import { InMemoryKeyValueStore } from '../../../src/libs/keyValueStore/providers/InMemoryKeyValueStore';
import { RedisDistributedKeyValueStore } from '../../../src/libs/keyValueStore/providers/RedisDistributedKeyValueStore';
import { SerializationFailedError, DeserializationFailedError } from '../../../src/libs/errors/serialization.error';
import { BadRequestError } from '../../../src/libs/errors/http.errors';
import { ConfigurationManagerStoreConfig } from '../../../src/modules/configuration_manager/config/config';
import { RedisStoreConfig } from '../../../src/libs/keyValueStore/providers/RedisDistributedKeyValueStore';

describe('KeyValueStoreFactory', () => {
  const etcdConfig: ConfigurationManagerStoreConfig = {
    host: 'http://localhost',
    port: 2379,
    dialTimeout: 2000,
  };

  const redisConfig: RedisStoreConfig = {
    host: 'localhost',
    port: 6379,
    password: 'secret',
    db: 0,
    keyPrefix: 'test:kv:',
  };

  const serializer = (value: string) => Buffer.from(value);
  const deserializer = (buffer: Buffer) => buffer.toString();

  afterEach(() => {
    sinon.restore();
  });

  // ---- Etcd3 store creation -------------------------------------
  describe('createStore - Etcd3', () => {
    it('should create an Etcd3DistributedKeyValueStore', () => {
      const store = KeyValueStoreFactory.createStore(
        StoreType.Etcd3,
        etcdConfig,
        serializer,
        deserializer,
      );
      expect(store).to.be.instanceOf(Etcd3DistributedKeyValueStore);
    });

    it('should throw SerializationFailedError when serializer is not provided', () => {
      try {
        KeyValueStoreFactory.createStore(
          StoreType.Etcd3,
          etcdConfig,
          undefined as any,
          deserializer,
        );
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(SerializationFailedError);
      }
    });

    it('should throw DeserializationFailedError when deserializer is not provided', () => {
      try {
        KeyValueStoreFactory.createStore(
          StoreType.Etcd3,
          etcdConfig,
          serializer,
          undefined as any,
        );
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(DeserializationFailedError);
      }
    });

    it('should throw BadRequestError when config is invalid for Etcd3', () => {
      try {
        // Pass redis config (no dialTimeout) for etcd3
        KeyValueStoreFactory.createStore(
          StoreType.Etcd3,
          redisConfig as any,
          serializer,
          deserializer,
        );
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError);
        expect((error as Error).message).to.include('Invalid config for Etcd3 store');
      }
    });
  });

  // ---- InMemory store creation ----------------------------------
  describe('createStore - InMemory', () => {
    it('should create an InMemoryKeyValueStore', () => {
      const store = KeyValueStoreFactory.createStore(
        StoreType.InMemory,
        etcdConfig, // config is ignored for in-memory
      );
      expect(store).to.be.instanceOf(InMemoryKeyValueStore);
    });

    it('should create InMemoryKeyValueStore without serializer/deserializer', () => {
      const store = KeyValueStoreFactory.createStore(
        StoreType.InMemory,
        etcdConfig,
      );
      expect(store).to.be.instanceOf(InMemoryKeyValueStore);
    });
  });

  // ---- Redis store creation -------------------------------------
  describe('createStore - Redis', () => {
    it('should create a RedisDistributedKeyValueStore', () => {
      const store = KeyValueStoreFactory.createStore(
        StoreType.Redis,
        redisConfig,
        serializer,
        deserializer,
      );
      expect(store).to.be.instanceOf(RedisDistributedKeyValueStore);
    });

    it('should throw SerializationFailedError when serializer is not provided', () => {
      try {
        KeyValueStoreFactory.createStore(
          StoreType.Redis,
          redisConfig,
          undefined as any,
          deserializer,
        );
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(SerializationFailedError);
      }
    });

    it('should throw DeserializationFailedError when deserializer is not provided', () => {
      try {
        KeyValueStoreFactory.createStore(
          StoreType.Redis,
          redisConfig,
          serializer,
          undefined as any,
        );
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(DeserializationFailedError);
      }
    });

    it('should throw BadRequestError when config is invalid for Redis', () => {
      try {
        // Pass etcd config for Redis - however the isRedisConfig type guard
        // is broad (accepts configs without dialTimeout), so the etcd config
        // which has dialTimeout would fail the isRedisConfig check.
        // Actually, etcdConfig has dialTimeout so isRedisConfig returns false for it
        // only if it also lacks 'db', 'password', 'keyPrefix' AND has 'dialTimeout'.
        // Let's verify: etcdConfig has dialTimeout and no db/password/keyPrefix.
        // isRedisConfig: 'db' in config || 'password' in config || 'keyPrefix' in config || !('dialTimeout' in config)
        // For etcdConfig: false || false || false || false = false
        // So isRedisConfig returns false for etcdConfig => BadRequestError
        KeyValueStoreFactory.createStore(
          StoreType.Redis,
          etcdConfig as any,
          serializer,
          deserializer,
        );
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError);
        expect((error as Error).message).to.include('Invalid config for Redis store');
      }
    });
  });

  // ---- Unsupported store type -----------------------------------
  describe('createStore - unsupported type', () => {
    it('should throw BadRequestError for unsupported store type', () => {
      try {
        KeyValueStoreFactory.createStore(
          'unknown' as StoreType,
          etcdConfig,
          serializer,
          deserializer,
        );
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(BadRequestError);
        expect((error as Error).message).to.include('Unsupported store type');
      }
    });
  });
});
