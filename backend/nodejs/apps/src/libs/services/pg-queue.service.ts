import { MessageBrokerError } from '../errors/messaging.errors';
import { Logger } from './logger.service';
import {
  IMessageProducer,
  IMessageConsumer,
  IMessageAdmin,
  StreamMessage,
  TopicDefinition,
} from '../types/messaging.types';
import {
  injectEnvelope,
  runWithRequestContext,
  ENVELOPE_REQUEST_ID,
  newAnonRoot,
  sanitizeRootId,
} from '../context/request-context';
import {
  EnvelopeRejected,
  runWithEnvelope,
  signMessage,
} from '../context/envelope';
import { spawnDetached } from '../context/orgContext';
import { ensurePgRuntime } from '../db/runtime-boot';
import { PgListener, listenUrl } from '../db/listen';
import type { Tx } from '../db/uow';

/**
 * The work queue behind `MESSAGE_BROKER=postgres`, on the same three tables the Python services use
 * (`platform.queue_messages`, `queue_groups`, `queue_deliveries`): Redis Streams semantics, in SQL.
 *
 * A message is stored once and each consumer group on its topic gets a delivery row, so groups ack, retry and
 * dead-letter independently. A claim counts an attempt and hides the row for `claimMinIdleMs` (a heartbeat keeps the
 * rows still being worked on hidden); an expired claim is handed out again. A handler that throws sends the row back
 * after a backoff; past `MAX_DELIVERY_ATTEMPTS` it is marked dead. NOTIFY wakes consumers; they also poll.
 */
export const QUEUE_CHANNEL = 'pipeshub_queue';

