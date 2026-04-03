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
  xautoclaim = sinon.stub().resolves(['0-0', [], []]);
  ping = sinon.stub().resolves('PONG');
  exists = sinon.stub().resolves(0);
  scan = sinon.stub().resolves(['0', []]);
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
  // buildRedisOptions — retryStrategy (lines 54-57)
  // ================================================================
  describe('buildRedisOptions retryStrategy', () => {
    it('should cap delay at maxRetryTime (default 30000)', () => {
      let capturedOptions: any;
      const ioredisPath = require.resolve('ioredis');
      const original = require.cache[ioredisPath];

      const CapturingRedis = function (this: any, options: any) {
        capturedOptions = options;
        const client = new MockRedisClient();
        Object.assign(this, client);
        this.on = client.on.bind(client);
        this.emit = client.emit.bind(client);
        return client;
      } as any;
      CapturingRedis.prototype = new MockRedisClient();

      require.cache[ioredisPath] = {
        ...original!,
        exports: { Redis: CapturingRedis, default: CapturingRedis, RedisOptions: {} },
      } as any;

      const svcPath = require.resolve(
        '../../../src/libs/services/redis-streams.service',
      );
      delete require.cache[svcPath];
      const mod = require('../../../src/libs/services/redis-streams.service');

      class TestP extends mod.BaseRedisStreamsProducerConnection {
        constructor(cfg: any, lgr: any) { super(cfg, lgr); }
      }
      new TestP(defaultConfig, mockLogger);

      expect(capturedOptions.retryStrategy).to.be.a('function');
      // times=1 → min(200, 30000) = 200
      expect(capturedOptions.retryStrategy(1)).to.equal(200);
      // times=200 → min(40000, 30000) = 30000 (capped)
      expect(capturedOptions.retryStrategy(200)).to.equal(30000);

      if (original) require.cache[ioredisPath] = original;
      delete require.cache[svcPath];
    });

    it('should use custom maxRetryTime from config', () => {
      let capturedOptions: any;
      const ioredisPath = require.resolve('ioredis');
      const original = require.cache[ioredisPath];

      const CapturingRedis = function (this: any, options: any) {
        capturedOptions = options;
        const client = new MockRedisClient();
        Object.assign(this, client);
        this.on = client.on.bind(client);
        this.emit = client.emit.bind(client);
        return client;
      } as any;
      CapturingRedis.prototype = new MockRedisClient();

      require.cache[ioredisPath] = {
        ...original!,
        exports: { Redis: CapturingRedis, default: CapturingRedis, RedisOptions: {} },
      } as any;

      const svcPath = require.resolve(
        '../../../src/libs/services/redis-streams.service',
      );
      delete require.cache[svcPath];
      const mod = require('../../../src/libs/services/redis-streams.service');

      class TestP extends mod.BaseRedisStreamsProducerConnection {
        constructor(cfg: any, lgr: any) { super(cfg, lgr); }
      }
      new TestP({ ...defaultConfig, maxRetryTime: 5000 }, mockLogger);

      // times=50 → min(10000, 5000) = 5000 (capped at custom value)
      expect(capturedOptions.retryStrategy(50)).to.equal(5000);

      if (original) require.cache[ioredisPath] = original;
      delete require.cache[svcPath];
    });
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

    describe('ensureConnection (producer)', () => {
      it('should auto-connect via ensureConnection if not connected', async () => {
        // Do NOT call producer.connect() first — no nested beforeEach here
        const msg: StreamMessage<string> = { key: 'k', value: 'v' };
        await producer.publish('topic', msg);
        expect(mockRedis.connect.called).to.be.true;
        expect(mockRedis.xadd.calledOnce).to.be.true;
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

      it('should throw MessageBrokerError when some pipeline entries fail', async () => {
        // Simulate partial failure: one success, one error
        const pipelineError = new Error('XADD failed for entry');
        mockPipeline.exec.resolves([
          [null, '1-0'],       // success
          [pipelineError, null], // failure
        ]);
        try {
          await producer.publishBatch('topic', [
            { key: 'k1', value: 'v1' },
            { key: 'k2', value: 'v2' },
          ]);
          expect.fail('Should have thrown');
        } catch (error) {
          expect(error).to.be.instanceOf(MessageBrokerError);
          expect((error as Error).message).to.include('Error publishing batch to Redis stream');
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
        // connect called twice: once for redis, once for ackRedis (same mock)
        expect(mockRedis.connect.called).to.be.true;
        expect(consumer.isConnected()).to.be.true;
        expect(mockLogger.info.calledWithMatch('Successfully connected Redis Streams consumer')).to.be.true;
      });

      it('should not reconnect if already connected', async () => {
        const countBefore = mockRedis.connect.callCount;
        await consumer.connect();
        await consumer.connect();
        // Only one connect() call (the first) should add new calls
        expect(mockRedis.connect.callCount).to.equal(countBefore + 2);
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
        // quit called twice: once for redis, once for ackRedis (same mock)
        expect(mockRedis.quit.called).to.be.true;
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

    describe('ensureConnection', () => {
      it('should auto-connect when not connected', async () => {
        // Not connected yet (initialized=false, status=ready)
        expect(consumer.isConnected()).to.be.false;
        // ensureConnection is protected; invoke indirectly via subscribe
        await consumer.subscribe(['topic-a']);
        expect(mockRedis.connect.called).to.be.true;
        expect(consumer.isConnected()).to.be.true;
      });
    });

    describe('disconnect with active consume loop', () => {
      it('should wait for consume loop to finish before disconnecting', async () => {
        await consumer.connect();
        await consumer.subscribe(['topic-a']);

        // xreadgroup: return null immediately, then stop loop
        let callCount = 0;
        mockRedis.xreadgroup.callsFake(async () => {
          callCount++;
          if (callCount >= 2) consumer.running = false;
          return null;
        });

        const handler = sinon.stub().resolves();
        await consumer.consume(handler);

        // Let the loop iterate briefly
        await new Promise((r) => setTimeout(r, 50));

        await consumer.disconnect();
        expect(consumer.consumeLoopPromise).to.be.null;
        expect(consumer.isConnected()).to.be.false;
      });
    });

    describe('drainPending', () => {
      beforeEach(async () => {
        await consumer.connect();
        await consumer.subscribe(['test-stream']);
      });

      it('should recover pending messages via XAUTOCLAIM', async () => {
        // First call returns a pending entry, second returns empty
        mockRedis.xautoclaim
          .onFirstCall()
          .resolves([
            '0-0',
            [
              [
                '1-0',
                [
                  'key',
                  'pending-key',
                  'value',
                  JSON.stringify({ recovered: true }),
                ],
              ],
            ],
            [],
          ])
          .onSecondCall()
          .resolves(['0-0', [], []]);

        // Stop consumeLoop after drainPending
        mockRedis.xreadgroup.callsFake(async () => {
          consumer.running = false;
          return null;
        });

        const handler = sinon.stub().resolves();
        await consumer.consume(handler);
        await consumer.consumeLoopPromise;

        expect(handler.calledOnce).to.be.true;
        expect(handler.firstCall.args[0].key).to.equal('pending-key');
        expect(handler.firstCall.args[0].value).to.deep.equal({
          recovered: true,
        });
        expect(mockRedis.xack.called).to.be.true;
      });

      it('should recover pending messages with headers', async () => {
        const headers = { 'x-trace': 'abc' };
        mockRedis.xautoclaim
          .onFirstCall()
          .resolves([
            '0-0',
            [
              [
                '1-0',
                [
                  'key',
                  'k',
                  'value',
                  JSON.stringify('v'),
                  'headers',
                  JSON.stringify(headers),
                ],
              ],
            ],
            [],
          ])
          .onSecondCall()
          .resolves(['0-0', [], []]);

        mockRedis.xreadgroup.callsFake(async () => {
          consumer.running = false;
          return null;
        });

        const handler = sinon.stub().resolves();
        await consumer.consume(handler);
        await consumer.consumeLoopPromise;

        expect(handler.firstCall.args[0].headers).to.deep.equal(headers);
      });

      it('should ack and skip entries without value field', async () => {
        mockRedis.xautoclaim
          .onFirstCall()
          .resolves([
            '0-0',
            [['1-0', ['key', 'k']]],  // no value field
            [],
          ])
          .onSecondCall()
          .resolves(['0-0', [], []]);

        mockRedis.xreadgroup.callsFake(async () => {
          consumer.running = false;
          return null;
        });

        const handler = sinon.stub().resolves();
        await consumer.consume(handler);
        await consumer.consumeLoopPromise;

        expect(handler.called).to.be.false;
        expect(mockRedis.xack.calledOnce).to.be.true;
      });

      it('should log error when entry processing fails and continue', async () => {
        mockRedis.xautoclaim
          .onFirstCall()
          .resolves([
            '0-0',
            [['1-0', ['key', 'k', 'value', 'not-json{{']]],
            [],
          ])
          .onSecondCall()
          .resolves(['0-0', [], []]);

        mockRedis.xreadgroup.callsFake(async () => {
          consumer.running = false;
          return null;
        });

        const handler = sinon.stub().resolves();
        await consumer.consume(handler);
        await consumer.consumeLoopPromise;

        expect(mockLogger.error.calledWithMatch('Error recovering pending message')).to.be.true;
      });

      it('should handle XAUTOCLAIM errors and break', async () => {
        mockRedis.xautoclaim.rejects(new Error('XAUTOCLAIM failed'));

        mockRedis.xreadgroup.callsFake(async () => {
          consumer.running = false;
          return null;
        });

        const handler = sinon.stub().resolves();
        await consumer.consume(handler);
        await consumer.consumeLoopPromise;

        expect(mockLogger.error.calledWithMatch('Error during XAUTOCLAIM')).to.be.true;
      });

      it('should follow nextId pagination until 0-0', async () => {
        // First page: returns entries with nextId != '0-0'
        mockRedis.xautoclaim
          .onFirstCall()
          .resolves([
            '2-0',
            [['1-0', ['key', 'k1', 'value', JSON.stringify('v1')]]],
            [],
          ])
          // Second page: returns entries with nextId == '0-0'
          .onSecondCall()
          .resolves([
            '0-0',
            [['2-0', ['key', 'k2', 'value', JSON.stringify('v2')]]],
            [],
          ])
          .onThirdCall()
          .resolves(['0-0', [], []]);

        mockRedis.xreadgroup.callsFake(async () => {
          consumer.running = false;
          return null;
        });

        const handler = sinon.stub().resolves();
        await consumer.consume(handler);
        await consumer.consumeLoopPromise;

        expect(handler.callCount).to.equal(2);
      });
    });

    describe('consumeLoop', () => {
      beforeEach(async () => {
        await consumer.connect();
        await consumer.subscribe(['test-stream']);
      });

      it('should process messages from xreadgroup', async () => {
        const xreadResult = [
          [
            'test-stream',
            [
              [
                '1-0',
                [
                  'key',
                  'msg-key',
                  'value',
                  JSON.stringify({ data: 'hello' }),
                ],
              ],
            ],
          ],
        ];

        let callCount = 0;
        mockRedis.xreadgroup.callsFake(async () => {
          callCount++;
          if (callCount === 1) return xreadResult;
          consumer.running = false;
          return null;
        });

        const handler = sinon.stub().resolves();
        await consumer.consume(handler);
        await consumer.consumeLoopPromise;

        expect(handler.calledOnce).to.be.true;
        expect(handler.firstCall.args[0].key).to.equal('msg-key');
        expect(handler.firstCall.args[0].value).to.deep.equal({
          data: 'hello',
        });
        expect(mockRedis.xack.called).to.be.true;
      });

      it('should include headers when present in stream entry', async () => {
        const headers = { 'x-request-id': '123' };
        const xreadResult = [
          [
            'test-stream',
            [
              [
                '1-0',
                [
                  'key',
                  'k',
                  'value',
                  JSON.stringify('v'),
                  'headers',
                  JSON.stringify(headers),
                ],
              ],
            ],
          ],
        ];

        let callCount = 0;
        mockRedis.xreadgroup.callsFake(async () => {
          callCount++;
          if (callCount === 1) return xreadResult;
          consumer.running = false;
          return null;
        });

        const handler = sinon.stub().resolves();
        await consumer.consume(handler);
        await consumer.consumeLoopPromise;

        expect(handler.firstCall.args[0].headers).to.deep.equal(headers);
      });

      it('should skip messages without value field and ack them', async () => {
        const xreadResult = [
          [
            'test-stream',
            [['1-0', ['key', 'k']]],  // no value field
          ],
        ];

        let callCount = 0;
        mockRedis.xreadgroup.callsFake(async () => {
          callCount++;
          if (callCount === 1) return xreadResult;
          consumer.running = false;
          return null;
        });

        const handler = sinon.stub().resolves();
        await consumer.consume(handler);
        await consumer.consumeLoopPromise;

        expect(handler.called).to.be.false;
        expect(mockRedis.xack.called).to.be.true;
        expect(mockLogger.debug.calledWithMatch('Skipping message without value field')).to.be.true;
      });

      it('should log error when handler throws and continue', async () => {
        const xreadResult = [
          [
            'test-stream',
            [['1-0', ['key', 'k', 'value', JSON.stringify('v')]]],
          ],
        ];

        let callCount = 0;
        mockRedis.xreadgroup.callsFake(async () => {
          callCount++;
          if (callCount === 1) return xreadResult;
          consumer.running = false;
          return null;
        });

        const handler = sinon.stub().rejects(new Error('handler error'));
        await consumer.consume(handler);
        await consumer.consumeLoopPromise;

        expect(mockLogger.error.calledWithMatch('Error processing Redis stream message')).to.be.true;
      });

      it('should warn and continue on unexpected xreadgroup result shape', async () => {
        let callCount = 0;
        mockRedis.xreadgroup.callsFake(async () => {
          callCount++;
          if (callCount === 1) return 'bad-shape';
          consumer.running = false;
          return null;
        });

        const handler = sinon.stub().resolves();
        await consumer.consume(handler);
        await consumer.consumeLoopPromise;

        expect(mockLogger.warn.calledWithMatch('Unexpected Redis xreadgroup payload shape')).to.be.true;
        expect(handler.called).to.be.false;
      });

      it('should handle xreadgroup errors with backoff', async () => {
        let callCount = 0;
        mockRedis.xreadgroup.callsFake(async () => {
          callCount++;
          if (callCount === 1) throw new Error('xreadgroup failed');
          consumer.running = false;
          return null;
        });

        const handler = sinon.stub().resolves();
        await consumer.consume(handler);
        await consumer.consumeLoopPromise;

        expect(mockLogger.error.calledWithMatch('Error in Redis Streams consume loop')).to.be.true;
      });

      it('should sleep when no topics are subscribed', async () => {
        // Clear subscribed topics
        consumer.subscribedTopics = [];

        let callCount = 0;
        mockRedis.xreadgroup.callsFake(async () => {
          callCount++;
          consumer.running = false;
          return null;
        });

        const handler = sinon.stub().resolves();
        await consumer.consume(handler);

        // Let the sleep+loop run briefly
        await new Promise((r) => setTimeout(r, 200));
        consumer.running = false;
        await consumer.consumeLoopPromise;

        // xreadgroup should not have been called (loop slept and exited)
        expect(handler.called).to.be.false;
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
        // Each topic triggers xgroup CREATE + xgroup DESTROY = 2 calls per topic
        expect(mockRedis.xgroup.callCount).to.equal(4);
        expect(mockRedis.quit.calledOnce).to.be.true;
      });

      it('should skip existing streams', async () => {
        const topics = [{ topic: 'existing-stream' }];
        mockRedis.exists.resolves(1);
        await admin.ensureTopicsExist(topics);
        expect(mockRedis.xgroup.called).to.be.false;
      });

      it('should collect per-topic errors and throw', async () => {
        const topics = [{ topic: 'bad-stream' }];
        mockRedis.exists.rejects(new Error('Redis error'));
        try {
          await admin.ensureTopicsExist(topics);
          expect.fail('Should have thrown');
        } catch (error) {
          expect((error as Error).message).to.include('Failed to ensure 1 Redis stream(s)');
        }
        expect(mockLogger.error.called).to.be.true;
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
        mockRedis.scan.resolves(['0', ['stream-1', 'hash-key', 'stream-2']]);
        mockRedis.type
          .onFirstCall().resolves('stream')
          .onSecondCall().resolves('hash')
          .onThirdCall().resolves('stream');

        const result = await admin.listTopics();
        expect(result).to.deep.equal(['stream-1', 'stream-2']);
        expect(mockRedis.connect.calledOnce).to.be.true;
        expect(mockRedis.quit.calledOnce).to.be.true;
      });

      it('should return empty array when no streams exist', async () => {
        mockRedis.scan.resolves(['0', []]);
        const result = await admin.listTopics();
        expect(result).to.deep.equal([]);
      });

      it('should handle multiple scan iterations', async () => {
        mockRedis.scan
          .onFirstCall().resolves(['42', ['stream-a']])
          .onSecondCall().resolves(['0', ['stream-b']]);
        mockRedis.type.resolves('stream');

        const result = await admin.listTopics();
        expect(result).to.deep.equal(['stream-a', 'stream-b']);
        expect(mockRedis.scan.callCount).to.equal(2);
      });
    });
  });
});
