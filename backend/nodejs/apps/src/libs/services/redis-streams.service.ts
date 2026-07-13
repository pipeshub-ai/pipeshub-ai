import { injectable, unmanaged } from 'inversify';

import { MessageBrokerError } from '../errors/messaging.errors';
import { Logger } from './logger.service';
import {
  IMessageProducer,
  IMessageConsumer,
  IMessageAdmin,
  StreamMessage,
  RedisBrokerConfig,
  TopicDefinition,
} from '../types/messaging.types';
import { REQUIRED_TOPICS } from './kafka-admin.service';
import {
  injectEnvelope,
  runWithRequestContext,
  ENVELOPE_REQUEST_ID,
  newAnonRoot,
  sanitizeRootId,
} from '../context/request-context';
import {
  MESSAGING_HEALTH_MESSAGE_KEY,
  MESSAGING_HEALTH_MESSAGE_TYPE,
  MESSAGING_HEALTH_TOPIC,
  REDIS_BUSYGROUP_SUBSTRING,
  REDIS_STREAM_ADMIN_TEMP_GROUP,
  REDIS_STREAM_FIELDS,
  REDIS_STREAM_MAXLEN_STRATEGY,
  REDIS_STREAMS_DEFAULTS,
} from '../constants/messaging.constants';
import {
  buildRedisClient,
  clusterAwareScan,
  RedisClient,
} from './redisClientFactory';

type RedisStreamEntry = [id: string, fields: string[]];
type RedisXReadGroupResult = [stream: string, entries: RedisStreamEntry[]];

/** Serialize a message value, stamping the trace id into the envelope. */
function serializeValueWithTrace(value: unknown): string {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return JSON.stringify(injectEnvelope({ ...(value as Record<string, unknown>) }));
  }
  return JSON.stringify(value);
}

/** Rebuild trace context from a consumed message value (fresh root if absent). */
function contextFromValue(value: unknown): { rootId: string } {
  const env = (value ?? {}) as Record<string, unknown>;
  return {
    rootId:
      sanitizeRootId(env[ENVELOPE_REQUEST_ID] as string | undefined) ??
      newAnonRoot(),
  };
}

function isRedisXReadGroupResult(
  value: unknown,
): value is RedisXReadGroupResult[] {
  if (!Array.isArray(value)) return false;
  return value.every((group) => {
    if (!Array.isArray(group) || group.length !== 2) return false;
    if (typeof group[0] !== 'string' || !Array.isArray(group[1])) return false;
    return (group[1] as unknown[]).every((entry) => {
      if (!Array.isArray(entry) || entry.length !== 2) return false;
      return (
        typeof entry[0] === 'string' &&
        Array.isArray(entry[1]) &&
        (entry[1] as unknown[]).every((f) => typeof f === 'string')
      );
    });
  });
}

function buildStreamsClient(config: RedisBrokerConfig): RedisClient {
  const maxRetryTime =
    config.maxRetryTime ?? REDIS_STREAMS_DEFAULTS.maxRetryTime;
  return buildRedisClient(config, {
    lazyConnect: true,
    retryDelayFactor: 200,
    retryDelayMax: maxRetryTime,
  });
}

/**
 * Redis Cluster hash-tagging for stream keys.
 *
 * XREADGROUP, XAUTOCLAIM, and pipelined XADD across multiple stream keys are
 * multi-key commands. Under Redis Cluster every key in a multi-key command
 * must hash to the same slot, or the server returns CROSSSLOT. We force
 * co-location by wrapping the topic name with a `{pipeshub}` hash tag in
 * cluster mode. The wrapping is bypassed in standalone mode so existing
 * deployments are not migrated unnecessarily.
 *
 * `streamKey` is forward (topic → Redis key); `topicFromStreamKey` is the
 * reverse (Redis key → topic) used when XREADGROUP returns the stream name.
 */
const STREAM_HASH_TAG = '{pipeshub}';
const STREAM_HASH_TAG_PREFIX = `${STREAM_HASH_TAG}:`;

function streamKey(
  config: { mode?: string },
  topic: string,
): string {
  return config.mode === 'cluster'
    ? `${STREAM_HASH_TAG_PREFIX}${topic}`
    : topic;
}

