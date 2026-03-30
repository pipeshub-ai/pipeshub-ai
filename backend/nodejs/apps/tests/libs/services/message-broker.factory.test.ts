import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { EventEmitter } from 'events';
import { createMockLogger, MockLogger } from '../../helpers/mock-logger';
import { KafkaConfig } from '../../../src/libs/types/kafka.types';
import { RedisBrokerConfig } from '../../../src/libs/types/messaging.types';

// ---------------------------------------------------------------------------
// Override ioredis mock to support `import { Redis } from 'ioredis'`
// ---------------------------------------------------------------------------
class FakeRedisForFactory extends EventEmitter {
  status = 'ready';
  connect = sinon.stub().resolves();
  quit = sinon.stub().resolves();
  xadd = sinon.stub().resolves('1-0');
  ping = sinon.stub().resolves('PONG');
  constructor(_options?: any) {
    super();
    process.nextTick(() => { this.emit('connect'); this.emit('ready'); });
  }
}

function ensureIoredisMock() {
  const ioredisPath = require.resolve('ioredis');
  const original = require.cache[ioredisPath];
  const FakeRedis = function(this: any, _opt: any) {
    return Object.assign(this, new FakeRedisForFactory(_opt));
  } as any;
  FakeRedis.prototype = FakeRedisForFactory.prototype;
  require.cache[ioredisPath] = {
    ...original!,
    exports: { Redis: FakeRedis, default: FakeRedis, RedisOptions: {} },
  } as any;

  // Reload the modules that import ioredis
  const rsPath = require.resolve('../../../src/libs/services/redis-streams.service');
  delete require.cache[rsPath];
  const factoryPath = require.resolve('../../../src/libs/services/message-broker.factory');
  delete require.cache[factoryPath];
}

// Set up the mock before any module that uses ioredis is loaded
ensureIoredisMock();

import {
  getMessageBrokerType,
  createMessageProducer,
  createMessageConsumer,
  createMessageAdmin,
  buildRedisBrokerConfig,
  ensureMessageTopicsExist,
  REQUIRED_TOPICS,
} from '../../../src/libs/services/message-broker.factory';

import { BaseKafkaProducerConnection, BaseKafkaConsumerConnection } from '../../../src/libs/services/kafka.service';
import { KafkaAdminService } from '../../../src/libs/services/kafka-admin.service';
import {
  BaseRedisStreamsProducerConnection,
  BaseRedisStreamsConsumerConnection,
  RedisStreamsAdminService,
} from '../../../src/libs/services/redis-streams.service';

