/**
 * QA18-8 (VS-11, DEP-18 index half): Mongo index migrations with durable runs in
 * flight, on a replica set (run instructions in execution-store.mongo.itest.ts).
 *
 * A release ships a prefix of `HARNESS_MIGRATIONS`. For every prefix, runs are
 * left queued, running and waiting for approval under that release's indexes;
 * then this release's `up`, a rollback's `down` to the prefix, and `up` again.
 * After each step every run keeps progressing: the queued one is claimed once,
 * the running one's holder commits under its fence, the approval resolves and
 * its run is claimed and completes.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import mongoose from 'mongoose';
import {
  HARNESS_MIGRATIONS,
  migrateHarnessIndexes,
  revertHarnessIndexes,
} from '../../../src/libs/repositories/agent-harness/migrations/mongo';
import { HarnessRun, RunTransition } from '../../../src/modules/agent-harness/domain/execution';
import { InteractionService } from '../../../src/modules/agent-harness/application/interaction-service';
import { InMemoryRunWakeup } from '../../../src/modules/agent-harness/adapters/in-memory-interactions';
import { contractClock, ContractHarness, createContractHarness } from './support/execution-store-contract';
import { connectHarnessMongo, disconnectHarnessMongo, mongoBackend, resetHarnessMongo } from './support/harness-mongo';
import { workerEvent } from './support/journal-fixtures';
import { HARNESS_ENABLED } from './support/rollout';

const AGENT_RUN = { agentKey: 'agent-1', authorization: 'Bearer user' };
const ANSWERED: RunTransition = { status: 'completed', outcome: 'answer' };
const SUSPEND_FOR = (interactionId: string): RunTransition => ({
  status: 'waiting_approval',
  detail: { kind: 'suspend', reason: 'approval', interactionId },
});

interface InFlight {
  queued: HarnessRun;
  running: HarnessRun;
  waiting: HarnessRun;
}

describe('QA18-8: index migrations with durable runs in flight', function () {
  this.timeout(300_000);
  let h: ContractHarness;
  let step = 0;

  before(connectHarnessMongo);
  after(disconnectHarnessMongo);
  beforeEach(async () => {
    await resetHarnessMongo();
    const clock = contractClock();
    h = createContractHarness(mongoBackend({ clock }), clock);
    h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
  });

  const db = () => mongoose.connection.db!;
  const command = (what: string) => `qa18-${what}-${String((step += 1))}`;

  const claim = async (run: Pick<HarnessRun, 'orgId' | 'runId'>, worker: string): Promise<HarnessRun> =>
    (
      await h.service.claim({
        orgId: run.orgId,
        runId: run.runId,
        workerId: worker,
        attemptId: `attempt-${worker}-${String(step)}`,
        leaseDurationMs: 60_000,
      })
    ).run;

  const commit = (run: HarnessRun, events: Parameters<typeof workerEvent>[] = [], transition?: RunTransition) =>
    h.service.commit({
      commandId: command('commit'),
      orgId: run.orgId,
      runId: run.runId,
      workerId: run.workerId ?? '',
      attemptId: run.attemptId ?? '',
      fence: run.fence,
      events: events.map((args) => workerEvent(...args)),
      legacyEvents: [],
      ...(transition !== undefined ? { transition } : {}),
      outbox: [],
    });

  const resolve = (run: HarnessRun, interactionId: string) =>
    new InteractionService(h.service, new InMemoryRunWakeup()).resolve({
      orgId: run.orgId,
      runId: run.runId,
      interactionId,
      decision: 'approved',
      channel: 'web',
      by: 'p_approver',
      commandId: command('decide'),
    });

  const status = async (run: HarnessRun) => (await h.service.get(run.orgId, run.runId))?.status;

  /** A queued run, a running one and one suspended on an approval, each in its own session. */
  async function inFlight(tag: string): Promise<InFlight> {
    const queued = (await h.admit(AGENT_RUN)).run;
    const running = await claim((await h.admit(AGENT_RUN)).run, `worker-run-${tag}`);
    const asking = await claim((await h.admit(AGENT_RUN)).run, `worker-ask-${tag}`);
    const interaction = `ix-${tag}`;
    const requested: Parameters<typeof workerEvent> = [
      'interaction.requested',
      { interactionId: interaction, kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` },
    ];
    const waiting = (await commit(asking, [requested], SUSPEND_FOR(interaction))).run;
    expect(waiting.status).to.equal('waiting_approval');
    return { queued, running, waiting };
  }

  /** Each run goes one step further: claimed, committed under its fence, resolved, resumed and completed. */
  async function progress(runs: InFlight, tag: string): Promise<void> {
    const claimed = await claim(runs.queued, `worker-queued-${tag}`);
    expect(claimed.status).to.equal('running');
    expect((await commit(claimed, [], ANSWERED)).run.status).to.equal('completed');

    expect((await commit(runs.running, [], ANSWERED)).run.status).to.equal('completed');

    const decided = await resolve(runs.waiting, `ix-${tag}`);
    expect(decided.run.status).to.equal('queued');
    const resumed = await claim(runs.waiting, `worker-resumed-${tag}`);
    expect((await commit(resumed, [], ANSWERED)).run.status).to.equal('completed');

    expect([await status(runs.queued), await status(runs.running), await status(runs.waiting)]).to.deep.equal([
      'completed',
      'completed',
      'completed',
    ]);
  }

  for (let shipped = 0; shipped < HARNESS_MIGRATIONS.length; shipped += 1) {
    const later = HARNESS_MIGRATIONS.slice(shipped);
    it(`release with ${String(shipped)} migrations -> this release (+${later.map((m) => m.id).join(', +')}) -> rollback -> up`, async () => {
      await revertHarnessIndexes(db(), later);

      const beforeUp = await inFlight('n');
      const beforeDown = await inFlight('n2');
      await migrateHarnessIndexes(db());
      await progress(beforeUp, 'n');

      const beforeUpAgain = await inFlight('n1');
      await revertHarnessIndexes(db(), later);
      await progress(beforeDown, 'n2');

      await migrateHarnessIndexes(db());
      await progress(beforeUpAgain, 'n1');
    });
  }
});
