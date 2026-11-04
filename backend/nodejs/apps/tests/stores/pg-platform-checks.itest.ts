/**
 * What only a real PostgreSQL shows about notifications, the outbox and the TTL sweeper: row policies and grants, concurrent
 * claimers, the month partitions, and the sweeper's reach across organisations as the jobs role.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { PgNotificationRepository } from '../../src/libs/repositories/notification/pg';
import { PgOutboxRepository } from '../../src/libs/repositories/outbox/pg';
import type { NewOutboxEvent } from '../../src/libs/repositories/outbox';
import { PgUnitOfWork, kernelTx } from '../../src/libs/repositories/pg';
import { TtlSweeper } from '../../src/libs/services/ttl-sweeper.service';
import { NOTIFICATION_RETENTION_DAYS } from '../../src/modules/notification/utils/notification-api.utils';
import {
  PG_BINS,
  PgStack,
  haveStack,
  inOrg,
  newId,
  startPgStack,
} from './pg-store-harness';

const suite = haveStack ? describe : describe.skip;
const DAY = 24 * 3600_000;
const ago = (ms: number) => new Date(Date.now() - ms);

for (const bin of PG_BINS.length ? PG_BINS : ['none']) {
  suite(`PostgreSQL platform checks (${bin})`, function () {
    this.timeout(120_000);
    let stack: PgStack;
    const notifications = new PgNotificationRepository();
    const outbox = new PgOutboxRepository();
    const [A, B] = [newId(), newId()];
    const run = <T>(
      org: string,
      fn: (
        q: (
          sql: string,
          v?: unknown[],
        ) => Promise<{ rows: any[]; rowCount: number | null }>,
      ) => Promise<T>,
      write = false,
    ) =>
      inOrg(org, () =>
        new PgUnitOfWork().run(
          (tx) =>
            fn((sql, v) =>
              kernelTx(tx).query(`/* pipeshub:test.check */ ${sql}`, v),
            ),
          { readOnly: !write },
        ),
      );
    const sql = (text: string, v?: unknown[]) =>
      stack.admin(async (c) => (await c.query(text, v)).rows);

    before(async () => {
      stack = await startPgStack(bin, 'platform_checks', { jobs: true });
    });
    after(async () => {
      await stack?.stop();
    });

    describe('row policies and grants', () => {
      it("an organisation sees none of another one's notifications, and cannot write into it", async () => {
        const user = newId();
        await inOrg(A, () =>
          notifications.insert({ orgId: A, assignedTo: user, type: 't' }),
        );
        expect(
          await inOrg(B, () =>
            notifications.list(
              { orgId: B, userId: user },
              { status: null, limit: 10 },
            ),
          ),
        ).to.deep.equal([]);
        const rows = (
          await run(B, (q) =>
            q('SELECT count(*)::int AS n FROM platform.notifications'),
          )
        ).rows;
        expect(rows[0].n).to.equal(0);
        let err: unknown;
        try {
          await run(
            B,
            (q) =>
              q(
                `INSERT INTO platform.notifications (org_id, id, created_at, type, assigned_to, updated_at) VALUES ($1, $2, now(), 'x', $2, now())`,
                [A, newId()],
              ),
            true,
          );
        } catch (e) {
          err = e;
        }
        expect(err).to.be.instanceOf(Error);
        // the org argument must be the unit's own: a mismatch fails before any statement
        let mismatch: unknown;
        try {
          await inOrg(B, () =>
            notifications.insert({ orgId: A, assignedTo: user, type: 't' }),
          );
        } catch (e) {
          mismatch = e;
        }
        expect(mismatch).to.be.instanceOf(Error);
      });

      it('the node role can append to the outbox and nothing else: no read, update or delete', async () => {
        await inOrg(A, () => outbox.append(ev()));
        for (const text of [
          'SELECT * FROM platform.outbox',
          'UPDATE platform.outbox SET attempts = 9',
          'DELETE FROM platform.outbox',
        ]) {
          let err: unknown;
          try {
            await run(A, (q) => q(text), true);
          } catch (e) {
            err = e;
          }
          expect(err, text).to.be.instanceOf(Error);
        }
      });

      it('an event cannot be appended without an organisation', async () => {
        let err: unknown;
        try {
          await outbox.append(ev());
        } catch (e) {
          err = e;
        }
        expect(err).to.be.instanceOf(Error);
      });
    });

    const ev = (over: Partial<NewOutboxEvent> = {}): NewOutboxEvent => ({
      topic: 'entity-events',
      key: 'k',
      orderingKey: 'global',
      value: '{}',
      status: 'pending',
      attempts: 0,
      nextAttemptAt: new Date(Date.now() - 1000),
      ...over,
    });
    const OPTS = { leaseMs: 60_000, candidateBatch: 20, maxScan: 500 };

    describe('claiming', () => {
      beforeEach(() => sql('DELETE FROM platform.outbox'));

      it('eight dispatchers at once take distinct rows, and only the oldest event of an entity at a time', async () => {
        const keys = ['u1', 'u2', 'u3'];
        for (let i = 0; i < 3; i++)
          for (const k of keys)
            await inOrg(i % 2 ? A : B, () =>
              outbox.append(ev({ key: `${k}-${i}`, orderingKey: k })),
            );
        const rows = await sql('SELECT id FROM platform.outbox ORDER BY id');
        for (const [i, r] of rows.entries())
          await sql(
            'UPDATE platform.outbox SET created_at = $2 WHERE id = $1',
            [r.id, new Date(Date.now() - 100_000 + i * 1000)],
          );
        for (let round = 0; round < 3; round++) {
          const got = (
            await Promise.all(
              Array.from({ length: 8 }, () => outbox.claimNext(OPTS)),
            )
          ).filter((x) => x !== null);
          for (
            let next = await outbox.claimNext(OPTS);
            next;
            next = await outbox.claimNext(OPTS)
          )
            got.push(next);
          expect(got.map((g) => g!.key).sort()).to.deep.equal(
            keys.map((k) => `${k}-${round}`),
          );
          for (const g of got) await outbox.markPublished(g!._id);
        }
        expect(await outbox.statusCounts()).to.deep.equal({
          pending: 0,
          publishing: 0,
          published: 9,
          failed: 0,
        });
      });

      it('two events of one entity written in the same millisecond go out one at a time, in the order they were written', async () => {
        for (const key of ['first', 'second', 'third'])
          await inOrg(A, () => outbox.append(ev({ key, orderingKey: 'same' })));
        await sql('UPDATE platform.outbox SET created_at = $1', [
          new Date(Date.now() - 100_000),
        ]);
        for (const expected of ['first', 'second', 'third']) {
          const got = (
            await Promise.all(
              Array.from({ length: 8 }, () => outbox.claimNext(OPTS)),
            )
          ).filter((x) => x !== null);
          expect(got.map((g) => g!.key)).to.deep.equal([expected]);
          await outbox.markPublished(got[0]!._id);
        }
      });

      it('a failed row holds back the newer events of its entity and is never claimed itself', async () => {
        await inOrg(A, () =>
          outbox.append(ev({ key: 'old', orderingKey: 'e' })),
        );
        await inOrg(A, () =>
          outbox.append(ev({ key: 'new', orderingKey: 'e' })),
        );
        const [old, fresh] = await sql(
          'SELECT id FROM platform.outbox ORDER BY id',
        );
        await sql(
          `UPDATE platform.outbox SET created_at = now() - interval '1 hour', status = 'failed' WHERE id = $1`,
          [old.id],
        );
        await sql(
          `UPDATE platform.outbox SET created_at = now() - interval '1 minute' WHERE id = $1`,
          [fresh.id],
        );
        expect(await outbox.claimNext(OPTS)).to.equal(null);
      });

      it('rows of different organisations are claimed by the one dispatcher, each keeping its organisation', async () => {
        await inOrg(A, () => outbox.append(ev({ orderingKey: 'a' })));
        await inOrg(B, () => outbox.append(ev({ orderingKey: 'b' })));
        const first = await outbox.claimNext(OPTS);
        const second = await outbox.claimNext(OPTS);
        expect([first, second].every((x) => x !== null)).to.equal(true);
        expect(
          (
            await sql('SELECT DISTINCT org_id FROM platform.outbox ORDER BY 1')
          ).map((r) => r.org_id),
        ).to.have.members([A, B]);
      });

      it('an id that is not a row id matches nothing', async () => {
        await outbox.markPublished('not-an-id');
        await outbox.markFailed('1; DROP TABLE platform.outbox', {
          attempts: 1,
          nextAttemptAt: new Date(),
          lastError: 'x',
        });
      });
    });

    describe('notifications', () => {
      const mk = (org: string, user: string) => ({
        orgId: org,
        assignedTo: user,
        type: 't',
      });

      it('the badge counts equal three separate counts', async () => {
        const [org, user] = [newId(), newId()];
        const rows = await inOrg(org, () =>
          notifications.insertMany([
            mk(org, user),
            mk(org, user),
            mk(org, user),
            mk(org, user),
          ]),
        );
        await inOrg(org, () =>
          notifications.setStatus(
            { orgId: org, userId: user },
            rows[0]!._id,
            'read',
          ),
        );
        await inOrg(org, () =>
          notifications.setStatus(
            { orgId: org, userId: user },
            rows[1]!._id,
            'archived',
          ),
        );
        const together = await inOrg(org, () =>
          notifications.counts({ orgId: org, userId: user }),
        );
        const apart = await Promise.all(
          ['unread', 'read', 'archived'].map((s) =>
            inOrg(org, () =>
              notifications.list(
                { orgId: org, userId: user },
                { status: s, limit: 100 },
              ),
            ).then((r) => r.length),
          ),
        );
        expect([
          together.unread,
          together.read,
          together.archived,
        ]).to.deep.equal(apart);
        expect(apart).to.deep.equal([2, 1, 1]);
      });

      it('the retention boundary is exact', async () => {
        const [org, user] = [newId(), newId()];
        const [inside, outside] = await inOrg(org, () =>
          notifications.insertMany([mk(org, user), mk(org, user)]),
        );
        await sql(
          'UPDATE platform.notifications SET created_at = $2 WHERE id = $1',
          [
            inside!._id,
            new Date(Date.now() - NOTIFICATION_RETENTION_DAYS * DAY + 60_000),
          ],
        );
        await sql(
          'UPDATE platform.notifications SET created_at = $2 WHERE id = $1',
          [
            outside!._id,
            new Date(Date.now() - NOTIFICATION_RETENTION_DAYS * DAY - 60_000),
          ],
        );
        const seen = await inOrg(org, () =>
          notifications.list(
            { orgId: org, userId: user },
            { status: null, limit: 10 },
          ),
        );
        expect(seen.map((r) => r._id)).to.deep.equal([inside!._id]);
      });

      it('a row dated beyond every month partition lands in the default one and reads back', async () => {
        const [org, user] = [newId(), newId()];
        const [row] = await inOrg(org, () =>
          notifications.insertMany([mk(org, user)]),
        );
        await sql(
          `UPDATE platform.notifications SET created_at = now() + interval '3 years' WHERE id = $1`,
          [row!._id],
        );
        expect(
          (
            await sql(
              'SELECT tableoid::regclass::text AS t FROM platform.notifications WHERE id = $1',
              [row!._id],
            )
          )[0].t,
        ).to.equal('platform.notifications_def');
        const seen = await inOrg(org, () =>
          notifications.list(
            { orgId: org, userId: user },
            { status: null, limit: 10 },
          ),
        );
        expect(seen.map((r) => r._id)).to.deep.equal([row!._id]);
        expect(
          (
            await inOrg(org, () =>
              notifications.setStatus(
                { orgId: org, userId: user },
                row!._id,
                'read',
              ),
            )
          )?.status,
        ).to.equal('read');
      });

      it('the current month has a partition of its own', async () => {
        const [org, user] = [newId(), newId()];
        const [row] = await inOrg(org, () =>
          notifications.insertMany([mk(org, user)]),
        );
        const t = (
          await sql(
            'SELECT tableoid::regclass::text AS t FROM platform.notifications WHERE id = $1',
            [row!._id],
          )
        )[0].t as string;
        expect(t).to.match(/^platform\.notifications_y\d{4}m\d{2}$/);
      });
    });

    describe('the TTL sweeper', () => {
      const logger = { info: sinon.stub(), error: sinon.stub() };
      const sweeper = new TtlSweeper(logger as never);
      const org = newId();
      it('removes notifications past retention and delivered events after a week, and nothing newer', async () => {
        const user = newId();
        const [old, fresh] = await inOrg(org, () =>
          notifications.insertMany([
            { orgId: org, assignedTo: user, type: 't' },
            { orgId: org, assignedTo: user, type: 't' },
          ]),
        );
        await sql(
          'UPDATE platform.notifications SET created_at = $2 WHERE id = $1',
          [old!._id, ago((NOTIFICATION_RETENTION_DAYS + 1) * DAY)],
        );
        for (const [i, [status, age]] of (
          [
            ['published', 8 * DAY],
            ['published', DAY],
            ['pending', 40 * DAY],
          ] as const
        ).entries())
          await sql(
            `INSERT INTO platform.outbox (org_id, topic, msg_key, value, status, next_attempt_at, published_at, created_at, updated_at) VALUES ($1, 't', $2, '{}', $3, now(), $4, now(), now())`,
            [org, `s${i}`, status, status === 'published' ? ago(age) : null],
          );
        const removed = await sweeper.sweepOnce();
        expect(removed.notifications).to.be.at.least(1);
        expect(removed.outbox).to.equal(1);
        expect(
          (
            await sql(
              'SELECT id FROM platform.notifications WHERE org_id = $1',
              [org],
            )
          ).map((r) => r.id),
        ).to.deep.equal([fresh!._id]);
        expect(
          (
            await sql(
              `SELECT msg_key FROM platform.outbox WHERE org_id = $1 ORDER BY msg_key`,
              [org],
            )
          ).map((r) => r.msg_key),
        ).to.deep.equal(['s1', 's2']);
      });

      it('a table that fails does not stop the others', async () => {
        const user = newId();
        const [old] = await inOrg(org, () =>
          notifications.insertMany([
            { orgId: org, assignedTo: user, type: 't' },
          ]),
        );
        await sql(
          'UPDATE platform.notifications SET created_at = $2 WHERE id = $1',
          [old!._id, ago((NOTIFICATION_RETENTION_DAYS + 1) * DAY)],
        );
        await sql('ALTER TABLE platform.outbox RENAME TO outbox_away');
        try {
          logger.error.resetHistory();
          const removed = await sweeper.sweepOnce();
          expect(removed).to.not.have.property('outbox');
          expect(removed.notifications).to.be.at.least(1);
          expect(
            logger.error.calledWithMatch(
              'TTL sweep failed',
              sinon.match({ target: 'outbox' }),
            ),
          ).to.equal(true);
        } finally {
          await sql('ALTER TABLE platform.outbox_away RENAME TO outbox');
        }
      });
    });
  });
}
