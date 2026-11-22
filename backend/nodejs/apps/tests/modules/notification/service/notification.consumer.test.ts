import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { NotificationConsumer, notificationEventRefused } from '../../../../src/modules/notification/service/notification.consumer';
import { NotificationService } from '../../../../src/modules/notification/service/notification.service';
import { setNotificationRepository } from '../../../../src/libs/repositories/notification';
import { getCurrentOrgId } from '../../../../src/libs/context/orgContext';
import { configureInbox, runWithEnvelope } from '../../../../src/libs/context/envelope';
import { MemInbox } from '../../../helpers/memory-inbox';
import { DocStoreFencedError, DocStoreReadOnlyError } from '../../../../src/libs/repositories/errors';
import { GatewayTimeoutError, ServiceUnavailableError } from '../../../../src/libs/errors/http.errors';
import * as NotificationSchema from '../../../../src/modules/notification/schema/notification.schema';
import * as RecipientResolver from '../../../../src/modules/notification/utils/notification-recipient.resolver';

describe('notification/service/notification.consumer', () => {
  let consumer: NotificationConsumer;
  let mockLogger: any;
  let mockConsumer: any;
  let mockNotificationService: sinon.SinonStubbedInstance<NotificationService>;

  beforeEach(() => {
    mockLogger = {
      info: sinon.stub(),
      error: sinon.stub(),
      warn: sinon.stub(),
      debug: sinon.stub(),
    };
    mockConsumer = {
      connect: sinon.stub().resolves(),
      disconnect: sinon.stub().resolves(),
      isConnected: sinon.stub().returns(false),
      subscribe: sinon.stub().resolves(),
      consume: sinon.stub().resolves(),
      pause: sinon.stub(),
      resume: sinon.stub(),
      healthCheck: sinon.stub().resolves(true),
    };
    mockNotificationService = sinon.createStubInstance(NotificationService);
    consumer = new NotificationConsumer(
      mockConsumer,
      mockLogger,
      mockNotificationService as unknown as NotificationService,
    );
  });

  afterEach(() => {
    sinon.restore();
  });

  describe('start', () => {
    it('should call connect if not connected', async () => {
      mockConsumer.isConnected.returns(false);
      await consumer.start();
      expect(mockConsumer.connect.calledOnce).to.be.true;
    });

    it('should not connect if already connected', async () => {
      mockConsumer.isConnected.returns(true);
      await consumer.start();
      expect(mockConsumer.connect.called).to.be.false;
    });
  });

  describe('stop', () => {
    it('should disconnect if connected', async () => {
      mockConsumer.isConnected.returns(true);
      await consumer.stop();
      expect(mockConsumer.disconnect.calledOnce).to.be.true;
    });

    it('should not disconnect if not connected', async () => {
      mockConsumer.isConnected.returns(false);
      await consumer.stop();
      expect(mockConsumer.disconnect.called).to.be.false;
    });
  });

  describe('subscribe', () => {
    it('should subscribe if connected', async () => {
      mockConsumer.isConnected.returns(true);
      await consumer.subscribe(['test-topic'], false);
      expect(mockConsumer.subscribe.calledOnce).to.be.true;
    });

    it('should not subscribe if not connected', async () => {
      mockConsumer.isConnected.returns(false);
      await consumer.subscribe(['test-topic'], false);
      expect(mockConsumer.subscribe.called).to.be.false;
    });

    it('should subscribe with fromBeginning flag', async () => {
      mockConsumer.isConnected.returns(true);
      await consumer.subscribe(['test-topic'], true);
      expect(mockConsumer.subscribe.calledWith(['test-topic'], true)).to.be.true;
    });
  });

  describe('consume', () => {
    it('should not consume if not connected', async () => {
      mockConsumer.isConnected.returns(false);
      const handler = sinon.stub().resolves();
      try {
        await consumer.consume(handler);
        expect.fail('Should have thrown');
      } catch (error: unknown) {
        expect((error as Error).message).to.equal('MessageConsumer is not connected');
      }
      expect(mockConsumer.consume.called).to.be.false;
      expect(mockLogger.error.calledOnce).to.be.true;
    });

    it('should call consumer.consume with wrapped handler if connected', async () => {
      mockConsumer.isConnected.returns(true);
      const handler = sinon.stub().resolves();
      await consumer.consume(handler);
      expect(mockConsumer.consume.calledOnce).to.be.true;
    });

    it('fans out to each recipientUserId and dispatches websocket', async () => {
      mockConsumer.isConnected.returns(true);
      const userHandler = sinon.stub().resolves();
      const orgId = new mongoose.Types.ObjectId().toString();
      const user1 = new mongoose.Types.ObjectId().toString();
      const user2 = new mongoose.Types.ObjectId().toString();
      const resolveStub = sinon
        .stub(RecipientResolver, 'resolveNotificationRecipientUserIds')
        .resolves([
          new mongoose.Types.ObjectId(user1),
          new mongoose.Types.ObjectId(user2),
        ]);
      // create() is called once with an array of docs (batched insert)
      const createStub = sinon.stub(NotificationSchema.Notifications, 'create').resolves([
        {
          _id: 'nid1',
          assignedTo: new mongoose.Types.ObjectId(user1),
          toObject: () => ({ _id: 'nid1', assignedTo: user1 }),
        },
        {
          _id: 'nid2',
          assignedTo: new mongoose.Types.ObjectId(user2),
          toObject: () => ({ _id: 'nid2', assignedTo: user2 }),
        },
      ] as any);

      await consumer.consume(userHandler);
      const wrapped = mockConsumer.consume.firstCall.args[0];
      await wrapped({
        value: {
          orgId,
          type: 'CONNECTOR_SYNC_ERROR',
          recipientUserIds: [user1, user2],
          recipientRoles: [],
        },
      });

      expect(resolveStub.calledOnce).to.be.true;
      expect(createStub.calledOnce).to.be.true;
      expect((mockNotificationService.sendToUser as sinon.SinonStub).callCount).to.equal(2);
      expect(userHandler.calledOnce).to.be.true;
    });

    it('a copy of an event another delivery already stored is acknowledged without a write, a push or an error log', async () => {
      mockConsumer.isConnected.returns(true);
      const inbox = new MemInbox();
      configureInbox(inbox);
      try {
        const user = new mongoose.Types.ObjectId().toString();
        const value = { orgId: new mongoose.Types.ObjectId().toString(), type: 'CONNECTOR_SYNC_ERROR', recipientUserIds: [user], eventId: 'evt-1' };
        sinon.stub(RecipientResolver, 'resolveNotificationRecipientUserIds').resolves([new mongoose.Types.ObjectId(user)]);
        const insertMany = sinon.stub().resolves([{ _id: 'n1', assignedTo: user }]);
        setNotificationRepository({ insertMany } as never);
        // the other delivery recorded the event after this one passed the inbox check
        inbox.seen = async () => false;
        await inbox.record('notification-consumer-group', 'evt-1', value.orgId);
        await consumer.consume(sinon.stub().resolves());
        const wrapped = mockConsumer.consume.firstCall.args[0];
        const out = await runWithEnvelope('notification', value, (v) => wrapped({ value: v }), 'notification-consumer-group');
        expect(out).to.equal(undefined);
        expect(insertMany.called).to.be.false;
        expect((mockNotificationService.sendToUser as sinon.SinonStub).called).to.be.false;
        expect(mockLogger.error.called).to.be.false;
      } finally {
        configureInbox(null);
        setNotificationRepository();
      }
    });

    it('skips persist when event has no valid recipients', async () => {
      mockConsumer.isConnected.returns(true);
      const userHandler = sinon.stub().resolves();
      const createStub = sinon.stub(NotificationSchema.Notifications, 'create');
      sinon.stub(RecipientResolver, 'resolveNotificationRecipientUserIds').resolves([]);

      await consumer.consume(userHandler);
      const wrapped = mockConsumer.consume.firstCall.args[0];
      await wrapped({
        value: {
          orgId: new mongoose.Types.ObjectId().toString(),
          type: 'CONNECTOR_SYNC_ERROR',
          recipientUserIds: [],
          recipientRoles: [],
        },
      });

      expect(createStub.called).to.be.false;
      expect(mockLogger.warn.called).to.be.true;
      expect(userHandler.calledOnce).to.be.true;
    });

    describe('the store', () => {
      const orgId = new mongoose.Types.ObjectId().toString();
      const user = new mongoose.Types.ObjectId().toString();
      const event = { orgId, type: 'CONNECTOR_SYNC_ERROR', recipientUserIds: [user], recipientRoles: [] };
      const run = async (insertMany: sinon.SinonStub, value: unknown = event) => {
        mockConsumer.isConnected.returns(true);
        sinon.stub(RecipientResolver, 'resolveNotificationRecipientUserIds').resolves([new mongoose.Types.ObjectId(user)]);
        setNotificationRepository({ insertMany } as never);
        const handler = sinon.stub().resolves();
        await consumer.consume(handler);
        const wrapped = mockConsumer.consume.firstCall.args[0];
        return { handler, outcome: wrapped({ value }) as Promise<void> };
      };
      afterEach(() => setNotificationRepository());

      it('writes in the org the event names', async () => {
        let seen: string | undefined;
        const insertMany = sinon.stub().callsFake(async () => {
          seen = getCurrentOrgId();
          return [];
        });
        const { handler, outcome } = await run(insertMany);
        await outcome;
        expect(seen).to.equal(orgId);
        expect(handler.calledOnce).to.be.true;
      });

      describe('a supplied _id', () => {
        const other = new mongoose.Types.ObjectId().toString();
        it('refuses an event with an _id and several recipients: logged, counted, nothing written, marked handled', async () => {
          mockConsumer.isConnected.returns(true);
          sinon.stub(RecipientResolver, 'resolveNotificationRecipientUserIds').resolves([new mongoose.Types.ObjectId(user), new mongoose.Types.ObjectId(other)]);
          const insertMany = sinon.stub().resolves([]);
          setNotificationRepository({ insertMany } as never);
          const refused = sinon.spy(notificationEventRefused, 'inc');
          const handler = sinon.stub().resolves();
          await consumer.consume(handler);
          await mockConsumer.consume.firstCall.args[0]({ value: { ...event, _id: 'fixed-id', recipientUserIds: [user, other] } });
          expect(insertMany.called).to.be.false;
          expect(mockNotificationService.sendToUser.called).to.be.false;
          // the producer's fault: one WARNING with the error, never the event's value
          expect(mockLogger.error.called).to.be.false;
          expect(mockLogger.warn.calledOnce).to.be.true;
          expect(mockLogger.warn.firstCall.args[1].error.message).to.equal('Notification event refused');
          expect(mockLogger.warn.firstCall.args[1]).to.not.have.property('messageValue');
          expect(handler.calledOnce).to.be.true;
          expect(refused.calledOnceWithExactly({ reason: 'id_with_many_recipients' })).to.be.true;
        });

        it('keeps the _id for one recipient', async () => {
          const insertMany = sinon.stub().resolves([]);
          const { outcome } = await run(insertMany, { ...event, _id: 'fixed-id' });
          await outcome;
          expect(insertMany.firstCall.args[0].map((d: any) => d._id)).to.deep.equal(['fixed-id']);
        });
      });

      describe('scheduled notifications (visibleAt ahead)', () => {
        const row = (id: string, visibleAt?: unknown) => ({ _id: id, orgId, assignedTo: user, type: 'X', ...(visibleAt === undefined ? {} : { visibleAt }) });
        const pushed = () => (mockNotificationService.sendToUser as sinon.SinonStub).getCalls().map((c) => [c.args[1], c.args[2]._id]);

        it('stores it but emits no newNotification and queues nothing; one that is already visible is pushed as before', async () => {
          const clock = sinon.useFakeTimers({ now: Date.now(), toFake: ['setTimeout', 'setInterval', 'setImmediate', 'Date'] });
          try {
            const insertMany = sinon.stub().callsFake(async () => [
              row('s1', new Date(Date.now() + 60_000)),
              row('s2', new Date(Date.now() + 86_400_000).toISOString()),
              row('v1', new Date(Date.now() - 1000)),
              row('p1'),
            ]);
            const { outcome } = await run(insertMany, { ...event, visibleAt: new Date(Date.now() + 60_000).toISOString() });
            const before = clock.countTimers();
            await outcome;
            expect(insertMany.calledOnce).to.be.true;
            expect(pushed()).to.deep.equal([['newNotification', 'v1'], ['newNotification', 'p1']]);
            // the consumer sets no timer to deliver it later, and nothing is pushed when the time comes
            expect(clock.countTimers()).to.equal(before);
            await clock.tickAsync(2 * 86_400_000);
            expect(pushed()).to.deep.equal([['newNotification', 'v1'], ['newNotification', 'p1']]);
          } finally {
            clock.restore();
          }
        });

        it('hands the store the visibleAt and createdAt the event carries (the store casts them to dates)', async () => {
          const at = new Date(Date.now() + 60_000).toISOString();
          const insertMany = sinon.stub().resolves([]);
          const { outcome } = await run(insertMany, { ...event, visibleAt: at, createdAt: at });
          await outcome;
          const [doc] = insertMany.firstCall.args[0];
          expect(doc.visibleAt).to.equal(at);
          expect(doc.createdAt).to.equal(at);
          expect(doc).to.not.have.property('recipientUserIds');
        });
      });

      for (const err of [new DocStoreReadOnlyError(), new DocStoreFencedError(), new ServiceUnavailableError('down'), new GatewayTimeoutError('slow')]) {
        it(`re-throws ${err.constructor.name} and does not mark the message handled`, async () => {
          const { handler, outcome } = await run(sinon.stub().rejects(err));
          let thrown: unknown;
          try {
            await outcome;
          } catch (e) {
            thrown = e;
          }
          expect(thrown).to.equal(err);
          expect(handler.called).to.be.false;
        });
      }

      it('logs any other failure and still marks the message handled', async () => {
        const { handler, outcome } = await run(sinon.stub().rejects(new Error('constraint')));
        await outcome;
        expect(mockLogger.error.calledOnce).to.be.true;
        expect(handler.calledOnce).to.be.true;
      });

      it('skips an event without a valid org, and marks it handled', async () => {
        const insertMany = sinon.stub();
        const { handler, outcome } = await run(insertMany, { orgId: 'nope', type: 'X' });
        await outcome;
        expect(insertMany.called).to.be.false;
        expect(mockLogger.warn.called).to.be.true;
        expect(handler.calledOnce).to.be.true;
      });
    });
  });
});
