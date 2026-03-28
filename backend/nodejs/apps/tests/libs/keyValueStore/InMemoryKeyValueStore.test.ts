import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { InMemoryKeyValueStore } from '../../../src/libs/keyValueStore/providers/InMemoryKeyValueStore';
import { KeyAlreadyExistsError, KeyNotFoundError, EtcdOperationNotSupportedError } from '../../../src/libs/errors/etcd.errors';

describe('InMemoryKeyValueStore', () => {
  let store: InMemoryKeyValueStore<string>;

  beforeEach(() => {
    store = new InMemoryKeyValueStore<string>();
  });

  afterEach(() => {
    sinon.restore();
  });

  // ---- createKey ------------------------------------------------
  describe('createKey', () => {
    it('should create a new key-value pair', async () => {
      await store.createKey('key1', 'value1');
      const result = await store.getKey('key1');
      expect(result).to.equal('value1');
    });

    it('should throw KeyAlreadyExistsError when key already exists', async () => {
      await store.createKey('key1', 'value1');
      try {
        await store.createKey('key1', 'value2');
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(KeyAlreadyExistsError);
        expect((error as Error).message).to.include('key1');
        expect((error as Error).message).to.include('already exists');
      }
    });

    it('should allow creating different keys', async () => {
      await store.createKey('key1', 'value1');
      await store.createKey('key2', 'value2');
      expect(await store.getKey('key1')).to.equal('value1');
      expect(await store.getKey('key2')).to.equal('value2');
    });
  });

  // ---- updateValue ----------------------------------------------
  describe('updateValue', () => {
    it('should update an existing key', async () => {
      await store.createKey('key1', 'value1');
      await store.updateValue('key1', 'updated');
      const result = await store.getKey('key1');
      expect(result).to.equal('updated');
    });

    it('should throw KeyNotFoundError when key does not exist', async () => {
      try {
        await store.updateValue('nonexistent', 'value');
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(KeyNotFoundError);
        expect((error as Error).message).to.include('nonexistent');
        expect((error as Error).message).to.include('does not exist');
      }
    });
  });

  // ---- getKey ---------------------------------------------------
  describe('getKey', () => {
    it('should return the value for an existing key', async () => {
      await store.createKey('key1', 'value1');
      const result = await store.getKey('key1');
      expect(result).to.equal('value1');
    });

    it('should return null for a nonexistent key', async () => {
      const result = await store.getKey('missing');
      expect(result).to.be.null;
    });
  });

  // ---- deleteKey ------------------------------------------------
  describe('deleteKey', () => {
    it('should remove an existing key', async () => {
      await store.createKey('key1', 'value1');
      await store.deleteKey('key1');
      const result = await store.getKey('key1');
      expect(result).to.be.null;
    });

    it('should not throw when deleting a nonexistent key', async () => {
      await store.deleteKey('nonexistent'); // should not throw
    });

    it('should allow re-creating a deleted key', async () => {
      await store.createKey('key1', 'v1');
      await store.deleteKey('key1');
      await store.createKey('key1', 'v2');
      expect(await store.getKey('key1')).to.equal('v2');
    });
  });

  // ---- getAllKeys ------------------------------------------------
  describe('getAllKeys', () => {
    it('should return empty array for empty store', async () => {
      const keys = await store.getAllKeys();
      expect(keys).to.deep.equal([]);
    });

    it('should return all keys in the store', async () => {
      await store.createKey('a', 'va');
      await store.createKey('b', 'vb');
      await store.createKey('c', 'vc');
      const keys = await store.getAllKeys();
      expect(keys).to.have.length(3);
      expect(keys).to.include.members(['a', 'b', 'c']);
    });

    it('should not include deleted keys', async () => {
      await store.createKey('a', 'va');
      await store.createKey('b', 'vb');
      await store.deleteKey('a');
      const keys = await store.getAllKeys();
      expect(keys).to.deep.equal(['b']);
    });
  });

  // ---- watchKey -------------------------------------------------
  describe('watchKey', () => {
    it('should throw EtcdOperationNotSupportedError', async () => {
      try {
        await store.watchKey('key', sinon.stub());
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(EtcdOperationNotSupportedError);
        expect((error as Error).message).to.include('not supported');
      }
    });
  });

  // ---- listKeysInDirectory --------------------------------------
  describe('listKeysInDirectory', () => {
    it('should return keys matching the directory prefix', async () => {
      await store.createKey('config/app/name', 'myapp');
      await store.createKey('config/app/version', '1.0');
      await store.createKey('config/db/host', 'localhost');
      await store.createKey('other/key', 'val');

      const keys = await store.listKeysInDirectory('config/app/');
      expect(keys).to.have.length(2);
      expect(keys).to.include.members(['config/app/name', 'config/app/version']);
    });

    it('should return empty array when no keys match', async () => {
      await store.createKey('a/b', 'v');
      const keys = await store.listKeysInDirectory('x/y/');
      expect(keys).to.deep.equal([]);
    });

    it('should return empty array for empty store', async () => {
      const keys = await store.listKeysInDirectory('any/');
      expect(keys).to.deep.equal([]);
    });
  });

  // ---- compareAndSet --------------------------------------------
  describe('compareAndSet', () => {
    it('should set new value when expected matches current (primitive)', async () => {
      await store.createKey('key', 'old');
      const result = await store.compareAndSet('key', 'old', 'new');
      expect(result).to.be.true;
      expect(await store.getKey('key')).to.equal('new');
    });

    it('should return false when expected does not match current', async () => {
      await store.createKey('key', 'current');
      const result = await store.compareAndSet('key', 'wrong', 'new');
      expect(result).to.be.false;
      expect(await store.getKey('key')).to.equal('current');
    });

    it('should set value when key does not exist and expected is null', async () => {
      const result = await store.compareAndSet('new-key', null, 'value');
      expect(result).to.be.true;
      expect(await store.getKey('new-key')).to.equal('value');
    });

    it('should return false when key exists but expected is null', async () => {
      await store.createKey('key', 'value');
      const result = await store.compareAndSet('key', null, 'new');
      expect(result).to.be.false;
      expect(await store.getKey('key')).to.equal('value');
    });

    it('should return false when key does not exist but expected is not null', async () => {
      const result = await store.compareAndSet('missing', 'expected', 'new');
      expect(result).to.be.false;
    });
  });

  // ---- compareAndSet with objects -------------------------------
  describe('compareAndSet with object values', () => {
    let objStore: InMemoryKeyValueStore<{ name: string; count: number }>;

    beforeEach(() => {
      objStore = new InMemoryKeyValueStore<{ name: string; count: number }>();
    });

    it('should succeed when expected object matches current (deep equality)', async () => {
      await objStore.createKey('key', { name: 'test', count: 1 });
      const result = await objStore.compareAndSet(
        'key',
        { name: 'test', count: 1 },
        { name: 'test', count: 2 },
      );
      expect(result).to.be.true;
      const val = await objStore.getKey('key');
      expect(val).to.deep.equal({ name: 'test', count: 2 });
    });

    it('should fail when expected object does not match current', async () => {
      await objStore.createKey('key', { name: 'test', count: 1 });
      const result = await objStore.compareAndSet(
        'key',
        { name: 'wrong', count: 1 },
        { name: 'test', count: 2 },
      );
      expect(result).to.be.false;
    });
  });

  // ---- compareAndSet with JSON.stringify failure -----------------
  describe('compareAndSet when JSON.stringify fails', () => {
    it('should return false when JSON.stringify throws for object comparison', async () => {
      const objStore = new InMemoryKeyValueStore<any>();
      // Create a circular object that will cause JSON.stringify to throw
      const circular: any = { a: 1 };
      circular.self = circular;
      // Directly set the value in the store's internal map
      (objStore as any).store.set('key', circular);

      // Now try compareAndSet with a different object (not the same reference)
      // JSON.stringify will fail, falling back to strict equality (false)
      const differentObj: any = { a: 1, self: {} };
      const result = await objStore.compareAndSet('key', differentObj, { a: 2 });
      expect(result).to.be.false;
    });
  });

  // ---- healthCheck ----------------------------------------------
  describe('healthCheck', () => {
    it('should always return true', async () => {
      const result = await store.healthCheck();
      expect(result).to.be.true;
    });
  });

  // ---- publishCacheInvalidation ---------------------------------
  describe('publishCacheInvalidation', () => {
    it('should be a no-op and not throw', async () => {
      await store.publishCacheInvalidation('any-key'); // should not throw
    });
  });
});
