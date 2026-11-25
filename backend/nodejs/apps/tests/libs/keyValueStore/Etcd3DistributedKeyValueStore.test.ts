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

  // ---- compareAndSet (UX2 LC-6: one etcd transaction) -----------
  describe('compareAndSet', () => {
    /** etcd as a transaction sees it: the comparison and the put are applied together, or not at all. */
    const etcd = (initial: string | null) => {
      const state = { value: initial === null ? null : Buffer.from(initial), puts: 0, keptLease: false };
      const client = (store as any).client;
      client.get = sinon.stub().throws(new Error('a compare-and-set must not read first'));
      client.put = (key: string) => ({
        value: (buffer: Buffer) => {
          const op = { op: 'put', key, buffer, keepsLease: false, ignoreLease: () => ({ ...op, keepsLease: true }) };
          return op;
        },
      });
      client.if = (_key: string, column: string, cmp: string, expected: Buffer | number) => {
        const ops: Array<{ buffer: Buffer; keepsLease?: boolean }> = [];
        const txn = {
          then: (...clauses: Array<{ buffer: Buffer; keepsLease?: boolean }>) => {
            ops.push(...clauses);
            return txn;
          },
          commit: async () => {
            const holds =
              cmp === '==' &&
              (column === 'Create'
                ? state.value === null && expected === 0
                : state.value !== null && Buffer.isBuffer(expected) && state.value.equals(expected));
            if (holds) {
              for (const op of ops) {
                state.value = op.buffer;
                state.puts += 1;
                state.keptLease = op.keepsLease === true;
              }
            }
            return { succeeded: holds };
          },
        };
        return txn;
      };
      return state;
    };

    it('writes when the stored value is the expected one, keeping the key\'s lease', async () => {
      const state = etcd('current');
      expect(await store.compareAndSet('key', 'current', 'new-val')).to.be.true;
      expect(state.value?.toString()).to.equal('new-val');
      expect(state.keptLease).to.equal(true);
    });

    it('LC-6: a write made since the caller read makes it fail, and nothing is overwritten', async () => {
      const state = etcd('read-by-caller');
      state.value = Buffer.from('written-by-someone-else');
      expect(await store.compareAndSet('key', 'read-by-caller', 'mine')).to.be.false;
      expect(state.value?.toString()).to.equal('written-by-someone-else');
      expect(state.puts).to.equal(0);
    });

    it('creates only when the key is absent', async () => {
      const absent = etcd(null);
      expect(await store.compareAndSet('key', null, 'new-val')).to.be.true;
      expect(absent.value?.toString()).to.equal('new-val');
      const present = etcd('exists');
      expect(await store.compareAndSet('key', null, 'new-val')).to.be.false;
      expect(present.value?.toString()).to.equal('exists');
    });

    it('fails when a value is expected and the key is absent', async () => {
      etcd(null);
      expect(await store.compareAndSet('key', 'expected', 'new-val')).to.be.false;
    });

    it('returns false when etcd fails', async () => {
      (store as any).client.if = () => {
        throw new Error('etcd down');
      };
      expect(await store.compareAndSet('key', 'old', 'new')).to.be.false;
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
