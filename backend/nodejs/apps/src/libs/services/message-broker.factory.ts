import { Logger } from './logger.service';
import { KafkaConfig } from '../types/kafka.types';
import {
  IMessageAdmin,
  IMessageProducer,
  IMessageConsumer,
  MessageBrokerType,
  RedisConfig,
  RedisBrokerConfig,
  TopicDefinition,
} from '../types/messaging.types';
import {
  BaseKafkaProducerConnection,
  BaseKafkaConsumerConnection,
} from './kafka.service';
import { KafkaAdminService, REQUIRED_TOPICS } from './kafka-admin.service';
import {
  BaseRedisStreamsProducerConnection,
  BaseRedisStreamsConsumerConnection,
  RedisStreamsAdminService,
} from './redis-streams.service';
import { AppConfig } from '../../modules/tokens_manager/config/config';
import { loadMessagingEnv } from '../config/messaging.env';
import {
  MESSAGING_ERRORS,
  MESSAGE_BROKER_TYPES,
} from '../constants/messaging.constants';

export { REQUIRED_TOPICS } from './kafka-admin.service';

export type ResolvedMessageBrokerConfig =
  | { type: 'kafka'; kafka: KafkaConfig }
  | { type: 'redis'; redis: RedisBrokerConfig };

export function getMessageBrokerType(): MessageBrokerType {
  const { messageBrokerRaw } = loadMessagingEnv();
  const brokerType = messageBrokerRaw.toLowerCase();
  if (
    brokerType !== MESSAGE_BROKER_TYPES.kafka &&
    brokerType !== MESSAGE_BROKER_TYPES.redis
  ) {
    throw new Error(MESSAGING_ERRORS.unsupportedBrokerType(brokerType));
  }
  return brokerType as MessageBrokerType;
}

class ConcreteKafkaProducer extends BaseKafkaProducerConnection {}

class ConcreteKafkaConsumer extends BaseKafkaConsumerConnection {}

class ConcreteRedisProducer extends BaseRedisStreamsProducerConnection {}

class ConcreteRedisConsumer extends BaseRedisStreamsConsumerConnection {}

function createMessageProducerByParts(
  brokerType: MessageBrokerType,
  kafkaConfig: KafkaConfig | undefined,
  redisConfig: RedisBrokerConfig | undefined,
  logger: Logger,
): IMessageProducer {
  if (brokerType === 'kafka') {
    if (!kafkaConfig) {
      throw new Error(MESSAGING_ERRORS.kafkaConfigRequired);
    }
    return new ConcreteKafkaProducer(kafkaConfig, logger);
  }
  if (!redisConfig) {
    throw new Error(MESSAGING_ERRORS.redisConfigRequired);
  }
  return new ConcreteRedisProducer(redisConfig, logger);
}

function createMessageConsumerByParts(
  brokerType: MessageBrokerType,
  kafkaConfig: KafkaConfig | undefined,
  redisConfig: RedisBrokerConfig | undefined,
  logger: Logger,
): IMessageConsumer {
  if (brokerType === 'kafka') {
    if (!kafkaConfig) {
      throw new Error(MESSAGING_ERRORS.kafkaConfigRequired);
    }
    return new ConcreteKafkaConsumer(kafkaConfig, logger);
  }
  if (!redisConfig) {
    throw new Error(MESSAGING_ERRORS.redisConfigRequired);
  }
  return new ConcreteRedisConsumer(redisConfig, logger);
}

function createMessageAdminByParts(
  brokerType: MessageBrokerType,
  kafkaConfig: KafkaConfig | undefined,
  redisConfig: RedisBrokerConfig | undefined,
  logger: Logger,
): IMessageAdmin {
  if (brokerType === 'kafka') {
    if (!kafkaConfig) {
      throw new Error(MESSAGING_ERRORS.kafkaConfigRequired);
    }
    return new KafkaAdminService(kafkaConfig, logger);
  }
  if (!redisConfig) {
    throw new Error(MESSAGING_ERRORS.redisConfigRequired);
  }
  return new RedisStreamsAdminService(redisConfig, logger);
}

