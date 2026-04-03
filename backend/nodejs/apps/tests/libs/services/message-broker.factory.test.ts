import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { EventEmitter } from 'events';
import { createMockLogger, MockLogger } from '../../helpers/mock-logger';
import { KafkaConfig } from '../../../src/libs/types/kafka.types';
import { RedisBrokerConfig } from '../../../src/libs/types/messaging.types';
import { ENV_MESSAGE_BROKER } from '../../../src/libs/constants/messaging.constants';

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
    process.nextTick(() => {
      this.emit('connect');
      this.emit('ready');
    });
  }
}

function ensureIoredisMock() {
  const ioredisPath = require.resolve('ioredis');
  const original = require.cache[ioredisPath];
  const FakeRedis = function (this: any, _opt: any) {
    return Object.assign(this, new FakeRedisForFactory(_opt));
  } as any;
  FakeRedis.prototype = FakeRedisForFactory.prototype;
  require.cache[ioredisPath] = {
    ...original!,
    exports: { Redis: FakeRedis, default: FakeRedis, RedisOptions: {} },
  } as any;

  // Reload the modules that import ioredis
  const rsPath = require.resolve(
    '../../../src/libs/services/redis-streams.service',
  );
  delete require.cache[rsPath];
  const factoryPath = require.resolve(
    '../../../src/libs/services/message-broker.factory',
  );
  delete require.cache[factoryPath];
}

// Set up the mock before any module that uses ioredis is loaded
ensureIoredisMock();

import {
  getMessageBrokerType,
  createMessageProducerForBrokerType,
  createMessageConsumerForBrokerType,
  createMessageAdminForBrokerType,
  buildRedisBrokerConfig,
  ensureMessageTopicsExist,
  ensureMessageTopicsExistFromConfig,
  resolveMessageBrokerConfig,
  createMessageProducer,
  createMessageConsumer,
  createMessageProducerFromConfig,
  REQUIRED_TOPICS,
} from '../../../src/libs/services/message-broker.factory';

import {
  BaseKafkaProducerConnection,
  BaseKafkaConsumerConnection,
} from '../../../src/libs/services/kafka.service';
import { KafkaAdminService } from '../../../src/libs/services/kafka-admin.service';
import {
  BaseRedisStreamsProducerConnection,
  BaseRedisStreamsConsumerConnection,
  RedisStreamsAdminService,
} from '../../../src/libs/services/redis-streams.service';

