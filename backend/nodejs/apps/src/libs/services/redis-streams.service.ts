import { injectable, unmanaged } from 'inversify';
import { Redis, RedisOptions } from 'ioredis';

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
  MESSAGING_HEALTH_MESSAGE_KEY,
  MESSAGING_HEALTH_MESSAGE_TYPE,
  MESSAGING_HEALTH_TOPIC,
  REDIS_BUSYGROUP_SUBSTRING,
  REDIS_STREAM_ADMIN_TEMP_GROUP,
  REDIS_STREAM_FIELDS,
  REDIS_STREAM_MAXLEN_STRATEGY,
  REDIS_STREAMS_DEFAULTS,
} from '../constants/messaging.constants';

type RedisStreamEntry = [id: string, fields: string[]];
type RedisXReadGroupResult = [stream: string, entries: RedisStreamEntry[]];

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

function buildRedisOptions(config: RedisBrokerConfig): RedisOptions {
  return {
    host: config.host,
    port: config.port,
    password: config.password,
    db: config.db ?? 0,
    retryStrategy: (times: number) => {
      const maxRetryTime =
        config.maxRetryTime ?? REDIS_STREAMS_DEFAULTS.maxRetryTime;
      const delay = Math.min(times * 200, maxRetryTime);
      return delay;
    },
    lazyConnect: true,
  };
}

@injectable()
export abstract class BaseRedisStreamsProducerConnection
  implements IMessageProducer
{
  protected redis: Redis;
  protected initialized = false;
  protected maxLen: number;

  constructor(
    @unmanaged() protected readonly config: RedisBrokerConfig,
    @unmanaged() protected readonly logger: Logger,
  ) {
    this.maxLen = config.maxLen ?? REDIS_STREAMS_DEFAULTS.maxLen;
    this.redis = new Redis(buildRedisOptions(config));
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
        details: error instanceof Error ? error.message : 'Unknown error',
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
        error: error instanceof Error ? error.message : 'Unknown error',
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
        JSON.stringify(message.value),
      ];

      if (message.headers) {
        fields.push(
          REDIS_STREAM_FIELDS.headers,
          JSON.stringify(message.headers),
        );
      }

      await this.redis.xadd(
        topic,
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
          details: error instanceof Error ? error.message : 'Unknown error',
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
        JSON.stringify(message.value),
      ];
      if (message.headers) {
        fields.push(
          REDIS_STREAM_FIELDS.headers,
          JSON.stringify(message.headers),
        );
      }

      pipeline.xadd(
        topic,
        'MAXLEN',
        REDIS_STREAM_MAXLEN_STRATEGY,
        String(this.maxLen),
        '*',
        ...fields,
      );
    }

    try {
      await pipeline.exec();
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
          details: error instanceof Error ? error.message : 'Unknown error',
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
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      return false;
    }
  }
}

@injectable()
export abstract class BaseRedisStreamsConsumerConnection
  implements IMessageConsumer
{
  protected redis: Redis;
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
    this.consumerId = config.clientId ?? `consumer-${String(Date.now())}`;
    this.blockMs = REDIS_STREAMS_DEFAULTS.blockMs;
    this.count = REDIS_STREAMS_DEFAULTS.count;
    this.redis = new Redis(buildRedisOptions(config));
  }

  async connect(): Promise<void> {
    try {
      if (!this.initialized) {
        await this.redis.connect();
        this.initialized = true;
        this.logger.info('Successfully connected Redis Streams consumer');
      }
    } catch (error) {
      this.initialized = false;
      throw new MessageBrokerError('Failed to connect Redis Streams consumer', {
        details: error instanceof Error ? error.message : 'Unknown error',
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
        await this.redis.quit();
        this.initialized = false;
        this.logger.info('Successfully disconnected Redis Streams consumer');
      }
    } catch (error) {
      this.logger.error('Error disconnecting Redis Streams consumer', {
        error: error instanceof Error ? error.message : 'Unknown error',
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
          topic,
          this.groupId,
          _fromBeginning ? '0' : '$',
          'MKSTREAM',
        );
        this.logger.info(
          `Created consumer group ${this.groupId} for stream ${topic}`,
        );
      } catch (error: unknown) {
        const errorMessage =
          error instanceof Error ? error.message : 'Unknown error';
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

  private async consumeLoop<T>(
    handler: (message: StreamMessage<T>) => Promise<void>,
  ): Promise<void> {
    while (this.running) {
      try {
        if (this.subscribedTopics.length === 0) {
          await this.sleep(REDIS_STREAMS_DEFAULTS.idleSleepMs);
          continue;
        }

        const streams = this.subscribedTopics.flatMap((topic) => [topic, '>']);

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

        if (!isRedisXReadGroupResult(xreadResult)) {
          this.logger.warn('Unexpected Redis xreadgroup payload shape');
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
                await this.redis.xack(streamName, this.groupId, entryId);
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

              await handler(parsedMessage);

              await this.redis.xack(streamName, this.groupId, entryId);
            } catch (error) {
              this.logger.error('Error processing Redis stream message', {
                entryId,
                error: error instanceof Error ? error.message : 'Unknown error',
              });
            }
          }
        }
      } catch (error) {
        this.logger.error('Error in Redis Streams consume loop', {
          error: error instanceof Error ? error.message : 'Unknown error',
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
        error: error instanceof Error ? error.message : 'Unknown error',
      });
      return false;
    }
  }

  private sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}

export class RedisStreamsAdminService implements IMessageAdmin {
  private redis: Redis;
  private logger: Logger;

  constructor(config: RedisBrokerConfig, logger: Logger) {
    this.logger = logger;
    this.redis = new Redis({
      host: config.host,
      port: config.port,
      password: config.password,
      db: config.db ?? 0,
      lazyConnect: true,
    });
  }

  async ensureTopicsExist(
    topics: TopicDefinition[] = REQUIRED_TOPICS,
  ): Promise<void> {
    try {
      await this.redis.connect();
      this.logger.info('Connected to Redis for stream administration');

      for (const topicDef of topics) {
        try {
          const exists = await this.redis.exists(topicDef.topic);
          if (exists === 0) {
            await this.redis.xgroup(
              'CREATE',
              topicDef.topic,
              REDIS_STREAM_ADMIN_TEMP_GROUP,
              '$',
              'MKSTREAM',
            );
            await this.redis.xgroup(
              'DESTROY',
              topicDef.topic,
              REDIS_STREAM_ADMIN_TEMP_GROUP,
            );
            this.logger.info(`Created Redis stream: ${topicDef.topic}`);
          } else {
            this.logger.debug(`Redis stream already exists: ${topicDef.topic}`);
          }
        } catch (error: unknown) {
          this.logger.warn(`Error ensuring Redis stream ${topicDef.topic}`, {
            error: error instanceof Error ? error.message : 'Unknown error',
          });
        }
      }

      this.logger.info('All required Redis streams verified');
    } catch (error: unknown) {
      this.logger.error('Failed to ensure Redis streams exist', {
        error: error instanceof Error ? error.message : 'Unknown error',
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
      const keys = await this.redis.keys('*');
      const streams: string[] = [];
      for (const key of keys) {
        const type = await this.redis.type(key);
        if (type === 'stream') {
          streams.push(key);
        }
      }
      return streams;
    } finally {
      await this.redis.quit();
    }
  }
}