export function resolveMessageBrokerConfig(
  appConfig: AppConfig,
): ResolvedMessageBrokerConfig {
  const brokerType = getMessageBrokerType();
  if (brokerType === 'kafka') {
    if (appConfig.kafka.brokers.length === 0) {
      throw new Error(MESSAGING_ERRORS.kafkaBrokersRequired);
    }
    const kafka: KafkaConfig = {
      type: 'kafka',
      ...appConfig.kafka,
    };
    return { type: 'kafka', kafka };
  }
  if (appConfig.redis.host === '') {
    throw new Error(MESSAGING_ERRORS.redisHostRequired);
  }
  return {
    type: 'redis',
    redis: buildRedisBrokerConfig(appConfig.redis),
  };
}

function resolvedToParts(resolved: ResolvedMessageBrokerConfig): {
  brokerType: MessageBrokerType;
  kafka: KafkaConfig | undefined;
  redis: RedisBrokerConfig | undefined;
} {
  if (resolved.type === 'kafka') {
    return {
      brokerType: 'kafka',
      kafka: resolved.kafka,
      redis: undefined,
    };
  }
  return {
    brokerType: 'redis',
    kafka: undefined,
    redis: resolved.redis,
  };
}

export function createMessageProducer(
  resolved: ResolvedMessageBrokerConfig,
  logger: Logger,
): IMessageProducer {
  const { brokerType, kafka, redis } = resolvedToParts(resolved);
  return createMessageProducerByParts(brokerType, kafka, redis, logger);
}

export function createMessageConsumer(
  resolved: ResolvedMessageBrokerConfig,
  logger: Logger,
): IMessageConsumer {
  const { brokerType, kafka, redis } = resolvedToParts(resolved);
  return createMessageConsumerByParts(brokerType, kafka, redis, logger);
}

export function buildRedisBrokerConfig(
  redisConfig: RedisConfig,
  options?: { clientId?: string; groupId?: string },
): RedisBrokerConfig {
  const env = loadMessagingEnv();
  return {
    type: 'redis',
    host: redisConfig.host,
    port: redisConfig.port,
    password: redisConfig.password,
    db: redisConfig.db,
    maxLen: env.redisStreamsMaxLen,
    keyPrefix: env.redisStreamsKeyPrefix,
    clientId: options?.clientId,
    groupId: options?.groupId,
  };
}

export function createMessageProducerFromConfig(
  appConfig: AppConfig,
  logger: Logger,
): IMessageProducer {
  return createMessageProducer(resolveMessageBrokerConfig(appConfig), logger);
}

export async function ensureMessageTopicsExist(
  resolved: ResolvedMessageBrokerConfig,
  logger: Logger,
  topics?: TopicDefinition[],
): Promise<void> {
  const { brokerType, kafka, redis } = resolvedToParts(resolved);
  const admin = createMessageAdminByParts(brokerType, kafka, redis, logger);
  await admin.ensureTopicsExist(topics ?? REQUIRED_TOPICS);
}

export async function ensureMessageTopicsExistFromConfig(
  appConfig: AppConfig,
  logger: Logger,
  topics?: TopicDefinition[],
): Promise<void> {
  await ensureMessageTopicsExist(
    resolveMessageBrokerConfig(appConfig),
    logger,
    topics,
  );
}

/** @internal Low-level factory for tests and advanced callers */
export function createMessageProducerForBrokerType(
  brokerType: MessageBrokerType,
  kafkaConfig: KafkaConfig | undefined,
  redisConfig: RedisBrokerConfig | undefined,
  logger: Logger,
): IMessageProducer {
  return createMessageProducerByParts(
    brokerType,
    kafkaConfig,
    redisConfig,
    logger,
  );
}

/** @internal */
export function createMessageConsumerForBrokerType(
  brokerType: MessageBrokerType,
  kafkaConfig: KafkaConfig | undefined,
  redisConfig: RedisBrokerConfig | undefined,
  logger: Logger,
): IMessageConsumer {
  return createMessageConsumerByParts(
    brokerType,
    kafkaConfig,
    redisConfig,
    logger,
  );
}

/** @internal */
export function createMessageAdminForBrokerType(
  brokerType: MessageBrokerType,
  kafkaConfig: KafkaConfig | undefined,
  redisConfig: RedisBrokerConfig | undefined,
  logger: Logger,
): IMessageAdmin {
  return createMessageAdminByParts(
    brokerType,
    kafkaConfig,
    redisConfig,
    logger,
  );
}
