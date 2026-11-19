import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import {
  castDate,
  isNotYetVisible,
  prepareNotification,
} from '../../../src/libs/repositories/notification/dates';
import { MongoNotificationRepository } from '../../../src/libs/repositories/notification/mongo';
import { Notifications } from '../../../src/modules/notification/schema/notification.schema';
import { buildNotificationDocForUser } from '../../../src/modules/notification/utils/notification-payload.resolver';

const id = () => String(new mongoose.Types.ObjectId());
const scope = { orgId: id(), userId: id() };

describe('scheduled notifications: the date helpers', () => {
  it('castDate: a date or an ISO string becomes a date, nothing stays nothing, junk is refused', () => {
    const at = new Date('2026-05-06T07:08:09.123Z');
    expect(castDate(at, 'f')).to.equal(at);
    expect(castDate(at.toISOString(), 'f')?.getTime()).to.equal(at.getTime());
    expect(castDate(undefined, 'f')).to.equal(null);
    expect(castDate(null, 'f')).to.equal(null);
    expect(() => castDate('nope', 'visibleAt')).to.throw('visibleAt');
    expect(() => castDate({}, 'visibleAt')).to.throw('visibleAt');
  });

  it('prepareNotification: strings become dates; a scheduled row takes visibleAt as updatedAt and, unless given, createdAt', () => {
    const at = new Date(Date.now() + 60_000);
    const out = prepareNotification({
      orgId: id(),
      assignedTo: id(),
      type: 'x',
      visibleAt: at.toISOString(),
      updatedAt: new Date(0),
    }) as Record<string, unknown>;
    expect(out.visibleAt).to.be.instanceOf(Date);
    expect((out.visibleAt as Date).getTime()).to.equal(at.getTime());
    expect((out.updatedAt as Date).getTime()).to.equal(at.getTime());
    expect((out.createdAt as Date).getTime()).to.equal(at.getTime());
    const given = new Date(at.getTime() - 1000);
    const two = prepareNotification({ orgId: id(), assignedTo: id(), type: 'x', visibleAt: at, createdAt: given.toISOString() }) as Record<string, unknown>;
    expect(two.createdAt).to.be.instanceOf(Date);
    expect((two.createdAt as Date).getTime()).to.equal(given.getTime());
  });

  it('prepareNotification: a row without visibleAt is left as it was, and a string createdAt becomes a date', () => {
    const plain = { orgId: id(), assignedTo: id(), type: 'x' };
    expect(prepareNotification(plain)).to.deep.equal(plain);
    const at = new Date('2026-01-01T00:00:00.000Z');
    const out = prepareNotification({ ...plain, createdAt: at.toISOString() }) as Record<string, unknown>;
    expect(out.createdAt).to.be.instanceOf(Date);
    expect(out).to.not.have.property('visibleAt');
    expect(out).to.not.have.property('updatedAt');
  });

  it('isNotYetVisible: only a visibleAt ahead of now', () => {
    const now = new Date('2026-05-06T00:00:00.000Z');
    expect(isNotYetVisible(undefined, now)).to.equal(false);
    expect(isNotYetVisible(null, now)).to.equal(false);
    expect(isNotYetVisible('2026-05-06T00:00:00.000Z', now)).to.equal(false);
    expect(isNotYetVisible(new Date('2026-05-05T23:59:59.999Z'), now)).to.equal(false);
    expect(isNotYetVisible('2026-05-06T00:00:00.001Z', now)).to.equal(true);
  });

  it('the event keeps its visibleAt and createdAt when it becomes a notification for a user', () => {
    const at = new Date(Date.now() + 60_000).toISOString();
    const doc = buildNotificationDocForUser({ orgId: id(), type: 'x', visibleAt: at, createdAt: at, recipientUserIds: [id()] } as never, id());
    expect(doc.visibleAt).to.equal(at);
    expect(doc.createdAt).to.equal(at);
    expect(doc).to.not.have.property('recipientUserIds');
  });
});

describe('scheduled notifications: every MongoDB read and change names visibleAt', () => {
  afterEach(() => sinon.restore());
  const repo = new MongoNotificationRepository();
  const near = (d: Date) => Math.abs(d.getTime() - Date.now()) < 5000;

  /** The filter has the predicate "no visibleAt, or one at or before now". */
  function expectVisibleOnly(filter: Record<string, any>) {
    const and = filter.$and as Array<{ $or: Array<Record<string, any>> }>;
    expect(and, JSON.stringify(filter)).to.be.an('array').with.length(1);
    const [none, due] = and[0]!.$or;
    expect(none).to.deep.equal({ visibleAt: null });
    expect(due!.visibleAt.$lte).to.be.instanceOf(Date);
    expect(near(due!.visibleAt.$lte)).to.equal(true);
  }

  it('list (with and without a cursor), counts, markAllRead, setStatus, unarchive and softDelete', async () => {
    const lean = sinon.stub().resolves([]);
    const find = sinon.stub(Notifications, 'find').returns({ sort: () => ({ limit: () => ({ lean }) }) } as never);
    const count = sinon.stub(Notifications, 'countDocuments').resolves(0 as never);
    const updateMany = sinon.stub(Notifications, 'updateMany').resolves({ modifiedCount: 0 } as never);
    const one = sinon.stub(Notifications, 'findOneAndUpdate').returns({ lean: async () => null } as never);

    await repo.list(scope, { status: null, limit: 5 });
    await repo.list(scope, { status: 'unread', limit: 5, cursor: { createdAt: new Date(), id: id() } });
    expectVisibleOnly(find.firstCall.args[0] as never);
    expectVisibleOnly(find.secondCall.args[0] as never);
    // the cursor's own $or is still there beside the visibility one
    expect((find.secondCall.args[0] as Record<string, unknown>).$or).to.be.an('array');

    await repo.counts(scope);
    expect(count.callCount).to.equal(3);
    for (const c of count.getCalls()) expectVisibleOnly(c.args[0] as never);

    await repo.markAllRead(scope);
    expectVisibleOnly(updateMany.firstCall.args[0] as never);

    await repo.setStatus(scope, id(), 'read');
    await repo.unarchive(scope, id());
    await repo.softDelete(scope, id());
    expect(one.callCount).to.equal(3);
    for (const c of one.getCalls()) expectVisibleOnly(c.args[0] as never);
  });

  it('insert of a row with no time of its own calls the model as it always did, one document and no options', async () => {
    const create = sinon.stub(Notifications, 'create').resolves({ toObject: () => ({ _id: id() }) } as never);
    await repo.insert({ orgId: id(), assignedTo: id(), type: 'x' });
    expect(create.firstCall.args).to.have.length(1);
    expect(Array.isArray(create.firstCall.args[0])).to.equal(false);
  });

  it('insert stores a scheduled row with updatedAt = visibleAt, without the schema stamping its own times', async () => {
    const create = sinon.stub(Notifications, 'create').resolves([{ toObject: () => ({ _id: id() }) }] as never);
    const at = new Date(Date.now() + 60_000);
    await repo.insert({ orgId: id(), assignedTo: id(), type: 'x', visibleAt: at.toISOString() });
    const [docs, opts] = create.firstCall.args as unknown as [Array<Record<string, any>>, Record<string, unknown>];
    expect(opts).to.deep.equal({ timestamps: false });
    expect(docs[0]!.visibleAt).to.be.instanceOf(Date);
    expect(docs[0]!.updatedAt.getTime()).to.equal(at.getTime());
    expect(docs[0]!.createdAt.getTime()).to.equal(at.getTime());
  });
});
