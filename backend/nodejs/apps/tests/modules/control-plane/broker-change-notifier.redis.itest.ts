/**
 * CPD-6 `BrokerChangeNotifier` on real Redis Streams (REDIS_IT_URL): the
 * notifier contract (CN-1..3) and per-org order (NTF-03) through the libs
 * producer and consumer the deployment uses with `MESSAGE_BROKER=redis`.
 * The stream is the deployment's (`control-plane-events`), so each case
 * uses org ids of its own and removes its entries afterwards.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { randomUUID } from 'node:crypto';
import { Redis } from 'ioredis';
import {
  createBroadcastMessageConsumer,
  createMessageProducerForBrokerType,
} from '../../../src/libs/services/message-broker.factory';
import { MessageBrokerType, type RedisBrokerConfig } from '../../../src/libs/types/messaging.types';
import {
  BrokerChangeNotifier,
  CONTROL_PLANE_EVENTS_TOPIC,
  type ChangeEvent,
  type ChangeNotifier,
} from '../../../src/modules/control-plane';
import { createMockLogger } from '../../helpers/mock-logger';
import { redisItUrl } from '../agent-harness/support/redis-it';
import { ORG_A, ORG_B } from './support/cp-objects';
import { changeEvent, describeChangeNotifierContract } from './support/notifier-contract';

const RUN = randomUUID().slice(0, 8);
const MARKER_ORG = `settle-${RUN}`;
const notifiers: BrokerChangeNotifier[] = [];

function config(groupId: string): RedisBrokerConfig {
  const url = redisItUrl();
  return { type: MessageBrokerType.REDIS, host: url.hostname, port: Number(url.port || 6379), clientId: groupId, groupId };
}

async function listening(label: string): Promise<BrokerChangeNotifier> {
  const groupId = `pxh-it-cpd6-${RUN}-${label}-${randomUUID().slice(0, 8)}`;
  const logger = createMockLogger() as never;
  const notifier = new BrokerChangeNotifier({
    producer: createMessageProducerForBrokerType(MessageBrokerType.REDIS, undefined, config(groupId), logger),
    consumer: createBroadcastMessageConsumer({ type: MessageBrokerType.REDIS, redis: config(groupId) }, logger, groupId),
  });
  await notifier.listen();
  notifiers.push(notifier);
  return notifier;
}

/** Resolves once `notifier`'s subscribers have seen `marker`, which is published last: everything before it has arrived. */
async function caughtUp(notifier: ChangeNotifier, marker: ChangeEvent): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    const unsubscribe = notifier.subscribe((event) => {
      if (event.orgId !== marker.orgId || event.revision !== marker.revision) return;
      unsubscribe();
      resolve();
    });
    notifier.publish(marker).catch(reject);
  });
}

describe('CPD-6 BrokerChangeNotifier on Redis Streams', function () {
  this.timeout(60_000);
  let admin: Redis;
  let startedAt: string;

  before(() => {
    startedAt = `${String(Date.now())}-0`;
    const url = redisItUrl();
    admin = new Redis({ host: url.hostname, port: Number(url.port || 6379) });
  });

  afterEach(async () => {
    for (const notifier of notifiers.splice(0)) await notifier.stop();
  });

  after(async () => {
    const left = ((await admin.xinfo('GROUPS', CONTROL_PLANE_EVENTS_TOPIC)) as unknown[][]).map((group) => String(group[1]));
    expect(left.filter((name) => name.includes(RUN)), 'a subscriber leaves no consumer group').to.deep.equal([]);
    // This run's entries: those naming its run id, and the contract's fixed orgs published since it started.
    const entries = await admin.xrange(CONTROL_PLANE_EVENTS_TOPIC, startedAt, '+');
    const mine = entries
      .filter(([, fields]) => [ORG_A, ORG_B].includes(fields[1] ?? '') || fields.some((field) => field.includes(RUN)))
      .map(([id]) => id);
    if (mine.length > 0) await admin.xdel(CONTROL_PLANE_EVENTS_TOPIC, ...mine);
    await admin.quit();
  });

  // The contract's orgs are fixed names, so its cases are told apart from other runs' events by the marker's org.
  let sequence = 0;
  describeChangeNotifierContract('Redis Streams', async () => {
    const real = await listening('contract');
    // A shared stream has no "everything arrived" signal but a marker event, which the suite's own subscribers must not see.
    const notifier: ChangeNotifier = {
      publish: (event) => real.publish(event),
      subscribe: (handler) => real.subscribe((event) => (event.orgId === MARKER_ORG ? undefined : handler(event))),
    };
    return {
      notifier,
      settled: () => caughtUp(real, changeEvent(MARKER_ORG, (sequence += 1))),
    };
  });

  it('NTF-03: three writes of one org reach two processes in revision order, each a subscriber of its own', async () => {
    const org = `org-${RUN}`;
    const other = `other-${RUN}`;
    const first = await listening('node-a');
    const second = await listening('node-b');
    const got: Record<string, string[]> = { a: [], b: [] };
    first.subscribe((event) => got['a']?.push(`${event.orgId}@${String(event.revision)}`));
    second.subscribe((event) => got['b']?.push(`${event.orgId}@${String(event.revision)}`));

    for (const event of [changeEvent(org, 1), changeEvent(other, 4), changeEvent(org, 2), changeEvent(org, 3)]) {
      await first.publish(event);
    }
    const done = changeEvent(MARKER_ORG, 1_000);
    await Promise.all([caughtUp(first, done), caughtUp(second, { ...done, revision: 1_001 })]);

    for (const seen of Object.values(got)) {
      expect(seen.filter((entry) => entry.startsWith(org))).to.deep.equal([`${org}@1`, `${org}@2`, `${org}@3`]);
      expect(seen.filter((entry) => entry.startsWith(other))).to.deep.equal([`${other}@4`]);
    }
  });

  it('the stream entry is `key` = the org and `value` = the envelope {eventType, payload} as a JSON string, JSON-encoded once more', async () => {
    const org = `wire-${RUN}`;
    const notifier = await listening('wire');
    const event = changeEvent(org, 6);

    await notifier.publish(event);

    const entries = await admin.xrevrange(CONTROL_PLANE_EVENTS_TOPIC, '+', '-', 'COUNT', 50);
    const fields = entries.map(([, f]) => f).find((f) => f[1] === org);
    expect(fields?.slice(0, 2)).to.deep.equal(['key', org]);
    expect(fields?.[2]).to.equal('value');
    expect(JSON.parse(JSON.parse(fields?.[3] ?? 'null') as string)).to.deep.equal({ eventType: 'cp.changed', payload: event });
    expect(fields, 'no headers member').to.have.length(4);
    expect([ORG_A, ORG_B]).to.not.include(org);
  });
});
