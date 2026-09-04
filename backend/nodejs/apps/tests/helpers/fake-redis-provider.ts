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

import { IRedisConnectionProvider } from '../../src/libs/services/redis/connectionProvider.interface';

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
] as const;

type StubbedCommand = (typeof DEFAULT_STUBBED_COMMANDS)[number];

/** Every command a test can stub a return value for, typed as a Sinon stub
 * rather than a `[key: string]: any` index -- a call site with a typo in the
 * command name is a compile error, not a silently-`undefined` mock method. */
export interface FakeRedisClient extends EventEmitter {
  status: string;
  multi: sinon.SinonStub;
  pipeline: sinon.SinonStub;
  get: sinon.SinonStub;
  set: sinon.SinonStub;
  del: sinon.SinonStub;
  getBuffer: sinon.SinonStub;
  exists: sinon.SinonStub;
  incr: sinon.SinonStub;
  expire: sinon.SinonStub;
  scan: sinon.SinonStub;
  watch: sinon.SinonStub;
  unwatch: sinon.SinonStub;
  ping: sinon.SinonStub;
  quit: sinon.SinonStub;
  disconnect: sinon.SinonStub;
  connect: sinon.SinonStub;
  publish: sinon.SinonStub;
  subscribe: sinon.SinonStub;
  xadd: sinon.SinonStub;
  xreadgroup: sinon.SinonStub;
  xack: sinon.SinonStub;
  xgroup: sinon.SinonStub;
  xautoclaim: sinon.SinonStub;
  type: sinon.SinonStub;
  script: sinon.SinonStub;
  keys: sinon.SinonStub;
}

export function makeFakeRedisClient(
  overrides: Partial<Pick<FakeRedisClient, StubbedCommand | 'multi' | 'pipeline' | 'status'>> = {},
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

/**
 * `extends IRedisConnectionProvider` so the compiler enforces that this
 * double stays complete: a member added to the interface breaks this file
 * rather than silently returning `undefined` to production code through an
 * `as any` cast.
 */
export interface FakeRedisProvider extends IRedisConnectionProvider {
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
  const stub = sinon.stub(mod, 'getRedisProvider').returns(provider);
  return () => stub.restore();
}
