/**
 * Test-only helper that swaps `require.cache['ioredis']` for a capturing
 * fake so a test can assert on the exact options a provider passed to
 * `new Redis(...)` / `new Cluster(...)`, then restores the previous
 * (global-mock) entry in `afterEach`.
 *
 * Mirrors the manual require.cache-swap pattern already used in
 * `tests/libs/services/redis.service.test.ts`, generalised for both the
 * `Redis` and `Cluster` exports.
 */
import { EventEmitter } from 'events';
import sinon from 'sinon';

export interface FakeClientHandle extends EventEmitter {
  quit: sinon.SinonStub;
  disconnect: sinon.SinonStub;
  ping: sinon.SinonStub;
  scan: sinon.SinonStub;
  script: sinon.SinonStub;
  [key: string]: any;
}

function makeFakeClient(): FakeClientHandle {
  const client = new EventEmitter() as FakeClientHandle;
  client.quit = sinon.stub().resolves('OK');
  client.disconnect = sinon.stub().resolves();
  client.ping = sinon.stub().resolves('PONG');
  client.scan = sinon.stub().resolves(['0', []]);
  client.script = sinon.stub().resolves('fakesha');
  client.nodes = sinon.stub().returns([client]);
  // `ClusterRedisProvider.masters()` skips its readiness ping when the
  // client already reports 'ready', and reads `options` when handing out a
  // dedicated pub/sub connection.
  client.status = 'ready';
  client.options = { host: 'localhost', port: 6379 };
  return client;
}

export interface IoredisCapture {
  capturedRedisArgs: any[][];
  capturedClusterArgs: any[][];
  lastRedisClient: FakeClientHandle | null;
  lastClusterClient: FakeClientHandle | null;
  install(): void;
  restore(): void;
}

export function createIoredisCapture(): IoredisCapture {
  const ioredisPath = require.resolve('ioredis');
  let saved: NodeModule | undefined;

  const state: IoredisCapture = {
    capturedRedisArgs: [],
    capturedClusterArgs: [],
    lastRedisClient: null,
    lastClusterClient: null,
    install() {
      saved = require.cache[ioredisPath];

      function FakeRedis(this: any, ...args: any[]) {
        state.capturedRedisArgs.push(args);
        const client = makeFakeClient();
        state.lastRedisClient = client;
        process.nextTick(() => client.emit('ready'));
        return client;
      }

      function FakeCluster(this: any, ...args: any[]) {
        state.capturedClusterArgs.push(args);
        const client = makeFakeClient();
        state.lastClusterClient = client;
        process.nextTick(() => client.emit('ready'));
        return client;
      }

      require.cache[ioredisPath] = {
        ...(saved as any),
        exports: { Redis: FakeRedis, Cluster: FakeCluster, default: FakeRedis },
      } as any;
    },
    restore() {
      if (saved) {
        require.cache[ioredisPath] = saved;
      }
      state.capturedRedisArgs = [];
      state.capturedClusterArgs = [];
      state.lastRedisClient = null;
      state.lastClusterClient = null;
    },
  };
  return state;
}
