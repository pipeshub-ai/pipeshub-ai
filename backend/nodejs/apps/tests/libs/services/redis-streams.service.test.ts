import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { EventEmitter } from 'events';
import { MessageBrokerError } from '../../../src/libs/errors/messaging.errors';
import { createMockLogger, MockLogger } from '../../helpers/mock-logger';
import { RedisBrokerConfig, StreamMessage } from '../../../src/libs/types/messaging.types';

// ---------------------------------------------------------------------------
// Mock Redis class for ioredis
// ---------------------------------------------------------------------------
class MockRedisClient extends EventEmitter {
  status = 'ready';
  connect = sinon.stub().resolves();
  quit = sinon.stub().resolves();
  xadd = sinon.stub().resolves('1234567890-0');
  xreadgroup = sinon.stub().resolves(null);
  xack = sinon.stub().resolves(1);
  xgroup = sinon.stub().resolves();
  ping = sinon.stub().resolves('PONG');
  exists = sinon.stub().resolves(0);
  keys = sinon.stub().resolves([]);
  type = sinon.stub().resolves('string');
  pipeline = sinon.stub();
}

// ---------------------------------------------------------------------------
// Helper: override ioredis mock and reload redis-streams.service
// ---------------------------------------------------------------------------
function setupIoredisMock(mockClient: MockRedisClient) {
  const ioredisPath = require.resolve('ioredis');
  const original = require.cache[ioredisPath];

  const FakeRedis = function (this: any, _options: any) {
    Object.assign(this, mockClient);
    this.on = mockClient.on.bind(mockClient);
    this.emit = mockClient.emit.bind(mockClient);
    this.removeListener = mockClient.removeListener.bind(mockClient);
    return mockClient;
  } as any;
  FakeRedis.prototype = mockClient;

  require.cache[ioredisPath] = {
    ...original!,
    exports: { Redis: FakeRedis, default: FakeRedis, RedisOptions: {} },
  } as any;

  const svcPath = require.resolve('../../../src/libs/services/redis-streams.service');
  delete require.cache[svcPath];

  return require('../../../src/libs/services/redis-streams.service');
}

