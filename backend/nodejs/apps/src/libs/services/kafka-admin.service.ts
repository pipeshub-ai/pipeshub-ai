import { Kafka, Admin, ITopicConfig, ITopicMetadata } from 'kafkajs';
import { KafkaConfig } from '../types/kafka.types';
import { BrokerTopic, IMessageAdmin, TopicDefinition } from '../types/messaging.types';
import {
  ENV_KAFKA_TOPIC_PARTITIONS,
  KAFKA_ADMIN_CLIENT_ID,
} from '../constants/messaging.constants';
import { MessageBrokerType } from '../types/messaging.types';
import { Logger } from './logger.service';
import { parsePositiveIntSafe } from '../utils/env.utils';

/**
 * Partition count for the indexing topic.
 *
 * On Kafka a fair-scheduling lane *is* a partition, so this is what bounds
 * how many keys can be isolated from one another -- and, because the indexing
 * consumer holds a partition for a record's whole lifetime, it is also the
 * ceiling on indexing concurrency. Default 1 preserves existing behaviour.
 */
const DEFAULT_TOPIC_PARTITIONS = 1;

/** Message from a caught value, which is not necessarily an Error. */
function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function configuredPartitions(): number {
  return parsePositiveIntSafe(
    process.env[ENV_KAFKA_TOPIC_PARTITIONS],
    DEFAULT_TOPIC_PARTITIONS,
    ENV_KAFKA_TOPIC_PARTITIONS,
  );
}

// Required topics for the application. Only the indexing topic is laned;
// entity/sync events are low volume with no fairness problem to solve.
export const REQUIRED_TOPICS: TopicDefinition[] = Object.values(
  BrokerTopic,
).map((topic) => ({
  topic,
  numPartitions:
    topic === BrokerTopic.RECORD_EVENTS ? configuredPartitions() : 1,
  replicationFactor: 1,
}));

/** @deprecated Use REQUIRED_TOPICS instead */
export const REQUIRED_KAFKA_TOPICS = REQUIRED_TOPICS;

export class KafkaAdminService implements IMessageAdmin {
  private kafka: Kafka;
  private admin: Admin;
  private logger: Logger;

  constructor(config: KafkaConfig, logger: Logger) {
    this.logger = logger;
    this.kafka = new Kafka({
      clientId: config.clientId ?? KAFKA_ADMIN_CLIENT_ID,
      brokers: config.brokers,
      ssl: config.ssl,
      sasl: config.sasl,
    });
    this.admin = this.kafka.admin();
  }

  /**
   * Raises the partition count of topics that already exist.
   *
   * createTopics only ever creates; an install that already has
   * record-events at one partition would otherwise stay there forever and
   * never get lanes. Kafka allows increasing partitions but never
   * decreasing, so this is one-way and idempotent: a topic already at or
   * above the configured count is left alone.
   *
   * Increasing partitions moves existing keys to different partitions once.
   * That is safe here because per-record serialisation comes from the
   * `record:<id>` lease rather than from partition ordering, but it is
   * logged because it is a real, one-time change in message placement.
   */
  private async growPartitions(existing: TopicDefinition[]): Promise<void> {
    const wanted = existing.filter((t) => (t.numPartitions ?? 1) > 1);
    if (wanted.length === 0) {
      return;
    }

    let metadata: { topics: ITopicMetadata[] };
    try {
      metadata = await this.admin.fetchTopicMetadata({
        topics: wanted.map((t) => t.topic),
      });
    } catch (error: unknown) {
      this.logger.warn(
        'Could not read topic metadata; leaving partition counts unchanged',
        { error: errorMessage(error) },
      );
      return;
    }

    const currentCounts = new Map(
      metadata.topics.map((t) => [t.name, t.partitions.length]),
    );

    const toGrow = wanted.filter((t) => {
      const current = currentCounts.get(t.topic);
      return current !== undefined && current < (t.numPartitions ?? 1);
    });

    if (toGrow.length === 0) {
      return;
    }

    try {
      await this.admin.createPartitions({
        topicPartitions: toGrow.map((t) => ({
          topic: t.topic,
          count: t.numPartitions ?? 1,
        })),
        timeout: 30000,
      });
      for (const t of toGrow) {
        this.logger.info(
          `Increased partitions for ${t.topic}: ${currentCounts.get(t.topic)} -> ${t.numPartitions}`,
        );
      }
    } catch (error: unknown) {
      // Non-fatal: the app runs fine at the current partition count, just
      // with fewer lanes than configured.
      this.logger.warn('Failed to increase Kafka partition count', {
        error: errorMessage(error),
        topics: toGrow.map((t) => t.topic),
      });
    }
  }

  /**
   * Ensures all required topics exist in the Kafka cluster.
   * Creates any missing topics with the specified configuration.
   * This is especially important for AWS MSK where auto.create.topics.enable is disabled by default.
   */
  async ensureTopicsExist(
    topics: TopicDefinition[] = REQUIRED_TOPICS,
  ): Promise<void> {
    try {
      await this.admin.connect();
      this.logger.info('Connected to Kafka admin client');

      const existingTopics = await this.admin.listTopics();
      const topicsToCreate = topics.filter(
        (t) => !existingTopics.includes(t.topic),
      );

      await this.growPartitions(
        topics.filter((t) => existingTopics.includes(t.topic)),
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
      if (error.type === 'TOPIC_ALREADY_EXISTS') {
        this.logger.info('Topics already exist (concurrent creation detected)');
        return;
      }

      this.logger.error('Failed to ensure Kafka topics exist', {
        error: (error as Error).message,
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
  async describeTopics(
    topics: string[],
  ): Promise<{ topics: ITopicMetadata[] }> {
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
    type: MessageBrokerType.KAFKA,
    clientId: KAFKA_ADMIN_CLIENT_ID,
    brokers: kafkaConfig.brokers,
    ssl: kafkaConfig.ssl,
    sasl: kafkaConfig.sasl,
  };

  const adminService = new KafkaAdminService(config, logger);
  await adminService.ensureTopicsExist(topics);
}
