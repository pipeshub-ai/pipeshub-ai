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
import { MESSAGING_ERRORS } from '../constants/messaging.constants';

export { REQUIRED_TOPICS } from './kafka-admin.service';

export type ResolvedMessageBrokerConfig =
  | { type: MessageBrokerType.KAFKA; kafka: KafkaConfig }
  | { type: MessageBrokerType.REDIS; redis: RedisBrokerConfig };

export function getMessageBrokerType(): MessageBrokerType {
  const { messageBrokerRaw } = loadMessagingEnv();
  const brokerType = messageBrokerRaw.toLowerCase();
  if (
    brokerType !== MessageBrokerType.KAFKA &&
    brokerType !== MessageBrokerType.REDIS
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
  if (brokerType === MessageBrokerType.KAFKA) {
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
  if (brokerType === MessageBrokerType.KAFKA) {
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
  if (brokerType === MessageBrokerType.KAFKA) {
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
  if (brokerType === MessageBrokerType.KAFKA) {
    if (appConfig.kafka.brokers.length === 0) {
      throw new Error(MESSAGING_ERRORS.kafkaBrokersRequired);
    }
    const kafka: KafkaConfig = {
      type: MessageBrokerType.KAFKA,
      ...appConfig.kafka,
    };
    return { type: MessageBrokerType.KAFKA, kafka };
  }
  if (appConfig.redis.host === '') {
    throw new Error(MESSAGING_ERRORS.redisHostRequired);
  }
  return {
    type: MessageBrokerType.REDIS,
    redis: buildRedisBrokerConfig(appConfig.redis),
  };
}

function resolvedToParts(resolved: ResolvedMessageBrokerConfig): {
  brokerType: MessageBrokerType;
  kafka: KafkaConfig | undefined;
  redis: RedisBrokerConfig | undefined;
} {
  if (resolved.type === MessageBrokerType.KAFKA) {
    return {
      brokerType: MessageBrokerType.KAFKA,
      kafka: resolved.kafka,
      redis: undefined,
    };
  }
  return {
    brokerType: MessageBrokerType.REDIS,
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

const NOTIFICATION_CONSUMER_GROUP = 'notification-consumer-group';
const NOTIFICATION_CLIENT_ID = 'notification-consumer';

/** Dedicated consumer group/stream group for the notification topic (Kafka + Redis). */
export function createNotificationMessageConsumer(
  appConfig: AppConfig,
  logger: Logger,
): IMessageConsumer {
  const resolved = resolveMessageBrokerConfig(appConfig);
  if (resolved.type === MessageBrokerType.KAFKA) {
    const kafka: KafkaConfig = {
      ...resolved.kafka,
      clientId: NOTIFICATION_CLIENT_ID,
      groupId: NOTIFICATION_CONSUMER_GROUP,
    };
    return createMessageConsumerByParts(
      MessageBrokerType.KAFKA,
      kafka,
      undefined,
      logger,
    );
  }
  const redis = buildRedisBrokerConfig(appConfig.redis, {
    clientId: NOTIFICATION_CLIENT_ID,
    groupId: NOTIFICATION_CONSUMER_GROUP,
  });
  return createMessageConsumerByParts(
    MessageBrokerType.REDIS,
    undefined,
    redis,
    logger,
  );
}

/**
 * Refuse `REDIS_KEY_NAMESPACE` + `MESSAGE_BROKER=redis` (R9).
 *
 * The namespace isolates KV keys, the cache-invalidation channel, and the
 * BullMQ queue prefix -- but NOT Redis Streams. Stream names round-trip
 * through `XREADGROUP` into `XACK`, and the indexing consumer derives lane
 * numbers from them, so prefixing them is a change with real message-loss
 * risk that has to land on its own.
 *
 * Until it does, two releases pointed at one endpoint share every stream
 * *and* consumer group, so a message produced by release A can be delivered
 * to release B's consumer and acked there -- A never sees it. Setting the
 * namespace is an explicit request for isolation, so failing fast is the
 * honest answer; silently not isolating routes work to the wrong deployment.
 *
 * Mirrors `_reject_namespaced_redis_streams` in
 * `backend/python/app/services/messaging/messaging_factory.py`.
 */
function rejectNamespacedRedisStreams(): void {
  const namespace = (process.env.REDIS_KEY_NAMESPACE ?? '').trim();
  if (namespace) {
    throw new Error(
      `REDIS_KEY_NAMESPACE='${namespace}' does not isolate Redis Streams, so ` +
        "two deployments sharing this endpoint would consume each other's " +
        'messages. Use a separate Redis/Valkey instance per deployment for ' +
        'MESSAGE_BROKER=redis, or switch to MESSAGE_BROKER=kafka.',
    );
  }
}

export function buildRedisBrokerConfig(
  redisConfig: RedisConfig,
  options?: { clientId?: string; groupId?: string },
): RedisBrokerConfig {
  rejectNamespacedRedisStreams();
  const env = loadMessagingEnv();
  return {
    type: MessageBrokerType.REDIS,
    host: redisConfig.host,
    port: redisConfig.port,
    password: redisConfig.password,
    db: redisConfig.db,
    maxLen: env.redisStreamsMaxLen,
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
