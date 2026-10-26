/**
 * PostgreSQL implementation of the outbox repository (`DOC_STORE=postgres`). A request appends in its own unit (so the
 * event commits or rolls back with the change that caused it); the dispatcher is a job: it claims and settles the rows of
 * every org as the jobs role. Claims are `FOR UPDATE SKIP LOCKED`, so several dispatchers never take the same row, and a
 * row is held back while an older one about the same entity (`ordering_key`) is undelivered.
 */
import { system, orgContextStorage } from '../../context/orgContext';
import { Tx as PgKernelTx, withJobsUow } from '../../db';
import { InternalServerError } from '../../errors/http.errors';
import { stripNul } from '../nul';
import { fill, inUnit } from '../pg-util';
import type { Tx } from '../uow';
import type {
  ClaimOptions,
  NewOutboxEvent,
  OutboxEventRow,
  OutboxRepository,
  OutboxStatus,
} from './types';

interface Row {
  id: string;
  topic: string;
  msg_key: string;
  ordering_key: string;
  value: string;
  headers: Record<string, string> | null;
  status: OutboxStatus;
  attempts: number;
  next_attempt_at: Date;
  claimed_at: Date | null;
  last_error: string | null;
  published_at: Date | null;
  created_at: Date;
  updated_at: Date;
}

const COLS = `o.id, o.topic, o.msg_key, o.ordering_key, o.value, o.headers, o.status, o.attempts, o.next_attempt_at, o.claimed_at,
  o.last_error, o.published_at, o.created_at, o.updated_at`;

function toRow(r: Row): OutboxEventRow {
  const o: OutboxEventRow = {
    _id: r.id,
    topic: r.topic,
    key: r.msg_key,
    orderingKey: r.ordering_key,
    value: r.value,
    status: r.status,
    attempts: r.attempts,
    nextAttemptAt: r.next_attempt_at,
    createdAt: r.created_at,
    updatedAt: r.updated_at,
  };
  if (r.headers !== null) o.headers = r.headers;
  if (r.claimed_at !== null) o.claimedAt = r.claimed_at;
  if (r.last_error !== null) o.lastError = r.last_error;
  if (r.published_at !== null) o.publishedAt = r.published_at;
  return o;
}

/** Cross-org work as the jobs role. The dispatcher's timer has no org context; that is what a system context is for. */
function inJobs<T>(fn: (k: PgKernelTx) => Promise<T>): Promise<T> {
  const run = (): Promise<T> => withJobsUow({ write: true }, fn);
  return orgContextStorage.getStore() ? run() : system('outbox', run);
}

/** Row ids are a database sequence; anything else matches nothing. */
const idOf = (id: string): string | undefined =>
  /^\d{1,18}$/.test(id) ? id : undefined;

export class PgOutboxRepository implements OutboxRepository {
  async append(event: NewOutboxEvent, tx?: Tx): Promise<void> {
    await inUnit(tx, { write: true }, async (k) => {
      if (!k.orgId)
        throw new InternalServerError(
          'an outbox event is appended inside an organisation',
        );
      const e = stripNul(event, 'outbox', 'event');
      await k.query(
        `/* pipeshub:platform.outbox_append */ INSERT INTO platform.outbox
           (org_id, topic, msg_key, ordering_key, value, headers, status, attempts, next_attempt_at, created_at, updated_at)
         VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $10)`,
        [
          k.orgId,
          e.topic,
          e.key,
          e.orderingKey,
          e.value,
          e.headers === undefined ? null : JSON.stringify(e.headers),
          e.status,
          e.attempts,
          e.nextAttemptAt,
          new Date(),
        ],
      );
    });
  }

  // `candidateBatch` and `maxScan` bound a walk past blocked rows on Mongo; here the guard is part of the one statement.
  async claimNext(opts: ClaimOptions): Promise<OutboxEventRow | null> {
    const now = new Date();
    return inJobs(async (k) => {
      const { rows } = await k.query<Row>(
        fill(
          `/* pipeshub:platform.outbox_claim */ WITH c AS (
           SELECT o.org_id, o.id FROM platform.outbox o
            WHERE ((o.status = 'pending' AND o.next_attempt_at <= $1) OR (o.status = 'publishing' AND o.claimed_at <= $2))
              AND NOT EXISTS (SELECT 1 FROM platform.outbox p WHERE p.ordering_key = o.ordering_key AND (p.created_at, p.id) < (o.created_at, o.id)
                                AND p.status IN ('pending', 'publishing', 'failed'))
            ORDER BY o.created_at, o.id LIMIT 1 FOR UPDATE OF o SKIP LOCKED)
         UPDATE platform.outbox o SET status = 'publishing', claimed_at = $1, updated_at = $1 FROM c
          WHERE o.org_id = c.org_id AND o.id = c.id RETURNING @{COLS}`,
          { COLS },
        ),
        [now, new Date(now.getTime() - opts.leaseMs)],
      );
      return rows[0] ? toRow(rows[0]) : null;
    });
  }

  async markPublished(id: string): Promise<void> {
    const n = idOf(id);
    if (!n) return;
    await inJobs((k) =>
      k.query(
        `/* pipeshub:platform.outbox_published */ UPDATE platform.outbox SET status = 'published', published_at = $2, updated_at = $2,
           claimed_at = NULL, last_error = NULL WHERE id = $1`,
        [n, new Date()],
      ),
    );
  }

  async markFailed(
    id: string,
    failure: { attempts: number; nextAttemptAt: Date; lastError: string },
  ): Promise<void> {
    const n = idOf(id);
    if (!n) return;
    await inJobs((k) =>
      k.query(
        `/* pipeshub:platform.outbox_failed */ UPDATE platform.outbox SET status = 'pending', attempts = $2, next_attempt_at = $3,
           last_error = $4, claimed_at = NULL, updated_at = $5 WHERE id = $1`,
        [
          n,
          failure.attempts,
          failure.nextAttemptAt,
          stripNul(failure.lastError, 'outbox', 'last_error'),
          new Date(),
        ],
      ),
    );
  }

  async statusCounts(): Promise<Record<OutboxStatus, number>> {
    const counts: Record<OutboxStatus, number> = {
      pending: 0,
      publishing: 0,
      published: 0,
      failed: 0,
    };
    const { rows } = await inJobs((k) =>
      k.query<{ status: OutboxStatus; n: string }>(
        `/* pipeshub:platform.outbox_counts */ SELECT status, count(*) AS n FROM platform.outbox GROUP BY status`,
      ),
    );
    for (const r of rows) counts[r.status] = Number(r.n);
    return counts;
  }
}
