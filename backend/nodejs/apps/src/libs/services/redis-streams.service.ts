import { injectable, unmanaged } from 'inversify';

import { MessageBrokerError } from '../errors/messaging.errors';
import { Logger } from './logger.service';
import { getRedisProvider } from './redis/connectionProviderFactory';
import { redisConnectionConfigFromHostPort } from './redis/connectionConfig';
import type {
  IRedisConnectionProvider,
  RedisClient,
} from './redis/connectionProvider.interface';
import { StreamReadPlanner } from './redis/streamReadPlanner';
import {
  IMessageProducer,
  IMessageConsumer,
  IMessageAdmin,
  IdleGroupSweep,
  IdleGroupSweepResult,
  StreamMessage,
  RedisBrokerConfig,
  TopicDefinition,
  BrokerTopic,
} from '../types/messaging.types';
import { REQUIRED_TOPICS } from './kafka-admin.service';
import { laneCount } from '../utils/lane.utils';
import {
  contextFromEnvelope,
  injectEnvelope,
  runWithRequestContext,
} from '../context/request-context';
import {
  EnvelopeRejected,
  runWithEnvelope,
  signMessage,
} from '../context/envelope';
import { spawnDetached } from '../context/orgContext';
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

/** Serialize a message value, stamping the trace id into the envelope. */
function serializeValueWithTrace(value: unknown, topic: string): string {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    return JSON.stringify(
      signMessage(
        topic,
        injectEnvelope({ ...(value as Record<string, unknown>) }),
      ),
    );
  }
  return JSON.stringify(value);
}