describe('MessageBrokerFactory', () => {
  let mockLogger: MockLogger;

  const kafkaConfig: KafkaConfig = {
    clientId: 'test-client',
    brokers: ['localhost:9092'],
    groupId: 'test-group',
  };

  const redisConfig: RedisBrokerConfig = {
    type: 'redis',
    host: 'localhost',
    port: 6379,
    password: undefined,
    db: 0,
    maxLen: 10000,
    clientId: 'test-client',
    groupId: 'test-group',
  };

  beforeEach(() => {
    mockLogger = createMockLogger();
  });

  afterEach(() => {
    sinon.restore();
    delete process.env.MESSAGE_BROKER;
    delete process.env.REDIS_STREAMS_MAXLEN;
    delete process.env.REDIS_STREAMS_PREFIX;
  });

  // ================================================================
  // getMessageBrokerType
  // ================================================================
  describe('getMessageBrokerType', () => {
    it('should default to kafka when env var is not set', () => {
      delete process.env.MESSAGE_BROKER;
      expect(getMessageBrokerType()).to.equal('kafka');
    });

    it('should return kafka when env is kafka', () => {
      process.env.MESSAGE_BROKER = 'kafka';
      expect(getMessageBrokerType()).to.equal('kafka');
    });

    it('should return redis when env is redis', () => {
      process.env.MESSAGE_BROKER = 'redis';
      expect(getMessageBrokerType()).to.equal('redis');
    });

    it('should be case-insensitive', () => {
      process.env.MESSAGE_BROKER = 'KAFKA';
      expect(getMessageBrokerType()).to.equal('kafka');

      process.env.MESSAGE_BROKER = 'Redis';
      expect(getMessageBrokerType()).to.equal('redis');
    });

    it('should throw for unsupported broker types', () => {
      process.env.MESSAGE_BROKER = 'rabbitmq';
      expect(() => getMessageBrokerType()).to.throw('Unsupported MESSAGE_BROKER type');
    });
  });

  // ================================================================
  // createMessageProducer
  // ================================================================
  describe('createMessageProducer', () => {
    it('should return a Kafka producer when broker type is kafka', () => {
      const producer = createMessageProducer('kafka', kafkaConfig, undefined, mockLogger as any);
      expect(producer).to.be.instanceOf(BaseKafkaProducerConnection);
    });

    it('should return a Redis producer when broker type is redis', () => {
      const producer = createMessageProducer('redis', undefined, redisConfig, mockLogger as any);
      expect(producer).to.be.instanceOf(BaseRedisStreamsProducerConnection);
    });

    it('should throw when kafka config is missing for kafka broker', () => {
      expect(() => createMessageProducer('kafka', undefined, undefined, mockLogger as any))
        .to.throw('Kafka config is required');
    });

    it('should throw when redis config is missing for redis broker', () => {
      expect(() => createMessageProducer('redis', undefined, undefined, mockLogger as any))
        .to.throw('Redis config is required');
    });
  });

  // ================================================================
  // createMessageConsumer
  // ================================================================
  describe('createMessageConsumer', () => {
    it('should return a Kafka consumer when broker type is kafka', () => {
      const consumer = createMessageConsumer('kafka', kafkaConfig, undefined, mockLogger as any);
      expect(consumer).to.be.instanceOf(BaseKafkaConsumerConnection);
    });

    it('should return a Redis consumer when broker type is redis', () => {
      const consumer = createMessageConsumer('redis', undefined, redisConfig, mockLogger as any);
      expect(consumer).to.be.instanceOf(BaseRedisStreamsConsumerConnection);
    });

    it('should throw when kafka config is missing for kafka broker', () => {
      expect(() => createMessageConsumer('kafka', undefined, undefined, mockLogger as any))
        .to.throw('Kafka config is required');
    });

    it('should throw when redis config is missing for redis broker', () => {
      expect(() => createMessageConsumer('redis', undefined, undefined, mockLogger as any))
        .to.throw('Redis config is required');
    });
  });

  // ================================================================
  // createMessageAdmin
  // ================================================================
  describe('createMessageAdmin', () => {
    it('should return a KafkaAdminService when broker type is kafka', () => {
      const admin = createMessageAdmin('kafka', kafkaConfig, undefined, mockLogger as any);
      expect(admin).to.be.instanceOf(KafkaAdminService);
    });

    it('should return a RedisStreamsAdminService when broker type is redis', () => {
      const admin = createMessageAdmin('redis', undefined, redisConfig, mockLogger as any);
      expect(admin).to.be.instanceOf(RedisStreamsAdminService);
    });

    it('should throw when kafka config is missing for kafka broker', () => {
      expect(() => createMessageAdmin('kafka', undefined, undefined, mockLogger as any))
        .to.throw('Kafka config is required');
    });

    it('should throw when redis config is missing for redis broker', () => {
      expect(() => createMessageAdmin('redis', undefined, undefined, mockLogger as any))
        .to.throw('Redis config is required');
    });
  });

  // ================================================================
  // buildRedisBrokerConfig
  // ================================================================
  describe('buildRedisBrokerConfig', () => {
    it('should build config from redis connection params', () => {
      const config = buildRedisBrokerConfig(
        { host: 'redis.local', port: 6380, password: 'secret', db: 2 },
        { clientId: 'my-client', groupId: 'my-group' },
      );
      expect(config.type).to.equal('redis');
      expect(config.host).to.equal('redis.local');
      expect(config.port).to.equal(6380);
      expect(config.password).to.equal('secret');
      expect(config.db).to.equal(2);
      expect(config.clientId).to.equal('my-client');
      expect(config.groupId).to.equal('my-group');
    });

    it('should use REDIS_STREAMS_MAXLEN env var for maxLen', () => {
      process.env.REDIS_STREAMS_MAXLEN = '5000';
      const config = buildRedisBrokerConfig({ host: 'localhost', port: 6379 });
      expect(config.maxLen).to.equal(5000);
    });

    it('should default maxLen to 10000', () => {
      delete process.env.REDIS_STREAMS_MAXLEN;
      const config = buildRedisBrokerConfig({ host: 'localhost', port: 6379 });
      expect(config.maxLen).to.equal(10000);
    });

    it('should use REDIS_STREAMS_PREFIX env var for keyPrefix', () => {
      process.env.REDIS_STREAMS_PREFIX = 'myapp:';
      const config = buildRedisBrokerConfig({ host: 'localhost', port: 6379 });
      expect(config.keyPrefix).to.equal('myapp:');
    });

    it('should default keyPrefix to empty string', () => {
      delete process.env.REDIS_STREAMS_PREFIX;
      const config = buildRedisBrokerConfig({ host: 'localhost', port: 6379 });
      expect(config.keyPrefix).to.equal('');
    });
  });

  // ================================================================
  // REQUIRED_TOPICS
  // ================================================================
  describe('REQUIRED_TOPICS', () => {
    it('should export the required topics from kafka-admin.service', () => {
      expect(REQUIRED_TOPICS).to.be.an('array');
      const topicNames = REQUIRED_TOPICS.map((t: any) => t.topic);
      expect(topicNames).to.include('record-events');
      expect(topicNames).to.include('entity-events');
      expect(topicNames).to.include('sync-events');
      expect(topicNames).to.include('health-check');
    });
  });

  // ================================================================
  // ensureMessageTopicsExist
  // ================================================================
  describe('ensureMessageTopicsExist', () => {
    it('should create admin and call ensureTopicsExist for kafka', async () => {
      const ensureStub = sinon.stub(KafkaAdminService.prototype, 'ensureTopicsExist').resolves();
      await ensureMessageTopicsExist(
        'kafka',
        { brokers: ['localhost:9092'] },
        undefined,
        mockLogger as any,
      );
      expect(ensureStub.calledOnce).to.be.true;
    });

    it('should create admin and call ensureTopicsExist for redis', async () => {
      const ensureStub = sinon.stub(RedisStreamsAdminService.prototype, 'ensureTopicsExist').resolves();
      await ensureMessageTopicsExist(
        'redis',
        undefined,
        redisConfig,
        mockLogger as any,
      );
      expect(ensureStub.calledOnce).to.be.true;
    });

    it('should pass custom topics when provided', async () => {
      const ensureStub = sinon.stub(KafkaAdminService.prototype, 'ensureTopicsExist').resolves();
      const customTopics = [{ topic: 'custom-topic', numPartitions: 3, replicationFactor: 2 }];
      await ensureMessageTopicsExist(
        'kafka',
        { brokers: ['localhost:9092'] },
        undefined,
        mockLogger as any,
        customTopics,
      );
      expect(ensureStub.firstCall.args[0]).to.deep.equal(customTopics);
    });
  });
});
