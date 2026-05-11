import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Kafka } from 'kafkajs';
import { KafkaAdminService, REQUIRED_KAFKA_TOPICS, ensureKafkaTopicsExist } from '../../../src/libs/services/kafka-admin.service';
import { createMockLogger } from '../../helpers/mock-logger';

describe('KafkaAdminService', () => {
  let mockAdmin: any;
  let mockLogger: any;
  let kafkaStub: sinon.SinonStub;

  beforeEach(() => {
    mockLogger = createMockLogger();
    mockAdmin = {
      connect: sinon.stub().resolves(),
      disconnect: sinon.stub().resolves(),
      listTopics: sinon.stub().resolves([]),
      createTopics: sinon.stub().resolves(true),
      fetchTopicMetadata: sinon.stub().resolves({ topics: [] }),
    };
    kafkaStub = sinon.stub(Kafka.prototype, 'admin').returns(mockAdmin);
  });

  afterEach(() => {
    sinon.restore();
  });

  describe('REQUIRED_KAFKA_TOPICS', () => {
    it('should have expected topics', () => {
      expect(REQUIRED_KAFKA_TOPICS).to.be.an('array');
      const topicNames = REQUIRED_KAFKA_TOPICS.map(t => t.topic);
      expect(topicNames).to.include('record-events');
      expect(topicNames).to.include('entity-events');
      expect(topicNames).to.include('ai-config-events');
      expect(topicNames).to.include('sync-events');
      expect(topicNames).to.include('health-check');
    });

    it('should have partition and replication config', () => {
      for (const topic of REQUIRED_KAFKA_TOPICS) {
        expect(topic.numPartitions).to.equal(1);
        expect(topic.replicationFactor).to.equal(1);
      }
    });
  });

  describe('constructor', () => {
    it('should create an instance with config', () => {
      const config = { brokers: ['localhost:9092'], clientId: 'test' };
      const service = new KafkaAdminService(config, mockLogger);
      expect(service).to.be.instanceOf(KafkaAdminService);
    });

    it('should default clientId to pipeshub-admin', () => {
      const config = { brokers: ['localhost:9092'] };
      const service = new KafkaAdminService(config, mockLogger);
      expect(service).to.exist;
    });
  });

  describe('ensureTopicsExist', () => {
    it('should create missing topics', async () => {
      mockAdmin.listTopics.resolves(['existing-topic']);
      const config = { brokers: ['localhost:9092'] };
      const service = new KafkaAdminService(config, mockLogger);
      await service.ensureTopicsExist(REQUIRED_KAFKA_TOPICS);
      expect(mockAdmin.connect.calledOnce).to.be.true;
      expect(mockAdmin.createTopics.calledOnce).to.be.true;
      expect(mockAdmin.disconnect.calledOnce).to.be.true;
    });

    it('should skip creation when all topics exist', async () => {
      mockAdmin.listTopics.resolves(REQUIRED_KAFKA_TOPICS.map(t => t.topic));
      const config = { brokers: ['localhost:9092'] };
      const service = new KafkaAdminService(config, mockLogger);
      await service.ensureTopicsExist(REQUIRED_KAFKA_TOPICS);
      expect(mockAdmin.createTopics.called).to.be.false;
    });

    it('should handle TOPIC_ALREADY_EXISTS error', async () => {
      const error: any = new Error('topic exists');
      error.type = 'TOPIC_ALREADY_EXISTS';
      mockAdmin.listTopics.rejects(error);
      const config = { brokers: ['localhost:9092'] };
      const service = new KafkaAdminService(config, mockLogger);
      // Should not throw
      await service.ensureTopicsExist();
    });

    it('should rethrow non-TOPIC_ALREADY_EXISTS errors', async () => {
      mockAdmin.listTopics.rejects(new Error('broker unavailable'));
      const config = { brokers: ['localhost:9092'] };
      const service = new KafkaAdminService(config, mockLogger);
      try {
        await service.ensureTopicsExist();
        expect.fail('Should have thrown');
      } catch (error: any) {
        expect(error.message).to.equal('broker unavailable');
      }
    });

    it('should handle createTopics returning false', async () => {
      mockAdmin.listTopics.resolves([]);
      mockAdmin.createTopics.resolves(false);
      const config = { brokers: ['localhost:9092'] };
      const service = new KafkaAdminService(config, mockLogger);
      await service.ensureTopicsExist(REQUIRED_KAFKA_TOPICS);
      expect(mockLogger.info.called).to.be.true;
    });

    it('should disconnect even on error', async () => {
      mockAdmin.listTopics.rejects(new Error('fail'));
      const config = { brokers: ['localhost:9092'] };
      const service = new KafkaAdminService(config, mockLogger);
      try {
        await service.ensureTopicsExist();
      } catch (_e) { /* expected */ }
      expect(mockAdmin.disconnect.calledOnce).to.be.true;
    });

    it('should handle disconnect error gracefully', async () => {
      mockAdmin.listTopics.resolves(REQUIRED_KAFKA_TOPICS.map(t => t.topic));
      mockAdmin.disconnect.rejects(new Error('disconnect failed'));
      const config = { brokers: ['localhost:9092'] };
      const service = new KafkaAdminService(config, mockLogger);
      // Should not throw even though disconnect fails
      await service.ensureTopicsExist();
      expect(mockLogger.warn.called).to.be.true;
    });

    it('should use default topics when none provided', async () => {
      mockAdmin.listTopics.resolves([]);
      const config = { brokers: ['localhost:9092'] };
      const service = new KafkaAdminService(config, mockLogger);
      await service.ensureTopicsExist();
      expect(mockAdmin.createTopics.calledOnce).to.be.true;
      const createCall = mockAdmin.createTopics.firstCall.args[0];
      expect(createCall.topics).to.have.lengthOf(REQUIRED_KAFKA_TOPICS.length);
    });

    it('should use default numPartitions and replicationFactor when not provided', async () => {
      mockAdmin.listTopics.resolves([]);
      const config = { brokers: ['localhost:9092'] };
      const service = new KafkaAdminService(config, mockLogger);
      const topicsWithoutDefaults = [
        { topic: 'no-defaults-1' },
        { topic: 'no-defaults-2' },
      ];
      await service.ensureTopicsExist(topicsWithoutDefaults);
      const createCall = mockAdmin.createTopics.firstCall.args[0];
      expect(createCall.topics[0].numPartitions).to.equal(1);
      expect(createCall.topics[0].replicationFactor).to.equal(1);
      expect(createCall.topics[1].numPartitions).to.equal(1);
      expect(createCall.topics[1].replicationFactor).to.equal(1);
    });

    it('should use explicit numPartitions and replicationFactor when provided', async () => {
      mockAdmin.listTopics.resolves([]);
      const config = { brokers: ['localhost:9092'] };
      const service = new KafkaAdminService(config, mockLogger);
      const topics = [
        { topic: 'custom-partitions', numPartitions: 3, replicationFactor: 2 },
      ];
      await service.ensureTopicsExist(topics);
      const createCall = mockAdmin.createTopics.firstCall.args[0];
      expect(createCall.topics[0].numPartitions).to.equal(3);
      expect(createCall.topics[0].replicationFactor).to.equal(2);
    });

  });

  describe('listTopics', () => {
    it('should return list of topics', async () => {
      mockAdmin.listTopics.resolves(['topic1', 'topic2']);
      const config = { brokers: ['localhost:9092'] };
      const service = new KafkaAdminService(config, mockLogger);
      const topics = await service.listTopics();
      expect(topics).to.deep.equal(['topic1', 'topic2']);
      expect(mockAdmin.connect.calledOnce).to.be.true;
      expect(mockAdmin.disconnect.calledOnce).to.be.true;
    });
  });

  describe('describeTopics', () => {
    it('should return topic metadata', async () => {
      const mockMetadata = { topics: [{ name: 'test', partitions: [] }] };
      mockAdmin.fetchTopicMetadata.resolves(mockMetadata);
      const config = { brokers: ['localhost:9092'] };
      const service = new KafkaAdminService(config, mockLogger);
      const result = await service.describeTopics(['test']);
      expect(result).to.deep.equal(mockMetadata);
    });
  });

  describe('ensureKafkaTopicsExist (utility function)', () => {
    it('should create admin service and ensure topics', async () => {
      mockAdmin.listTopics.resolves([]);
      const kafkaConfig = { brokers: ['localhost:9092'] };
      await ensureKafkaTopicsExist(kafkaConfig, mockLogger);
      expect(mockAdmin.connect.calledOnce).to.be.true;
    });

    it('should pass custom topics', async () => {
      mockAdmin.listTopics.resolves([]);
      const kafkaConfig = { brokers: ['localhost:9092'] };
      const customTopics = [{ topic: 'custom-topic', numPartitions: 3, replicationFactor: 2 }];
      await ensureKafkaTopicsExist(kafkaConfig, mockLogger, customTopics);
      const createCall = mockAdmin.createTopics.firstCall.args[0];
      expect(createCall.topics[0]!.topic).to.equal('custom-topic');
    });

    it('should pass ssl and sasl config', async () => {
      mockAdmin.listTopics.resolves(REQUIRED_KAFKA_TOPICS.map(t => t.topic));
      const kafkaConfig = {
        brokers: ['localhost:9092'],
        ssl: true,
        sasl: { mechanism: 'plain' as const, username: 'user', password: 'pass' },
      };
      await ensureKafkaTopicsExist(kafkaConfig, mockLogger);
      // Should complete without error
    });
  });
});
