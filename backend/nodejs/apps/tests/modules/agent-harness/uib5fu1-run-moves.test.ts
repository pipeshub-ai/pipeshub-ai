/**
 * UIB5-FU-1: a scheduled run that starts waiting on its person, or goes on after they answer, is told to its owner
 * at once, by the execution service's run-status listener, not at the firer's next watch tick (15 s). The watch stays
 * the backstop for a replica that died before it could tell; it tells nothing twice.
 */
import { expect } from 'chai';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { runEndVerdict } from '../../../src/modules/agent-harness/domain/inbox';
import { workerEvent } from './support/journal-fixtures';
import { scheduledWorld, ScheduledWorld } from './support/scheduled-admission';
import { aSchedule, ALICE, ORG } from './support/schedules';
import { without } from './support/without';

const LIMITS = { perActor: 50, perOrg: 500 };

async function firedAndClaimed(w: ScheduledWorld) {
  const schedule = await w.schedules.create(
    aSchedule({ agentKey: 'agent-1', deadlineS: 3600, name: 'Weekly digest' }, w.clock.value),
    LIMITS,
  );
  w.clock.value = (schedule.nextFireAt as number) + 1_000;
  await w.firer.tick();
  const [firing] = await w.schedules.firings(ORG, schedule.id, 1);
  const run = (await w.executions.get(ORG, firing!.runId!)) as HarnessRun;
  const claimed = await w.service.claim({
    orgId: ORG,
    runId: run.runId,
    workerId: 'worker-1',
    attemptId: 'attempt-1',
    leaseDurationMs: 60_000,
  });
  return { schedule, firing: firing!, run: claimed.run };
}

function suspend(run: HarnessRun) {
  return {
    commandId: 'worker-1:suspend:1',
    orgId: ORG,
    runId: run.runId,
    workerId: 'worker-1',
    attemptId: 'attempt-1',
    fence: run.fence,
    events: [
      workerEvent('interaction.requested', {
        interactionId: 'ix-1',
        kind: 'approval',
        bindingDigest: `sha256:${'1'.repeat(64)}`,
      }),
    ],
    legacyEvents: [],
    transition: {
      status: 'waiting_approval' as const,
      detail: { kind: 'suspend' as const, reason: 'approval' as const, interactionId: 'ix-1' },
    },
    outbox: [],
  };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

describe('UIB5-FU-1: a scheduled run is told as it starts waiting and goes on, not at the next tick', () => {
  it('FU1-1: ExecutionService tells its status listeners of a move into waiting and of the answer, once each; a replay, a claim and an end are not told', async () => {
    const w = scheduledWorld();
    const moves: string[] = [];
    w.service.onRunStatusChanged((run) => moves.push(run.status));
    const { run } = await firedAndClaimed(w);
    const waiting = await w.service.commit(suspend(run));
    await w.service.commit(suspend(run));
    const resumed = await w.service.control({
      commandId: 'approve-1',
      dispatchCommandId: 'approve-1-dispatch',
      orgId: ORG,
      runId: run.runId,
      expectedRevision: waiting.run.revision,
      action: 'resume',
      by: `p_${ALICE}`,
      resolution: { interactionId: 'ix-1', decision: 'approved', channel: 'web' },
    });
    await w.service.control({
      commandId: 'cancel-1',
      dispatchCommandId: 'cancel-1-dispatch',
      orgId: ORG,
      runId: run.runId,
      expectedRevision: resumed.run.revision,
      action: 'cancel',
      by: `p_${ALICE}`,
    });
    expect(moves).to.deep.equal(['waiting_approval', 'queued']);
  });

  it('FU1-2: the firer tells its actor of the wait at once; the watch then tells nothing again', async () => {
    const w = scheduledWorld();
    w.service.onRunStatusChanged((moved) => void w.firer.runMoved(moved));
    const { run } = await firedAndClaimed(w);
    await w.service.commit(suspend(run));
    await settle();
    expect(w.notifier.notices.map((n) => n.runId), 'told on the commit, with no tick').to.deep.equal([run.runId]);
    await w.firer.watchRuns();
    expect(w.notifier.notices).to.have.length(1);
  });

  it('FU1-4: a fired run whose key is stored as a digest (QA15-F3) still ends its watch at its end, at once: the firing is found by the run', async () => {
    const w = scheduledWorld();
    w.service.onRunEnded((ended) => void w.firer.runEnded(ended));
    const { firing, run } = await firedAndClaimed(w);
    expect(run.idempotencyKey, 'the run holds a digest, not the slot').to.match(/^hk1:/);
    const waiting = await w.service.commit(suspend(run));
    await w.service.control({
      commandId: 'cancel-1',
      dispatchCommandId: 'cancel-1-dispatch',
      orgId: ORG,
      runId: run.runId,
      expectedRevision: waiting.run.revision,
      action: 'cancel',
      by: `p_${ALICE}`,
    });
    await settle();
    expect(w.notifier.ends.map((n) => n.runId), 'told with no tick').to.deep.equal([run.runId]);
    expect((await w.schedules.watched(w.clock.value, 10)).map((f) => f.id)).to.not.include(firing.id);
  });

  it('FU1-5: a fired run that ends is not also a finished-chat notice: its digest key names no slot, its attribution names the schedule', async () => {
    const w = scheduledWorld();
    const { schedule, run } = await firedAndClaimed(w);
    expect(run.attribution?.initiatorRef).to.equal(`trigger:${schedule.id}`);
    expect(runEndVerdict({ ...run, status: 'completed', outcome: 'answered' } as unknown as HarnessRun)).to.deep.equal({ notify: false, reason: 'scheduled' });
  });

  it('FU1-3: a run no schedule fired, or not the firing’s own run, tells nothing and never throws', async () => {
    const w = scheduledWorld();
    const { schedule, firing, run } = await firedAndClaimed(w);
    const unattributed = without(run, 'attribution');
    await w.firer.runMoved({ ...unattributed, idempotencyKey: 'chat-turn-1', status: 'waiting_approval' });
    await w.firer.runMoved({ ...run, runId: 'someone-else', status: 'waiting_approval' });
    expect(w.notifier.notices).to.deep.equal([]);
    w.schedules.activeFirings = () => Promise.reject(new Error('store down'));
    await w.firer.runMoved({ ...run, status: 'waiting_approval' });
    expect(w.notifier.notices).to.deep.equal([]);
    expect(firing.scheduleId).to.equal(schedule.id);
  });
});
