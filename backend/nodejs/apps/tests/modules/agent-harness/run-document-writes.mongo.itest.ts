/**
 * PERF-B (QA22-F2 item 2) on a MongoDB replica set (run as
 * execution-store.mongo.itest.ts says): what each kind of commit sends
 * MongoDB for the run document, read from the server's own profile of the
 * operations. A commit names the fields it changes; it never sends the
 * document, and never a field admission fixed.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose from 'mongoose';
// bson is the driver's own dependency (nested under mongodb), so EJSON comes through the driver, a direct dependency.
import { BSON } from 'mongodb';

const { EJSON } = BSON;
import { HarnessRun, RunTransition } from '../../../src/modules/agent-harness/domain/execution';
import { JournalEventInput } from '../../../src/modules/agent-harness/domain/journal';
import { AgentRunModel } from '../../../src/libs/repositories/agent-harness/execution/schema/execution.schema';
import {
  contractClock,
  ContractHarness,
  createContractHarness,
} from './support/execution-store-contract';
import {
  connectHarnessMongo,
  disconnectHarnessMongo,
  mongoBackend,
  resetHarnessMongo,
} from './support/harness-mongo';
import { HARNESS_ENABLED } from './support/rollout';
import { workerEvent } from './support/journal-fixtures';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const WORKER = { workerId: 'worker-a', attemptId: 'attempt-worker-a' };

/** What a run's life changes on its document; everything else is admission's and is never written again. */
const MUTABLE = new Set([
  'status',
  'outcome',
  'revision',
  'fence',
  'attemptId',
  'workerId',
  'leaseExpiresAt',
  'attempts',
  'claimedFrom',
  'notBefore',
  'checkpoint',
  'checkpointRef',
  'profile',
  'suspendedFor',
  // FIX-P1-F3: a wait for a person holds the working deadline and gives it back when they answer.
  'absoluteDeadlineAt',
  'workLeftMs',
  // WAITCHILD: a child's wait for a person is recorded on, and holds, the parents joined on it.
  'waitingChildren',
  'activeSessionKey',
  'openInteractions',
  'pendingInputs',
  'inputSeq',
  'requires',
  'attemptFeatures',
  'waitingForWorker',
  'updatedAt',
  'label',
  'followups',
  'nextRunsQueued',
  'children',
]);
/** Written once, by the first claim's transaction: the pin and the schema version it needs. */
const FIRST_CLAIM_ONLY = new Set(['pinned', 'requires.schemaVersion']);
const ADMISSION_FIELDS = ['input', 'agentDefinition', 'engineProfile', 'modelAccess', 'via', 'audience', 'memory', 'payloadHash', 'idempotencyKey', 'userMessageKey', 'createdAt', 'harnessMajor'];

interface RunWrite {
  /** `update` or `findAndModify`. */
  op: string;
  /** Top-level paths the write names; null when it sent a replacement document. */
  fields: string[] | null;
}

/** The fields an update specification names: a pipeline's `$set`/`$unset` stages, or update operators. */
function namedFields(update: unknown): string[] | null {
  const stages = Array.isArray(update) ? (update as Array<Record<string, unknown>>) : [update as Record<string, unknown>];
  const fields: string[] = [];
  for (const stage of stages) {
    for (const [operator, argument] of Object.entries(stage)) {
      if (!operator.startsWith('$')) return null;
      if (Array.isArray(argument)) fields.push(...(argument as string[]));
      else fields.push(...Object.keys(argument as Record<string, unknown>));
    }
  }
  return fields;
}