/** The message an entry carries; null for an entry without a value (a stream's init entry). */
function decodeEntry<T>(fields: string[]): StreamMessage<T> | null {
  const fieldMap: Record<string, string> = {};
  for (let i = 0; i < fields.length; i += 2) {
    const key = fields[i];
    const value = fields[i + 1];
    if (key !== undefined && value !== undefined) {
      fieldMap[key] = value;
    }
  }
  const rawValue = fieldMap[REDIS_STREAM_FIELDS.value];
  if (rawValue === undefined) return null;
  const message: StreamMessage<T> = {
    key: fieldMap[REDIS_STREAM_FIELDS.key] ?? '',
    value: JSON.parse(rawValue) as T,
  };
  const rawHeaders = fieldMap[REDIS_STREAM_FIELDS.headers];
  if (rawHeaders !== undefined) {
    message.headers = JSON.parse(rawHeaders) as Record<string, string>;
  }
  return message;
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

/**
 * Expands a laned topic into its lane streams.
 *
 * A Redis Streams lane is a separate stream (`record-events.3`), since
 * streams have no partitions. Pre-creating them is not required for
 * correctness -- `XGROUP CREATE ... MKSTREAM` makes one lazily -- but it
 * means lag dashboards and consumer subscriptions see every lane from
 * startup instead of only after the first message happens to land on it.
 *
 * With laning off (`FAIR_SCHEDULING_LANE_COUNT=1`) this returns the input unchanged.
 */
function expandLaneStreams(topics: TopicDefinition[]): TopicDefinition[] {
  const lanes = laneCount();
  if (lanes <= 1) {
    return topics;
  }

  return topics.flatMap((topicDef) => {
    if (topicDef.topic !== BrokerTopic.RECORD_EVENTS) {
      return [topicDef];
    }
    // The base stream stays, so anything written before laning was turned
    // on still has a stream to be drained from.
    return [
      topicDef,
      ...Array.from({ length: lanes }, (_unused, lane) => ({
        ...topicDef,
        topic: `${topicDef.topic}.${lane}`,
      })),
    ];
  });
}

function streamsProvider(config: RedisBrokerConfig): IRedisConnectionProvider {
  return getRedisProvider(
    redisConnectionConfigFromHostPort({
      host: config.host,
      port: config.port,
      password: config.password,
      db: config.db,
    }),
  );
}

/**
 * Every Redis Streams connection is long-lived, holds group/consumer state,
 * and blocks on XREADGROUP -- so it must always be a dedicated client
 * (`createClient`, never the shared `getClient()`), lazily connected so
 * callers control exactly when `connect()`/`disconnect()` happen (R12).
 *
 * XADD/XACK/XAUTOCLAIM are single-stream commands with no CROSSSLOT
 * concern; the multi-stream XREADGROUP path in the consumer is the one
 * that needs `StreamReadPlanner` (R1), not client construction itself.
 */
function createStreamsClient(config: RedisBrokerConfig): RedisClient {
  return streamsProvider(config).createClient({ blocking: true });
}

@injectable()
export abstract class BaseRedisStreamsProducerConnection
  implements IMessageProducer
{
  protected redis: RedisClient;
  protected initialized = false;
  /** Set only in legacy `maxlen` retention mode; otherwise nothing is trimmed on write. */
  protected maxLen: number | undefined;

  constructor(
    @unmanaged() protected readonly config: RedisBrokerConfig,
    @unmanaged() protected readonly logger: Logger,
  ) {
    this.maxLen = config.maxLen;
    this.redis = createStreamsClient(config);
  }

  /**
   * XADD trim arguments. By default nothing: MAXLEN ignores consumer progress
   * and drops unconsumed events; the Python indexing service trims behind the
   * consumer groups instead. The health-check stream has no consumer, so it
   * keeps a small fixed cap.
   */
  protected trimArgs(topic: string): string[] {
    if (topic === MESSAGING_HEALTH_TOPIC) {
      return [
        'MAXLEN',
        REDIS_STREAM_MAXLEN_STRATEGY,
        String(REDIS_STREAMS_DEFAULTS.healthMaxLen),
      ];
    }
    return this.maxLen === undefined
      ? []
      : ['MAXLEN', REDIS_STREAM_MAXLEN_STRATEGY, String(this.maxLen)];
  }

  async connect(): Promise<void> {
    try {
      if (!this.initialized) {
        await this.redis.connect();
        this.initialized = true;
        this.logger.info('Successfully connected Redis Streams producer');
        if (this.maxLen !== undefined) {
          this.logger.warn(
            'REDIS_STREAMS_RETENTION_MODE=maxlen: streams are capped and unconsumed events beyond the cap are dropped',
            { maxLen: this.maxLen },
          );
        }
      }
    } catch (error) {
      this.initialized = false;
      throw new MessageBrokerError('Failed to connect Redis Streams producer', {
        details: (error as Error).message,
      });
    }
  }

  async disconnect(): Promise<void> {
    if (!this.initialized) {
      return;
    }
    this.initialized = false;
    const closing = this.redis;
    this.redis = createStreamsClient(this.config);
    try {
      await closing.quit();
      this.logger.info('Successfully disconnected Redis Streams producer');
    } catch (error) {
      this.logger.error('Error disconnecting Redis Streams producer', {
        error: (error as Error).message,
      });
    } finally {
      // `finally`, not the try body: `quit()` on a client whose connection
      // already dropped rejects, and releasing only on the success path
      // retains one dead client per reconnect cycle -- exactly the leak this
      // release exists to prevent.
      streamsProvider(this.config).release(closing);
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
        serializeValueWithTrace(message.value, topic),
      ];

      if (message.headers) {
        fields.push(
          REDIS_STREAM_FIELDS.headers,
          JSON.stringify(message.headers),
        );
      }

      await this.redis.xadd(topic, ...this.trimArgs(topic), '*', ...fields);

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
        serializeValueWithTrace(message.value, topic),
      ];
      if (message.headers) {
        fields.push(
          REDIS_STREAM_FIELDS.headers,
          JSON.stringify(message.headers),
        );
      }

      pipeline.xadd(topic, ...this.trimArgs(topic), '*', ...fields);
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

  /** Read through a call: `stop()` clears `running` while a loop awaits, which narrowing cannot see. */
  protected stopped(): boolean {
    return !this.running;
  }
  protected subscribedTopics: string[] = [];
  protected groupId: string;
  protected consumerId: string;
  protected blockMs: number;
  protected count: number;
  private readonly claimMinIdleMs: number;
  private readonly maxDeliveries: number;
  private reclaimedAt = 0;
  protected readonly planner: StreamReadPlanner;
  private consumeLoopPromise: Promise<void> | null = null;

  constructor(
    @unmanaged() protected readonly config: RedisBrokerConfig,
    @unmanaged() protected readonly logger: Logger,
  ) {
    this.groupId =
      config.groupId ?? `${config.clientId ?? 'redis-consumer'}-group`;
    this.consumerId = config.clientId ?? 'consumer-' + crypto.randomUUID();
    this.blockMs = config.blockMs ?? REDIS_STREAMS_DEFAULTS.blockMs;
    this.count = config.readCount ?? REDIS_STREAMS_DEFAULTS.count;
    this.claimMinIdleMs =
      config.claimMinIdleMs ?? REDIS_STREAMS_DEFAULTS.claimMinIdleMs;
    this.maxDeliveries =
      config.maxDeliveries ?? REDIS_STREAMS_DEFAULTS.maxDeliveries;
    this.redis = createStreamsClient(config);
    this.ackRedis = createStreamsClient(config);
    this.planner = new StreamReadPlanner(streamsProvider(config));
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
      if (!this.initialized) {
        return;
      }
      this.initialized = false;
      const closing = [this.redis, this.ackRedis];
      this.redis = createStreamsClient(this.config);
      this.ackRedis = createStreamsClient(this.config);
      // `allSettled` + release outside the failure path: `Promise.all` rejects
      // as soon as either `quit()` does, so a single dropped connection used
      // to leave BOTH replaced clients tracked forever.
      const results = await Promise.allSettled(
        closing.map((client) => client.quit()),
      );
      const provider = streamsProvider(this.config);
      closing.forEach((client) => provider.release(client));
      const failure = results.find((r) => r.status === 'rejected');
      if (failure) {
        throw (failure as PromiseRejectedResult).reason;
      }
      this.logger.info('Successfully disconnected Redis Streams consumer');
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
          topic,
          this.groupId,
          _fromBeginning ? '0' : '$',
          'MKSTREAM',
        );
        this.logger.info(
          `Created consumer group ${this.groupId} for stream ${topic}`,
        );
      } catch (error: unknown) {
        const errorMessage = (error as Error).message;
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

    // The loop outlives this call: it must not keep the caller's org context.
    this.consumeLoopPromise = spawnDetached(() => this.consumeLoop(handler));
  }

  /** Run the handler under the message's trace and verified org context. A rejected envelope is dropped (and acked by the caller). */
  protected async handleMessage<T>(
    stream: string,
    entryId: string,
    message: StreamMessage<T>,
    handler: (message: StreamMessage<T>) => Promise<void>,
  ): Promise<void> {
    try {
      await runWithRequestContext(contextFromEnvelope(message.value), () =>
        runWithEnvelope(stream, message.value, (value) =>
          handler({ ...message, value }),
        ),
      );
    } catch (error) {
      if (!(error instanceof EnvelopeRejected)) throw error;
      this.logger.error('Envelope rejected, message dropped', {
        stream,
        entryId,
        reason: error.reason,
      });
    }
  }

  /**
   * A claim counts as a delivery. An entry claimed more often than
   * `maxDeliveries` has failed or outlived its handler that many times: it is
   * acknowledged and logged, so one message cannot be retried forever.
   */
  private async setAsideIfOverDelivered(
    topic: string,
    entryId: string,
  ): Promise<boolean> {
    const pending = (await this.ackRedis.xpending(
      topic,
      this.groupId,
      entryId,
      entryId,
      1,
    )) as Array<
      [id: string, consumer: string, idleMs: number, deliveries: number]
    >;
    const deliveries = pending[0]?.[3] ?? 0;
    if (deliveries <= this.maxDeliveries) return false;
    await this.ackRedis.xack(topic, this.groupId, entryId);
    this.logger.error('Set aside a message after its last delivery failed', {
      stream: topic,
      group: this.groupId,
      id: entryId,
      deliveries: deliveries - 1,
    });
    return true;
  }

  /**
   * Hands this consumer the entries another consumer of the group took and
   * never acknowledged (it crashed, or its handler failed), once they have
   * been idle for `claimMinIdleMs`. Runs at start and then every
   * `claimMinIdleMs`, so a live consumer picks them up without a restart.
   */
  private async drainPending<T>(
    handler: (message: StreamMessage<T>) => Promise<void>,
  ): Promise<void> {
    this.reclaimedAt = Date.now();
    this.logger.debug('Draining pending messages from PEL');

    for (const topic of this.subscribedTopics) {
      let startId = '0-0';
      while (this.running) {
        try {
          const result = await this.redis.xautoclaim(
            topic,
            this.groupId,
            this.consumerId,
            this.claimMinIdleMs,
            startId,
            'COUNT',
            String(this.count),
          );

          // ioredis returns [nextStartId, [[id, fields], ...], deletedIds]
          const nextId = result[0] as string;
          const claimed = result[1] as RedisStreamEntry[];

          // XAUTOCLAIM scans at most COUNT*10 pending entries per call, so an
          // empty page with a live cursor can still have stale entries past it.
          if (!claimed || claimed.length === 0) {
            if (nextId === '0-0') break;
            startId = nextId;
            continue;
          }

          for (const entry of claimed) {
            const entryId = entry[0];
            try {
              if (await this.setAsideIfOverDelivered(topic, entryId)) continue;
              const parsedMessage = decodeEntry<T>(entry[1]);
              if (parsedMessage === null) {
                await this.ackRedis.xack(topic, this.groupId, entryId);
                continue;
              }

              await this.handleMessage(topic, entryId, parsedMessage, handler);
              await this.ackRedis.xack(topic, this.groupId, entryId);
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

    this.logger.debug('PEL drained, switching to new messages');
  }

  /**
   * One `XREADGROUP` per hash-slot group of `this.subscribedTopics` (R1).
   *
   * On standalone every stream is in one group, so this is exactly the
   * single call this replaced, with the full `blockMs` budget and the
   * per-stream `count` unaffected (COUNT applies per-stream, not per
   * call). On Redis Cluster / MemoryDB, lane streams especially can span
   * slots, so each group gets its own call with a fair share of the
   * overall block budget -- an empty poll still costs at most `blockMs`
   * total, not once per group.
   */
  private async xreadGroupGrouped(): Promise<unknown> {
    const groups = this.planner.group(this.subscribedTopics);
    if (groups.length === 0) {
      return [];
    }
    if (groups.length === 1) {
      // Single group: pass the raw client response through unmodified
      // (may be `null` on an empty BLOCK timeout, handled by the caller).
      // Guarded by the length check above: groups[0] is always defined here.
      return this.xreadGroupCall(groups[0]!, this.blockMs);
    }
    const perGroupBlockMs = Math.max(
      1,
      Math.floor(this.blockMs / groups.length),
    );
    const combined: unknown[] = [];
    let firstError: unknown = null;
    for (const group of groups) {
      // Each group is isolated: one slot's node being mid-failover must not
      // discard what other groups already returned. `XREADGROUP >` hands a
      // message out exactly once, so letting one group's failure reject the
      // whole call would strand the successful groups' entries in the PEL
      // until an idle-triggered XAUTOCLAIM pass -- or indefinitely, if new
      // messages keep the consumer from ever going idle. Mirrors the Python
      // consumers' `_read_new_messages`.
      try {
        const results = await this.xreadGroupCall(group, perGroupBlockMs);
        if (results) {
          combined.push(...(results as unknown[]));
        }
      } catch (error) {
        this.logger.warn('XREADGROUP failed for slot group', {
          stream: group[0],
          streamCount: group.length,
          error,
        });
        firstError = firstError ?? error;
      }
    }
    // Only surface the failure when nothing at all came back, so the caller's
    // existing backoff still kicks in on a genuinely broken cluster.
    if (firstError !== null && combined.length === 0) {
      throw firstError;
    }
    return combined;
  }

  private async xreadGroupCall(
    streamNames: string[],
    blockMs: number,
  ): Promise<unknown> {
    const streams = streamNames.flatMap((topic) => [topic, '>']);
    return this.redis.xreadgroup(
      'GROUP',
      this.groupId,
      this.consumerId,
      'COUNT',
      String(this.count),
      'BLOCK',
      String(blockMs),
      'STREAMS',
      ...streams,
    );
  }

  protected async consumeLoop<T>(
    handler: (message: StreamMessage<T>) => Promise<void>,
  ): Promise<void> {
    while (this.running) {
      try {
        if (Date.now() - this.reclaimedAt >= this.claimMinIdleMs) {
          await this.drainPending(handler);
        }
        if (this.subscribedTopics.length === 0) {
          await this.sleep(REDIS_STREAMS_DEFAULTS.idleSleepMs);
          continue;
        }

        const xreadResult = await this.xreadGroupGrouped();

        if (
          xreadResult === null ||
          (Array.isArray(xreadResult) && xreadResult.length === 0)
        ) {
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
            try {
              const parsedMessage = decodeEntry<T>(entry[1]);
              if (parsedMessage === null) {
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

              await this.handleMessage(
                streamName,
                entryId,
                parsedMessage,
                handler,
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

  protected sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}

/**
 * A fan-out subscriber: every process that subscribes sees every message.
 *
 * A consumer group shares work among its members and remembers what each has
 * not acknowledged; a subscriber that must see everything wants neither. It
 * reads with XREAD from a position it keeps in memory, so the server holds
 * nothing for it and a process that dies leaves nothing behind (Redis never
 * expires a group). It starts after the last entry of the stream; what was
 * published while the process was not running is not replayed.
 */
export class RedisStreamsBroadcastConsumer extends BaseRedisStreamsConsumerConnection {
  private readonly positions = new Map<string, string>();

  override async subscribe(
    topics: string[],
    fromBeginning = false,
  ): Promise<void> {
    await this.ensureConnection();
    for (const topic of topics) {
      if (this.positions.has(topic)) continue;
      this.positions.set(
        topic,
        fromBeginning ? '0-0' : await this.lastEntryId(topic),
      );
    }
    this.subscribedTopics = [...this.positions.keys()];
  }

  /** Read once: `$` in every XREAD would skip what arrives between two reads. */
  private async lastEntryId(topic: string): Promise<string> {
    try {
      const last = await this.ackRedis.xrevrange(topic, '+', '-', 'COUNT', 1);
      return last[0]?.[0] ?? '0-0';
    } catch (error) {
      throw new MessageBrokerError('Failed to subscribe to Redis stream', {
        topic,
        details: (error as Error).message,
      });
    }
  }

  protected override async consumeLoop<T>(
    handler: (message: StreamMessage<T>) => Promise<void>,
  ): Promise<void> {
    while (this.running) {
      try {
        const groups = this.planner.group(this.subscribedTopics);
        if (groups.length === 0) {
          await this.sleep(REDIS_STREAMS_DEFAULTS.idleSleepMs);
          continue;
        }
        const blockMs = Math.max(1, Math.floor(this.blockMs / groups.length));
        for (const group of groups) {
          await this.deliver(await this.read(group, blockMs), handler);
        }
      } catch (error) {
        if (this.stopped()) break;
        this.logger.error('Error in Redis Streams broadcast loop', {
          error: (error as Error).message,
        });
        await this.sleep(REDIS_STREAMS_DEFAULTS.errorBackoffMs);
      }
    }
  }

  private read(topics: string[], blockMs: number): Promise<unknown> {
    return this.redis.xread(
      'COUNT',
      this.count,
      'BLOCK',
      blockMs,
      'STREAMS',
      ...topics,
      ...topics.map((topic) => this.positions.get(topic) ?? '0-0'),
    );
  }

  /** A handler that fails is logged and passed: no other process would get this subscriber's copy. */
  private async deliver<T>(
    read: unknown,
    handler: (message: StreamMessage<T>) => Promise<void>,
  ): Promise<void> {
    if (!isRedisXReadGroupResult(read)) return;
    for (const [topic, entries] of read) {
      for (const [entryId, fields] of entries) {
        try {
          const message = decodeEntry<T>(fields);
          if (message !== null) {
            await this.handleMessage(topic, entryId, message, handler);
          }
        } catch (error) {
          this.logger.error('Error processing Redis stream message', {
            entryId,
            error: (error as Error).message,
          });
        }
        this.positions.set(topic, entryId);
      }
    }
  }
}

function infoFields(flat: unknown): Map<string, unknown> {
  const fields = new Map<string, unknown>();
  if (!Array.isArray(flat)) return fields;
  for (let i = 0; i + 1 < flat.length; i += 2) {
    fields.set(String(flat[i]), flat[i + 1]);
  }
  return fields;
}

export class RedisStreamsAdminService implements IMessageAdmin {
  private readonly provider: IRedisConnectionProvider;
  private readonly redis: RedisClient;
  private logger: Logger;

  constructor(config: RedisBrokerConfig, logger: Logger) {
    this.logger = logger;
    this.provider = streamsProvider(config);
    this.redis = this.provider.getClient();
  }

  /**
   * Redis never expires a consumer group. A group is dead when every consumer
   * in it last asked for messages more than `idleMs` ago: a live consumer
   * asks again at least every block period, with or without traffic, and
   * Redis reports the time since as the consumer's `idle`.
   */
  async removeIdleGroups(sweep: IdleGroupSweep): Promise<IdleGroupSweepResult> {
    const { topic, formats, idleMs } = sweep;
    const result: IdleGroupSweepResult = { removed: [], left: [] };
    if ((await this.redis.exists(topic)) === 0) return result;
    const groups = (await this.redis.xinfo('GROUPS', topic)) as unknown[];
    for (const name of groups.map((group) =>
      String(infoFields(group).get('name')),
    )) {
      const consumers = (
        (await this.redis.xinfo('CONSUMERS', topic, name)) as unknown[]
      ).map(infoFields);
      // A group nobody ever read carries no consumer name, so nothing proves whose it is.
      const ours =
        consumers.length > 0 &&
        formats.some(
          (format) =>
            format.name.test(name) &&
            consumers.every(
              (c) => String(c.get('name')) === format.consumer(name),
            ),
        );
      if (!ours) {
        result.left.push(name);
        continue;
      }
      if (consumers.every((c) => Number(c.get('idle')) >= idleMs)) {
        await this.redis.xgroup('DESTROY', topic, name);
        result.removed.push(name);
      }
    }
    return result;
  }

  async ensureTopicsExist(
    topics: TopicDefinition[] = REQUIRED_TOPICS,
  ): Promise<void> {
    try {
      const failures: Array<{ topic: string; error: string }> = [];
      for (const topicDef of expandLaneStreams(topics)) {
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
    }
  }

  /**
   * `provider.scanKeys()`, not a raw single-client SCAN (R2): on Redis
   * Cluster / MemoryDB a single-node SCAN only sees that node's shard, and
   * this must see every stream regardless of which shard it lives on.
   */
  async listTopics(): Promise<string[]> {
    const client = this.provider.getClient();
    const streams: string[] = [];
    for await (const key of this.provider.scanKeys('*')) {
      const type = await client.type(key);
      if (type === 'stream') {
        streams.push(key);
      }
    }
    return streams;
  }
}
