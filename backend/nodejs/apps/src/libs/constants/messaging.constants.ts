import { MessageBrokerType } from '../types/messaging.types';

/** Environment variable names (single place for messaging-related env keys). */
export const ENV_MESSAGE_BROKER = 'MESSAGE_BROKER';
export const ENV_REDIS_STREAMS_MAXLEN = 'REDIS_STREAMS_MAXLEN';
export const ENV_REDIS_STREAMS_PREFIX = 'REDIS_STREAMS_PREFIX';

export const DEFAULT_MESSAGE_BROKER = MessageBrokerType.KAFKA;
export const DEFAULT_REDIS_STREAMS_MAXLEN = 10000;
export const DEFAULT_REDIS_STREAMS_PREFIX = '';

/** Kafka client id used for admin / topic bootstrap operations. */
export const KAFKA_ADMIN_CLIENT_ID = 'pipeshub-admin';

/** API / error layer */
export const MESSAGE_BROKER_ERROR_CODE = 'MESSAGE_BROKER_ERROR' as const;

export const MESSAGING_ERRORS = {
  unsupportedBrokerType: (brokerType: string) =>
    `Unsupported ${ENV_MESSAGE_BROKER} type: ${brokerType}. Must be '${MessageBrokerType.KAFKA}' or '${MessageBrokerType.REDIS}'.`,
  kafkaConfigRequired: `Kafka config is required when ${ENV_MESSAGE_BROKER}=${MessageBrokerType.KAFKA}`,
  redisConfigRequired: `Redis config is required when ${ENV_MESSAGE_BROKER}=${MessageBrokerType.REDIS}`,
  kafkaBrokersRequired: 'Kafka brokers are required in app configuration',
  redisHostRequired: 'Redis host is required in app configuration',
} as const;

/** Redis Streams: field names stored in stream entries */
export const REDIS_STREAM_FIELDS = {
  key: 'key',
  value: 'value',
  headers: 'headers',
} as const;

/** Redis Streams: XADD trimming */
export const REDIS_STREAM_MAXLEN_STRATEGY = '~' as const;

/** Temporary consumer group used to create a stream via XGROUP CREATE ... MKSTREAM */
export const REDIS_STREAM_ADMIN_TEMP_GROUP = 'admin_init';

/** Health probe topic / payload (aligned with REQUIRED_TOPICS) */
export const MESSAGING_HEALTH_TOPIC = 'health-check';
export const MESSAGING_HEALTH_MESSAGE_KEY = 'health-check';
export const MESSAGING_HEALTH_MESSAGE_TYPE = 'HEALTH_CHECK';

/** Redis Streams consumer/producer tuning */
export const REDIS_STREAMS_DEFAULTS = {
  maxLen: 10000,
  blockMs: 2000,
  count: 10,
  maxRetryTime: 30000,
  idleSleepMs: 100,
  errorBackoffMs: 1000,
} as const;

/** Redis CLI error substring when group already exists */
export const REDIS_BUSYGROUP_SUBSTRING = 'BUSYGROUP';
