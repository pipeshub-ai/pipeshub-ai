/// <reference types="mocha" />
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import {
  archiveNotification,
  deleteNotification,
  getNotificationStats,
  listNotifications,
  markAllRead,
  markRead,
  unarchiveNotification,
} from '../../../../src/modules/notification/controllers/notification.controller';
import {
  setNotificationRepository,
  type NotificationRepository,
  type NotificationRow,
} from '../../../../src/libs/repositories/notification';
import { encodeCursor } from '../../../../src/modules/notification/utils/notification-api.utils';

const id = () => String(new mongoose.Types.ObjectId());
const row = (over: Partial<NotificationRow> = {}): NotificationRow => ({
  _id: id(),
  orgId: id(),
  assignedTo: id(),
  type: 't',
  status: 'unread',
  isDeleted: false,
  createdAt: new Date('2026-05-20T10:30:00.000Z'),
  ...over,
});

describe('notification.controller on the repository seam', () => {
  const userId = id();
  const orgId = id();
  let repo: { [K in keyof NotificationRepository]: sinon.SinonStub };
  let res: any;
  let next: sinon.SinonStub;
  const req = (extra: Record<string, unknown> = {}): any => ({
    user: { userId, orgId },
    query: {},
    params: {},
    ...extra,
  });

  beforeEach(() => {
    repo = {
      insertMany: sinon.stub(),
      list: sinon.stub().resolves([]),
      counts: sinon.stub().resolves({ unread: 2, read: 3, archived: 1 }),
      markAllRead: sinon.stub().resolves(4),
      setStatus: sinon.stub().resolves(row()),
      unarchive: sinon.stub().resolves(row()),
      softDelete: sinon.stub().resolves(row()),
    };
    setNotificationRepository(repo as unknown as NotificationRepository);
    res = { status: sinon.stub().returnsThis(), json: sinon.stub() };
    next = sinon.stub();
  });
  afterEach(() => setNotificationRepository());

  it('answers 401 without a valid user, before touching the store', async () => {
    await listNotifications({ user: {}, query: {} } as any, res, next);
    expect(res.status.calledWith(401)).to.equal(true);
    expect(repo.list.called).to.equal(false);
  });

  it('lists with the caller\'s scope, the clamped page size and the status filter', async () => {
    await listNotifications(req({ query: { limit: '500', status: 'read' } }), res, next);
    expect(repo.list.firstCall.args).to.deep.equal([{ userId, orgId }, { status: 'read', cursor: undefined, limit: 50 }]);
  });

  it('pages: more rows than the limit give hasMore and a cursor that resumes after the last row shown', async () => {
    const rows = [row(), row(), row()].map((r, i) => ({ ...r, createdAt: new Date(Date.UTC(2026, 4, 20, 10, 30 - i)) }));
    repo.list.resolves(rows);
    await listNotifications(req({ query: { limit: '2' } }), res, next);
    const body = res.json.firstCall.args[0];
    expect(body.notifications).to.have.length(2);
    expect(body.hasMore).to.equal(true);
    expect(body.cursor).to.equal(encodeCursor({ createdAt: rows[1].createdAt, _id: rows[1]._id }));

    repo.list.resetHistory();
    await listNotifications(req({ query: { limit: '2', cursor: body.cursor } }), res, next);
    expect(repo.list.firstCall.args[1].cursor).to.deep.equal({ createdAt: rows[1].createdAt, id: rows[1]._id });
  });

  it('rejects a malformed cursor with 400 and does not query', async () => {
    await listNotifications(req({ query: { cursor: 'not-a-cursor' } }), res, next);
    expect(res.status.calledWith(400)).to.equal(true);
    expect(repo.list.called).to.equal(false);
  });

  it('the badge is the three counts from one repository call', async () => {
    await getNotificationStats(req(), res, next);
    expect(repo.counts.calledOnceWith({ userId, orgId })).to.equal(true);
    expect(res.json.firstCall.args[0]).to.deep.equal({ unreadCount: 2, readCount: 3, archivedCount: 1 });
  });

  it('mark all read reports how many changed', async () => {
    await markAllRead(req(), res, next);
    expect(res.json.firstCall.args[0]).to.deep.equal({ success: true, modifiedCount: 4 });
  });

  it('mark read, archive, unarchive and delete use their repository calls and answer 404 when nothing matches', async () => {
    const target = id();
    await markRead(req({ params: { id: target } }), res, next);
    expect(repo.setStatus.calledWith({ userId, orgId }, target, 'read')).to.equal(true);
    await archiveNotification(req({ params: { id: target } }), res, next);
    expect(repo.setStatus.calledWith({ userId, orgId }, target, 'archived')).to.equal(true);
    await unarchiveNotification(req({ params: { id: target } }), res, next);
    expect(repo.unarchive.calledWith({ userId, orgId }, target)).to.equal(true);
    await deleteNotification(req({ params: { id: target } }), res, next);
    expect(repo.softDelete.calledWith({ userId, orgId }, target)).to.equal(true);
    expect(res.json.lastCall.args[0]).to.deep.equal({ success: true });

    repo.setStatus.resolves(null);
    res.status.resetHistory();
    await markRead(req({ params: { id: target } }), res, next);
    expect(res.status.calledWith(404)).to.equal(true);
  });

  it('a store failure goes to next()', async () => {
    repo.counts.rejects(new Error('down'));
    await getNotificationStats(req(), res, next);
    expect(next.firstCall.args[0].message).to.equal('down');
  });
});
