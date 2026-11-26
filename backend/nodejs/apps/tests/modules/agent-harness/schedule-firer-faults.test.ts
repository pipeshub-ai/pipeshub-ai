/**
 * The schedule firer's fault paths (P8a, P2.6c, AP-10, TR-07, UX3), over the in-memory store and fakes: a phase
 * that throws does not stop the others; a notice that cannot be sent is logged and the firing goes on; an
 * approval link that cannot be minted leaves its channel out while the notice still goes; an unreadable setting
 * is the stricter answer (scheduled runs off, in-app notices only).
 */
import { expect } from 'chai';
import { InMemoryScheduleStore } from '../../../src/modules/agent-harness/adapters/in-memory-schedules';
import { ScheduleFirer, type ScheduleFirerOptions } from '../../../src/modules/agent-harness/application/schedule-firer';
import type { AgentSchedule } from '../../../src/modules/agent-harness/domain/schedules';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import {
  aSchedule,
  ALICE,
  FakeActors,
  FakeAdmission,
  FakeNotifier,
  FakeRuns,
  FakeSetting,
  FakeTriggerSettings,
  ORG,
  recordingLogger,
  type RecordedLog,
  TestClock,
} from './support/schedules';

const LIMITS = { perActor: 50, perOrg: 500 };

function world(overrides: Partial<ScheduleFirerOptions> = {}) {
  const clock = new TestClock();
  const store = new InMemoryScheduleStore(clock);
  const admission = new FakeAdmission();
  const runs = new FakeRuns();
  const notifier = new FakeNotifier();
  const setting = new FakeSetting();
  const settings = new FakeTriggerSettings();
  const logs: RecordedLog[] = [];
  const firer = new ScheduleFirer({
    store,
    admission,
    actors: new FakeActors(),
    setting,
    runs,
    notifier,
    settings,
    logger: recordingLogger(logs),
    ...overrides,
  });
  const due = async (o: Partial<AgentSchedule> = {}): Promise<AgentSchedule> => {
    const schedule = await store.create(aSchedule(o, clock.value), LIMITS);
    clock.value = (schedule.nextFireAt as number) + 10_000;
    return schedule;
  };
  const warned = (event: string) => logs.filter((line) => line.level === 'warn' && line.event === event);
  return { clock, store, admission, runs, notifier, setting, settings, logs, firer, due, warned };
}

const asRun = (runId: string, idempotencyKey: string, status: HarnessRun['status']) =>
  ({ orgId: ORG, runId, idempotencyKey, status }) as Pick<HarnessRun, 'orgId' | 'runId' | 'idempotencyKey' | 'status'>;

describe('schedule firer faults: a tick', () => {
  it('a phase that throws is logged by phase, and the phases after it still run', async () => {
    const w = world();
    const schedule = await w.due();
    await w.firer.tick();
    const [firing] = await w.store.firings(ORG, schedule.id, 1);
    w.runs.statuses.set(firing!.runId!, 'completed');
    const dueCron = w.store.dueCron.bind(w.store);
    w.store.dueCron = async () => {
      throw new TypeError('store down');
    };
    await w.firer.tick();
    expect(w.warned('schedule.tick_failed')).to.have.length(1);
    expect(w.warned('schedule.tick_failed')[0]!.meta).to.include({ phase: 'due', errorType: 'TypeError' });
    // The watch phase ran after it: the ended run was told.
    expect(w.notifier.ends.map((notice) => notice.status)).to.deep.equal(['completed']);
    w.store.dueCron = dueCron;
  });

  it('a scheduled-runs setting that cannot be read refuses the firing scheduled_runs_disabled', async () => {
    const w = world();
    w.setting.enabled = async () => {
      throw new Error('settings down');
    };
    const schedule = await w.due();
    await w.firer.tick();
    const [firing] = await w.store.firings(ORG, schedule.id, 1);
    expect(firing).to.include({ status: 'refused', reason: 'scheduled_runs_disabled' });
    expect(w.admission.requests).to.deep.equal([]);
  });

  it('a refusal whose notice cannot be sent is still refused, and the failure is logged', async () => {
    const w = world();
    w.notifier.firingRefused = async () => {
      throw new RangeError('inbox down');
    };
    w.admission.refusals.push('agent_forbidden');
    const schedule = await w.due();
    await w.firer.tick();
    const [firing] = await w.store.firings(ORG, schedule.id, 1);
    expect(firing).to.include({ status: 'refused', reason: 'agent_forbidden' });
    expect(w.warned('schedule.notify_failed')[0]?.meta).to.include({ errorType: 'RangeError', scheduleId: schedule.id });
  });

  it('a refusal the store cannot settle leaves the firing pending for another tick, with a log line', async () => {
    const w = world();
    w.admission.refusals.push('agent_forbidden');
    const schedule = await w.due();
    w.store.settle = async () => {
      throw new Error('store down');
    };
    await w.firer.tick();
    const [firing] = await w.store.firings(ORG, schedule.id, 1);
    expect(firing?.status).to.equal('pending');
    expect(w.warned('schedule.firing_failed')[0]?.meta).to.include({ reason: 'agent_forbidden', errorType: 'Error' });
  });
});

