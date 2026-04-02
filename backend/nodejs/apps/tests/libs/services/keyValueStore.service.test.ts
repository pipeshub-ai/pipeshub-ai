import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { createMockLogger } from '../../helpers/mock-logger';

// We import the service and its dependencies, but will mock the factory and store.
import { KeyValueStoreService } from '../../../src/libs/services/keyValueStore.service';
import { KeyValueStoreFactory } from '../../../src/libs/keyValueStore/keyValueStoreFactory';
import { ConfigurationManagerConfig } from '../../../src/modules/configuration_manager/config/config';

// ----------------------------------------------------------------
// Mock store that implements DistributedKeyValueStore
// ----------------------------------------------------------------
function createMockStore(): any {
  return {
    createKey: sinon.stub().resolves(),
    updateValue: sinon.stub().resolves(),
    getKey: sinon.stub().resolves(null),
    deleteKey: sinon.stub().resolves(),
    getAllKeys: sinon.stub().resolves([]),
    watchKey: sinon.stub().resolves(),
    listKeysInDirectory: sinon.stub().resolves([]),
    compareAndSet: sinon.stub().resolves(true),
    healthCheck: sinon.stub().resolves(true),
    publishCacheInvalidation: sinon.stub().resolves(),
  };
}

describe('KeyValueStoreService', () => {
  let service: KeyValueStoreService;
  let mockStore: any;
  let factoryStub: sinon.SinonStub;

  const mockConfig: ConfigurationManagerConfig = {
    storeType: 'etcd3',
    storeConfig: {
      host: 'http://localhost',
      port: 2379,
      dialTimeout: 2000,
    },
    redisConfig: {
      host: 'localhost',
      port: 6379,
      db: 0,
      keyPrefix: 'pipeshub:kv:',
    },
    secretKey: 'test-secret-key',
    algorithm: 'aes-256-gcm',
  };

  beforeEach(() => {
    // Reset the singleton so each test gets a fresh instance
    (KeyValueStoreService as any).instance = undefined;

    mockStore = createMockStore();

    // Stub the factory to return our mock store
    factoryStub = sinon.stub(KeyValueStoreFactory, 'createStore').returns(mockStore as any);

    // Stub Logger.getInstance to return our mock logger
    const mockLogger = createMockLogger();
    sinon.stub(require('../../../src/libs/services/logger.service').Logger, 'getInstance').returns(mockLogger);

    service = KeyValueStoreService.getInstance(mockConfig);
  });

  afterEach(() => {
    sinon.restore();
  });

  // ---- Singleton pattern ----------------------------------------
  describe('getInstance', () => {
    it('should return the same instance on repeated calls', () => {
      const instance1 = KeyValueStoreService.getInstance(mockConfig);
      const instance2 = KeyValueStoreService.getInstance(mockConfig);
      expect(instance1).to.equal(instance2);
    });
  });

  // ---- connect --------------------------------------------------
  describe('connect', () => {
    it('should create a store via factory and verify health', async () => {
      await service.connect();
      expect(factoryStub.calledOnce).to.be.true;
      expect(mockStore.healthCheck.calledOnce).to.be.true;
      expect(service.isConnected()).to.be.true;
    });

    it('should not reconnect if already connected', async () => {
      await service.connect();
      await service.connect();
      // Factory should have been called only once
      expect(factoryStub.calledOnce).to.be.true;
    });

    it('should throw when health check fails on all retries', async () => {
      mockStore.healthCheck.resolves(false);
      // Override sleep to avoid waiting
      (service as any).sleep = sinon.stub().resolves();

      try {
        await service.connect();
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(Error);
        expect((error as Error).message).to.include('health check failed');
      }
      expect(service.isConnected()).to.be.false;
    });

    it('should retry on factory creation error', async () => {
      // Fail first call, succeed second
      factoryStub.onFirstCall().throws(new Error('etcd unavailable'));
      const freshStore = createMockStore();
      factoryStub.onSecondCall().returns(freshStore as any);
      freshStore.healthCheck.resolves(true);

      // Override sleep to avoid waiting
      (service as any).sleep = sinon.stub().resolves();

      await service.connect();
      expect(factoryStub.callCount).to.equal(2);
      expect(service.isConnected()).to.be.true;
    });

    it('should use redis config when store type is redis', async () => {
      // Reset singleton
      (KeyValueStoreService as any).instance = undefined;
      const redisConfig: ConfigurationManagerConfig = {
        ...mockConfig,
        storeType: 'redis',
      };
      const redisSvc = KeyValueStoreService.getInstance(redisConfig);
      // Override sleep to avoid waiting
      (redisSvc as any).sleep = sinon.stub().resolves();

      await redisSvc.connect();
      expect(factoryStub.calledOnce).to.be.true;
    });
  });

  // ---- disconnect -----------------------------------------------
  describe('disconnect', () => {
    it('should set isInitialized to false', async () => {
      await service.connect();
      expect(service.isConnected()).to.be.true;
      await service.disconnect();
      expect(service.isConnected()).to.be.false;
    });
  });

  // ---- isConnected ----------------------------------------------
  describe('isConnected', () => {
    it('should return false before connect', () => {
      expect(service.isConnected()).to.be.false;
    });

    it('should return true after connect', async () => {
      await service.connect();
      expect(service.isConnected()).to.be.true;
    });
  });

  // ---- healthCheck ----------------------------------------------
  describe('healthCheck', () => {
    it('should return false when not initialized', async () => {
      const result = await service.healthCheck();
      expect(result).to.be.false;
    });

    it('should delegate to store.healthCheck when initialized', async () => {
      await service.connect();
      mockStore.healthCheck.resolves(true);
      const result = await service.healthCheck();
      expect(result).to.be.true;
    });

    it('should return false when store health check throws', async () => {
      await service.connect();
      mockStore.healthCheck.rejects(new Error('store down'));
      const result = await service.healthCheck();
      expect(result).to.be.false;
    });
  });

  // ---- set ------------------------------------------------------
  describe('set', () => {
    beforeEach(async () => {
      await service.connect();
    });

    it('should create a new key via store.createKey', async () => {
      await service.set('my/key', 'value');
      expect(mockStore.createKey.calledWith('my/key', 'value')).to.be.true;
    });

    it('should fall back to updateValue when key already exists', async () => {
      mockStore.createKey.rejects(new Error('Key "my/key" already exists'));
      await service.set('my/key', 'updated-value');
      expect(mockStore.updateValue.calledWith('my/key', 'updated-value')).to.be.true;
    });

    it('should throw when createKey fails with non-exists error', async () => {
      mockStore.createKey.rejects(new Error('Connection lost'));
      try {
        await service.set('key', 'val');
        expect.fail('Should have thrown');
      } catch (error) {
        expect((error as Error).message).to.equal('Connection lost');
      }
    });

    it('should publish cache invalidation after set', async () => {
      await service.set('key', 'value');
      expect(mockStore.publishCacheInvalidation.calledWith('key')).to.be.true;
    });

    it('should publish cache invalidation after update fallback', async () => {
      mockStore.createKey.rejects(new Error('already exists'));
      await service.set('key', 'value');
      expect(mockStore.publishCacheInvalidation.calledWith('key')).to.be.true;
    });
  });

  // ---- get ------------------------------------------------------
  describe('get', () => {
    beforeEach(async () => {
      await service.connect();
    });

    it('should return value from store.getKey', async () => {
      mockStore.getKey.resolves('stored-value');
      const result = await service.get('my/key');
      expect(result).to.equal('stored-value');
      expect(mockStore.getKey.calledWith('my/key')).to.be.true;
    });

    it('should return null when key is not found', async () => {
      mockStore.getKey.resolves(null);
      const result = await service.get('missing-key');
      expect(result).to.be.null;
    });
  });

  // ---- delete ---------------------------------------------------
  describe('delete', () => {
    beforeEach(async () => {
      await service.connect();
    });

    it('should delete the key from store', async () => {
      await service.delete('my/key');
      expect(mockStore.deleteKey.calledWith('my/key')).to.be.true;
    });

    it('should publish cache invalidation after delete', async () => {
      await service.delete('my/key');
      expect(mockStore.publishCacheInvalidation.calledWith('my/key')).to.be.true;
    });
  });

  // ---- listKeysInDirectory --------------------------------------
  describe('listKeysInDirectory', () => {
    beforeEach(async () => {
      await service.connect();
    });

    it('should return keys from store.listKeysInDirectory', async () => {
      mockStore.listKeysInDirectory.resolves(['dir/a', 'dir/b']);
      const result = await service.listKeysInDirectory('dir/');
      expect(result).to.deep.equal(['dir/a', 'dir/b']);
      expect(mockStore.listKeysInDirectory.calledWith('dir/')).to.be.true;
    });
  });

  // ---- watchKey -------------------------------------------------
  describe('watchKey', () => {
    beforeEach(async () => {
      await service.connect();
    });

    it('should delegate to store.watchKey', async () => {
      const cb = sinon.stub();
      await service.watchKey('watched-key', cb);
      expect(mockStore.watchKey.calledWith('watched-key', cb)).to.be.true;
    });
  });

  // ---- getAllKeys ------------------------------------------------
  describe('getAllKeys', () => {
    beforeEach(async () => {
      await service.connect();
    });

    it('should return all keys from store', async () => {
      mockStore.getAllKeys.resolves(['k1', 'k2', 'k3']);
      const result = await service.getAllKeys();
      expect(result).to.deep.equal(['k1', 'k2', 'k3']);
    });
  });

  // ---- compareAndSet --------------------------------------------
  describe('compareAndSet', () => {
    beforeEach(async () => {
      await service.connect();
    });

    it('should return true and publish cache invalidation on success', async () => {
      mockStore.compareAndSet.resolves(true);
      const result = await service.compareAndSet('key', 'old', 'new');
      expect(result).to.be.true;
      expect(mockStore.compareAndSet.calledWith('key', 'old', 'new')).to.be.true;
      expect(mockStore.publishCacheInvalidation.calledWith('key')).to.be.true;
    });

    it('should return false and not publish cache invalidation on failure', async () => {
      mockStore.compareAndSet.resolves(false);
      const result = await service.compareAndSet('key', 'wrong', 'new');
      expect(result).to.be.false;
      expect(mockStore.publishCacheInvalidation.called).to.be.false;
    });

    it('should handle null expected value', async () => {
      mockStore.compareAndSet.resolves(true);
      const result = await service.compareAndSet('key', null, 'new');
      expect(result).to.be.true;
      expect(mockStore.compareAndSet.calledWith('key', null, 'new')).to.be.true;
    });
  });

  // ---- sleep (private helper) ------------------------------------
  describe('sleep (private)', () => {
    it('should resolve after the specified delay', async () => {
      const sleepFn = (service as any).sleep.bind(service);
      // Call with a very short delay
      await sleepFn(1);
      // If we reach here, sleep resolved successfully
      expect(true).to.be.true;
    });
  });

  // ---- ensureConnection -----------------------------------------
  describe('ensureConnection (via operations)', () => {
    it('should auto-connect on get when not connected', async () => {
      mockStore.getKey.resolves('val');
      const result = await service.get('key');
      expect(result).to.equal('val');
      expect(service.isConnected()).to.be.true;
    });

    it('should auto-connect on set when not connected', async () => {
      await service.set('key', 'val');
      expect(service.isConnected()).to.be.true;
    });

    it('should auto-connect on delete when not connected', async () => {
      await service.delete('key');
      expect(service.isConnected()).to.be.true;
    });
  });
});
