import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { RedisDistributedKeyValueStore, RedisStoreConfig } from '../../../src/libs/keyValueStore/providers/RedisDistributedKeyValueStore';
import { KeyAlreadyExistsError, KeyNotFoundError } from '../../../src/libs/errors/etcd.errors';

// ----------------------------------------------------------------
// Mock Redis client
// ----------------------------------------------------------------
class MockRedisClient {
  set = sinon.stub();
  getBuffer = sinon.stub();
  del = sinon.stub();
  scan = sinon.stub();
  watch = sinon.stub().resolves();
  unwatch = sinon.stub().resolves();
  multi = sinon.stub();
  ping = sinon.stub();
  quit = sinon.stub().resolves();
  publish = sinon.stub().resolves();
}

class MockMulti {
  set = sinon.stub().returnsThis();
  exec = sinon.stub();
}

describe('RedisDistributedKeyValueStore', () => {
  let store: RedisDistributedKeyValueStore<string>;
  let mockClient: MockRedisClient;
  let serializer: sinon.SinonStub;
  let deserializer: sinon.SinonStub;

  const config: RedisStoreConfig = {
    host: 'localhost',
    port: 6379,
    password: 'secret',
    db: 0,
    keyPrefix: 'test:kv:',
  };

  beforeEach(() => {
    serializer = sinon.stub().callsFake((v: string) => Buffer.from(v));
    deserializer = sinon.stub().callsFake((b: Buffer) => b.toString());

    store = new RedisDistributedKeyValueStore<string>(config, serializer, deserializer);

    // Replace the internal Redis client with our mock
    mockClient = new MockRedisClient();
    (store as any).client = mockClient;
  });

  afterEach(() => {
    sinon.restore();
  });

  // ---- constructor / key helpers --------------------------------
  describe('constructor and key helpers', () => {
    it('should use provided keyPrefix', () => {
      expect((store as any).keyPrefix).to.equal('test:kv:');
    });

    it('should default keyPrefix to pipeshub:kv: when not provided', () => {
      const noPrefix: RedisStoreConfig = { host: 'localhost', port: 6379 };
      const s = new RedisDistributedKeyValueStore(noPrefix, serializer, deserializer);
      expect((s as any).keyPrefix).to.equal('pipeshub:kv:');
    });

    it('should build full key with prefix', () => {
      const result = (store as any).buildKey('mykey');
      expect(result).to.equal('test:kv:mykey');
    });

    it('should strip prefix from full key', () => {
      const result = (store as any).stripPrefix('test:kv:mykey');
      expect(result).to.equal('mykey');
    });

    it('should return key unchanged if it does not start with prefix', () => {
      const result = (store as any).stripPrefix('other:mykey');
      expect(result).to.equal('other:mykey');
    });
  });

  // ---- createKey ------------------------------------------------
  describe('createKey', () => {
    it('should create key with NX flag (set-if-not-exists)', async () => {
      mockClient.set.resolves('OK');
      await store.createKey('key1', 'value1');
      expect(mockClient.set.calledOnce).to.be.true;
      const args = mockClient.set.firstCall.args;
      expect(args[0]).to.equal('test:kv:key1');
      expect(args[2]).to.equal('NX');
      expect(serializer.calledWith('value1')).to.be.true;
    });

    it('should throw KeyAlreadyExistsError when NX returns null', async () => {
      mockClient.set.resolves(null);
      try {
        await store.createKey('key1', 'value1');
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(KeyAlreadyExistsError);
        expect((error as Error).message).to.include('already exists');
      }
    });

    it('should notify watchers on successful create', async () => {
      mockClient.set.resolves('OK');
      const callback = sinon.stub();
      await store.watchKey('key1', callback);
      await store.createKey('key1', 'value1');
      expect(callback.calledOnce).to.be.true;
      expect(callback.firstCall.args[0]).to.equal('value1');
    });
  });

  // ---- updateValue ----------------------------------------------
  describe('updateValue', () => {
    it('should update key with XX flag (set-if-exists)', async () => {
      mockClient.set.resolves('OK');
      await store.updateValue('key1', 'updated');
      expect(mockClient.set.calledOnce).to.be.true;
      const args = mockClient.set.firstCall.args;
      expect(args[0]).to.equal('test:kv:key1');
      expect(args[2]).to.equal('XX');
      expect(serializer.calledWith('updated')).to.be.true;
    });

    it('should throw KeyNotFoundError when XX returns null', async () => {
      mockClient.set.resolves(null);
      try {
        await store.updateValue('missing', 'value');
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(KeyNotFoundError);
        expect((error as Error).message).to.include('missing');
      }
    });

    it('should notify watchers on successful update', async () => {
      mockClient.set.resolves('OK');
      const callback = sinon.stub();
      await store.watchKey('key1', callback);
      await store.updateValue('key1', 'updated');
      expect(callback.calledOnce).to.be.true;
      expect(callback.firstCall.args[0]).to.equal('updated');
    });
  });

  // ---- getKey ---------------------------------------------------
  describe('getKey', () => {
    it('should return deserialized value when key exists', async () => {
      mockClient.getBuffer.resolves(Buffer.from('hello'));
      const result = await store.getKey('key1');
      expect(result).to.equal('hello');
      expect(mockClient.getBuffer.calledWith('test:kv:key1')).to.be.true;
      expect(deserializer.calledOnce).to.be.true;
    });

    it('should return null when key does not exist', async () => {
      mockClient.getBuffer.resolves(null);
      const result = await store.getKey('missing');
      expect(result).to.be.null;
      expect(deserializer.called).to.be.false;
    });
  });

  // ---- deleteKey ------------------------------------------------
  describe('deleteKey', () => {
    it('should delete the key from redis', async () => {
      mockClient.del.resolves(1);
      await store.deleteKey('key1');
      expect(mockClient.del.calledWith('test:kv:key1')).to.be.true;
    });

    it('should notify watchers with null on delete', async () => {
      mockClient.del.resolves(1);
      const callback = sinon.stub();
      await store.watchKey('key1', callback);
      await store.deleteKey('key1');
      expect(callback.calledOnce).to.be.true;
      expect(callback.firstCall.args[0]).to.be.null;
    });
  });

  // ---- getAllKeys ------------------------------------------------
  describe('getAllKeys', () => {
    it('should scan and return stripped keys', async () => {
      mockClient.scan
        .onFirstCall().resolves(['0', ['test:kv:k1', 'test:kv:k2']]);

      const keys = await store.getAllKeys();
      expect(keys).to.deep.equal(['k1', 'k2']);
      expect(mockClient.scan.calledOnce).to.be.true;
    });

    it('should handle multiple scan iterations', async () => {
      mockClient.scan
        .onFirstCall().resolves(['1', ['test:kv:k1']])
        .onSecondCall().resolves(['0', ['test:kv:k2']]);

      const keys = await store.getAllKeys();
      expect(keys).to.deep.equal(['k1', 'k2']);
      expect(mockClient.scan.callCount).to.equal(2);
    });

    it('should return empty array when no keys exist', async () => {
      mockClient.scan.resolves(['0', []]);
      const keys = await store.getAllKeys();
      expect(keys).to.deep.equal([]);
    });
  });

  // ---- watchKey -------------------------------------------------
  describe('watchKey', () => {
    it('should register a callback for the key', async () => {
      const cb = sinon.stub();
      await store.watchKey('mykey', cb);
      expect((store as any).watchers.has('mykey')).to.be.true;
      expect((store as any).watchers.get('mykey')).to.have.length(1);
    });

    it('should allow multiple watchers for the same key', async () => {
      const cb1 = sinon.stub();
      const cb2 = sinon.stub();
      await store.watchKey('mykey', cb1);
      await store.watchKey('mykey', cb2);
      expect((store as any).watchers.get('mykey')).to.have.length(2);
    });

    it('should invoke all watchers when value changes', async () => {
      const cb1 = sinon.stub();
      const cb2 = sinon.stub();
      await store.watchKey('mykey', cb1);
      await store.watchKey('mykey', cb2);

      mockClient.set.resolves('OK');
      await store.createKey('mykey', 'val');

      expect(cb1.calledOnce).to.be.true;
      expect(cb2.calledOnce).to.be.true;
    });

    it('should not break other watchers if one throws', async () => {
      const cb1 = sinon.stub().throws(new Error('watcher error'));
      const cb2 = sinon.stub();
      await store.watchKey('mykey', cb1);
      await store.watchKey('mykey', cb2);

      mockClient.set.resolves('OK');
      await store.createKey('mykey', 'val');

      // Both should be called; cb2 should still execute despite cb1 throwing
      expect(cb1.calledOnce).to.be.true;
      expect(cb2.calledOnce).to.be.true;
    });
  });

  // ---- listKeysInDirectory --------------------------------------
  describe('listKeysInDirectory', () => {
    it('should scan with prefix pattern and strip prefix', async () => {
      mockClient.scan.resolves(['0', ['test:kv:dir/a', 'test:kv:dir/b']]);
      const keys = await store.listKeysInDirectory('dir/');
      expect(keys).to.deep.equal(['dir/a', 'dir/b']);
    });

    it('should append trailing slash if missing', async () => {
      mockClient.scan.resolves(['0', []]);
      await store.listKeysInDirectory('dir');
      const pattern = mockClient.scan.firstCall.args[2]; // MATCH argument
      expect(pattern).to.equal('test:kv:dir/*');
    });

    it('should not double-append trailing slash', async () => {
      mockClient.scan.resolves(['0', []]);
      await store.listKeysInDirectory('dir/');
      const pattern = mockClient.scan.firstCall.args[2]; // MATCH argument
      expect(pattern).to.equal('test:kv:dir/*');
    });

    it('should handle multiple scan iterations', async () => {
      mockClient.scan
        .onFirstCall().resolves(['5', ['test:kv:dir/a']])
        .onSecondCall().resolves(['0', ['test:kv:dir/b']]);

      const keys = await store.listKeysInDirectory('dir/');
      expect(keys).to.deep.equal(['dir/a', 'dir/b']);
    });
  });

  // ---- compareAndSet --------------------------------------------
  describe('compareAndSet', () => {
    let mockMulti: MockMulti;

    beforeEach(() => {
      mockMulti = new MockMulti();
      mockClient.multi.returns(mockMulti);
    });

    it('should succeed when expected matches current value', async () => {
      mockClient.getBuffer.resolves(Buffer.from('current'));
      mockMulti.exec.resolves([['OK']]);

      const result = await store.compareAndSet('key', 'current', 'new-val');
      expect(result).to.be.true;
      expect(mockClient.watch.calledWith('test:kv:key')).to.be.true;
    });

    it('should fail when expected does not match current value', async () => {
      mockClient.getBuffer.resolves(Buffer.from('different'));

      const result = await store.compareAndSet('key', 'expected', 'new-val');
      expect(result).to.be.false;
      expect(mockClient.unwatch.calledOnce).to.be.true;
    });

    it('should succeed when both expected and current are null', async () => {
      mockClient.getBuffer.resolves(null);
      mockMulti.exec.resolves([['OK']]);

      const result = await store.compareAndSet('key', null, 'new-val');
      expect(result).to.be.true;
    });

    it('should fail when expected is null but current exists', async () => {
      mockClient.getBuffer.resolves(Buffer.from('exists'));

      const result = await store.compareAndSet('key', null, 'new-val');
      expect(result).to.be.false;
    });

    it('should fail when expected is set but current is null', async () => {
      mockClient.getBuffer.resolves(null);

      const result = await store.compareAndSet('key', 'expected', 'new-val');
      expect(result).to.be.false;
    });

    it('should return false when transaction is aborted (result is null)', async () => {
      mockClient.getBuffer.resolves(Buffer.from('current'));
      mockMulti.exec.resolves(null);

      const result = await store.compareAndSet('key', 'current', 'new-val');
      expect(result).to.be.false;
    });

    it('should notify watchers on successful CAS', async () => {
      mockClient.getBuffer.resolves(Buffer.from('current'));
      mockMulti.exec.resolves([['OK']]);

      const callback = sinon.stub();
      await store.watchKey('key', callback);

      await store.compareAndSet('key', 'current', 'new-val');
      expect(callback.calledOnce).to.be.true;
      expect(callback.firstCall.args[0]).to.equal('new-val');
    });

    it('should return false on error', async () => {
      mockClient.watch.rejects(new Error('redis down'));

      const result = await store.compareAndSet('key', 'old', 'new');
      expect(result).to.be.false;
    });
  });

  // ---- publishCacheInvalidation ---------------------------------
  describe('publishCacheInvalidation', () => {
    it('should publish to the cache invalidation channel', async () => {
      mockClient.publish.resolves(1);
      await store.publishCacheInvalidation('some-key');
      expect(mockClient.publish.calledOnce).to.be.true;
      expect(mockClient.publish.firstCall.args[0]).to.equal('pipeshub:cache:invalidate');
      expect(mockClient.publish.firstCall.args[1]).to.equal('some-key');
    });

    it('should not throw when publish fails', async () => {
      mockClient.publish.rejects(new Error('publish failed'));
      await store.publishCacheInvalidation('key'); // should not throw
    });
  });

  // ---- disconnect -----------------------------------------------
  describe('disconnect', () => {
    it('should clear watchers and quit client', async () => {
      const cb = sinon.stub();
      await store.watchKey('key', cb);
      expect((store as any).watchers.size).to.equal(1);

      await store.disconnect();
      expect((store as any).watchers.size).to.equal(0);
      expect(mockClient.quit.calledOnce).to.be.true;
    });
  });

  // ---- healthCheck ----------------------------------------------
  describe('healthCheck', () => {
    it('should return true when ping returns PONG', async () => {
      mockClient.ping.resolves('PONG');
      const result = await store.healthCheck();
      expect(result).to.be.true;
    });

    it('should return false when ping does not return PONG', async () => {
      mockClient.ping.resolves('ERROR');
      const result = await store.healthCheck();
      expect(result).to.be.false;
    });

    it('should return false when ping throws', async () => {
      mockClient.ping.rejects(new Error('connection lost'));
      const result = await store.healthCheck();
      expect(result).to.be.false;
    });
  });
});
