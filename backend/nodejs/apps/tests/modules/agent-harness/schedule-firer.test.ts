/** P8a SF: the scheduler over the in-memory store and fakes of its ports. */
import { expect } from 'chai';
import { InMemoryScheduleStore } from '../../../src/modules/agent-harness/adapters/in-memory-schedules';
import {
  BrokerTriggerSource,
  connectorTriggerEvent,
  recordEventStreams,
} from '../../../src/modules/agent-harness/adapters/messaging/broker-trigger-source';
import { REFUSAL_NOTICE_INTERVAL_MS, ScheduleFirer } from '../../../src/modules/agent-harness/application/schedule-firer';
import {
  type AgentSchedule,
  FIRING_LEASE_MS,
  MISFIRE_GRACE_MS,
} from '../../../src/modules/agent-harness/domain/schedules';
import type { IMessageConsumer, StreamMessage } from '../../../src/libs/types/messaging.types';
import {
  aFiring,
  aSchedule,
  ALICE,
  BOB,
  FakeActors,
  FakeAdmission,
  FakeNotifier,
  FakeRuns,
  FakeSetting,
  ORG,
  RecordedLog,
  T0,
  recordingLogger,
  TestClock,
} from './support/schedules';

const LIMITS = { perActor: 50, perOrg: 500 };

interface World {
  clock: TestClock;
  store: InMemoryScheduleStore;
  admission: FakeAdmission;
  actors: FakeActors;
  setting: FakeSetting;
  runs: FakeRuns;
  notifier: FakeNotifier;
  logs: RecordedLog[];
  firer: ScheduleFirer;
  newFirer(): ScheduleFirer;
}

function world(): World {
  const clock = new TestClock();
  const store = new InMemoryScheduleStore(clock);
  const w = {
    clock,
    store,
    admission: new FakeAdmission(),
    actors: new FakeActors(),
    setting: new FakeSetting(),
    runs: new FakeRuns(),
    notifier: new FakeNotifier(),
    logs: [] as RecordedLog[],
  } as World;
  w.newFirer = () =>
    new ScheduleFirer({
      store,
      admission: w.admission,
      actors: w.actors,
      setting: w.setting,
      runs: w.runs,
      notifier: w.notifier,
      logger: recordingLogger(w.logs),
    });
  w.firer = w.newFirer();
  return w;
}

async function due(w: World, overrides: Partial<AgentSchedule> = {}): Promise<AgentSchedule> {
  const schedule = await w.store.create(aSchedule(overrides, w.clock.value), LIMITS);
  w.clock.value = (schedule.nextFireAt as number) + 10_000;
  return schedule;
}