describe('MessageBrokerFactory', () => {
  let mockLogger: MockLogger;

  const kafkaConfig: KafkaConfig = {
    type: 'kafka',
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
      expect(() => getMessageBrokerType()).to.throw(
        `Unsupported ${ENV_MESSAGE_BROKER} type`,
      );
    });
  });

  // ================================================================
  // createMessageProducerForBrokerType
  // ================================================================
  describe('createMessageProducerForBrokerType', () => {
    it('should return a Kafka producer when broker type is kafka', () => {
      const producer = createMessageProducerForBrokerType(
        'kafka',
        kafkaConfig,
        undefined,
        mockLogger as any,
      );
      expect(producer).to.be.instanceOf(BaseKafkaProducerConnection);
    });

    it('should return a Redis producer when broker type is redis', () => {
      const producer = createMessageProducerForBrokerType(
        'redis',
        undefined,
        redisConfig,
        mockLogger as any,
      );
      expect(producer).to.be.instanceOf(BaseRedisStreamsProducerConnection);
    });

    it('should throw when kafka config is missing for kafka broker', () => {
      expect(() =>
        createMessageProducerForBrokerType(
          'kafka',
          undefined,
          undefined,
          mockLogger as any,
        ),
      ).to.throw('Kafka config is required');
    });

    it('should throw when redis config is missing for redis broker', () => {
      expect(() =>
        createMessageProducerForBrokerType(
          'redis',
          undefined,
          undefined,
          mockLogger as any,
        ),
      ).to.throw('Redis config is required');
    });
  });

  // ================================================================
  // createMessageConsumerForBrokerType
  // ================================================================
  describe('createMessageConsumerForBrokerType', () => {
    it('should return a Kafka consumer when broker type is kafka', () => {
      const consumer = createMessageConsumerForBrokerType(
        'kafka',
        kafkaConfig,
        undefined,
        mockLogger as any,
      );
      expect(consumer).to.be.instanceOf(BaseKafkaConsumerConnection);
    });

    it('should return a Redis consumer when broker type is redis', () => {
      const consumer = createMessageConsumerForBrokerType(
        'redis',
        undefined,
        redisConfig,
        mockLogger as any,
      );
      expect(consumer).to.be.instanceOf(BaseRedisStreamsConsumerConnection);
    });

    it('should throw when kafka config is missing for kafka broker', () => {
      expect(() =>
        createMessageConsumerForBrokerType(
          'kafka',
          undefined,
          undefined,
          mockLogger as any,
        ),
      ).to.throw('Kafka config is required');
    });

    it('should throw when redis config is missing for redis broker', () => {
      expect(() =>
        createMessageConsumerForBrokerType(
          'redis',
          undefined,
          undefined,
          mockLogger as any,
        ),
      ).to.throw('Redis config is required');
    });
  });

  // ================================================================
  // createMessageAdminForBrokerType
  // ================================================================
  describe('createMessageAdminForBrokerType', () => {
    it('should return a KafkaAdminService when broker type is kafka', () => {
      const admin = createMessageAdminForBrokerType(
        'kafka',
        kafkaConfig,
        undefined,
        mockLogger as any,
      );
      expect(admin).to.be.instanceOf(KafkaAdminService);
    });

    it('should return a RedisStreamsAdminService when broker type is redis', () => {
      const admin = createMessageAdminForBrokerType(
        'redis',
        undefined,
        redisConfig,
        mockLogger as any,
      );
      expect(admin).to.be.instanceOf(RedisStreamsAdminService);
    });

    it('should throw when kafka config is missing for kafka broker', () => {
      expect(() =>
        createMessageAdminForBrokerType(
          'kafka',
          undefined,
          undefined,
          mockLogger as any,
        ),
      ).to.throw('Kafka config is required');
    });

    it('should throw when redis config is missing for redis broker', () => {
      expect(() =>
        createMessageAdminForBrokerType(
          'redis',
          undefined,
          undefined,
          mockLogger as any,
        ),
      ).to.throw('Redis config is required');
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
      const ensureStub = sinon
        .stub(KafkaAdminService.prototype, 'ensureTopicsExist')
        .resolves();
      await ensureMessageTopicsExist(
        {
          type: 'kafka',
          kafka: { type: 'kafka', brokers: ['localhost:9092'] },
        },
        mockLogger as any,
      );
      expect(ensureStub.calledOnce).to.be.true;
    });

    it('should create admin and call ensureTopicsExist for redis', async () => {
      const ensureStub = sinon
        .stub(RedisStreamsAdminService.prototype, 'ensureTopicsExist')
        .resolves();
      await ensureMessageTopicsExist(
        { type: 'redis', redis: redisConfig },
        mockLogger as any,
      );
      expect(ensureStub.calledOnce).to.be.true;
    });

    it('should pass custom topics when provided', async () => {
      const ensureStub = sinon
        .stub(KafkaAdminService.prototype, 'ensureTopicsExist')
        .resolves();
      const customTopics = [
        { topic: 'custom-topic', numPartitions: 3, replicationFactor: 2 },
      ];
      await ensureMessageTopicsExist(
        {
          type: 'kafka',
          kafka: { type: 'kafka', brokers: ['localhost:9092'] },
        },
        mockLogger as any,
        customTopics,
      );
      expect(ensureStub.firstCall.args[0]).to.deep.equal(customTopics);
    });
  });

  // ================================================================
  // resolveMessageBrokerConfig
  // ================================================================
  describe('resolveMessageBrokerConfig', () => {
    it('should resolve kafka when MESSAGE_BROKER=kafka', () => {
      process.env.MESSAGE_BROKER = 'kafka';
      const r = resolveMessageBrokerConfig({
        kafka: { brokers: ['localhost:9092'] },
        redis: { host: 'localhost', port: 6379 },
      } as any);
      expect(r.type).to.equal('kafka');
      expect((r as any).kafka.brokers).to.deep.equal(['localhost:9092']);
    });

    it('should resolve redis when MESSAGE_BROKER=redis', () => {
      process.env.MESSAGE_BROKER = 'redis';
      const r = resolveMessageBrokerConfig({
        kafka: { brokers: ['localhost:9092'] },
        redis: { host: 'localhost', port: 6379 },
      } as any);
      expect(r.type).to.equal('redis');
      expect((r as any).redis.host).to.equal('localhost');
    });

    it('should throw when kafka brokers array is empty', () => {
      process.env.MESSAGE_BROKER = 'kafka';
      expect(() =>
        resolveMessageBrokerConfig({
          kafka: { brokers: [] },
          redis: { host: 'localhost', port: 6379 },
        } as any),
      ).to.throw('Kafka brokers');
    });

    it('should throw when redis host is empty string', () => {
      process.env.MESSAGE_BROKER = 'redis';
      expect(() =>
        resolveMessageBrokerConfig({
          kafka: { brokers: ['localhost:9092'] },
          redis: { host: '', port: 6379 },
        } as any),
      ).to.throw('Redis host');
    });
  });

  // ================================================================
  // createMessageProducer (high-level, takes ResolvedMessageBrokerConfig)
  // ================================================================
  describe('createMessageProducer', () => {
    it('should create a Kafka producer from resolved kafka config', () => {
      const resolved = { type: 'kafka' as const, kafka: kafkaConfig };
      const producer = createMessageProducer(resolved, mockLogger as any);
      expect(producer).to.be.instanceOf(BaseKafkaProducerConnection);
    });

    it('should create a Redis producer from resolved redis config', () => {
      const resolved = { type: 'redis' as const, redis: redisConfig };
      const producer = createMessageProducer(resolved, mockLogger as any);
      expect(producer).to.be.instanceOf(BaseRedisStreamsProducerConnection);
    });
  });

  // ================================================================
  // createMessageConsumer (high-level, takes ResolvedMessageBrokerConfig)
  // ================================================================
  describe('createMessageConsumer', () => {
    it('should create a Kafka consumer from resolved kafka config', () => {
      const resolved = { type: 'kafka' as const, kafka: kafkaConfig };
      const consumer = createMessageConsumer(resolved, mockLogger as any);
      expect(consumer).to.be.instanceOf(BaseKafkaConsumerConnection);
    });

    it('should create a Redis consumer from resolved redis config', () => {
      const resolved = { type: 'redis' as const, redis: redisConfig };
      const consumer = createMessageConsumer(resolved, mockLogger as any);
      expect(consumer).to.be.instanceOf(BaseRedisStreamsConsumerConnection);
    });
  });

  // ================================================================
  // createMessageProducerFromConfig
  // ================================================================
  describe('createMessageProducerFromConfig', () => {
    it('should create a kafka producer from AppConfig', () => {
      process.env.MESSAGE_BROKER = 'kafka';
      const appConfig = {
        kafka: {
          clientId: 'app',
          brokers: ['localhost:9092'],
          groupId: 'app-group',
        },
        redis: { host: 'localhost', port: 6379 },
      } as any;
      const producer = createMessageProducerFromConfig(
        appConfig,
        mockLogger as any,
      );
      expect(producer).to.be.instanceOf(BaseKafkaProducerConnection);
    });

    it('should create a redis producer from AppConfig', () => {
      process.env.MESSAGE_BROKER = 'redis';
      const appConfig = {
        kafka: { brokers: ['localhost:9092'] },
        redis: { host: 'localhost', port: 6379 },
      } as any;
      const producer = createMessageProducerFromConfig(
        appConfig,
        mockLogger as any,
      );
      expect(producer).to.be.instanceOf(BaseRedisStreamsProducerConnection);
    });
  });

  // ================================================================
  // ensureMessageTopicsExistFromConfig
  // ================================================================
  describe('ensureMessageTopicsExistFromConfig', () => {
    it('should ensure topics for kafka via AppConfig', async () => {
      process.env.MESSAGE_BROKER = 'kafka';
      const ensureStub = sinon
        .stub(KafkaAdminService.prototype, 'ensureTopicsExist')
        .resolves();
      const appConfig = {
        kafka: {
          clientId: 'app',
          brokers: ['localhost:9092'],
          groupId: 'app-group',
        },
        redis: { host: 'localhost', port: 6379 },
      } as any;
      await ensureMessageTopicsExistFromConfig(appConfig, mockLogger as any);
      expect(ensureStub.calledOnce).to.be.true;
    });

    it('should ensure topics for redis via AppConfig', async () => {
      process.env.MESSAGE_BROKER = 'redis';
      const ensureStub = sinon
        .stub(RedisStreamsAdminService.prototype, 'ensureTopicsExist')
        .resolves();
      const appConfig = {
        kafka: { brokers: ['localhost:9092'] },
        redis: { host: 'localhost', port: 6379 },
      } as any;
      await ensureMessageTopicsExistFromConfig(appConfig, mockLogger as any);
      expect(ensureStub.calledOnce).to.be.true;
    });

    it('should pass custom topics when provided', async () => {
      process.env.MESSAGE_BROKER = 'kafka';
      const ensureStub = sinon
        .stub(KafkaAdminService.prototype, 'ensureTopicsExist')
        .resolves();
      const customTopics = [
        { topic: 'custom', numPartitions: 1, replicationFactor: 1 },
      ];
      await ensureMessageTopicsExistFromConfig(
        {
          kafka: { clientId: 'a', brokers: ['b:9092'], groupId: 'g' },
          redis: { host: 'localhost', port: 6379 },
        } as any,
        mockLogger as any,
        customTopics,
      );
      expect(ensureStub.firstCall.args[0]).to.deep.equal(customTopics);
    });
  });
});
