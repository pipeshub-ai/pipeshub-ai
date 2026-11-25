/**
 * OPS1 item 4 on real Redis Streams (REDIS_IT_URL), through the libs
 * consumers a deployment uses with `MESSAGE_BROKER=redis`: a fan-out
 * subscriber leaves no consumer group behind, a work-queue consumer takes
 * over what a dead one never acknowledged, and the leftover groups of earlier
 * builds are removed only once nothing reads them. Every stream is this
 * run's own and is deleted afterwards.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { randomUUID } from 'node:crypto';
import { Redis } from 'ioredis';
import {
  createBroadcastMessageConsumer,
  createMessageConsumerForBrokerType,
  createMessageProducerForBrokerType,
} from '../../../src/libs/services/message-broker.factory';
import { RedisStreamsAdminService } from '../../../src/libs/services/redis-streams.service';
import {
  LEFTOVER_GROUPS,
  LeftoverGroupSweeper,
} from '../../../src/modules/agent-harness/application/leftover-group-sweeper';
import {
  IMessageConsumer,
  IMessageProducer,
  MessageBrokerType,
  RedisBrokerConfig,
  StreamMessage,
} from '../../../src/libs/types/messaging.types';
import { createMockLogger, MockLogger } from '../../helpers/mock-logger';
import { redisItUrl } from './support/redis-it';

const RUN = randomUUID().slice(0, 8);
const BLOCK_MS = 50;

const brokerConfig = (extra: Partial<RedisBrokerConfig> = {}): RedisBrokerConfig => {
  const url = redisItUrl();
  return { type: MessageBrokerType.REDIS, host: url.hostname, port: Number(url.port || 6379), blockMs: BLOCK_MS, ...extra };
};

/** Polls a condition on the server; every wait here is for Redis or a consumer loop, never a fixed delay. */
async function until(what: string, condition: () => Promise<boolean> | boolean): Promise<void> {
  const deadline = Date.now() + 20_000;
  while (!(await condition())) {
    if (Date.now() > deadline) throw new Error(`not reached: ${what}`);
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
}

describe('OPS1 item 4: broker subscriptions on Redis Streams', function () {
  this.timeout(60_000);
  let admin: Redis;
  let producer: IMessageProducer;
  let logger: MockLogger;
  const topics: string[] = [];
  const consumers: IMessageConsumer[] = [];
  const held: Array<() => void> = [];

  const topic = (label: string): string => {
    const name = `ops1f4-${RUN}-${label}`;
    topics.push(name);
    return name;
  };
  const publish = (to: string, value: string): Promise<void> => producer.publish(to, { key: 'k', value });
  const groupsOf = async (of: string): Promise<string[]> =>
    ((await admin.xinfo('GROUPS', of)) as unknown[][]).map((group) => String(group[1]));
  const pendingOf = async (of: string, group: string): Promise<number> =>
    Number(((await admin.xpending(of, group)) as unknown[])[0]);

  async function subscriber(to: string, handler: (value: string) => Promise<void> | void): Promise<IMessageConsumer> {
    const consumer = createBroadcastMessageConsumer(
      { type: MessageBrokerType.REDIS, redis: brokerConfig() },
      logger as never,
      `ops1f4-${RUN}-${randomUUID().slice(0, 8)}`,
    );
    consumers.push(consumer);
    await consumer.connect();
    await consumer.subscribe([to], false);
    await consumer.consume<string>(async (message) => handler(message.value));
    return consumer;
  }

  async function worker(
    to: string,
    group: string,
    handler: (message: StreamMessage<string>) => Promise<void>,
    extra: Partial<RedisBrokerConfig> = {},
  ): Promise<IMessageConsumer> {
    // One consumer name for every process of a role, as the product's work-queue consumers have.
    const config = brokerConfig({ clientId: 'worker', groupId: group, ...extra });
    const consumer = createMessageConsumerForBrokerType(MessageBrokerType.REDIS, undefined, config, logger as never);
    consumers.push(consumer);
    await consumer.connect();
    await consumer.subscribe([to], false);
    await consumer.consume<string>(handler);
    return consumer;
  }

  /** A handler call that does not return until the test ends: a process that stopped where it stood. */
  const neverReturns = (): Promise<void> => new Promise<void>((resolve) => held.push(resolve));

  before(async () => {
    const url = redisItUrl();
    admin = new Redis({ host: url.hostname, port: Number(url.port || 6379) });
    producer = createMessageProducerForBrokerType(MessageBrokerType.REDIS, undefined, brokerConfig(), createMockLogger() as never);
    await producer.connect();
  });

  beforeEach(() => {
    logger = createMockLogger();
  });

  afterEach(async () => {
    for (const release of held.splice(0)) release();
    for (const consumer of consumers.splice(0)) await consumer.disconnect();
  });

  after(async () => {
    await producer.disconnect();
    if (topics.length > 0) await admin.del(...topics);
    await admin.quit();
  });

  describe('fan-out', () => {
    it('OPS1-4-1: 20 pods subscribe; half stop cleanly, half are abandoned: the topic never has a consumer group', async () => {
      const events = topic('deploys');
      await publish(events, 'made');
      const seen: string[] = [];
      const abandoned: IMessageConsumer[] = [];
      for (let pod = 0; pod < 20; pod += 1) {
        const consumer = await subscriber(events, (value) => void seen.push(`${String(pod)}:${value}`));
        if (pod % 2 === 0) await consumer.disconnect();
        else abandoned.push(consumer);
        expect(await groupsOf(events)).to.deep.equal([]);
      }

      await publish(events, 'after');
      await until('every pod still running saw the event', () => seen.length === abandoned.length);
      expect(await groupsOf(events)).to.deep.equal([]);
    });

    it('OPS1-4-2: each subscriber sees every message published after it subscribed, in order, and none from before', async () => {
      const events = topic('fanout');
      await publish(events, 'before');
      const got: Record<string, string[]> = { a: [], b: [] };
      await subscriber(events, (value) => void got['a']?.push(value));
      await subscriber(events, (value) => void got['b']?.push(value));

      for (const value of ['1', '2', '3']) await publish(events, value);

      await until('both saw three', () => got['a']?.length === 3 && got['b']?.length === 3);
      expect(got).to.deep.equal({ a: ['1', '2', '3'], b: ['1', '2', '3'] });
    });

    it('OPS1-4-3: a subscriber that stops reading while the leftover sweep runs gets everything when it reads again', async () => {
      const events = topic('paused');
      await publish(events, 'made');
      const got: string[] = [];
      let resume: () => void = () => undefined;
      const paused = new Promise<void>((resolve) => {
        resume = resolve;
      });
      await subscriber(events, async (value) => {
        got.push(value);
        if (value === '1') await paused;
      });
      await publish(events, '1');
      await until('the subscriber is stopped in its handler', () => got.length === 1);
      for (const value of ['2', '3', '4']) await publish(events, value);

      // The sweep at its most eager: any group of this topic, dead for no time at all.
      const sweep = new RedisStreamsAdminService(brokerConfig(), logger as never);
      const anything = { name: /.*/, consumer: (group: string): string => group };
      expect((await sweep.removeIdleGroups({ topic: events, formats: [anything], idleMs: 0 })).removed).to.deep.equal([]);
      resume();

      await until('it caught up', () => got.length === 4);
      expect(got).to.deep.equal(['1', '2', '3', '4']);
    });

    it('OPS1-4-4: a subscriber whose connection the server drops gets what was published meanwhile, once', async () => {
      const events = topic('dropped');
      await publish(events, 'made');
      const got: string[] = [];
      const consumer = await subscriber(events, (value) => void got.push(value));
      // The connection it reads on, named by the server so the test can drop exactly that one.
      const reading = (consumer as unknown as { redis: Redis }).redis;
      const id = await reading.client('ID');
      await publish(events, '1');
      await until('first arrived', () => got.length === 1);

      await admin.client('KILL', 'ID', id);
      for (const value of ['2', '3']) await publish(events, value);

      await until('the rest arrived', () => got.length >= 3);
      expect(got).to.deep.equal(['1', '2', '3']);
    });
  });

  describe('work queue', () => {
    it('OPS1-4-5: what a dead consumer took and never acknowledged goes once to one already running', async () => {
      const jobs = topic('jobs');
      const group = `ops1f4-${RUN}-jobs-group`;
      const dead: string[] = [];
      await worker(jobs, group, async (message) => {
        dead.push(message.value);
        await neverReturns();
      });
      await publish(jobs, 'job-1');
      await until('the first consumer took it', () => dead.length === 1);

      const handled: string[] = [];
      await worker(jobs, group, async (message) => void handled.push(message.value), { claimMinIdleMs: 300 });

      await until('the live consumer handled it', () => handled.length === 1);
      await until('and acknowledged it', async () => (await pendingOf(jobs, group)) === 0);
      await publish(jobs, 'job-2');
      await until('it went on', () => handled.length === 2);
      expect(handled).to.deep.equal(['job-1', 'job-2']);
    });

    it('OPS1-4-6: a message whose handler always fails is tried maxDeliveries times, then set aside', async () => {
      const jobs = topic('poison');
      const group = `ops1f4-${RUN}-poison-group`;
      const tries: string[] = [];
      await worker(
        jobs,
        group,
        async (message) => {
          tries.push(message.value);
          if (message.value === 'poison') throw new Error('cannot be handled');
        },
        { claimMinIdleMs: 100, maxDeliveries: 3 },
      );
      await publish(jobs, 'poison');
      await publish(jobs, 'good');

      await until('nothing is pending', async () => tries.includes('good') && (await pendingOf(jobs, group)) === 0);
      expect(tries.filter((value) => value === 'poison')).to.have.length(3);
      expect(tries.filter((value) => value === 'good')).to.have.length(1);
      const setAside = logger.error.getCalls().filter((call) => String(call.args[0]).startsWith('Set aside'));
      expect(setAside.map((call) => (call.args[1] as { deliveries: number }).deliveries)).to.deep.equal([3]);
    });
  });

  describe('leftover groups of earlier builds', () => {
    it('OPS1-4-7: an earlier build\'s group is removed only once every consumer is idle past the bound; a live one stays', async () => {
      const events = topic('leftover');
      const idleMs = 2_000;
      const named = (label: string): string => `control-plane-events-${label}-1`;
      const ours = { name: /^control-plane-events-.+-1$/, consumer: (group: string): string => group };
      await publish(events, 'made');
      await worker(events, named('live'), async () => undefined, { clientId: named('live') });
      await admin.xgroup('CREATE', events, named('dead'), '$');
      await admin.xreadgroup('GROUP', named('dead'), named('dead'), 'COUNT', 1, 'STREAMS', events, '>');
      const sweep = new RedisStreamsAdminService(brokerConfig(), logger as never);
      const idleOf = async (group: string): Promise<number> => {
        const [consumer] = (await admin.xinfo('CONSUMERS', events, group)) as unknown[][];
        return Number(consumer?.[(consumer?.indexOf('idle') ?? -1) + 1]);
      };

      expect((await sweep.removeIdleGroups({ topic: events, formats: [ours], idleMs })).removed, 'nothing is dead yet').to.deep.equal([]);
      await until('the stopped consumer is idle past the bound', async () => (await idleOf(named('dead'))) >= idleMs);

      expect((await sweep.removeIdleGroups({ topic: events, formats: [ours], idleMs })).removed).to.deep.equal([named('dead')]);
      expect(await idleOf(named('live')), 'a consumer that is reading reports itself').to.be.below(idleMs);
      expect(await groupsOf(events)).to.deep.equal([named('live')]);
    });

    it('OPS1-4-14: only a group provably an earlier build\'s is removed; an operator\'s group of the same shape survives, named in the log', async () => {
      const events = topic('provable');
      const idleMs = 1_500;
      await publish(events, 'made');
      const readOnce = async (group: string, consumer: string | null): Promise<void> => {
        await admin.xgroup('CREATE', events, group, '$');
        if (consumer !== null) await admin.xreadgroup('GROUP', group, consumer, 'COUNT', 1, 'STREAMS', events, '>');
      };
      // An earlier Node: its consumer is named as its group. An earlier Python process: the role's client name.
      await readOnce('control-plane-events-pipeshub-7d9f-abcde-41', 'control-plane-events-pipeshub-7d9f-abcde-41');
      await readOnce('control_plane_events_group-4242-0a1b2c3d', 'control_plane_events_client');
      // The operator's: the same shape of name, read by a consumer of its own; and one nobody ever read.
      await readOnce('control-plane-events-x-2', 'ops-audit');
      await readOnce('control-plane-events-y-3', null);
      const swept = createMockLogger();
      const sweeper = new LeftoverGroupSweeper({
        admin: new RedisStreamsAdminService(brokerConfig(), logger as never),
        lease: { tryAcquire: async () => true, release: async () => undefined },
        holderId: 'ops1',
        logger: swept,
        groups: LEFTOVER_GROUPS.filter((group) => group.topic === 'control-plane-events').map((group) => ({ ...group, topic: events })),
        idleMs,
      });
      await new Promise((resolve) => setTimeout(resolve, idleMs + 200));

      expect((await sweeper.tick()).sort()).to.deep.equal(
        ['control-plane-events-pipeshub-7d9f-abcde-41', 'control_plane_events_group-4242-0a1b2c3d'].sort(),
      );
      expect((await groupsOf(events)).sort()).to.deep.equal(['control-plane-events-x-2', 'control-plane-events-y-3']);
      const left = swept.info.getCalls().filter((call) => call.args[0] === 'broker.leftover_groups_left');
      expect(left.map((call) => (call.args[1] as { groups: string[] }).groups.sort())).to.deep.equal([
        ['control-plane-events-x-2', 'control-plane-events-y-3'],
      ]);
      await sweeper.tick();
      expect(swept.info.getCalls().filter((call) => call.args[0] === 'broker.leftover_groups_left'), 'named once').to.have.length(1);
    });

    it('a topic that does not exist has nothing to remove', async () => {
      const sweep = new RedisStreamsAdminService(brokerConfig(), logger as never);
      expect(await sweep.removeIdleGroups({ topic: `ops1f4-${RUN}-none`, formats: [], idleMs: 0 })).to.deep.equal({ removed: [], left: [] });
    });
  });
});