describe('Redis Streams Service', () => {
  const defaultConfig: RedisBrokerConfig = {
    type: 'redis',
    host: 'localhost',
    port: 6379,
    password: undefined,
    db: 0,
    maxLen: 5000,
    clientId: 'test-client',
    groupId: 'test-group',
  };

  let mockLogger: MockLogger;
  let mockRedis: MockRedisClient;
  let BaseRedisStreamsProducerConnection: any;
  let BaseRedisStreamsConsumerConnection: any;
  let RedisStreamsAdminService: any;

  beforeEach(() => {
    mockLogger = createMockLogger();
    mockRedis = new MockRedisClient();
    const mod = setupIoredisMock(mockRedis);
    BaseRedisStreamsProducerConnection = mod.BaseRedisStreamsProducerConnection;
    BaseRedisStreamsConsumerConnection = mod.BaseRedisStreamsConsumerConnection;
    RedisStreamsAdminService = mod.RedisStreamsAdminService;
  });

  afterEach(() => {
    sinon.restore();
  });

  // ================================================================
  // BaseRedisStreamsProducerConnection
  // ================================================================
  describe('BaseRedisStreamsProducerConnection', () => {
    let producer: any;

    beforeEach(() => {
      class TestRedisProducer extends BaseRedisStreamsProducerConnection {
        constructor(config: RedisBrokerConfig, logger: any) {
          super(config, logger);
        }
      }
      producer = new TestRedisProducer(defaultConfig, mockLogger);
    });

    describe('constructor', () => {
      it('should set maxLen from config', () => {
        expect(producer.maxLen).to.equal(5000);
      });

      it('should default maxLen to 10000 when not specified', () => {
        class TestP extends BaseRedisStreamsProducerConnection {
          constructor(config: RedisBrokerConfig, logger: any) { super(config, logger); }
        }
        const p = new TestP({ ...defaultConfig, maxLen: undefined }, mockLogger);
        expect(p.maxLen).to.equal(10000);
      });

      it('should report isConnected false before connect', () => {
        expect(producer.isConnected()).to.be.false;
      });
    });

    describe('connect', () => {
      it('should connect and set initialized to true', async () => {
        await producer.connect();
        expect(mockRedis.connect.calledOnce).to.be.true;
        expect(producer.isConnected()).to.be.true;
        expect(mockLogger.info.calledWithMatch('Successfully connected Redis Streams producer')).to.be.true;
      });

      it('should not reconnect if already connected', async () => {
        await producer.connect();
        await producer.connect();
        expect(mockRedis.connect.calledOnce).to.be.true;
      });

      it('should throw MessageBrokerError on connection failure', async () => {
        mockRedis.connect.rejects(new Error('Connection refused'));
        try {
          await producer.connect();
          expect.fail('Should have thrown');
        } catch (error) {
          expect(error).to.be.instanceOf(MessageBrokerError);
          expect((error as Error).message).to.include('Failed to connect Redis Streams producer');
        }
        expect(producer.isConnected()).to.be.false;
      });
    });

    describe('disconnect', () => {
      it('should disconnect and set initialized to false', async () => {
        await producer.connect();
        await producer.disconnect();
        expect(mockRedis.quit.calledOnce).to.be.true;
        expect(producer.isConnected()).to.be.false;
        expect(mockLogger.info.calledWithMatch('Successfully disconnected Redis Streams producer')).to.be.true;
      });

      it('should do nothing when not connected', async () => {
        await producer.disconnect();
        expect(mockRedis.quit.called).to.be.false;
      });

      it('should handle disconnect errors gracefully', async () => {
        await producer.connect();
        mockRedis.quit.rejects(new Error('Disconnect failed'));
        await producer.disconnect();
        expect(mockLogger.error.calledOnce).to.be.true;
      });
    });

    describe('isConnected', () => {
      it('should return true when initialized and redis status is ready', async () => {
        await producer.connect();
        mockRedis.status = 'ready';
        expect(producer.isConnected()).to.be.true;
      });

      it('should return false when redis status is not ready', async () => {
        await producer.connect();
        mockRedis.status = 'connecting';
        expect(producer.isConnected()).to.be.false;
      });
    });

    describe('publish', () => {
      beforeEach(async () => {
        await producer.connect();
      });

      it('should publish a message to the correct stream', async () => {
        const msg: StreamMessage<{ type: string }> = {
          key: 'msg-key',
          value: { type: 'TEST' },
        };
        await producer.publish('test-topic', msg);
        expect(mockRedis.xadd.calledOnce).to.be.true;
        const args = mockRedis.xadd.firstCall.args;
        expect(args[0]).to.equal('test-topic');
        expect(args[1]).to.equal('MAXLEN');
        expect(args[2]).to.equal('~');
        expect(args[3]).to.equal('5000');
        expect(args[4]).to.equal('*');
        expect(args).to.include('key');
        expect(args).to.include('msg-key');
      });

      it('should JSON-stringify the message value', async () => {
        const msg: StreamMessage<{ data: number }> = {
          key: 'k',
          value: { data: 42 },
        };
        await producer.publish('topic', msg);
        const args = mockRedis.xadd.firstCall.args;
        const valueIdx = args.indexOf('value');
        expect(JSON.parse(args[valueIdx + 1])).to.deep.equal({ data: 42 });
      });

      it('should include headers when present', async () => {
        const msg: StreamMessage<string> = {
          key: 'hdr-key',
          value: 'payload',
          headers: { 'x-custom': 'abc' },
        };
        await producer.publish('topic', msg);
        const args = mockRedis.xadd.firstCall.args;
        expect(args).to.include('headers');
        const headersIdx = args.indexOf('headers');
        expect(JSON.parse(args[headersIdx + 1])).to.deep.equal({ 'x-custom': 'abc' });
      });

      it('should not include headers field when absent', async () => {
        const msg: StreamMessage<string> = { key: 'k', value: 'v' };
        await producer.publish('topic', msg);
        const args = mockRedis.xadd.firstCall.args;
        expect(args).to.not.include('headers');
      });

      it('should throw MessageBrokerError when xadd fails', async () => {
        mockRedis.xadd.rejects(new Error('Stream unavailable'));
        try {
          await producer.publish('fail-topic', { key: 'k', value: 'v' });
          expect.fail('Should have thrown');
        } catch (error) {
          expect(error).to.be.instanceOf(MessageBrokerError);
          expect((error as Error).message).to.include('Error publishing to Redis stream fail-topic');
        }
      });
    });

    describe('publishBatch', () => {
      let mockPipeline: any;

      beforeEach(async () => {
        mockPipeline = {
          xadd: sinon.stub().returnsThis(),
          exec: sinon.stub().resolves([]),
        };
        mockRedis.pipeline.returns(mockPipeline);
        await producer.connect();
      });

      it('should send multiple messages via pipeline', async () => {
        const msgs: StreamMessage<string>[] = [
          { key: 'k1', value: 'v1' },
          { key: 'k2', value: 'v2' },
          { key: 'k3', value: 'v3' },
        ];
        await producer.publishBatch('batch-topic', msgs);
        expect(mockPipeline.xadd.callCount).to.equal(3);
        expect(mockPipeline.exec.calledOnce).to.be.true;
      });

      it('should include headers in batch messages', async () => {
        const msgs: StreamMessage<string>[] = [
          { key: 'k1', value: 'v1', headers: { 'h1': 'hv1' } },
        ];
        await producer.publishBatch('topic', msgs);
        const args = mockPipeline.xadd.firstCall.args;
        expect(args).to.include('headers');
      });

      it('should throw MessageBrokerError when pipeline exec fails', async () => {
        mockPipeline.exec.rejects(new Error('Pipeline failed'));
        try {
          await producer.publishBatch('t', [{ key: 'k', value: 'v' }]);
          expect.fail('Should have thrown');
        } catch (error) {
          expect(error).to.be.instanceOf(MessageBrokerError);
        }
      });
    });

    describe('healthCheck', () => {
      it('should return true when publish succeeds', async () => {
        await producer.connect();
        const result = await producer.healthCheck();
        expect(result).to.be.true;
        expect(mockRedis.xadd.calledOnce).to.be.true;
        const args = mockRedis.xadd.firstCall.args;
        expect(args[0]).to.equal('health-check');
      });

      it('should return false when publish fails', async () => {
        await producer.connect();
        mockRedis.xadd.rejects(new Error('xadd error'));
        const result = await producer.healthCheck();
        expect(result).to.be.false;
        expect(mockLogger.error.called).to.be.true;
      });
    });
  });

  // ================================================================
  // BaseRedisStreamsConsumerConnection
  // ================================================================
  describe('BaseRedisStreamsConsumerConnection', () => {
    let consumer: any;

    beforeEach(() => {
      class TestRedisConsumer extends BaseRedisStreamsConsumerConnection {
        constructor(config: RedisBrokerConfig, logger: any) {
          super(config, logger);
        }
      }
      consumer = new TestRedisConsumer(defaultConfig, mockLogger);
    });

    describe('constructor', () => {
      it('should set groupId from config', () => {
        expect(consumer.groupId).to.equal('test-group');
      });

      it('should use clientId-group as default groupId when not provided', () => {
        class TestC extends BaseRedisStreamsConsumerConnection {
          constructor(config: RedisBrokerConfig, logger: any) { super(config, logger); }
        }
        const c = new TestC({ ...defaultConfig, groupId: undefined }, mockLogger);
        expect(c.groupId).to.equal('test-client-group');
      });

      it('should set consumerId from config clientId', () => {
        expect(consumer.consumerId).to.equal('test-client');
      });
    });

    describe('connect', () => {
      it('should connect the consumer and set initialized to true', async () => {
        await consumer.connect();
        expect(mockRedis.connect.calledOnce).to.be.true;
        expect(consumer.isConnected()).to.be.true;
        expect(mockLogger.info.calledWithMatch('Successfully connected Redis Streams consumer')).to.be.true;
      });

      it('should not reconnect if already connected', async () => {
        await consumer.connect();
        await consumer.connect();
        expect(mockRedis.connect.calledOnce).to.be.true;
      });

      it('should throw MessageBrokerError on connection failure', async () => {
        mockRedis.connect.rejects(new Error('Connection refused'));
        try {
          await consumer.connect();
          expect.fail('Should have thrown');
        } catch (error) {
          expect(error).to.be.instanceOf(MessageBrokerError);
          expect((error as Error).message).to.include('Failed to connect Redis Streams consumer');
        }
        expect(consumer.isConnected()).to.be.false;
      });
    });

    describe('disconnect', () => {
      it('should disconnect and set initialized to false', async () => {
        await consumer.connect();
        await consumer.disconnect();
        expect(mockRedis.quit.calledOnce).to.be.true;
        expect(consumer.isConnected()).to.be.false;
        expect(mockLogger.info.calledWithMatch('Successfully disconnected Redis Streams consumer')).to.be.true;
      });

      it('should do nothing when not connected', async () => {
        await consumer.disconnect();
        expect(mockRedis.quit.called).to.be.false;
      });

      it('should handle disconnect errors gracefully', async () => {
        await consumer.connect();
        mockRedis.quit.rejects(new Error('Disconnect failed'));
        await consumer.disconnect();
        expect(mockLogger.error.calledOnce).to.be.true;
      });
    });

    describe('subscribe', () => {
      beforeEach(async () => {
        await consumer.connect();
      });

      it('should create consumer groups for each topic', async () => {
        await consumer.subscribe(['topic-a', 'topic-b']);
        expect(mockRedis.xgroup.callCount).to.equal(2);
      });

      it('should handle BUSYGROUP error gracefully', async () => {
        const error = new Error('BUSYGROUP Consumer Group name already exists');
        mockRedis.xgroup.rejects(error);
        await consumer.subscribe(['topic-a']);
        expect(mockLogger.debug.called).to.be.true;
      });

      it('should throw MessageBrokerError for non-BUSYGROUP errors', async () => {
        mockRedis.xgroup.rejects(new Error('Connection lost'));
        try {
          await consumer.subscribe(['topic-a']);
          expect.fail('Should have thrown');
        } catch (error) {
          expect(error).to.be.instanceOf(MessageBrokerError);
          expect((error as Error).message).to.include('Failed to subscribe to Redis stream');
        }
      });

      it('should deduplicate subscribed topics', async () => {
        await consumer.subscribe(['topic-a', 'topic-b']);
        await consumer.subscribe(['topic-b', 'topic-c']);
        expect(consumer.subscribedTopics).to.deep.equal(['topic-a', 'topic-b', 'topic-c']);
      });

      it('should use $ offset by default and 0 when fromBeginning', async () => {
        await consumer.subscribe(['topic-a'], false);
        const firstArgs = mockRedis.xgroup.firstCall.args;
        expect(firstArgs).to.include('$');

        mockRedis.xgroup.resetHistory();
        await consumer.subscribe(['topic-b'], true);
        const secondArgs = mockRedis.xgroup.firstCall.args;
        expect(secondArgs).to.include('0');
      });
    });

    describe('consume', () => {
      beforeEach(async () => {
        await consumer.connect();
      });

      it('should set running to true and start consume loop', async () => {
        await consumer.consume(sinon.stub().resolves());
        expect(consumer.running).to.be.true;
        expect(consumer.consumeLoopPromise).to.not.be.null;
        consumer.running = false;
      });
    });

    describe('pause', () => {
      it('should be a no-op for running state', () => {
        consumer.running = true;
        consumer.pause(['t1']);
        expect(consumer.running).to.be.true;
      });
    });

    describe('resume', () => {
      it('should be a no-op', () => {
        consumer.resume(['t1']);
        expect(mockLogger.debug.called).to.be.false;
      });
    });

    describe('healthCheck', () => {
      it('should return true when ping succeeds', async () => {
        await consumer.connect();
        const result = await consumer.healthCheck();
        expect(result).to.be.true;
        expect(mockRedis.ping.calledOnce).to.be.true;
      });

      it('should return false when ping fails', async () => {
        await consumer.connect();
        mockRedis.ping.rejects(new Error('ping failed'));
        const result = await consumer.healthCheck();
        expect(result).to.be.false;
        expect(mockLogger.error.called).to.be.true;
      });
    });
  });

  // ================================================================
  // RedisStreamsAdminService
  // ================================================================
  describe('RedisStreamsAdminService', () => {
    let admin: any;

    beforeEach(() => {
      admin = new RedisStreamsAdminService(defaultConfig, mockLogger);
    });

    describe('constructor', () => {
      it('should create an instance', () => {
        expect(admin).to.exist;
      });
    });

    describe('ensureTopicsExist', () => {
      it('should create streams that do not exist', async () => {
        const topics = [
          { topic: 'record-events', numPartitions: 1, replicationFactor: 1 },
          { topic: 'entity-events', numPartitions: 1, replicationFactor: 1 },
        ];
        mockRedis.exists.resolves(0);
        await admin.ensureTopicsExist(topics);
        expect(mockRedis.connect.calledOnce).to.be.true;
        expect(mockRedis.xgroup.callCount).to.equal(4);
        expect(mockRedis.quit.calledOnce).to.be.true;
      });

      it('should skip existing streams', async () => {
        const topics = [{ topic: 'existing-stream' }];
        mockRedis.exists.resolves(1);
        await admin.ensureTopicsExist(topics);
        expect(mockRedis.xgroup.called).to.be.false;
      });

      it('should handle per-topic errors gracefully', async () => {
        const topics = [{ topic: 'bad-stream' }];
        mockRedis.exists.rejects(new Error('Redis error'));
        await admin.ensureTopicsExist(topics);
        expect(mockLogger.warn.called).to.be.true;
      });

      it('should disconnect even on connection error', async () => {
        mockRedis.connect.rejects(new Error('Connection refused'));
        try {
          await admin.ensureTopicsExist();
        } catch (_e) { /* expected */ }
        expect(mockRedis.quit.calledOnce).to.be.true;
      });

      it('should handle disconnect error gracefully', async () => {
        mockRedis.quit.rejects(new Error('disconnect failed'));
        await admin.ensureTopicsExist([{ topic: 'stream-1' }]);
        expect(mockLogger.warn.called).to.be.true;
      });

      it('should use default topics when none provided', async () => {
        await admin.ensureTopicsExist();
        expect(mockRedis.exists.callCount).to.be.greaterThan(0);
      });
    });

    describe('listTopics', () => {
      it('should return only stream-type keys', async () => {
        mockRedis.keys.resolves(['stream-1', 'hash-key', 'stream-2']);
        mockRedis.type.onFirstCall().resolves('stream');
        mockRedis.type.onSecondCall().resolves('hash');
        mockRedis.type.onThirdCall().resolves('stream');

        const result = await admin.listTopics();
        expect(result).to.deep.equal(['stream-1', 'stream-2']);
        expect(mockRedis.connect.calledOnce).to.be.true;
        expect(mockRedis.quit.calledOnce).to.be.true;
      });

      it('should return empty array when no streams exist', async () => {
        mockRedis.keys.resolves([]);
        const result = await admin.listTopics();
        expect(result).to.deep.equal([]);
      });
    });
  });
});
