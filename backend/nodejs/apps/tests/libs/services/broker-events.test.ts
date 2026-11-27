import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { EnvelopeRejected } from '../../../src/libs/context/envelope';
import { onPlatformEvent } from '../../../src/libs/platform-events';
import type { PlatformEvents } from '../../../src/libs/platform-events/events';
import {
  consumeAnnounced,
  forgetMessageTypes,
  lagMsSince,
  MAX_MESSAGE_TYPES,
  messageTypeOf,
  parseAnnounced,
  streamEntryMs,
} from '../../../src/libs/services/broker-events';
import {
  BaseKafkaConsumerConnection,
  BaseKafkaProducerConnection,
} from '../../../src/libs/services/kafka.service';
import { DocStoreReadOnlyError } from '../../../src/libs/repositories/errors';
import * as fence from '../../../src/libs/repositories/fence';
import { KafkaConfig } from '../../../src/libs/types/kafka.types';
import { MessageBrokerType } from '../../../src/libs/types/messaging.types';
import { createMockLogger } from '../../helpers/mock-logger';

const SECRET = 'SEEDED-payload-text';

class TestProducer extends BaseKafkaProducerConnection {
  constructor(config: KafkaConfig, logger: any) {
    super(config, logger);
  }
}
class TestConsumer extends BaseKafkaConsumerConnection {
  constructor(config: KafkaConfig, logger: any) {
    super(config, logger);
  }
}