function topicFromStreamKey(
  config: { mode?: string },
  key: string,
): string {
  if (config.mode === 'cluster' && key.startsWith(STREAM_HASH_TAG_PREFIX)) {
    return key.slice(STREAM_HASH_TAG_PREFIX.length);
  }
  return key;
}

@injectable()
export abstract class BaseRedisStreamsProducerConnection
  implements IMessageProducer
{
  protected redis: RedisClient;
  protected initialized = false;
  protected maxLen: number;

  constructor(
    @unmanaged() protected readonly config: RedisBrokerConfig,
    @unmanaged() protected readonly logger: Logger,
  ) {
    this.maxLen = config.maxLen ?? REDIS_STREAMS_DEFAULTS.maxLen;
    this.redis = buildStreamsClient(config);
  }

  async connect(): Promise<void> {
    try {
      if (!this.initialized) {
        await this.redis.connect();
        this.initialized = true;
        this.logger.info('Successfully connected Redis Streams producer');
      }
    } catch (error) {
      this.initialized = false;
      throw new MessageBrokerError('Failed to connect Redis Streams producer', {
        details: (error as Error).message,
      });
    }
  }

  async disconnect(): Promise<void> {
    try {
      if (this.initialized) {
        await this.redis.quit();
        this.initialized = false;
        this.logger.info('Successfully disconnected Redis Streams producer');
      }
    } catch (error) {
      this.logger.error('Error disconnecting Redis Streams producer', {
        error: (error as Error).message,
      });
    }
  }

  isConnected(): boolean {
    return this.initialized && this.redis.status === 'ready';
  }

  protected async ensureConnection(): Promise<void> {
    if (!this.isConnected()) {
      await this.connect();
    }
  }

  async publish<T>(topic: string, message: StreamMessage<T>): Promise<void> {
    await this.ensureConnection();
    try {
      const fields: string[] = [
        REDIS_STREAM_FIELDS.key,
        message.key,
        REDIS_STREAM_FIELDS.value,
        serializeValueWithTrace(message.value),
      ];

      if (message.headers) {
        fields.push(
          REDIS_STREAM_FIELDS.headers,
          JSON.stringify(message.headers),
        );
      }

      await this.redis.xadd(
        streamKey(this.config, topic),
        'MAXLEN',
        REDIS_STREAM_MAXLEN_STRATEGY,
        String(this.maxLen),
        '*',
        ...fields,
      );

      this.logger.debug('Successfully published to Redis stream', {
        topic,
      });
    } catch (error) {
      throw new MessageBrokerError(
        `Error publishing to Redis stream ${topic}`,
        {
          topic,
          details: (error as Error).message,
        },
      );
    }
  }

  async publishBatch<T>(
    topic: string,
    messages: StreamMessage<T>[],
  ): Promise<void> {
    await this.ensureConnection();
    const pipeline = this.redis.pipeline();

    for (const message of messages) {
      const fields: string[] = [
        REDIS_STREAM_FIELDS.key,
        message.key,
        REDIS_STREAM_FIELDS.value,
        serializeValueWithTrace(message.value),
      ];
      if (message.headers) {
        fields.push(
          REDIS_STREAM_FIELDS.headers,
          JSON.stringify(message.headers),
        );
      }

      pipeline.xadd(
        streamKey(this.config, topic),
        'MAXLEN',
        REDIS_STREAM_MAXLEN_STRATEGY,
        String(this.maxLen),
        '*',
        ...fields,
      );
    }

    try {
      const results = await pipeline.exec();
      if (results) {
        const failures = results.filter(([err]) => err !== null);
        if (failures.length > 0) {
          throw new MessageBrokerError(
            `${failures.length}/${messages.length} failed in batch publish to ${topic}`,
            { topic, firstError: failures[0]![0]!.message },
          );
        }
      }
      this.logger.debug('Successfully published batch to Redis stream', {
        topic,
        messageCount: messages.length,
      });
    } catch (error) {
      throw new MessageBrokerError(
        `Error publishing batch to Redis stream ${topic}`,
        {
          topic,
          messageCount: messages.length,
          details: (error as Error).message,
        },
      );
    }
  }

  async healthCheck(): Promise<boolean> {
    try {
      await this.ensureConnection();
      await this.publish(MESSAGING_HEALTH_TOPIC, {
        key: MESSAGING_HEALTH_MESSAGE_KEY,
        value: {
          type: MESSAGING_HEALTH_MESSAGE_TYPE,
          timestamp: Date.now(),
        },
      });
      return true;
    } catch (error) {
      this.logger.error('Redis Streams producer health check failed', {
        error: (error as Error).message,
      });
      return false;
    }
  }
}

