import { Kafka, Admin, ITopicConfig } from 'kafkajs';
import { KafkaConfig } from '../types/kafka.types';
import { Logger } from './logger.service';

export interface TopicDefinition {
  topic: string;
  numPartitions?: number;
  replicationFactor?: number;
}

// Required topics for the application
export const REQUIRED_KAFKA_TOPICS: TopicDefinition[] = [
  { topic: 'record-events', numPartitions: 1, replicationFactor: 1 },
  { topic: 'entity-events', numPartitions: 1, replicationFactor: 1 },
  { topic: 'sync-events', numPartitions: 1, replicationFactor: 1 },
  { topic: 'health-check', numPartitions: 1, replicationFactor: 1 },
];

export class KafkaAdminService {
  private kafka: Kafka;
  private admin: Admin;
  private logger: Logger;

  constructor(config: KafkaConfig, logger: Logger) {
    this.logger = logger;
    this.kafka = new Kafka({
      clientId: config.clientId || 'pipeshub-admin',
      brokers: config.brokers,
      ssl: config.ssl,
      sasl: config.sasl,
    });
    this.admin = this.kafka.admin();
  }

  /**
   * Ensures all required topics exist in the Kafka cluster.
   * Creates any missing topics with the specified configuration.
   * This is especially important for AWS MSK where auto.create.topics.enable is disabled by default.
   */
  async ensureTopicsExist(
    topics: TopicDefinition[] = REQUIRED_KAFKA_TOPICS,
  ): Promise<void> {
    try {
      await this.admin.connect();
      this.logger.info('Connected to Kafka admin client');

      const existingTopics = await this.admin.listTopics();
      const topicsToCreate = topics.filter(
        (t) => !existingTopics.includes(t.topic),
      );

      if (topicsToCreate.length === 0) {
        this.logger.info('All required Kafka topics already exist', {
          topics: topics.map((t) => t.topic),
        });
        return;
      }

      const topicConfigs: ITopicConfig[] = topicsToCreate.map((t) => ({
        topic: t.topic,
        numPartitions: t.numPartitions ?? 1,
        replicationFactor: t.replicationFactor ?? 1,
      }));

      const result = await this.admin.createTopics({
        topics: topicConfigs,
        waitForLeaders: true,
        timeout: 30000,
      });

      if (result) {
        this.logger.info(
          `Successfully created Kafka topics: ${topicsToCreate.map((t) => t.topic).join(', ')}`,
        );
      } else {
        this.logger.info('Topics may already exist or creation was skipped');
      }
    } catch (error: any) {
      // Handle the case where topics already exist (race condition)
      if (error.type === 'TOPIC_ALREADY_EXISTS') {
        this.logger.info('Topics already exist (concurrent creation detected)');
        return;
      }

      this.logger.error('Failed to ensure Kafka topics exist', {
        error: error.message || error,
      });
      throw error;
    } finally {
      try {
        await this.admin.disconnect();
        this.logger.debug('Disconnected from Kafka admin client');
      } catch (disconnectError) {
        this.logger.warn('Error disconnecting Kafka admin client', {
          error: disconnectError,
        });
      }
    }
  }

  /**
   * Lists all topics in the Kafka cluster
   */
  async listTopics(): Promise<string[]> {
    try {
      await this.admin.connect();
      const topics = await this.admin.listTopics();
      return topics;
    } finally {
      await this.admin.disconnect();
    }
  }

  /**
   * Describes the configuration of specified topics
   */
  async describeTopics(topics: string[]): Promise<any> {
    try {
      await this.admin.connect();
      const metadata = await this.admin.fetchTopicMetadata({ topics });
      return metadata;
    } finally {
      await this.admin.disconnect();
    }
  }
}

/**
 * Utility function to ensure Kafka topics exist during application startup.
 * Safe to call multiple times - will only create topics that don't exist.
 */
export async function ensureKafkaTopicsExist(
  kafkaConfig: {
    brokers: string[];
    ssl?: boolean;
    sasl?: {
      mechanism: 'plain' | 'scram-sha-256' | 'scram-sha-512';
      username: string;
      password: string;
    };
  },
  logger: Logger,
  topics?: TopicDefinition[],
): Promise<void> {
  const config: KafkaConfig = {
    clientId: 'pipeshub-admin',
    brokers: kafkaConfig.brokers,
    ssl: kafkaConfig.ssl,
    sasl: kafkaConfig.sasl,
  };

  const adminService = new KafkaAdminService(config, logger);
  await adminService.ensureTopicsExist(topics);
}
