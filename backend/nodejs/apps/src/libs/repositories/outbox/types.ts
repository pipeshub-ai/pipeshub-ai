/**
 * Outbox repository: domain events that were decided with a change and are
 * delivered to the broker afterwards, retried until they go out.
 *
 * Rows leave as plain objects with ids as strings. How a row is found and
 * claimed (due, abandoned, not held back by an older event about the same
 * entity) is decided here, atomically, so several instances can run the
 * dispatcher at once.
 */
import type { Tx } from '../uow';

export type OutboxStatus = 'pending' | 'publishing' | 'published' | 'failed';

export interface OutboxEventRow {
  _id: string;
  topic: string;
  key: string;
  orderingKey: string;
  value: string;
  headers?: Record<string, string>;
  status: OutboxStatus;
  attempts: number;
  nextAttemptAt: Date;
  claimedAt?: Date;
  lastError?: string;
  publishedAt?: Date;
  createdAt: Date;
  updatedAt?: Date;
}

export interface NewOutboxEvent {
  topic: string;
  key: string;
  orderingKey: string;
  value: string;
  headers?: Record<string, string>;
  status: 'pending';
  attempts: number;
  nextAttemptAt: Date;
}

export interface ClaimOptions {
  /** A `publishing` claim older than this is treated as abandoned. */
  leaseMs: number;
  /** How many due rows are read at a time while looking for an unblocked one. */
  candidateBatch: number;
  /** How far to walk before giving up for this pass. */
  maxScan: number;
}

export interface OutboxRepository {
  /** Stores an event, in the caller's transaction when it has one. */
  append(event: NewOutboxEvent, tx?: Tx): Promise<void>;
  /**
   * Takes the oldest due event that is free to go (nothing older about the same
   * entity is still undelivered) and marks it as taken. Null when none is.
   */
  claimNext(opts: ClaimOptions): Promise<OutboxEventRow | null>;
  markPublished(id: string): Promise<void>;
  /** Back to `pending` with a later retry time. */
  markFailed(
    id: string,
    failure: { attempts: number; nextAttemptAt: Date; lastError: string },
  ): Promise<void>;
  statusCounts(): Promise<Record<OutboxStatus, number>>;
}
