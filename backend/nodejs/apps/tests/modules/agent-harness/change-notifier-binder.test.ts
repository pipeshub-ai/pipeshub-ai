/**
 * CPD-6 wiring: the notifier and the live layer are bound whatever
 * `CONTROL_PLANE_PIN` says; a process without a broker hears its own writes
 * only; each Node process consumes `control-plane-events` in its own group.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import { Kafka } from 'kafkajs';
import { ENV_MESSAGE_BROKER } from '../../../src/libs/constants/messaging.constants';
import {
  changeEventsGroupId,
  controlPlaneEventBroker,
} from '../../../src/modules/agent-harness/adapters/messaging/control-plane-event-broker';
import * as outboxDispatcher from '../../../src/libs/services/outbox/outbox.dispatcher';
import { OutboxEvent } from '../../../src/libs/repositories/outbox/schema/outbox.schema';
import {
  controlPlaneSubscribers,
  processChangeOutbox,
} from '../../../src/modules/agent-harness/adapters/control-plane/process-changes';
import { MongoLeaderLease } from '../../../src/libs/repositories/agent-harness/leader-lease/mongo';
import {
  LEFTOVER_GROUPS,
  LEFTOVER_SWEEP_INTERVAL_MS,
  LEFTOVER_SWEEP_LEASE_NAME,
} from '../../../src/modules/agent-harness/application/leftover-group-sweeper';
import { LiveLayerService } from '../../../src/modules/agent-harness/application/live-layer-service';
import {
  AGENT_HARNESS_CHANGE_NOTIFIER,
  AGENT_HARNESS_LIVE_LAYERS,
  bindChangeNotifier,
  changeListenBackoff,
  changeNotifier,
  startChangeNotifier,
  stopChangeNotifier,
} from '../../../src/modules/agent-harness/binders/change-notifier';
import type { HarnessBindingContext } from '../../../src/modules/agent-harness/binders/context';
import { changeEventMessage, MongoHeadRevisions, type ChangeEvent } from '../../../src/modules/control-plane';
import { createMockLogger } from '../../helpers/mock-logger';
import { stubGetRedisProvider, createFakeRedisProvider } from '../../helpers/fake-redis-provider';
import { InMemoryBroker } from '../control-plane/support/in-memory-broker';
import { changeEvent } from '../control-plane/support/notifier-contract';

const ORG = 'org-binder-1';

const context = (container: Container, changeEvents: unknown, killSwitch = true): HarnessBindingContext =>
  ({
    container,
    logger: undefined,
    dependencies: {
      changeEvents,
      rollout: {
        settings: async () => ({ infrastructureReady: true, killSwitch, orgEnabled: true, modelFamilies: null }),
      },
    },
  }) as unknown as HarnessBindingContext;

describe('CPD-6 bindChangeNotifier', () => {
  it("with a broker: the notifier listens once started, another process's event reaches this process's holders, and stop ends it", async () => {
    const broker = new InMemoryBroker();
    const container = new Container();
    const consumer = broker.consumer('this-node');
    bindChangeNotifier(context(container, { producer: broker.producer(), consumer }));
    const got: ChangeEvent[] = [];
    const unsubscribe = controlPlaneSubscribers.subscribe((event) => got.push(event));
    try {
      expect(changeNotifier(container)).to.equal(container.get(AGENT_HARNESS_CHANGE_NOTIFIER));
      startChangeNotifier(container);
      await new Promise((resolve) => setImmediate(resolve));
      const { topic, key, value } = changeEventMessage(changeEvent(ORG, 5));
      await broker.producer().publish(topic, { key, value });
      await broker.settled();
      expect(got).to.deep.equal([changeEvent(ORG, 5)]);

      await stopChangeNotifier(container);
      expect(consumer.isConnected()).to.equal(false);
    } finally {
      unsubscribe();
    }
  });

  it('without a broker: no notifier is bound, start and stop do nothing, and the live layer still answers', async () => {
    const container = new Container();
    bindChangeNotifier(context(container, null));

    expect(changeNotifier(container)).to.equal(null);
    startChangeNotifier(container);
    await stopChangeNotifier(container);
    // No Mongo in this lane: the head adapter refuses a closed connection at once, so the head is given here.
    sinon.stub(MongoHeadRevisions.prototype, 'head').resolves(4);
    try {
      const layer = await container.get<LiveLayerService>(AGENT_HARNESS_LIVE_LAYERS).read(ORG);
      expect(layer).to.deep.equal({
        schemaVersion: 1,
        orgId: ORG,
        revision: 4,
        layers: [{ layer: { schemaVersion: 1, scope: { kind: 'live' }, rollout: { AGENT_HARNESS_KILL_SWITCH: true } } }],
        revokedBindings: [],
      });
    } finally {
      sinon.restore();
    }
  });

  it('keeps a notifier and a live-layer service bound before it', () => {
    const container = new Container();
    const notifier = { marker: 'notifier' };
    const layers = new LiveLayerService({ heads: { head: async () => 0, heads: async () => ({ org: 0, deployment: 0 }) } });
    container.bind(AGENT_HARNESS_CHANGE_NOTIFIER).toConstantValue(notifier);
    container.bind(AGENT_HARNESS_LIVE_LAYERS).toConstantValue(layers);
    const broker = new InMemoryBroker();

    bindChangeNotifier(context(container, { producer: broker.producer(), consumer: broker.consumer('g') }));

    expect(container.get(AGENT_HARNESS_CHANGE_NOTIFIER)).to.equal(notifier);
    expect(container.get(AGENT_HARNESS_LIVE_LAYERS)).to.equal(layers);
  });

  it("OPS1-4-8: with the broker's admin, the leftover-group sweep starts with the notifier, runs as the lease holder and stops with it", async () => {
    const clock = sinon.useFakeTimers();
    const acquire = sinon.stub(MongoLeaderLease.prototype, 'tryAcquire').resolves(true);
    const release = sinon.stub(MongoLeaderLease.prototype, 'release').resolves();
    const removeIdleGroups = sinon.stub().resolves({ removed: [], left: [] });
    const broker = new InMemoryBroker();
    const container = new Container();
    try {
      bindChangeNotifier(context(container, { producer: broker.producer(), consumer: broker.consumer('n'), admin: { removeIdleGroups } }));
      expect(removeIdleGroups.called, 'nothing before start').to.equal(false);

      startChangeNotifier(container);
      await clock.tickAsync(LEFTOVER_SWEEP_INTERVAL_MS);
      expect(acquire.firstCall.args[0]).to.equal(LEFTOVER_SWEEP_LEASE_NAME);
      expect(removeIdleGroups.callCount).to.equal(LEFTOVER_GROUPS.length);

      await stopChangeNotifier(container);
      expect(release.calledOnceWith(LEFTOVER_SWEEP_LEASE_NAME, acquire.firstCall.args[1])).to.equal(true);
      await clock.tickAsync(2 * LEFTOVER_SWEEP_INTERVAL_MS);
      expect(removeIdleGroups.callCount).to.equal(LEFTOVER_GROUPS.length);
    } finally {
      clock.restore();
      sinon.restore();
    }
  });
});

describe("CPD-6 this process's change outbox", () => {
  afterEach(() => {
    sinon.restore();
  });

  it('writes the event to the shared outbox with the write, then tells this process and wakes the dispatcher once the write committed', async () => {
    const created = sinon.stub(OutboxEvent, 'create').resolves([] as never);
    const got: ChangeEvent[] = [];
    const kick = sinon.stub(outboxDispatcher, 'kickOutbox');
    const unsubscribe = controlPlaneSubscribers.subscribe((event) => got.push(event));
    const session = { id: 'the-write-session' };
    const tx = { orgId: ORG, headRevision: 5, session, enlist: (write: () => Promise<void>) => write(), external: () => ({ value: undefined }) };
    const event = changeEvent(ORG, 5);
    try {
      await processChangeOutbox.enqueue(tx as never, event);

      expect([got.length, kick.callCount], 'nothing before the commit').to.deep.equal([0, 0]);
      const [rows, options] = created.firstCall.args as unknown as [Array<Record<string, unknown>>, { session?: unknown }];
      expect(options.session).to.equal(session);
      expect(rows.map(({ topic, key, orderingKey, value, status, attempts }) => ({ topic, key, orderingKey, value, status, attempts }))).to.deep.equal([
        { ...changeEventMessage(event), status: 'pending', attempts: 0 },
      ]);

      await processChangeOutbox.committed(tx as never);
      await processChangeOutbox.committed(tx as never);
      expect(got).to.deep.equal([event]);
      expect(kick.callCount).to.equal(1);
    } finally {
      unsubscribe();
    }
  });

  it('without a session (ordered steps) the row is written on its own; a write that never commits tells nobody', async () => {
    const created = sinon.stub(OutboxEvent, 'create').resolves([] as never);
    const got: ChangeEvent[] = [];
    const unsubscribe = controlPlaneSubscribers.subscribe((event) => got.push(event));
    const tx = { orgId: ORG, headRevision: 1, session: undefined, enlist: (write: () => Promise<void>) => write(), external: () => ({ value: undefined }) };
    try {
      await processChangeOutbox.enqueue(tx as never, changeEvent(ORG, 1));
      expect(created.firstCall.args[1]).to.deep.equal({});
      expect(got).to.deep.equal([]);
    } finally {
      unsubscribe();
    }
  });

  it('a holder that throws does not stop the others or the write (the process subscribers report nothing)', () => {
    const got: number[] = [];
    const first = controlPlaneSubscribers.subscribe(() => {
      throw new Error('a holder bug');
    });
    const second = controlPlaneSubscribers.subscribe((event) => got.push(event.revision));
    try {
      controlPlaneSubscribers.deliver(changeEvent(ORG, 2));
      expect(got).to.deep.equal([2]);
    } finally {
      first();
      second();
    }
  });

  it('listening is retried after 1 s, 2 s, 4 s … and never later than 30 s', async () => {
    const clock = sinon.useFakeTimers();
    try {
      for (const [attempt, ms] of [[1, 1_000], [2, 2_000], [3, 4_000], [6, 30_000], [40, 30_000]] as const) {
        let done = false;
        void changeListenBackoff(attempt).then(() => {
          done = true;
        });
        await clock.tickAsync(ms - 1);
        expect(done, `attempt ${String(attempt)} before ${String(ms)} ms`).to.equal(false);
        await clock.tickAsync(1);
        expect(done, `attempt ${String(attempt)} at ${String(ms)} ms`).to.equal(true);
      }
    } finally {
      clock.restore();
    }
  });
});

describe('CPD-6 the broker of control-plane-events', () => {
  let provider: ReturnType<typeof createFakeRedisProvider>;
  const saved = process.env[ENV_MESSAGE_BROKER];
  const appConfig = { redis: { host: 'redis', port: 6379 }, kafka: { brokers: ['kafka:9092'] } } as never;

  beforeEach(() => {
    provider = createFakeRedisProvider();
    stubGetRedisProvider(provider);
  });

  afterEach(() => {
    sinon.restore();
    if (saved === undefined) delete process.env[ENV_MESSAGE_BROKER];
    else process.env[ENV_MESSAGE_BROKER] = saved;
  });

  it('the group is this process alone: its host and pid, the same after a restart of the same container', () => {
    expect(changeEventsGroupId('node-7f9', 1)).to.equal('control-plane-events-node-7f9-1');
    expect(changeEventsGroupId('node-7f9', 1)).to.equal(changeEventsGroupId('node-7f9', 1));
    expect(changeEventsGroupId('node-7f9', 1)).to.not.equal(changeEventsGroupId('node-abc', 1));
    expect(changeEventsGroupId()).to.match(/^control-plane-events-.+-\d+$/);
  });

  it('MESSAGE_BROKER=redis: Redis Streams clients, the consumer a subscriber that holds no group (OPS1-4-12)', () => {
    process.env[ENV_MESSAGE_BROKER] = 'redis';
    const broker = controlPlaneEventBroker(appConfig, createMockLogger() as never, 'control-plane-events-h-1');
    expect([broker.producer.constructor.name, broker.consumer.constructor.name, broker.admin?.constructor.name]).to.deep.equal([
      'ConcreteRedisProducer',
      'RedisStreamsBroadcastConsumer',
      'RedisStreamsAdminService',
    ]);
  });

  it("MESSAGE_BROKER=kafka: Kafka clients, the consumer in this process's own group (OPS1-4-12)", () => {
    process.env[ENV_MESSAGE_BROKER] = 'kafka';
    const consumerOf = sinon.spy(Kafka.prototype, 'consumer');
    const broker = controlPlaneEventBroker(appConfig, createMockLogger() as never, 'control-plane-events-h-1');
    expect([broker.producer.constructor.name, broker.consumer.constructor.name, broker.admin?.constructor.name]).to.deep.equal([
      'ConcreteKafkaProducer',
      'ConcreteKafkaConsumer',
      'KafkaAdminService',
    ]);
    expect(consumerOf.firstCall.args[0].groupId).to.equal('control-plane-events-h-1');
  });
});