const SQL = {
  groups: `/* pipeshub:queue.groups */ SELECT group_id FROM platform.queue_groups WHERE topic = $1 FOR SHARE`,
  insert: `/* pipeshub:queue.publish */
    WITH m AS (
      INSERT INTO platform.queue_messages (topic, msg_key, value, headers)
      SELECT $1, u.k, u.v, u.h FROM unnest($2::text[], $3::text[], $4::text[]) WITH ORDINALITY AS u(k, v, h, ord) ORDER BY u.ord
      RETURNING id, msg_key)
    INSERT INTO platform.queue_deliveries (group_id, topic, message_id, msg_key)
    SELECT g, $1, m.id, m.msg_key FROM m CROSS JOIN unnest($5::text[]) AS g`,
  notify: `/* pipeshub:queue.notify */ SELECT pg_notify('${QUEUE_CHANNEL}', $1)`,
  lockGroups: `/* pipeshub:queue.lockgroups */ LOCK TABLE platform.queue_groups IN EXCLUSIVE MODE`,
  addGroup: `/* pipeshub:queue.addgroup */
    INSERT INTO platform.queue_groups (topic, group_id, ephemeral) VALUES ($1, $2, $3)
    ON CONFLICT (topic, group_id) DO UPDATE SET seen_at = now() RETURNING (xmax = 0) AS created`,
  backfill: `/* pipeshub:queue.backfill */
    INSERT INTO platform.queue_deliveries (group_id, topic, message_id, msg_key)
    SELECT $2, topic, id, msg_key FROM platform.queue_messages WHERE topic = $1 ORDER BY id`,
  claim: `/* pipeshub:queue.claim */
    WITH picked AS (
      SELECT group_id, topic, message_id FROM platform.queue_deliveries
      WHERE group_id = $1 AND topic = ANY($2::text[])
        AND ((status = 'ready' AND visible_at <= now()) OR (status = 'claimed' AND locked_until <= now()))
      ORDER BY message_id LIMIT $3 FOR UPDATE SKIP LOCKED),
    upd AS (
      UPDATE platform.queue_deliveries d
      SET status = 'claimed', attempts = d.attempts + 1, locked_until = now() + make_interval(secs => $4::double precision), consumer = $5
      FROM picked p WHERE d.group_id = p.group_id AND d.topic = p.topic AND d.message_id = p.message_id
      RETURNING d.topic, d.message_id, d.attempts)
    SELECT u.topic, u.message_id::text AS message_id, u.attempts, m.value, m.msg_key, m.headers
    FROM upd u JOIN platform.queue_messages m ON m.id = u.message_id ORDER BY u.message_id`,
  ack: `/* pipeshub:queue.ack */ DELETE FROM platform.queue_deliveries
    WHERE group_id = $1 AND topic = $2 AND message_id = $3 AND attempts = $4 AND status = 'claimed'`,
  nack: `/* pipeshub:queue.nack */ UPDATE platform.queue_deliveries
    SET status = 'ready', locked_until = NULL, consumer = NULL, last_error = $5, visible_at = now() + make_interval(secs => $6::double precision)
    WHERE group_id = $1 AND topic = $2 AND message_id = $3 AND attempts = $4 AND status = 'claimed'`,
  dead: `/* pipeshub:queue.dead */ UPDATE platform.queue_deliveries SET status = 'dead', locked_until = NULL, visible_at = now(), last_error = $5
    WHERE group_id = $1 AND topic = $2 AND message_id = $3 AND attempts = $4 AND status = 'claimed'`,
  extend: `/* pipeshub:queue.extend */
    UPDATE platform.queue_deliveries d SET locked_until = now() + make_interval(secs => $5::double precision)
    FROM unnest($2::text[], $3::bigint[], $4::int[]) AS x(topic, message_id, attempts)
    WHERE d.group_id = $1 AND d.topic = x.topic AND d.message_id = x.message_id AND d.attempts = x.attempts AND d.status = 'claimed'`,
  touch: `/* pipeshub:queue.touch */ UPDATE platform.queue_groups SET seen_at = now()
    WHERE group_id = $1 AND topic = ANY($2::text[]) AND ephemeral`,
  dropDeliveries: `/* pipeshub:queue.dropdeliveries */ DELETE FROM platform.queue_deliveries WHERE group_id = $1 AND topic = $2`,
  dropGroup: `/* pipeshub:queue.dropgroup */ DELETE FROM platform.queue_groups WHERE group_id = $1 AND topic = $2`,
  sweepGroups: `/* pipeshub:queue.sweepgroups */
    WITH gone AS (DELETE FROM platform.queue_groups WHERE ephemeral AND seen_at < now() - make_interval(secs => $1::double precision)
                  RETURNING group_id, topic)
    DELETE FROM platform.queue_deliveries d USING gone WHERE d.group_id = gone.group_id AND d.topic = gone.topic`,
  sweepDead: `/* pipeshub:queue.sweepdead */ DELETE FROM platform.queue_deliveries WHERE (group_id, topic, message_id) IN (
      SELECT group_id, topic, message_id FROM platform.queue_deliveries
      WHERE status = 'dead' AND visible_at < now() - make_interval(secs => $1::double precision) LIMIT $2)`,
  sweepMessages: `/* pipeshub:queue.sweepmessages */ DELETE FROM platform.queue_messages WHERE id IN (
      SELECT m.id FROM platform.queue_messages m
      WHERE NOT EXISTS (SELECT 1 FROM platform.queue_deliveries d WHERE d.message_id = m.id)
        AND (EXISTS (SELECT 1 FROM platform.queue_groups g WHERE g.topic = m.topic) OR m.created_at < now() - make_interval(secs => $1::double precision))
      LIMIT $2)`,
  topics: `/* pipeshub:queue.topics */ SELECT DISTINCT topic FROM platform.queue_groups UNION SELECT DISTINCT topic FROM platform.queue_messages ORDER BY 1`,
  nextDue: `/* pipeshub:queue.nextdue */ SELECT extract(epoch FROM min(CASE WHEN status = 'ready' THEN visible_at ELSE locked_until END) - now())::float8 AS secs
    FROM platform.queue_deliveries WHERE group_id = $1 AND topic = ANY($2::text[]) AND status IN ('ready', 'claimed')`,
  health: `/* pipeshub:queue.health */ SELECT 1 FROM platform.queue_messages WHERE false`,
};

interface ClaimedRow {
  topic: string;
  message_id: string;
  attempts: number;
  value: string;
  msg_key: string | null;
  headers: string | null;
}

function unit<R>(write: boolean, fn: (tx: Tx) => Promise<R>): Promise<R> {
  return ensurePgRuntime().withUow({ preOrg: true, write }, fn);
}

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

function contextFromValue(value: unknown): { rootId: string } {
  const env = (value ?? {}) as Record<string, unknown>;
  return {
    rootId:
      sanitizeRootId(env[ENVELOPE_REQUEST_ID] as string | undefined) ??
      newAnonRoot(),
  };
}

export abstract class BasePgQueueProducerConnection implements IMessageProducer {
  protected connected = false;

  constructor(protected readonly logger: Logger) {}

  async connect(): Promise<void> {
    // like the Kafka and Redis connections, fail at start-up when the database cannot be reached or is misconfigured
    if (!(await this.healthCheck())) {
      throw new MessageBrokerError(
        'Failed to connect to the PostgreSQL queue',
        {
          details:
            'the queue health probe failed (check PG_NODE_URL and that the database is migrated)',
        },
      );
    }
    this.connected = true;
  }

