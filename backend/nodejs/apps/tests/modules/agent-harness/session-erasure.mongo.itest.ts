import type { Tx } from '../../../src/libs/repositories/uow';
/**
 * E7 + T5 on a real MongoDB replica set (and Redis for the LiveBus when
 * REDIS_IT_URL is set): every erasure step against the stores it owns,
 * C-14b (every store a session writes to is covered, another session's
 * stays), a crash between each pair of steps (ER-14), the
 * GC rules (GC-1..GC-7), retention (RTN-2) and the D2 migration (D2-1).
 * The job store (fencing ER-15, BF-18) is support/erasure-job-store-contract.ts.
 * Run instructions in execution-store.mongo.itest.ts.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { idOperator } from '../../../src/libs/repositories/id-filter-guard/mongo';
import sinon from 'sinon';
import mongoose, { Model, Types } from 'mongoose';
import { randomUUID } from 'node:crypto';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';
import { storedManifest } from './support/workspace-repository-contract';
import { testCipher } from './support/data-protection';
import { fixedMongoTime } from '../../../src/libs/repositories/agent-harness/time/mongo';
import { agentDefinition } from './support/agent-definition';
import { CHILD_RESULT } from './support/child-run-contract';
import { ChildRunService } from '../../../src/modules/agent-harness/application/child-run-service';
import { bulkyOutcome, workerEvent } from './support/journal-fixtures';
import { AgentSessionErasureModel } from '../../../src/libs/repositories/agent-harness/session-erasure/schema/session-erasure.schema';
import {
  MongoErasureJobStore,
  MongoSessionRunLookup,
} from '../../../src/libs/repositories/agent-harness/session-erasure/mongo';
import { MongoSessionStoreEraser } from '../../../src/libs/repositories/agent-harness/session-store-eraser/mongo';
import { MongoRetentionStore } from '../../../src/libs/repositories/agent-harness/retention-store/mongo';
import {
  AgentWorkspaceKeyModel,
  AgentWorkspaceManifestModel,
  AgentWorkspaceWriterModel,
} from '../../../src/libs/repositories/agent-harness/workspace/schema/workspace.schema';
import {
  MongoWorkspaceRepository,
  MongoWorkspaceWriterStore,
} from '../../../src/libs/repositories/agent-harness/workspace/mongo';
import {
  AgentAdmissionReceiptModel,
  AgentCommandReceiptModel,
  AgentEventModel,
  AgentRunModel,
  AgentWorkspaceHeadModel,
} from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import {
  AgentJournalPayloadModel,
  SessionEventModel,
} from '../../../src/libs/repositories/agent-harness/journal/schema/journal.schema';
import { MongoAuditChainStore } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { MongoAuditAnchorSink } from '../../../src/libs/repositories/agent-harness/audit-anchor-sink/mongo';
import { AgentAuditRecordModel } from '../../../src/libs/repositories/agent-harness/audit/schema/audit.schema';
import { auditLinksIdle } from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { inMongoDurableTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import {
  migrateErasureStore,
  revertErasureStore,
} from '../../../src/libs/repositories/agent-harness/migrations/mongo';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { sealRunContent } from '../../../src/modules/agent-harness/application/run-content-service';
import { AuditVerifier } from '../../../src/modules/agent-harness/application/audit-anchoring';
import { SessionErasureRunner } from '../../../src/modules/agent-harness/application/session-erasure';
import { RetentionSweeper } from '../../../src/modules/agent-harness/application/retention-sweeper';
import { formatContentRef } from '../../../src/modules/agent-harness/domain/run-content';
import {
  ERASURE_STEPS,
  ErasureStep,
} from '../../../src/modules/agent-harness/domain/session-erasure';
import { SessionErasedError } from '../../../src/modules/agent-harness/domain/errors';
import { TERMINAL_RUN_STATUSES } from '../../../src/modules/agent-harness/domain/execution';
import {
  SessionDataEraser,
} from '../../../src/modules/agent-harness/ports/session-erasure';
import { ChatSession } from '../../../src/modules/enterprise_search/schema/chat.session.schema';
import { ChatSessionMessage } from '../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import Citation from '../../../src/modules/enterprise_search/schema/citation.schema';
import { Conversation } from '../../../src/modules/enterprise_search/schema/conversation.schema';
import { AgentConversation } from '../../../src/modules/enterprise_search/schema/agent.conversation.schema';
import { HarnessConversationRetention } from '../../../src/libs/repositories/agent-harness/conversation-retention/mongo';
import { deleteConversationByIdWith } from '../../../src/modules/enterprise_search/controller/es_controller';
import { SessionRunStopper } from '../../../src/modules/agent-harness/application/session-run-stopper';
import * as conversationArtifacts from '../../../src/modules/artifacts/services/conversation-artifacts.service';
import type { AppConfig } from '../../../src/modules/tokens_manager/config/config';
import { deleteAgentConversation } from '../../../src/modules/enterprise_search/utils/utils';
import { OutboxEvent } from '../../../src/libs/repositories/outbox/schema/outbox.schema';
import { describeErasureJobMachine } from './support/erasure-job-machine';

import {
  SENTINEL,
  World,
  world,
  Seeded,
  append,
  seed,
  ownedPayloads,
  liveDeltas,
  eraseFully,
  typesOf,
  markDeleted,
  enqueue,
} from './support/session-erasure-world';

registerHarnessModels([
  AgentSessionErasureModel,
  AgentWorkspaceManifestModel,
  AgentWorkspaceKeyModel,
  AgentWorkspaceWriterModel,
  Citation,
  Conversation,
  AgentConversation,
] as unknown as Array<Model<never>>);

async function expectErased(w: World, s: Seeded, typesBefore: string[], crashed?: ErasureStep): Promise<void> {
  const job = (await w.jobs.find(s.orgId, s.sessionId))!;
  expect(job.status).to.equal('completed');
  // Runs: all ended, no input or definition left; documents kept as tombstones.
  const runs = await AgentRunModel.find({ orgId: s.orgId, sessionId: s.sessionId }).lean();
  expect(runs.map((r) => r.runId).sort()).to.deep.equal([...s.runIds].sort());
  for (const run of runs) {
    expect(TERMINAL_RUN_STATUSES.has(run.status), run.status).to.equal(true);
    expect(run.input).to.equal(null);
    expect(run.agentDefinition).to.equal(null);
  }
  // Journal: every earlier envelope a tombstone, seqs gap-free, one session.erased last.
  const events = await SessionEventModel.find({ orgId: s.orgId, sessionId: s.sessionId, streamId: s.sessionId }).sort({ seq: 1 }).lean();
  expect(events.map((e) => e.seq)).to.deep.equal(events.map((_e, i) => i + 1));
  // Erasure's own cancels (and the sweep that ends a cancelling run) come after what was there.
  expect(events.slice(0, typesBefore.length).map((e) => e.type)).to.deep.equal(typesBefore);
  expect(events.at(-1)?.type).to.equal('session.erased');
  for (const event of events.slice(0, -1)) {
    expect(event.erased, `${event.type}@${String(event.seq)}`).to.equal(true);
    expect(event).to.not.have.property('sealed');
    expect(event).to.not.have.property('data');
    expect(event).to.not.have.property('labels');
  }
  expect(events.filter((e) => e.type === 'session.erased')).to.have.length(1);
  expect(JSON.stringify(events)).to.not.include(SENTINEL);
  // Audit: the chain verifies, every record of the session still names an envelope of its type, and the erasure is recorded with counts only.
  const verification = await new AuditVerifier(new MongoAuditChainStore(), new MongoAuditAnchorSink()).verify(s.orgId);
  expect(verification.ok, JSON.stringify(verification.problems)).to.equal(true);
  await auditLinksIdle();
  const audits = await AgentAuditRecordModel.find({ orgId: s.orgId, sessionId: s.sessionId }).lean();
  for (const record of audits) {
    const envelope = await SessionEventModel.findOne({ orgId: s.orgId, streamId: record.streamId, seq: record.journalSeq }).lean();
    expect(envelope?.type, record.kind).to.equal(record.kind);
  }
  const erasedRecord = audits.find((r) => r.kind === 'session.erased')!;
  expect(erasedRecord.fields).to.include({ reason: 'user_delete' });
  // A step's counts are recorded when it is marked done; a crashed first try's are lost.
  if (crashed !== 'journal') {
    expect(erasedRecord.fields).to.include({ count_events: events.length - 1 });
  }
  expect(JSON.stringify(audits)).to.not.include(SENTINEL);
  // Payloads, v1 events, receipts.
  expect(await ownedPayloads(s)).to.equal(0);
  expect(await AgentJournalPayloadModel.countDocuments({ _id: s.legacyPayload })).to.equal(0);
  expect(await AgentEventModel.countDocuments({ orgId: s.orgId, sessionId: s.sessionId })).to.equal(0);
  expect(await AgentCommandReceiptModel.countDocuments({ orgId: s.orgId, runId: { $in: s.runIds } })).to.equal(0);
  expect(await AgentAdmissionReceiptModel.countDocuments({ 'result.sessionId': s.sessionId })).to.equal(0);
  // Workspaces: manifests and heads gone, the key shredded for good.
  const ours = idOperator({ $regex: `^ws:${s.sessionId}` });
  expect(await AgentWorkspaceManifestModel.countDocuments({ orgId: s.orgId, workspaceId: ours })).to.equal(0);
  expect(await AgentWorkspaceHeadModel.countDocuments({ orgId: s.orgId, workspaceId: ours })).to.equal(0);
  expect(await AgentWorkspaceWriterModel.countDocuments({ orgId: s.orgId, workspaceId: ours })).to.equal(0);
  let keyError: unknown;
  try {
    await w.keys.issue(s.orgId, s.sessionId);
  } catch (error) {
    keyError = error;
  }
  expect((keyError as { code?: string }).code).to.equal('WORKSPACE_KEY_DESTROYED');
  // LiveBus, artifacts, conversation.
  for (const runId of s.runIds) expect(await liveDeltas(w, runId)).to.equal(0);
  expect(w.artifacts.of(s.orgId, s.sessionId)).to.equal(0);
  expect(await ChatSession.countDocuments({ _id: s.sessionId })).to.equal(0);
  expect(await ChatSessionMessage.countDocuments({ sessionId: new Types.ObjectId(s.sessionId) })).to.equal(0);
  expect(await Citation.countDocuments({ _id: { $in: s.citationIds } })).to.equal(0);
  expect(await mongoose.connection.collection('conversations').countDocuments({ _id: new Types.ObjectId(s.sessionId) })).to.equal(0);
  // ER-13: a read of the erased journal is a typed answer.
  let readError: unknown;
  try {
    await w.sealed.readStream(s.orgId, s.sessionId, 0, 100);
  } catch (error) {
    readError = error;
  }
  expect(readError).to.be.instanceOf(SessionErasedError);
}

async function expectIntact(w: World, s: Seeded): Promise<void> {
  expect(await ownedPayloads(s)).to.be.greaterThan(0);
  expect(await AgentJournalPayloadModel.countDocuments({ _id: s.legacyPayload })).to.equal(1);
  const events = await w.sealed.readStream(s.orgId, s.sessionId, 0, 100);
  expect(events.length).to.be.greaterThan(0);
  expect(events.every((e) => e.erased !== true)).to.equal(true);
  expect(await AgentWorkspaceManifestModel.countDocuments({ orgId: s.orgId, workspaceId: idOperator({ $regex: `^ws:${s.sessionId}` }) })).to.equal(5);
  expect(await AgentWorkspaceWriterModel.countDocuments({ orgId: s.orgId, workspaceId: idOperator({ $regex: `^ws:${s.sessionId}` }) })).to.equal(1);
  const fork = await new MongoWorkspaceRepository().head(s.orgId, `ws:${s.sessionId}:fork:child-1`);
  expect(fork?.lastMerge?.key).to.equal('child-1');
  expect((await w.keys.issue(s.orgId, s.sessionId)).keyDomain).to.equal(`ws:${s.sessionId}`);
  for (const runId of s.runIds) expect(await liveDeltas(w, runId)).to.be.greaterThan(0);
  expect(w.artifacts.of(s.orgId, s.sessionId)).to.equal(2);
  expect(await ChatSession.countDocuments({ _id: s.sessionId })).to.equal(1);
  expect(await Citation.countDocuments({ _id: { $in: s.citationIds } })).to.equal(2);
  if (s.liveRunId !== null) {
    expect((await AgentRunModel.findOne({ orgId: s.orgId, runId: s.liveRunId }).lean())?.status).to.equal('running');
  }
  const runs = await AgentRunModel.find({ orgId: s.orgId, sessionId: s.sessionId }).lean();
  expect(runs.every((run) => run.input !== null)).to.equal(true);
}

describe('E7 session erasure on a MongoDB replica set', function () {
  this.timeout(180_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  describeErasureJobMachine(
    'mongo',
    async (clock) => {
      await resetHarnessMongo();
      return new MongoErasureJobStore(fixedMongoTime(() => clock.now));
    },
    { ci: 15, nightly: 300 },
  );

  it('C-14b, ER-2..ER-12, ER-16, ER-17: erases every store of the session; a running session of the same org is untouched', async () => {
    const w = world();
    const a = await seed(w, { running: true });
    const b = await seed(w, { running: true });
    const typesBefore = await typesOf(a);
    await enqueue(w, a);
    const results = await eraseFully(w, a);
    expect(results).to.include('waiting');
    expect(results.at(-1)).to.equal('completed');
    await expectErased(w, a, typesBefore);
    const job = (await w.jobs.find(a.orgId, a.sessionId))!;
    expect(job.counts).to.include({ runs: 2, cancelled: 1, workspaceKeys: 1, artifacts: 2, manifests: 5, workspaces: 3, writers: 1, payloads: 4, citations: 2, sessions: 1, auditEvents: 1 });
    expect(job.counts['events']).to.be.greaterThan(typesBefore.length);
    expect(job.counts['messages']).to.be.greaterThan(1);
    // B's run is still running: the sweep that ended A's cancelling run only reached A.
    w.clock.set(0);
    await expectIntact(w, { ...b, liveRunId: null });
  });

  it('C-14 (P6.1): a session with child runs: every child ends, its own stream and payloads are erased', async () => {
    const w = world();
    w.harness.setAgentDefinition((agentKey) => agentDefinition({ agentKey, maxChildren: 2 }));
    const admitted = await w.harness.admit({ agentKey: 'agent-1', authorization: 'Bearer user' });
    const parent = await w.harness.claim(admitted.run, 'worker-p', 60_000);
    const backend = w.harness.backend;
    const children = new ChildRunService({
      runs: backend.executionStore,
      children: backend.childRuns!,
      journals: new JournalService(backend.payloads, testCipher()),
    });
    const attempt = {
      orgId: parent.orgId,
      runId: parent.runId,
      workerId: 'worker-p',
      attemptId: parent.attemptId as string,
      fence: parent.fence,
    };
    const done = await children.spawn(attempt, { invocationId: 't1.0', goal: SENTINEL, grants: {} });
    const busy = await children.spawn(attempt, { invocationId: 't1.1', goal: SENTINEL, grants: {} });
    const finished = await w.harness.claim((await w.harness.service.get(parent.orgId, done.childRunId))!, 'worker-c', 60_000);
    await w.harness.service.commit({
      commandId: 'child-done',
      orgId: finished.orgId,
      runId: finished.runId,
      workerId: 'worker-c',
      attemptId: finished.attemptId as string,
      fence: finished.fence,
      events: [],
      legacyEvents: [],
      transition: { status: 'completed', outcome: 'answer', childResult: CHILD_RESULT },
      outbox: [],
    });
    const running = await w.harness.claim((await w.harness.service.get(parent.orgId, busy.childRunId))!, 'worker-d', 60_000);
    await w.harness.service.commit({
      commandId: 'child-bulky',
      orgId: running.orgId,
      runId: running.runId,
      workerId: 'worker-d',
      attemptId: running.attemptId as string,
      fence: running.fence,
      events: [bulkyOutcome(300_000)],
      legacyEvents: [],
      outbox: [],
    });
    const s = { orgId: parent.orgId, sessionId: parent.sessionId };
    const childStreams = [done.childRunId, busy.childRunId];
    expect(await AgentJournalPayloadModel.countDocuments({ orgId: s.orgId, 'owner.runId': busy.childRunId })).to.equal(1);
    await markDeleted(s);
    await w.jobs.enqueue({ ...s, reason: 'user_delete', by: `p_${w.harness.tenant.actorId}` });
    await eraseFully(w, s);
    const runs = await AgentRunModel.find(s).lean();
    expect(runs.map((r) => r.runId).sort()).to.deep.equal([parent.runId, ...childStreams].sort());
    for (const run of runs) {
      expect(TERMINAL_RUN_STATUSES.has(run.status), `${run.runId} ${run.status}`).to.equal(true);
      expect(run.input).to.equal(null);
    }
    for (const streamId of childStreams) {
      const events = await SessionEventModel.find({ orgId: s.orgId, streamId }).lean();
      expect(events.length, streamId).to.be.greaterThan(0);
      for (const event of events) {
        expect(event.erased, `${streamId} ${event.type}`).to.equal(true);
        expect(event).to.not.have.property('sealed');
        expect(event).to.not.have.property('data');
      }
    }
    expect(await AgentJournalPayloadModel.countDocuments({ orgId: s.orgId, 'owner.sessionId': s.sessionId })).to.equal(0);
    expect(JSON.stringify(await SessionEventModel.find({ orgId: s.orgId, sessionId: s.sessionId }).lean())).to.not.include(SENTINEL);
  });

  for (const crashed of ERASURE_STEPS) {
    it(`ER-14: a crash after ${crashed} did its work and before it was marked done resumes there and finishes`, async () => {
      const w = world();
      const a = await seed(w, { running: false });
      const b = await seed(w, { running: false });
      const typesBefore = await typesOf(a);
      let crash = true;
      const crashing = w.steps.map((step): SessionDataEraser<Tx> =>
        step.step !== crashed
          ? step
          : {
              step: step.step,
              erase: async (job, context) => {
                if (crashed === 'audit' && crash) {
                  crash = false;
                  // The audit step is one transaction; a crash inside it commits nothing.
                  await step.erase(job, {
                    completeIn: async (tx, counts) => {
                      await context.completeIn(tx, counts);
                      throw new Error('crash before the commit');
                    },
                  });
                }
                const outcome = await step.erase(job, context);
                if (crash) {
                  crash = false;
                  throw new Error('crash after the side effects');
                }
                return outcome;
              },
            },
      );
      await enqueue(w, a);
      const runner = new SessionErasureRunner({ jobs: w.jobs, erasers: crashing, conversations: w.conversations });
      const first = await runner.run((await w.jobs.claimDue('it', 60_000))!);
      expect(first).to.equal('retry');
      const stuck = (await w.jobs.find(a.orgId, a.sessionId))!;
      expect(stuck.step).to.equal(crashed);
      expect(stuck.failures).to.equal(1);
      w.clock.set(stuck.nextAttemptAt);
      await eraseFully(w, a, runner);
      await expectErased(w, a, typesBefore, crashed);
      await expectIntact(w, b);
    });
  }

  it('ER-6: a query service that is down retries the artifacts step with backoff, then finishes', async () => {
    const w = world();
    const a = await seed(w, { running: false });
    await enqueue(w, a);
    w.artifacts.fail = true;
    const runner = new SessionErasureRunner({ jobs: w.jobs, erasers: w.steps, conversations: w.conversations });
    expect(await runner.run((await w.jobs.claimDue('it', 60_000))!)).to.equal('retry');
    const job = (await w.jobs.find(a.orgId, a.sessionId))!;
    expect(job).to.include({ step: 'artifacts', failures: 1, lastErrorType: 'Error' });
    w.artifacts.fail = false;
    w.clock.set(job.nextAttemptAt);
    await eraseFully(w, a, runner);
    expect(w.artifacts.of(a.orgId, a.sessionId)).to.equal(0);
  });

  it('BF-12: the runner withdraws the job of a conversation that is live again, and erases nothing of it', async () => {
    const w = world();
    const a = await seed(w, { running: false });
    await enqueue(w, a);
    await ChatSession.updateOne({ _id: a.sessionId }, { $set: { isDeleted: false } });
    const messages = await ChatSessionMessage.countDocuments({ sessionId: new Types.ObjectId(a.sessionId) });
    expect(messages).to.be.greaterThan(0);
    const runner = new SessionErasureRunner({ jobs: w.jobs, erasers: w.steps, conversations: w.conversations });
    expect(await runner.run((await w.jobs.claimDue('it', 60_000))!)).to.equal('withdrawn');
    expect(await w.jobs.find(a.orgId, a.sessionId)).to.equal(null);
    expect(await ChatSessionMessage.countDocuments({ sessionId: new Types.ObjectId(a.sessionId) })).to.equal(messages);
    expect(await w.keys.issue(a.orgId, a.sessionId)).to.not.equal(undefined);
  });

  it('R30-LAT ER-STOP: deleting a chat stops its running run before the delete answers; the erasure step then finds it stopped', async () => {
    const w = world();
    const a = await seed(w, { running: true });
    const live = a.liveRunId!;
    const stopper = new SessionRunStopper({ runs: w.harness.backend.executionStore, executions: w.harness.service });
    const handler = deleteConversationByIdWith({} as AppConfig, w.jobs, stopper);
    const cleanup = sinon.stub(conversationArtifacts, 'deleteConversationArtifacts').resolves();
    let status = 0;
    try {
      await handler(
        { params: { conversationId: a.sessionId }, user: { userId: w.harness.tenant.actorId, orgId: a.orgId }, context: {} } as never,
        { status: (code: number) => ({ json: () => { status = code; } }) } as never,
        (error: unknown) => { throw error; },
      );
    } finally {
      cleanup.restore();
    }
    expect(status).to.equal(200);
    // Stopped by the delete itself, not by the erasure job (no tick has run).
    const run = await w.harness.backend.executionStore.get(a.orgId, live);
    expect(run?.status).to.be.oneOf(['cancelling', 'cancelled']);
    const job = (await w.jobs.find(a.orgId, a.sessionId))!;
    expect(job.step).to.equal('cancel_runs');
    // The same command id as the erasure step's: a second stop controls nothing again.
    expect(await stopper.stop(a.orgId, a.sessionId, w.harness.tenant.actorId)).to.equal(0);
  });

  it('ER-1, ER-18: deleting a chat conversation enqueues its erasure in the delete transaction, due at once; a repeat adds none', async () => {
    const w = world();
    const a = await seed(w, { running: false });
    const handler = deleteConversationByIdWith({} as AppConfig, w.jobs);
    const call = async () => {
      let status = 0;
      let failure: unknown;
      // The artifact cleanup is a connectors-service call before the delete; not under test here.
      const cleanup = sinon.stub(conversationArtifacts, 'deleteConversationArtifacts').resolves();
      try {
        await handler(
          { params: { conversationId: a.sessionId }, user: { userId: w.harness.tenant.actorId, orgId: a.orgId }, context: {} } as never,
          { status: (code: number) => ({ json: () => { status = code; } }) } as never,
          (error: unknown) => { failure = error; },
        );
      } finally {
        cleanup.restore();
      }
      return { status, failure };
    };
    expect((await call()).status).to.equal(200);
    const job = (await w.jobs.find(a.orgId, a.sessionId))!;
    expect(job).to.include({ reason: 'user_delete', by: `p_${w.harness.tenant.actorId}`, status: 'pending', step: 'cancel_runs' });
    expect(job.nextAttemptAt).to.equal(job.requestedAt);
    const deletedChat = await ChatSession.findById(a.sessionId).select('+erasureRequestedAt').lean();
    expect(deletedChat?.isDeleted).to.equal(true);
    // BF-2: the marker is written by the delete itself, so the sweeper finds a lost enqueue (E7B).
    expect(deletedChat?.erasureRequestedAt).to.be.a('number');
    // X-5 confirmed: the route's citation soft delete matches nothing (the schema has neither field).
    const citations = await mongoose.connection.collection('citations').find({ _id: { $in: a.citationIds.map((id) => new Types.ObjectId(id)) } }).toArray();
    expect(citations).to.have.length(2);
    expect(citations.every((citation) => citation['isDeleted'] === undefined)).to.equal(true);
    expect((await call()).failure).to.not.equal(undefined);
    expect(await AgentSessionErasureModel.countDocuments({})).to.equal(1);
  });

  it('ER-1: deleting an agent conversation enqueues its erasure with the delete', async () => {
    const w = world();
    const orgId = new Types.ObjectId().toString();
    const userId = new Types.ObjectId().toString();
    const session = await ChatSession.create({ sessionType: 'agent', agentKey: 'agent-1', userId, orgId, initiator: userId, title: SENTINEL });
    const deleted = await deleteAgentConversation(String(session._id), 'agent-1', userId, orgId, async (tx) => {
      await w.jobs.enqueue({ orgId, sessionId: String(session._id), reason: 'user_delete', by: `p_${userId}` }, tx);
    });
    expect(deleted?.isDeleted).to.equal(true);
    expect((await w.jobs.find(orgId, String(session._id)))?.status).to.equal('pending');
    expect((await ChatSession.findById(session._id).select('+erasureRequestedAt').lean())?.erasureRequestedAt, 'BF-2').to.be.a('number');
    // Without an erasure queue the delete asks for none.
    const plain = await ChatSession.create({ sessionType: 'agent', agentKey: 'agent-1', userId, orgId, initiator: userId, title: SENTINEL });
    await deleteAgentConversation(String(plain._id), 'agent-1', userId, orgId);
    expect((await ChatSession.findById(plain._id).select('+erasureRequestedAt').lean())?.erasureRequestedAt).to.equal(undefined);
  });

  it('ER-7: workspace prefixes are exact: ws:S and ws:S:* go, ws:S0 stays', async () => {
    const repository = new MongoWorkspaceRepository();
    for (const id of ['ws:abc', 'ws:abc:fork', 'ws:abc:skills:00ff', 'ws:abc0', 'ws:ab']) {
      await repository.append({ manifest: storedManifest(id, 1, 'org-1'), expectedRevision: 0 });
    }
    const writers = new MongoWorkspaceWriterStore();
    for (const id of ['ws:abc:fork:c', 'ws:abc0']) {
      await writers.acquire({ orgId: 'org-1', sessionId: 'abc', workspaceId: id, runId: 'r', expiresAt: 10 }, 1);
    }
    const erased = await new MongoSessionStoreEraser(testCipher()).eraseWorkspaces('org-1', 'abc');
    expect(erased).to.deep.equal({ manifests: 3, heads: 3, writers: 1 });
    expect((await AgentWorkspaceWriterModel.find({}).lean()).map((l) => l.workspaceId)).to.deep.equal(['ws:abc0']);
    expect((await AgentWorkspaceManifestModel.find({}).lean()).map((m) => m.workspaceId).sort()).to.deep.equal(['ws:ab', 'ws:abc0']);
  });

  it("ER-8: a payload another session owns is never erased, even when this session's events point at it", async () => {
    const w = world();
    const a = await seed(w, { running: false });
    const theirs = await w.payloads.put(a.orgId, `sha256:theirs-${randomUUID()}`, Buffer.from('x'), { sessionId: 'other-session', runId: 'other-run' });
    await append(w, {
      orgId: a.orgId, sessionId: a.sessionId, streamId: a.sessionId, runId: a.runIds[0], attemptId: 'a1', fence: 1, writer: 'worker',
      events: [workerEvent('tool.outcome', { invocationId: 'inv-3', status: 'ok', receipt: { ref: formatContentRef({ writerRunId: a.runIds[0]!, storeRef: theirs }) }, labels: { integrity: 'external' } })],
    });
    await enqueue(w, a);
    await eraseFully(w, a);
    expect(await AgentJournalPayloadModel.countDocuments({ 'owner.sessionId': 'other-session' })).to.equal(1);
  });
});

describe('T5 GC and retention on a MongoDB replica set', function () {
  this.timeout(120_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  const DAY = 24 * 60 * 60 * 1000;

  it('GC-2: an append marks its payloads referenced in its transaction; an aborted one leaves them unreferenced', async () => {
    const w = world();
    const a = await seed(w, { running: false });
    expect(await AgentJournalPayloadModel.countDocuments({ 'owner.sessionId': a.sessionId, referenced: true })).to.equal(2);
    const orphan = await sealRunContent(testCipher(), w.payloads, { orgId: a.orgId, sessionId: a.sessionId, runId: a.runIds[0]! }, Buffer.from('orphan'));
    let aborted = false;
    try {
      await inMongoDurableTransaction(async (tx) => {
        await w.journal.append(await w.journals.prepare({
          orgId: a.orgId, sessionId: a.sessionId, streamId: a.sessionId, runId: a.runIds[0], attemptId: 'a1', fence: 1, writer: 'worker',
          events: [workerEvent('tool.outcome', { invocationId: 'inv-9', status: 'ok', receipt: { ref: orphan.ref }, labels: { integrity: 'external' } })],
        }), tx);
        throw new Error('abort');
      });
    } catch {
      aborted = true;
    }
    expect(aborted).to.equal(true);
    const storeRef = orphan.ref.slice(orphan.ref.indexOf('/') + 1);
    expect((await AgentJournalPayloadModel.findById(storeRef.replace(/^mongo:/, '')).lean())?.referenced).to.equal(false);
  });

  it('GC-1 (I8): collects only unreferenced payloads past the grace whose writer run ended or never existed', async () => {
    const w = world();
    const a = await seed(w, { running: true });
    const put = (name: string, runId: string | undefined) =>
      w.payloads.put(a.orgId, `sha256:${name}-${randomUUID()}`, Buffer.from(name), { sessionId: a.sessionId, ...(runId !== undefined ? { runId } : {}) });
    const ended = await put('ended', a.runIds[0]);
    const live = await put('live', a.liveRunId!);
    const aborted = await put('aborted-admission', 'run-that-never-existed');
    const sessionLevel = await put('session-level', undefined);
    const young = await put('young', a.runIds[0]);
    const old = new Date(Date.now() - 2 * DAY);
    await AgentJournalPayloadModel.updateMany({ _id: { $in: [ended, live, aborted, sessionLevel].map((ref) => ref.replace(/^mongo:/, '')) } }, { $set: { createdAt: old } });
    const store = new MongoRetentionStore();
    const collected = await store.collectPayloads(Date.now() - DAY, 100);
    expect(collected).to.equal(3);
    const left = new Set((await AgentJournalPayloadModel.find({}, { _id: 1 }).lean()).map((p) => `mongo:${p._id}`));
    expect(left.has(live), 'a live run may still reference it').to.equal(true);
    expect(left.has(young), 'inside the grace').to.equal(true);
    expect(left.has(ended) || left.has(aborted) || left.has(sessionLevel)).to.equal(false);
    // Referenced ones (seeded, and the second run's history) and legacy unowned ones are never candidates.
    expect(await AgentJournalPayloadModel.countDocuments({ 'owner.sessionId': a.sessionId, referenced: true })).to.equal(3);
  });

  it('P6.4 EG-3: a GC pass leaves a live fork, its merge record and the leases whole', async () => {
    const w = world();
    const a = await seed(w, { running: true });
    const ours = { orgId: a.orgId, workspaceId: idOperator({ $regex: `^ws:${a.sessionId}` }) };
    const before = {
      manifests: await AgentWorkspaceManifestModel.countDocuments(ours),
      heads: await AgentWorkspaceHeadModel.countDocuments(ours),
      writers: await AgentWorkspaceWriterModel.countDocuments(ours),
    };
    const store = new MongoRetentionStore();
    await store.collectPayloads(Date.now() + DAY, 1_000);
    await store.expireCommandReceipts(Date.now() + DAY, 1_000);
    await store.expireAdmissionReceipts(Date.now() + DAY, 1_000);
    expect({
      manifests: await AgentWorkspaceManifestModel.countDocuments(ours),
      heads: await AgentWorkspaceHeadModel.countDocuments(ours),
      writers: await AgentWorkspaceWriterModel.countDocuments(ours),
    }).to.deep.equal(before);
    expect(before).to.deep.equal({ manifests: 5, heads: 3, writers: 1 });
    const fork = await new MongoWorkspaceRepository().head(a.orgId, `ws:${a.sessionId}:fork:child-1`);
    expect(fork?.forkOf).to.deep.equal({ workspaceId: `ws:${a.sessionId}`, revision: 1 });
  });

  it('GC-3, GC-4: receipts expire after the TTL; a live run keeps its receipts; a replay inside the TTL still answers', async () => {
    const w = world();
    const a = await seed(w, { running: true });
    await AgentCommandReceiptModel.create({ orgId: a.orgId, runId: a.liveRunId!, commandId: 'live', payloadHash: 'h', receipt: {}, createdAt: 1 });
    const replay = await w.harness.admit({ ...w.harness.tenant, idempotencyKey: 'replay-key' }).catch(() => null);
    const store = new MongoRetentionStore();
    const receiptsBefore = await AgentCommandReceiptModel.countDocuments({});
    const expired = await store.expireCommandReceipts(Date.now(), 100);
    expect(expired).to.equal(receiptsBefore - (await AgentCommandReceiptModel.countDocuments({})));
    expect(await AgentCommandReceiptModel.countDocuments({ runId: a.liveRunId! })).to.be.greaterThan(0);
    expect(await AgentCommandReceiptModel.countDocuments({ runId: a.runIds[0]! })).to.equal(0);
    // Admission receipts: the contract clock's createdAt (~1 s) is far past a week before now; inside the TTL nothing goes.
    expect(await store.expireAdmissionReceipts(0, 100)).to.equal(0);
    if (replay !== null) {
      const again = await w.harness.admit({ ...w.harness.tenant, idempotencyKey: 'replay-key' });
      expect(again.run.runId).to.equal(replay.run.runId);
    }
    expect(await store.expireAdmissionReceipts(Date.now(), 100)).to.be.greaterThan(0);
  });

  it('GC-6: the shared outbox expires published rows and never pending ones', async () => {
    await OutboxEvent.init();
    const ttl = (await OutboxEvent.collection.indexes()).find((index) => index.expireAfterSeconds !== undefined);
    expect(ttl?.key).to.deep.equal({ publishedAt: 1 });
    expect(ttl?.partialFilterExpression).to.deep.equal({ status: 'published' });
  });

  it('GC-7, RT-5: audit records are never collected; the chain verifies after GC and erasure', async () => {
    const w = world();
    const a = await seed(w, { running: false });
    await auditLinksIdle();
    const before = await AgentAuditRecordModel.countDocuments({ orgId: a.orgId });
    await new MongoRetentionStore().collectPayloads(Date.now() + DAY, 100);
    await new MongoRetentionStore().expireCommandReceipts(Date.now() + DAY, 100);
    await enqueue(w, a);
    await eraseFully(w, a);
    await auditLinksIdle();
    expect(await AgentAuditRecordModel.countDocuments({ orgId: a.orgId })).to.equal(before + 1);
    expect((await new AuditVerifier(new MongoAuditChainStore(), new MongoAuditAnchorSink()).verify(a.orgId)).ok).to.equal(true);
  });

  it('RTN-2: retention expires idle conversations through the erasure job; one with a live run waits', async () => {
    const w = world();
    const idle = await seed(w, { running: false });
    const busy = await seed(w, { running: true });
    await ChatSession.updateMany({}, { $set: { lastActivityAt: Date.now() - 40 * DAY } });
    const settings = { all: async () => [{ orgId: idle.orgId, days: 30 }] };
    const sweeper = new RetentionSweeper<Tx>({
      store: new MongoRetentionStore(),
      settings,
      conversations: new HarnessConversationRetention(),
      erasures: w.jobs,
      runs: new MongoSessionRunLookup(),
      artifacts: null,
      transaction: (work) => inMongoDurableTransaction(work),
      lease: { tryAcquire: () => Promise.resolve(true), release: () => Promise.resolve() },
      holderId: 'it',
    });
    const report = (await sweeper.tick())!;
    expect(report.expiredSessions).to.equal(1);
    expect((await ChatSession.findById(idle.sessionId).lean())?.isDeleted).to.equal(true);
    expect((await w.jobs.find(idle.orgId, idle.sessionId))?.reason).to.equal('retention');
    expect((await ChatSession.findById(busy.sessionId).lean())?.isDeleted).to.equal(false);
    expect(await w.jobs.find(busy.orgId, busy.sessionId)).to.equal(null);
    expect(report.requeued).to.equal(0);
  });

  it('RTN-2: erasing a retention-expired session records reason retention and no person', async () => {
    const w = world();
    const a = await seed(w, { running: false });
    await markDeleted(a);
    await w.jobs.enqueue({ orgId: a.orgId, sessionId: a.sessionId, reason: 'retention', by: null });
    await eraseFully(w, a);
    await auditLinksIdle();
    const record = await AgentAuditRecordModel.findOne({ orgId: a.orgId, kind: 'session.erased' }).lean();
    expect(record?.fields).to.include({ reason: 'retention' });
    expect(record?.fields).to.not.have.property('by');
  });

  it('ER-2: a retention erasure cancels a live run as the retention principal', async () => {
    const w = world();
    const a = await seed(w, { running: true });
    await markDeleted(a);
    await w.jobs.enqueue({ orgId: a.orgId, sessionId: a.sessionId, reason: 'retention', by: null });
    await eraseFully(w, a);
    await auditLinksIdle();
    const cancel = await AgentAuditRecordModel.findOne({ orgId: a.orgId, sessionId: a.sessionId, kind: 'run.cancel_requested', runId: a.liveRunId }).lean();
    expect(cancel?.fields).to.deep.equal({ by: 'p_system_retention' });
  });

  it('D2-1: the erasure migration goes up, down and up again on the same documents', async () => {
    await new MongoErasureJobStore().enqueue({ orgId: 'o', sessionId: 's', reason: 'user_delete', by: null });
    const db = mongoose.connection.db!;
    const names = async () =>
      (await db.collection('agentSessionErasures').indexes()).map((index) => index.name).sort();
    await revertErasureStore(db);
    const down = await revertErasureStore(db);
    expect(down.dropped).to.deep.equal([]);
    expect(await names()).to.deep.equal(['_id_']);
    const up = await migrateErasureStore(db);
    expect(up.created).to.include('agentSessionErasures.pending_due');
    expect(up.created).to.include('agentJournalPayloads.unreferenced_created');
    expect(up.created).to.include('agentRuns.org_session');
    expect((await migrateErasureStore(db)).created).to.deep.equal([]);
    expect(await names()).to.deep.equal(['_id_', 'org_session', 'pending_due']);
    expect(await AgentSessionErasureModel.countDocuments({})).to.equal(1);
    const again = await revertErasureStore(db);
    expect(again.dropped.length).to.equal(up.created.length);
    expect((await migrateErasureStore(db)).created.length).to.equal(up.created.length);
  });
});

