/**
 * Contract every NotificationRepository must meet. Store-neutral: the store
 * under test supplies the repository, an id generator and one raw-store helper.
 */
import { expect } from 'chai';
import type {
  NewNotification,
  NotificationRepository,
} from '../../../src/libs/repositories/notification';

export interface NotificationFixture {
  repo: NotificationRepository;
  newId(): string;
  /** Moves a row's createdAt without touching anything else. */
  setCreatedAt(id: string, at: Date): Promise<void>;
}

const HEX = /^[0-9a-f]{24}$/;

export function describeNotificationContract(fx: NotificationFixture): void {
  const { repo } = fx;
  const mk = (org: string, user: string, over: Partial<NewNotification> = {}): NewNotification => ({
    orgId: org,
    assignedTo: user,
    type: 'test',
    title: 't',
    message: 'm',
    status: 'unread',
    isDeleted: false,
    ...over,
  });
  const ago = (ms: number) => new Date(Date.now() - ms);

  it('insertMany stores one row per recipient and returns plain rows with string ids', async () => {
    const org = fx.newId();
    const [a, b] = [fx.newId(), fx.newId()];
    const rows = await repo.insertMany([mk(org, a), mk(org, b, { payload: { x: 1 } })]);
    expect(rows).to.have.length(2);
    expect(rows[0]._id).to.match(HEX);
    expect(rows[0].orgId).to.equal(org);
    expect(rows[0].assignedTo).to.equal(a);
    expect(rows[1].payload).to.deep.equal({ x: 1 });
    expect(rows[0].status).to.equal('unread');
    expect(rows[0].createdAt).to.be.instanceOf(Date);
    expect(JSON.parse(JSON.stringify(rows[0])).assignedTo).to.equal(a);
  });

  it('insert stores one row and returns it with string ids', async () => {
    const org = fx.newId();
    const user = fx.newId();
    const row = await repo.insert(mk(org, user, { payload: { y: 2 } }));
    expect(row._id).to.match(HEX);
    expect(row).to.include({ orgId: org, assignedTo: user, status: 'unread' });
    expect(row.payload).to.deep.equal({ y: 2 });
    expect(JSON.parse(JSON.stringify(row)).assignedTo).to.equal(user);
  });

  it('list is newest first, only the user\'s, in the org, and never archived or deleted ones by default', async () => {
    const org = fx.newId();
    const me = fx.newId();
    const [r1, r2, r3, arch, del] = await repo.insertMany([
      mk(org, me), mk(org, me), mk(org, me), mk(org, me, { status: 'archived' }), mk(org, me, { isDeleted: true }),
    ]);
    await repo.insertMany([mk(org, fx.newId())]);
    await repo.insertMany([mk(fx.newId(), me)]);
    await fx.setCreatedAt(r1._id, ago(3000));
    await fx.setCreatedAt(r2._id, ago(2000));
    await fx.setCreatedAt(r3._id, ago(1000));
    const rows = await repo.list({ orgId: org, userId: me }, { status: null, limit: 10 });
    expect(rows.map((r) => r._id)).to.deep.equal([r3._id, r2._id, r1._id]);
    expect(rows.map((r) => r._id)).to.not.include.members([arch._id, del._id]);
    const archived = await repo.list({ orgId: org, userId: me }, { status: 'archived', limit: 10 });
    expect(archived.map((r) => r._id)).to.deep.equal([arch._id]);
  });

  it('list pages by (createdAt, _id): ties break on _id descending, no row repeats or is skipped', async () => {
    const org = fx.newId();
    const me = fx.newId();
    const rows = await repo.insertMany(Array.from({ length: 7 }, () => mk(org, me)));
    const same = new Date(Date.now() - 5000);
    for (const r of rows.slice(0, 4)) await fx.setCreatedAt(r._id, same);
    for (const [i, r] of rows.slice(4).entries()) await fx.setCreatedAt(r._id, ago(1000 - i * 100));
    const seen: string[] = [];
    let cursor: { createdAt: Date; id: string } | undefined;
    for (let page = 0; page < 10; page++) {
      const got = await repo.list({ orgId: org, userId: me }, { status: null, limit: 3, cursor });
      const shown = got.slice(0, 3);
      seen.push(...shown.map((r) => r._id));
      if (got.length <= 3) break;
      const last = shown[shown.length - 1];
      cursor = { createdAt: last.createdAt, id: last._id };
    }
    expect(seen).to.have.length(7);
    expect(new Set(seen).size).to.equal(7);
    const tied = seen.filter((id) => rows.slice(0, 4).some((r) => r._id === id));
    expect(tied).to.deep.equal([...tied].sort().reverse());
  });

  it('list returns one row more than the limit so the caller can see there is a next page', async () => {
    const org = fx.newId();
    const me = fx.newId();
    await repo.insertMany(Array.from({ length: 4 }, () => mk(org, me)));
    expect(await repo.list({ orgId: org, userId: me }, { status: null, limit: 3 })).to.have.length(4);
    expect(await repo.list({ orgId: org, userId: me }, { status: null, limit: 4 })).to.have.length(4);
  });

  it('rows past the retention window are hidden from every read and change', async () => {
    const org = fx.newId();
    const me = fx.newId();
    const [old, fresh] = await repo.insertMany([mk(org, me), mk(org, me)]);
    await fx.setCreatedAt(old._id, ago(31 * 24 * 3600_000));
    const scope = { orgId: org, userId: me };
    expect((await repo.list(scope, { status: null, limit: 10 })).map((r) => r._id)).to.deep.equal([fresh._id]);
    expect((await repo.counts(scope)).unread).to.equal(1);
    expect(await repo.setStatus(scope, old._id, 'read')).to.equal(null);
    expect(await repo.softDelete(scope, old._id)).to.equal(null);
    expect(await repo.markAllRead(scope)).to.equal(1);
  });

  it('counts is the badge: one count per status for this user, deleted ones not counted', async () => {
    const org = fx.newId();
    const me = fx.newId();
    await repo.insertMany([
      mk(org, me), mk(org, me), mk(org, me, { status: 'read' }), mk(org, me, { status: 'archived' }),
      mk(org, me, { isDeleted: true }), mk(org, fx.newId()),
    ]);
    await repo.insertMany([mk(fx.newId(), me)]);
    expect(await repo.counts({ orgId: org, userId: me })).to.deep.equal({ unread: 2, read: 1, archived: 1 });
    expect(await repo.counts({ orgId: org, userId: fx.newId() })).to.deep.equal({ unread: 0, read: 0, archived: 0 });
  });

  it('markAllRead reads only this user\'s unread rows and reports how many changed', async () => {
    const org = fx.newId();
    const me = fx.newId();
    const other = fx.newId();
    await repo.insertMany([mk(org, me), mk(org, me), mk(org, me, { status: 'archived' }), mk(org, other)]);
    expect(await repo.markAllRead({ orgId: org, userId: me })).to.equal(2);
    expect(await repo.markAllRead({ orgId: org, userId: me })).to.equal(0);
    expect(await repo.counts({ orgId: org, userId: me })).to.deep.equal({ unread: 0, read: 2, archived: 1 });
    expect((await repo.counts({ orgId: org, userId: other })).unread).to.equal(1);
  });

  it('setStatus changes one row and returns it as updated; archived rows are not reachable', async () => {
    const org = fx.newId();
    const me = fx.newId();
    const scope = { orgId: org, userId: me };
    const [a, arch] = await repo.insertMany([mk(org, me), mk(org, me, { status: 'archived' })]);
    expect((await repo.setStatus(scope, a._id, 'read'))?.status).to.equal('read');
    expect((await repo.setStatus(scope, a._id, 'unread'))?.status).to.equal('unread');
    expect((await repo.setStatus(scope, a._id, 'archived'))?.status).to.equal('archived');
    expect(await repo.setStatus(scope, arch._id, 'read')).to.equal(null);
    expect(await repo.setStatus({ orgId: org, userId: fx.newId() }, a._id, 'read')).to.equal(null);
    expect(await repo.setStatus({ orgId: fx.newId(), userId: me }, a._id, 'read')).to.equal(null);
  });

  it('unarchive turns an archived row into a read one, and only an archived one', async () => {
    const org = fx.newId();
    const me = fx.newId();
    const scope = { orgId: org, userId: me };
    const [arch, plain] = await repo.insertMany([mk(org, me, { status: 'archived' }), mk(org, me)]);
    expect((await repo.unarchive(scope, arch._id))?.status).to.equal('read');
    expect(await repo.unarchive(scope, arch._id)).to.equal(null);
    expect(await repo.unarchive(scope, plain._id)).to.equal(null);
  });

  it('softDelete hides the row (archived too) and records who deleted it', async () => {
    const org = fx.newId();
    const me = fx.newId();
    const scope = { orgId: org, userId: me };
    const [a, arch] = await repo.insertMany([mk(org, me), mk(org, me, { status: 'archived' })]);
    const gone = await repo.softDelete(scope, a._id);
    expect(gone?.isDeleted).to.equal(true);
    expect(gone?.deletedBy).to.equal(me);
    expect((await repo.softDelete(scope, arch._id))?.isDeleted).to.equal(true);
    expect(await repo.softDelete(scope, a._id)).to.equal(null);
    expect(await repo.list(scope, { status: 'archived', limit: 10 })).to.deep.equal([]);
    expect(await repo.softDelete({ orgId: org, userId: fx.newId() }, a._id)).to.equal(null);
  });

  it('exists is by org, user and id and is ungated: a deleted or expired notification still exists; another user, org or a bad id does not', async () => {
    const org = fx.newId();
    const me = fx.newId();
    const other = fx.newId();
    const row = await repo.insert(mk(org, me));
    expect(await repo.exists({ orgId: org, userId: me }, row._id)).to.equal(true);
    expect(await repo.exists({ orgId: org, userId: other }, row._id), 'another user').to.equal(false);
    expect(await repo.exists({ orgId: fx.newId(), userId: me }, row._id), 'another org').to.equal(false);
    expect(await repo.exists({ orgId: org, userId: me }, fx.newId()), 'another id').to.equal(false);
    expect(await repo.exists({ orgId: org, userId: me }, 'not-an-id')).to.equal(false);
    await repo.softDelete({ orgId: org, userId: me }, row._id);
    expect(await repo.exists({ orgId: org, userId: me }, row._id), 'a deleted one still exists').to.equal(true);
    await fx.setCreatedAt(row._id, ago(400 * 24 * 3600 * 1000));
    expect(await repo.exists({ orgId: org, userId: me }, row._id), 'an expired one still exists').to.equal(true);
    const later = await repo.insert(mk(org, me, { visibleAt: new Date(Date.now() + 3600_000) }));
    expect(await repo.list({ orgId: org, userId: me }, { status: null, limit: 10 }), 'a scheduled one is not listed').to.deep.equal([]);
    expect(await repo.exists({ orgId: org, userId: me }, later._id), 'a scheduled one still exists').to.equal(true);
  });

  it('a row that names its own _id is stored under it and is idempotent: a republish returns the stored row and writes nothing', async () => {
    const org = fx.newId();
    const me = fx.newId();
    const id = fx.newId();
    const [a] = await repo.insertMany([mk(org, me, { _id: id, title: 'first' })]);
    expect(a!._id).to.equal(id);
    const [b, c] = await repo.insertMany([mk(org, me, { _id: id, title: 'second' }), mk(org, me, { title: 'plain' })]);
    expect(b!._id, 'the existing row').to.equal(id);
    expect(b!.title, 'nothing was overwritten').to.equal('first');
    expect(c!._id).to.match(HEX);
    expect(c!._id).to.not.equal(id);
    expect(await repo.exists({ orgId: org, userId: me }, id)).to.equal(true);
    const counts = await repo.counts({ orgId: org, userId: me });
    expect(counts.unread, 'one row for the id, one plain').to.equal(2);
  });

  describe('scheduled notifications (visibleAt)', function () {
    this.timeout(30_000);
    const ahead = (ms: number) => new Date(Date.now() + ms);
    const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
    const HOLD_MS = 1500;

    it('a row without visibleAt carries none and reads as before', async () => {
      const org = fx.newId();
      const me = fx.newId();
      const row = await repo.insert(mk(org, me));
      expect(row.visibleAt).to.equal(undefined);
      expect((await repo.list({ orgId: org, userId: me }, { status: null, limit: 10 })).map((r) => r._id)).to.deep.equal([row._id]);
    });

    it('before visibleAt a row is not listed, counted or marked read; at visibleAt it is there, still unread', async () => {
      const org = fx.newId();
      const me = fx.newId();
      const scope = { orgId: org, userId: me };
      const [plain, later] = await repo.insertMany([mk(org, me), mk(org, me, { visibleAt: ahead(HOLD_MS) })]);
      expect((await repo.list(scope, { status: null, limit: 10 })).map((r) => r._id)).to.deep.equal([plain._id]);
      expect((await repo.list(scope, { status: 'unread', limit: 10 })).map((r) => r._id)).to.deep.equal([plain._id]);
      expect(await repo.counts(scope)).to.deep.equal({ unread: 1, read: 0, archived: 0 });
      expect(await repo.markAllRead(scope)).to.equal(1);
      expect(await repo.counts(scope)).to.deep.equal({ unread: 0, read: 1, archived: 0 });
      await sleep(HOLD_MS + 150);
      expect((await repo.list(scope, { status: null, limit: 10 })).map((r) => r._id)).to.have.members([plain._id, later._id]);
      expect(await repo.counts(scope)).to.deep.equal({ unread: 1, read: 1, archived: 0 });
      expect(await repo.markAllRead(scope)).to.equal(1);
    });

    it('a row that is not yet visible is not found by id: setStatus, unarchive and softDelete answer null, and change nothing', async () => {
      const org = fx.newId();
      const me = fx.newId();
      const scope = { orgId: org, userId: me };
      const at = ahead(HOLD_MS);
      const [a, arch, d] = await repo.insertMany([
        mk(org, me, { visibleAt: at }),
        mk(org, me, { visibleAt: at, status: 'archived' }),
        mk(org, me, { visibleAt: at }),
      ]);
      expect(await repo.setStatus(scope, a._id, 'read')).to.equal(null);
      expect(await repo.setStatus(scope, a._id, 'archived')).to.equal(null);
      expect(await repo.unarchive(scope, arch._id)).to.equal(null);
      expect(await repo.softDelete(scope, d._id)).to.equal(null);
      expect(await repo.softDelete(scope, arch._id)).to.equal(null);
      await sleep(HOLD_MS + 150);
      // nothing happened to them meanwhile
      expect(await repo.counts(scope)).to.deep.equal({ unread: 2, read: 0, archived: 1 });
      expect((await repo.setStatus(scope, a._id, 'read'))?.status).to.equal('read');
      expect((await repo.unarchive(scope, arch._id))?.status).to.equal('read');
      expect((await repo.softDelete(scope, d._id))?.isDeleted).to.equal(true);
    });

    it('the served updatedAt is visibleAt (not the time of the insert) and createdAt defaults to it', async () => {
      const org = fx.newId();
      const me = fx.newId();
      const at = ahead(60_000);
      const row = await repo.insert(mk(org, me, { visibleAt: at }));
      expect(row.visibleAt).to.be.instanceOf(Date);
      expect(row.visibleAt?.getTime()).to.equal(at.getTime());
      expect(row.updatedAt?.getTime()).to.equal(at.getTime());
      expect(row.createdAt.getTime()).to.equal(at.getTime());
      const given = new Date(at.getTime() - 5000);
      const two = await repo.insert(mk(org, me, { visibleAt: at, createdAt: given }));
      expect(two.createdAt.getTime()).to.equal(given.getTime());
      expect(two.updatedAt?.getTime()).to.equal(at.getTime());
    });

    it('visibleAt and createdAt that arrive as ISO strings are stored as dates', async () => {
      const org = fx.newId();
      const me = fx.newId();
      const scope = { orgId: org, userId: me };
      const past = ago(10_000);
      const [seen, hidden] = await repo.insertMany([
        mk(org, me, { visibleAt: past.toISOString(), createdAt: past.toISOString() }),
        mk(org, me, { visibleAt: ahead(60_000).toISOString() }),
      ]);
      expect(seen.visibleAt).to.be.instanceOf(Date);
      expect(seen.createdAt).to.be.instanceOf(Date);
      expect(seen.visibleAt?.getTime()).to.equal(past.getTime());
      expect(seen.createdAt.getTime()).to.equal(past.getTime());
      expect(seen.updatedAt?.getTime()).to.equal(past.getTime());
      expect(hidden.visibleAt).to.be.instanceOf(Date);
      expect((await repo.list(scope, { status: null, limit: 10 })).map((r) => r._id)).to.deep.equal([seen._id]);
      expect((await repo.list(scope, { status: null, limit: 10 }))[0].visibleAt).to.be.instanceOf(Date);
    });

    it('a visibleAt that is not a date is refused, and nothing is stored', async () => {
      const org = fx.newId();
      const me = fx.newId();
      let err: unknown;
      try {
        await repo.insert(mk(org, me, { visibleAt: 'not a date' }));
      } catch (e) {
        err = e;
      }
      expect(err).to.be.instanceOf(Error);
      expect(await repo.counts({ orgId: org, userId: me })).to.deep.equal({ unread: 0, read: 0, archived: 0 });
    });

    it('a row scheduled far ahead (past any partition made in advance) is stored and stays hidden', async () => {
      const org = fx.newId();
      const me = fx.newId();
      const scope = { orgId: org, userId: me };
      const far = await repo.insert(mk(org, me, { visibleAt: ahead(400 * 24 * 3600_000) }));
      expect(far._id).to.match(HEX);
      expect(await repo.list(scope, { status: null, limit: 10 })).to.deep.equal([]);
      expect(await repo.counts(scope)).to.deep.equal({ unread: 0, read: 0, archived: 0 });
      expect(await repo.setStatus(scope, far._id, 'read')).to.equal(null);
    });
  });
}
