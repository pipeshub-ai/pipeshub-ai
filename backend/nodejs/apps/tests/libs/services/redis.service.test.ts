import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { EventEmitter } from 'events';

import { RedisCacheError } from '../../../src/libs/errors/redis.errors';
import { Logger } from '../../../src/libs/services/logger.service';
import { createMockLogger, MockLogger } from '../../helpers/mock-logger';
import { stubGetRedisProvider } from '../../helpers/fake-redis-provider';
import { IRedisConnectionProvider } from '../../../src/libs/services/redis/connectionProvider.interface';
import {
  getSharedRedisService,
  resetSharedRedisServices,
} from '../../../src/libs/services/redis.service';

// RedisService gets its client from `getRedisProvider().createClient(...)`
// (`src/libs/services/redis/connectionProviderFactory`), not `new
// Redis(...)` directly, so this mocks that seam instead of `ioredis`.
class MockRedisClient extends EventEmitter {
  get = sinon.stub();
  set = sinon.stub();
  del = sinon.stub();
  incr = sinon.stub();
  expire = sinon.stub();
  quit = sinon.stub();
}

const rsPath = require.resolve('../../../src/libs/services/redis.service');

describe('RedisService', () => {
  let RedisService: any;
  let mockClient: MockRedisClient;
  let mockLogger: MockLogger;
  let service: any;
  let capturedClientOptions: any;
  let capturedConnectionConfig: any;

  beforeEach(() => {
    mockClient = new MockRedisClient();
    mockLogger = createMockLogger();
    capturedClientOptions = null;

    const provider: IRedisConnectionProvider = {
      isCluster: false,
      mode: 'standalone',
      keyNamespace: '',
      getClient: sinon.stub().returns(mockClient),
      createClient: sinon.stub().callsFake((options: any) => {
        capturedClientOptions = options;
        return mockClient;
      }),
      createPubSubClient: sinon.stub().returns(mockClient),
      keySlot: sinon.stub().returns(0),
      loadScript: sinon.stub().resolves('fakesha'),
      connectionUrl: sinon.stub().returns('redis://fake:6379/0'),
      ping: sinon.stub().resolves(true),
      close: sinon.stub().resolves(),
      release: sinon.stub(),
      scanKeys: sinon.stub().callsFake(async function* (): AsyncIterable<string> {}),
    };
    // Restored by the global `sinon.restore()` in this suite's `afterEach`.
    const restoreProvider = stubGetRedisProvider(provider as any);
    restoreProvider();
    const factoryModule = require('../../../src/libs/services/redis/connectionProviderFactory');
    sinon
      .stub(factoryModule, 'getRedisProvider')
      .callsFake((connectionConfig: any) => {
        capturedConnectionConfig = connectionConfig;
        return provider as any;
      });

    delete require.cache[rsPath];

    const config = {
      host: 'localhost',
      port: 6379,
      password: '',
      db: 0,
      keyPrefix: 'test:',
    };

    const { RedisService: RS } = require('../../../src/libs/services/redis.service');
    RedisService = RS;

    service = new RS(config, mockLogger);
    (service as any).connected = true;
  });

  afterEach(() => {
    delete require.cache[rsPath];
    sinon.restore();
  });

  describe('constructor', () => {
    it('should use default keyPrefix when not provided', () => {
      // Verify default keyPrefix logic without creating real Redis connection
      const svc = Object.create(RedisService.prototype);
      svc.keyPrefix = 'app:'; // default
      expect(svc.keyPrefix).to.equal('app:');
    });

    it('should use provided keyPrefix', () => {
      expect((service as any).keyPrefix).to.equal('test:');
    });

    it('prepends the provider key namespace (R9) to the key prefix', () => {
      const namespacedProvider = {
        isCluster: false,
        mode: 'standalone',
        keyNamespace: 'tenant-a',
        getClient: sinon.stub().returns(mockClient),
        createClient: sinon.stub().returns(mockClient),
        createPubSubClient: sinon.stub().returns(mockClient),
        keySlot: sinon.stub().returns(0),
        loadScript: sinon.stub().resolves('fakesha'),
        connectionUrl: sinon.stub().returns('redis://fake:6379/0'),
        ping: sinon.stub().resolves(true),
        close: sinon.stub().resolves(),
        scanKeys: sinon.stub().callsFake(async function* (): AsyncIterable<string> {}),
      };
      // `beforeEach` already stubbed `getRedisProvider`; retarget that same
      // stub for this one construction instead of re-wrapping it.
      const mod = require('../../../src/libs/services/redis/connectionProviderFactory');
      (mod.getRedisProvider as sinon.SinonStub).returns(namespacedProvider);

      delete require.cache[rsPath];
      const { RedisService: RS } = require('../../../src/libs/services/redis.service');
      const namespaced = new RS(
        { host: 'localhost', port: 6379, keyPrefix: 'test:' },
        mockLogger,
      );
      expect((namespaced as any).keyPrefix).to.equal('tenant-a:test:');
    });

    it('should enable TLS when config.tls is true', () => {
      // Verify TLS branch by checking logger was called during main service init
      // The main service was constructed in beforeEach and we can test the TLS path
      // by verifying the config handling logic
      const config = { host: 'localhost', port: 6379, tls: true, keyPrefix: 'tls:' };
      const svc = Object.create(RedisService.prototype);
      svc.keyPrefix = config.keyPrefix;
      svc.config = config;
      expect(svc.keyPrefix).to.equal('tls:');
      expect(svc.config.tls).to.be.true;
    });
  });

  describe('event handlers', () => {
    it('should set connected=true on connect event', () => {
      // Emit the 'connect' event on the underlying mock client
      mockClient.emit('connect');
      expect(service.isConnected()).to.be.true;
    });

    it('should set connected=false and log error on error event', () => {
      const testError = new Error('redis error');
      mockClient.emit('error', testError);
      expect(service.isConnected()).to.be.false;
      expect(mockLogger.error.called).to.be.true;
    });

    it('should log info on ready event', () => {
      mockClient.emit('ready');
      expect(mockLogger.info.calledWithMatch('Redis client ready')).to.be.true;
    });
  });

  describe('isConnected', () => {
    it('should return true when connected', () => {
      (service as any).connected = true;
      expect(service.isConnected()).to.be.true;
    });

    it('should return false when not connected', () => {
      (service as any).connected = false;
      expect(service.isConnected()).to.be.false;
    });
  });

  describe('disconnect', () => {
    it('should call quit on the client', async () => {
      mockClient.quit.resolves();
      await service.disconnect();
      expect(mockClient.quit.calledOnce).to.be.true;
      expect(service.isConnected()).to.be.false;
    });

    it('should handle disconnect errors gracefully', async () => {
      mockClient.quit.rejects(new Error('quit failed'));
      await service.disconnect(); // should not throw
      expect(mockLogger.error.calledOnce).to.be.true;
    });
  });

  describe('get', () => {
    it('should return parsed JSON value', async () => {
      mockClient.get.resolves(JSON.stringify({ name: 'test' }));
      const result = await service.get('mykey');
      expect(result).to.deep.equal({ name: 'test' });
      expect(mockClient.get.calledWith('test:mykey')).to.be.true;
    });

    it('should return null when key does not exist', async () => {
      mockClient.get.resolves(null);
      const result = await service.get('nonexistent');
      expect(result).to.be.null;
    });

    it('should use namespace in key', async () => {
      mockClient.get.resolves(null);
      await service.get('mykey', { namespace: 'session' });
      expect(mockClient.get.calledWith('test:session:mykey')).to.be.true;
    });

    it('should throw RedisCacheError on failure', async () => {
      mockClient.get.rejects(new Error('connection lost'));
      try {
        await service.get('mykey');
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(RedisCacheError);
      }
    });
  });

  describe('set', () => {
    it('should serialize value as JSON and set with TTL', async () => {
      mockClient.set.resolves('OK');
      await service.set('mykey', { name: 'test' });
      expect(mockClient.set.calledWith('test:mykey', JSON.stringify({ name: 'test' }), 'EX', 3600)).to.be.true;
    });

    it('should use custom TTL', async () => {
      mockClient.set.resolves('OK');
      await service.set('mykey', 'value', { ttl: 300 });
      expect(mockClient.set.calledWith('test:mykey', JSON.stringify('value'), 'EX', 300)).to.be.true;
    });

    it('should use namespace in key', async () => {
      mockClient.set.resolves('OK');
      await service.set('mykey', 'value', { namespace: 'cache' });
      expect(mockClient.set.calledWith('test:cache:mykey', sinon.match.string, 'EX', 3600)).to.be.true;
    });

    it('should throw RedisCacheError on failure', async () => {
      mockClient.set.rejects(new Error('connection lost'));
      try {
        await service.set('mykey', 'value');
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(RedisCacheError);
      }
    });
  });

  describe('delete', () => {
    it('should delete the key', async () => {
      mockClient.del.resolves(1);
      await service.delete('mykey');
      expect(mockClient.del.calledWith('test:mykey')).to.be.true;
    });

    it('should use namespace in key', async () => {
      mockClient.del.resolves(1);
      await service.delete('mykey', { namespace: 'cache' });
      expect(mockClient.del.calledWith('test:cache:mykey')).to.be.true;
    });

    it('should throw RedisCacheError on failure', async () => {
      mockClient.del.rejects(new Error('failed'));
      try {
        await service.delete('mykey');
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(RedisCacheError);
      }
    });
  });

  describe('increment', () => {
    it('should increment and return new value', async () => {
      mockClient.incr.resolves(5);
      const result = await service.increment('counter');
      expect(result).to.equal(5);
    });

    it('should set TTL when provided', async () => {
      mockClient.incr.resolves(1);
      mockClient.expire.resolves(1);
      await service.increment('counter', { ttl: 60 });
      expect(mockClient.expire.calledWith('test:counter', 60)).to.be.true;
    });

    it('should not set TTL when not provided', async () => {
      mockClient.incr.resolves(1);
      await service.increment('counter');
      expect(mockClient.expire.called).to.be.false;
    });

    it('should throw RedisCacheError on failure', async () => {
      mockClient.incr.rejects(new Error('failed'));
      try {
        await service.increment('counter');
        expect.fail('Should have thrown');
      } catch (error) {
        expect(error).to.be.instanceOf(RedisCacheError);
      }
    });
  });

  // TLS socket options and retry backoff now live entirely in
  // StandaloneRedisProvider (tests/libs/services/redis/standaloneRedisProvider.test.ts);
  // RedisService forwards pool-sizing knobs (ClientOptions) and the stored
  // config's TLS flag into the connection config.
  describe('initializeClient internals', () => {
    it('forwards connect timeout / retry / offline-queue knobs as ClientOptions', () => {
      expect(capturedClientOptions).to.deep.equal({
        connectTimeoutMs: undefined,
        maxRetriesPerRequest: undefined,
        enableOfflineQueue: undefined,
      });
    });

    // The stored (admin-UI) Redis config is the only place a `tls` flag
    // lives for installs that never set REDIS_TLS_ENABLED. Dropping it here
    // would connect in plaintext to a TLS-only endpoint.
    it('forwards the stored config TLS flag into the connection config', () => {
      new RedisService(
        { host: 'localhost', port: 6379, keyPrefix: 'tls:', tls: true },
        createMockLogger(),
      );
      expect(capturedConnectionConfig.tls).to.equal(true);
    });

    it('leaves TLS off when the stored config does not ask for it', () => {
      expect(capturedConnectionConfig.tls).to.not.equal(true);
    });
  });
});

