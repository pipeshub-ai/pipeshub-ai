import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { createMockLogger } from '../../helpers/mock-logger';
import { DocStoreReadOnlyError } from '../../../src/libs/repositories/errors';

/**
 * Step F3 of a document-store move: the background consumers and the outbox dispatcher take no new work while the MongoDB fence is
 * not active. Each module is required afresh with the fence module that is in the cache now, so the stubs below are the ones it calls.
 */
const FENCE = '../../../src/libs/repositories/fence';
// the cached module is put back after each test: later suites in this worker stub the instance their code imported
const saved = new Map<string, NodeModule | undefined>();
const fresh = (path: string): unknown => {
  const key = require.resolve(path);
  if (!saved.has(key)) saved.set(key, require.cache[key]);
  delete require.cache[key];
  return require(path);
};
const fence = (): typeof import('../../../src/libs/repositories/fence') => require(FENCE);

describe('background writers pause while the document store is moved (F3)', () => {
  afterEach(() => {
    sinon.restore();
    for (const [key, mod] of saved) {
      if (mod) require.cache[key] = mod;
      else delete require.cache[key];
    }
    saved.clear();
  });

  it('Kafka: pauses the partition, keeps the offset (throws a retryable error) and resumes once the store is writable', async () => {
    const f = fence();
    sinon.stub(f, 'mongoStoreMoving').resolves(true);
    let release: (v: boolean) => void = () => undefined;
    sinon.stub(f, 'untilMongoWritable').returns(new Promise<boolean>((r) => { release = r; }));
    const { BaseKafkaConsumerConnection } = (fresh('../../../src/libs/services/kafka.service') as typeof import('../../../src/libs/services/kafka.service'));
    class C extends BaseKafkaConsumerConnection {}
    const c = new C({ clientId: 'c', brokers: ['b:9092'], groupId: 'g' }, createMockLogger() as never);
    const run = sinon.stub().resolves();
    (c as unknown as { consumer: unknown }).consumer = { connect: sinon.stub().resolves(), run };
    (c as unknown as { isInitialized: boolean }).isInitialized = true;
    const handler = sinon.stub().resolves();
    await c.consume(handler);
    const resume = sinon.stub();
    const pause = sinon.stub().returns(resume);
    const message = { key: null, value: Buffer.from('{"a":1}'), headers: {} };
    let thrown: unknown;
    await run.firstCall.args[0].eachMessage({ topic: 't', partition: 0, message, pause }).catch((e: unknown) => { thrown = e; });
    expect(thrown).to.be.instanceOf(DocStoreReadOnlyError);
    expect(pause.calledOnce).to.equal(true);
    expect(handler.called).to.equal(false);
    expect(resume.called).to.equal(false);
    release(true);
    await new Promise((r) => setImmediate(r));
    expect(resume.calledOnce).to.equal(true);
  });

  it('Kafka: an active store hands the message to the handler without pausing', async () => {
    sinon.stub(fence(), 'mongoStoreMoving').resolves(false);
    const { BaseKafkaConsumerConnection } = (fresh('../../../src/libs/services/kafka.service') as typeof import('../../../src/libs/services/kafka.service'));
    class C extends BaseKafkaConsumerConnection {}
    const c = new C({ clientId: 'c', brokers: ['b:9092'], groupId: 'g' }, createMockLogger() as never);
    const run = sinon.stub().resolves();
    (c as unknown as { consumer: unknown }).consumer = { connect: sinon.stub().resolves(), run };
    (c as unknown as { isInitialized: boolean }).isInitialized = true;
    const handler = sinon.stub().resolves();
    await c.consume(handler);
    const pause = sinon.stub();
    await run.firstCall.args[0].eachMessage({ topic: 't', partition: 0, message: { key: null, value: Buffer.from('{"a":1}'), headers: {} }, pause });
    expect(pause.called).to.equal(false);
    expect(handler.calledOnce).to.equal(true);
  });

  it('Redis Streams: an entry read while the store is moved is neither handled nor acked when the consumer stops waiting', async () => {
    const wait = sinon.stub(fence(), 'untilMongoWritable').resolves(false);
    const { BaseRedisStreamsConsumerConnection } = (fresh('../../../src/libs/services/redis-streams.service') as typeof import('../../../src/libs/services/redis-streams.service'));
    class C extends BaseRedisStreamsConsumerConnection {}
    const c = Object.create(C.prototype) as Record<string, unknown>;
    const xack = sinon.stub().resolves(1);
    Object.assign(c, {
      running: true,
      subscribedTopics: ['s'],
      groupId: 'g',
      logger: createMockLogger(),
      ackRedis: { xack },
      drainPending: sinon.stub().resolves(),
      xreadGroupGrouped: sinon.stub().resolves([['s', [['1-0', ['key', 'k', 'value', '{"n":1}']]]]]),
    });
    // the gate answers "stopped": the loop ends without touching the entry
    wait.callsFake(async () => { c.running = false; return false; });
    const handler = sinon.stub().resolves();
    await (c as unknown as { consumeLoop(h: unknown): Promise<void> }).consumeLoop(handler);
    expect(wait.calledOnce).to.equal(true);
    expect(handler.called).to.equal(false);
    expect(xack.called).to.equal(false);
  });

  it('Redis Streams: an entry recovered from the pending list at start waits too, and stays pending if the consumer stops', async () => {
    const wait = sinon.stub(fence(), 'untilMongoWritable').resolves(false);
    const { BaseRedisStreamsConsumerConnection } = (fresh('../../../src/libs/services/redis-streams.service') as typeof import('../../../src/libs/services/redis-streams.service'));
    class C extends BaseRedisStreamsConsumerConnection {}
    const c = Object.create(C.prototype) as Record<string, unknown>;
    const xack = sinon.stub().resolves(1);
    Object.assign(c, {
      running: true,
      subscribedTopics: ['s'],
      groupId: 'g',
      consumerId: 'x',
      count: 10,
      config: {},
      logger: createMockLogger(),
      ackRedis: { xack },
      redis: { xautoclaim: sinon.stub().resolves(['0-0', [['1-0', ['key', 'k', 'value', '{"n":1}']]], []]) },
    });
    wait.callsFake(async () => { c.running = false; return false; });
    const handler = sinon.stub().resolves();
    await (c as unknown as { drainPending(h: unknown): Promise<void> }).drainPending(handler);
    expect(wait.calledOnce).to.equal(true);
    expect(handler.called).to.equal(false);
    expect(xack.called).to.equal(false);
  });

  it('PostgreSQL queue: no claim is made (no attempt counted) while the store is moved', async () => {
    const { BasePgQueueConsumerConnection } = (fresh('../../../src/libs/services/pg-queue.service') as typeof import('../../../src/libs/services/pg-queue.service'));
    class C extends BasePgQueueConsumerConnection {}
    const c = Object.create(C.prototype) as Record<string, unknown>;
    let running = true;
    Object.assign(c, { isRunning: () => running, held: new Map(), topics: ['t'], groupId: 'g', clientId: 'x', visibilityS: 30, config: {}, logger: createMockLogger(), settle: sinon.stub(), idle: async () => { running = false; } });
    const boot = require('../../../src/libs/db/runtime-boot') as typeof import('../../../src/libs/db/runtime-boot');
    const claims = sinon.stub().resolves({ rows: [] });
    sinon.stub(boot, 'ensurePgRuntime').returns({ withUow: async (_o: unknown, fn: (tx: unknown) => unknown) => fn({ query: claims }) } as never);
    sinon.stub(fence(), 'untilMongoWritable').callsFake(async () => { running = false; return false; });
    await (c as unknown as { consumeLoop(h: unknown): Promise<void> }).consumeLoop(sinon.stub());
    expect(claims.called, 'the claim query counts an attempt: it must not run').to.equal(false);
  });

  it('PostgreSQL queue: a claimed row is not settled (no attempt spent) while the store is moved', async () => {
    const f = fence();
    const { BasePgQueueConsumerConnection } = (fresh('../../../src/libs/services/pg-queue.service') as typeof import('../../../src/libs/services/pg-queue.service'));
    class C extends BasePgQueueConsumerConnection {}
    const c = Object.create(C.prototype) as Record<string, unknown>;
    let running = true;
    const settle = sinon.stub().resolves();
    Object.assign(c, {
      isRunning: () => running,
      settle,
      held: new Map(),
      topics: ['t'],
      groupId: 'g',
      clientId: 'x',
      visibilityS: 30,
      config: {},
      logger: createMockLogger(),
      idle: async () => { running = false; },
    });
    const boot = require('../../../src/libs/db/runtime-boot') as typeof import('../../../src/libs/db/runtime-boot');
    const claimed = { rows: [{ topic: 't', message_id: '1', attempts: 1 }] };
    sinon.stub(boot, 'ensurePgRuntime').returns({ withUow: async (_o: unknown, fn: (tx: unknown) => unknown) => fn({ query: async () => claimed }) } as never);
    // the store starts moving between the claim and the row: the first wait (before the claim) passes, the per-row one holds
    sinon.stub(f, 'untilMongoWritable').onFirstCall().resolves(true).callsFake(async () => { running = false; return false; });
    await (c as unknown as { consumeLoop(h: unknown): Promise<void> }).consumeLoop(sinon.stub());
    expect(settle.called).to.equal(false);
  });

  it('outbox dispatcher: claims nothing while the store is moved', async () => {
    sinon.stub(fence(), 'mongoStoreMoving').resolves(true);
    const outbox = require('../../../src/libs/repositories/outbox') as typeof import('../../../src/libs/repositories/outbox');
    const claimNext = sinon.stub().resolves(null);
    outbox.setOutboxRepository({ claimNext } as never);
    try {
      const { OutboxDispatcher } = (fresh('../../../src/libs/services/outbox/outbox.dispatcher') as typeof import('../../../src/libs/services/outbox/outbox.dispatcher'));
      const d = new OutboxDispatcher({ publish: sinon.stub() } as never, createMockLogger() as never);
      expect(await d.drain()).to.equal(0);
      expect(claimNext.called).to.equal(false);
    } finally {
      outbox.setOutboxRepository(undefined);
    }
  });
});
