import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { createMockLogger } from '../../helpers/mock-logger';
import * as runtimeBoot from '../../../src/libs/db/runtime-boot';
import {
  BasePgQueueConsumerConnection,
  BasePgQueueProducerConnection,
} from '../../../src/libs/services/pg-queue.service';
import { onPlatformEvent } from '../../../src/libs/platform-events';
import type { PlatformEvents } from '../../../src/libs/platform-events/events';
import { forgetMessageTypes } from '../../../src/libs/services/broker-events';

const SECRET = 'SEEDED-payload-text';

class TestProducer extends BasePgQueueProducerConnection {}
class TestConsumer extends BasePgQueueConsumerConnection {}

describe('PostgreSQL queue: platform events', () => {
  let published: PlatformEvents['message.published'][];
  let consumed: PlatformEvents['message.consumed'][];
  let stop: Array<() => void>;
  let tx: { query: sinon.SinonStub };

  beforeEach(() => {
    forgetMessageTypes();
    published = [];
    consumed = [];
    stop = [
      onPlatformEvent('message.published', (p) => {
        published.push(p);
      }),
      onPlatformEvent('message.consumed', (p) => {
        consumed.push(p);
      }),
    ];
    tx = { query: sinon.stub().resolves({ rows: [] }) };
    sinon.stub(runtimeBoot, 'ensurePgRuntime').returns({
      withUow: (_opts: unknown, fn: (t: unknown) => Promise<unknown>) => fn(tx),
    } as unknown as ReturnType<typeof runtimeBoot.ensurePgRuntime>);
  });
  afterEach(() => {
    stop.forEach((s) => s());
    sinon.restore();
  });

  const settle = (
    row: Record<string, unknown>,
    handler: () => Promise<void>,
  ): Promise<void> => {
    const consumer = new TestConsumer(
      { groupId: 'g' } as never,
      createMockLogger() as never,
    );
    return (
      consumer as unknown as {
        settle: (r: unknown, h: () => Promise<void>) => Promise<void>;
      }
    ).settle(row, handler);
  };
  const row = (attempts: number, value: string): Record<string, unknown> => ({
    topic: 't',
    message_id: '1',
    attempts,
    value,
    msg_key: null,
    headers: null,
  });
  const body = JSON.stringify({
    eventType: 'newRecord',
    payload: { text: SECRET },
    orgId: 'org-1',
  });

  it('announces every message of a published batch, with no payload or key', async () => {
    const producer = new TestProducer(createMockLogger() as never);
    await producer.publishBatch('record-events', [
      { key: SECRET, value: { eventType: 'newRecord', payload: SECRET } },
      { key: 'k2', value: { eventType: 'updateRecord' } },
    ]);
    expect(published.map((p) => [p.broker, p.topic, p.messageType, p.outcome])).to.deep.equal([
      ['postgres', 'record-events', 'newRecord', 'success'],
      ['postgres', 'record-events', 'updateRecord', 'success'],
    ]);
    expect(published[0]!.durationSeconds).to.be.at.least(0);
    expect(JSON.stringify(published)).to.not.include(SECRET);
  });

  it('announces failure when the queue does not take the batch', async () => {
    tx.query.rejects(new Error('database is away'));
    const producer = new TestProducer(createMockLogger() as never);
    let thrown: unknown;
    try {
      await producer.publish('record-events', { key: 'k', value: { eventType: 'newRecord' } });
    } catch (error) {
      thrown = error;
    }
    expect(thrown).to.be.instanceOf(Error);
    expect(published.map((p) => p.outcome)).to.deep.equal(['failure']);
  });

  it('announces nothing for an empty batch', async () => {
    await new TestProducer(createMockLogger() as never).publishBatch('t', []);
    expect(published).to.deep.equal([]);
  });

  it('announces a handled delivery as success', async () => {
    await settle(row(1, body), async () => undefined);
    expect(consumed.map((c) => [c.broker, c.topic, c.messageType, c.outcome])).to.deep.equal([
      ['postgres', 't', 'newRecord', 'success'],
    ]);
    expect(consumed[0]).to.not.have.property('lagMs');
    expect(JSON.stringify(consumed)).to.not.include(SECRET);
  });

  it('announces retry while attempts remain and failure on the last one', async () => {
    const previous = process.env.MAX_DELIVERY_ATTEMPTS;
    process.env.MAX_DELIVERY_ATTEMPTS = '3';
    try {
      const failing = async (): Promise<void> => {
        throw new Error('handler');
      };
      await settle(row(1, body), failing);
      await settle(row(3, body), failing);
    } finally {
      if (previous === undefined) delete process.env.MAX_DELIVERY_ATTEMPTS;
      else process.env.MAX_DELIVERY_ATTEMPTS = previous;
    }
    expect(consumed.map((c) => c.outcome)).to.deep.equal(['retry', 'failure']);
  });

  it('announces a body that is not JSON as unparseable, with no handler run', async () => {
    const handler = sinon.stub().resolves();
    await settle(row(1, '{not json'), handler);
    expect(handler.called).to.equal(false);
    expect(consumed.map((c) => [c.broker, c.messageType, c.outcome])).to.deep.equal([
      ['postgres', 'unknown', 'unparseable'],
    ]);
  });
});