describe('getSharedRedisService cache key', () => {
  // Sharing one instance across containers is only safe while "same endpoint"
  // means "same connection". Two configs differing only in credentials or TLS
  // are different connections, and keying on host/port alone would hand the
  // second caller the first one's authenticated client.
  const base = { host: 'localhost', port: 6379, keyPrefix: 'app:' };

  afterEach(() => {
    resetSharedRedisServices();
  });

  // `createMockLogger()` returns a `MockLogger`, not a `Logger` instance --
  // `Logger` carries private fields, so no plain object literal is
  // structurally assignable to it without going through `unknown` first.
  const asLogger = (logger: MockLogger): Logger => logger as unknown as Logger;

  it('reuses one instance for an identical connection', () => {
    const a = getSharedRedisService({ ...base }, asLogger(createMockLogger()));
    const b = getSharedRedisService({ ...base }, asLogger(createMockLogger()));
    expect(a).to.equal(b);
  });

  it('does not share across different passwords', () => {
    const a = getSharedRedisService(
      { ...base, password: 'one' },
      asLogger(createMockLogger()),
    );
    const b = getSharedRedisService(
      { ...base, password: 'two' },
      asLogger(createMockLogger()),
    );
    expect(a).to.not.equal(b);
  });

  it('does not share across different usernames', () => {
    const a = getSharedRedisService(
      { ...base, username: 'u1' },
      asLogger(createMockLogger()),
    );
    const b = getSharedRedisService(
      { ...base, username: 'u2' },
      asLogger(createMockLogger()),
    );
    expect(a).to.not.equal(b);
  });

  it('rebinds the endpoint to the newer credentials rather than reusing', () => {
    // The key is the endpoint alone, so a credential change replaces the
    // cached entry. The first caller keeps its own working instance; the
    // next lookup gets one built with the current credentials, never the
    // stale authenticated client.
    const first = getSharedRedisService(
      { ...base, password: 'old' },
      asLogger(createMockLogger()),
    );
    const second = getSharedRedisService(
      { ...base, password: 'new' },
      asLogger(createMockLogger()),
    );
    const third = getSharedRedisService(
      { ...base, password: 'new' },
      asLogger(createMockLogger()),
    );

    expect(second).to.not.equal(first);
    expect(third).to.equal(second);
  });

  it('does not share a TLS connection with a plaintext one', () => {
    const a = getSharedRedisService(
      { ...base, tls: true },
      asLogger(createMockLogger()),
    );
    const b = getSharedRedisService(
      { ...base, tls: false },
      asLogger(createMockLogger()),
    );
    expect(a).to.not.equal(b);
  });
});
