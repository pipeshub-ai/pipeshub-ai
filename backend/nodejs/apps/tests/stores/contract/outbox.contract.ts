/**
 * Contract every OutboxRepository and MailLogRepository must meet.
 * Store-neutral: the store under test supplies the repositories, a unit of
 * work, and raw-store helpers.
 */
import { expect } from 'chai';
import type {
  NewOutboxEvent,
  OutboxRepository,
  OutboxEventRow,
} from '../../../src/libs/repositories/outbox';
import type { MailLogRepository } from '../../../src/libs/repositories/mail-log';
import type { UnitOfWork } from '../../../src/libs/repositories/uow';

export interface OutboxFixture {
  repo: OutboxRepository;
  /** Absent when the mail log is covered by its own suite. */
  mailLog?: MailLogRepository;
  uow: UnitOfWork;
  /** True when a rolled-back unit of work undoes its writes (a replica set). */
  transactional: boolean;
  /** Every outbox row, oldest first. */
  allRows(): Promise<OutboxEventRow[]>;
  /** Removes all outbox rows, so each test starts empty. */
  clear(): Promise<void>;
  setCreatedAt(id: string, at: Date): Promise<void>;
  setRow(id: string, set: Record<string, unknown>): Promise<void>;
  mailLogRows?(): Promise<Record<string, unknown>[]>;
  newId(): string;
  /** What a row id looks like in this store (an ObjectId by default; a database sequence number on PostgreSQL). */
  idPattern?: RegExp;
}

const OPTS = { leaseMs: 60_000, candidateBatch: 2, maxScan: 20 };

