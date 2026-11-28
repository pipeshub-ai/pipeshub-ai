/**
 * P8a AP: an approval in a scheduled run. The run suspends for approval
 * (P2.6c) and waits until the firing's deadline; the scheduler tells the
 * actor once; a decision resumes it; nobody deciding lets the run sweeper
 * expire it at the deadline, and the watch ends either way.
 */
import { expect } from 'chai';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { workerEvent } from './support/journal-fixtures';
import { scheduledWorld, ScheduledWorld } from './support/scheduled-admission';
import { aSchedule, ALICE, ORG } from './support/schedules';

const LIMITS = { perActor: 50, perOrg: 500 };
const DEADLINE_S = 60 * 60;

async function suspendedForApproval(w: ScheduledWorld) {
  const schedule = await w.schedules.create(
    aSchedule({ agentKey: 'agent-1', deadlineS: DEADLINE_S, name: 'Weekly digest' }, w.clock.value),
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
  const receipt = await w.service.commit({
    commandId: 'worker-1:suspend:1',
    orgId: ORG,
    runId: run.runId,
    workerId: 'worker-1',
    attemptId: 'attempt-1',
    fence: claimed.run.fence,
    events: [
      workerEvent('interaction.requested', {
        interactionId: 'ix-1',
        kind: 'approval',
        bindingDigest: `sha256:${'1'.repeat(64)}`,
      }),
    ],
    legacyEvents: [],
    transition: {
      status: 'waiting_approval',
      detail: { kind: 'suspend', reason: 'approval', interactionId: 'ix-1' },
    },
    outbox: [],
  });
  expect(receipt.run.status).to.equal('waiting_approval');
  return { schedule, firing: firing!, run: receipt.run };
}

describe('P8a an approval in a scheduled run (AP)', () => {
  it('AP-1: the actor is told once, naming the schedule, the conversation and the deadline', async () => {
    const w = scheduledWorld();
    const { schedule, firing, run } = await suspendedForApproval(w);
    await w.firer.tick();
    await w.firer.tick();
    expect(w.notifier.notices).to.deep.equal([
      {
        orgId: ORG,
        actorId: ALICE,
        scheduleId: schedule.id,
        scheduleName: 'Weekly digest',
        // UX3 SE-3: the link opens the agent's own chat.
        ...(schedule.agentKey !== undefined ? { agentKey: schedule.agentKey } : {}),
        sessionId: run.sessionId,
        runId: run.runId,
        runner: 'harness',
        deadlineAt: firing.watchUntil,
      },
    ]);
    // The run's own deadline is the firing's: the approval cannot outlive it.
    expect(run.absoluteDeadlineAt).to.equal(firing.watchUntil);
  });

  it('AP-2: the actor approves: the run resumes; once it ends the firing is no longer watched', async () => {
    const w = scheduledWorld();
    const { firing, run } = await suspendedForApproval(w);
    await w.firer.tick();
    const resumed = await w.service.control({
      commandId: 'approve-1',
      dispatchCommandId: 'approve-1-dispatch',
      orgId: ORG,
      runId: run.runId,
      expectedRevision: run.revision,
      action: 'resume',
      by: `p_${ALICE}`,
      resolution: { interactionId: 'ix-1', decision: 'approved', channel: 'web' },
    });
    expect(resumed.run.status).to.equal('queued');
    expect((await w.schedules.watched(w.clock.value, 10)).map((f) => f.id)).to.deep.equal([firing.id]);
    await w.service.control({
      commandId: 'cancel-1',
      dispatchCommandId: 'cancel-1-dispatch',
      orgId: ORG,
      runId: run.runId,
      expectedRevision: resumed.run.revision,
      action: 'cancel',
    });
    await w.firer.tick();
    expect(await w.schedules.watched(w.clock.value, 10)).to.deep.equal([]);
    expect(w.notifier.notices).to.have.length(1);
  });

  it('AP-3: nobody decides: the run sweeper fails the run at the firing deadline; the watch ends', async () => {
    const w = scheduledWorld();
    const { firing, run } = await suspendedForApproval(w);
    w.clock.value = run.absoluteDeadlineAt - 1;
    expect(await w.service.sweep(10)).to.equal(0);
    w.clock.value = run.absoluteDeadlineAt;
    expect(await w.service.sweep(10)).to.equal(1);
    expect((await w.executions.get(ORG, run.runId))?.status).to.equal('failed');
    const types = (await w.executions.journal.readStream(ORG, run.sessionId, 0, 50)).map((e) => e.type);
    expect(types).to.not.include('tool.intent');
    expect(firing.watchUntil).to.equal(run.absoluteDeadlineAt);
    // The watch outlives the deadline long enough to see the run fail (TR-07), tells the actor, and ends.
    await w.firer.tick();
    expect(await w.schedules.watched(w.clock.value, 10)).to.deep.equal([]);
    expect(w.notifier.ends.map((n) => [n.runId, n.status])).to.deep.equal([[run.runId, 'failed']]);
  });

  it("AP-3: the sweeper's caller is told which run it expired and why", async () => {
    const w = scheduledWorld();
    const { run } = await suspendedForApproval(w);
    w.clock.value = run.absoluteDeadlineAt;
    const told: Array<[string, string, string]> = [];
    expect(
      await w.service.sweep(10, (expired, reason) => {
        told.push([expired.runId, expired.status, reason]);
      }),
    ).to.equal(1);
    expect(told).to.deep.equal([[run.runId, run.status, 'deadline_exceeded']]);
  });
});

describe('the inbox link of a scheduled run (UX3 SE-3)', () => {
  it('opens an agent’s conversation in that agent, and a chat’s in the chat', async () => {
    const { scheduledRunLink } = await import('../../../src/libs/repositories/agent-harness/schedule-notifier/mongo');
    expect(scheduledRunLink('ses-1', 'support-agent')).to.equal('/chat/?agentId=support-agent&conversationId=ses-1');
    expect(scheduledRunLink('ses-1')).to.equal('/chat/?conversationId=ses-1');
  });
});
