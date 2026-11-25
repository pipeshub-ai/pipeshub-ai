import type { Tx } from '../../../src/libs/repositories/uow';
/**
 * WP56-OUTBOX on a MongoDB replica set: the commit that makes a harness-v2 run wait for a person writes its
 * approver-notice outbox entry in the same transaction, once, keyed by the run's revision; a commit that does not
 * start the wait writes none, and an entry that cannot be written fails the commit whole. The outbox itself is an edition's; here
 * it is a recording one that writes in the transaction it is handed, so a write outside it would show.
 * Run as execution-store.mongo.itest.ts says.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose from 'mongoose';
import { connectHarnessMongo, disconnectHarnessMongo, mongoBackend, resetHarnessMongo } from './support/harness-mongo';
import { contractClock, ContractHarness, createContractHarness, START } from './support/execution-store-contract';
import { HARNESS_ENABLED } from './support/rollout';
import { workerEvent } from './support/journal-fixtures';
import { asMongoSession } from '../../../src/libs/repositories/agent-harness/durable-transaction/mongo';
import { approverNoticeId } from '../../../src/modules/agent-harness/domain/approval-links';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import type { ApproverNoticeOutbox } from '../../../src/modules/agent-harness/ports/approval-links';

const PROBE = 'pxhApproverNoticeProbe';
const INTERACTION = 'ix.approve.t2.0';

interface Enqueued {
  id: string;
  orgId: string;
  runId: string;
  createdAt: number;
  inTransaction: boolean;
}

class RecordingNoticeOutbox implements ApproverNoticeOutbox<Tx> {
  readonly enqueued: Enqueued[] = [];
  failAfterWrite = false;

  async enqueue(notice: { id: string; orgId: string; runId: string; createdAt: number }, tx: Tx): Promise<void> {
    const session = asMongoSession(tx);
    this.enqueued.push({ ...notice, inTransaction: session.inTransaction() });
    await mongoose.connection.collection(PROBE).insertOne({ _id: notice.id as never, runId: notice.runId }, { session });
    if (this.failAfterWrite) throw new Error('outbox down');
  }
}

describe('approver notices written by the commit that makes a run wait, on a MongoDB replica set', function () {
  this.timeout(60_000);
  before(connectHarnessMongo);
  after(disconnectHarnessMongo);

  let h: ContractHarness;
  let outbox: RecordingNoticeOutbox;
  beforeEach(async () => {
    await resetHarnessMongo();
    await mongoose.connection.collection(PROBE).deleteMany({});
    outbox = new RecordingNoticeOutbox();
    const clock = contractClock();
    h = createContractHarness(mongoBackend({ clock, approverNotices: outbox }), clock);
  });

  const commit = (run: HarnessRun, commandId: string, suspend: boolean, ask = false) =>
    h.service.commit({
      commandId,
      orgId: run.orgId,
      runId: run.runId,
      workerId: 'worker-a',
      attemptId: 'attempt-worker-a',
      fence: run.fence,
      events: ask
        ? [workerEvent('interaction.requested', { interactionId: INTERACTION, kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` })]
        : [],
      legacyEvents: [],
      ...(suspend
        ? { transition: { status: 'waiting_approval' as const, detail: { kind: 'suspend' as const, reason: 'approval' as const, interactionId: INTERACTION } } }
        : {}),
      outbox: [],
    });

  const claimed = async (): Promise<HarnessRun> => {
    const admitted = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user' });
    return h.claim(admitted.run, 'worker-a', 60_000);
  };

  it('the suspend commit of a v2 run writes one entry, in its transaction, for the revision it commits', async () => {
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 as const });
    const run = await claimed();
    h.setNow(START + 500);
    await commit(run, 'worker-a:ask', false, true);
    expect(outbox.enqueued, 'an ask alone does not start the wait').to.deep.equal([]);

    const suspended = (await commit(run, 'worker-a:suspend', true)).run;

    expect(suspended.status).to.equal('waiting_approval');
    expect(outbox.enqueued).to.deep.equal([
      {
        id: approverNoticeId(run.orgId, run.runId, suspended.revision),
        orgId: run.orgId,
        runId: run.runId,
        createdAt: START + 500,
        inTransaction: true,
      },
    ]);
    expect(await mongoose.connection.collection(PROBE).countDocuments({ runId: run.runId })).to.equal(1);
    // The same commit retried is answered from its receipt: no second entry.
    await commit(run, 'worker-a:suspend', true);
    expect(outbox.enqueued).to.have.length(1);
  });

  it('an entry that cannot be written fails the commit: the run does not wait and nothing of it is kept', async () => {
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 as const });
    const run = await claimed();
    await commit(run, 'worker-a:ask', false, true);
    outbox.failAfterWrite = true;

    const failed = await commit(run, 'worker-a:suspend', true).then(
      () => null,
      (error: Error) => error.message,
    );

    expect(failed).to.equal('outbox down');
    expect((await h.service.get(run.orgId, run.runId))?.status).to.equal('running');
    expect(await mongoose.connection.collection(PROBE).countDocuments({ runId: run.runId })).to.equal(0);
  });
});
