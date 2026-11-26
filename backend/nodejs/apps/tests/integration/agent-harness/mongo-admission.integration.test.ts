import { migrateHarnessIndexes } from '../../../src/libs/repositories/agent-harness/migrations/mongo';
import { expect } from 'chai';
import mongoose, { Types } from 'mongoose';
import {
  RunAdmissionService,
  SubmitDurableRun,
} from '../../../src/modules/agent-harness/application/run-admission-service';
import { RunAdmissionResult } from '../../../src/modules/agent-harness/domain/admission';
import { MongoRunAdmissionStore } from '../../../src/libs/repositories/agent-harness/run-admission-store/mongo';
import { MongoExecutionStore } from '../../../src/libs/repositories/agent-harness/execution/mongo';
import { commitBatchFromTransition } from '../../../src/modules/agent-harness/domain/execution';
import {
  AgentAdmissionReceiptModel,
  AgentEventCounterModel,
  AgentEventModel,
  AgentRunModel,
} from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { MongoDispatchOutbox } from '../../../src/libs/repositories/agent-harness/dispatch-outbox/mongo';
import { OutboxEvent } from '../../../src/libs/repositories/outbox/schema/outbox.schema';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import { HarnessChatSessionRepository } from '../../../src/libs/repositories/agent-harness/chat-session-repository/mongo';
import { HarnessConversationWriter } from '../../../src/libs/repositories/agent-harness/conversation-writer/mongo';
import { HarnessHistorySource } from '../../../src/libs/repositories/agent-harness/history-source/mongo';
import { fixedRollout } from '../../modules/agent-harness/support/rollout';
import { agentDefinition } from '../../modules/agent-harness/support/agent-definition';
import { testRunKeys } from '../../modules/agent-harness/support/data-protection';

const admit = async (
  service: RunAdmissionService,
  request: SubmitDurableRun,
): Promise<RunAdmissionResult> => {
  const outcome = await service.submit(request);
  if (outcome.route !== 'durable') expect.fail('expected a durable admission');
  return outcome.admission;
};

const admissionStore = () =>
  new MongoRunAdmissionStore({
    sessions: new HarnessChatSessionRepository(),
    history: new HarnessHistorySource(),
    conversations: new HarnessConversationWriter(),
    dispatches: new MongoDispatchOutbox(),
    transactionsAvailable: true,
    runKeys: testRunKeys(),
  });

const mongoUri = process.env.AGENT_HARNESS_TEST_MONGO_URI;
const describeWithMongo = mongoUri ? describe : describe.skip;

