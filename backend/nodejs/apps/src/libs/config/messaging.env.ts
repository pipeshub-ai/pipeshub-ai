/**
 * Environment-backed settings for the message broker (Kafka / Redis Streams).
 * Keep `process.env` reads here only — not in services or factories.
 */

import {
  DEFAULT_MESSAGE_BROKER,
  DEFAULT_REDIS_STREAMS_MAXLEN,
  ENV_MESSAGE_BROKER,
  ENV_REDIS_STREAMS_MAXLEN,
} from '../constants/messaging.constants';
import { parseIntSafe } from '../utils/env.utils';

export interface MessagingEnv {
  /** Raw value before normalization (e.g. kafka, KAFKA). */
  messageBrokerRaw: string;
  redisStreamsMaxLen: number;
}

export function loadMessagingEnv(): MessagingEnv {
  return {
    messageBrokerRaw: process.env[ENV_MESSAGE_BROKER] ?? DEFAULT_MESSAGE_BROKER,
    redisStreamsMaxLen: parseIntSafe(
      process.env[ENV_REDIS_STREAMS_MAXLEN],
      DEFAULT_REDIS_STREAMS_MAXLEN,
    ),
  };
}