export function describeOutboxContract(fx: OutboxFixture): void {
  const { repo } = fx;
  const ev = (over: Partial<NewOutboxEvent> = {}): NewOutboxEvent => ({
    topic: 'entity-events',
    key: 'userAdded',
    orderingKey: 'global',
    value: '{}',
    headers: { eventType: 'userAdded' },
    status: 'pending',
    attempts: 0,
    nextAttemptAt: new Date(Date.now() - 1000),
    ...over,
  });
  /** Appends events oldest first with distinct, increasing createdAt. */
  const appendAll = async (events: NewOutboxEvent[]): Promise<OutboxEventRow[]> => {
    for (const e of events) await repo.append(e);
    const rows = await fx.allRows();
    for (const [i, r] of rows.entries()) await fx.setCreatedAt(r._id, new Date(Date.now() - 100_000 + i * 1000));
    return fx.allRows();
  };

  beforeEach(() => fx.clear());

  describe('append', () => {
    it('stores a pending event with string id and its headers', async () => {
      await repo.append(ev({ key: 'k1', value: '{"a":1}' }));
      const [row] = await fx.allRows();
      expect(row._id).to.match(fx.idPattern ?? /^[0-9a-f]{24}$/);
      expect(row).to.include({ topic: 'entity-events', key: 'k1', value: '{"a":1}', status: 'pending', attempts: 0 });
      expect(row.headers).to.deep.equal({ eventType: 'userAdded' });
    });

    it('is stored with the change that caused it: a rolled-back unit of work leaves no event (replica set)', async () => {
      let thrown = false;
      try {
        await fx.uow.run(async (tx) => {
          await repo.append(ev({ key: 'in-tx' }), tx);
          throw new Error('abort');
        });
      } catch {
        thrown = true;
      }
      expect(thrown).to.equal(true);
      expect((await fx.allRows()).length).to.equal(fx.transactional ? 0 : 1);
    });

    it('a committed unit of work keeps it', async () => {
      await fx.uow.run((tx) => repo.append(ev({ key: 'kept' }), tx));
      expect((await fx.allRows()).map((r) => r.key)).to.deep.equal(['kept']);
    });
  });

  describe('claimNext', () => {
    it('takes the oldest due event first and marks it publishing', async () => {
      const [a, b] = await appendAll([ev({ key: 'a', orderingKey: 'o1' }), ev({ key: 'b', orderingKey: 'o2' })]);
      const first = await repo.claimNext(OPTS);
      expect(first?._id).to.equal(a._id);
      expect(first?.status).to.equal('publishing');
      expect(first?.claimedAt).to.be.instanceOf(Date);
      expect((await repo.claimNext(OPTS))?._id).to.equal(b._id);
      expect(await repo.claimNext(OPTS)).to.equal(null);
    });

    it('skips events that are not due yet', async () => {
      await appendAll([ev({ nextAttemptAt: new Date(Date.now() + 3600_000) })]);
      expect(await repo.claimNext(OPTS)).to.equal(null);
    });

    it('holds back a later event while an older one about the same entity is undelivered, but not other entities', async () => {
      const [blocker, blocked, free] = await appendAll([
        ev({ key: 'first', orderingKey: 'user-1', nextAttemptAt: new Date(Date.now() + 3600_000) }),
        ev({ key: 'second', orderingKey: 'user-1' }),
        ev({ key: 'other', orderingKey: 'user-2' }),
      ]);
      expect((await repo.claimNext(OPTS))?._id).to.equal(free._id);
      expect(await repo.claimNext(OPTS)).to.equal(null);
      await fx.setRow(blocker._id, { nextAttemptAt: new Date(Date.now() - 1) });
      expect((await repo.claimNext(OPTS))?._id).to.equal(blocker._id);
      expect(await repo.claimNext(OPTS)).to.equal(null);
      await repo.markPublished(blocker._id);
      expect((await repo.claimNext(OPTS))?._id).to.equal(blocked._id);
    });

    it('events of one entity created at the same instant are delivered in id order: the second waits for the first', async () => {
      const [first, second] = await appendAll([ev({ key: 'one', orderingKey: 'user-t' }), ev({ key: 'two', orderingKey: 'user-t' })]);
      const same = new Date(Date.now() - 100_000);
      await fx.setCreatedAt(first._id, same);
      await fx.setCreatedAt(second._id, same);
      expect((await repo.claimNext(OPTS))?._id).to.equal(first._id);
      // the first is still publishing, so the second must not be handed to another dispatcher
      expect(await repo.claimNext(OPTS)).to.equal(null);
      await repo.markPublished(first._id);
      expect((await repo.claimNext(OPTS))?._id).to.equal(second._id);
    });

    it('walks past a full batch of blocked rows to reach an unblocked one', async () => {
      const rows = await appendAll([
        ev({ key: 'stuck', orderingKey: 'u1', nextAttemptAt: new Date(Date.now() + 3600_000) }),
        ev({ key: 'b1', orderingKey: 'u1' }),
        ev({ key: 'b2', orderingKey: 'u1' }),
        ev({ key: 'b3', orderingKey: 'u1' }),
        ev({ key: 'free', orderingKey: 'u2' }),
      ]);
      expect((await repo.claimNext(OPTS))?._id).to.equal(rows[4]._id);
    });

    it('of several instances claiming at once, no event goes to two of them', async () => {
      await appendAll(Array.from({ length: 6 }, (_, i) => ev({ key: `e${i}`, orderingKey: `o${i}` })));
      const claimed = (await Promise.all(Array.from({ length: 12 }, () => repo.claimNext(OPTS)))).filter(Boolean) as OutboxEventRow[];
      // A pass that loses a race may come back empty while a row is left (the
      // next tick takes it), so only exclusivity is promised, then completeness.
      for (let next = await repo.claimNext(OPTS); next; next = await repo.claimNext(OPTS)) claimed.push(next);
      const ids = claimed.map((c) => c._id);
      expect(new Set(ids).size).to.equal(ids.length);
      expect(ids.length).to.equal(6);
    });

    it('takes back an abandoned claim, but not a fresh one', async () => {
      const [a] = await appendAll([ev()]);
      await repo.claimNext(OPTS);
      expect(await repo.claimNext(OPTS)).to.equal(null);
      await fx.setRow(a._id, { claimedAt: new Date(Date.now() - 120_000) });
      expect((await repo.claimNext(OPTS))?._id).to.equal(a._id);
    });
  });

  describe('delivery outcome', () => {
    it('markPublished records the time and clears the claim and error', async () => {
      const [a] = await appendAll([ev()]);
      await repo.claimNext(OPTS);
      await repo.markFailed(a._id, { attempts: 1, nextAttemptAt: new Date(Date.now() - 1), lastError: 'boom' });
      await repo.claimNext(OPTS);
      await repo.markPublished(a._id);
      const [row] = await fx.allRows();
      expect(row.status).to.equal('published');
      expect(row.publishedAt).to.be.instanceOf(Date);
      expect(row).to.not.have.property('claimedAt');
      expect(row).to.not.have.property('lastError');
    });

    it('markFailed puts it back to pending with the next attempt later, and it is not claimed until then', async () => {
      const [a] = await appendAll([ev()]);
      await repo.claimNext(OPTS);
      const later = new Date(Date.now() + 3600_000);
      await repo.markFailed(a._id, { attempts: 2, nextAttemptAt: later, lastError: 'broker down' });
      const [row] = await fx.allRows();
      expect(row).to.include({ status: 'pending', attempts: 2, lastError: 'broker down' });
      expect(row.nextAttemptAt.getTime()).to.equal(later.getTime());
      expect(row).to.not.have.property('claimedAt');
      expect(await repo.claimNext(OPTS)).to.equal(null);
    });

    it('statusCounts counts every status, zero for none', async () => {
      expect(await repo.statusCounts()).to.deep.equal({ pending: 0, publishing: 0, published: 0, failed: 0 });
      const rows = await appendAll([ev({ orderingKey: 'a' }), ev({ orderingKey: 'b' }), ev({ orderingKey: 'c' }), ev({ orderingKey: 'd' })]);
      await repo.claimNext(OPTS);
      await repo.markPublished(rows[2]._id);
      await fx.setRow(rows[3]._id, { status: 'failed' });
      expect(await repo.statusCounts()).to.deep.equal({ pending: 1, publishing: 1, published: 1, failed: 1 });
    });
  });

  (fx.mailLog ? describe : describe.skip)('mail log', () => {
    it('records what was sent, with the cc list and the org', async () => {
      const org = fx.newId();
      await fx.mailLog!.record({
        orgId: org, subject: 'Hello', from: 'noreply@example.com', to: ['a@example.com', 'b@example.com'],
        cc: [], emailTemplateType: 'welcome',
      });
      const rows = await fx.mailLogRows!();
      const row = rows.find((r) => String(r.orgId) === org);
      expect(row).to.include({ subject: 'Hello', from: 'noreply@example.com', emailTemplateType: 'welcome' });
      expect(row?.to).to.deep.equal(['a@example.com', 'b@example.com']);
      expect(row?.cc).to.deep.equal([]);
      expect(row?.createdAt).to.be.instanceOf(Date);
    });

    it('a record without a sender is refused, so the caller can log the failure', async () => {
      let err: unknown;
      try {
        await fx.mailLog!.record({ to: ['a@example.com'], emailTemplateType: 'welcome' } as never);
      } catch (e) {
        err = e;
      }
      expect(err).to.be.instanceOf(Error);
    });

    it('a record without recipients is stored with an empty list', async () => {
      const org = fx.newId();
      await fx.mailLog!.record({ orgId: org, from: 'x@example.com', emailTemplateType: 'welcome' });
      const row = (await fx.mailLogRows!()).find((r) => String(r.orgId) === org);
      expect(row?.to).to.deep.equal([]);
    });
  });
}
