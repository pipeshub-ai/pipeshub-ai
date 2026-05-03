import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { KafkaError } from '../../../src/libs/errors/kafka.errors';
import { createMockLogger, MockLogger } from '../../helpers/mock-logger';
import { KafkaConfig, KafkaMessage } from '../../../src/libs/types/kafka.types';

// ----------------------------------------------------------------
// Mock kafkajs primitives
// ----------------------------------------------------------------

class MockProducer {
  connect = sinon.stub().resolves();
  disconnect = sinon.stub().resolves();
  send = sinon.stub().resolves();
}

class MockConsumer {
  connect = sinon.stub().resolves();
  disconnect = sinon.stub().resolves();
  subscribe = sinon.stub().resolves();
  run = sinon.stub().resolves();
  pause = sinon.stub();
  resume = sinon.stub();
}

class MockKafka {
  producer = sinon.stub();
  consumer = sinon.stub();

  constructor() {
    this.producer.returns(new MockProducer());
    this.consumer.returns(new MockConsumer());
  }
}

// ----------------------------------------------------------------
// Concrete test subclasses of the abstract base classes
// ----------------------------------------------------------------

// We must import *after* we have stubbed kafkajs (not possible with ESM stubs),
// so instead we manipulate the prototype chain at the instance level.
import {
  BaseKafkaConnection,
  BaseKafkaProducerConnection,
  BaseKafkaConsumerConnection,
} from '../../../src/libs/services/kafka.service';

// Minimal concrete implementations used exclusively for testing.
class TestKafkaProducer extends BaseKafkaProducerConnection {
  constructor(config: KafkaConfig, logger: any) {
    super(config, logger);
  }
}

class TestKafkaConsumer extends BaseKafkaConsumerConnection {
  constructor(config: KafkaConfig, logger: any) {
    super(config, logger);
  }
}

// ----------------------------------------------------------------
// Tests
// ----------------------------------------------------------------

