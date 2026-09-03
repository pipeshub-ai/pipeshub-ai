/**
 * Test-only fake `IRedisConnectionProvider`.
 *
 * Lets a test stub `getRedisProvider` (from
 * `src/libs/services/redis/connectionProviderFactory`) to return a fully
 * controllable fake instead of chasing the real `ioredis` module through
 * `require.cache`. This is the Node equivalent of the Python test pattern
 * `patch("...connection_provider_factory.get_redis_provider", return_value=mock_provider)`
 * and is the intended way to unit test anything built on
 * `IRedisConnectionProvider` -- through the abstraction, not the
 * `ioredis` client underneath it.
 */
import { EventEmitter } from 'events';
import sinon from 'sinon';

export interface FakeRedisClient extends EventEmitter {
  [command: string]: any;
}

const DEFAULT_STUBBED_COMMANDS = [
  'get',
  'set',
  'del',
  'getBuffer',
  'exists',
  'incr',
  'expire',
  'scan',
  'watch',
  'unwatch',
  'ping',
  'quit',
  'disconnect',
  'connect',
  'publish',
  'subscribe',
  'xadd',
  'xreadgroup',
  'xack',
  'xgroup',
  'xautoclaim',
  'type',
  'script',
  'keys',
];

export function makeFakeRedisClient(
  overrides: Record<string, any> = {},
): FakeRedisClient {
  const client = new EventEmitter() as FakeRedisClient;
  client.status = 'ready';
  for (const command of DEFAULT_STUBBED_COMMANDS) {
    client[command] = sinon.stub().resolves(undefined);
  }
  client.multi = sinon.stub().returns({
    set: () => client.multi(),
    del: () => client.multi(),
    exec: sinon.stub().resolves([]),
  });
  client.pipeline = sinon.stub().returns({
    xadd: () => client.pipeline(),
    exec: sinon.stub().resolves([]),
  });
  Object.assign(client, overrides);
  return client;
}

export interface FakeRedisProvider {
  isCluster: boolean;
  mode: string;
  keyNamespace: string;
  getClient: sinon.SinonStub;
  createClient: sinon.SinonStub;
  createPubSubClient: sinon.SinonStub;
  release: sinon.SinonStub;
  scanKeys: sinon.SinonStub;
  /**
   * Queue the keys the next `scanKeys()` should yield. `scanKeys` is an
   * async *iterable* on the real interface, so a test cannot simply
   * `.resolves([...])` it -- this keeps the fake honest about that.
   */
  setScanKeys: (keys: string[]) => void;
  loadScript: sinon.SinonStub;
  keySlot: sinon.SinonStub;
  connectionUrl: sinon.SinonStub;
  ping: sinon.SinonStub;
  close: sinon.SinonStub;
  /** Every client createClient() has handed out so far, in call order. */
  createdClients: FakeRedisClient[];
}

/**
 * Build a fake provider. `createClient()` returns a fresh client each call
 * (from `clientFactory`, defaulting to `makeFakeRedisClient`); `getClient()`
 * returns one shared instance.
 */
export function createFakeRedisProvider(
  clientFactory: () => FakeRedisClient = makeFakeRedisClient,
  keyNamespace = '',
): FakeRedisProvider {
  const createdClients: FakeRedisClient[] = [];
  const sharedClient = clientFactory();
  let scanResults: string[] = [];

  const provider: FakeRedisProvider = {
    isCluster: false,
    mode: 'standalone',
    keyNamespace,
    getClient: sinon.stub().callsFake(() => sharedClient),
    createClient: sinon.stub().callsFake(() => {
      const client = clientFactory();
      createdClients.push(client);
      return client;
    }),
    createPubSubClient: sinon.stub().callsFake(() => clientFactory()),
    release: sinon.stub(),
    scanKeys: sinon.stub().callsFake(async function* (): AsyncIterable<string> {
      yield* scanResults;
    }),
    setScanKeys: (keys: string[]) => {
      scanResults = keys;
    },
    loadScript: sinon.stub().resolves('fakesha'),
    keySlot: sinon.stub().returns(0),
    connectionUrl: sinon.stub().returns('redis://fake:6379/0'),
    ping: sinon.stub().resolves(true),
    close: sinon.stub().resolves(),
    createdClients,
  };
  return provider;
}

/**
 * Stub `getRedisProvider` on the real `connectionProviderFactory` module so
 * every call-site (which accesses it as a live property lookup on the
 * required module, not a destructured copy) picks up `provider` without
 * needing to reload any source file. Returns a restore function.
 */
export function stubGetRedisProvider(provider: FakeRedisProvider): () => void {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const mod = require('../../src/libs/services/redis/connectionProviderFactory');
  const stub = sinon.stub(mod, 'getRedisProvider').returns(provider as any);
  return () => stub.restore();
}