describeWithMongo('Mongo durable run admission integration', function () {
  this.timeout(30_000);

  before(async () => {
    if (mongoUri === undefined || mongoUri.length === 0) {
      throw new Error('AGENT_HARNESS_TEST_MONGO_URI is required');
    }
    await mongoose.connect(mongoUri, {
      dbName: `agent_harness_admission_${Date.now()}`,
    });
    await Promise.all([
      ChatSession.init(),
      ChatSessionMessage.init(),
      AgentRunModel.init(),
      AgentEventModel.init(),
      OutboxEvent.init(),
      AgentEventCounterModel.init(),
      AgentAdmissionReceiptModel.init(),
    ]);
    await migrateHarnessIndexes(mongoose.connection.db!);
  });

  after(async () => {
    if (mongoose.connection.readyState === mongoose.ConnectionStates.connected) {
      await mongoose.connection.dropDatabase();
    }
    await mongoose.disconnect();
  });

  it('atomically admits once, replays the receipt, and rejects conflicts', async () => {
    const orgId = new Types.ObjectId().toString();
    const actorId = new Types.ObjectId().toString();
    let nextRun = 0;
    const service = new RunAdmissionService({
      store: admissionStore(),
      rollout: fixedRollout(),
      clock: { now: () => 10_000 },
      newId: () => `run-${++nextRun}`,
      agentAccess: {
        authorizeRun: async (request) =>
          agentDefinition({ agentKey: request.agentKey }),
      },
    });
    const request = {
      orgId,
      actorId,
      agentKey: 'integration-agent',
      authorization: 'Bearer integration-user',
      idempotencyKey: 'integration-submit-1',
      deadlineMs: 60_000,
      input: {
        query: 'Create a durable artifact',
        recordIds: [],
        filters: {},
        attachments: [],
        model: { chatMode: 'auto' },
      },
    };

    const admitted = await admit(service, request);
    const replayed = await admit(service, request);

    expect(admitted.created).to.equal(true);
    // A replay answers the stored admission; only the first call created the conversation.
    expect(admitted.createdSession).to.deep.equal({ title: 'Create a durable artifact' });
    const stored: Partial<RunAdmissionResult> = { ...admitted, created: false };
    delete stored.createdSession;
    expect(replayed).to.deep.equal(stored);
    expect(admitted.run.status).to.equal('queued');
    expect(admitted.run.input?.previousConversations).to.deep.equal([]);
    expect(await ChatSession.countDocuments()).to.equal(1);
    expect(await ChatSessionMessage.countDocuments()).to.equal(1);
    expect(await AgentRunModel.countDocuments()).to.equal(1);
    expect(await AgentEventModel.countDocuments()).to.equal(1);
    expect(await OutboxEvent.countDocuments()).to.equal(1);
    expect(await AgentAdmissionReceiptModel.countDocuments()).to.equal(1);

    try {
      await admit(service, {
        ...request,
        input: { ...request.input, query: 'Changed payload' },
      });
      expect.fail('changed idempotent payload should have been rejected');
    } catch (error) {
      expect(error).to.be.instanceOf(HarnessConflictError);
      expect((error as HarnessConflictError).code).to.equal(
        'IDEMPOTENCY_CONFLICT',
      );
    }

    try {
      await admit(service, {
        ...request,
        sessionId: admitted.sessionId,
        idempotencyKey: 'integration-submit-2',
      });
      expect.fail('second active root run should have been rejected');
    } catch (error) {
      expect(error).to.be.instanceOf(HarnessConflictError);
      expect((error as HarnessConflictError).code).to.equal('RUN_ACTIVE');
    }

    expect(await ChatSessionMessage.countDocuments()).to.equal(1);
    expect(await AgentRunModel.countDocuments()).to.equal(1);
    expect(await OutboxEvent.countDocuments()).to.equal(1);
  });

  it('atomically creates a durable built-in chat session', async () => {
    const orgId = new Types.ObjectId().toString();
    const actorId = new Types.ObjectId().toString();
    const service = new RunAdmissionService({
      store: admissionStore(),
      rollout: fixedRollout(),
      clock: { now: () => 20_000 },
      newId: () => 'plain-chat-run',
    });

    const admitted = await admit(service, {
      orgId,
      actorId,
      idempotencyKey: 'plain-chat-submit',
      deadlineMs: 60_000,
      input: {
        query: 'Create a durable built-in chat response',
        recordIds: [],
        filters: {},
        attachments: [],
        model: { chatMode: 'auto' },
      },
    });

    const session = await ChatSession.findById(admitted.sessionId)
      .select('+sessionType +harnessVersion')
      .lean();
    expect(admitted.run.agentKey).to.equal(undefined);
    expect(session?.sessionType).to.equal('chat');
    expect(session?.harnessVersion).to.equal('durable-v1');

    const executions = new MongoExecutionStore(
      new HarnessConversationWriter(),
      new MongoDispatchOutbox(),
    );
    const { run: claimed } = await executions.claim({
      orgId,
      runId: admitted.run.runId,
      workerId: 'integration-worker',
      attemptId: 'integration-attempt',
      leaseDurationMs: 30_000,
    });
    const receipt = await executions.commit(
      commitBatchFromTransition({
        orgId,
        runId: claimed.runId,
        workerId: 'integration-worker',
        attemptId: 'integration-attempt',
        fence: claimed.fence,
        commandId: 'plain-chat-complete',
        expectedRevision: claimed.revision,
        status: 'completed',
        outcome: 'answer',
        events: [
          { type: 'TEXT_MESSAGE_CONTENT', data: { delta: 'done' } },
          {
            type: 'run_completed',
            data: { answer: 'done', citations: [] },
          },
        ],
        outbox: [],
      }),
      [],
    );
    expect(receipt.run.status).to.equal('completed');
    expect(receipt.lastEventSeq).to.equal(
      (receipt.firstEventSeq as number) + 1,
    );
    expect(
      await ChatSessionMessage.countDocuments({ sessionId: session?._id }),
    ).to.equal(2);
  });
});