  disconnect(): Promise<void> {
    this.connected = false;
    return Promise.resolve();
  }

  isConnected(): boolean {
    return this.connected;
  }

  async publish<T>(topic: string, message: StreamMessage<T>): Promise<void> {
    await this.publishBatch(topic, [message]);
  }

  async publishBatch<T>(
    topic: string,
    messages: StreamMessage<T>[],
  ): Promise<void> {
    if (messages.length === 0) return;
    try {
      // signed and stamped here, in the caller's request context
      const keys = messages.map((m) => m.key);
      const values = messages.map((m) =>
        serializeValueWithTrace(m.value, topic),
      );
      const headers = messages.map((m) =>
        m.headers ? JSON.stringify(m.headers) : null,
      );
      await unit(true, async (tx) => {
        // FOR SHARE on the group rows conflicts with a group being created (its table lock): the new group is either seen
        // here or backfilled from this message, never neither
        const groups = (
          await tx.query<{ group_id: string }>(SQL.groups, [topic])
        ).rows.map((r) => r.group_id);
        await tx.query(SQL.insert, [topic, keys, values, headers, groups]);
        await tx.query(SQL.notify, [topic]);
      });
    } catch (error) {
      throw new MessageBrokerError(
        'Failed to publish to the PostgreSQL queue',
        {
          topic,
          details: (error as Error).message,
        },
      );
    }
  }

  async healthCheck(): Promise<boolean> {
    try {
      await unit(false, (tx) => tx.query(SQL.health));
      return true;
    } catch {
      return false;
    }
  }
}

export interface PgQueueConsumerConfig {
  clientId?: string;
  groupId?: string;
  /** A claim idle this long goes to another consumer; the heartbeat keeps a claim being worked on from idling. */
  claimMinIdleMs?: number;
  readCount?: number;
  /** A group of this process only: starts at the tail and is dropped when stale. */
  ephemeral?: boolean;
}

const MAX_DELIVERY_ATTEMPTS = (): number =>
  Number(process.env.MAX_DELIVERY_ATTEMPTS ?? 3);
const RETRY_BASE_S = (): number =>
  Number(process.env.PG_QUEUE_RETRY_BASE_S ?? 5);
const SAFETY_NET_MS = 300_000; // longest an idle consumer with a LISTEN connection sleeps
const POLL_MS = 1000; // without LISTEN
const SWEEP_EVERY_MS = 60_000;
const SWEEP_BATCH = 5000;
const SWEEP_MAX_BATCHES = 40; // per sweep: bounds one tick, the next carries on

export abstract class BasePgQueueConsumerConnection implements IMessageConsumer {
  private connected = false;
  private running = false;
  private topics: string[] = [];
  private loop: Promise<void> | undefined;
  private beat: Promise<void> | undefined;
  private beatTimer: NodeJS.Timeout | undefined;
  private beatWake: (() => void) | undefined;
  private wake: (() => void) | undefined;
  private woken = false;
  private listener: PgListener | undefined;
  private held = new Map<
    string,
    { topic: string; id: string; attempts: number }
  >();
  private lastSweep = 0;
  private readonly groupId: string;
  private readonly clientId: string;
  private readonly visibilityS: number;

  constructor(
    protected readonly config: PgQueueConsumerConfig,
    protected readonly logger: Logger,
  ) {
    this.groupId = config.groupId ?? 'default_group';
    this.clientId = config.clientId ?? 'pipeshub';
    this.visibilityS = Math.max((config.claimMinIdleMs ?? 30_000) / 1000, 5);
  }

  async connect(): Promise<void> {
    // like the Kafka and Redis connections, fail at start-up when the database cannot be reached or is misconfigured
    if (!(await this.healthCheck())) {
      throw new MessageBrokerError(
        'Failed to connect to the PostgreSQL queue',
        {
          details:
            'the queue health probe failed (check PG_NODE_URL and that the database is migrated)',
        },
      );
    }
    this.connected = true;
  }

  async disconnect(): Promise<void> {
    this.running = false;
    this.connected = false;
    this.beatWake?.();
    this.poke();
    await this.loop?.catch(() => undefined);
    await this.beat?.catch(() => undefined);
    await this.listener?.stop();
    this.listener = undefined;
    if (this.config.ephemeral) {
      for (const topic of this.topics) {
        await unit(true, async (tx) => {
          await tx.query(SQL.dropDeliveries, [this.groupId, topic]);
          await tx.query(SQL.dropGroup, [this.groupId, topic]);
        }).catch(() => undefined);
      }
    }
  }