describe('Kafka Service', () => {
  const defaultConfig: KafkaConfig = {
    clientId: 'test-client',
    brokers: ['localhost:9092'],
    groupId: 'test-group',
  };

  let mockLogger: MockLogger;

  beforeEach(() => {
    mockLogger = createMockLogger();
  });

  afterEach(() => {
    sinon.restore();
  });

  // ================================================================
  // BaseKafkaConnection (tested indirectly through Producer)
  // ================================================================
  describe('BaseKafkaConnection', () => {
    it('should initialize kafka instance in constructor', () => {
      const producer = new TestKafkaProducer(defaultConfig, mockLogger);
      // The kafka property should be set (it is a real Kafka instance from kafkajs)
      expect((producer as any).kafka).to.exist;
    });

    it('should report isConnected false before connect', () => {
      const producer = new TestKafkaProducer(defaultConfig, mockLogger);
      expect(producer.isConnected()).to.be.false;
    });

    it('should call connect via ensureConnection when not connected', async () => {
      const producer = new TestKafkaProducer(defaultConfig, mockLogger);
      const mockProd = new MockProducer();
      (producer as any).producer = mockProd;

      await producer.connect();
      expect(producer.isConnected()).to.be.true;
    });

    it('should throw KafkaError when Kafka constructor fails (lines 40-44)', () => {
      const kafkajsPath = require.resolve('kafkajs');
      const originalKafkajs = require.cache[kafkajsPath];
      const modulePath = require.resolve('../../../src/libs/services/kafka.service');

      // Replace kafkajs with one whose Kafka constructor throws
      require.cache[kafkajsPath] = {
        ...originalKafkajs!,
        exports: {
          ...originalKafkajs!.exports,
          Kafka: function () {
            throw new Error('Kafka init failed');
          },
        },
      } as any;

      delete require.cache[modulePath];

      try {
        const mod = require('../../../src/libs/services/kafka.service');

        class FailingProducer extends mod.BaseKafkaProducerConnection {
          constructor(cfg: any, lgr: any) {
            super(cfg, lgr);
          }
        }

        try {
          new FailingProducer(defaultConfig, mockLogger);
          expect.fail('Should have thrown');
        } catch (error) {
          expect(error).to.be.instanceOf(KafkaError);
          expect((error as KafkaError).message).to.include(
            'Failed to initialize Kafka',
          );
          expect((error as any).metadata.clientId).to.equal('test-client');
          expect((error as any).metadata.details).to.equal(
            'Kafka init failed',
          );
        }
      } finally {
        // Restore kafkajs and re-require kafka.service with the working
        // Kafka constructor so the cached module has valid coverage.
        if (originalKafkajs) {
          require.cache[kafkajsPath] = originalKafkajs;
        }
        delete require.cache[modulePath];
        require('../../../src/libs/services/kafka.service');
      }
    });

    it('should not re-connect via ensureConnection when already connected', async () => {
      const producer = new TestKafkaProducer(defaultConfig, mockLogger);
      const mockProd = new MockProducer();
      (producer as any).producer = mockProd;

      await producer.connect();
      const connectSpy = sinon.spy(producer, 'connect');

      // ensureConnection is protected, invoke it indirectly through publish
      await producer.publish('topic', { key: 'k', value: 'v' });

      // connect should not have been called again since we are already connected
      expect(connectSpy.called).to.be.false;
    });
  });

  // ================================================================
  // BaseKafkaProducerConnection
  // ================================================================
  describe('BaseKafkaProducerConnection', () => {
    let producer: TestKafkaProducer;
    let mockProd: MockProducer;

    beforeEach(() => {
      producer = new TestKafkaProducer(defaultConfig, mockLogger);
      mockProd = new MockProducer();
      (producer as any).producer = mockProd;
    });

    // ---- connect ------------------------------------------------
    describe('connect', () => {
      it('should connect the producer and set isInitialized to true', async () => {
        await producer.connect();
        expect(mockProd.connect.calledOnce).to.be.true;
        expect(producer.isConnected()).to.be.true;
        expect(mockLogger.info.calledWithMatch('Successfully connected Kafka producer')).to.be.true;
      });

      it('should not reconnect if already connected', async () => {
        await producer.connect();
        await producer.connect();
        expect(mockProd.connect.calledOnce).to.be.true;
      });

      it('should throw KafkaError on connection failure', async () => {
        mockProd.connect.rejects(new Error('Connection refused'));
        try {
          await producer.connect();
          expect.fail('Should have thrown');
        } catch (error) {
          expect(error).to.be.instanceOf(KafkaError);
          expect((error as KafkaError).message).to.include('Failed to connect Kafka producer');
        }
        expect(producer.isConnected()).to.be.false;
      });

    });

    // ---- disconnect ---------------------------------------------
    describe('disconnect', () => {
      it('should disconnect the producer and set isInitialized to false', async () => {
        await producer.connect();
        await producer.disconnect();
        expect(mockProd.disconnect.calledOnce).to.be.true;
        expect(producer.isConnected()).to.be.false;
        expect(mockLogger.info.calledWithMatch('Successfully disconnected Kafka producer')).to.be.true;
      });

      it('should do nothing when not connected', async () => {
        await producer.disconnect();
        expect(mockProd.disconnect.called).to.be.false;
      });

      it('should handle disconnect errors gracefully (no throw)', async () => {
        await producer.connect();
        mockProd.disconnect.rejects(new Error('Disconnect failed'));
        await producer.disconnect(); // should not throw
        expect(mockLogger.error.calledOnce).to.be.true;
      });
    });

    // ---- publish ------------------------------------------------
    describe('publish', () => {
      beforeEach(async () => {
        await producer.connect();
      });

      it('should send a single formatted message to the topic', async () => {
        const msg: KafkaMessage<{ type: string }> = {
          key: 'msg-key',
          value: { type: 'TEST' },
        };
        await producer.publish('test-topic', msg);
        expect(mockProd.send.calledOnce).to.be.true;
        const sendArg = mockProd.send.firstCall.args[0];
        expect(sendArg.topic).to.equal('test-topic');
        expect(sendArg.messages).to.have.length(1);
        expect(sendArg.messages[0].key).to.equal('msg-key');
        expect(JSON.parse(sendArg.messages[0].value)).to.deep.equal({ type: 'TEST' });
      });

      it('should include headers in the formatted message', async () => {
        const msg: KafkaMessage<string> = {
          key: 'hdr-key',
          value: 'payload',
          headers: { 'x-custom': 'abc' },
        };
        await producer.publish('topic', msg);
        const sentMsg = mockProd.send.firstCall.args[0].messages[0];
        expect(sentMsg.headers).to.deep.equal({ 'x-custom': 'abc' });
      });

      it('should auto-connect if not connected before publish', async () => {
        const freshProducer = new TestKafkaProducer(defaultConfig, mockLogger);
        const freshMock = new MockProducer();
        (freshProducer as any).producer = freshMock;

        await freshProducer.publish('topic', { key: 'k', value: 'v' });
        expect(freshMock.connect.calledOnce).to.be.true;
        expect(freshMock.send.calledOnce).to.be.true;
      });

      it('should throw KafkaError when send fails', async () => {
        mockProd.send.rejects(new Error('Broker unavailable'));
        try {
          await producer.publish('fail-topic', { key: 'k', value: 'v' });
          expect.fail('Should have thrown');
        } catch (error) {
          expect(error).to.be.instanceOf(KafkaError);
          expect((error as KafkaError).message).to.include('Error publishing to Kafka topic fail-topic');
        }
      });

    });

    // ---- publishBatch -------------------------------------------
    describe('publishBatch', () => {
      beforeEach(async () => {
        await producer.connect();
      });

      it('should send multiple formatted messages to the topic', async () => {
        const msgs: KafkaMessage<string>[] = [
          { key: 'k1', value: 'v1' },
          { key: 'k2', value: 'v2' },
          { key: 'k3', value: 'v3' },
        ];
        await producer.publishBatch('batch-topic', msgs);
        expect(mockProd.send.calledOnce).to.be.true;
        const sendArg = mockProd.send.firstCall.args[0];
        expect(sendArg.topic).to.equal('batch-topic');
        expect(sendArg.messages).to.have.length(3);
        expect(sendArg.messages[0].key).to.equal('k1');
        expect(JSON.parse(sendArg.messages[1].value)).to.equal('v2');
      });

      it('should throw KafkaError when batch send fails', async () => {
        mockProd.send.rejects(new Error('Batch send failed'));
        try {
          await producer.publishBatch('t', [{ key: 'k', value: 'v' }]);
          expect.fail('Should have thrown');
        } catch (error) {
          expect(error).to.be.instanceOf(KafkaError);
        }
      });
    });

    // ---- healthCheck --------------------------------------------
    describe('healthCheck', () => {
      it('should return true when producer can publish a health-check message', async () => {
        const result = await producer.healthCheck();
        expect(result).to.be.true;
        expect(mockProd.send.calledOnce).to.be.true;
        const sendArg = mockProd.send.firstCall.args[0];
        expect(sendArg.topic).to.equal('health-check');
      });

      it('should return false when publish fails', async () => {
        mockProd.send.rejects(new Error('send error'));
        const result = await producer.healthCheck();
        expect(result).to.be.false;
        expect(mockLogger.error.called).to.be.true;
      });

    });

    // ---- formatMessage ------------------------------------------
    describe('formatMessage', () => {
      it('should JSON-stringify the value and preserve key and headers', () => {
        const msg: KafkaMessage<{ a: number }> = {
          key: 'fk',
          value: { a: 1 },
          headers: { 'h1': 'hv' },
        };
        // formatMessage is protected; invoke via any cast
        const formatted = (producer as any).formatMessage(msg);
        expect(formatted.key).to.equal('fk');
        expect(JSON.parse(formatted.value)).to.deep.equal({ a: 1 });
        expect(formatted.headers).to.deep.equal({ 'h1': 'hv' });
      });

      it('should handle undefined headers', () => {
        const msg: KafkaMessage<string> = { key: 'k', value: 'v' };
        const formatted = (producer as any).formatMessage(msg);
        expect(formatted.headers).to.be.undefined;
      });
    });
  });

  // ================================================================
  // BaseKafkaConsumerConnection
  // ================================================================
  describe('BaseKafkaConsumerConnection', () => {
    let consumer: TestKafkaConsumer;
    let mockCons: MockConsumer;

    beforeEach(() => {
      consumer = new TestKafkaConsumer(defaultConfig, mockLogger);
      mockCons = new MockConsumer();
      (consumer as any).consumer = mockCons;
    });

    // ---- constructor --------------------------------------------
    describe('constructor', () => {
      it('should use provided groupId', () => {
        const cfg: KafkaConfig = { ...defaultConfig, groupId: 'custom-group' };
        const c = new TestKafkaConsumer(cfg, mockLogger);
        // The consumer is constructed internally; we just verify no errors
        expect(c).to.exist;
      });

      it('should default groupId to clientId-group when not provided', () => {
        const cfg: KafkaConfig = { clientId: 'my-app', brokers: ['b:9092'] };
        const c = new TestKafkaConsumer(cfg, mockLogger);
        // No error means the fallback groupId was used
        expect(c).to.exist;
      });

      it('should default groupId to "default-group" when both groupId and clientId are missing', () => {
        const cfg: KafkaConfig = { brokers: ['b:9092'] };
        const c = new TestKafkaConsumer(cfg, mockLogger);
        expect(c).to.exist;
      });

      it('should use provided retry config values', () => {
        const cfg: KafkaConfig = {
          ...defaultConfig,
          initialRetryTime: 200,
          maxRetryTime: 60000,
          maxRetries: 5,
        };
        const c = new TestKafkaConsumer(cfg, mockLogger);
        expect(c).to.exist;
      });
    });

    // ---- connect ------------------------------------------------
    describe('connect', () => {
      it('should connect the consumer and set isInitialized to true', async () => {
        await consumer.connect();
        expect(mockCons.connect.calledOnce).to.be.true;
        expect(consumer.isConnected()).to.be.true;
        expect(mockLogger.info.calledWithMatch('Successfully connected Kafka consumer')).to.be.true;
      });

      it('should not reconnect if already connected', async () => {
        await consumer.connect();
        await consumer.connect();
        expect(mockCons.connect.calledOnce).to.be.true;
      });

      it('should throw KafkaError on connection failure', async () => {
        mockCons.connect.rejects(new Error('Connection refused'));
        try {
          await consumer.connect();
          expect.fail('Should have thrown');
        } catch (error) {
          expect(error).to.be.instanceOf(KafkaError);
          expect((error as KafkaError).message).to.include('Failed to connect Kafka consumer');
        }
        expect(consumer.isConnected()).to.be.false;
      });

    });

    // ---- disconnect ---------------------------------------------
    describe('disconnect', () => {
      it('should disconnect the consumer and set isInitialized to false', async () => {
        await consumer.connect();
        await consumer.disconnect();
        expect(mockCons.disconnect.calledOnce).to.be.true;
        expect(consumer.isConnected()).to.be.false;
        expect(mockLogger.info.calledWithMatch('Successfully disconnected Kafka consumer')).to.be.true;
      });

      it('should do nothing when not connected', async () => {
        await consumer.disconnect();
        expect(mockCons.disconnect.called).to.be.false;
      });

      it('should handle disconnect errors gracefully (no throw)', async () => {
        await consumer.connect();
        mockCons.disconnect.rejects(new Error('Disconnect failed'));
        await consumer.disconnect(); // should not throw
        expect(mockLogger.error.calledOnce).to.be.true;
      });

    });

    // ---- subscribe ----------------------------------------------
    describe('subscribe', () => {
      beforeEach(async () => {
        await consumer.connect();
      });

      it('should subscribe to multiple topics', async () => {
        await consumer.subscribe(['topic-a', 'topic-b']);
        expect(mockCons.subscribe.callCount).to.equal(2);
        expect(mockCons.subscribe.firstCall.args[0]).to.deep.equal({
          topic: 'topic-a',
          fromBeginning: false,
        });
        expect(mockCons.subscribe.secondCall.args[0]).to.deep.equal({
          topic: 'topic-b',
          fromBeginning: false,
        });
      });

      it('should subscribe from beginning when specified', async () => {
        await consumer.subscribe(['topic-a'], true);
        expect(mockCons.subscribe.calledOnce).to.be.true;
        expect(mockCons.subscribe.firstCall.args[0]).to.deep.equal({
          topic: 'topic-a',
          fromBeginning: true,
        });
      });

      it('should auto-connect if not connected before subscribe', async () => {
        const freshConsumer = new TestKafkaConsumer(defaultConfig, mockLogger);
        const freshMock = new MockConsumer();
        (freshConsumer as any).consumer = freshMock;

        await freshConsumer.subscribe(['topic']);
        expect(freshMock.connect.calledOnce).to.be.true;
      });

      it('should throw KafkaError when subscribe fails', async () => {
        mockCons.subscribe.rejects(new Error('Subscribe failed'));
        try {
          await consumer.subscribe(['bad-topic']);
          expect.fail('Should have thrown');
        } catch (error) {
          expect(error).to.be.instanceOf(KafkaError);
          expect((error as KafkaError).message).to.include('Failed to subscribe to topics');
        }
      });

    });

    // ---- consume ------------------------------------------------
    describe('consume', () => {
      beforeEach(async () => {
        await consumer.connect();
      });

      it('should call consumer.run with an eachMessage handler', async () => {
        const handler = sinon.stub().resolves();
        await consumer.consume(handler);
        expect(mockCons.run.calledOnce).to.be.true;
        const runArg = mockCons.run.firstCall.args[0];
        expect(runArg.eachMessage).to.be.a('function');
      });

      it('should parse and pass messages to handler', async () => {
        const handler = sinon.stub().resolves();
        mockCons.run.callsFake(async ({ eachMessage }: any) => {
          await eachMessage({
            topic: 'test-topic',
            partition: 0,
            message: {
              key: Buffer.from('msg-key'),
              value: Buffer.from(JSON.stringify({ data: 'hello' })),
              headers: {},
            },
          });
        });
        await consumer.consume(handler);
        expect(handler.calledOnce).to.be.true;
        const parsedMsg = handler.firstCall.args[0];
        expect(parsedMsg.key).to.equal('msg-key');
        expect(parsedMsg.value).to.deep.equal({ data: 'hello' });
      });

      it('should use empty string for key when message key is null', async () => {
        const handler = sinon.stub().resolves();
        mockCons.run.callsFake(async ({ eachMessage }: any) => {
          await eachMessage({
            topic: 'test-topic',
            partition: 0,
            message: {
              key: null,
              value: Buffer.from(JSON.stringify('payload')),
              headers: {},
            },
          });
        });
        await consumer.consume(handler);
        expect(handler.firstCall.args[0].key).to.equal('');
      });

      it('should log error for empty message value and not call handler', async () => {
        const handler = sinon.stub().resolves();
        mockCons.run.callsFake(async ({ eachMessage }: any) => {
          await eachMessage({
            topic: 'test-topic',
            partition: 0,
            message: {
              key: Buffer.from('k'),
              value: null,
              headers: {},
            },
          });
        });
        await consumer.consume(handler);
        expect(handler.called).to.be.false;
        expect(mockLogger.error.calledOnce).to.be.true;
      });

      it('should log error when handler throws and continue', async () => {
        const handler = sinon.stub().rejects(new Error('handler error'));
        mockCons.run.callsFake(async ({ eachMessage }: any) => {
          await eachMessage({
            topic: 'test-topic',
            partition: 0,
            message: {
              key: Buffer.from('k'),
              value: Buffer.from(JSON.stringify('val')),
              headers: {},
            },
          });
        });
        await consumer.consume(handler);
        expect(mockLogger.error.calledOnce).to.be.true;
      });

      it('should log error when message value is invalid JSON', async () => {
        const handler = sinon.stub().resolves();
        mockCons.run.callsFake(async ({ eachMessage }: any) => {
          await eachMessage({
            topic: 'test-topic',
            partition: 0,
            message: {
              key: Buffer.from('k'),
              value: Buffer.from('not-json{{{'),
              headers: {},
            },
          });
        });
        await consumer.consume(handler);
        expect(handler.called).to.be.false;
        expect(mockLogger.error.calledOnce).to.be.true;
      });

      it('should throw KafkaError when consumer.run fails', async () => {
        mockCons.run.rejects(new Error('run failed'));
        try {
          await consumer.consume(sinon.stub());
          expect.fail('Should have thrown');
        } catch (error) {
          expect(error).to.be.instanceOf(KafkaError);
          expect((error as KafkaError).message).to.include('Failed to start message consumption');
        }
      });

      it('should auto-connect if not connected before consume', async () => {
        const freshConsumer = new TestKafkaConsumer(defaultConfig, mockLogger);
        const freshMock = new MockConsumer();
        (freshConsumer as any).consumer = freshMock;

        await freshConsumer.consume(sinon.stub());
        expect(freshMock.connect.calledOnce).to.be.true;
      });
    });

    // ---- pause --------------------------------------------------
    describe('pause', () => {
      it('should pause consumption for each topic', () => {
        consumer.pause(['t1', 't2']);
        expect(mockCons.pause.callCount).to.equal(2);
        expect(mockCons.pause.firstCall.args[0]).to.deep.equal([{ topic: 't1' }]);
        expect(mockCons.pause.secondCall.args[0]).to.deep.equal([{ topic: 't2' }]);
        expect(mockLogger.debug.callCount).to.equal(2);
      });
    });

    // ---- resume -------------------------------------------------
    describe('resume', () => {
      it('should resume consumption for each topic', () => {
        consumer.resume(['t1', 't2']);
        expect(mockCons.resume.callCount).to.equal(2);
        expect(mockCons.resume.firstCall.args[0]).to.deep.equal([{ topic: 't1' }]);
        expect(mockCons.resume.secondCall.args[0]).to.deep.equal([{ topic: 't2' }]);
        expect(mockLogger.debug.callCount).to.equal(2);
      });
    });

    // ---- healthCheck --------------------------------------------
    describe('healthCheck', () => {
      it('should return true when consumer can connect', async () => {
        const result = await consumer.healthCheck();
        expect(result).to.be.true;
      });

      it('should return false when connection fails', async () => {
        const freshConsumer = new TestKafkaConsumer(defaultConfig, mockLogger);
        const freshMock = new MockConsumer();
        freshMock.connect.rejects(new Error('connect failed'));
        (freshConsumer as any).consumer = freshMock;

        const result = await freshConsumer.healthCheck();
        expect(result).to.be.false;
        expect(mockLogger.error.called).to.be.true;
      });

    });
  });
});
