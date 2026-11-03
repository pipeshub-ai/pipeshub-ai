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
}