  isConnected(): boolean {
    return this.connected;
  }

  private isRunning(): boolean {
    return this.running;
  }

  async subscribe(topics: string[], fromBeginning = false): Promise<void> {
    for (const topic of topics) {
      const created = await unit(true, async (tx) => {
        await tx.query(SQL.lockGroups);
        const r = await tx.query<{ created: boolean }>(SQL.addGroup, [
          topic,
          this.groupId,
          Boolean(this.config.ephemeral),
        ]);
        const isNew = r.rows[0]?.created === true;
        if (isNew && fromBeginning && !this.config.ephemeral)
          await tx.query(SQL.backfill, [topic, this.groupId]);
        return isNew;
      });
      if (created)
        this.logger.info(
          `Created consumer group ${this.groupId} for topic ${topic}`,
        );
    }
    this.topics = [...new Set([...this.topics, ...topics])];
  }

  async consume<T>(
    handler: (message: StreamMessage<T>) => Promise<void>,
  ): Promise<void> {
    this.running = true;
    const url = listenUrl();
    if (url) {
      this.listener = new PgListener(
        url,
        QUEUE_CHANNEL,
        (t) => this.topics.includes(t) && this.poke(),
        () => {
          this.poke();
        },
        () => {
          this.poke(); // wake to start polling: a notification can no longer be trusted to arrive
        },
      );
      await this.listener.start();
    }
    // the loop outlives this call: it must not keep the caller's org context
    this.loop = spawnDetached(() => this.consumeLoop(handler));
    this.beat = spawnDetached(() => this.heartbeatLoop());
  }

  /** Every third of the visibility window: keeps the claims being worked on hidden, an ephemeral group alive, and sweeps. */
  private async heartbeatLoop(): Promise<void> {
    while (this.isRunning()) {
      await new Promise<void>((resolve) => {
        this.beatTimer = setTimeout(resolve, (this.visibilityS * 1000) / 3);
        this.beatWake = () => {
          clearTimeout(this.beatTimer);
          resolve();
        };
      });
      if (this.isRunning()) await this.heartbeat();
    }
  }

  private async heartbeat(): Promise<void> {
    const held = [...this.held.values()];
    const sweepDue = Date.now() - this.lastSweep > SWEEP_EVERY_MS;
    // an idle consumer touches the database only to sweep
    if (held.length === 0 && !this.config.ephemeral && !sweepDue) return;
    try {
      await unit(true, async (tx) => {
        if (held.length > 0)
          await tx.query(SQL.extend, [
            this.groupId,
            held.map((h) => h.topic),
            held.map((h) => h.id),
            held.map((h) => h.attempts),
            this.visibilityS,
          ]);
        if (this.config.ephemeral)
          await tx.query(SQL.touch, [this.groupId, this.topics]);
        if (sweepDue) {
          this.lastSweep = Date.now();
          await tx.query(SQL.sweepGroups, [600]);
        }
      });
      if (sweepDue) await this.sweepFinished();
    } catch (e) {
      this.logger.warn('Queue heartbeat failed', {
        claims: held.length,
        error: (e as Error).message,
      });
    }
  }

  /** Dead letters (counted from when they died) and finished messages, in batches of one short transaction while batches come back full. */
  private async sweepFinished(): Promise<void> {
    for (const [sql, retention] of [
      [SQL.sweepDead, 7 * 86400],
      [SQL.sweepMessages, 86400],
    ] as const) {
      for (let i = 0; i < SWEEP_MAX_BATCHES; i++) {
        const r = await unit(true, (tx) =>
          tx.query(sql, [retention, SWEEP_BATCH]),
        );
        if ((r.rowCount ?? 0) < SWEEP_BATCH) break;
      }
    }
  }

  private async idle(): Promise<void> {
    if (this.woken) {
      this.woken = false;
      return;
    }
    let waitMs = POLL_MS;
    if (this.listener?.healthy()) {
      // no polling: wake on NOTIFY, or when a retry backoff ends / a claim expires (neither notifies)
      waitMs = SAFETY_NET_MS;
      try {
        const r = await unit(false, (tx) =>
          tx.query<{ secs: number | null }>(SQL.nextDue, [
            this.groupId,
            this.topics,
          ]),
        );
        const secs = r.rows[0]?.secs;
        if (secs !== null && secs !== undefined)
          waitMs = Math.min(Math.max(secs * 1000, 50) + 50, SAFETY_NET_MS);
      } catch {
        waitMs = POLL_MS;
      }
    }
    await new Promise<void>((resolve) => {
      const t = setTimeout(resolve, waitMs);
      if (this.woken) {
        // a notification arrived while the next due time was being read
        clearTimeout(t);
        resolve();
        return;
      }
      this.wake = () => {
        clearTimeout(t);
        resolve();
      };
    });
    this.wake = undefined;
    this.woken = false;
  }

