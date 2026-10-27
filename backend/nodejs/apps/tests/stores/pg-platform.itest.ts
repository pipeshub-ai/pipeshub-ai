/**
 * The notification and outbox repositories on PostgreSQL (Alembic head, row-level security, through PgBouncer): the same
 * contract suites the Mongo stores meet.
 *
 *   PG_TEST_BINS=… PG_TEST_PGBOUNCER=… PG_TEST_MIGRATE_PYTHON=… npm run test:pg-stores
 */
import 'reflect-metadata';
import { PgNotificationRepository } from '../../src/libs/repositories/notification/pg';
import { PgOutboxRepository } from '../../src/libs/repositories/outbox/pg';
import { PgMailLogRepository } from '../../src/libs/repositories/mail-log/pg';
import type { OutboxEventRow } from '../../src/libs/repositories/outbox';
import type { Tx } from '../../src/libs/repositories/uow';
import { describeNotificationContract } from './contract/notification.contract';
import { describeOutboxContract } from './contract/outbox.contract';
import {
  OrgLedger,
  PG_BINS,
  PgStack,
  haveStack,
  inOrg,
  newId,
  scoped,
  scopedUow,
  startPgStack,
} from './pg-store-harness';

const suite = haveStack ? describe : describe.skip;
const OUTBOX_ORG = 'cccccccccccccccccccccccc';
const COLUMN: Record<string, string> = {
  nextAttemptAt: 'next_attempt_at',
  claimedAt: 'claimed_at',
  status: 'status',
};

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(
    `PostgreSQL notification and outbox stores (${bin}) behind PgBouncer`,
    function () {
      this.timeout(120_000);
      let stack: PgStack;
      const ledger = new OrgLedger();

      before(async () => {
        stack = await startPgStack(bin, 'platform_stores', { jobs: true });
      });
      after(async () => {
        await stack?.stop();
      });

      describe('NotificationRepository contract', () => {
        describeNotificationContract({
          repo: scoped(new PgNotificationRepository(), ledger),
          newId,
          setCreatedAt: (id, at) =>
            stack.admin(
              async (c) => { await c.query(
                  'UPDATE platform.notifications SET created_at = $2 WHERE id = $1',
                  [id, at],
                ) },
            ),
        });
      });

      describe('OutboxRepository contract', () => {
        const repo = new PgOutboxRepository();
        const uow = scopedUow();
        describeOutboxContract({
          // an event is appended by a request, which has an organisation; the dispatcher side has none
          repo: {
            append: (e, tx) => inOrg(OUTBOX_ORG, () => repo.append(e, tx)),
            claimNext: (o) => repo.claimNext(o),
            markPublished: (id) => repo.markPublished(id),
            markFailed: (id, f) => repo.markFailed(id, f),
            statusCounts: () => repo.statusCounts(),
          },
          uow: {
            run: <R>(
              fn: (tx: Tx) => Promise<R>,
              opts?: { readOnly?: boolean },
            ) => uow.run(fn, { ...opts, org: OUTBOX_ORG }),
          },
          transactional: true,
          idPattern: /^\d+$/,
          mailLog: scoped(new PgMailLogRepository(), ledger),
          mailLogRows: () =>
            stack.admin(async (c) =>
              (
                await c.query(
                  'SELECT * FROM platform.mail_log ORDER BY created_at',
                )
              ).rows.map((r) => ({
                orgId: r.org_id,
                subject: r.subject,
                from: r.from_address,
                to: r.to_addresses,
                cc: r.cc_addresses,
                emailTemplateType: r.email_template_type,
                createdAt: r.created_at,
              })),
            ),
          allRows: () =>
            stack.admin(async (c) =>
              (
                await c.query(
                  'SELECT * FROM platform.outbox ORDER BY created_at, id',
                )
              ).rows.map((r) => {
                const row: Record<string, unknown> = {
                  _id: String(r.id),
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
                if (r.headers !== null) row.headers = r.headers;
                if (r.claimed_at !== null) row.claimedAt = r.claimed_at;
                if (r.last_error !== null) row.lastError = r.last_error;
                if (r.published_at !== null) row.publishedAt = r.published_at;
                return row as unknown as OutboxEventRow;
              }),
            ),
          clear: () =>
            stack.admin(
              async (c) => { await c.query('DELETE FROM platform.outbox') },
            ),
          setCreatedAt: (id, at) =>
            stack.admin(
              async (c) => { await c.query(
                  'UPDATE platform.outbox SET created_at = $2 WHERE id = $1',
                  [id, at],
                ) },
            ),
          setRow: (id, set) =>
            stack.admin(async (c) => {
              for (const [k, v] of Object.entries(set))
                await c.query(
                  `UPDATE platform.outbox SET ${COLUMN[k]} = $2 WHERE id = $1`,
                  [id, v],
                );
            }),
          newId,
        });
      });
    },
  );
}
