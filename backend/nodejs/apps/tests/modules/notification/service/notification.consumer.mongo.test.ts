import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose, { Types } from 'mongoose';
import { NotificationConsumer } from '../../../../src/modules/notification/service/notification.consumer';
import { NotificationService } from '../../../../src/modules/notification/service/notification.service';
import { Notifications } from '../../../../src/modules/notification/schema/notification.schema';
import * as RecipientResolver from '../../../../src/modules/notification/utils/notification-recipient.resolver';
import { NotificationEmailDispatcher } from '../../../../src/modules/notification/service/notification-email.dispatcher';

const uri = process.env.PCC_MONGO_URI;

;(uri ? describe : describe.skip)('notification consumer against a real MongoDB', function () {
  this.timeout(30_000);
  const orgId = new Types.ObjectId();
  const userA = new Types.ObjectId();
  const userB = new Types.ObjectId();
  const emailIntent = { template: 'chatShared', actorName: 'Ada', orgName: 'Acme', accessLevel: 'read' };
  let deliver: (value: unknown) => Promise<void>;
  let sendToUser: sinon.SinonStub;
  let dispatch: sinon.SinonStub;

  before(async () => {
    await mongoose.connect(uri as string);
    await Notifications.init();
  });
  after(async () => {
    await Notifications.deleteMany({ orgId });
    await mongoose.disconnect();
  });

  beforeEach(async () => {
    await Notifications.deleteMany({ orgId });
    sinon
      .stub(RecipientResolver, 'resolveNotificationRecipientUserIds')
      .callsFake(async (_org, ids) => (ids ?? []).map((id) => new Types.ObjectId(id)));
    const logger = { info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(), debug: sinon.stub() };
    const broker = {
      isConnected: () => true,
      consume: sinon.stub().resolves(),
    };
    const notificationService = sinon.createStubInstance(NotificationService);
    sendToUser = notificationService.sendToUser as unknown as sinon.SinonStub;
    dispatch = sinon.stub().resolves();
    const consumer = new NotificationConsumer(
      broker as never,
      logger as never,
      notificationService as unknown as NotificationService,
      { dispatch },
    );
    await consumer.consume(async () => undefined);
    const wrapped = broker.consume.firstCall.args[0];
    deliver = (value) => wrapped({ value });
  });
  afterEach(() => sinon.restore());

  const shareEvent = (sessionId: string) =>
    JSON.stringify({
      orgId: String(orgId),
      type: 'chat.shared',
      recipientUserIds: [String(userA), String(userB)],
      title: 'Shared',
      message: 'A conversation was shared with you',
      redirectLink: `/chat?conversationId=${sessionId}`,
      dedupeKey: `chat.shared:${sessionId}`,
      emailIntent,
      payload: { sessionId },
    });

  it('delivers an event once: a redelivery adds no doc, push or email', async () => {
    const event = shareEvent('s1');
    await deliver(event);
    expect(await Notifications.countDocuments({ orgId })).to.equal(2);
    expect(sendToUser.callCount).to.equal(2);
    expect(dispatch.callCount).to.equal(2);
    const stored = await Notifications.findOne({ orgId, assignedTo: userA }).lean<Record<string, unknown>>();
    expect(stored).to.not.have.property('emailIntent');

    await deliver(event);
    expect(await Notifications.countDocuments({ orgId })).to.equal(2);
    expect(sendToUser.callCount).to.equal(2);
    expect(dispatch.callCount).to.equal(2);
  });

  it('F-12: a share, unshare, re-share loop gives a new bell row each round but one email a day', async () => {
    const publishEvent = sinon.stub().resolves();
    const real = new NotificationEmailDispatcher({ publishEvent }, { findEmail: async () => 'b@example.com' }, 'https://app', { info: sinon.stub(), warn: sinon.stub(), error: sinon.stub(), debug: sinon.stub() } as never);
    dispatch.callsFake((input) => real.dispatch(input));
    const round = (aclVersion: number) =>
      JSON.stringify({ ...JSON.parse(shareEvent('s9')), recipientUserIds: [String(userB)], dedupeKey: `chat.shared:s9:${aclVersion}` });
    await deliver(round(1));
    await Notifications.updateMany({ orgId, assignedTo: userB }, { $set: { status: 'archived' } });
    await deliver(round(3));
    expect(await Notifications.countDocuments({ orgId, assignedTo: userB })).to.equal(2);
    expect(dispatch.callCount).to.equal(2);
    expect(dispatch.secondCall.args[0].notification).to.include({ type: 'chat.shared', sessionId: 's9' });
    expect(publishEvent.callCount).to.equal(1);
  });

  it('on a partial redelivery only the missing recipient is pushed and emailed', async () => {
    await Notifications.create({
      orgId,
      assignedTo: userA,
      type: 'chat.shared',
      dedupeKey: 'chat.shared:s2',
    });
    await deliver(shareEvent('s2'));
    expect(await Notifications.countDocuments({ orgId })).to.equal(2);
    expect(sendToUser.callCount).to.equal(1);
    expect(sendToUser.firstCall.args[0]).to.equal(String(userB));
    expect(dispatch.callCount).to.equal(1);
    expect(dispatch.firstCall.args[0]).to.deep.include({
      dedupeKey: 'chat.shared:s2',
      redirectLink: '/chat?conversationId=s2',
    });
  });

  describe('chat.activity coalescing', () => {
    const activity = (sessionId: string, message = 'New activity') =>
      JSON.stringify({
        orgId: String(orgId),
        type: 'chat.activity',
        recipientUserIds: [String(userA)],
        message,
        redirectLink: `/chat?conversationId=${sessionId}`,
        coalesceKey: `chat.activity:${sessionId}`,
        payload: { sessionId, count: 1 },
      });

    it('keeps one unread doc per user and chat and counts the events', async () => {
      await deliver(activity('s3', 'first'));
      await deliver(activity('s3', 'second'));
      const docs = await Notifications.find({ orgId, assignedTo: userA }).lean();
      expect(docs).to.have.length(1);
      expect(docs[0]!.payload).to.deep.equal({ sessionId: 's3', count: 2 });
      expect(docs[0]!.message).to.equal('second');
      expect(docs[0]!.coalesceKey).to.equal('chat.activity:s3');
      expect(docs[0]!.status).to.equal('unread');
      expect(docs[0]!.createdAt).to.be.instanceOf(Date);
      expect(sendToUser.getCall(0).args[1]).to.equal('newNotification');
      expect(sendToUser.getCall(1).args[1]).to.equal('notificationUpdated');
      expect(dispatch.called).to.be.false;
    });

    it('concurrent events for a new chat converge on one doc with every event counted', async () => {
      await Promise.all(Array.from({ length: 12 }, () => deliver(activity('s4'))));
      const docs = await Notifications.find({ orgId, assignedTo: userA }).lean();
      expect(docs).to.have.length(1);
      expect((docs[0]!.payload as { count: number }).count).to.equal(12);
    });

    it('starts a new unread doc once the earlier one is read', async () => {
      await deliver(activity('s5'));
      await Notifications.updateOne({ orgId, assignedTo: userA }, { $set: { status: 'read' } });
      await deliver(activity('s5'));
      const docs = await Notifications.find({ orgId, assignedTo: userA }).sort({ createdAt: 1 }).lean();
      expect(docs.map((d) => d.status)).to.deep.equal(['read', 'unread']);
      expect((docs[1]!.payload as { count: number }).count).to.equal(1);
    });

    it('keeps chats separate', async () => {
      await deliver(activity('s6'));
      await deliver(activity('s7'));
      expect(await Notifications.countDocuments({ orgId, assignedTo: userA })).to.equal(2);
    });
  });

  it('events without the new keys insert one doc per recipient, as before', async () => {
    await deliver({
      orgId: String(orgId),
      type: 'CONNECTOR_SYNC_ERROR',
      recipientUserIds: [String(userA), String(userB)],
      title: 'Sync failed',
    });
    await deliver({
      orgId: String(orgId),
      type: 'CONNECTOR_SYNC_ERROR',
      recipientUserIds: [String(userA), String(userB)],
      title: 'Sync failed',
    });
    expect(await Notifications.countDocuments({ orgId })).to.equal(4);
    expect(sendToUser.callCount).to.equal(4);
    expect(dispatch.called).to.be.false;
  });
});
