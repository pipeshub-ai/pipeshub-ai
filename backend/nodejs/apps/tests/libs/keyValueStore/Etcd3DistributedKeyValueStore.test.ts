import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Etcd3DistributedKeyValueStore } from '../../../src/libs/keyValueStore/providers/Etcd3DistributedKeyValueStore';
import { KeyAlreadyExistsError, KeyNotFoundError } from '../../../src/libs/errors/etcd.errors';
import { ConfigurationManagerStoreConfig } from '../../../src/modules/configuration_manager/config/config';

// ----------------------------------------------------------------
// Mock etcd3 client internals
// ----------------------------------------------------------------

class MockPutBuilder {
  value = sinon.stub().resolves();
}

class MockGetBuilder {
  private _bufferVal: Buffer | null = null;

  setBufferVal(val: Buffer | null) {
    this._bufferVal = val;
    return this;
  }

  buffer = sinon.stub().callsFake(() => Promise.resolve(this._bufferVal));
}

class MockDeleteBuilder {
  key = sinon.stub().resolves();
}

class MockGetAllBuilder {
  private _keys: string[] = [];
  private _prefixBuilder: MockGetAllBuilder | null = null;

  setKeys(keys: string[]) {
    this._keys = keys;
    return this;
  }

  keys = sinon.stub().callsFake(() => Promise.resolve(this._keys));

  prefix = sinon.stub().callsFake(() => {
    if (!this._prefixBuilder) {
      this._prefixBuilder = new MockGetAllBuilder();
    }
    return this._prefixBuilder;
  });

  getPrefixBuilder(): MockGetAllBuilder {
    if (!this._prefixBuilder) {
      this._prefixBuilder = new MockGetAllBuilder();
    }
    return this._prefixBuilder;
  }
}

class MockWatchBuilder {
  private _keyBuilder: MockWatchKeyBuilder | null = null;

  key = sinon.stub().callsFake(() => {
    if (!this._keyBuilder) {
      this._keyBuilder = new MockWatchKeyBuilder();
    }
    return this._keyBuilder;
  });

  getKeyBuilder(): MockWatchKeyBuilder {
    if (!this._keyBuilder) {
      this._keyBuilder = new MockWatchKeyBuilder();
    }
    return this._keyBuilder;
  }
}

class MockWatchKeyBuilder {
  private _watcher: MockWatcher | null = null;

  create = sinon.stub().callsFake(() => {
    if (!this._watcher) {
      this._watcher = new MockWatcher();
    }
    return Promise.resolve(this._watcher);
  });

  getWatcher(): MockWatcher {
    if (!this._watcher) {
      this._watcher = new MockWatcher();
    }
    return this._watcher;
  }
}

class MockWatcher {
  private handlers: Map<string, Function[]> = new Map();

  on(event: string, handler: Function) {
    if (!this.handlers.has(event)) {
      this.handlers.set(event, []);
    }
    this.handlers.get(event)!.push(handler);
    return this;
  }

  emit(event: string, ...args: any[]) {
    const handlers = this.handlers.get(event);
    if (handlers) {
      handlers.forEach((h) => h(...args));
    }
  }
}

class MockMaintenance {
  status = sinon.stub().resolves({});
}

