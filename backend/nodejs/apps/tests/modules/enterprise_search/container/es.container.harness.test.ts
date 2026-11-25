/**
 * The conversation module's container composes the agent harness (and session
 * erasure, which runs whether or not the harness is on: X-5), without touching
 * Redis while the deployment has not asked for the harness.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import { EnterpriseSearchAgentContainer } from '../../../../src/modules/enterprise_search/container/es.container';
import { KeyValueStoreService } from '../../../../src/libs/services/keyValueStore.service';
import * as composition from '../../../../src/modules/agent-harness/composition';
import * as redisProviders from '../../../../src/libs/services/redis/connectionProviderFactory';
import * as messageBroker from '../../../../src/libs/services/message-broker.factory';
import { TokenScopes } from '../../../../src/libs/enums/token-scopes.enum';
import jwt from 'jsonwebtoken';

const HARNESS_ENV = [
  'REPLICA_SET_AVAILABLE',
  'AGENT_HARNESS_RELAY_ENABLED',
  'AGENT_HARNESS_WORKER_ENABLED',
  'ENABLE_AGENT_HARNESS',
];

const appConfig = {
  jwtSecret: 'jwt',
  scopedJwtSecret: 'scoped',
  aiBackend: 'http://query:8000',
  storageBackend: 'http://storage:3000',
  storage: { storageType: 'local' },
} as never;

const cmConfig = {
  redisConfig: { host: 'redis', port: 6379 },
} as never;

const fakeKv = (overrides: Record<string, unknown> = {}) => ({
  connect: sinon.stub().resolves(),
  disconnect: sinon.stub().resolves(),
  isConnected: sinon.stub().returns(true),
  get: sinon.stub().resolves(null),
  usesEtcd: () => false,
  ...overrides,
});

const setInstance = (container: Container | undefined): void => {
  (EnterpriseSearchAgentContainer as unknown as { instance: Container | undefined }).instance =
    container;
};

describe('EnterpriseSearchAgentContainer with the agent harness', () => {
  beforeEach(() => {
    const env: NodeJS.ProcessEnv = { ...process.env, SECRET_KEY: '82caea2952e4c88729a5665d901f67aaab60ec12d7e52de6be6a11ec2f23a1c2' };
    for (const name of HARNESS_ENV) delete env[name];
    sinon.stub(process, 'env').value(env);
  });

  afterEach(() => {
    sinon.restore();
    setInstance(undefined);
  });

  it('composes the harness, starts session erasure, and opens no Redis while the harness is off', async () => {
    const kv = fakeKv();
    sinon.stub(KeyValueStoreService, 'getInstance').returns(kv as never);
    const redis = sinon.stub(redisProviders, 'getRedisProvider');
    const erasure = sinon.stub(composition, 'startSessionErasureServices').resolves();

    const container = await EnterpriseSearchAgentContainer.initialize(cmConfig, appConfig);

    expect(kv.connect.calledOnce).to.equal(true);
    expect(container.get('KeyValueStoreService')).to.equal(kv);
    expect(container.isBound('AuthMiddleware')).to.equal(true);
    for (const key of [
      composition.AGENT_HARNESS_EXECUTION_SERVICE,
      composition.AGENT_HARNESS_SCHEDULE_FIRER,
      composition.AGENT_HARNESS_PUBLICATIONS,
      composition.AGENT_HARNESS_SESSION_FORKS,
      composition.AGENT_HARNESS_ERASURE_WORKER,
      composition.AGENT_HARNESS_RETENTION_SWEEPER,
    ]) {
      expect(container.isBound(key), key).to.equal(true);
    }
    expect(redis.called).to.equal(false);
    expect(erasure.calledOnce).to.equal(true);
    expect(erasure.firstCall.args[0]).to.equal(container);
    expect(EnterpriseSearchAgentContainer.getInstance()).to.equal(container);
  });

  it("hands the harness adapters that read the app config when used, so a refreshed config reaches them", async () => {
    const config = {
      jwtSecret: 'jwt-1',
      scopedJwtSecret: 'scoped-1',
      aiBackend: 'http://query-1:8000',
      storageBackend: 'http://storage-1:3000',
      storage: { storageType: 'local' },
    };
    const kv = fakeKv({
      get: sinon
        .stub()
        .resolves(JSON.stringify({ sandboxBroker: { endpoint: 'http://broker:9000' } })),
    });
    sinon.stub(KeyValueStoreService, 'getInstance').returns(kv as never);
    sinon.stub(composition, 'startSessionErasureServices').resolves();
    const consumer = { consumer: true };
    const createConsumer = sinon
      .stub(messageBroker, 'createDedicatedMessageConsumer')
      .returns(consumer as never);
    const bind = sinon.spy(composition, 'bindAgentHarness');

    await EnterpriseSearchAgentContainer.initialize(cmConfig, config as never);
    Object.assign(config, {
      jwtSecret: 'jwt-2',
      scopedJwtSecret: 'scoped-2',
      aiBackend: 'http://query-2:8000',
      storageBackend: 'http://storage-2:3000',
    });

    const deps = bind.firstCall.args[1];
    // The adapters keep the config readers private; reading them is the point here.
    const reader = (adapter: unknown, field: string): (() => unknown) =>
      (adapter as Record<string, () => unknown>)[field]!;
    const fresh = { aiBackend: 'http://query-2:8000', jwtSecret: 'jwt-2', scopedJwtSecret: 'scoped-2' };

    expect(deps.inlineClaimer!()).to.equal('http://query-2:8000');
    for (const adapter of [deps.agentAccess, deps.recordAccess, deps.artifactAccess, deps.publications!.authority]) {
      expect(reader(adapter, 'aiBackend')()).to.equal('http://query-2:8000');
    }
    expect(reader(deps.schedules!.actors, 'jwtSecret')()).to.equal('jwt-2');
    for (const adapter of [deps.forks!.artifacts, deps.erasure!.artifacts, deps.erasure!.agentDeletions]) {
      expect(reader(adapter, 'config')()).to.deep.equal(fresh);
    }
    expect(await reader(deps.forks!.objects, 'endpoint')()).to.deep.equal({
      baseUrl: 'http://storage-2:3000',
      storageType: 'local',
    });
    const token = (deps.forks!.objects as unknown as { storageToken: (orgId: string) => string }).storageToken('org-9');
    const claims = jwt.verify(token, 'scoped-2') as jwt.JwtPayload;
    expect(claims).to.deep.include({ orgId: 'org-9', scopes: [TokenScopes.STORAGE_TOKEN] });
    expect((claims.exp ?? 0) - (claims.iat ?? 0)).to.equal(300);

    expect(await deps.sandboxBrokerEndpoint!()).to.equal('http://broker:9000');
    expect(deps.schedules!.connectorEvents!.consumer()).to.equal(consumer);
    expect(createConsumer.firstCall.args.slice(2)).to.deep.equal([
      'agent-harness-triggers',
      'agent-harness-triggers',
    ]);
  });

  it('fails startup when the configuration store cannot be reached', async () => {
    const down = new Error('etcd down');
    sinon
      .stub(KeyValueStoreService, 'getInstance')
      .returns(fakeKv({ connect: sinon.stub().rejects(down) }) as never);
    let thrown: unknown;
    try {
      await EnterpriseSearchAgentContainer.initialize(cmConfig, appConfig);
    } catch (error) {
      thrown = error;
    }
    expect(thrown).to.equal(down);
    setInstance(undefined);
    expect(() => EnterpriseSearchAgentContainer.getInstance()).to.throw(
      'Service container not initialized',
    );
  });

  it('dispose stops the harness background services before disconnecting the store, and forgets the container', async () => {
    const order: string[] = [];
    const kv = fakeKv({
      disconnect: sinon.stub().callsFake(async () => {
        order.push('kv');
      }),
    });
    const container = new Container();
    container.bind('KeyValueStoreService').toConstantValue(kv);
    container.bind(composition.AGENT_HARNESS_ERASURE_WORKER).toConstantValue({
      stop: async () => {
        order.push('erasure');
      },
    });
    setInstance(container);

    await EnterpriseSearchAgentContainer.dispose();

    expect(order).to.deep.equal(['erasure', 'kv']);
    expect(() => EnterpriseSearchAgentContainer.getInstance()).to.throw(
      'Service container not initialized',
    );
  });

  it('dispose does not throw when stopping fails, and still forgets the container', async () => {
    const container = new Container();
    container.bind(composition.AGENT_HARNESS_ERASURE_WORKER).toConstantValue({
      stop: () => Promise.reject(new Error('lease store down')),
    });
    setInstance(container);
    await EnterpriseSearchAgentContainer.dispose();
    expect(() => EnterpriseSearchAgentContainer.getInstance()).to.throw();
  });
});