describe('schedule firer faults: a fired run waiting for approval (P2.6c, AP-10)', () => {
  async function waiting(w: ReturnType<typeof world>, notify: AgentSchedule['notify'] = ['in_app', 'email', 'slack']) {
    const schedule = await w.due({ notify });
    await w.firer.tick();
    const [firing] = await w.store.firings(ORG, schedule.id, 1);
    w.runs.statuses.set(firing!.runId!, 'waiting_approval');
    return { schedule, firing: firing! };
  }

  it('the bound wait notices get the outside channels the schedule and the org allow, after the inbox notice', async () => {
    const sent: unknown[] = [];
    const w = world({ waitNotices: { runWaiting: async (n) => void sent.push(n) } });
    const { firing } = await waiting(w);
    await w.firer.tick();
    expect(w.notifier.notices).to.have.length(1);
    expect(sent).to.deep.equal([{ orgId: ORG, actorId: ALICE, scheduleName: w.notifier.notices[0]!.scheduleName, agentKey: w.notifier.notices[0]!.agentKey, runId: firing.runId, channels: ['email', 'slack'] }]);
  });

  it('an org channel setting that cannot be read allows in-app only: the wait notices are not asked', async () => {
    const sent: unknown[] = [];
    const w = world({ waitNotices: { runWaiting: async (n) => void sent.push(n) } });
    w.settings.notifyChannels = async () => {
      throw new Error('policy store down');
    };
    await waiting(w);
    await w.firer.tick();
    expect(sent).to.deep.equal([]);
    expect(w.notifier.notices).to.have.length(1);
  });

  it('wait notices that fail are logged like a failed inbox notice', async () => {
    const w = world({
      waitNotices: {
        runWaiting: async () => {
          throw new RangeError('mail down');
        },
      },
    });
    await waiting(w);
    await w.firer.tick();
    expect(w.notifier.notices).to.have.length(1);
    expect(w.warned('schedule.notify_failed')[0]?.meta).to.include({ errorType: 'RangeError' });
  });

  it('a waiting notice that cannot be sent is logged, and the actor is not told twice', async () => {
    const w = world();
    let attempts = 0;
    w.notifier.approvalWaiting = async () => {
      attempts += 1;
      throw new Error('inbox down');
    };
    const { firing } = await waiting(w, ['in_app']);
    await w.firer.tick();
    await w.firer.tick();
    expect(attempts).to.equal(1);
    expect(w.warned('schedule.notify_failed')[0]?.meta).to.include({ runId: firing.runId, errorType: 'Error' });
  });
});

describe('schedule firer faults: a fired run that ends (TR-07)', () => {
  it('an end notice that cannot be sent is logged, and the watch ends', async () => {
    const w = world();
    w.notifier.runEnded = async () => {
      throw new Error('mailer down');
    };
    const schedule = await w.due();
    await w.firer.tick();
    const [firing] = await w.store.firings(ORG, schedule.id, 1);
    w.runs.statuses.set(firing!.runId!, 'completed');
    await w.firer.runEnded(asRun(firing!.runId!, w.admission.requests[0]!.idempotencyKey, 'completed'));
    expect(w.warned('schedule.notify_failed')[0]?.meta).to.include({ runId: firing!.runId, errorType: 'Error' });
    expect(await w.store.watched(w.clock.value, 10)).to.deep.equal([]);
  });

  it('a run end the store cannot read is logged and never thrown to the execution service', async () => {
    const w = world();
    const schedule = await w.due();
    await w.firer.tick();
    const [firing] = await w.store.firings(ORG, schedule.id, 1);
    w.store.now = async () => {
      throw new TypeError('clock read failed');
    };
    await w.firer.runEnded(asRun(firing!.runId!, w.admission.requests[0]!.idempotencyKey, 'failed'));
    expect(w.warned('schedule.run_end_failed')[0]?.meta).to.include({
      orgId: ORG,
      runId: firing!.runId,
      scheduleId: schedule.id,
      errorType: 'TypeError',
    });
    expect(w.notifier.ends).to.deep.equal([]);
  });
});
