/**
 * G2 (ADR-INBOX-01 §Finished runs, §Watching, §Mute, §Pause): a person's own interactive run that ends while they
 * are not watching its conversation leaves one "finished" or "couldn't finish" notice, by email or Slack too where
 * they opted in and the org allows; a watched end leaves none; workflows' notices follow the person's mutes and pause.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { InMemoryInboxStore } from '../../../src/modules/agent-harness/adapters/in-memory-inbox-store';
import { InboxAwareScheduleNotifier } from '../../../src/modules/agent-harness/application/inbox-notice-policy';
import { RunEndNoticeService, RunWatchRegistry, WATCH_LEASE_MS } from '../../../src/modules/agent-harness/application/run-end-notices';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import type { InteractiveRunNotice } from '../../../src/modules/agent-harness/ports/inbox';
import type { RunEndedNotice, ScheduleNotifier } from '../../../src/modules/agent-harness/ports/schedules';
import { HarnessScheduleNotifier } from '../../../src/libs/repositories/agent-harness/schedule-notifier/mongo';
import type { NoticeRow, ScheduleNoticeRecords } from '../../../src/libs/repositories/agent-harness/schedule-notifier/records';
import { NotificationContainer } from '../../../src/modules/notification/container/notification.container';

const NOW = 1_700_000_000_000;

const run = (over: Partial<HarnessRun> = {}): HarnessRun =>
  ({
    runId: 'r1',
    orgId: 'o1',
    sessionId: 'c1',
    actorId: 'ana',
    status: 'completed',
    outcome: 'answer',
    idempotencyKey: 'turn-1',
    input: {},
    ...over,
  }) as HarnessRun;

function world(options: { allowed?: Array<'in_app' | 'email' | 'slack'>; failing?: boolean } = {}) {
  const store = new InMemoryInboxStore();
  const told: InteractiveRunNotice[] = [];
  const recounted: string[] = [];
  const service = new RunEndNoticeService({
    leases: store,
    claims: store,
    preferences: store,
    notifier: {
      runFinished: async (notice) => {
        if (options.failing === true) throw new Error('mail down');
        told.push(notice);
      },
    },
    channels: { notifyChannels: async () => options.allowed ?? ['in_app', 'email', 'slack'] },
    titles: { titles: async (_org, ids) => new Map(ids.map((id) => [id, `Title of ${id}`])) },
    noticed: (_org, userId) => recounted.push(userId),
    now: () => NOW,
  });
  return { store, told, recounted, service };
}

describe('G2 finished-run notices (ADR-INBOX-01 §Finished runs)', () => {
  it('IBX-N1: a run that ends unwatched is told once, in the Inbox, named by its chat', async () => {
    const { service, told, recounted } = world();
    expect(await service.runEnded(run())).to.equal('noticed');
    expect(told).to.deep.equal([
      {
        orgId: 'o1',
        userId: 'ana',
        sessionId: 'c1',
        runId: 'r1',
        finished: true,
        cutShort: false,
        chatTitle: 'Title of c1',
        channels: ['in_app'],
        quiet: false,
      },
    ]);
    expect(recounted).to.deep.equal(['ana']);
  });

  it('IBX-N2: a run that fails says it could not finish; an agent chat names its agent', async () => {
    const { service, told } = world();
    await service.runEnded(run({ status: 'failed', outcome: null, agentKey: 'triage' }));
    expect(told[0]).to.deep.include({ finished: false, agentKey: 'triage' });
  });

  it('IBX-N3: a run whose conversation the owner is watching ends without a notice', async () => {
    const { service, store, told } = world();
    await store.watch({ orgId: 'o1', userId: 'ana', sessionId: 'c1', watcherId: 'w', until: NOW + 1 });
    expect(await service.runEnded(run())).to.equal('watched');
    // Someone else watching the same shared conversation is not the owner watching it.
    await store.unwatch('o1', 'w');
    await store.watch({ orgId: 'o1', userId: 'ben', sessionId: 'c1', watcherId: 'w2', until: NOW + 1 });
    expect(await service.runEnded(run())).to.equal('noticed');
    expect(told).to.have.length(1);
  });

  it('IBX-N4: two replicas that both see the end tell it once', async () => {
    const { service, told } = world();
    const outcomes = await Promise.all([service.runEnded(run()), service.runEnded(run())]);
    expect(outcomes.sort()).to.deep.equal(['claimed_elsewhere', 'noticed']);
    expect(told).to.have.length(1);
  });

  it('IBX-N5: jobs, workflow firings, service callers, cancels and questions are not told here', async () => {
    const { service, told } = world();
    expect(await service.runEnded(run({ parent: { runId: 'p' } as never }))).to.equal('child');
    expect(await service.runEnded(run({ idempotencyKey: 'schedule:s1:2026-11-09T09:00Z' }))).to.equal('scheduled');
    expect(await service.runEnded(run({ input: { caller: { email: 'x@y.z' } } as never }))).to.equal('service_caller');
    expect(await service.runEnded(run({ status: 'cancelled' }))).to.equal('cancelled');
    expect(await service.runEnded(run({ outcome: 'needs_input' }))).to.equal('asked_a_question');
    expect(told).to.deep.equal([]);
  });

  it('IBX-N6: email and Slack go only where the person opted in and the org allows the channel', async () => {
    const { service, store, told } = world({ allowed: ['in_app', 'email'] });
    await store.setRunNotices('o1', 'ana', { email: true, slack: true }, NOW);
    await service.runEnded(run());
    expect(told[0]!.channels).to.deep.equal(['in_app', 'email']);
  });

  it('IBX-N7: while paused the notice is stored quietly and leaves by no other channel', async () => {
    const { service, store, told } = world();
    await store.setRunNotices('o1', 'ana', { email: true, slack: true }, NOW);
    await store.setPause('o1', 'ana', { until: NOW + 1_000 }, NOW);
    await service.runEnded(run());
    expect(told[0]).to.deep.include({ channels: ['in_app'], quiet: true });
  });

  it('IBX-N8: a failure to tell is logged and never thrown at the run’s end', async () => {
    const { service } = world({ failing: true });
    expect(await service.runEnded(run())).to.equal('failed');
  });
});

describe('G2 watch leases (ADR-INBOX-01 §Watching)', () => {
  let clock: sinon.SinonFakeTimers;
  beforeEach(() => {
    clock = sinon.useFakeTimers({ now: NOW, toFake: ['setInterval', 'clearInterval', 'Date'] });
  });
  afterEach(() => clock.restore());

  it('IBX-W1: an open stream holds a lease, renews it at half its length and gives it back when it closes', async () => {
    const store = new InMemoryInboxStore();
    const registry = new RunWatchRegistry(store, { newId: () => 'w1' });
    const stop = registry.watch({ orgId: 'o1', userId: 'ana', sessionId: 'c1' });
    await Promise.resolve();
    expect(await store.watching('o1', 'ana', 'c1', NOW + WATCH_LEASE_MS - 1)).to.equal(true);
    await clock.tickAsync(WATCH_LEASE_MS);
    expect(await store.watching('o1', 'ana', 'c1', Date.now() + WATCH_LEASE_MS / 4)).to.equal(true);
    stop();
    stop();
    await Promise.resolve();
    expect(await store.watching('o1', 'ana', 'c1', Date.now())).to.equal(false);
  });

  it('IBX-W2: a lease store that fails never fails the stream', async () => {
    const registry = new RunWatchRegistry({
      watch: () => Promise.reject(new Error('down')),
      unwatch: () => Promise.reject(new Error('down')),
      watching: () => Promise.resolve(false),
    });
    const stop = registry.watch({ orgId: 'o1', userId: 'ana', sessionId: 'c1' });
    await clock.tickAsync(WATCH_LEASE_MS);
    stop();
  });
});

describe("G2 a workflow's notices follow the person's mutes and pause (ADR-INBOX-01 §Mute, §Pause)", () => {
  const ended = (status: RunEndedNotice['status']): RunEndedNotice => ({
    orgId: 'o1',
    actorId: 'ana',
    scheduleId: 's1',
    scheduleName: 'Escalation digest',
    sessionId: 'c1',
    runId: 'r1',
    status,
    channels: ['in_app', 'email'],
  });

  function wrapped() {
    const store = new InMemoryInboxStore();
    const calls: Array<[string, unknown]> = [];
    const inner: ScheduleNotifier = {
      approvalWaiting: async (n) => void calls.push(['approval', n]),
      firingRefused: async (n) => void calls.push(['refused', n]),
      runEnded: async (n) => void calls.push(['ended', n]),
    };
    return { store, calls, notifier: new InboxAwareScheduleNotifier(inner, store, () => NOW) };
  }

  it("IBX-M1: a muted workflow's successful runs are not told; its failures and asks are", async () => {
    const { store, calls, notifier } = wrapped();
    await store.setMuted('o1', 'ana', 's1', true, NOW);
    await notifier.runEnded(ended('completed'));
    await notifier.runEnded(ended('failed'));
    await notifier.approvalWaiting({ orgId: 'o1', actorId: 'ana', scheduleId: 's1', scheduleName: 'x', sessionId: 'c1', runId: 'r1', deadlineAt: NOW });
    expect(calls.map(([kind]) => kind)).to.deep.equal(['ended', 'approval']);
    expect((calls[0]![1] as RunEndedNotice).status).to.equal('failed');
  });

  it('IBX-M2: while paused a run end is stored quietly in the Inbox only; an ask is never paused', async () => {
    const { store, calls, notifier } = wrapped();
    await store.setPause('o1', 'ana', { untilResumed: true }, NOW);
    await notifier.runEnded(ended('completed'));
    await notifier.firingRefused({ orgId: 'o1', actorId: 'ana', scheduleId: 's1', scheduleName: 'x', reason: 'r' });
    await notifier.approvalWaiting({ orgId: 'o1', actorId: 'ana', scheduleId: 's1', scheduleName: 'x', sessionId: 'c1', runId: 'r1', deadlineAt: NOW });
    expect(calls[0]).to.deep.equal(['ended', { ...ended('completed'), channels: ['in_app'], quiet: true }]);
    expect(calls[1]![1]).to.deep.include({ quiet: true });
    expect(calls[2]![1]).to.not.have.property('quiet');
  });

  it('IBX-M3: settings nobody can read leave the notice as the schedule asked', async () => {
    const calls: unknown[] = [];
    const notifier = new InboxAwareScheduleNotifier(
      { approvalWaiting: async () => undefined, firingRefused: async () => undefined, runEnded: async (n) => void calls.push(n) },
      { preferences: () => Promise.reject(new Error('down')) },
    );
    await notifier.runEnded(ended('completed'));
    expect(calls).to.deep.equal([ended('completed')]);
  });
});

describe("G2 the finished-run notice on the schedule notifier's channels", () => {
  afterEach(() => sinon.restore());

  function notifier() {
    const saved: NoticeRow[] = [];
    const mails: unknown[] = [];
    const slacks: unknown[] = [];
    const pushes: unknown[] = [];
    sinon.stub(NotificationContainer, 'getNotificationService').returns({
      sendToUser: (...args: unknown[]) => pushes.push(args),
    } as never);
    const records: ScheduleNoticeRecords = {
      answer: async () => null,
      person: async () => ({ email: 'ana@example.com', name: 'Ana' }),
      saveNotice: async (_o, _a, row) => {
        saved.push(row);
        return { _id: 'n1', ...row };
      },
    };
    const instance = new HarnessScheduleNotifier({
      records,
      mail: { sendRunEnded: async (m) => void mails.push(m), sendTemplate: async () => undefined },
      slack: { send: async (m) => (slacks.push(m), 'sent') },
      frontendUrl: () => 'https://pipeshub.example/',
    });
    return { instance, saved, mails, slacks, pushes };
  }

  const notice: InteractiveRunNotice = {
    orgId: 'o1',
    userId: 'ana',
    sessionId: 'c1',
    agentKey: 'triage',
    runId: 'r1',
    finished: false,
    cutShort: false,
    chatTitle: 'Ticket triage',
    channels: ['in_app', 'email', 'slack'],
    quiet: false,
  };

  it('IBX-C1: the Inbox row, the email and the Slack line all name the chat and the outcome, and link to it', async () => {
    const { instance, saved, mails, slacks, pushes } = notifier();
    await instance.runFinished(notice);
    expect(saved[0]).to.deep.include({
      type: 'AGENT_RUN_NOT_FINISHED',
      severity: 'error',
      title: 'Ticket triage',
      redirectLink: '/chat/?agentId=triage&conversationId=c1',
    });
    expect(saved[0]!.payload).to.deep.equal({ conversationId: 'c1', chatTitle: 'Ticket triage', runId: 'r1', agentKey: 'triage', finished: false });
    expect(mails[0]).to.deep.include({
      subject: "Couldn't finish: Ticket triage",
      link: 'https://pipeshub.example/chat/?agentId=triage&conversationId=c1',
    });
    expect(slacks[0]).to.deep.include({ agentKey: 'triage', email: 'ana@example.com' });
    expect(pushes).to.have.length(1);
  });

  it('IBX-C2: a quiet notice is stored and not pushed; channels not named are not used', async () => {
    const { instance, saved, mails, pushes } = notifier();
    await instance.runFinished({ ...notice, channels: ['in_app'], quiet: true });
    expect(saved).to.have.length(1);
    expect(pushes).to.deep.equal([]);
    expect(mails).to.deep.equal([]);
  });
});
