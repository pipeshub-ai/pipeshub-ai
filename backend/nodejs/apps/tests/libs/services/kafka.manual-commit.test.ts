import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Kafka } from 'kafkajs';
import { createMockLogger, MockLogger } from '../../helpers/mock-logger';
import { stubGetRedisProvider, createFakeRedisProvider } from '../../helpers/fake-redis-provider';
import { KafkaConfig } from '../../../src/libs/types/kafka.types';
import { MessageBrokerType } from '../../../src/libs/types/messaging.types';
import { BaseKafkaConsumerConnection } from '../../../src/libs/services/kafka.service';
import {
  createMailMessageConsumer,
  createNotificationMessageConsumer,
} from '../../../src/libs/services/message-broker.factory';

class TestKafkaConsumer extends BaseKafkaConsumerConnection {}

describe('Kafka consumer: opt-in manual commit', () => {
  const config: KafkaConfig = {
    type: MessageBrokerType.KAFKA,
    clientId: 'test-client',
    brokers: ['localhost:9092'],
    groupId: 'test-group',
  };
  const kafkaMessage = (value: string | null, offset = '41') => ({
    topic: 'notification',
    partition: 2,
    message: { key: Buffer.from('k'), value: value === null ? null : Buffer.from(value), offset },
  });

  let logger: MockLogger;
  let kafkaConsumer: { connect: sinon.SinonStub; run: sinon.SinonStub; commitOffsets: sinon.SinonStub };

  // Starts consuming and returns the eachMessage callback kafkajs would call.
  async function consumeWith(
    handler: sinon.SinonStub,
    manualCommit?: boolean,
  ): Promise<(payload: ReturnType<typeof kafkaMessage>) => Promise<void>> {
    const consumer = new TestKafkaConsumer({ ...config, manualCommit }, logger as never);
    (consumer as unknown as { consumer: typeof kafkaConsumer }).consumer = kafkaConsumer;
    await consumer.consume(handler);
    return kafkaConsumer.run.firstCall.args[0].eachMessage;
  }

  beforeEach(() => {
    logger = createMockLogger();
    kafkaConsumer = {
      connect: sinon.stub().resolves(),
      run: sinon.stub().resolves(),
      commitOffsets: sinon.stub().resolves(),
    };
  });

  afterEach(() => {
    sinon.restore();
    delete process.env.MESSAGE_BROKER;
  });

  describe('when opted in', () => {
    it('turns auto-commit off and commits the next offset once the handler returns', async () => {
      const handler = sinon.stub().resolves();
      const eachMessage = await consumeWith(handler, true);

      await eachMessage(kafkaMessage(JSON.stringify({ n: 1 })));

      expect(kafkaConsumer.run.firstCall.args[0].autoCommit).to.equal(false);
      expect(handler.calledOnceWith({ key: 'k', value: { n: 1 } })).to.be.true;
      expect(kafkaConsumer.commitOffsets.calledOnceWith([{ topic: 'notification', partition: 2, offset: '42' }])).to.be
        .true;
    });

    it('leaves the offset uncommitted when the handler throws, so kafkajs delivers the message again', async () => {
      const eachMessage = await consumeWith(sinon.stub().rejects(new Error('outbox down')), true);

      const outcome = await eachMessage(kafkaMessage(JSON.stringify({ n: 1 }))).then(
        () => 'committed',
        (error: Error) => error.message,
      );

      expect(outcome).to.equal('outbox down');
      expect(kafkaConsumer.commitOffsets.called).to.be.false;
    });

    it('skips and commits a message it cannot read, so it never blocks the partition', async () => {
      const handler = sinon.stub().resolves();
      const eachMessage = await consumeWith(handler, true);

      await eachMessage(kafkaMessage('not-json{{{'));
      await eachMessage(kafkaMessage(null, '42'));

      expect(handler.called).to.be.false;
      expect(logger.error.calledTwice).to.be.true;
      expect(kafkaConsumer.commitOffsets.args).to.deep.equal([
        [[{ topic: 'notification', partition: 2, offset: '42' }]],
        [[{ topic: 'notification', partition: 2, offset: '43' }]],
      ]);
    });
  });

  describe('by default', () => {
    it('keeps auto-commit on, and logs and moves past a handler that throws', async () => {
      const eachMessage = await consumeWith(sinon.stub().rejects(new Error('handler error')));

      await eachMessage(kafkaMessage(JSON.stringify({ n: 1 })));

      expect(kafkaConsumer.run.firstCall.args[0].autoCommit).to.equal(true);
      expect(logger.error.calledOnce).to.be.true;
      expect(kafkaConsumer.commitOffsets.called).to.be.false;
    });
  });

  describe('who opts in', () => {
    const appConfig = {
      kafka: { brokers: ['localhost:9092'] },
      redis: { host: 'localhost', port: 6379 },
    } as never;

    it('the notification consumer, with a session that outlasts its 30 s retry; the mail consumer does not', () => {
      process.env.MESSAGE_BROKER = 'kafka';
      const kafkaOptions = sinon.spy(Kafka.prototype, 'consumer');

      const notifications = createNotificationMessageConsumer(appConfig, logger as never);
      const mail = createMailMessageConsumer(appConfig, logger as never);

      expect((notifications as unknown as { config: KafkaConfig }).config.manualCommit).to.equal(true);
      expect((mail as unknown as { config: KafkaConfig }).config.manualCommit).to.equal(undefined);
      const options = kafkaOptions.firstCall.args[0]!;
      expect(options.groupId).to.equal('notification-consumer-group');
      expect(options.sessionTimeout).to.be.greaterThan(30_000);
      expect(options.rebalanceTimeout).to.be.greaterThan(30_000);
    });

    it('on Redis Streams, the notification consumer is not reclaimed mid-retry and reads one entry at a time', () => {
      process.env.MESSAGE_BROKER = 'redis';
      stubGetRedisProvider(createFakeRedisProvider());

      const consumer = createNotificationMessageConsumer(appConfig, logger as never) as unknown as {
        config: { claimMinIdleMs: number };
        count: number;
      };

      expect(consumer.config.claimMinIdleMs).to.be.greaterThan(30_000);
      expect(consumer.count).to.equal(1);
    });
  });
});
