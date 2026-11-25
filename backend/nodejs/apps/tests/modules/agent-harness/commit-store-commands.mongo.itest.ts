/**
 * PERF-D (PERFC-F1) on a MongoDB replica set (run as
 * execution-store.mongo.itest.ts says): the store commands one commit costs,
 * read from the server's own profile. Each command is event-loop time on the
 * one Node process every request shares, so the counts are a ratchet: a
 * commit may send fewer, never more without this file saying why.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose from 'mongoose';
import type { CommandStartedEvent } from 'mongodb';
import {
  AUDIT_LINK_DEFER_MS,
  auditLinkDefer,
  auditLinksIdle,
  MongoAuditChainStore,
} from '../../../src/libs/repositories/agent-harness/audit/mongo';
import { AgentAuditPendingModel, AgentAuditRecordModel } from '../../../src/libs/repositories/agent-harness/audit/schema/audit.schema';
import { ChainWorkspaceDownloadAudit } from '../../../src/modules/agent-harness/adapters/chain-workspace-download-audit';
import { inMongoTransaction } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import {
  AgentJournalPayloadModel,
  SessionEventModel,
  SessionStreamCounterModel,
} from '../../../src/libs/repositories/agent-harness/journal/schema/journal.schema';
import { HarnessRun, RunTransition } from '../../../src/modules/agent-harness/domain/execution';
import { JournalEventInput } from '../../../src/modules/agent-harness/domain/journal';
import { contractClock, ContractHarness, createContractHarness } from './support/execution-store-contract';
import { connectHarnessMongo, disconnectHarnessMongo, mongoBackend, resetHarnessMongo } from './support/harness-mongo';
import { HARNESS_ENABLED } from './support/rollout';
import { workerEvent } from './support/journal-fixtures';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const WORKER = { workerId: 'worker-a', attemptId: 'attempt-worker-a' };
const ANSWER = workerEvent(
  'x.pipeshub.answer.finalized',
  { outcome: 'answered', answer: 'Done.', partTexts: ['Done.'], citations: [], confidence: null },
  { critical: false },
);

/** `<reads|writes> <collection>` to how many commands. */
type Commands = Record<string, number>;

const READS = new Set(['find', 'aggregate', 'count', 'distinct', 'getMore']);
const WRITES = new Set(['insert', 'update', 'delete', 'findAndModify']);
/** A transaction's inserts that nothing in it reads back, sent together (PERF-E2). */
const BATCH = 'writes (one bulkWrite)';

const settle = async (): Promise<void> => {
  for (let i = 0; i < 20; i += 1) await new Promise((resolve) => setImmediate(resolve));
};

/**
 * The audit link (PERF-C): the org's pending appends read (once to see there
 * are some, once in its transaction), the head claimed and moved, the records
 * written, the appends removed. It waits `auditLinkDefer.ms` after a commit
 * and takes every commit of the org since (PERF-E3), so it is no request's.
 */
const LINK: Commands = {
  'reads agentAuditPending': 2,
  'writes agentAuditPending': 1,
  'writes agentAuditHeads': 2,
  'writes agentAuditRecords': 1,
};

/**
 * The run: the attempt check, the transaction, and two for the labels of the
 * turns its model saw; one write, the label with the ending. The journal: the
 * tail the transcript is projected from, then the receipt; the worker's events
 * and Node's `run.completed` are two appends of one range, allocated once
 * (PERF-E3). The conversation is one read and one write of its session
 * (PERF-E2; the legacy writer sent nine). The audit entry commits with the
 * journal; its link is the org's, not this commit's (`LINK`). The
 * transaction's writes that nothing in it reads back
 * (the events of both appends, the audit entry, the answer and the session's
 * update, the receipt) are one command (PERF-E2, PERF-E3).
 */
const ENDING: Commands = {
  'reads agentRuns': 4,
  'writes agentRuns': 1,
  // The replay check; the transaction reads the receipt with the run (WP88-PF05-5).
  'reads agentCommandReceipts': 1,
  'reads sessionEvents': 2,
  'writes sessionStreamCounters': 1,
  [BATCH]: 1,
  'reads chatSessions': 1,
};

/**
 * The actor's and the org's quota holds outside the transaction, a reserve and a
 * settle each (DOS-MONGO-1: no transaction writes a quota document, so a
 * person's admissions never abort each other there); in it the receipt, the
 * session's active run, its counter, the actor's and the org's active runs, the conversation's session bound (one write that returns its
 * counter and ledger, PERF-E3), its messages read as history, and the journal.
 * The user's turn, the session's update with it (counter and the import's
 * ledger), the history stored by reference and its reference mark (PERF-E3),
 * and the run's inserts are the one batch.
 */
