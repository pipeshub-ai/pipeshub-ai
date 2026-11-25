/**
 * CJFIX-A F1 (ADR-INBOX-02): Needs you is live. Every command that changes what a run asks of its person tells the
 * execution service's asks listeners, the Inbox pushes that person's counts from it, and a turn that answers a question
 * tells the Inbox too. Announcements for one person never overlap, and the last push follows the last change.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { Container } from 'inversify';
import { InMemoryInboxStore } from '../../../src/modules/agent-harness/adapters/in-memory-inbox-store';
import { InboxFeedService } from '../../../src/modules/agent-harness/application/inbox-feed';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import type { HarnessBindingContext } from '../../../src/modules/agent-harness/binders/context';
import { bindInbox } from '../../../src/modules/agent-harness/binders/inbox';
import { AGENT_HARNESS_EXECUTION_SERVICE } from '../../../src/modules/agent-harness/binders/tokens';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import type { InboxCounts, InboxItem } from '../../../src/modules/agent-harness/domain/inbox';
import { runAsksKey } from '../../../src/modules/agent-harness/domain/interactions';
import type { InboxNotices, InboxWaitingSource } from '../../../src/modules/agent-harness/ports/inbox';
import { workerEvent } from './support/journal-fixtures';
import { scheduledWorld, ScheduledWorld } from './support/scheduled-admission';
import { aSchedule, ALICE, ORG } from './support/schedules';
import { grantingAccess } from './support/agent-definition';
import { fixedRollout } from './support/rollout';

const LIMITS = { perActor: 50, perOrg: 500 };
const notices = { unreadCount: async () => 0 } as unknown as InboxNotices;

async function claimedRun(w: ScheduledWorld): Promise<HarnessRun> {
  const schedule = await w.schedules.create(aSchedule({ agentKey: 'agent-1', deadlineS: 3600, name: 'Digest' }, w.clock.value), LIMITS);
  w.clock.value = (schedule.nextFireAt as number) + 1_000;
  await w.firer.tick();
  const [firing] = await w.schedules.firings(ORG, schedule.id, 1);
  const run = (await w.executions.get(ORG, firing!.runId!)) as HarnessRun;
  return (await w.service.claim({ orgId: ORG, runId: run.runId, workerId: 'worker-1', attemptId: 'attempt-1', leaseDurationMs: 60_000 })).run;
}

const asking = (run: HarnessRun, transition?: 'waiting_approval') => ({
  commandId: `worker-1:ask:${transition ?? 'running'}`,
  orgId: ORG,
  runId: run.runId,
  workerId: 'worker-1',
  attemptId: 'attempt-1',
  fence: run.fence,
  events: [workerEvent('interaction.requested', { interactionId: 'ix-1', kind: 'approval', bindingDigest: `sha256:${'1'.repeat(64)}` })],
  legacyEvents: [],
  ...(transition
    ? { transition: { status: transition, detail: { kind: 'suspend' as const, reason: 'approval' as const, interactionId: 'ix-1' } } }
    : {}),
  outbox: [],
});

const cancel = (run: HarnessRun, n: number) => ({
  commandId: `cancel-${n}`,
  dispatchCommandId: `cancel-${n}-dispatch`,
  orgId: ORG,
  runId: run.runId,
  expectedRevision: run.revision,
  action: 'cancel' as const,
  by: `p_${ALICE}`,
});

async function until(done: () => boolean): Promise<void> {
  for (let i = 0; i < 200 && !done(); i += 1) await new Promise((resolve) => setImmediate(resolve));
  expect(done()).to.equal(true);
}

describe('CJFIX-A F1: an ask raised or settled reaches Needs you as a count push (ADR-INBOX-02)', () => {
  it('CJA-1: runAsksKey names the open asks a decision can reach and a root run that ended on a question', () => {
    const base = { status: 'running', openInteractions: ['b', 'a'], outcome: null } as unknown as HarnessRun;
    expect(runAsksKey(base)).to.equal('a,b|');
    expect(runAsksKey({ ...base, status: 'cancelled' })).to.equal('|');
    expect(runAsksKey({ ...base, status: 'completed', outcome: 'needs_input', openInteractions: [] })).to.equal('|question');
    expect(runAsksKey({ ...base, status: 'completed', outcome: 'needs_input', parent: { runId: 'p' } } as unknown as HarnessRun)).to.equal('|');
  });

  it('CJA-2: the suspend that raises an ask and the decision that settles it are told once each; a replay and a cancel with nothing open are not', async () => {
    const w = scheduledWorld();
    const told: string[] = [];
    w.service.onRunAsksChanged((run) => told.push(`${run.status}:${(run.openInteractions ?? []).join(',')}`));
    const run = await claimedRun(w);
    const waiting = await w.service.commit(asking(run, 'waiting_approval'));
    await w.service.commit(asking(run, 'waiting_approval'));
    const resumed = await w.service.control({
      commandId: 'approve-1',
      dispatchCommandId: 'approve-1-dispatch',
      orgId: ORG,
      runId: run.runId,
      expectedRevision: waiting.run.revision,
      expectedStatus: waiting.run.status,
      action: 'resolve',
      by: `p_${ALICE}`,
      resolution: { interactionId: 'ix-1', decision: 'approved', channel: 'web' },
    });
    await w.service.control(cancel(resumed.run, 1));
    expect(told).to.deep.equal(['waiting_approval:ix-1', 'queued:']);
  });

  it('CJA-3: an ask a running run raises without suspending (an engine job asks and goes on) is told, and so is its cancel, which closes it', async () => {
    const w = scheduledWorld();
    const told: string[] = [];
    w.service.onRunAsksChanged((run) => told.push(run.status));
    const run = await claimedRun(w);
    const asked = await w.service.commit(asking(run));
    expect(asked.run.status).to.equal('running');
    await w.service.control(cancel(asked.run, 2));
    expect(told[0]).to.equal('running');
    expect(told, 'the cancel closed the open ask').to.have.length(2);
  });

  it('CJA-4: bindInbox pushes the run actor’s counts when its asks change', async () => {
    const container = new Container();
    const asksChanged: Array<(run: HarnessRun) => void> = [];
    container.bind(AGENT_HARNESS_EXECUTION_SERVICE).toConstantValue({
      onRunEnded: () => undefined,
      onRunAsksChanged: (listener: (run: HarnessRun) => void) => asksChanged.push(listener),
      onRunWorkingChanged: () => undefined,
    });
    const pushed: Array<[string, number]> = [];
    bindInbox({
      container,
      logger: undefined,
      dependencies: {
        inbox: {
          store: new InMemoryInboxStore(),
          notices,
          publisher: { publish: (userId: string, counts: InboxCounts) => pushed.push([userId, counts.waiting]) },
        },
      },
    } as unknown as HarnessBindingContext);
    expect(asksChanged).to.have.length(1);
    asksChanged[0]!({ orgId: ORG, actorId: 'ana', runId: 'r-1', status: 'waiting_approval' } as HarnessRun);
    await until(() => pushed.length === 1);
    expect(pushed).to.deep.equal([['ana', 0]]);
  });

  it('CJA-5: announcements for one person do not overlap; one asked for while another counts is one more count, with the newest count', async () => {
    let waiting = 1;
    let counting = 0;
    let most = 0;
    let release: () => void = () => undefined;
    let gate = new Promise<void>((resolve) => (release = resolve));
    const source: InboxWaitingSource = {
      name: 'approvals',
      waiting: async () => {
        counting += 1;
        most = Math.max(most, counting);
        await gate;
        counting -= 1;
        return Array.from({ length: waiting }, (_, i) => ({ id: `a:r:${i}`, waiting: true, at: i }) as unknown as InboxItem);
      },
    };
    const pushed: number[] = [];
    const feed = new InboxFeedService({
      notices,
      waiting: [source],
      preferences: new InMemoryInboxStore(),
      schedules: async () => false,
      publisher: { publish: (_userId, counts) => pushed.push(counts.waiting) },
    });
    const first = feed.announce(ORG, 'ana');
    await new Promise((resolve) => setImmediate(resolve));
    waiting = 2;
    void feed.announce(ORG, 'ana');
    void feed.announce(ORG, 'ana');
    release();
    gate = Promise.resolve();
    await first;
    expect(most, 'never two counts at once').to.equal(1);
    expect(pushed, 'two pushes, the last one the newest count').to.deep.equal([2, 2]);
  });

  it('CJA-6: a new turn that answers a question tells the Inbox its asker’s question is gone; a turn that answers nothing does not', async () => {
    const told: string[] = [];
    const store = {
      answeredRun: sinon.stub().resolves({ runId: 'asked-1' }),
      admit: sinon.stub().callsFake(async (command) => ({ command, created: true, run: {} })),
    };
    const service = new RunAdmissionService({
      store: store as never,
      rollout: fixedRollout(),
      clock: { now: () => 1_000 },
      agentAccess: grantingAccess(),
      questionAnswered: (orgId, askingRunId) => told.push(`${orgId}:${askingRunId}`),
    });
    const turn = {
      orgId: 'org-1',
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer user-token',
      sessionId: 'session-1',
      idempotencyKey: 'k-1',
      deadlineMs: 60_000,
      input: { query: 'North America', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
    };
    await service.submit(turn);
    store.answeredRun.resolves(null);
    await service.submit({ ...turn, idempotencyKey: 'k-2' });
    expect(told, 'the asking run, whose actor the Inbox reads').to.deep.equal(['org-1:asked-1']);
  });
});
