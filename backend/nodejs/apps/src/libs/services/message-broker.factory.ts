import { Logger } from './logger.service';
import { KafkaConfig } from '../types/kafka.types';
import {
  IMessageAdmin,
  IMessageProducer,
  IMessageConsumer,
  MessageBrokerType,
  RedisBrokerConfig,
  TopicDefinition,
} from '../types/messaging.types';
import { BaseKafkaProducerConnection, BaseKafkaConsumerConnection } from './kafka.service';
import { KafkaAdminService, REQUIRED_TOPICS } from './kafka-admin.service';
import {
  BaseRedisStreamsProducerConnection,
  BaseRedisStreamsConsumerConnection,
  RedisStreamsAdminService,
} from './redis-streams.service';

export { REQUIRED_TOPICS } from './kafka-admin.service';

export function getMessageBrokerType(): MessageBrokerType {
  const brokerType = (process.env.MESSAGE_BROKER || 'kafka').toLowerCase();
  if (brokerType !== 'kafka' && brokerType !== 'redis') {
    throw new Error(`Unsupported MESSAGE_BROKER type: ${brokerType}. Must be 'kafka' or 'redis'.`);
  }
  return brokerType as MessageBrokerType;
}

class ConcreteKafkaProducer extends BaseKafkaProducerConnection {
  constructor(config: KafkaConfig, logger: Logger) {
    super(config, logger);
  }
}

class ConcreteKafkaConsumer extends BaseKafkaConsumerConnection {
  constructor(config: KafkaConfig, logger: Logger) {
    super(config, logger);
  }
}

class ConcreteRedisProducer extends BaseRedisStreamsProducerConnection {
  constructor(config: RedisBrokerConfig, logger: Logger) {
    super(config, logger);
  }
}

class ConcreteRedisConsumer extends BaseRedisStreamsConsumerConnection {
  constructor(config: RedisBrokerConfig, logger: Logger) {
    super(config, logger);
  }
}

export function createMessageProducer(
  brokerType: MessageBrokerType,
  kafkaConfig: KafkaConfig | undefined,
  redisConfig: RedisBrokerConfig | undefined,
  logger: Logger,
): IMessageProducer {
  if (brokerType === 'kafka') {
    if (!kafkaConfig) {
      throw new Error('Kafka config is required when MESSAGE_BROKER=kafka');
    }
    return new ConcreteKafkaProducer(kafkaConfig, logger);
  } else {
    if (!redisConfig) {
      throw new Error('Redis config is required when MESSAGE_BROKER=redis');
    }
    return new ConcreteRedisProducer(redisConfig, logger);
  }
}

export function createMessageConsumer(
  brokerType: MessageBrokerType,
  kafkaConfig: KafkaConfig | undefined,
  redisConfig: RedisBrokerConfig | undefined,
  logger: Logger,
): IMessageConsumer {
  if (brokerType === 'kafka') {
    if (!kafkaConfig) {
      throw new Error('Kafka config is required when MESSAGE_BROKER=kafka');
    }
    return new ConcreteKafkaConsumer(kafkaConfig, logger);
  } else {
    if (!redisConfig) {
      throw new Error('Redis config is required when MESSAGE_BROKER=redis');
    }
    return new ConcreteRedisConsumer(redisConfig, logger);
  }
}

export function createMessageAdmin(
  brokerType: MessageBrokerType,
  kafkaConfig: KafkaConfig | undefined,
  redisConfig: RedisBrokerConfig | undefined,
  logger: Logger,
): IMessageAdmin {
  if (brokerType === 'kafka') {
    if (!kafkaConfig) {
      throw new Error('Kafka config is required when MESSAGE_BROKER=kafka');
    }
    return new KafkaAdminService(kafkaConfig, logger);
  } else {
    if (!redisConfig) {
      throw new Error('Redis config is required when MESSAGE_BROKER=redis');
    }
    return new RedisStreamsAdminService(redisConfig, logger);
  }
}

export function buildRedisBrokerConfig(redisConfig: {
  host: string;
  port: number;
  password?: string;
  db?: number;
}, options?: { clientId?: string; groupId?: string }): RedisBrokerConfig {
  return {
    type: 'redis',
    host: redisConfig.host,
    port: redisConfig.port,
    password: redisConfig.password,
    db: redisConfig.db,
    maxLen: parseInt(process.env.REDIS_STREAMS_MAXLEN || '10000', 10),
    keyPrefix: process.env.REDIS_STREAMS_PREFIX || '',
    clientId: options?.clientId,
    groupId: options?.groupId,
  };
}

export async function ensureMessageTopicsExist(
  brokerType: MessageBrokerType,
  kafkaConfig: { brokers: string[]; ssl?: boolean; sasl?: any } | undefined,
  redisConfig: RedisBrokerConfig | undefined,
  logger: Logger,
  topics?: TopicDefinition[],
): Promise<void> {
  const admin = createMessageAdmin(
    brokerType,
    kafkaConfig ? { clientId: 'pipeshub-admin', ...kafkaConfig } : undefined,
    redisConfig,
    logger,
  );
  await admin.ensureTopicsExist(topics || REQUIRED_TOPICS);
}
