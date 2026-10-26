/**
 * Environment-backed settings for the message broker (Kafka / Redis Streams).
 * Keep `process.env` reads here only — not in services or factories.
 */

import {
  DEFAULT_MESSAGE_BROKER,
  DEFAULT_REDIS_STREAMS_MAXLEN,
  ENV_MESSAGE_BROKER,
  ENV_REDIS_STREAMS_MAXLEN,
  ENV_REDIS_STREAMS_RETENTION_MODE,
} from '../constants/messaging.constants';
import { parseIntSafe } from '../utils/env.utils';

export interface MessagingEnv {
  /** Raw value before normalization (e.g. kafka, KAFKA). */
  messageBrokerRaw: string;
  redisStreamsMaxLen: number;
  /** `after_ack` (default): no producer-side cap. `maxlen`: legacy XADD MAXLEN. */
  redisStreamsRetentionMode: 'after_ack' | 'maxlen';
}

function parseRetentionMode(raw: string | undefined): 'after_ack' | 'maxlen' {
  const mode = (raw ?? '').trim().toLowerCase() || 'after_ack';
  if (mode !== 'after_ack' && mode !== 'maxlen') {
    throw new Error(
      `${ENV_REDIS_STREAMS_RETENTION_MODE} must be "after_ack" or "maxlen", got "${raw}"`,
    );
  }
  return mode;
}

export function loadMessagingEnv(): MessagingEnv {
  return {
    messageBrokerRaw: process.env[ENV_MESSAGE_BROKER] ?? DEFAULT_MESSAGE_BROKER,
    redisStreamsMaxLen: parseIntSafe(
      process.env[ENV_REDIS_STREAMS_MAXLEN],
      DEFAULT_REDIS_STREAMS_MAXLEN,
    ),
    redisStreamsRetentionMode: parseRetentionMode(
      process.env[ENV_REDIS_STREAMS_RETENTION_MODE],
    ),
  };
}
