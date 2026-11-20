import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose, { Types } from 'mongoose';
import { NotificationConsumer } from '../../../../src/modules/notification/service/notification.consumer';
import { NotificationService } from '../../../../src/modules/notification/service/notification.service';
import { Notifications } from '../../../../src/modules/notification/schema/notification.schema';
import * as RecipientResolver from '../../../../src/modules/notification/utils/notification-recipient.resolver';
import { OutboxEvent } from '../../../../src/libs/repositories/outbox/schema/outbox.schema';
import {
  INotificationDeliveryPolicy,
  RetryableDeliveryError,
} from '../../../../src/modules/notification/service/notification-delivery.port';

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
  // This test's redeliveries, by the org id inside the stored event.
  const redeliveries = { orderingKey: /^redeliver:/, value: { $regex: String(orgId) } };

  before(async () => {
    await mongoose.connect(uri as string);
    await Notifications.init();
  });
  after(async () => {
    await Notifications.deleteMany({ orgId });
    await OutboxEvent.deleteMany(redeliveries);
    await mongoose.disconnect();
  });

  beforeEach(async () => {
    await Notifications.deleteMany({ orgId });
    await OutboxEvent.deleteMany(redeliveries);
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

  describe('a primary step-down during delivery (simulated: the shared replica set must stay writable)', () => {
    // What the driver reports once its own retry of the write has also failed.
    const notPrimary = () =>
      new mongoose.mongo.MongoServerError({ message: 'not primary', code: 10107, codeName: 'NotWritablePrimary' });

    it('retries the write after the election, and the event lands once', async () => {
      const insert = sinon.stub(Notifications.collection, 'insertMany');
      insert.onFirstCall().rejects(notPrimary());
      insert.callThrough();

      await deliver(shareEvent('s8'));

      expect(insert.callCount).to.equal(2);
      expect(await Notifications.countDocuments({ orgId })).to.equal(2);
      expect(sendToUser.callCount).to.equal(2);
      expect(dispatch.callCount).to.equal(2);
      expect(await OutboxEvent.countDocuments(redeliveries)).to.equal(0);
    });

    it('re-queues an event that outlasts the retry window, and the redelivery lands once', async () => {
      // Only Date is faked, so the driver's own timers keep running.
      const clock = sinon.useFakeTimers({ now: Date.now(), toFake: ['Date'] });
      const insert = sinon.stub(Notifications.collection, 'insertMany').callsFake(async () => {
        clock.tick(31_000);
        throw notPrimary();
      });

      await deliver(shareEvent('s10'));

      const [row, ...others] = await OutboxEvent.find(redeliveries).lean();
      expect(others).to.have.length(0);
      expect(row).to.include({ topic: 'notification', status: 'pending', attempts: 0 });
      expect(row!.nextAttemptAt.getTime()).to.equal(Date.now() + 2_000);
      expect(JSON.parse(row!.value)).to.deep.equal({ ...JSON.parse(shareEvent('s10')), redelivery: 1 });
      expect(await Notifications.countDocuments({ orgId })).to.equal(0);

      insert.restore();
      clock.restore();
      await deliver(row!.value);
      await deliver(row!.value);

      const docs = await Notifications.find({ orgId }).lean<Record<string, unknown>[]>();
      expect(docs).to.have.length(2);
      expect(docs[0]).to.not.have.property('redelivery');
      expect(sendToUser.callCount).to.equal(2);
    });
  });

  it("stores a policy's readFor rows already read", async () => {
    const broker = { isConnected: () => true, consume: sinon.stub().resolves() };
    const consumer = new NotificationConsumer(
      broker as never,
      { info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(), debug: sinon.stub() } as never,
      sinon.createStubInstance(NotificationService) as unknown as NotificationService,
      { dispatch },
      { plan: async (_event, base) => ({ userIds: base, readFor: new Set([String(userA)]) }) },
    );
    await consumer.consume(async () => undefined);

    await broker.consume.firstCall.args[0]({ value: shareEvent('s11') });

    const status = async (user: Types.ObjectId) =>
      (await Notifications.findOne({ orgId, assignedTo: user }).lean())?.status;
    expect(await status(userA)).to.equal('read');
    expect(await status(userB)).to.equal('unread');
  });

  describe('access that goes while a notice is being stored (the check after the insert)', () => {
    const consumerWith = async (policy: INotificationDeliveryPolicy) => {
      const broker = { isConnected: () => true, consume: sinon.stub().resolves() };
      const sockets = sinon.createStubInstance(NotificationService);
      const consumer = new NotificationConsumer(
        broker as never,
        { info: sinon.stub(), error: sinon.stub(), warn: sinon.stub(), debug: sinon.stub() } as never,
        sockets as unknown as NotificationService,
        { dispatch },
        policy,
      );
      await consumer.consume(async () => undefined);
      return {
        deliver: (value: unknown) => broker.consume.firstCall.args[0]({ value }),
        pushedTo: () => (sockets.sendToUser as unknown as sinon.SinonStub).args.map((a) => a[0]),
      };
    };
    const inbox = async () =>
      (await Notifications.find({ orgId }).lean()).map((n) => String(n.assignedTo));

    it('someone who lost access between the plan and the insert keeps no row and gets no push or email; the others do', async () => {
      // Asked with the rows already stored, as the consumer must, or a change between the plan and the insert is missed.
      let storedWhenAsked: string[] = [];
      const { deliver, pushedTo } = await consumerWith({
        plan: async (_event, base) => ({
          userIds: base,
          stillFor: async (ids) => {
            storedWhenAsked = await inbox();
            expect(ids.map(String)).to.have.members([String(userA), String(userB)]);
            return new Set([String(userA)]);
          },
        }),
      });

      await deliver(shareEvent('s12'));

      expect(storedWhenAsked).to.have.members([String(userA), String(userB)]);
      expect(await inbox()).to.deep.equal([String(userA)]);
      expect(pushedTo()).to.deep.equal([String(userA)]);
      expect(dispatch.args.map((a) => String(a[0].assignedTo))).to.deep.equal([String(userA)]);
    });

    it('a check that cannot tell yet leaves nothing behind, and the retry stores, pushes and emails once', async () => {
      let asked = 0;
      const { deliver, pushedTo } = await consumerWith({
        plan: async (_event, base) => ({
          userIds: base,
          stillFor: async () => {
            asked += 1;
            if (asked === 1) throw new RetryableDeliveryError('membership not readable yet');
            return new Set([String(userA)]);
          },
        }),
      });

      await deliver(shareEvent('s13'));

      expect(asked).to.equal(2);
      expect(await inbox()).to.deep.equal([String(userA)]);
      expect(pushedTo()).to.deep.equal([String(userA)]);
      expect(dispatch.callCount).to.equal(1);
    });

    it('a check that fails for good stores and tells nobody', async () => {
      const { deliver, pushedTo } = await consumerWith({
        plan: async (_event, base) => ({
          userIds: base,
          stillFor: async () => {
            throw new Error('policy bug');
          },
        }),
      });

      await deliver(shareEvent('s14'));

      expect(await inbox()).to.deep.equal([]);
      expect(pushedTo()).to.deep.equal([]);
      expect(dispatch.called).to.equal(false);
    });
  });
});