const ADMISSION: Commands = {
  'writes agentAdmissionQuotas': 4,
  'reads agentAdmissionReceipts': 1,
  // 3 since WP-64: a reply may answer the conversation's last run, and the asking run's
  // gates and budget then hold it (G-13), so the latest root run is read once, on the (orgId, sessionId) index.
  'reads agentRuns': 3,
  'reads agentEventCounters': 1,
  'writes agentEventCounters': 1,
  'writes chatSessions': 1,
  'reads chatSessionMessages': 1,
  'writes sessionStreamCounters': 1,
  [BATCH]: 1,
};

/** A new conversation: no bind, no history, no counter; the session itself joins the batch (PERF-E3). */
const NEW_CONVERSATION: Commands = {
  'writes agentAdmissionQuotas': 4,
  'reads agentAdmissionReceipts': 1,
  'reads agentRuns': 1,
  'writes agentEventCounters': 1,
  'writes sessionStreamCounters': 1,
  [BATCH]: 1,
};

describe('PERFD-14: the store commands a commit sends', function () {
  this.timeout(120_000);
  let h: ContractHarness;

  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  afterEach(() => {
    auditLinkDefer.ms = AUDIT_LINK_DEFER_MS;
  });
  beforeEach(async () => {
    await resetHarnessMongo();
    const clock = contractClock();
    h = createContractHarness(mongoBackend({ clock }), clock);
    h.setRollout(V2);
  });

  /**
   * The store commands Node sent while `work` ran and what it left running ended, as the rig counts them (the
   * client's own command events): a `bulkWrite` is one command, whatever collections it writes.
   */
  const commandsDuring = async (work: () => Promise<unknown>): Promise<Commands> => {
    const commands: Commands = {};
    const count = (event: CommandStartedEvent): void => {
      const target = event.command[event.commandName] as unknown;
      const key =
        event.commandName === 'bulkWrite'
          ? BATCH
          : typeof target !== 'string'
            ? null
            : READS.has(event.commandName)
              ? `reads ${target}`
              : WRITES.has(event.commandName)
                ? `writes ${target}`
                : null;
      if (key !== null) commands[key] = (commands[key] ?? 0) + 1;
    };
    const client = mongoose.connection.getClient();
    client.on('commandStarted', count);
    try {
      await work();
      await settle();
    } finally {
      client.off('commandStarted', count);
    }
    return commands;
  };

  /** A commit as the worker's route makes it: the attempt check's read of the run, the commit, its receipt's events. */
  const commit = async (run: HarnessRun, commandId: string, events: JournalEventInput[], transition?: RunTransition) => {
    const read = await h.backend.executionStore.get(run.orgId, run.runId);
    const receipt = await h.service.commit(
      {
        commandId,
        orgId: run.orgId,
        runId: run.runId,
        ...WORKER,
        fence: run.fence,
        events,
        legacyEvents: [],
        outbox: [],
        ...(transition !== undefined ? { transition } : {}),
      },
      read,
    );
    await h.service.receiptEvents(run.orgId, receipt);
    return receipt;
  };

  const claimed = async (): Promise<HarnessRun> => {
    const { run } = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user' });
    const claim = await h.service.claim({ orgId: run.orgId, runId: run.runId, ...WORKER, leaseDurationMs: 30_000 });
    await commit(claim.run, 'first', [workerEvent('turn.started', { turn: 1 })]);
    await settle();
    await auditLinksIdle();
    return claim.run;
  };

  const within = (commands: Commands, ratchet: Commands): void => {
    for (const [key, sent] of Object.entries(commands)) {
      expect(sent, `${key} (all: ${JSON.stringify(commands)})`).to.be.at.most(ratchet[key] ?? 0);
    }
  };

  it('a step commit: the run once for its attempt check and once in its transaction with its receipt (WP88-PF05-5), the receipt once before it, its events once', async () => {
    const run = await claimed();
    const commands = await commandsDuring(() => commit(run, 'step', [workerEvent('turn.started', { turn: 2 })]));
    within(commands, {
      'reads agentRuns': 2,
      'reads agentCommandReceipts': 1,
      'reads sessionEvents': 1,
      'writes agentRuns': 1,
      'writes sessionStreamCounters': 1,
      [BATCH]: 1,
    });
    expect(commands['reads sessionEvents'], 'the receipt and the read-ahead are one read').to.equal(1);
    expect(commands[BATCH], 'PERFE2-1: the events and the receipt are one command').to.equal(1);
  });

  it('an admission into a conversation that has a turn', async () => {
    const first = await claimed();
    await commit(first, 'answer', [ANSWER], { status: 'completed', outcome: 'answer' });
    await settle();
    await auditLinksIdle();
    const commands = await commandsDuring(() =>
      h.admit({ agentKey: 'agent-1', authorization: 'Bearer user', sessionId: first.sessionId }),
    );
    within(commands, ADMISSION);
    expect(commands[BATCH], 'PERFE2-1: the run, its events, audit entry, outbox row and receipt are one command').to.equal(1);
    expect(
      [commands['reads chatSessions'], commands['writes chatSessions'], commands['writes agentJournalPayloads']],
      'PERFE3-5: the session bound once and never read; the stored history and its mark go with the batch',
    ).to.deep.equal([undefined, 1, undefined]);
    const history = await AgentJournalPayloadModel.find({ 'owner.sessionId': first.sessionId }).lean();
    expect(history.length, 'the history is stored by reference').to.be.greaterThan(0);
    expect(history.map((payload) => payload.referenced), 'and marked referenced').to.deep.equal(history.map(() => true));
  });

  it('an admission into a new conversation (what the rig admits)', async () => {
    const commands = await commandsDuring(() => h.admit({ agentKey: 'agent-1', authorization: 'Bearer user' }));
    within(commands, NEW_CONVERSATION);
    expect(
      [commands['reads chatSessions'], commands['writes chatSessions']],
      'PERFE3-5: the session and its first turn are written with the batch, never read',
    ).to.deep.equal([undefined, undefined]);
  });

  it('the commit that ends a run with its answer', async () => {
    const run = await claimed();
    const commands = await commandsDuring(() => commit(run, 'answer', [ANSWER], { status: 'completed', outcome: 'answer' }));
    within(commands, ENDING);
    expect(commands['writes agentRuns'], 'the ending and the turn\'s label are one write').to.equal(1);
    expect(
      [commands['reads chatSessions'], commands['writes chatSessions'], commands['reads chatSessionMessages']],
      'PERFE2-8: the answer is one read of the session, its write in the batch (PERF-E3); nothing reads the conversation back',
    ).to.deep.equal([1, undefined, undefined]);
    expect(commands['writes sessionStreamCounters'], 'PERFE3-7: both appends of the ending are allocated by one write').to.equal(1);
    const seqs = (await SessionEventModel.find({ orgId: run.orgId, streamId: run.sessionId }).sort({ seq: 1 }).lean()).map((event) => event.seq);
    const counter = await SessionStreamCounterModel.findOne({ orgId: run.orgId, streamId: run.sessionId }).lean();
    expect(seqs, 'the stream has no hole').to.deep.equal(seqs.map((_, index) => index + 1));
    expect(counter?.lastSeq, 'the counter is where the last append left it').to.equal(seqs.at(-1));
    const ending = await h.backend.executionStore.readEnding(run.orgId, run.runId);
    expect(ending?.run.status).to.equal('completed');
    expect(ending?.label, 'the label is stored with the ending').to.not.equal(null);
  });

  it('PERFE3-1: an org\'s ending commit and admission of one moment are linked onto its chain by one link', async () => {
    const run = await claimed();
    // Held for the test, so a slow host cannot start the link between the two.
    auditLinkDefer.ms = 600_000;
    const sent = await commandsDuring(async () => {
      await commit(run, 'answer', [ANSWER], { status: 'completed', outcome: 'answer' });
      await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user', sessionId: run.sessionId });
    });
    expect(Object.keys(sent).filter((key) => key.includes('agentAudit')), 'nothing linked while the link waits').to.deep.equal([]);
    expect(await AgentAuditPendingModel.countDocuments({ orgId: run.orgId })).to.equal(2);
    const link = await commandsDuring(auditLinksIdle);
    expect(link, 'one link for the two').to.deep.equal(LINK);
    expect(await AgentAuditPendingModel.countDocuments({ orgId: run.orgId })).to.equal(0);
  });

  it("GAPS2-2: a workspace download's audit is one insert, linked by the org's one link with the commits of that moment", async () => {
    const run = await claimed();
    const downloads = new ChainWorkspaceDownloadAudit(new MongoAuditChainStore(), inMongoTransaction);
    auditLinkDefer.ms = 600_000;
    const download = {
      type: 'workspace.downloaded' as const,
      orgId: run.orgId,
      actorId: run.actorId,
      sessionId: run.sessionId,
      revision: 1,
      target: 'archive' as const,
      files: 2,
      bytes: 10,
    };
    const audited = await commandsDuring(() => downloads.record(download));
    expect(audited, 'the entry alone, in its transaction').to.deep.equal({ 'writes agentAuditPending': 1 });
    await commit(run, 'answer', [ANSWER], { status: 'completed', outcome: 'answer' });
    await downloads.record({ ...download, target: 'file', path: 'out/a.txt', files: 1, bytes: 4 });
    expect(await AgentAuditPendingModel.countDocuments({ orgId: run.orgId })).to.equal(3);
    const link = await commandsDuring(auditLinksIdle);
    expect(link, 'one link for the downloads and the commit').to.deep.equal(LINK);
    const linked = await AgentAuditRecordModel.find({ orgId: run.orgId, kind: 'workspace.downloaded' }).sort({ seq: 1 }).lean();
    expect(linked.map((record) => record.sessionId)).to.deep.equal([run.sessionId, run.sessionId]);
  });
});

