import { EnvelopeRejected } from '../context/envelope';
import { emitPlatformEvent } from '../platform-events';

/**
 * What the broker wrappers announce: `message.published` and
 * `message.consumed`. The facts are the topic, a bounded message type, how it
 * ended and how long it took. Never a payload or a key.
 */

export type Broker = 'kafka' | 'redis';

export const UNKNOWN_MESSAGE_TYPE = 'unknown';
export const OTHER_MESSAGE_TYPE = 'other';
export const MAX_MESSAGE_TYPES = 200;

// An event type is a short name set by code (newRecord, connector.sync.started). Anything else is data.
const TYPE_SHAPE = /^[A-Za-z][A-Za-z0-9_.:-]{0,63}$/;
const seenTypes = new Set<string>();

/** Tests: forget the message types seen so far. */
export const forgetMessageTypes = (): void => {
  seenTypes.clear();
};

/**
 * The message's `eventType` (or `type`) as a value from a bounded set:
 * `unknown` without one, `other` for a value that is not a short name or once
 * MAX_MESSAGE_TYPES distinct ones were seen.
 */
export function messageTypeOf(value: unknown): string {
  if (value === null || typeof value !== 'object' || Array.isArray(value))
    return UNKNOWN_MESSAGE_TYPE;
  const fields = value as { eventType?: unknown; type?: unknown };
  const type = fields.eventType ?? fields.type;
  if (typeof type !== 'string' || type === '') return UNKNOWN_MESSAGE_TYPE;
  if (seenTypes.has(type)) return type;
  if (!TYPE_SHAPE.test(type) || seenTypes.size >= MAX_MESSAGE_TYPES)
    return OTHER_MESSAGE_TYPE;
  seenTypes.add(type);
  return type;
}

/** Milliseconds since the broker stamped the message, when it gave a timestamp. */
export function lagMsSince(publishedAtMs: unknown): number | undefined {
  const at =
    typeof publishedAtMs === 'string' && /^\d+$/.test(publishedAtMs)
      ? Number(publishedAtMs)
      : publishedAtMs;
  if (typeof at !== 'number' || !Number.isFinite(at) || at <= 0)
    return undefined;
  return Math.max(0, Math.round(Date.now() - at));
}

/** The millisecond time a Redis stream entry id (`<ms>-<seq>`) starts with. */
export const streamEntryMs = (entryId: string): string | undefined =>
  /^(\d+)-/.exec(entryId)?.[1];

const secondsSince = (startedAt: bigint): number =>
  Number(process.hrtime.bigint() - startedAt) / 1e9;

/** One event per message handed to the broker; `startedAt` is `process.hrtime.bigint()` before the send. */
export function announcePublished(
  broker: Broker,
  topic: string,
  values: readonly unknown[],
  ok: boolean,
  startedAt: bigint,
): void {
  const durationSeconds = secondsSince(startedAt);
  for (const value of values) {
    emitPlatformEvent('message.published', {
      broker,
      topic,
      messageType: messageTypeOf(value),
      outcome: ok ? 'success' : 'failure',
      durationSeconds,
    });
  }
}

/**
 * Runs one delivery's handler and announces how it ended: `success`,
 * `rejected` (the envelope was refused), `retry` when `retried(error)` says the
 * message will be delivered again, otherwise `failure`. The error is rethrown.
 */
export async function consumeAnnounced<R>(
  facts: {
    broker: Broker;
    topic: string;
    value: unknown;
    publishedAtMs?: unknown;
    retried?: (error: unknown) => boolean;
  },
  run: () => Promise<R>,
): Promise<R> {
  const lagMs = lagMsSince(facts.publishedAtMs);
  const startedAt = process.hrtime.bigint();
  const announce = (
    outcome: 'success' | 'failure' | 'rejected' | 'retry',
  ): void => {
    emitPlatformEvent('message.consumed', {
      broker: facts.broker,
      topic: facts.topic,
      messageType: messageTypeOf(facts.value),
      outcome,
      durationSeconds: secondsSince(startedAt),
      ...(lagMs === undefined ? {} : { lagMs }),
    });
  };
  try {
    const result = await run();
    announce('success');
    return result;
  } catch (error) {
    if (error instanceof EnvelopeRejected) announce('rejected');
    else announce(facts.retried?.(error) ? 'retry' : 'failure');
    throw error;
  }
}
