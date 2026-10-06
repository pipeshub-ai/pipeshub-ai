import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import mongoose from 'mongoose';
import { NotificationConsumer } from '../../../../src/modules/notification/service/notification.consumer';
import { NotificationService } from '../../../../src/modules/notification/service/notification.service';
import * as NotificationSchema from '../../../../src/modules/notification/schema/notification.schema';
import * as RecipientResolver from '../../../../src/modules/notification/utils/notification-recipient.resolver';

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
      expect(createStub.firstCall.args[1]).to.deep.equal({ ordered: false });
      expect(mockDispatcher.dispatch.called).to.be.false;
      expect((mockNotificationService.sendToUser as sinon.SinonStub).callCount).to.equal(2);
      expect(userHandler.calledOnce).to.be.true;
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
  });
  describe('outbox events (JSON string, dedupeKey, coalesceKey, emailIntent)', () => {
    const orgId = new mongoose.Types.ObjectId().toString();
    const userA = new mongoose.Types.ObjectId();
    const userB = new mongoose.Types.ObjectId();
    const emailIntent = {
      template: 'chatShared',
      actorName: 'Ada',
      orgName: 'Acme',
      accessLevel: 'write',
    };
    const saved = (user: mongoose.Types.ObjectId, extra: Record<string, unknown> = {}) => ({
      _id: new mongoose.Types.ObjectId(),
      orgId: new mongoose.Types.ObjectId(orgId),
      assignedTo: user,
      dedupeKey: `chat.shared:s1:${String(user)}`,
      redirectLink: '/chat?conversationId=s1',
      toObject() {
        return { _id: this._id, assignedTo: user };
      },
      ...extra,
    });
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
        writeErrors: codes.map((code) => ({ code })),
        insertedDocs,
      });

    let run: (value: unknown) => Promise<void>;
    let insertManyStub: sinon.SinonStub;
    let findOneAndUpdateStub: sinon.SinonStub;
    const sendToUser = () => mockNotificationService.sendToUser as sinon.SinonStub;

    beforeEach(async () => {
      mockConsumer.isConnected.returns(true);
      sinon
        .stub(RecipientResolver, 'resolveNotificationRecipientUserIds')
        .callsFake(async (_org, ids) => (ids ?? []).map((id) => new mongoose.Types.ObjectId(id)));
      insertManyStub = sinon.stub(NotificationSchema.Notifications, 'insertMany');
      findOneAndUpdateStub = sinon.stub(NotificationSchema.Notifications, 'findOneAndUpdate');
      await consumer.consume(sinon.stub().resolves());
      const wrapped = mockConsumer.consume.firstCall.args[0];
      run = (value) => wrapped({ value });
    });

    it('accepts the event as a JSON string and stores coalesceKey but not emailIntent', async () => {
      insertManyStub.resolves([saved(userA), saved(userB)]);
      await run(JSON.stringify(event({ emailIntent, coalesceKey: undefined })));
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

    it('logs identifiers, never the share note, for an invalid or failed event', async () => {
      const note = 'SECRET-NOTE-for-bob'
      await run(JSON.stringify({ orgId: 'nope', type: 'chat.shared', dedupeKey: 'k1', payload: { note } }))
      expect(mockLogger.warn.firstCall.args[1]).to.deep.equal({ type: 'chat.shared', dedupeKey: 'k1', orgId: 'nope', invalidPaths: ['orgId'] })
      insertManyStub.rejects(new Error('connection lost'))
      await run(JSON.stringify(event({ type: 'chat.shared', dedupeKey: 'k2', payload: { note } })))
      expect(mockLogger.error.firstCall.args[1]).to.include({ error: 'connection lost', type: 'chat.shared', dedupeKey: 'k2' })
      expect(JSON.stringify([mockLogger.warn.args, mockLogger.error.args])).to.not.include(note)
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
        assignedTo: userB,
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

    describe('coalesceKey', () => {
      const coalesced = (extra: Record<string, unknown> = {}) =>
        event({
          type: 'chat.activity',
          recipientUserIds: [String(userA)],
          coalesceKey: 'chat.activity:s1',
          message: 'Bob replied',
          payload: { sessionId: 's1', count: 1 },
          dedupeKey: undefined,
          ...extra,
        });

      it('upserts one unread doc per recipient and increments payload.count', async () => {
        findOneAndUpdateStub.resolves({
          value: saved(userA),
          lastErrorObject: { updatedExisting: true },
        });
        await run(coalesced({ payload: { sessionId: 's1', count: 3 } }));
        expect(insertManyStub.called).to.be.false;
        const [filter, update, options] = findOneAndUpdateStub.firstCall.args;
        expect(filter).to.deep.equal({
          assignedTo: userA,
          coalesceKey: 'chat.activity:s1',
          status: 'unread',
        });
        expect(update.$inc).to.deep.equal({ 'payload.count': 3 });
        expect(update.$set.message).to.equal('Bob replied');
        expect(update.$set.updatedAt).to.be.instanceOf(Date);
        expect(update.$setOnInsert).to.include({
          type: 'chat.activity',
          'payload.sessionId': 's1',
          isDeleted: false,
        });
        expect(update.$setOnInsert).to.not.have.any.keys('payload', 'message', 'status', 'assignedTo', 'coalesceKey');
        expect(options).to.include({ upsert: true });
      });

      it('pushes an update to a coalesced doc and never emails', async () => {
        findOneAndUpdateStub.resolves({
          value: saved(userA),
          lastErrorObject: { updatedExisting: true },
        });
        await run(coalesced({ emailIntent }));
        expect(sendToUser().firstCall.args[1]).to.equal('notificationUpdated');
        expect(mockDispatcher.dispatch.called).to.be.false;
      });

      it('treats an upserted doc as new: push newNotification', async () => {
        findOneAndUpdateStub.resolves({
          value: saved(userA),
          lastErrorObject: { updatedExisting: false },
        });
        await run(coalesced());
        expect(sendToUser().firstCall.args[1]).to.equal('newNotification');
      });

      it('retries once as an update after losing the upsert race (E11000)', async () => {
        findOneAndUpdateStub
          .onFirstCall()
          .rejects(Object.assign(new Error('E11000'), { code: 11000 }))
          .onSecondCall()
          .resolves({ value: saved(userA), lastErrorObject: { updatedExisting: true } });
        await run(coalesced());
        expect(findOneAndUpdateStub.callCount).to.equal(2);
        expect(sendToUser().firstCall.args[1]).to.equal('notificationUpdated');
      });

      it('gives up after one retry and keeps delivering to other recipients', async () => {
        findOneAndUpdateStub.callsFake(async (filter: { assignedTo: mongoose.Types.ObjectId }) => {
          if (filter.assignedTo.equals(userA)) {
            throw Object.assign(new Error('E11000'), { code: 11000 });
          }
          return { value: saved(userB), lastErrorObject: { updatedExisting: false } };
        });
        await run(coalesced({ recipientUserIds: [String(userA), String(userB)] }));
        expect(findOneAndUpdateStub.callCount).to.equal(3);
        expect(mockLogger.error.calledOnce).to.be.true;
        expect(sendToUser().callCount).to.equal(1);
        expect(sendToUser().firstCall.args[0]).to.equal(String(userB));
      });

      it('does not retry a failure that is not a duplicate key', async () => {
        findOneAndUpdateStub.rejects(new Error('boom'));
        await run(coalesced());
        expect(findOneAndUpdateStub.callCount).to.equal(1);
        expect(mockLogger.error.calledOnce).to.be.true;
      });

      it('increments by one when the event carries no usable count', async () => {
        findOneAndUpdateStub.resolves({ value: saved(userA), lastErrorObject: { updatedExisting: true } });
        await run(coalesced({ payload: { sessionId: 's1' } }));
        expect(findOneAndUpdateStub.firstCall.args[1].$inc).to.deep.equal({ 'payload.count': 1 });
      });
    });
  });
});