describe('Etcd3DistributedKeyValueStore', () => {
  let store: Etcd3DistributedKeyValueStore<string>;
  let mockGetBuilder: MockGetBuilder;
  let mockPutBuilder: MockPutBuilder;
  let mockDeleteBuilder: MockDeleteBuilder;
  let mockGetAllBuilder: MockGetAllBuilder;
  let mockWatchBuilder: MockWatchBuilder;
  let mockMaintenance: MockMaintenance;
  let serializer: sinon.SinonStub;
  let deserializer: sinon.SinonStub;

  const config: ConfigurationManagerStoreConfig = {
    host: 'http://localhost',
    port: 2379,
    dialTimeout: 2000,
  };

  beforeEach(() => {
    serializer = sinon.stub().callsFake((v: string) => Buffer.from(v));
    deserializer = sinon.stub().callsFake((b: Buffer) => b.toString());

    store = new Etcd3DistributedKeyValueStore<string>(
      config,
      serializer,
      deserializer,
    );

    // Replace the internal etcd3 client with our mock
    mockGetBuilder = new MockGetBuilder();
    mockPutBuilder = new MockPutBuilder();
    mockDeleteBuilder = new MockDeleteBuilder();
    mockGetAllBuilder = new MockGetAllBuilder();
    mockWatchBuilder = new MockWatchBuilder();
    mockMaintenance = new MockMaintenance();

    const mockClient = {
      get: sinon.stub().returns(mockGetBuilder),
      put: sinon.stub().returns(mockPutBuilder),
      delete: sinon.stub().returns(mockDeleteBuilder),
      getAll: sinon.stub().returns(mockGetAllBuilder),
      watch: sinon.stub().returns(mockWatchBuilder),
      maintenance: mockMaintenance,
    };

    (store as any).client = mockClient;
  });

  afterEach(() => {
    sinon.restore();
  });

  // ---- constructor ----------------------------------------------
  describe('constructor', () => {
    it('should construct with host and port', () => {
      const s = new Etcd3DistributedKeyValueStore(config, serializer, deserializer);
      expect(s).to.exist;
    });

    it('should construct with host only (no port)', () => {
      const noPortConfig = { host: 'http://localhost', port: 0, dialTimeout: 2000 };
      const s = new Etcd3DistributedKeyValueStore(noPortConfig, serializer, deserializer);
      expect(s).to.exist;
    });
  });

  // ---- createKey ------------------------------------------------
  describe('createKey', () => {
    it('should create key when it does not exist', async () => {
      mockGetBuilder.setBufferVal(null);
      await store.createKey('key1', 'value1');
      expect(serializer.calledWith('value1')).to.be.true;
      expect(mockPutBuilder.value.calledOnce).to.be.true;
    });

    it('should throw KeyAlreadyExistsError when key exists', async () => {
      mockGetBuilder.setBufferVal(Buffer.from('existing'));
      try {
        await store.createKey('key1', 'value1');
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(KeyAlreadyExistsError);
        expect((error as Error).message).to.include('key1');
      }
    });
  });

  // ---- updateValue ----------------------------------------------
  describe('updateValue', () => {
    it('should update when key exists', async () => {
      mockGetBuilder.setBufferVal(Buffer.from('old'));
      await store.updateValue('key1', 'new');
      expect(serializer.calledWith('new')).to.be.true;
      expect(mockPutBuilder.value.calledOnce).to.be.true;
    });

    it('should throw KeyNotFoundError when key does not exist', async () => {
      mockGetBuilder.setBufferVal(null);
      try {
        await store.updateValue('missing', 'value');
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(KeyNotFoundError);
        expect((error as Error).message).to.include('missing');
      }
    });
  });

  // ---- getKey ---------------------------------------------------
  describe('getKey', () => {
    it('should return deserialized value when key exists', async () => {
      mockGetBuilder.setBufferVal(Buffer.from('hello'));
      const result = await store.getKey('key1');
      expect(result).to.equal('hello');
      expect(deserializer.calledOnce).to.be.true;
    });

    it('should return null when key does not exist', async () => {
      mockGetBuilder.setBufferVal(null);
      const result = await store.getKey('missing');
      expect(result).to.be.null;
      expect(deserializer.called).to.be.false;
    });
  });

  // ---- deleteKey ------------------------------------------------
  describe('deleteKey', () => {
    it('should delete the key', async () => {
      await store.deleteKey('key1');
      expect(mockDeleteBuilder.key.calledWith('key1')).to.be.true;
    });
  });

  // ---- getAllKeys ------------------------------------------------
  describe('getAllKeys', () => {
    it('should return all keys from etcd', async () => {
      mockGetAllBuilder.setKeys(['k1', 'k2', 'k3']);
      const keys = await store.getAllKeys();
      expect(keys).to.deep.equal(['k1', 'k2', 'k3']);
    });

    it('should return empty array when no keys exist', async () => {
      mockGetAllBuilder.setKeys([]);
      const keys = await store.getAllKeys();
      expect(keys).to.deep.equal([]);
    });
  });

  // ---- watchKey -------------------------------------------------
  describe('watchKey', () => {
    it('should set up a watcher and call callback on put events', async () => {
      const callback = sinon.stub();
      await store.watchKey('watched', callback);

      // Simulate a put event on the watcher
      const watcher = mockWatchBuilder.getKeyBuilder().getWatcher();
      watcher.emit('put', { value: Buffer.from('new-value') });

      expect(callback.calledOnce).to.be.true;
      expect(deserializer.called).to.be.true;
    });

    it('should call callback with null on delete events', async () => {
      const callback = sinon.stub();
      await store.watchKey('watched', callback);

      const watcher = mockWatchBuilder.getKeyBuilder().getWatcher();
      watcher.emit('delete');

      expect(callback.calledOnce).to.be.true;
      expect(callback.firstCall.args[0]).to.be.null;
    });
  });

  // ---- listKeysInDirectory --------------------------------------
  describe('listKeysInDirectory', () => {
    it('should return keys with the given prefix', async () => {
      const prefixBuilder = mockGetAllBuilder.getPrefixBuilder();
      prefixBuilder.setKeys(['dir/a', 'dir/b']);

      const keys = await store.listKeysInDirectory('dir/');
      expect(keys).to.deep.equal(['dir/a', 'dir/b']);
      expect(mockGetAllBuilder.prefix.calledWith('dir/')).to.be.true;
    });
  });

  // ---- compareAndSet --------------------------------------------
  describe('compareAndSet', () => {
    it('should succeed when current value matches expected', async () => {
      // First get returns current value, second get returns updated value
      const getStub = sinon.stub();
      getStub.onFirstCall().returns({
        buffer: sinon.stub().resolves(Buffer.from('current')),
      });
      getStub.onSecondCall().returns({
        buffer: sinon.stub().resolves(Buffer.from('new-val')),
      });
      (store as any).client.get = getStub;

      const result = await store.compareAndSet('key', 'current', 'new-val');
      expect(result).to.be.true;
    });

    it('should fail when current value does not match expected', async () => {
      (store as any).client.get = sinon.stub().returns({
        buffer: sinon.stub().resolves(Buffer.from('different')),
      });

      const result = await store.compareAndSet('key', 'expected', 'new-val');
      expect(result).to.be.false;
    });

    it('should succeed when both expected and current are null', async () => {
      const getStub = sinon.stub();
      getStub.onFirstCall().returns({
        buffer: sinon.stub().resolves(null),
      });
      getStub.onSecondCall().returns({
        buffer: sinon.stub().resolves(Buffer.from('new-val')),
      });
      (store as any).client.get = getStub;

      const result = await store.compareAndSet('key', null, 'new-val');
      expect(result).to.be.true;
    });

    it('should fail when expected is null but current exists', async () => {
      (store as any).client.get = sinon.stub().returns({
        buffer: sinon.stub().resolves(Buffer.from('exists')),
      });

      const result = await store.compareAndSet('key', null, 'new-val');
      expect(result).to.be.false;
    });

    it('should fail when expected is set but current is null', async () => {
      (store as any).client.get = sinon.stub().returns({
        buffer: sinon.stub().resolves(null),
      });

      const result = await store.compareAndSet('key', 'expected', 'new-val');
      expect(result).to.be.false;
    });

    it('should return false on error', async () => {
      (store as any).client.get = sinon.stub().throws(new Error('etcd down'));

      const result = await store.compareAndSet('key', 'old', 'new');
      expect(result).to.be.false;
    });
  });

  // ---- healthCheck ----------------------------------------------
  describe('healthCheck', () => {
    it('should return true when maintenance status succeeds', async () => {
      mockMaintenance.status.resolves({});
      const result = await store.healthCheck();
      expect(result).to.be.true;
    });

    it('should return false when maintenance status fails', async () => {
      mockMaintenance.status.rejects(new Error('etcd down'));
      const result = await store.healthCheck();
      expect(result).to.be.false;
    });
  });

  // ---- publishCacheInvalidation ---------------------------------
  describe('publishCacheInvalidation', () => {
    it('should log a warning (not implemented for etcd)', async () => {
      // Should not throw
      await store.publishCacheInvalidation('some-key');
    });
  });
});