@injectable()
export abstract class BaseRedisStreamsConsumerConnection
  implements IMessageConsumer
{
  protected redis: RedisClient;
  /** Dedicated connection for XACK so it is never queued behind a blocked XREADGROUP. */
  protected ackRedis: RedisClient;
  protected initialized = false;
  protected running = false;
  protected subscribedTopics: string[] = [];
  protected groupId: string;
  protected consumerId: string;
  protected blockMs: number;
  protected count: number;
  private consumeLoopPromise: Promise<void> | null = null;

  constructor(
    @unmanaged() protected readonly config: RedisBrokerConfig,
    @unmanaged() protected readonly logger: Logger,
  ) {
    this.groupId =
      config.groupId ?? `${config.clientId ?? 'redis-consumer'}-group`;
    this.consumerId = config.clientId ?? 'consumer-' + crypto.randomUUID();
    this.blockMs = REDIS_STREAMS_DEFAULTS.blockMs;
    this.count = REDIS_STREAMS_DEFAULTS.count;
    this.redis = buildStreamsClient(config);
    this.ackRedis = buildStreamsClient(config);
  }

  async connect(): Promise<void> {
    try {
      if (!this.initialized) {
        await Promise.all([this.redis.connect(), this.ackRedis.connect()]);
        this.initialized = true;
        this.logger.info('Successfully connected Redis Streams consumer');
      }
    } catch (error) {
      this.initialized = false;
      throw new MessageBrokerError('Failed to connect Redis Streams consumer', {
        details: (error as Error).message,
      });
    }
  }

  async disconnect(): Promise<void> {
    try {
      this.running = false;
      if (this.consumeLoopPromise) {
        await this.consumeLoopPromise;
        this.consumeLoopPromise = null;
      }
      if (this.initialized) {
        await Promise.all([this.redis.quit(), this.ackRedis.quit()]);
        this.initialized = false;
        this.logger.info('Successfully disconnected Redis Streams consumer');
      }
    } catch (error) {
      this.logger.error('Error disconnecting Redis Streams consumer', {
        error: (error as Error).message,
      });
    }
  }

  isConnected(): boolean {
    return this.initialized && this.redis.status === 'ready';
  }

  protected async ensureConnection(): Promise<void> {
    if (!this.isConnected()) {
      await this.connect();
    }
  }

  async subscribe(topics: string[], _fromBeginning = false): Promise<void> {
    await this.ensureConnection();
    for (const topic of topics) {
      try {
        await this.redis.xgroup(
          'CREATE',
          streamKey(this.config, topic),
          this.groupId,
          _fromBeginning ? '0' : '$',
          'MKSTREAM',
        );
        this.logger.info(
          `Created consumer group ${this.groupId} for stream ${topic}`,
        );
      } catch (error: unknown) {
        const errorMessage =
          (error as Error).message;
        if (errorMessage.includes(REDIS_BUSYGROUP_SUBSTRING)) {
          this.logger.debug(
            `Consumer group ${this.groupId} already exists for stream ${topic}`,
          );
        } else {
          throw new MessageBrokerError('Failed to subscribe to Redis stream', {
            topic,
            details: errorMessage,
          });
        }
      }
    }
    this.subscribedTopics = [...new Set([...this.subscribedTopics, ...topics])];
    this.logger.info('Successfully subscribed to Redis streams', {
      topics: this.subscribedTopics,
    });
  }

  async consume<T>(
    handler: (message: StreamMessage<T>) => Promise<void>,
  ): Promise<void> {
    await this.ensureConnection();
    this.running = true;

    this.consumeLoopPromise = this.consumeLoop(handler);
  }

  /**
   * Drain messages left in the Pending Entries List (PEL) from a previous
   * crash.  Uses XAUTOCLAIM to steal idle messages from any consumer in the
   * group (including crashed ones), then processes and acks them.
   */
  private async drainPending<T>(
    handler: (message: StreamMessage<T>) => Promise<void>,
  ): Promise<void> {
    this.logger.info('Draining pending messages from PEL');

    for (const topic of this.subscribedTopics) {
      const streamName = streamKey(this.config, topic);
      let startId = '0-0';
      while (this.running) {
        try {
          const result = await this.redis.xautoclaim(
            streamName,
            this.groupId,
            this.consumerId,
            30000, // min-idle-time: claim all pending
            startId,
            'COUNT',
            '10',
          );

          // ioredis returns [nextStartId, [[id, fields], ...], deletedIds]
          const nextId = result[0] as string;
          const claimed = result[1] as RedisStreamEntry[];

          if (!claimed || claimed.length === 0) break;

          for (const entry of claimed) {
            const entryId = entry[0];
            const fields = entry[1];
            try {
              const fieldMap: Record<string, string> = {};
              for (let i = 0; i < fields.length; i += 2) {
                const key = fields[i];
                const value = fields[i + 1];
                if (key !== undefined && value !== undefined) {
                  fieldMap[key] = value;
                }
              }

              const rawValue = fieldMap[REDIS_STREAM_FIELDS.value];
              if (rawValue === undefined) {
                await this.ackRedis.xack(streamName, this.groupId, entryId);
                continue;
              }

              const parsedMessage: StreamMessage<T> = {
                key: fieldMap[REDIS_STREAM_FIELDS.key] ?? '',
                value: JSON.parse(rawValue) as T,
              };

              const rawHeaders = fieldMap[REDIS_STREAM_FIELDS.headers];
              if (rawHeaders !== undefined) {
                parsedMessage.headers = JSON.parse(rawHeaders) as Record<
                  string,
                  string
                >;
              }

              await runWithRequestContext(
                contextFromValue(parsedMessage.value),
                () => handler(parsedMessage),
              );
              await this.ackRedis.xack(streamName, this.groupId, entryId);
              this.logger.info('Recovered pending message', {
                stream: topic,
                id: entryId,
              });
            } catch (error) {
              this.logger.error('Error recovering pending message', {
                entryId,
                error: (error as Error).message,
              });
            }
          }

          startId = nextId;
          if (nextId === '0-0') break;
        } catch (error) {
          this.logger.error('Error during XAUTOCLAIM', {
            topic,
            error: (error as Error).message,
          });
          break;
        }
      }
    }

    this.logger.info('PEL drained, switching to new messages');
  }

  private async consumeLoop<T>(
    handler: (message: StreamMessage<T>) => Promise<void>,
  ): Promise<void> {
    await this.drainPending(handler);
    while (this.running) {
      try {
        if (this.subscribedTopics.length === 0) {
          await this.sleep(REDIS_STREAMS_DEFAULTS.idleSleepMs);
          continue;
        }

        // In cluster mode every stream key must hash to the same slot, so we
        // wrap with `{pipeshub}:` (see streamKey above). xreadgroup returns
        // the raw stream key, which is unwrapped back to the topic name when
        // we log / xack.
        const streams = this.subscribedTopics.flatMap((topic) => [
          streamKey(this.config, topic),
          '>',
        ]);

        const xreadResult = await this.redis.xreadgroup(
          'GROUP',
          this.groupId,
          this.consumerId,
          'COUNT',
          String(this.count),
          'BLOCK',
          String(this.blockMs),
          'STREAMS',
          ...streams,
        );

        if (xreadResult === null) {
          // Normal: BLOCK timeout expired with no new messages.
          continue;
        }

        if (!isRedisXReadGroupResult(xreadResult)) {
          this.logger.warn('Unexpected Redis xreadgroup payload shape', {
            type: typeof xreadResult,
            value: JSON.stringify(xreadResult),
          });
          continue;
        }

        for (const result of xreadResult) {
          const streamName = result[0];
          const entries = result[1];
          for (const entry of entries) {
            const entryId = entry[0];
            const fields = entry[1];
            try {
              const fieldMap: Record<string, string> = {};
              for (let i = 0; i < fields.length; i += 2) {
                const key = fields[i];
                const value = fields[i + 1];
                if (key !== undefined && value !== undefined) {
                  fieldMap[key] = value;
                }
              }

              const rawValue = fieldMap[REDIS_STREAM_FIELDS.value];
              if (rawValue === undefined) {
                this.logger.debug(
                  'Skipping message without value field (likely init message)',
                  {
                    stream: streamName,
                    id: entryId,
                  },
                );
                await this.ackRedis.xack(streamName, this.groupId, entryId);
                continue;
              }

              const parsedMessage: StreamMessage<T> = {
                key: fieldMap[REDIS_STREAM_FIELDS.key] ?? '',
                value: JSON.parse(rawValue) as T,
              };

              const rawHeaders = fieldMap[REDIS_STREAM_FIELDS.headers];
              if (rawHeaders !== undefined) {
                parsedMessage.headers = JSON.parse(rawHeaders) as Record<
                  string,
                  string
                >;
              }

              await runWithRequestContext(
                contextFromValue(parsedMessage.value),
                () => handler(parsedMessage),
              );

              await this.ackRedis.xack(streamName, this.groupId, entryId);
            } catch (error) {
              this.logger.error('Error processing Redis stream message', {
                entryId,
                error: (error as Error).message,
              });
            }
          }
        }
      } catch (error) {
        if (!this.running) break;
        this.logger.error('Error in Redis Streams consume loop', {
          error: (error as Error).message,
        });
        await this.sleep(REDIS_STREAMS_DEFAULTS.errorBackoffMs);
      }
    }
  }

  pause(_topics: string[]): void {
    // No-op: Redis Streams does not support pause/resume natively.
  }

  resume(_topics: string[]): void {
    // No-op: Redis Streams does not support pause/resume natively.
  }

  async healthCheck(): Promise<boolean> {
    try {
      await this.ensureConnection();
      await this.redis.ping();
      return true;
    } catch (error) {
      this.logger.error('Redis Streams consumer health check failed', {
        error: (error as Error).message,
      });
      return false;
    }
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}

export class RedisStreamsAdminService implements IMessageAdmin {
  private redis: RedisClient;
  private logger: Logger;
  private config: RedisBrokerConfig;

  constructor(config: RedisBrokerConfig, logger: Logger) {
    this.logger = logger;
    this.config = config;
    this.redis = buildStreamsClient(config);
  }

  async ensureTopicsExist(
    topics: TopicDefinition[] = REQUIRED_TOPICS,
  ): Promise<void> {
    try {
      await this.redis.connect();
      this.logger.info('Connected to Redis for stream administration');

      const failures: Array<{ topic: string; error: string }> = [];
      for (const topicDef of topics) {
        const key = streamKey(this.config, topicDef.topic);
        try {
          const exists = await this.redis.exists(key);
          if (exists === 0) {
            await this.redis.xgroup(
              'CREATE',
              key,
              REDIS_STREAM_ADMIN_TEMP_GROUP,
              '$',
              'MKSTREAM',
            );
            await this.redis.xgroup(
              'DESTROY',
              key,
              REDIS_STREAM_ADMIN_TEMP_GROUP,
            );
            this.logger.info(`Created Redis stream: ${topicDef.topic}`);
          } else {
            this.logger.debug(`Redis stream already exists: ${topicDef.topic}`);
          }
        } catch (error: unknown) {
          const msg = (error as Error).message;
          this.logger.error(`Failed to ensure Redis stream ${topicDef.topic}`, {
            error: msg,
          });
          failures.push({ topic: topicDef.topic, error: msg });
        }
      }

      if (failures.length > 0) {
        throw new Error(
          `Failed to ensure ${failures.length} Redis stream(s): ${failures.map((f) => f.topic).join(', ')}`,
        );
      }

      this.logger.info('All required Redis streams verified');
    } catch (error: unknown) {
      this.logger.error('Failed to ensure Redis streams exist', {
        error: (error as Error).message,
      });
      throw error;
    } finally {
      try {
        await this.redis.quit();
      } catch (disconnectError) {
        this.logger.warn('Error disconnecting Redis admin client', {
          error: disconnectError,
        });
      }
    }
  }

  async listTopics(): Promise<string[]> {
    try {
      await this.redis.connect();
      const keys = await clusterAwareScan(this.redis, '*', 100);
      const streams: string[] = [];
      for (const key of keys) {
        const type = await this.redis.type(key);
        if (type === 'stream') {
          // Unwrap the hash-tag prefix so callers see the topic name they
          // know, not the cluster-internal Redis key.
          streams.push(topicFromStreamKey(this.config, key));
        }
      }
      return streams;
    } finally {
      await this.redis.quit();
    }
  }
}