describe('P8a schedule firer (SF)', () => {
  let w: World;
  beforeEach(() => {
    w = world();
  });

  it('SF-1: a due slot admits one run as the actor, with its own credential and the firing pinned', async () => {
    const schedule = await due(w, { maxCostUsd: 0.5 });
    const slot = schedule.nextFireAt as number;
    await w.firer.tick();
    expect(w.admission.requests).to.have.length(1);
    const request = w.admission.requests[0]!;
    const [firing] = await w.store.firings(ORG, schedule.id, 10);
    expect(request).to.deep.equal({
      orgId: ORG,
      actorId: ALICE,
      agentKey: 'support-agent',
      // Made before agent versions (no pin stored): it runs version 1, the migration's (GA-01).
      agentVersion: 1,
      idempotencyKey: `schedule:${schedule.id}:c:2026-07-06T07:00:00.000Z`,
      deadlineMs: 8 * 60 * 60 * 1000,
      input: {
        ...schedule.input,
        timezone: 'Europe/Berlin',
        currentTime: '2026-07-06T07:00:00.000Z',
        // TR-03: why the run exists, staged for it at /pipeshub/inputs/trigger.json.
        trigger: {
          kind: 'cron',
          scheduleId: schedule.id,
          firingId: firing!.id,
          slotAt: '2026-07-06T07:00:00.000Z',
        },
      },
      authorization: 'Bearer token-of-alice',
      trigger: {
        scheduleId: schedule.id,
        firingId: firing!.id,
        slotKey: 'c:2026-07-06T07:00:00.000Z',
        maxCostUsd: 0.5,
      },
    });
    expect(firing).to.deep.include({
      status: 'admitted',
      runId: 'run-1',
      sessionId: 'ses-1',
      slotAt: slot,
      kind: 'cron',
      watchUntil: w.clock.value + 8 * 60 * 60 * 1000,
    });
    expect(w.actors.minted).to.deep.equal([ALICE]);
    expect((await w.store.get(ORG, schedule.id))?.nextFireAt).to.equal(slot + 7 * 86_400_000);
    // The next tick in the same week fires nothing.
    await w.firer.tick();
    expect(w.admission.requests).to.have.length(1);
  });

  it('SF-2: two firers interleaved on one store admit one run', async () => {
    await due(w);
    const other = w.newFirer();
    await Promise.all([w.firer.tick(), other.tick(), w.firer.tick(), other.tick()]);
    expect(w.admission.requests).to.have.length(1);
  });

  it('SF-3: after an outage, skip records one skipped firing and admits nothing', async () => {
    const schedule = await due(w);
    w.clock.value = (schedule.nextFireAt as number) + 15 * 86_400_000;
    await w.firer.tick();
    expect(w.admission.requests).to.deep.equal([]);
    const firings = await w.store.firings(ORG, schedule.id, 10);
    expect(firings.map((f) => [f.status, f.reason, f.missed, f.slotKey])).to.deep.equal([
      ['skipped', 'catch_up_skip', 2, 'c:2026-07-20T07:00:00.000Z'],
    ]);
    expect(w.logs.find((l) => l.event === 'schedule.slot_skipped')?.meta).to.include({ missed: 3, reason: 'catch_up_skip' });
    expect((await w.store.get(ORG, schedule.id))?.nextFireAt).to.equal(Date.parse('2026-07-27T07:00:00.000Z'));
  });

  it('SF-3: after an outage, run_latest admits one run for the latest slot', async () => {
    const schedule = await due(w, {
      trigger: { kind: 'cron', cron: '0 9 * * 1', timeZone: 'Europe/Berlin', catchUp: 'run_latest' },
    });
    w.clock.value = (schedule.nextFireAt as number) + 15 * 86_400_000;
    await w.firer.tick();
    await w.firer.tick();
    expect(w.admission.requests.map((r) => r.idempotencyKey)).to.deep.equal([
      `schedule:${schedule.id}:c:2026-07-20T07:00:00.000Z`,
    ]);
  });

  it('SF-4: a disabled actor gets no credential and no run; the schedule pauses', async () => {
    const schedule = await due(w);
    w.actors.refused.set(ALICE, 'actor_disabled');
    await w.firer.tick();
    expect(w.actors.minted).to.deep.equal([]);
    expect(w.admission.requests).to.deep.equal([]);
    expect((await w.store.firings(ORG, schedule.id, 1))[0]).to.deep.include({ status: 'refused', reason: 'actor_disabled' });
    expect(await w.store.get(ORG, schedule.id)).to.deep.include({ status: 'paused', statusReason: 'actor_disabled', nextFireAt: null });
    expect(w.logs.some((l) => l.event === 'schedule.auto_paused' && l.meta['reason'] === 'actor_disabled')).to.equal(true);
  });

  it('SF-4: a deleted actor disables the schedule; a service account pauses it', async () => {
    const deleted = await due(w);
    const service = await w.store.create(aSchedule({ actorId: BOB }, T0), LIMITS);
    w.actors.refused.set(ALICE, 'actor_deleted');
    w.actors.refused.set(BOB, 'actor_not_person');
    await w.firer.tick();
    expect(w.admission.requests).to.deep.equal([]);
    expect(await w.store.get(ORG, deleted.id)).to.deep.include({ status: 'disabled', statusReason: 'actor_deleted' });
    expect(await w.store.get(ORG, service.id)).to.deep.include({ status: 'paused', statusReason: 'actor_not_person' });
  });

  it('SF-5: agent access revoked since creation: refused agent_forbidden, schedule paused', async () => {
    const schedule = await due(w);
    w.admission.forbiddenActors.add(ALICE);
    await w.firer.tick();
    expect(w.admission.admitted).to.equal(0);
    expect((await w.store.firings(ORG, schedule.id, 1))[0]).to.deep.include({ status: 'refused', reason: 'agent_forbidden' });
    expect((await w.store.get(ORG, schedule.id))?.status).to.equal('paused');
  });

  it('SF-6: flag off: refused scheduled_runs_disabled, no credential minted, schedule stays active', async () => {
    const schedule = await due(w);
    w.setting.on = false;
    await w.firer.tick();
    expect(w.actors.minted).to.deep.equal([]);
    expect((await w.store.firings(ORG, schedule.id, 1))[0]).to.deep.include({ status: 'refused', reason: 'scheduled_runs_disabled' });
    expect((await w.store.get(ORG, schedule.id))?.status).to.equal('active');
  });

  it('SF-7: a transient failure retries with the same key; admitted once', async () => {
    const schedule = await due(w);
    w.admission.refusals.push('unavailable');
    await w.firer.tick();
    let [firing] = await w.store.firings(ORG, schedule.id, 1);
    expect(firing).to.deep.include({ status: 'pending', attempts: 1, leaseUntil: w.clock.value + FIRING_LEASE_MS });
    await w.firer.tick();
    expect(w.admission.requests).to.have.length(1);
    w.clock.advance(FIRING_LEASE_MS);
    await w.firer.tick();
    [firing] = await w.store.firings(ORG, schedule.id, 1);
    expect(firing).to.deep.include({ status: 'admitted', attempts: 2 });
    expect(new Set(w.admission.requests.map((r) => r.idempotencyKey)).size).to.equal(1);
  });

  it('SF-7: after five attempts the firing is refused unavailable; the schedule goes on', async () => {
    const schedule = await due(w);
    w.actors.outage = true;
    for (let i = 0; i < 6; i += 1) {
      await w.firer.tick();
      w.clock.advance(11 * 60_000);
    }
    const [firing] = await w.store.firings(ORG, schedule.id, 1);
    expect(firing).to.deep.include({ status: 'refused', reason: 'unavailable', attempts: 5 });
    expect((await w.store.get(ORG, schedule.id))?.status).to.equal('active');
  });

  it('SF-8: a firing claimed by a process that died before admitting is admitted by the next, once', async () => {
    const schedule = await due(w);
    const slot = schedule.nextFireAt as number;
    // The dead process claimed the slot (cursor moved, firing pending) and never admitted.
    await w.store.claimCronSlot(
      aFiring(schedule, { kind: 'cron', slotKey: 'c:2026-07-06T07:00:00.000Z', slotAt: slot, leaseUntil: w.clock.value + FIRING_LEASE_MS }),
      slot,
      slot + 7 * 86_400_000,
    );
    await w.firer.tick();
    expect(w.admission.requests).to.deep.equal([]);
    w.clock.advance(FIRING_LEASE_MS);
    await w.firer.tick();
    await w.newFirer().tick();
    expect(w.admission.requests.map((r) => r.idempotencyKey)).to.deep.equal([
      `schedule:${schedule.id}:c:2026-07-06T07:00:00.000Z`,
    ]);
  });

  it('SF-9: paused fires nothing; resumed fires from the next slot only', async () => {
    const schedule = await due(w);
    await w.store.setStatus(ORG, schedule.id, { from: ['active'], to: 'paused', reason: 'by_actor', nextFireAt: null }, w.clock.value);
    await w.firer.tick();
    w.clock.advance(14 * 86_400_000);
    await w.firer.tick();
    expect(w.admission.requests).to.deep.equal([]);
    // The service computes the cursor at resume; here, what it would set.
    await w.store.setStatus(ORG, schedule.id, { from: ['paused'], to: 'active', reason: null, nextFireAt: Date.parse('2026-07-27T07:00:00.000Z') }, w.clock.value);
    await w.firer.tick();
    expect(w.admission.requests).to.deep.equal([]);
    w.clock.set('2026-07-27T07:00:30.000Z');
    await w.firer.tick();
    expect(w.admission.requests.map((r) => r.trigger.slotKey)).to.deep.equal(['c:2026-07-27T07:00:00.000Z']);
  });

  it('SF-10: a rollout that says legacy refuses the firing, not retried', async () => {
    const schedule = await due(w);
    w.admission.refusals.push('rollout_legacy');
    await w.firer.tick();
    w.clock.advance(FIRING_LEASE_MS * 2);
    await w.firer.tick();
    expect(w.admission.requests).to.have.length(1);
    expect((await w.store.firings(ORG, schedule.id, 1))[0]).to.deep.include({ status: 'refused', reason: 'rollout_legacy' });
    expect((await w.store.get(ORG, schedule.id))?.status).to.equal('active');
  });

  it('UX3 SE-2: a refused firing is told to the actor, at most once a day per schedule', async () => {
    const schedule = await due(w, { trigger: { kind: 'cron', cron: '0 * * * *', timeZone: 'UTC' } });
    w.admission.refusals.push('rollout_legacy', 'rollout_legacy', 'rollout_legacy');
    await w.firer.tick();
    expect(w.notifier.refusals).to.deep.equal([
      { orgId: ORG, actorId: ALICE, scheduleId: schedule.id, scheduleName: schedule.name, reason: 'rollout_legacy' },
    ]);
    w.clock.advance(60 * 60 * 1000);
    await w.firer.tick();
    expect(w.notifier.refusals).to.have.length(1);
    w.clock.advance(REFUSAL_NOTICE_INTERVAL_MS);
    await w.firer.tick();
    expect(w.notifier.refusals).to.have.length(2);
  });

  it('SF-11: connector events coalesce per window; paused, other orgs and other connectors fire nothing', async () => {
    const trigger = { kind: 'connector' as const, connectorId: 'c1', coalesceS: 300 };
    // Each window's run while the last is still active: overlap is TR-06's, not this case's.
    const s1 = await w.store.create(aSchedule({ trigger, nextFireAt: null, overlap: 'allow' }), LIMITS);
    const paused = await w.store.create(aSchedule({ trigger, nextFireAt: null, status: 'paused' }), LIMITS);
    w.clock.set('2026-10-05T10:01:00.000Z');
    const event = { orgId: ORG, kind: 'connector' as const, connectorId: 'c1', at: w.clock.value };
    expect(await w.firer.deliver(event)).to.have.length(1);
    expect(await w.firer.deliver({ ...event, at: event.at + 60_000 })).to.deep.equal([]);
    expect(await w.firer.deliver({ ...event, orgId: 'org-2' })).to.deep.equal([]);
    expect(await w.firer.deliver({ ...event, connectorId: 'c2' })).to.deep.equal([]);
    expect(await w.firer.deliver({ ...event, at: event.at + 300_000 })).to.have.length(1);
    expect(w.admission.requests.map((r) => r.trigger.slotKey)).to.deep.equal([
      'e:2026-10-05T10:00:00.000Z',
      'e:2026-10-05T10:05:00.000Z',
    ]);
    expect(await w.store.firings(ORG, paused.id, 10)).to.deep.equal([]);
    expect(await w.store.firings(ORG, s1.id, 10)).to.have.length(2);
  });

  it('SF-11: the broker source parses newRecord events only and starts only while a connector trigger is active', async () => {
    expect(connectorTriggerEvent(JSON.stringify({ eventType: 'newRecord', payload: { orgId: ORG, connectorId: 'c1', recordName: 'secret' } }), 5)).to.deep.equal({
      orgId: ORG,
      kind: 'connector',
      connectorId: 'c1',
      at: 5,
    });
    expect(connectorTriggerEvent({ eventType: 'updateRecord', payload: { orgId: ORG, connectorId: 'c1' } }, 5)).to.equal(null);
    expect(connectorTriggerEvent('not json', 5)).to.equal(null);
    expect(connectorTriggerEvent({ eventType: 'newRecord', payload: { orgId: ORG } }, 5)).to.equal(null);
    expect(recordEventStreams(false, 8)).to.deep.equal(['record-events']);
    expect(recordEventStreams(true, 1)).to.deep.equal(['record-events']);
    expect(recordEventStreams(true, 3)).to.deep.equal(['record-events.0', 'record-events.1', 'record-events.2']);

    let handler: ((m: StreamMessage<unknown>) => Promise<void>) | null = null;
    const subscribed: string[][] = [];
    let connected = false;
    const consumer = {
      connect: async () => void (connected = true),
      disconnect: async () => void (connected = false),
      isConnected: () => connected,
      subscribe: async (topics: string[]) => void subscribed.push(topics),
      consume: async (h: (m: StreamMessage<unknown>) => Promise<void>) => void (handler = h),
    } as unknown as IMessageConsumer;
    const source = new BrokerTriggerSource({
      createConsumer: () => consumer,
      streams: () => ['record-events'],
      deliver: (event) => w.firer.deliver(event),
      clock: () => w.store.now(),
    });
    const firer = new ScheduleFirer({
      store: w.store,
      admission: w.admission,
      actors: w.actors,
      setting: w.setting,
      connectorEvents: source,
    });
    await firer.tick();
    expect(source.running).to.equal(false);
    await w.store.create(aSchedule({ trigger: { kind: 'connector', connectorId: 'c1', coalesceS: 300 }, nextFireAt: null }), LIMITS);
    await firer.tick();
    expect(source.running).to.equal(true);
    expect(subscribed).to.deep.equal([['record-events']]);
    await handler!({ key: 'k', value: { eventType: 'newRecord', payload: { orgId: ORG, connectorId: 'c1' } } });
    expect(w.admission.requests).to.have.length(1);
    await firer.stop();
    expect(connected).to.equal(false);
  });

  it('SF-12: log lines carry ids and reasons, never the query or a credential', async () => {
    await due(w);
    w.actors.refused.set(BOB, 'actor_disabled');
    await w.store.create(aSchedule({ actorId: BOB }, T0), LIMITS);
    await w.firer.tick();
    const fired = w.logs.find((l) => l.event === 'schedule.fired');
    expect(fired?.meta).to.include.keys('orgId', 'scheduleId', 'actorId', 'firingId', 'slotKey', 'runId', 'sessionId');
    const refused = w.logs.find((l) => l.event === 'schedule.firing_refused');
    expect(refused?.meta).to.include({ reason: 'actor_disabled', actorId: BOB });
    const text = JSON.stringify(w.logs);
    expect(text).to.not.include('Summarise');
    expect(text).to.not.include('token-of');
  });

  it('SF-12: a late slot is fired on time when within the grace', async () => {
    const schedule = await due(w);
    w.clock.value = (schedule.nextFireAt as number) + MISFIRE_GRACE_MS;
    await w.firer.tick();
    expect(w.admission.requests).to.have.length(1);
  });
});