describe('PERFB-11: every write of a run document names the fields it changes', function () {
  this.timeout(120_000);
  let h: ContractHarness;

  before(connectHarnessMongo);
  after(async () => {
    const db = mongoose.connection.db!;
    await db.command({ profile: 0 });
    await db.collection('system.profile').drop().catch(() => undefined);
    await disconnectHarnessMongo();
  });
  beforeEach(async () => {
    await resetHarnessMongo();
    const clock = contractClock();
    h = createContractHarness(mongoBackend({ clock }), clock);
    h.setRollout(V2);
  });

  /** The writes MongoDB ran on the run collection while `work` ran, as its profiler recorded them. */
  const writesDuring = async (work: () => Promise<unknown>): Promise<RunWrite[]> => {
    const db = mongoose.connection.db!;
    await db.command({ profile: 0 });
    await db.collection('system.profile').drop().catch(() => undefined);
    await db.command({ profile: 2 });
    try {
      await work();
    } finally {
      await db.command({ profile: 0 });
    }
    const namespace = `${db.databaseName}.${AgentRunModel.collection.collectionName}`;
    const profiled = await db.collection('system.profile').find({ ns: namespace }).sort({ ts: 1 }).toArray();
    const writes: RunWrite[] = [];
    for (const entry of profiled) {
      const command = entry['command'] as Record<string, unknown>;
      if (entry['op'] === 'update') writes.push({ op: 'update', fields: namedFields(command['u']) });
      else if (command['findAndModify'] !== undefined) writes.push({ op: 'findAndModify', fields: namedFields(command['update']) });
      else if (entry['op'] === 'insert' || entry['op'] === 'remove') writes.push({ op: String(entry['op']), fields: null });
    }
    return writes;
  };

  const commit = (run: HarnessRun, commandId: string, events: JournalEventInput[], transition?: RunTransition) =>
    h.service.commit({
      commandId,
      orgId: run.orgId,
      runId: run.runId,
      ...WORKER,
      fence: run.fence,
      events,
      legacyEvents: [],
      outbox: [],
      ...(transition !== undefined ? { transition } : {}),
    });
  const claim = (run: Pick<HarnessRun, 'orgId' | 'runId'>) =>
    h.service.claim({ orgId: run.orgId, runId: run.runId, ...WORKER, leaseDurationMs: 30_000 }).then((claimed) => claimed.run);
  const admissionFields = async (run: Pick<HarnessRun, 'orgId' | 'runId'>): Promise<string> => {
    const document = (await AgentRunModel.findOne({ orgId: run.orgId, runId: run.runId }).lean()) as unknown as Record<string, unknown>;
    return EJSON.stringify(Object.fromEntries(ADMISSION_FIELDS.map((field) => [field, document[field]])));
  };

  /** A second turn of a session, so the run has a history and an input worth not rewriting. */
  const admitted = async () => {
    const first = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user' });
    const claimed = await claim(first.run);
    await commit(
      claimed,
      'first-answer',
      [workerEvent('x.pipeshub.answer.finalized', { outcome: 'answered', answer: 'First.', partTexts: ['First.'], citations: [], confidence: null }, { critical: false })],
      { status: 'completed', outcome: 'answer' },
    );
    return (await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user', sessionId: first.sessionId })).run;
  };

  const KINDS: Array<[kind: string, act: (run: HarnessRun) => Promise<unknown>]> = [
    ['the first claim', (run) => claim(run)],
    ['a renewal', async (run) => {
      const claimed = await claim(run);
      await h.service.renew({ orgId: run.orgId, runId: run.runId, ...WORKER, fence: claimed.fence, leaseDurationMs: 30_000 });
    }],
    ['a commit of journal events', async (run) => {
      const claimed = await claim(run);
      await commit(claimed, 'events', [workerEvent('turn.started', { turn: 1 })]);
    }],
    ['a suspension for approval and its resolution', async (run) => {
      const claimed = await claim(run);
      const suspended = await commit(
        claimed,
        'suspend',
        [workerEvent('interaction.requested', { interactionId: 'ix-1', kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` })],
        { status: 'waiting_approval', detail: { kind: 'suspend', reason: 'approval', interactionId: 'ix-1' } },
      );
      await h.service.control({
        commandId: 'resolve',
        dispatchCommandId: 'resolve:dispatch',
        orgId: run.orgId,
        runId: run.runId,
        expectedRevision: suspended.run.revision,
        action: 'resolve',
        by: 'p_approver',
        resolution: { interactionId: 'ix-1', decision: 'approved', channel: 'web' },
      });
      await claim(run);
    }],
    ['a yield and the claim after it', async (run) => {
      const claimed = await claim(run);
      await commit(claimed, 'yield', [], {
        status: 'queued',
        detail: { kind: 'yield', reason: 'llm_backoff', notBefore: new Date(h.clock.now()).toISOString(), checkpointRef: 'ckpt-1' },
      });
      await claim(run);
    }],
    ['a release', async (run) => {
      const claimed = await claim(run);
      await h.service.release({ orgId: run.orgId, runId: run.runId, ...WORKER, fence: claimed.fence, reason: 'fold_incompatible' });
    }],
    ['a message steering the run', async (run) => {
      await claim(run);
      await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user', sessionId: run.sessionId, idempotencyKey: 'steer-1' });
    }],
    ['a cancel request and the cancelled ending', async (run) => {
      const claimed = await claim(run);
      const cancelling = await h.service.control({
        commandId: 'cancel',
        dispatchCommandId: 'cancel:dispatch',
        orgId: run.orgId,
        runId: run.runId,
        expectedRevision: claimed.revision,
        action: 'cancel',
      });
      await commit(cancelling.run, 'stopped', [], { status: 'cancelled' });
    }],
    ['the completing commit, its transcript and its follow-ups', async (run) => {
      const claimed = await claim(run);
      await commit(
        claimed,
        'answer',
        [workerEvent('x.pipeshub.answer.finalized', { outcome: 'answered', answer: 'Second.', partTexts: ['Second.'], citations: [], confidence: null }, { critical: false })],
        { status: 'completed', outcome: 'answer' },
      );
      await h.service.storeFollowups(run.orgId, run.runId, [{ text: 'And then?' }]);
    }],
    ['a failing commit', async (run) => {
      const claimed = await claim(run);
      await commit(claimed, 'fail', [], { status: 'failed', errorType: 'tool_failed' });
    }],
    ['a lease that lapsed, swept back to the queue', async (run) => {
      await claim(run);
      h.setNow(h.clock.now() + 31_000);
      expect(await h.service.sweep(10)).to.equal(1);
    }],
    ['a deadline that passed, swept to failed', async (run) => {
      h.setNow(run.absoluteDeadlineAt + 1);
      expect(await h.service.sweep(10)).to.equal(1);
    }],
  ];

  for (const [kind, act] of KINDS) {
    it(`${kind}: only fields a run's life changes, by name, and admission's fields are untouched`, async () => {
      const run = await admitted();
      const before = await admissionFields(run);
      expect(before, 'a run with a history to leave alone').to.include('historyRef');

      const writes = await writesDuring(() => act(run));

      expect(writes.length, 'the run document was written').to.be.greaterThan(0);
      for (const write of writes) {
        expect(write.op, 'no insert or delete of a run').to.be.oneOf(['update', 'findAndModify']);
        expect(write.fields, 'an update that names fields, never a replacement document').to.not.equal(null);
        for (const field of write.fields ?? []) {
          const allowed = MUTABLE.has(field) || FIRST_CLAIM_ONLY.has(field);
          expect(allowed, `${kind} writes ${field}`).to.equal(true);
        }
      }
      expect(await admissionFields(run)).to.equal(before);
    });
  }

  it('the pin is written by the first claim and by no later one', async () => {
    const run = await admitted();
    let claimed!: HarnessRun;
    const first = await writesDuring(async () => {
      claimed = await claim(run);
    });
    expect(first.flatMap((write) => write.fields ?? []).filter((field) => FIRST_CLAIM_ONLY.has(field)).sort()).to.deep.equal([
      'pinned',
      'requires.schemaVersion',
    ]);
    await h.service.release({ orgId: run.orgId, runId: run.runId, ...WORKER, fence: claimed.fence, reason: 'fold_incompatible' });

    const writes = await writesDuring(() => claim(run));

    expect(writes.flatMap((write) => write.fields ?? []).filter((field) => FIRST_CLAIM_ONLY.has(field))).to.deep.equal([]);
  });
});
