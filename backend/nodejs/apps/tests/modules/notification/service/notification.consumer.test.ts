import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { NotificationConsumer, notificationEventRefused } from '../../../../src/modules/notification/service/notification.consumer';
import { NotificationService } from '../../../../src/modules/notification/service/notification.service';
import { getCurrentOrgId } from '../../../../src/libs/context/orgContext';
import { configureInbox, runWithEnvelope } from '../../../../src/libs/context/envelope';
import { MemInbox } from '../../../helpers/memory-inbox';
import { DocStoreFencedError, DocStoreReadOnlyError } from '../../../../src/libs/repositories/errors';
import { GatewayTimeoutError, ServiceUnavailableError } from '../../../../src/libs/errors/http.errors';
import * as NotificationSchema from '../../../../src/modules/notification/schema/notification.schema';
import * as RecipientResolver from '../../../../src/modules/notification/utils/notification-recipient.resolver';
import * as PayloadResolver from '../../../../src/modules/notification/utils/notification-payload.resolver';
import {
  RetryableDeliveryError,
  deliverToRecipients,
} from '../../../../src/modules/notification/service/notification-delivery.port';
import { OutboxEvent } from '../../../../src/libs/repositories/outbox/schema/outbox.schema';

describe('notification/service/notification.consumer', () => {
  let consumer: NotificationConsumer;
  let mockLogger: any;
  let mockConsumer: any;
  let mockNotificationService: sinon.SinonStubbedInstance<NotificationService>;
  let mockDispatcher: { dispatch: sinon.SinonStub };

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
    mockDispatcher = { dispatch: sinon.stub().resolves() };
    consumer = new NotificationConsumer(
      mockConsumer,
      mockLogger,
      mockNotificationService as unknown as NotificationService,
      mockDispatcher,
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
      const createStub = sinon.stub(NotificationSchema.Notifications, 'insertMany').resolves([
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
      // the store stamps both times itself, so a scheduled row's updatedAt is its visibleAt
      expect(createStub.firstCall.args[1]).to.deep.equal({ ordered: false, timestamps: false });
      expect(mockDispatcher.dispatch.called).to.be.false;
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
      const createStub = sinon.stub(NotificationSchema.Notifications, 'insertMany');
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

    describe('an event for the installation admins', () => {
      const platformEvent = { audience: 'platformAdmins', type: 'SANDBOX_CAPACITY_WARNING', payload: { v: 1, alertId: 'a1' } };

      it('goes to the platform alert handler and not through the org path', async () => {
        mockConsumer.isConnected.returns(true);
        const platformHandler = { handle: sinon.stub().resolves() };
        const toBroker = sinon.spy(PayloadResolver, 'toBrokerMessage');
        consumer = new NotificationConsumer(
          mockConsumer,
          mockLogger,
          mockNotificationService as unknown as NotificationService,
          platformHandler as never,
        );
        const handler = sinon.stub().resolves();
        await consumer.consume(handler);
        await mockConsumer.consume.firstCall.args[0]({ value: platformEvent });

        expect(platformHandler.handle.calledOnceWith(platformEvent)).to.be.true;
        expect(toBroker.called).to.be.false;
        expect(handler.calledOnce).to.be.true;
      });

      it('is acknowledged when the handler delivers nothing or fails for good', async () => {
        mockConsumer.isConnected.returns(true);
        const platformHandler = { handle: sinon.stub().rejects(new Error('bad')) };
        consumer = new NotificationConsumer(
          mockConsumer,
          mockLogger,
          mockNotificationService as unknown as NotificationService,
          platformHandler as never,
        );
        const handler = sinon.stub().resolves();
        await consumer.consume(handler);
        await mockConsumer.consume.firstCall.args[0]({ value: platformEvent });

        expect(handler.calledOnce).to.be.true;
        expect(mockLogger.error.calledOnce).to.be.true;
      });

      it('is delivered again after a retryable store error', async () => {
        mockConsumer.isConnected.returns(true);
        const err = new DocStoreReadOnlyError();
        const platformHandler = { handle: sinon.stub().rejects(err) };
        consumer = new NotificationConsumer(
          mockConsumer,
          mockLogger,
          mockNotificationService as unknown as NotificationService,
          platformHandler as never,
        );
        const handler = sinon.stub().resolves();
        await consumer.consume(handler);
        let thrown: unknown;
        try {
          await mockConsumer.consume.firstCall.args[0]({ value: platformEvent });
        } catch (e) {
          thrown = e;
        }
        expect(thrown).to.equal(err);
        expect(handler.called).to.be.false;
      });

      it('is acknowledged with a warning when no handler is configured', async () => {
        mockConsumer.isConnected.returns(true);
        const handler = sinon.stub().resolves();
        await consumer.consume(handler);
        await mockConsumer.consume.firstCall.args[0]({ value: platformEvent });

        expect(mockLogger.warn.calledWithMatch('Platform alert skipped')).to.be.true;
        expect(handler.calledOnce).to.be.true;
      });
    });

    describe('the store', () => {
      const orgId = new mongoose.Types.ObjectId().toString();
      const user = new mongoose.Types.ObjectId().toString();
      const event = { orgId, type: 'CONNECTOR_SYNC_ERROR', recipientUserIds: [user], recipientRoles: [] };
      const run = async (insertMany: sinon.SinonStub, value: unknown = event) => {
        mockConsumer.isConnected.returns(true);
        sinon.stub(RecipientResolver, 'resolveNotificationRecipientUserIds').resolves([new mongoose.Types.ObjectId(user)]);
        sinon.stub(NotificationSchema.Notifications, 'insertMany').callsFake(insertMany as never);
        const handler = sinon.stub().resolves();
        await consumer.consume(handler);
        const wrapped = mockConsumer.consume.firstCall.args[0];
        return { handler, outcome: wrapped({ value }) as Promise<void> };
      };

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
          const insertMany = sinon.stub(NotificationSchema.Notifications, 'insertMany').resolves([]);
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
          const fixed = new mongoose.Types.ObjectId().toString();
          const { outcome } = await run(insertMany, { ...event, _id: fixed });
          await outcome;
          expect(insertMany.firstCall.args[0].map((d: any) => String(d._id))).to.deep.equal([fixed]);
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
          expect(doc.visibleAt).to.deep.equal(new Date(at));
          expect(doc.createdAt).to.deep.equal(new Date(at));
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
  describe('outbox events (JSON string, dedupeKey, emailIntent)', () => {
    const orgId = new mongoose.Types.ObjectId().toString();
    const userA = new mongoose.Types.ObjectId();
    const userB = new mongoose.Types.ObjectId();
    const emailIntent = {
      template: 'chatShared',
      actorName: 'Ada',
      orgName: 'Acme',
      accessLevel: 'write',
    };
    // A stored document as Mongoose hands it back: ids as ObjectIds, read through `toObject()`.
    const saved = (user: mongoose.Types.ObjectId, extra: Record<string, unknown> = {}) => {
      const stored = {
        _id: new mongoose.Types.ObjectId(),
        orgId: new mongoose.Types.ObjectId(orgId),
        assignedTo: user,
        dedupeKey: `chat.shared:s1:${String(user)}`,
        redirectLink: '/chat?conversationId=s1',
        ...extra,
      };
      return { ...stored, toObject: () => stored };
    };
    const event = (extra: Record<string, unknown> = {}) => ({
      orgId,
      type: 'chat.shared',
      recipientUserIds: [String(userA), String(userB)],
      redirectLink: '/chat?conversationId=s1',
      dedupeKey: 'k',
      ...extra,
    });
    const duplicateFailure = (insertedDocs: unknown[], codes: number[] = [11000]) =>
      Object.assign(new Error('E11000 duplicate key'), {
        code: codes[0],
        // As Mongoose 8 hands them over: plain copies with the code only under `err`.
        writeErrors: codes.map((code, index) => ({ err: { index, code, errmsg: `E${code}` } })),
        insertedDocs,
      });

    let run: (value: unknown) => Promise<void>;
    let insertManyStub: sinon.SinonStub;
    const sendToUser = () => mockNotificationService.sendToUser as sinon.SinonStub;

    beforeEach(async () => {
      mockConsumer.isConnected.returns(true);
      sinon
        .stub(RecipientResolver, 'resolveNotificationRecipientUserIds')
        .callsFake(async (_org, ids) => (ids ?? []).map((id) => new mongoose.Types.ObjectId(id)));
      insertManyStub = sinon.stub(NotificationSchema.Notifications, 'insertMany');
      await consumer.consume(sinon.stub().resolves());
      const wrapped = mockConsumer.consume.firstCall.args[0];
      run = (value) => wrapped({ value });
    });

    it('accepts the event as a JSON string and stores dedupeKey but not emailIntent', async () => {
      insertManyStub.resolves([saved(userA), saved(userB)]);
      await run(JSON.stringify(event({ emailIntent })));
      const docs = insertManyStub.firstCall.args[0] as Record<string, unknown>[];
      expect(docs).to.have.length(2);
      expect(docs[0]).to.not.have.property('emailIntent');
      expect(docs[0]).to.not.have.property('recipientUserIds');
      expect(docs[0]).to.include({ dedupeKey: 'k', status: 'unread' });
      expect(sendToUser().callCount).to.equal(2);
    });

    it('skips an unparsable string without touching the store', async () => {
      await run('{not json');
      expect(insertManyStub.called).to.be.false;
      expect(mockLogger.warn.called).to.be.true;
    });

    it('logs identifiers, never user-written text, for an invalid or failed event', async () => {
      const note = 'SECRET-NOTE-for-bob'
      await run(JSON.stringify({ orgId: 'nope', type: 'chat.shared', dedupeKey: 'k1', payload: { note } }))
      expect(mockLogger.warn.firstCall.args[1]).to.deep.equal({ type: 'chat.shared', dedupeKey: 'k1', orgId: 'nope', invalidPaths: ['orgId'] })
      insertManyStub.rejects(new Error('connection lost'))
      await run(JSON.stringify(event({ type: 'chat.shared', dedupeKey: 'k2', payload: { note } })))
      expect(mockLogger.error.firstCall.args[1]).to.include({ error: 'connection lost', type: 'chat.shared', dedupeKey: 'k2' })
      expect(JSON.stringify([mockLogger.warn.args, mockLogger.error.args])).to.not.include(note)
    });

    it('a chat notice scheduled for later is stored, but neither pushed nor emailed before its time; a due one is both', async () => {
      const later = new Date(Date.now() + 3_600_000);
      insertManyStub.resolves([
        saved(userA, { visibleAt: later }),
        saved(userB, { visibleAt: new Date(Date.now() - 1000) }),
      ]);
      await run(JSON.stringify(event({ emailIntent, visibleAt: later.toISOString() })));
      expect(insertManyStub.calledOnce).to.be.true;
      expect(sendToUser().callCount).to.equal(1);
      expect(sendToUser().firstCall.args[0]).to.equal(String(userB));
      expect(mockDispatcher.dispatch.callCount).to.equal(1);
      expect(mockDispatcher.dispatch.firstCall.args[0]).to.deep.include({ assignedTo: String(userB) });
    });

    it('pushes and emails nothing for a duplicate delivery (E11000 on every doc)', async () => {
      insertManyStub.rejects(duplicateFailure([], [11000, 11000]));
      await run(JSON.stringify(event({ emailIntent })));
      expect(sendToUser().called).to.be.false;
      expect(mockDispatcher.dispatch.called).to.be.false;
      expect(mockLogger.error.called).to.be.false;
    });

    it('pushes and emails only the docs that were newly inserted', async () => {
      const inserted = saved(userB);
      insertManyStub.rejects(duplicateFailure([inserted]));
      await run(JSON.stringify(event({ emailIntent })));
      expect(sendToUser().callCount).to.equal(1);
      expect(sendToUser().firstCall.args[0]).to.equal(String(userB));
      expect(sendToUser().firstCall.args[1]).to.equal('newNotification');
      expect(mockDispatcher.dispatch.callCount).to.equal(1);
      expect(mockDispatcher.dispatch.firstCall.args[0]).to.deep.include({
        assignedTo: String(userB),
        dedupeKey: inserted.dedupeKey,
        redirectLink: '/chat?conversationId=s1',
        emailIntent,
      });
    });

    it('logs write errors that are not duplicates but still delivers the inserted docs', async () => {
      insertManyStub.rejects(duplicateFailure([saved(userA)], [11000, 121]));
      await run(event());
      expect(mockLogger.error.calledOnce).to.be.true;
      expect(sendToUser().callCount).to.equal(1);
    });

    it('logs each schema-invalid doc it drops, without who it was for, and inserts nothing', async () => {
      await run(event({ severity: 'catastrophic', title: 'Quarterly numbers' }));
      expect(insertManyStub.called).to.be.false;
      expect(mockLogger.warn.callCount).to.equal(2);
      const [message, meta] = mockLogger.warn.firstCall.args;
      expect(message).to.match(/dropped/);
      expect(meta).to.deep.equal({ type: 'chat.shared', dedupeKey: 'k', invalidPaths: ['severity'] });
      expect(JSON.stringify(mockLogger.warn.args)).to.not.include(String(userA));
      expect(sendToUser().called).to.be.false;
    });

    it('does not email when the event has no emailIntent', async () => {
      insertManyStub.resolves([saved(userA), saved(userB)]);
      await run(event());
      expect(mockDispatcher.dispatch.called).to.be.false;
    });

    it('lets a failing store reach the generic error log and still acks the message', async () => {
      insertManyStub.rejects(new Error('connection lost'));
      const handler = sinon.stub().resolves();
      mockConsumer.consume.resetHistory();
      await consumer.consume(handler);
      await mockConsumer.consume.firstCall.args[0]({ value: event() });
      expect(mockLogger.error.calledOnce).to.be.true;
      expect(sendToUser().called).to.be.false;
      expect(handler.calledOnce).to.be.true;
    });
  });

  describe('delivery policy and redelivery', () => {
    const orgId = new mongoose.Types.ObjectId().toString();
    const userA = new mongoose.Types.ObjectId();
    const userB = new mongoose.Types.ObjectId();
    const event = (extra: Record<string, unknown> = {}) => ({
      orgId,
      type: 'CONNECTOR_SYNC_ERROR',
      recipientUserIds: [String(userA), String(userB)],
      dedupeKey: 'k',
      payload: { note: 'SECRET-NOTE' },
      ...extra,
    });
    // How Mongoose 8 hands over an `ordered: false` write that failed as a whole (an election): a bulk error with an
    // empty `writeErrors`, and `insertedDocs` listing every doc although none was stored (checked on a live replica set).
    const notPrimary = () =>
      Object.assign(
        new mongoose.mongo.MongoServerError({ message: 'not primary', code: 10107, codeName: 'NotWritablePrimary' }),
        { writeErrors: [], insertedDocs: [{ assignedTo: userA }, { assignedTo: userB }] },
      );

    let policy: { plan: sinon.SinonStub };
    let insertManyStub: sinon.SinonStub;
    let outboxCreate: sinon.SinonStub;
    let handler: sinon.SinonStub;
    let clock: sinon.SinonFakeTimers | undefined;
    let run: (value: unknown, key?: string) => Promise<void>;
    const sendToUser = () => mockNotificationService.sendToUser as sinon.SinonStub;
    // Runs one message through the whole retry window, on fake time.
    const runPastRetryWindow = async (value: unknown, key?: string) => {
      clock = sinon.useFakeTimers({ now: new Date('2026-10-16T10:00:00Z') });
      const done = run(value, key);
      await clock.tickAsync(30_000);
      await done;
    };

    beforeEach(async () => {
      mockConsumer.isConnected.returns(true);
      sinon
        .stub(RecipientResolver, 'resolveNotificationRecipientUserIds')
        .callsFake(async (_org, ids) => (ids ?? []).map((id) => new mongoose.Types.ObjectId(id)));
      insertManyStub = sinon
        .stub(NotificationSchema.Notifications, 'insertMany')
        .callsFake(async (docs: unknown) =>
          (docs as { assignedTo: string }[]).map((doc) => {
            // Mongoose casts the recipient id to an ObjectId on insert.
            const stored = { _id: new mongoose.Types.ObjectId(), assignedTo: new mongoose.Types.ObjectId(doc.assignedTo) };
            return { ...stored, toObject: () => stored };
          }) as never,
        );
      outboxCreate = sinon.stub(OutboxEvent, 'create').resolves();
      policy = { plan: sinon.stub().callsFake(async (_event, base) => ({ userIds: base })) };
      consumer = new NotificationConsumer(
        mockConsumer,
        mockLogger,
        mockNotificationService as unknown as NotificationService,
        mockDispatcher,
        policy,
      );
      handler = sinon.stub().resolves();
      await consumer.consume(handler);
      const wrapped = mockConsumer.consume.firstCall.args[0];
      run = (value, key = 'k1') => wrapped({ key, value });
    });

    afterEach(() => {
      clock?.restore();
      clock = undefined;
    });

    it('the default policy delivers to the listed recipients', async () => {
      expect(await deliverToRecipients.plan(event() as never, [userA])).to.deep.equal({ userIds: [userA] });
    });

    it('asks the policy before the empty-recipients check and delivers to whom it names', async () => {
      policy.plan.resolves({ userIds: [userB] });
      await run(event({ recipientUserIds: [] }));
      expect(policy.plan.firstCall.args[1]).to.deep.equal([]);
      const docs = insertManyStub.firstCall.args[0] as { assignedTo: mongoose.Types.ObjectId }[];
      expect(docs.map((doc) => String(doc.assignedTo))).to.deep.equal([String(userB)]);
    });

    it('skips the event when the policy names nobody', async () => {
      policy.plan.resolves({ userIds: [] });
      await run(event());
      expect(insertManyStub.called).to.be.false;
      expect(mockLogger.warn.calledWith('Notification event skipped: no recipients')).to.be.true;
    });

    it('creates the rows of readFor users already read', async () => {
      policy.plan.callsFake(async (_event, base) => ({ userIds: base, readFor: new Set([String(userA)]) }));
      await run(event());
      const docs = insertManyStub.firstCall.args[0] as { status: string }[];
      expect(docs.map((doc) => doc.status)).to.deep.equal(['read', 'unread']);
    });

    it('puts pushExtras in the live push only, never in the stored row', async () => {
      const context = { excerpt: 'see the plan' };
      policy.plan.callsFake(async (_event, base) => ({ userIds: base, pushExtras: async () => ({ context }) }));
      await run(event());
      expect(insertManyStub.firstCall.args[0][0]).to.not.have.property('context');
      const [{ _id }] = (await insertManyStub.firstCall.returnValue) as [{ _id: mongoose.Types.ObjectId }];
      expect(sendToUser().firstCall.args[2]).to.deep.equal({ _id: String(_id), assignedTo: String(userA), context });
    });

    it('keeps broker-only fields off the stored rows', async () => {
      await run(event({ audience: { teamIds: ['t1'] }, redelivery: 2 }));
      expect(insertManyStub.firstCall.args[0][0]).to.not.have.any.keys('audience', 'redelivery');
    });

    it('retries a write that failed as a whole, and a buffered one that timed out, until it lands', async () => {
      clock = sinon.useFakeTimers();
      insertManyStub.onFirstCall().rejects(new mongoose.Error('Operation `notifications.insertMany()` buffering timed out'));
      insertManyStub.onSecondCall().rejects(notPrimary());
      const done = run(event());
      await clock.tickAsync(3_000);
      await done;
      expect(insertManyStub.callCount).to.equal(3);
      expect(sendToUser().callCount).to.equal(2);
      expect(outboxCreate.called).to.be.false;
      expect(mockLogger.error.called).to.be.false;
    });

    it('after about 30 s puts the original back on the outbox under a key of its own, then acknowledges', async () => {
      insertManyStub.rejects(notPrimary());
      let appendedIn: string | undefined;
      outboxCreate.callsFake(async () => {
        appendedIn = getCurrentOrgId();
      });
      await runPastRetryWindow(JSON.stringify(event()));
      expect(insertManyStub.callCount).to.equal(6);
      expect(outboxCreate.calledOnce).to.be.true;
      // a PostgreSQL outbox appends only inside an organisation
      expect(appendedIn).to.equal(orgId);
      const [row] = outboxCreate.firstCall.args[0];
      expect(row).to.include({ topic: 'notification', key: 'k1' });
      expect(row.orderingKey).to.match(/^redeliver:[0-9a-f-]{36}$/);
      expect(JSON.parse(row.value)).to.deep.equal({ ...event(), redelivery: 1 });
      expect(row.nextAttemptAt.getTime()).to.equal(Date.now() + 2_000);
      expect(handler.calledOnce).to.be.true;
      expect(mockLogger.warn.calledWith('Notification re-queued after a transient failure')).to.be.true;
      expect(JSON.stringify(mockLogger.warn.args)).to.not.include('SECRET-NOTE');
    });

    it('backs off longer on each redelivery, and keys an unkeyed message by its type', async () => {
      insertManyStub.rejects(notPrimary());
      await runPastRetryWindow(event({ redelivery: 3 }), '');
      const [row] = outboxCreate.firstCall.args[0];
      expect(row.key).to.equal('CONNECTOR_SYNC_ERROR');
      expect(JSON.parse(row.value).redelivery).to.equal(4);
      expect(row.nextAttemptAt.getTime()).to.equal(Date.now() + 16_000);
    });

    it('gives up after 8 redeliveries, logging ids only', async () => {
      insertManyStub.rejects(notPrimary());
      await runPastRetryWindow(event({ redelivery: 8 }));
      expect(outboxCreate.called).to.be.false;
      expect(mockLogger.error.calledOnce).to.be.true;
      const [message, meta] = mockLogger.error.firstCall.args;
      expect(message).to.equal('Notification dropped after retries');
      expect(meta).to.include({ type: 'CONNECTOR_SYNC_ERROR', dedupeKey: 'k', orgId, redelivery: 8 });
      expect(JSON.stringify(mockLogger.error.args)).to.not.include('SECRET-NOTE');
      expect(handler.calledOnce).to.be.true;
    });

    it('retries a policy failure that may pass, then re-queues it', async () => {
      policy.plan.rejects(new RetryableDeliveryError('teams unavailable'));
      await runPastRetryWindow(event());
      expect(policy.plan.callCount).to.equal(6);
      expect(outboxCreate.calledOnce).to.be.true;
    });

    it('does not retry an error a retry would not fix', async () => {
      insertManyStub.rejects(new TypeError('boom'));
      await run(event());
      expect(insertManyStub.calledOnce).to.be.true;
      expect(outboxCreate.called).to.be.false;
      expect(mockLogger.error.firstCall.args[0]).to.equal('Failed to process notification message');
    });

    it('throws when the re-queue itself fails, so the broker keeps the message', async () => {
      insertManyStub.rejects(notPrimary());
      outboxCreate.rejects(new Error('outbox down'));
      clock = sinon.useFakeTimers();
      const settled = run(event()).then(
        () => 'acknowledged',
        (error: Error) => error.message,
      );
      await clock.tickAsync(30_000);
      expect(await settled).to.equal('outbox down');
      expect(handler.calledOnce).to.be.true;
    });
  });
});
