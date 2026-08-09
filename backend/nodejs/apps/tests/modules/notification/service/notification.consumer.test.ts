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
  });

  describe('workflow run notifications', () => {
    const orgId = new mongoose.Types.ObjectId().toString();
    const userId = new mongoose.Types.ObjectId().toString();

    const workflowRunEvent = (payload: Record<string, unknown>) => ({
      value: {
        orgId,
        type: 'WORKFLOW_RUN_SUCCEEDED',
        recipientUserIds: [userId],
        recipientRoles: [],
        payload,
      },
    });

    const stubRecipients = () =>
      sinon
        .stub(RecipientResolver, 'resolveNotificationRecipientUserIds')
        .resolves([new mongoose.Types.ObjectId(userId)]);

    const deliver = async (event: unknown) => {
      mockConsumer.isConnected.returns(true);
      const userHandler = sinon.stub().resolves();
      await consumer.consume(userHandler);
      await mockConsumer.consume.firstCall.args[0](event);
    };

    it('upserts on (type, runId, assignedTo) so a Kafka redelivery cannot duplicate the row', async () => {
      stubRecipients();
      const createStub = sinon.stub(NotificationSchema.Notifications, 'create');
      const upsertStub = sinon
        .stub(NotificationSchema.Notifications, 'findOneAndUpdate')
        .returns({
          exec: sinon.stub().resolves({
            _id: 'nid1',
            assignedTo: new mongoose.Types.ObjectId(userId),
            toObject: () => ({ _id: 'nid1' }),
          }),
        } as any);

      await deliver(
        workflowRunEvent({ runId: 'run-1', workflowId: 'wf-1', status: 'succeeded' }),
      );

      expect(createStub.called).to.be.false;
      expect(upsertStub.calledOnce).to.be.true;
      const [filter, , options] = upsertStub.firstCall.args as any[];
      expect(filter.type).to.equal('WORKFLOW_RUN_SUCCEEDED');
      expect(filter['payload.runId']).to.equal('run-1');
      expect(String(filter.assignedTo)).to.equal(userId);
      expect(options.upsert).to.be.true;
    });

    it('emits the live run update alongside the persisted notification', async () => {
      stubRecipients();
      sinon.stub(NotificationSchema.Notifications, 'findOneAndUpdate').returns({
        exec: sinon.stub().resolves({
          _id: 'nid1',
          assignedTo: new mongoose.Types.ObjectId(userId),
          toObject: () => ({ _id: 'nid1' }),
        }),
      } as any);

      await deliver(
        workflowRunEvent({ runId: 'run-1', workflowId: 'wf-1', status: 'succeeded' }),
      );

      const emit = mockNotificationService.emitWorkflowRunUpdate as sinon.SinonStub;
      expect(emit.calledOnce).to.be.true;
      expect(emit.firstCall.args[0]).to.include({
        workflowId: 'wf-1',
        runId: 'run-1',
        status: 'succeeded',
      });
    });

    it('a dry run updates the chat card live but writes nothing to the inbox', async () => {
      stubRecipients();
      const createStub = sinon.stub(NotificationSchema.Notifications, 'create');
      const upsertStub = sinon.stub(NotificationSchema.Notifications, 'findOneAndUpdate');

      await deliver(
        workflowRunEvent({
          runId: 'run-dry',
          workflowId: 'wf-1',
          status: 'succeeded',
          isDryRun: true,
        }),
      );

      expect(createStub.called).to.be.false;
      expect(upsertStub.called).to.be.false;
      expect((mockNotificationService.sendToUser as sinon.SinonStub).called).to.be.false;
      const emit = mockNotificationService.emitWorkflowRunUpdate as sinon.SinonStub;
      expect(emit.calledOnce).to.be.true;
      expect(emit.firstCall.args[0].runId).to.equal('run-dry');
    });

    it('falls back to the notification type when the payload carries no status', async () => {
      stubRecipients();
      sinon.stub(NotificationSchema.Notifications, 'findOneAndUpdate').returns({
        exec: sinon.stub().resolves({
          _id: 'nid1',
          assignedTo: new mongoose.Types.ObjectId(userId),
          toObject: () => ({ _id: 'nid1' }),
        }),
      } as any);

      await deliver(workflowRunEvent({ runId: 'run-1', workflowId: 'wf-1' }));

      const emit = mockNotificationService.emitWorkflowRunUpdate as sinon.SinonStub;
      expect(emit.firstCall.args[0].status).to.equal('succeeded');
    });

    it('does not emit a run update when the payload has no workflow/run ids', async () => {
      stubRecipients();
      // No runId means no natural key to upsert on, so this falls back to the
      // plain insert path.
      sinon.stub(NotificationSchema.Notifications, 'create').resolves([
        {
          _id: 'nid1',
          assignedTo: new mongoose.Types.ObjectId(userId),
          toObject: () => ({ _id: 'nid1' }),
        },
      ] as any);

      await deliver(workflowRunEvent({ status: 'succeeded' }));

      expect((mockNotificationService.emitWorkflowRunUpdate as sinon.SinonStub).called).to.be
        .false;
    });

    it('non-workflow notifications still use the plain insert path', async () => {
      stubRecipients();
      const createStub = sinon.stub(NotificationSchema.Notifications, 'create').resolves([
        {
          _id: 'nid1',
          assignedTo: new mongoose.Types.ObjectId(userId),
          toObject: () => ({ _id: 'nid1' }),
        },
      ] as any);
      const upsertStub = sinon.stub(NotificationSchema.Notifications, 'findOneAndUpdate');

      await deliver({
        value: {
          orgId,
          type: 'CONNECTOR_SYNC_ERROR',
          recipientUserIds: [userId],
          recipientRoles: [],
        },
      });

      expect(createStub.calledOnce).to.be.true;
      expect(upsertStub.called).to.be.false;
    });
  });
});
