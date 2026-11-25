/**
 * JRN-15 (gap JRN-G3), Redis Streams leg: an admitted run's `run_ready` goes
 * from the Mongo outbox (MONGO_IT_URI) through OutboxDispatcher and the real
 * Redis Streams producer (REDIS_IT_URL) onto `agent-run-events`, keyed by run
 * and carrying identifiers only. The entry is decoded the way the Python
 * consumer decodes it. The stream lives in a Redis database of its own
 * (`REDIS_IT_STREAMS_DB`, default 13) so no other lane's worker sees the
 * hint, and only this test's entries are deleted afterwards.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { randomUUID } from 'node:crypto';
import { Redis } from 'ioredis';
import { OutboxDispatcher } from '../../../src/libs/services/outbox/outbox.dispatcher';
import { OutboxEvent } from '../../../src/libs/repositories/outbox/schema/outbox.schema';
import { createMessageProducerForBrokerType } from '../../../src/libs/services/message-broker.factory';
import { Logger } from '../../../src/libs/services/logger.service';
import { BrokerTopic, IMessageProducer, MessageBrokerType } from '../../../src/libs/types/messaging.types';
import { AGENT_RUN_DISPATCH_EVENT } from '../../../src/modules/agent-harness/adapters/messaging/dispatch-envelope';
import { contractClock, createContractHarness } from './support/execution-store-contract';
import { connectHarnessMongo, disconnectHarnessMongo, mongoBackend, resetHarnessMongo } from './support/harness-mongo';
import { HARNESS_ENABLED } from './support/rollout';
import { redisItUrl } from './support/redis-it';

const STREAMS_DB = Number(process.env.REDIS_IT_STREAMS_DB ?? 13);

type Entry = [id: string, fields: string[]];

/** `app/services/messaging/redis_streams/consumer.py`: the value may be JSON of a JSON string. */
const decodeValue = (value: string): Record<string, unknown> => {
  let raw: unknown = JSON.parse(value);
  if (typeof raw === 'string') raw = JSON.parse(raw);
  return raw as Record<string, unknown>;
};

const fieldsOf = (fields: string[]): Record<string, string> =>
  Object.fromEntries(Array.from({ length: fields.length / 2 }, (_, i) => [fields[2 * i]!, fields[2 * i + 1]!]));

describe('outbox relay over real Redis Streams (JRN-15 itest)', function () {
  this.timeout(60_000);
  const url = redisItUrl();
  let producer: IMessageProducer;
  let reader: Redis;
  const written: string[] = [];

  before(connectHarnessMongo);
  before(async () => {
    producer = createMessageProducerForBrokerType(
      MessageBrokerType.REDIS,
      undefined,
      { type: MessageBrokerType.REDIS, host: url.hostname, port: Number(url.port || 6379), db: STREAMS_DB },
      Logger.getInstance({ service: 'outbox-relay-itest' }),
    );
    reader = new Redis({ host: url.hostname, port: Number(url.port || 6379), db: STREAMS_DB });
  });

  after(async () => {
    if (written.length > 0) await reader.xdel(BrokerTopic.AGENT_RUN_EVENTS, ...written);
    if ((await reader.xlen(BrokerTopic.AGENT_RUN_EVENTS)) === 0) await reader.del(BrokerTopic.AGENT_RUN_EVENTS);
    await producer.disconnect();
    reader.disconnect();
    await disconnectHarnessMongo();
  });

  beforeEach(resetHarnessMongo);

  it('JRN-15: an admitted run reaches the stream as run_ready, keyed by run, with identifiers only', async () => {
    const clock = contractClock();
    const h = createContractHarness(mongoBackend({ clock }), clock);
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
    const secret = `confidential question ${randomUUID()}`;
    const run = await h.admit({
      input: { query: secret, recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
    });

    const dispatcher = new OutboxDispatcher(producer, Logger.getInstance({ service: 'outbox-relay-itest' }));
    expect(await dispatcher.drain()).to.equal(1);
    expect((await OutboxEvent.findOne({ orderingKey: run.run.runId }).lean())?.status).to.equal('published');

    const entries = (await reader.xrevrange(BrokerTopic.AGENT_RUN_EVENTS, '+', '-', 'COUNT', 200)) as Entry[];
    const ours = entries.filter(([, fields]) => fieldsOf(fields)['key'] === run.run.runId);
    written.push(...ours.map(([id]) => id));
    expect(ours).to.have.length(1);
    const fields = fieldsOf(ours[0]![1]);
    expect(JSON.stringify(fields)).to.not.include(secret);
    const event = decodeValue(fields['value']!);
    expect(event['eventType']).to.equal(AGENT_RUN_DISPATCH_EVENT);
    const payload = event['payload'] as Record<string, unknown>;
    expect(Object.keys(payload).sort()).to.deep.equal(['commandId', 'createdAt', 'kind', 'orgId', 'runId', 'schemaVersion']);
    expect(payload).to.include({ kind: 'run_ready', runId: run.run.runId, orgId: run.run.orgId, schemaVersion: 1 });
    expect(JSON.parse(fields['headers']!)).to.include({ eventType: AGENT_RUN_DISPATCH_EVENT });

    expect(await dispatcher.drain()).to.equal(0);
  });
});
