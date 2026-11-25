import type { Document } from 'mongodb';
import { mongoHello } from '../../mongo';

/**
 * Where lease and deadline predicates get "now": the database, so a Node
 * replica with a skewed or jumped clock can neither steal a live lease nor
 * keep a lapsed one [R-8].
 */
export interface MongoTime {
  /** Aggregation expression that evaluates to epoch milliseconds. */
  expr(): unknown;
  /** Epoch milliseconds, for decisions that cannot live inside one statement. */
  read(): Promise<number>;
}

/** How long one reading of the server's clock is carried forward (the document store's offset keeps 5 s too). */
export const SERVER_TIME_CARRY_MS = 5_000;

export interface CarriedServerTimeOptions {
  carryMs?: number;
  /** This process's monotonic clock, in ms: it never jumps, so carrying a reading on it keeps R-8. */
  monotonicMs?: () => number;
  hello?: () => Promise<Document>;
}

/**
 * The server's clock, read with `hello` and carried forward on this process's
 * monotonic clock for `carryMs` (WP88-FU-4, FU-5). A read was one store round
 * trip on every admission, claim and budget transaction, the same in-step
 * burst of them all asking at once; now one reading answers them, and readers
 * that arrive while it is asked wait for that one. The monotonic clock cannot
 * be set or stepped, so a skewed or jumped local wall clock still moves
 * nothing [R-8]; what a reading can be off by is its own round trip and the
 * two clocks' drift over `carryMs`, well under a millisecond.
 */
export function carriedServerTime(
  options: CarriedServerTimeOptions = {},
): MongoTime {
  const carryMs = options.carryMs ?? SERVER_TIME_CARRY_MS;
  const monotonicMs = options.monotonicMs ?? (() => performance.now());
  const hello = options.hello ?? (() => mongoHello());
  let reading: { serverMs: number; at: number } | null = null;
  let asking: Promise<number> | null = null;

  const ask = async (): Promise<number> => {
    const at = monotonicMs();
    const localTime = (await hello())['localTime'] as unknown;
    if (!(localTime instanceof Date)) {
      throw new Error('MongoDB hello did not report localTime');
    }
    reading = { serverMs: localTime.getTime(), at };
    return reading.serverMs;
  };

  return {
    expr: () => ({ $toDouble: '$$NOW' }),
    read: () => {
      const now = monotonicMs();
      if (reading !== null && now - reading.at < carryMs) {
        // Whole milliseconds, as the server's clock reads: stored times and the wire are integers.
        return Promise.resolve(reading.serverMs + Math.floor(now - reading.at));
      }
      asking ??= ask().finally(() => {
        asking = null;
      });
      return asking;
    },
  };
}

export const mongoServerTime: MongoTime = carriedServerTime();

/** Test double: the database clock reads `now()`. */
export const fixedMongoTime = (now: () => number): MongoTime => ({
  expr: () => ({ $literal: now() }),
  read: () => Promise.resolve(now()),
});
