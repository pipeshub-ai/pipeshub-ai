/**
 * Environment-backed settings for the message broker (Kafka / Redis Streams).
 * Keep `process.env` reads here only — not in services or factories.
 */

import {
  DEFAULT_MESSAGE_BROKER,
  DEFAULT_REDIS_STREAMS_MAXLEN,
  DEFAULT_REDIS_STREAMS_PREFIX,
  ENV_MESSAGE_BROKER,
  ENV_REDIS_STREAMS_MAXLEN,
  ENV_REDIS_STREAMS_PREFIX,
} from '../constants/messaging.constants';

export interface MessagingEnv {
  /** Raw value before normalization (e.g. kafka, KAFKA). */
  messageBrokerRaw: string;
  redisStreamsMaxLen: number;
  redisStreamsKeyPrefix: string;
}

export function loadMessagingEnv(): MessagingEnv {
  return {
    messageBrokerRaw: process.env[ENV_MESSAGE_BROKER] ?? DEFAULT_MESSAGE_BROKER,
    redisStreamsMaxLen: parseInt(
      process.env[ENV_REDIS_STREAMS_MAXLEN] ??
        String(DEFAULT_REDIS_STREAMS_MAXLEN),
      10,
    ),
    redisStreamsKeyPrefix:
      process.env[ENV_REDIS_STREAMS_PREFIX] ?? DEFAULT_REDIS_STREAMS_PREFIX,
  };
}
