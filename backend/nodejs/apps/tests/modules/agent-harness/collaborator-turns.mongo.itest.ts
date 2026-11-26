/**
 * SS2 on a MongoDB replica set: the collaborator-turns contract against the
 * real admission store, share predicate and conversation writer, plus what
 * only Mongo can show (the enqueue racing the run's end, the conversation's
 * attribution and the history a collaborator's run is given). Run as
 * execution-store.mongo.itest.ts says.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { randomUUID } from 'crypto';
import { Types } from 'mongoose';
import { ChatSessionMessage } from '../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import { HarnessHistorySource } from '../../../src/libs/repositories/agent-harness/history-source/mongo';
import { AgentNextRunModel, AgentRunModel } from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import { SessionEventModel } from '../../../src/libs/repositories/agent-harness/journal/schema/journal.schema';
import { MongoRunAdmissionStore } from '../../../src/libs/repositories/agent-harness/run-admission-store/mongo';
import { MongoSessionStoreEraser } from '../../../src/libs/repositories/agent-harness/session-store-eraser/mongo';
import { HarnessConversationEraser } from '../../../src/libs/repositories/agent-harness/conversation-eraser/mongo';
import { TurnLabel } from '../../../src/modules/agent-harness/domain/turn-labels';
import { testCipher } from './support/data-protection';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { NextRunService } from '../../../src/modules/agent-harness/application/next-run-service';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { FOREIGN_TURN_STUB } from '../../../src/modules/agent-harness/domain/session-audience';
import {
  CollaboratorBackend,
  describeCollaboratorTurnsContract,
} from './support/collaborator-turns-contract';
import {
  contractClock,
  createContractHarness,
  admittedHistory,
} from './support/execution-store-contract';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  resetHarnessMongo,
} from './support/harness-mongo';
import { fixedRollout, HARNESS_ENABLED } from './support/rollout';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testModelAccess } from './support/model-access';
import { workerEvent } from './support/journal-fixtures';
import {
  mongoCollaboratorBackend,
  shareConversation,
} from './support/collaborator-mongo';
import { readmissionBy } from './support/next-runs';
import { FakeChatAttachments } from './support/chat-attachments';

const share = shareConversation;
const collaboratorBackend = mongoCollaboratorBackend;

describe('collaborator turns on a MongoDB replica set (SS2)', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  describeCollaboratorTurnsContract('mongo replica set', (clock) => {
    const backend = collaboratorBackend(clock);
    // NextRunQueue and NextRunEraser are served by the production admission store.
    expect(backend.admissionStore).to.be.instanceOf(MongoRunAdmissionStore);
    return backend;
  });

  describe('what only Mongo shows', () => {
    const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
    let clock: ReturnType<typeof contractClock>;
    let backend: CollaboratorBackend;
    let orgId: string;
    let alice: string;
    let bob: string;

    const admission = (): RunAdmissionService =>
      new RunAdmissionService({
        store: backend.admissionStore,
        rollout: fixedRollout(() => V2),
        clock,
        agentAccess: grantingAccess((agentKey) => agentDefinition({ agentKey })),
        modelAccess: testModelAccess(),
        roles: backend.roles,
        collaboratorTurns: { enabled: async () => true },
      });

    const send = async (actorId: string, sessionId?: string, query = 'q') => {
      const outcome = await admission().submit({
        orgId,
        actorId,
        ...(sessionId !== undefined ? { sessionId } : {}),
        idempotencyKey: randomUUID(),
        deadlineMs: 9_000,
        input: {
          query,
          recordIds: [],
          filters: {},
          attachments: [],
          model: { chatMode: 'auto' },
        },
      });
      if (outcome.route !== 'durable') expect.fail('expected durable');
      return outcome.admission;
    };

    beforeEach(async () => {
      await resetHarnessMongo();
      clock = contractClock();
      backend = collaboratorBackend(clock);
      orgId = new Types.ObjectId().toString();
      alice = new Types.ObjectId().toString();
      bob = new Types.ObjectId().toString();
    });

    const answer = async (run: HarnessRun, text: string): Promise<void> => {
      const h = createContractHarness(backend, clock);
      const claimed = await h.claim(run, `worker-${run.runId}`);
      await h.service.commit({
        commandId: `complete-${run.runId}`,
        orgId: run.orgId,
        runId: run.runId,
        workerId: `worker-${run.runId}`,
        attemptId: `attempt-worker-${run.runId}`,
        fence: claimed.fence,
        events: [
          workerEvent(
            'x.pipeshub.answer.finalized',
            {
              outcome: 'answered',
              answer: text,
              partTexts: [text],
              citations: [],
              confidence: null,
            },
            { critical: false },
          ),
        ],
        legacyEvents: [],
        transition: { status: 'completed', outcome: 'answer' },
        outbox: [],
      });
    };

    it('AU-5: a collaborator\'s turn and answer are attributed; the next run of anyone else gets them as a stub', async () => {
      const first = await send(alice, undefined, 'alice asks about R');
      await share(first.sessionId, bob, 'write');
      await answer(first.run, 'R says forty days');
      const bobs = await send(bob, first.sessionId, 'bob asks');
      expect(bobs.run.sessionRole).to.equal('collaborator');
      // Bob's history: Alice's turns are someone else's.
      expect(await admittedHistory(backend, bobs.run)).to.deep.equal([
        { role: 'user_query', content: FOREIGN_TURN_STUB },
        { role: 'bot_response', content: FOREIGN_TURN_STUB },
      ]);
      await answer(bobs.run, 'bob gets his answer');
      const messages = await ChatSessionMessage.find({ sessionId: first.sessionId })
        .sort({ seq: 1 })
        .lean();
      expect(
        messages.map((m) => [m.messageType, m.senderId ? String(m.senderId) : null]),
      ).to.deep.equal([
        ['user_query', null],
        ['bot_response', null],
        ['user_query', bob],
        ['bot_response', bob],
      ]);
      const history = await new HarnessHistorySource().read(
        { orgId, sessionId: first.sessionId },
        null as never,
      );
      expect(history.messages.flatMap((m) => m.turns).map((t) => t['sender'] ?? null)).to.deep.equal([
        null,
        null,
        `p_${bob}`,
        `p_${bob}`,
      ]);
      // Alice's next run: her own turns as they were, Bob's as a stub.
      const alices = await send(alice, first.sessionId, 'alice again');
      expect(alices.run.sessionRole).to.equal('owner');
      const turns = await admittedHistory(backend, alices.run);
      expect(turns.map((t) => t['content'])).to.deep.equal([
        'alice asks about R',
        'R says forty days',
        FOREIGN_TURN_STUB,
        FOREIGN_TURN_STUB,
      ]);
      expect(JSON.stringify(turns)).to.not.contain('bob asks');
      expect(JSON.stringify(turns)).to.not.contain('bob gets his answer');
    });

    const labelled = async (run: HarnessRun, label: TurnLabel): Promise<void> => {
      const h = createContractHarness(backend, clock);
      const claimed = await h.claim(run, `worker-${run.runId}`);
      await h.service.commit({
        commandId: `complete-${run.runId}`,
        orgId: run.orgId,
        runId: run.runId,
        workerId: `worker-${run.runId}`,
        attemptId: `attempt-worker-${run.runId}`,
        fence: claimed.fence,
        events: [
          workerEvent(
            'x.pipeshub.answer.finalized',
            { outcome: 'answered', answer: 'a', partTexts: ['a'], citations: [], confidence: null, label },
            { critical: false },
          ),
        ],
        legacyEvents: [],
        transition: { status: 'completed', outcome: 'answer' },
        outbox: [],
      });
    };
    const RECORD_LABEL: TurnLabel = { complete: true, sources: [{ kind: 'indexed', recordId: 'rec-secret-id' }] };

    it('ST-M: the turn label is on the run and its answer, and an ordinary read of the conversation never sees it', async () => {
      const first = await send(alice);
      await labelled(first.run, RECORD_LABEL);
      const stored = await AgentRunModel.findOne({ runId: first.run.runId }).lean();
      expect((stored as { label?: unknown }).label).to.deep.equal(RECORD_LABEL);
      const ordinary = await ChatSessionMessage.find({ sessionId: first.sessionId }).lean();
      expect(JSON.stringify(ordinary)).to.not.include('rec-secret-id');
      const named = await ChatSessionMessage.findOne({ sessionId: first.sessionId, messageType: 'bot_response' })
        .select('+label')
        .lean();
      expect((named as { label?: unknown }).label).to.deep.equal(RECORD_LABEL);
      expect((named as { runId?: unknown }).runId, 'UX5: the panel finds the run after a reload').to.equal(first.run.runId);
      const run = await backend.executionStore.get(orgId, first.run.runId);
      expect(JSON.stringify(run)).to.not.include('rec-secret-id');
    });

    it('ER-1: erasing the session leaves no label on any run, message or event', async () => {
      const first = await send(alice);
      await labelled(first.run, RECORD_LABEL);
      await new MongoSessionStoreEraser(testCipher()).tombstoneJournal(orgId, first.sessionId, [first.run.runId]);
      await new HarnessConversationEraser(new FakeChatAttachments()).erase(orgId, first.sessionId);
      const runs = await AgentRunModel.find({ orgId, sessionId: first.sessionId }).lean();
      const events = await SessionEventModel.find({ orgId, sessionId: first.sessionId }).lean();
      const messages = await ChatSessionMessage.find({ sessionId: first.sessionId }).select('+label').lean();
      expect(runs).to.have.length(1);
      expect(messages).to.have.length(0);
      expect(JSON.stringify({ runs, events })).to.not.include('rec-secret-id');
    });

    it('NR-10: a message racing the run\'s end is never stranded', async function () {
      this.timeout(120_000);
      for (let round = 0; round < 10; round += 1) {
        await resetHarnessMongo();
        const first = await send(alice);
        await share(first.sessionId, bob, 'write');
        const h = createContractHarness(backend, clock);
        const drains = new NextRunService({
          store: backend.admissionStore,
          readmission: readmissionBy(admission),
          collaboratorTurns: { enabled: async () => true },
          clock,
        });
        h.service.onRunEnded((run) => drains.runEnded(run));
        const current = (await h.service.get(orgId, first.run.runId)) as HarnessRun;
        const [sent, ended] = await Promise.allSettled([
          send(bob, first.sessionId),
          h.service.control({
            commandId: `end-${String(round)}`,
            dispatchCommandId: `end-${String(round)}:dispatch`,
            orgId,
            runId: first.run.runId,
            expectedRevision: current.revision,
            action: 'cancel',
          }),
        ]);
        expect([sent.status, ended.status]).to.deep.equal(['fulfilled', 'fulfilled']);
        const result = (sent as PromiseFulfilledResult<Awaited<ReturnType<typeof send>>>).value;
        const bobsRunId = result.nextRun?.runId ?? result.run.runId;
        let bobs: HarnessRun | null = null;
        for (let tries = 0; tries < 100 && bobs === null; tries += 1) {
          bobs = await h.service.get(orgId, bobsRunId);
          if (bobs === null) {
            // The run's own drain may have run before the enqueue committed: the sweeper's look finds it.
            await drains.drainDue(10);
            await new Promise((resolve) => setTimeout(resolve, 20));
          }
        }
        expect(bobs?.actorId, `round ${String(round)}`).to.equal(bob);
        expect(await AgentNextRunModel.countDocuments({ status: 'pending' })).to.equal(0);
      }
    });
  });
});
