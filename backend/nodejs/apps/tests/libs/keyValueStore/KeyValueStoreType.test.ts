import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import {
  StoreType,
  KeyValueStoreType,
} from '../../../src/libs/keyValueStore/constants/KeyValueStoreType';

describe('KeyValueStoreType', () => {
  afterEach(() => {
    sinon.restore();
  });

  describe('StoreType enum', () => {
    it('should have Etcd3 value', () => {
      expect(StoreType.Etcd3).to.equal('etcd3');
    });

    it('should have InMemory value', () => {
      expect(StoreType.InMemory).to.equal('inmemory');
    });

    it('should have Redis value', () => {
      expect(StoreType.Redis).to.equal('redis');
    });
  });

  describe('fromString', () => {
    it('should return StoreType.Etcd3 for "etcd3"', () => {
      expect(KeyValueStoreType.fromString('etcd3')).to.equal(StoreType.Etcd3);
    });

    it('should return StoreType.InMemory for "inmemory"', () => {
      expect(KeyValueStoreType.fromString('inmemory')).to.equal(StoreType.InMemory);
    });

    it('should return StoreType.Redis for "redis"', () => {
      expect(KeyValueStoreType.fromString('redis')).to.equal(StoreType.Redis);
    });

    it('should throw Error for unsupported store type', () => {
      try {
        KeyValueStoreType.fromString('dynamodb');
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(Error);
        expect((error as Error).message).to.include('Unsupported store type');
        expect((error as Error).message).to.include('dynamodb');
      }
    });

    it('should throw Error for empty string', () => {
      try {
        KeyValueStoreType.fromString('');
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(Error);
      }
    });
  });

  describe('toString', () => {
    it('should return the string value of Etcd3', () => {
      expect(KeyValueStoreType.toString(StoreType.Etcd3)).to.equal('etcd3');
    });

    it('should return the string value of InMemory', () => {
      expect(KeyValueStoreType.toString(StoreType.InMemory)).to.equal('inmemory');
    });

    it('should return the string value of Redis', () => {
      expect(KeyValueStoreType.toString(StoreType.Redis)).to.equal('redis');
    });
  });
});