describe('broker platform events', () => {
  const config: KafkaConfig = {
    type: MessageBrokerType.KAFKA,
    clientId: 'c',
    brokers: ['localhost:9092'],
    groupId: 'g',
  };
  let published: PlatformEvents['message.published'][];
  let consumed: PlatformEvents['message.consumed'][];
  let unsubscribe: Array<() => void>;

  beforeEach(() => {
    forgetMessageTypes();
    published = [];
    consumed = [];
    unsubscribe = [
      onPlatformEvent('message.published', (payload) => {
        published.push(payload);
      }),
      onPlatformEvent('message.consumed', (payload) => {
        consumed.push(payload);
      }),
    ];
  });
  afterEach(() => {
    unsubscribe.forEach((off) => off());
    forgetMessageTypes();
    sinon.restore();
  });

  describe('messageTypeOf', () => {
    it('passes a short name and reads eventType before type', () => {
      expect(messageTypeOf({ eventType: 'newRecord' })).to.equal('newRecord');
      expect(messageTypeOf({ type: 'HEALTH_CHECK' })).to.equal('HEALTH_CHECK');
      expect(messageTypeOf({ eventType: 'a.b-c:d', type: 'x' })).to.equal(
        'a.b-c:d',
      );
    });

    it('answers unknown without a type and other for anything that is not a short name', () => {
      for (const value of [undefined, null, 'text', 7, [], {}, { eventType: 3 }, { eventType: '' }]) {
        expect(messageTypeOf(value)).to.equal('unknown');
      }
      for (const eventType of ['has space', 'x'.repeat(65), '9digit', 'a@b.co']) {
        expect(messageTypeOf({ eventType })).to.equal('other');
      }
    });

    it('caps the distinct types it lets through', () => {
      for (let i = 0; i < MAX_MESSAGE_TYPES; i++) {
        expect(messageTypeOf({ eventType: `t${i}` })).to.equal(`t${i}`);
      }
      expect(messageTypeOf({ eventType: 'oneMore' })).to.equal('other');
      expect(messageTypeOf({ eventType: 't0' })).to.equal('t0');
    });
  });

  describe('lag', () => {
    it('needs a real broker timestamp', () => {
      expect(lagMsSince(Date.now() - 1000)).to.be.within(1000, 30_000);
      expect(lagMsSince(String(Date.now() - 1000))).to.be.within(1000, 30_000);
      // a broker clock ahead of ours is no negative lag
      expect(lagMsSince(Date.now() + 60_000)).to.equal(0);
      for (const value of [undefined, null, '', 'soon', '-1', 0, -5, NaN, {}]) {
        expect(lagMsSince(value)).to.equal(undefined);
      }
    });

    it('reads the time a Redis stream entry id starts with', () => {
      expect(streamEntryMs('1700000000123-4')).to.equal('1700000000123');
      expect(streamEntryMs('abc')).to.equal(undefined);
    });
  });

  describe('parseAnnounced', () => {
    const facts = { broker: 'redis' as const, topic: 't' };

    it('returns the parsed body and announces nothing', () => {
      expect(parseAnnounced(facts, '{"eventType":"x"}')).to.deep.equal({
        eventType: 'x',
      });
      expect(consumed).to.deep.equal([]);
    });

    it('announces a body that is not JSON once and rethrows the parse error', () => {
      expect(() => parseAnnounced(facts, `{${SECRET}`)).to.throw(SyntaxError);
      expect(consumed).to.deep.equal([
        {
          broker: 'redis',
          topic: 't',
          messageType: 'unknown',
          outcome: 'unparseable',
          durationSeconds: 0,
        },
      ]);
    });
  });

  describe('consumeAnnounced', () => {
    const facts = { broker: 'redis' as const, topic: 't', value: { eventType: 'e' } };

    it('announces success and returns the result', async () => {
      expect(await consumeAnnounced(facts, async () => 5)).to.equal(5);
      expect(consumed).to.have.length(1);
      expect(consumed[0]).to.include({ broker: 'redis', topic: 't', messageType: 'e', outcome: 'success' });
      expect(consumed[0]!.durationSeconds).to.be.at.least(0);
      expect(consumed[0]).not.to.have.property('lagMs');
    });

    it('announces rejected, retry or failure and rethrows', async () => {
      const outcomes: Array<[unknown, string, ((e: unknown) => boolean)?]> = [
        [new EnvelopeRejected('stale'), 'rejected'],
        [new Error('again'), 'retry', () => true],
        [new Error('boom'), 'failure', () => false],
        [new Error('boom'), 'failure'],
      ];
      for (const [error, , retried] of outcomes) {
        let thrown: unknown;
        try {
          await consumeAnnounced({ ...facts, retried }, async () => {
            throw error;
          });
        } catch (e) {
          thrown = e;
        }
        expect(thrown).to.equal(error);
      }
      expect(consumed.map((c) => c.outcome)).to.deep.equal(outcomes.map((o) => o[1]));
    });
  });

  describe('Kafka producer', () => {
    let producer: TestProducer;
    let send: sinon.SinonStub;

    beforeEach(async () => {
      producer = new TestProducer(config, createMockLogger());
      send = sinon.stub().resolves();
      (producer as any).producer = {
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        send,
      };
      await producer.connect();
    });

    it('announces a published message with its topic and type, and no payload or key', async () => {
      await producer.publish('record-events', {
        key: 'key-SEEDED',
        value: { eventType: 'newRecord', payload: { text: SECRET } },
      });
      expect(published).to.have.length(1);
      expect(Object.keys(published[0]!).sort()).to.deep.equal(
        ['broker', 'durationSeconds', 'messageType', 'outcome', 'topic'],
      );
      expect(published[0]).to.include({
        broker: 'kafka',
        topic: 'record-events',
        messageType: 'newRecord',
        outcome: 'success',
      });
      expect(JSON.stringify(published)).not.to.include(SECRET);
      expect(JSON.stringify(published)).not.to.include('key-SEEDED');
    });

    it('announces every message of a batch, and failure when the broker does not take it', async () => {
      await producer.publishBatch<unknown>('t', [
        { key: 'a', value: { eventType: 'one' } },
        { key: 'b', value: 'plain text' },
      ]);
      send.rejects(new Error('broker down'));
      let failed = false;
      try {
        await producer.publish('t', { key: 'c', value: { eventType: 'two' } });
      } catch {
        failed = true;
      }
      expect(failed).to.equal(true);
      expect(published.map((p) => [p.messageType, p.outcome])).to.deep.equal([
        ['one', 'success'],
        ['unknown', 'success'],
        ['two', 'failure'],
      ]);
    });
  });

  describe('Kafka consumer', () => {
    let consumer: TestConsumer;
    let run: sinon.SinonStub;

    const deliver = async (
      message: Record<string, unknown>,
      handler: sinon.SinonStub,
    ): Promise<unknown> => {
      let thrown: unknown;
      run.callsFake(async ({ eachMessage }: any) => {
        try {
          await eachMessage({
            topic: 'record-events',
            partition: 0,
            message: { key: Buffer.from('key-SEEDED'), headers: {}, ...message },
            pause: () => () => undefined,
          });
        } catch (e) {
          thrown = e;
        }
      });
      await consumer.consume(handler);
      return thrown;
    };
    const body = (value: unknown): Buffer => Buffer.from(JSON.stringify(value));

    beforeEach(async () => {
      consumer = new TestConsumer(config, createMockLogger());
      run = sinon.stub().resolves();
      (consumer as any).consumer = {
        connect: sinon.stub().resolves(),
        disconnect: sinon.stub().resolves(),
        subscribe: sinon.stub().resolves(),
        run,
        pause: sinon.stub(),
        resume: sinon.stub(),
      };
      sinon.stub(fence, 'mongoStoreMoving').resolves(false);
      await consumer.connect();
    });

    it('announces a handled message with the lag the broker timestamp gives', async () => {
      await deliver(
        {
          value: body({ eventType: 'newRecord', payload: { text: SECRET } }),
          timestamp: String(Date.now() - 4000),
        },
        sinon.stub().resolves(),
      );
      expect(consumed).to.have.length(1);
      expect(consumed[0]).to.include({
        broker: 'kafka',
        topic: 'record-events',
        messageType: 'newRecord',
        outcome: 'success',
      });
      expect(consumed[0]!.lagMs).to.be.within(4000, 60_000);
      expect(JSON.stringify(consumed)).not.to.include(SECRET);
      expect(JSON.stringify(consumed)).not.to.include('key-SEEDED');
    });

    it('announces a handler error as failure and gives no lag without a timestamp', async () => {
      const thrown = await deliver(
        { value: body({ eventType: 'newRecord' }) },
        sinon.stub().rejects(new Error('handler failed')),
      );
      expect(thrown).to.equal(undefined);
      expect(consumed.map((c) => c.outcome)).to.deep.equal(['failure']);
      expect(consumed[0]).not.to.have.property('lagMs');
    });

    it('announces retry when the message will be delivered again', async () => {
      const thrown = await deliver(
        { value: body({ eventType: 'newRecord' }) },
        sinon.stub().rejects(new DocStoreReadOnlyError()),
      );
      expect(thrown).to.be.instanceOf(DocStoreReadOnlyError);
      expect(consumed.map((c) => c.outcome)).to.deep.equal(['retry']);
    });

    it('announces a body that is not JSON as unparseable, with its lag and no handler run', async () => {
      const handler = sinon.stub().resolves();
      const thrown = await deliver(
        {
          value: Buffer.from(`{not json ${SECRET}`),
          timestamp: String(Date.now() - 2000),
        },
        handler,
      );
      expect(thrown).to.equal(undefined);
      expect(handler.called).to.equal(false);
      expect(consumed).to.have.length(1);
      expect(consumed[0]).to.include({
        broker: 'kafka',
        topic: 'record-events',
        messageType: 'unknown',
        outcome: 'unparseable',
        durationSeconds: 0,
      });
      expect(consumed[0]!.lagMs).to.be.within(2000, 60_000);
      expect(JSON.stringify(consumed)).not.to.include(SECRET);
    });

    it('announces a message with no body as unparseable', async () => {
      await deliver({ value: null }, sinon.stub().resolves());
      expect(consumed.map((c) => [c.messageType, c.outcome])).to.deep.equal([
        ['unknown', 'unparseable'],
      ]);
      expect(consumed[0]).not.to.have.property('lagMs');
    });
  });
});
