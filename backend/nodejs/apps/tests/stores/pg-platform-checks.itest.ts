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
import {
  PgActivityRepository,
  trimActivitiesPage,
} from '../../src/libs/repositories/activity/pg';
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

    describe('visibility inside one reused unit', () => {
      it('a scheduled row is hidden before visibleAt and visible after it within the same open transaction (the app clock decides, not the transaction start)', async () => {
        const user = newId();
        const scope = { orgId: A, userId: user };
        // the operation's clock is held by the test: nothing here depends on how long the setup or the statements take
        const t0 = Date.now();
        const lead = 60_000;
        const insertScheduled = () =>
          inOrg(A, () =>
            notifications.insert({ orgId: A, assignedTo: user, type: 't', visibleAt: new Date(t0 + lead) }),
          );
        await insertScheduled();
        const clock = sinon.useFakeTimers({ now: t0, toFake: ['Date'] });
        try {
          const seen = await run(
            A,
            async () => {
              const before = await notifications.list(scope, { status: null, limit: 10 });
              const beforeCount = await notifications.counts(scope);
              clock.setSystemTime(t0 + lead + 1_000); // past visibleAt, the unit stays open
              const after = await notifications.list(scope, { status: null, limit: 10 });
              const afterCount = await notifications.counts(scope);
              const marked = await notifications.markAllRead(scope);
              return { before, beforeCount, after, afterCount, marked };
            },
            true,
          );
          expect(seen.before).to.deep.equal([]);
          expect(seen.beforeCount.unread).to.equal(0);
          expect(seen.after).to.have.length(1);
          expect(seen.afterCount.unread).to.equal(1);
          expect(seen.marked).to.equal(1);
          clock.setSystemTime(t0);
          const row = await insertScheduled();
          const changed = await run(
            A,
            async () => {
              const early = await notifications.setStatus(scope, row._id, 'read');
              clock.setSystemTime(t0 + lead + 1_000);
              const late = await notifications.setStatus(scope, row._id, 'read');
              return { early, late };
            },
            true,
          );
          expect(changed.early).to.equal(null);
          expect(changed.late?.status).to.equal('read');
        } finally {
          clock.restore();
        }
      });
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

    describe('the sign-in history trim', () => {
      const logger = { info: sinon.stub(), error: sinon.stub() };
      const activities = new PgActivityRepository();
      const org = async (deleted = false): Promise<string> => {
        const id = newId();
        await sql(
          `INSERT INTO identity.orgs (org_id, domain, contact_email, account_type, is_deleted, created_at, updated_at)
             VALUES ($1, 'x.test', 'a@x.test', 'business', $2, now(), now())`,
          [id, deleted],
        );
        return id;
      };
      // [user, type, days old, removed]; returns the row ids in the same order
      const plant = async (
        o: string,
        rows: Array<[string | null, string, number, boolean?]>,
      ): Promise<string[]> => {
        const ids: string[] = [];
        for (const [user, type, days, deleted] of rows) {
          const id = newId();
          ids.push(id);
          await sql(
            `INSERT INTO identity.user_activities (org_id, id, user_id, email, activity_type, ip_address, is_deleted, created_at, updated_at)
               VALUES ($1, $2, $3, 'a@x.test', $4, '127.0.0.1', $5, $6, $6)`,
            [o, id, user, type, deleted ?? false, ago(days * DAY)],
          );
        }
        return ids;
      };
      const left = async (o: string): Promise<string[]> =>
        (
          await sql(
            'SELECT id FROM identity.user_activities WHERE org_id = $1 ORDER BY id',
            [o],
          )
        ).map((r) => r.id as string);

      it('keeps each person newest row of a type however old, and what is newer than the retention; deletes the rest', async () => {
        const [a, b, gone] = [await org(), await org(), await org(true)];
        const [u1, u2] = [newId(), newId()];
        const keep = await plant(a, [
          [u1, 'LOGOUT', 200], // u1's newest sign-out: a token issued before it must stay refused
          [u1, 'LOGIN', 1],
          [u2, 'PASSWORD CHANGED', 400],
          [null, 'WRONG PASSWORD', 1],
        ]);
        const drop = await plant(a, [
          [u1, 'LOGOUT', 300],
          [u1, 'LOGIN', 100],
          [u1, 'LOGOUT', 150, true], // removed rows are never read
          [null, 'WRONG PASSWORD', 200], // an attempt with no person
        ]);
        const [bOld, bNew] = await plant(b, [
          [u1, 'LOGIN', 120],
          [u1, 'LOGIN', 95],
        ]);
        const [deletedOrgs] = await plant(gone, [[u1, 'LOGIN', 300]]);
        const before = await inOrg(a, () =>
          activities.latestSessionEnding(u1, a),
        );

        const sweeper = new TtlSweeper(logger as never, 60_000, () => new Date(), 90);
        const removed = await sweeper.sweepOnce();
        expect(removed.user_activities).to.be.at.least(drop.length + 1);
        expect(await left(a)).to.deep.equal([...keep].sort());
        expect(await left(b)).to.deep.equal([bNew]);
        expect(bOld).to.not.equal(bNew);
        // the organisation list comes from the live organisations only
        expect(await left(gone)).to.deep.equal([deletedOrgs]);
        const after = await inOrg(a, () =>
          activities.latestSessionEnding(u1, a),
        );
        expect(after?._id).to.equal(before?._id).and.to.equal(keep[0]);

        // the trim runs once a day: a second pass the same day leaves new old rows alone
        await plant(a, [[u1, 'LOGIN', 100]]);
        expect(await sweeper.sweepOnce()).to.not.have.property('user_activities');
      });

      it('pages through an organisation by id, each row read once', async () => {
        const a = await org();
        const u = newId();
        await plant(a, [
          [u, 'LOGIN', 400],
          [u, 'LOGIN', 300],
          [u, 'LOGIN', 200],
          [u, 'LOGIN', 100],
          [u, 'LOGIN', 1],
        ]);
        const cutoff = ago(90 * DAY);
        let [after, pages, total] = ['', 0, 0];
        for (;;) {
          const p = await trimActivitiesPage(a, after, cutoff, 2);
          total += p.removed;
          if (p.last === null) break;
          [after, pages] = [p.last, pages + 1];
        }
        expect(pages).to.equal(3);
        expect(total).to.equal(4);
        expect(await left(a)).to.have.length(1);
      });
    });
  });
}