  /** A notification arrived: end the wait, or remember it for the next one (it can land while a batch is being worked). */
  private poke(): void {
    if (this.wake) this.wake();
    else this.woken = true;
  }

  private async consumeLoop<T>(
    handler: (message: StreamMessage<T>) => Promise<void>,
  ): Promise<void> {
    while (this.isRunning()) {
      try {
        if (this.topics.length === 0) {
          await this.idle();
          continue;
        }
        const rows = (
          await unit(true, (tx) =>
            tx.query<ClaimedRow>(SQL.claim, [
              this.groupId,
              this.topics,
              this.config.readCount ?? 10,
              this.visibilityS,
              this.clientId,
            ]),
          )
        ).rows;
        if (rows.length === 0) {
          await this.idle();
          continue;
        }
        for (const r of rows)
          this.held.set(`${r.topic}:${r.message_id}`, {
            topic: r.topic,
            id: r.message_id,
            attempts: r.attempts,
          });
        for (const r of rows) {
          if (!this.isRunning()) break;
          await this.settle(r, handler);
        }
      } catch (error) {
        if (!this.isRunning()) break;
        this.logger.error('Error in PostgreSQL queue consume loop', {
          error: (error as Error).message,
        });
        await new Promise((r) => setTimeout(r, 1000));
      }
    }
  }

  private async settle<T>(
    r: ClaimedRow,
    handler: (message: StreamMessage<T>) => Promise<void>,
  ): Promise<void> {
    const key = [this.groupId, r.topic, r.message_id, r.attempts];
    const max = MAX_DELIVERY_ATTEMPTS();
    const finish = (sql: string, extra: unknown[] = []): Promise<unknown> =>
      unit(true, (tx) => tx.query(sql, [...key, ...extra]));
    try {
      if (r.attempts > max) {
        this.logger.warn('Dead-lettered a message delivered too often', {
          topic: r.topic,
          id: r.message_id,
        });
        await finish(SQL.dead, ['delivery attempts exhausted']);
        return;
      }
      const message: StreamMessage<T> = {
        key: r.msg_key ?? '',
        value: JSON.parse(r.value) as T,
      };
      if (r.headers)
        message.headers = JSON.parse(r.headers) as Record<string, string>;
      try {
        await runWithRequestContext(contextFromValue(message.value), () =>
          runWithEnvelope(r.topic, message.value, (value) =>
            handler({ ...message, value }),
          ),
        );
      } catch (error) {
        if (!(error instanceof EnvelopeRejected)) throw error;
        this.logger.error('Envelope rejected, message dropped', {
          topic: r.topic,
          id: r.message_id,
          reason: error.reason,
        });
        await finish(SQL.dead, [`envelope rejected: ${error.reason}`]);
        return;
      }
      await finish(SQL.ack);
    } catch (error) {
      const text = error instanceof Error ? error.message : String(error);
      this.logger.error('Error processing queue message', {
        topic: r.topic,
        id: r.message_id,
        attempt: r.attempts,
        error: text,
      });
      try {
        if (r.attempts >= max) await finish(SQL.dead, [text.slice(0, 500)]);
        else
          await finish(SQL.nack, [
            text.slice(0, 500),
            Math.min(RETRY_BASE_S() * r.attempts, 30),
          ]);
      } catch (e) {
        this.logger.error('Could not settle a failed queue message', {
          error: (e as Error).message,
        });
      }
    } finally {
      this.held.delete(`${r.topic}:${r.message_id}`);
    }
  }

  /** Pause and resume are no-ops, as on Redis Streams. */
  pause(_topics: string[]): void {}
  resume(_topics: string[]): void {}

  async healthCheck(): Promise<boolean> {
    try {
      await unit(false, (tx) => tx.query(SQL.health));
      return true;
    } catch {
      return false;
    }
  }
}

export class PgQueueAdminService implements IMessageAdmin {
  /** Topics exist as soon as something is published or a group subscribes; there is nothing to create. */
  async ensureTopicsExist(_topics?: TopicDefinition[]): Promise<void> {}

  async listTopics(): Promise<string[]> {
    const r = await unit(false, (tx) =>
      tx.query<{ topic: string }>(SQL.topics),
    );
    return r.rows.map((x) => x.topic);
  }
}
