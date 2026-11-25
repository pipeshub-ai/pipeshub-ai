/**
 * PERF-B (QA22-F2) on a MongoDB replica set (run as execution-store.mongo.itest.ts
 * says): a run's history is kept beside its document, read back by the claim,
 * erased with the session and never collected while a run names it.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { sessionMessages } from './support/session-messages';
import mongoose, { Model } from 'mongoose';
// bson is the driver's own dependency (nested under mongodb), so EJSON comes through the driver, a direct dependency.
import { BSON } from 'mongodb';

const { EJSON } = BSON;
import { ChatSessionMessage } from '../../../src/modules/enterprise_search/schema/chat.session.message.schema';
import Citation from '../../../src/modules/enterprise_search/schema/citation.schema';
import { Conversation } from '../../../src/modules/enterprise_search/schema/conversation.schema';
import { AgentConversation } from '../../../src/modules/enterprise_search/schema/agent.conversation.schema';
import { formatPreviousConversations } from '../../../src/modules/enterprise_search/utils/utils';
import { appendAccounted } from '../../../src/libs/repositories/agent-harness/history-source/mongo';
import { IMessage } from '../../../src/modules/enterprise_search/types/conversation.interfaces';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { RunAdmissionResult } from '../../../src/modules/agent-harness/domain/admission';
import { parseContentRef } from '../../../src/modules/agent-harness/domain/run-content';
import { RunHistoryRef } from '../../../src/modules/agent-harness/domain/run-history';
import { JournalPayloadMissingError } from '../../../src/modules/agent-harness/domain/errors';
import {
  AgentAdmissionQuotaModel,
  AgentAdmissionReceiptModel,
  AgentCommandReceiptModel,
  AgentRunModel,
} from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import { AgentJournalPayloadModel } from '../../../src/libs/repositories/agent-harness/journal/schema/journal.schema';
import { AgentSessionErasureModel } from '../../../src/libs/repositories/agent-harness/session-erasure/schema/session-erasure.schema';
import {
  AgentWorkspaceKeyModel,
  AgentWorkspaceManifestModel,
  AgentWorkspaceWriterModel,
} from '../../../src/libs/repositories/agent-harness/workspace/schema/workspace.schema';
import { MongoRetentionStore } from '../../../src/libs/repositories/agent-harness/retention-store/mongo';
import { MongoPayloadStore } from '../../../src/libs/repositories/agent-harness/payload-store/mongo';
import { RunHistoryContent } from '../../../src/modules/agent-harness/application/run-history-content';
import {
  contractClock,
  ContractHarness,
  createContractHarness,
} from './support/execution-store-contract';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  mongoBackend,
  MongoBackendOptions,
  registerHarnessModels,
  resetHarnessMongo,
} from './support/harness-mongo';
import { HARNESS_ENABLED } from './support/rollout';
import { workerEvent } from './support/journal-fixtures';
import { testCipher } from './support/data-protection';
import { enqueue, eraseFully, seed, world } from './support/session-erasure-world';

registerHarnessModels([
  AgentSessionErasureModel,
  AgentWorkspaceManifestModel,
  AgentWorkspaceKeyModel,
  AgentWorkspaceWriterModel,
  Citation,
  Conversation,
  AgentConversation,
] as unknown as Array<Model<never>>);

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DAY = 86_400_000;
const CANARY = 'forty-two days of paid leave';

const ask = (query: string) => ({
  input: { query, recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
});

const bsonSize = (document: unknown): number =>
  mongoose.mongo.BSON.calculateObjectSize(document as Record<string, unknown>);

describe('PERF-B: a run history by reference on a MongoDB replica set', function () {
  this.timeout(180_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(resetHarnessMongo);

  const harness = (options: Omit<MongoBackendOptions, 'clock'> = {}): ContractHarness => {
    const clock = contractClock();
    const h = createContractHarness(mongoBackend({ ...options, clock }), clock);
    h.setRollout(V2);
    return h;
  };

  /** What the legacy stream would send Python as history for this conversation now. */
  const conversationHistory = async (sessionId: string) =>
    JSON.parse(JSON.stringify(formatPreviousConversations(await sessionMessages(sessionId)))) as Array<
      Record<string, unknown>
    >;

  /** The run claimed and completed with `reply`; returns its commit's receipt as stored. */
  const answer = async (h: ContractHarness, run: HarnessRun, reply: string) => {
    const claimed = await h.claim(run);
    return h.service.commit({
      commandId: `${run.runId}:answer`,
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'worker-a',
      attemptId: 'attempt-worker-a',
      fence: claimed.fence,
      events: [
        workerEvent(
          'x.pipeshub.answer.finalized',
          { outcome: 'answered', answer: reply, partTexts: [reply], citations: [], confidence: null },
          { critical: false },
        ),
      ],
      legacyEvents: [],
      transition: { status: 'completed', outcome: 'answer' },
      outbox: [],
    });
  };

  /** A session with one answered turn, and its second turn admitted. */
  const followUp = async (h: ContractHarness, reply = CANARY) => {
    const first = await h.admit(ask('How much leave do I get?'));
    await answer(h, first.run, reply);
    const expected = await conversationHistory(first.sessionId);
    const second = await h.admit({ sessionId: first.sessionId, ...ask('And sick days?') });
    return { first, second, expected };
  };

  const stored = async (run: Pick<HarnessRun, 'orgId' | 'runId'>) =>
    (await AgentRunModel.findOne({ orgId: run.orgId, runId: run.runId }).lean()) as unknown as Record<string, unknown> & {
      input: (Record<string, unknown> & { historyRef?: RunHistoryRef }) | null;
    };
  /** Where the stored run names its history. */
  const named = async (run: Pick<HarnessRun, 'orgId' | 'runId'>): Promise<RunHistoryRef> =>
    (await stored(run)).input!.historyRef!;

  const partIds = (ref: RunHistoryRef): string[] =>
    ref.parts.map((part) => parseContentRef(part)!.storeRef.replace(/^mongo:/, ''));

  it('PERFB-1: the run, its admission receipt and the admission answer name the history and hold none of it', async () => {
    const h = harness({ historyPartBytes: 64 });
    const { second, expected } = await followUp(h);
    expect(JSON.stringify(expected)).to.include(CANARY);

    const document = await stored(second.run);
    const history = await named(second.run);
    expect(document.input).to.not.have.property('previousConversations');
    expect(document, 'named inside the input, where an older Node cannot overlook it').to.not.have.property('history');
    expect(history.turns).to.equal(expected.length);
    expect(history.parts.length).to.be.greaterThan(1);
    expect(EJSON.stringify(document)).to.not.include(CANARY);

    expect(second.run.input).to.not.have.property('previousConversations');
    expect(second.run.input, 'the reference never leaves the store').to.not.have.property('historyRef');
    const receipts = await AgentAdmissionReceiptModel.find({ 'result.run.runId': second.run.runId }).lean();
    expect(receipts).to.have.length(1);
    expect(EJSON.stringify(receipts)).to.not.include(CANARY);
    expect(JSON.stringify(await h.service.get(second.run.orgId, second.run.runId))).to.not.include(CANARY);

    const parts = await AgentJournalPayloadModel.find({ _id: { $in: partIds(history) } }).lean();
    expect(parts).to.have.length(history.parts.length);
    for (const part of parts) {
      expect(part.owner).to.deep.equal({ sessionId: second.sessionId, runId: second.run.runId });
      expect(part.referenced, 'marked in the admission transaction').to.equal(true);
      expect(EJSON.stringify(part), 'sealed').to.not.include(CANARY);
    }
  });

  it('PERFB-1: a first turn has no history to keep: nothing is stored for it', async () => {
    const h = harness();
    const first = await h.admit(ask('How much leave do I get?'));
    const document = await stored(first.run);
    expect(document.input).to.not.have.property('historyRef');
    expect(document.input!['previousConversations']).to.deep.equal([]);
    expect(await AgentJournalPayloadModel.countDocuments({ 'owner.runId': first.run.runId })).to.equal(0);
  });

  it('PERFB-2: the claim gives the worker the history admission read; renew and commit answers carry none', async () => {
    const h = harness({ historyPartBytes: 64 });
    const { second, expected } = await followUp(h);

    const claimed = await h.claim(second.run);
    expect(claimed.input?.previousConversations).to.deep.equal(expected);
    expect(Object.keys(claimed.input!.previousConversations![1]!)).to.deep.equal(Object.keys(expected[1]!));
    expect(claimed.input).to.not.have.property('historyRef');

    const renewed = await h.service.renew({
      orgId: claimed.orgId,
      runId: claimed.runId,
      workerId: 'worker-a',
      attemptId: 'attempt-worker-a',
      fence: claimed.fence,
      leaseDurationMs: 1_000,
    });
    expect(renewed.input).to.not.have.property('previousConversations');
    expect(renewed.input).to.not.have.property('historyRef');
    const receipt = await h.service.commit({
      commandId: `${claimed.runId}:step`,
      orgId: claimed.orgId,
      runId: claimed.runId,
      workerId: 'worker-a',
      attemptId: 'attempt-worker-a',
      fence: claimed.fence,
      events: [workerEvent('turn.started', { turn: 1 })],
      legacyEvents: [],
      outbox: [],
    });
    expect(JSON.stringify(receipt)).to.not.include(CANARY);
    expect(receipt.run.input).to.not.have.property('historyRef');
    expect((await h.service.get(claimed.orgId, claimed.runId))?.input).to.not.have.property('historyRef');
    expect(EJSON.stringify(await AgentCommandReceiptModel.find({ runId: claimed.runId }).lean())).to.not.include(CANARY);
  });

  it('PERFB-2: what a later turn changed in the conversation does not reach an earlier run: the history is the admission snapshot', async () => {
    const h = harness();
    const { second, expected } = await followUp(h);
    await ChatSessionMessage.updateMany({ sessionId: second.sessionId, messageType: 'bot_response' }, { $set: { content: 'edited later' } });

    const claimed = await h.claim(second.run);
    expect(claimed.input?.previousConversations).to.deep.equal(expected);
  });

  it('PERFB-3: a run document written before this change is claimed with the history its input holds', async () => {
    const h = harness();
    const { second, expected } = await followUp(h);
    const before = await named(second.run);
    // The document as the previous build wrote it: the history inline, no reference.
    await AgentRunModel.updateOne(
      { runId: second.run.runId },
      { $set: { 'input.previousConversations': expected }, $unset: { 'input.historyRef': '' } },
    );
    await AgentJournalPayloadModel.deleteMany({ _id: { $in: partIds(before) } });

    const claimed = await h.claim(second.run);
    expect(claimed.input?.previousConversations).to.deep.equal(expected);
  });

  it('PERFB-21: with writing switched off a turn is stored as releases before PERF-B stored it, and both forms are claimed in one session', async () => {
    const off = harness({ historyByReference: false });
    const { first, second, expected } = await followUp(off);
    const document = await stored(second.run);
    expect(document.input).to.not.have.property('historyRef');
    // GAPS2-1: in the run document, as before PERF-B, but sealed with the org's key.
    expect(document.input).to.not.have.property('previousConversations');
    expect(document.input).to.have.property('historySealed');
    expect(JSON.stringify(document)).to.not.include(CANARY);
    expect(JSON.stringify(await AgentAdmissionReceiptModel.find({}).lean()), 'nor its admission receipt').to.not.include(CANARY);
    expect(await AgentJournalPayloadModel.countDocuments({ 'owner.runId': second.run.runId })).to.equal(0);
    const claimed = await off.claim(second.run);
    expect(claimed.input?.previousConversations).to.deep.equal(expected);
    await off.service.commit({
      commandId: `${second.run.runId}:end`,
      orgId: second.run.orgId,
      runId: second.run.runId,
      workerId: 'worker-a',
      attemptId: 'attempt-worker-a',
      fence: claimed.fence,
      events: [],
      legacyEvents: [],
      transition: { status: 'failed', errorType: 'tool_failed' },
      outbox: [],
    });

    // Switched back on (as another Node of the deployment would be): the next turn is by reference, read by either.
    const clock = contractClock();
    const on = createContractHarness(mongoBackend({ clock }), clock);
    on.setRollout(V2);
    const third = await on.admit({ ...off.tenant, sessionId: first.sessionId, ...ask('And parental leave?') });
    expect((await stored(third.run)).input).to.have.property('historyRef');
    expect((await off.claim(third.run)).input?.previousConversations?.slice(0, expected.length)).to.deep.equal(expected);
  });

  it('PERFB-24: a turn with a history counts once against its user and its org, as a first turn does', async () => {
    const h = harness({ historyPartBytes: 64 });
    const windows = async () =>
      Object.fromEntries(
        (await AgentAdmissionQuotaModel.find({}).lean()).map((window) => [
          window._id.includes('"user"') ? 'user' : 'org',
          (window as unknown as { count: number }).count,
        ]),
      );
    const first = await h.admit(ask('How much leave do I get?'));
    await answer(h, first.run, CANARY);
    expect(await windows()).to.deep.equal({ org: 1, user: 1 });

    const second = await h.admit({ sessionId: first.sessionId, ...ask('And sick days?') });

    expect((await named(second.run)).parts.length, 'several parts stored and marked').to.be.greaterThan(1);
    expect(await windows(), 'one admission, however many operations its transaction made').to.deep.equal({ org: 2, user: 2 });
  });

  it('PERFB-4: a session whose history is past MongoDB 16 MB document limit admits a turn and its claim reads all of it', async () => {
    const h = harness();
    const first = await h.admit(ask('Start'));
    await answer(h, first.run, 'A short answer.');
    // Turns a long tool-heavy session leaves, written as the harness writes them (the ledger covers them).
    const megabyte = 'r'.repeat(1024 * 1024);
    const bulk = Array.from({ length: 18 }, (_unused, index) => [
      { messageType: 'user_query', content: `question ${String(index)}` },
      { messageType: 'bot_response', content: `answer ${String(index)} ${megabyte}`, citations: [] },
    ]).flat() as unknown as IMessage[];
    await appendAccounted({ _id: first.sessionId }, null, bulk);
    const expected = await conversationHistory(first.sessionId);
    const size = Buffer.byteLength(JSON.stringify(expected));
    expect(size).to.be.greaterThan(16 * 1024 * 1024);

    const next = await h.admit({ sessionId: first.sessionId, ...ask('Go on') });

    expect(next.imported, 'nothing to import: the journal accounts for every turn').to.equal(undefined);
    const history = await named(next.run);
    expect(history).to.include({ size, turns: expected.length });
    expect(history.parts.length).to.be.greaterThan(4);
    expect(bsonSize(await stored(next.run))).to.be.lessThan(16 * 1024);
    const claimed = await h.claim(next.run);
    expect(claimed.input?.previousConversations).to.have.length(expected.length);
    expect(claimed.input?.previousConversations).to.deep.equal(expected);
  });

  it('PERFB-5: run documents and receipts do not grow with the session history', async () => {
    const sizes = async (reply: string) => {
      const h = harness();
      const { second } = await followUp(h, reply);
      const receipt = await answer(h, second.run, 'ok');
      expect(receipt.run.status).to.equal('completed');
      return {
        run: bsonSize(await stored(second.run)),
        admission: bsonSize(await AgentAdmissionReceiptModel.findOne({ 'result.run.runId': second.run.runId }).lean()),
        commit: bsonSize(await AgentCommandReceiptModel.findOne({ runId: second.run.runId }).lean()),
      };
    };
    const short = await sizes('A short answer.');
    await resetHarnessMongo();
    const long = await sizes('A long answer. '.repeat(40_000));

    // The reference's own digits (a size, a count) are all that may differ.
    for (const kind of ['run', 'admission', 'commit'] as const) {
      expect(Math.abs(long[kind] - short[kind]), `${kind} document`).to.be.lessThan(64);
    }
  });

  it('PERFB-6: erasing the session deletes the history and what named it', async () => {
    const w = world();
    const s = await seed(w, { running: true });
    const history = await named({ orgId: s.orgId, runId: s.liveRunId! });
    const ids = partIds(history);
    expect(ids.length).to.be.greaterThan(0);
    const reader = new RunHistoryContent(testCipher(), new MongoPayloadStore());
    const run = { orgId: s.orgId, sessionId: s.sessionId, runId: s.liveRunId! };
    expect(JSON.stringify(await reader.read(run, history))).to.include(s.canary);

    await enqueue(w, s);
    await eraseFully(w, s);

    expect(await AgentJournalPayloadModel.countDocuments({ _id: { $in: ids } })).to.equal(0);
    const erased = await stored({ orgId: s.orgId, runId: s.liveRunId! });
    expect(erased.input, 'the reference went with the input').to.equal(null);
    const subject = await w.harness.backend.executionStore.readClaimSubject(s.orgId, s.liveRunId!);
    expect(subject?.history).to.equal(null);
    const gone = await reader.read(run, history).then(
      () => null,
      (error: unknown) => error,
    );
    expect(gone).to.be.instanceOf(JournalPayloadMissingError);
  });

  it('PERFB-7: retention never collects a history a run names, while the run lives or after it ended', async () => {
    const h = harness();
    const { second, expected } = await followUp(h);
    const ids = partIds(await named(second.run));
    const retention = new MongoRetentionStore();
    const old = new Date(Date.now() - 2 * DAY);
    await AgentJournalPayloadModel.updateMany({ _id: { $in: ids } }, { $set: { createdAt: old } });

    await retention.collectPayloads(Date.now() - DAY, 1_000);
    expect(await AgentJournalPayloadModel.countDocuments({ _id: { $in: ids } }), 'the run is queued').to.equal(ids.length);

    await answer(h, second.run, 'ok');
    await retention.collectPayloads(Date.now() + DAY, 1_000);
    expect(await AgentJournalPayloadModel.countDocuments({ _id: { $in: ids } }), 'the run ended').to.equal(ids.length);

    // A third turn reads its own snapshot; the second run's is still whole.
    const third = await h.admit({ sessionId: second.sessionId, ...ask('And parental leave?') });
    const claimed = await h.claim(third.run);
    expect(claimed.input?.previousConversations?.slice(0, expected.length)).to.deep.equal(expected);
  });

  it('PERFB-7: an admission that aborts leaves no history behind', async () => {
    const h = harness();
    const { second } = await followUp(h);
    await answer(h, second.run, 'ok');
    const before = await AgentJournalPayloadModel.countDocuments({});
    // A run id already taken: the transaction stores the history, then fails on the run.
    const refused: RunAdmissionResult | Error = await h
      .admit({ sessionId: second.sessionId, ...ask('Again?') }, second.run.runId)
      .catch((error: Error) => error);

    expect(refused).to.be.instanceOf(Error);
    expect(await AgentJournalPayloadModel.countDocuments({})).to.equal(before);
  });
});
