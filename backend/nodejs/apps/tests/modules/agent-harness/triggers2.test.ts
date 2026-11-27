/**
 * TRIGGERS2 (checklist 2.6, C2.53–C2.60): catch-up `run_all` (TR-02), the
 * trigger context a run is given and redelivery (TR-03), signed, capped and
 * rate-limited webhooks (TR-04), the overlap policy (TR-06) and completion
 * notices (TR-07), over the in-memory store and fakes of the firer's ports.
 */
import { expect } from 'chai';
import { createHash } from 'node:crypto';
import { InMemoryScheduleStore } from '../../../src/modules/agent-harness/adapters/in-memory-schedules';
import {
  BrokerTriggerSource,
  connectorTriggerEvent,
} from '../../../src/modules/agent-harness/adapters/messaging/broker-trigger-source';
import { ScheduleFirer } from '../../../src/modules/agent-harness/application/schedule-firer';
import {
  type CreateScheduleRequest,
  type ScheduleCaller,
  ScheduleService,
} from '../../../src/modules/agent-harness/application/schedule-service';
import {
  HarnessScheduleRefusedError,
  HarnessTriggerRefusedError,
} from '../../../src/modules/agent-harness/domain/errors';
import type { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import {
  type AgentSchedule,
  decideCron,
  MAX_CATCH_UP_RUNS,
  MAX_EVENT_AGE_MS,
  OVERLAP_RECHECK_MS,
  parseScheduleIdempotencyKey,
  scheduleIdempotencyKey,
  WATCH_GRACE_MS,
  webhookSignature,
} from '../../../src/modules/agent-harness/domain/schedules';
import { admissionRequirements } from '../../../src/modules/agent-harness/domain/worker-compat';
import type { StreamMessage } from '../../../src/libs/types/messaging.types';
import { testControlPlane } from '../control-plane/support/in-memory';
import {
  aSchedule,
  ALICE,
  FakeActors,
  FakeAdmission,
  FakeNotifier,
  FakeRecordAccess,
  FakeRuns,
  FakeSetting,
  FakeTriggerSettings,
  ORG,
  recordingLogger,
  type RecordedLog,
  TestClock,
} from './support/schedules';
import { without } from './support/without';

const LIMITS = { perActor: 50, perOrg: 500 };
const WEEK = 7 * 86_400_000;
const HOUR = 60 * 60 * 1000;

interface World {
  clock: TestClock;
  store: InMemoryScheduleStore;
  admission: FakeAdmission;
  actors: FakeActors;
  setting: FakeSetting;
  runs: FakeRuns;
  notifier: FakeNotifier;
  records: FakeRecordAccess;
  settings: FakeTriggerSettings;
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
    records: new FakeRecordAccess(),
    settings: new FakeTriggerSettings(),
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
      records: w.records,
      settings: w.settings,
      logger: recordingLogger(w.logs),
    });
  w.firer = w.newFirer();
  return w;
}

const iso = (at: number): string => new Date(at).toISOString();

async function due(w: World, overrides: Partial<AgentSchedule> = {}): Promise<AgentSchedule> {
  const schedule = await w.store.create(aSchedule(overrides, w.clock.value), LIMITS);
  w.clock.value = (schedule.nextFireAt as number) + 10_000;
  return schedule;
}

/** A run of the fake admission ending, as the execution service tells the firer. */
function ended(w: World, runId: string, status: 'completed' | 'failed' | 'cancelled', key: string) {
  w.runs.statuses.set(runId, status);
  return { orgId: ORG, runId, idempotencyKey: key, status } as Pick<
    HarnessRun,
    'orgId' | 'runId' | 'idempotencyKey' | 'status'
  >;
}

describe('TRIGGERS2 TR-02: catch-up run_all', () => {
  const WEEKLY = { kind: 'cron' as const, cron: '0 9 * * 1', timeZone: 'Europe/Berlin', catchUp: 'run_all' as const };

  it('TR-02: after an outage run_all fires each missed slot, oldest first, one per tick, each with its own time', async () => {
    const w = world();
    const schedule = await due(w, { trigger: WEEKLY, overlap: 'allow' });
    const first = schedule.nextFireAt as number;
    w.clock.value = first + 2 * WEEK + 60 * 60_000;
    await w.firer.tick();
    expect(w.admission.requests.map((r) => r.input.currentTime)).to.deep.equal([iso(first)]);
    // The cursor moved only past the slot it fired: the next tick fires the next missed one.
    expect((await w.store.get(ORG, schedule.id))?.nextFireAt).to.equal(first + WEEK);
    await w.firer.tick();
    await w.firer.tick();
    await w.firer.tick();
    expect(w.admission.requests.map((r) => r.idempotencyKey)).to.deep.equal(
      [first, first + WEEK, first + 2 * WEEK].map((slot) => scheduleIdempotencyKey(schedule.id, `c:${iso(slot)}`)),
    );
    expect((await w.store.get(ORG, schedule.id))?.nextFireAt).to.equal(first + 3 * WEEK);
  });

  it(`TR-02: run_all replays at most the latest ${String(MAX_CATCH_UP_RUNS)} missed slots; the older ones are counted missed`, async () => {
    const w = world();
    const hourly = { kind: 'cron' as const, cron: '0 * * * *', timeZone: 'UTC', catchUp: 'run_all' as const };
    const schedule = await due(w, { trigger: hourly, overlap: 'allow' });
    const first = schedule.nextFireAt as number;
    w.clock.value = first + 71 * HOUR + 30 * 60_000;
    for (let i = 0; i < MAX_CATCH_UP_RUNS + 3; i += 1) await w.firer.tick();
    expect(w.admission.requests).to.have.length(MAX_CATCH_UP_RUNS);
    expect(w.admission.requests[0]?.input.currentTime).to.equal(iso(first + (72 - MAX_CATCH_UP_RUNS) * HOUR));
    const firings = await w.store.firings(ORG, schedule.id, 100);
    expect(firings.reduce((sum, f) => sum + f.missed, 0)).to.equal(72 - MAX_CATCH_UP_RUNS);
  });

  it('TR-02: the decision table: on time fires the slot; skip and run_latest are unchanged', () => {
    const slot = Date.parse('2026-07-06T07:00:00.000Z');
    expect(decideCron(WEEKLY, slot, slot - 1)).to.deep.equal({ kind: 'wait' });
    expect(decideCron(WEEKLY, slot, slot + 1_000)).to.deep.include({ kind: 'fire', slotAt: slot, missed: 0, late: false, nextFireAt: slot + WEEK });
    const late = slot + 2 * WEEK + 10 * 60_000;
    expect(decideCron({ ...WEEKLY, catchUp: 'skip' }, slot, late)).to.deep.include({ kind: 'skip', slotAt: slot + 2 * WEEK, missed: 2 });
    expect(decideCron({ ...WEEKLY, catchUp: 'run_latest' }, slot, late)).to.deep.include({ kind: 'fire', slotAt: slot + 2 * WEEK, missed: 2 });
    expect(decideCron(WEEKLY, slot, late)).to.deep.include({ kind: 'fire', slotAt: slot, missed: 0, late: true, nextFireAt: slot + WEEK });
  });
});

describe('TRIGGERS2 TR-03: event context staged for the run, idempotent on redelivery', () => {
  const CONNECTOR = { kind: 'connector' as const, connectorId: 'c1', coalesceS: 300 };

  it('TR-03: a connector event names its record to the run only when the actor may read it, asked with their credential', async () => {
    const w = world();
    const schedule = await w.store.create(aSchedule({ trigger: CONNECTOR, nextFireAt: null }), LIMITS);
    w.clock.set('2026-10-05T10:01:00.000Z');
    w.records.readable.set(ALICE, new Set(['rec-1']));
    const [firing] = await w.firer.deliver({ orgId: ORG, kind: 'connector', connectorId: 'c1', recordId: 'rec-1', at: w.clock.value });
    expect(w.admission.requests[0]?.input.trigger).to.deep.equal({
      kind: 'connector',
      scheduleId: schedule.id,
      firingId: firing!.id,
      slotAt: '2026-10-05T10:00:00.000Z',
      window: { start: '2026-10-05T10:00:00.000Z', end: '2026-10-05T10:05:00.000Z' },
      event: { connectorId: 'c1', eventType: 'newRecord', at: '2026-10-05T10:01:00.000Z', recordId: 'rec-1' },
    });
    expect(w.records.queries.map((q) => q.authorization)).to.deep.equal(['Bearer token-of-alice']);
  });

  it('TR-03: a record the actor may not read is left out; the run still starts', async () => {
    const w = world();
    await w.store.create(aSchedule({ trigger: CONNECTOR, nextFireAt: null }), LIMITS);
    w.clock.set('2026-10-05T10:01:00.000Z');
    await w.firer.deliver({ orgId: ORG, kind: 'connector', connectorId: 'c1', recordId: 'rec-secret', at: w.clock.value });
    expect(w.admission.requests).to.have.length(1);
    expect(w.admission.requests[0]?.input.trigger?.event).to.deep.equal({
      connectorId: 'c1',
      eventType: 'newRecord',
      at: '2026-10-05T10:01:00.000Z',
    });
  });

  it('TR-03: the record authority down: the firing waits and retries; the context is fixed once, so every attempt admits the same input', async () => {
    const w = world();
    const schedule = await w.store.create(aSchedule({ trigger: CONNECTOR, nextFireAt: null }), LIMITS);
    w.clock.set('2026-10-05T10:01:00.000Z');
    w.records.outage = true;
    await w.firer.deliver({ orgId: ORG, kind: 'connector', connectorId: 'c1', recordId: 'rec-1', at: w.clock.value });
    expect(w.admission.requests).to.deep.equal([]);
    expect((await w.store.firings(ORG, schedule.id, 1))[0]).to.deep.include({ status: 'pending' });
    w.records.outage = false;
    w.records.readable.set(ALICE, new Set(['rec-1']));
    w.admission.refusals.push('unavailable');
    w.clock.advance(2 * 60_000);
    await w.firer.tick();
    // Access is taken away before the retry: the retry still admits what the first attempt fixed.
    w.records.readable.set(ALICE, new Set());
    w.clock.advance(3 * 60_000);
    await w.firer.tick();
    expect(w.admission.requests).to.have.length(2);
    expect(w.admission.requests[1]?.input).to.deep.equal(w.admission.requests[0]?.input);
    expect(w.admission.admitted).to.equal(1);
  });

  it('TR-03: a redelivered event is windowed by its own time, so it fires nothing more', async () => {
    const w = world();
    await w.store.create(aSchedule({ trigger: CONNECTOR, nextFireAt: null }), LIMITS);
    w.clock.set('2026-10-05T10:01:00.000Z');
    const message = {
      value: JSON.stringify({
        eventType: 'newRecord',
        timestamp: w.clock.value,
        payload: { orgId: ORG, connectorId: 'c1', recordId: 'rec-1', recordName: 'secret plan' },
      }),
    } as StreamMessage<unknown>;
    const source = new BrokerTriggerSource({
      createConsumer: () => {
        throw new Error('not consumed here');
      },
      streams: () => ['record-events'],
      deliver: (event) => w.firer.deliver(event),
      clock: () => w.store.now(),
    });
    await source.handle(message);
    w.clock.advance(20 * 60_000);
    await source.handle(message);
    expect(w.admission.requests).to.have.length(1);
    expect(JSON.stringify(w.admission.requests[0]?.input)).to.not.include('secret plan');
  });

  it('TR-03: an event carries its record and producer time; one too old or too far ahead is timed by our clock', () => {
    const now = Date.parse('2026-10-05T10:00:00.000Z');
    const at = (timestamp: number) =>
      connectorTriggerEvent({ eventType: 'newRecord', timestamp, payload: { orgId: ORG, connectorId: 'c1', recordId: 'r' } }, now)?.at;
    expect(connectorTriggerEvent({ eventType: 'newRecord', timestamp: now - 5_000, payload: { orgId: ORG, connectorId: 'c1', recordId: 'r' } }, now)).to.deep.equal({
      orgId: ORG,
      kind: 'connector',
      connectorId: 'c1',
      recordId: 'r',
      at: now - 5_000,
    });
    expect(at(now - MAX_EVENT_AGE_MS - 1)).to.equal(now);
    expect(at(now + 6 * 60_000)).to.equal(now);
  });

  it('TR-03: cron and run-now runs say so; a run whose input carries a trigger needs a worker that stages it', async () => {
    const w = world();
    const schedule = await due(w);
    await w.firer.tick();
    expect(w.admission.requests[0]?.input.trigger).to.deep.include({ kind: 'cron', scheduleId: schedule.id });
    await w.firer.runNow(schedule, 'k1');
    expect(w.admission.requests[1]?.input.trigger).to.deep.include({ kind: 'manual', scheduleId: schedule.id });
    const input = w.admission.requests[0]!.input;
    expect(admissionRequirements({ input }).features).to.include('trigger_context');
    const plain = without(input, 'trigger');
    expect(admissionRequirements({ input: plain }).features).to.not.include('trigger_context');
  });
});

const alice: ScheduleCaller = { orgId: ORG, userId: ALICE, via: 'session' };
const CRON: CreateScheduleRequest = {
  name: 'Triage new issues',
  agentKey: 'support-agent',
  input: { query: 'Triage what came in' },
  trigger: { kind: 'cron', cron: '0 9 * * 1', timeZone: 'Europe/Berlin' },
};

function serviceWorld() {
  const w = world();
  w.clock.value = Date.parse('2026-07-01T00:00:00.000Z');
  let secrets = 0;
  const service = new ScheduleService({
    transaction: testControlPlane(),
    store: w.store,
    firer: w.firer,
    actors: w.actors,
    setting: w.setting,
    triggers: w.settings,
    logger: recordingLogger(w.logs),
    newSecret: () => `pxs_secret_${String((secrets += 1))}`,
  });
  return { ...w, service };
}

async function refusal(promise: Promise<unknown>): Promise<string> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof HarnessScheduleRefusedError || error instanceof HarnessTriggerRefusedError) {
      return error.code;
    }
    throw error;
  }
  throw new Error('expected a refusal');
}

describe('TRIGGERS2 TR-04: webhook signature, payload cap and rate limit', () => {
  const body = Buffer.from('{"issue":"PH-1"}');

  async function signedHook() {
    const w = serviceWorld();
    const created = await w.service.create(alice, { ...CRON, trigger: { kind: 'webhook', coalesceS: 60 } });
    const secret = created.webhookSecret!;
    const sign = (bytes: Buffer, at = w.clock.value) => {
      const timestamp = String(Math.floor(at / 1000));
      return { timestamp, signature: webhookSignature(secret, timestamp, bytes), body: bytes };
    };
    return { ...w, created, secret, sign };
  }

  it('TR-04: a new webhook is signed: its secret is answered once and stored as the signing key, with no token', async () => {
    const { created, secret, store } = await signedHook();
    expect(secret).to.equal('pxs_secret_1');
    expect(created.webhookToken).to.equal(undefined);
    expect(created.schedule.trigger).to.deep.include({ kind: 'webhook', auth: 'signature' });
    const stored = await store.get(ORG, created.schedule.id);
    expect(stored).to.deep.include({ webhookSigningKey: secret, webhookTokenHash: null });
  });

  it('TR-04: the right signature fires; a wrong one, a stale or missing timestamp, and a bare token answer alike', async () => {
    const { service, created, sign, clock } = await signedHook();
    const id = created.schedule.id;
    const fired = await service.webhook(id, { ...sign(body), contentType: 'application/json' });
    expect(fired.firing).to.deep.include({ kind: 'event', status: 'admitted' });
    const good = sign(body);
    expect(await refusal(service.webhook(id, { ...good, body: Buffer.from('{"issue":"PH-2"}') }))).to.equal('SCHEDULE_TRIGGER_TOKEN_INVALID');
    expect(await refusal(service.webhook(id, { ...good, signature: 'v1=00' }))).to.equal('SCHEDULE_TRIGGER_TOKEN_INVALID');
    expect(await refusal(service.webhook(id, { body, signature: good.signature }))).to.equal('SCHEDULE_TRIGGER_TOKEN_INVALID');
    expect(await refusal(service.webhook(id, sign(body, clock.value - 301_000)))).to.equal('SCHEDULE_TRIGGER_TOKEN_INVALID');
    expect(await refusal(service.webhook(id, { body, token: 'pxs_secret_1' }))).to.equal('SCHEDULE_TRIGGER_TOKEN_INVALID');
    // A secret being rotated: either signature is accepted.
    clock.advance(60_000);
    const next = sign(body);
    expect((await service.webhook(id, { ...next, signature: `v1=bad, ${next.signature}` })).firing).to.not.equal(null);
  });

  it('TR-04: the run is told the body by size, type and digest only', async () => {
    const { service, created, sign, admission } = await signedHook();
    await service.webhook(created.schedule.id, { ...sign(body), contentType: 'application/json' });
    const trigger = admission.requests[0]?.input.trigger;
    expect(trigger?.webhook).to.deep.equal({
      receivedAt: '2026-07-01T00:00:00.000Z',
      bodyBytes: body.length,
      bodySha256: `sha256:${createHash('sha256').update(body).digest('hex')}`,
      contentType: 'application/json',
    });
    expect(JSON.stringify(admission.requests[0]?.input)).to.not.include('PH-1');
  });

  it("TR-04: a body over the org's cap is refused and fires nothing", async () => {
    const { service, created, sign, settings, admission } = await signedHook();
    settings.maxBytes = 8;
    expect(await refusal(service.webhook(created.schedule.id, sign(body)))).to.equal('TRIGGER_PAYLOAD_TOO_LARGE');
    expect(admission.requests).to.deep.equal([]);
  });

  it('TR-04: past the per-minute rate a delivery is refused with a retry time; the next minute takes it', async () => {
    const { service, created, sign, settings, clock, logs } = await signedHook();
    settings.perMinute = 2;
    const id = created.schedule.id;
    await service.webhook(id, sign(body));
    await service.webhook(id, sign(body));
    let refused: unknown;
    try {
      await service.webhook(id, sign(body));
    } catch (error) {
      refused = error;
    }
    expect(refused).to.be.instanceOf(HarnessTriggerRefusedError);
    expect(refused).to.deep.include({ code: 'TRIGGER_RATE_LIMITED', retryAfterS: 60 });
    expect(logs.some((l) => l.event === 'schedule.webhook_rate_limited')).to.equal(true);
    clock.advance(60_000);
    expect((await service.webhook(id, sign(body))).firing).to.not.equal(null);
  });

  it('TR-04: a token webhook (a caller that cannot sign) is still only its token', async () => {
    const w = serviceWorld();
    const created = await w.service.create(alice, { ...CRON, trigger: { kind: 'webhook', auth: 'token' } });
    expect(created.webhookSecret).to.equal(undefined);
    expect(created.webhookToken).to.match(/^pxt_/);
    expect((await w.service.webhook(created.schedule.id, { token: created.webhookToken!, body })).firing).to.not.equal(null);
  });
});

describe('TRIGGERS2 TR-06: overlap policy', () => {
  async function twoSlots(overlap: AgentSchedule['overlap']) {
    const w = world();
    const hourly = { kind: 'cron' as const, cron: '0 * * * *', timeZone: 'UTC', catchUp: 'skip' as const };
    const schedule = await due(w, { trigger: hourly, ...(overlap !== undefined ? { overlap } : {}) });
    await w.firer.tick();
    w.clock.advance(HOUR);
    await w.firer.tick();
    return { w, schedule };
  }

  it('TR-06: skip (the default): a slot while the last run is active is skipped, and the next after it ends runs', async () => {
    const { w, schedule } = await twoSlots(undefined);
    expect(w.admission.requests).to.have.length(1);
    const [second, first] = await w.store.firings(ORG, schedule.id, 2);
    expect(second).to.deep.include({ status: 'skipped', reason: 'overlap_skip' });
    w.runs.statuses.set(first!.runId!, 'completed');
    w.clock.advance(HOUR);
    await w.firer.tick();
    expect(w.admission.requests).to.have.length(2);
  });

  it('TR-06: queue: the slot waits behind the active run, one at most, and runs when that run ends', async () => {
    const { w, schedule } = await twoSlots('queue');
    const [queued, first] = await w.store.firings(ORG, schedule.id, 2);
    expect(queued).to.deep.include({ status: 'pending', queuedBehind: first!.runId, attempts: 1 });
    w.clock.advance(HOUR);
    await w.firer.tick();
    expect((await w.store.firings(ORG, schedule.id, 1))[0]).to.deep.include({ status: 'skipped', reason: 'overlap_queue_full' });
    // Still active at the recheck: it waits again, without spending an attempt.
    w.clock.advance(OVERLAP_RECHECK_MS);
    await w.firer.tick();
    expect(await w.store.getFiring(ORG, queued!.id)).to.deep.include({ status: 'pending', attempts: 1 });
    expect(w.admission.requests).to.have.length(1);
    await w.firer.runEnded(ended(w, first!.runId!, 'completed', w.admission.requests[0]!.idempotencyKey));
    await w.firer.tick();
    expect(await w.store.getFiring(ORG, queued!.id)).to.deep.include({ status: 'admitted' });
    expect(w.admission.requests.map((r) => r.trigger.slotKey)).to.deep.equal([first!.slotKey, queued!.slotKey]);
  });

  it('TR-06: allow: both run', async () => {
    const { w } = await twoSlots('allow');
    expect(w.admission.requests).to.have.length(2);
  });

  it("TR-06: a person's run now is never held by the policy", async () => {
    const { w, schedule } = await twoSlots('skip');
    await w.firer.runNow((await w.store.get(ORG, schedule.id))!, 'now-1');
    expect(w.admission.requests).to.have.length(2);
  });

  it('TR-06: a new schedule skips by default; the policy is shown and audited with it', async () => {
    const w = serviceWorld();
    const { schedule } = await w.service.create(alice, CRON);
    expect(schedule).to.deep.include({ overlap: 'skip', notify: ['in_app'] });
    const queued = await w.service.create(alice, { ...CRON, overlap: 'queue', notify: ['in_app', 'email'] });
    expect(queued.schedule).to.deep.include({ overlap: 'queue', notify: ['in_app', 'email'] });
  });
});

describe('TRIGGERS2 TR-07: completion notices', () => {
  it('TR-07: a fired run that ends is told to its actor once, on the channels the schedule names', async () => {
    const w = world();
    const schedule = await due(w, { notify: ['in_app', 'email'] });
    await w.firer.tick();
    const [firing] = await w.store.firings(ORG, schedule.id, 1);
    w.runs.statuses.set(firing!.runId!, 'completed');
    await Promise.all([w.firer.tick(), w.newFirer().tick()]);
    await w.firer.tick();
    expect(w.notifier.ends).to.deep.equal([
      {
        orgId: ORG,
        actorId: ALICE,
        scheduleId: schedule.id,
        scheduleName: schedule.name,
        agentKey: 'support-agent',
        sessionId: firing!.sessionId,
        runId: firing!.runId,
        status: 'completed',
        channels: ['in_app', 'email'],
      },
    ]);
    expect(await w.store.watched(w.clock.value, 10)).to.deep.equal([]);
  });

  it("TR-07: the run's own end tells it at once; the watch does not tell it again", async () => {
    const w = world();
    const schedule = await due(w);
    await w.firer.tick();
    const [firing] = await w.store.firings(ORG, schedule.id, 1);
    await w.firer.runEnded(ended(w, firing!.runId!, 'failed', w.admission.requests[0]!.idempotencyKey));
    await w.firer.tick();
    expect(w.notifier.ends.map((n) => [n.status, n.channels])).to.deep.equal([['failed', ['in_app']]]);
    // A run no schedule fired is not the firer's.
    await w.firer.runEnded({ orgId: ORG, runId: 'r', idempotencyKey: 'chat:1', status: 'completed' });
    expect(w.notifier.ends).to.have.length(1);
  });

  it("GA-05 (G-07): the notice carries the run's end-of-run summary; a summary that cannot be read does not stop the notice", async () => {
    const endWith = async (read: () => Promise<never>) => {
      const w = world();
      const asked: unknown[] = [];
      const firer = new ScheduleFirer({
        store: w.store,
        admission: w.admission,
        actors: w.actors,
        setting: w.setting,
        runs: w.runs,
        notifier: w.notifier,
        settings: w.settings,
        digests: {
          digest: async (orgId, runId, status) => {
            asked.push([orgId, runId, status]);
            return read();
          },
        },
        logger: recordingLogger(w.logs),
      });
      const schedule = await due(w);
      await firer.tick();
      const [firing] = await w.store.firings(ORG, schedule.id, 1);
      await firer.runEnded(ended(w, firing!.runId!, 'failed', w.admission.requests[0]!.idempotencyKey));
      return { w, asked, runId: firing!.runId };
    };
    const digest = { outcome: 'failed' } as never;
    const read = await endWith(async () => digest);
    expect(read.asked).to.deep.equal([[ORG, read.runId, 'failed']]);
    expect(read.w.notifier.ends).to.have.length(1);
    expect(read.w.notifier.ends[0]).to.deep.include({ status: 'failed', digest });

    const broken = await endWith(async () => {
      throw new Error('journal down');
    });
    expect(broken.w.notifier.ends).to.have.length(1);
    expect(broken.w.notifier.ends[0]).to.not.have.property('digest');
    expect(broken.w.logs.map((l) => l.event)).to.include('schedule.run_digest_failed');
  });

  it('TR-07: a run expired at its deadline is still told (the watch outlives the deadline)', async () => {
    const w = world();
    const schedule = await due(w, { deadlineS: 300 });
    await w.firer.tick();
    const [firing] = await w.store.firings(ORG, schedule.id, 1);
    w.clock.value = (firing!.watchUntil as number) + WATCH_GRACE_MS - 1_000;
    w.runs.statuses.set(firing!.runId!, 'failed');
    await w.firer.tick();
    expect(w.notifier.ends.map((n) => n.status)).to.deep.equal(['failed']);
  });

  it("TR-07: only the channels the org allows; a schedule cannot ask for one it does not", async () => {
    const w = serviceWorld();
    w.settings.channels = ['in_app'];
    expect(await refusal(w.service.create(alice, { ...CRON, notify: ['email'] }))).to.equal('SCHEDULE_INVALID');
    w.settings.channels = ['in_app', 'email', 'slack'];
    const { schedule } = await w.service.create(alice, { ...CRON, notify: ['in_app', 'email', 'slack'] });
    w.settings.channels = ['in_app', 'slack'];
    const stored = (await w.store.get(ORG, schedule.id))!;
    w.clock.value = (stored.nextFireAt as number) + 10_000;
    await w.firer.tick();
    const [firing] = await w.store.firings(ORG, schedule.id, 1);
    w.runs.statuses.set(firing!.runId!, 'completed');
    await w.firer.tick();
    expect(w.notifier.ends.map((n) => n.channels)).to.deep.equal([['in_app', 'slack']]);
  });

  it('TR-07: a schedule that asked for no channel is told nothing', async () => {
    const w = world();
    const schedule = await due(w, { notify: [] });
    await w.firer.tick();
    const [firing] = await w.store.firings(ORG, schedule.id, 1);
    w.runs.statuses.set(firing!.runId!, 'completed');
    await w.firer.tick();
    expect(w.notifier.ends).to.deep.equal([]);
  });
});

describe('TRIGGERS2 keys', () => {
  it("a scheduled run's idempotency key names its schedule and slot; any other key names none", () => {
    expect(parseScheduleIdempotencyKey(scheduleIdempotencyKey('sch-1', 'c:2026-07-06T07:00:00.000Z'))).to.deep.equal({
      scheduleId: 'sch-1',
      slotKey: 'c:2026-07-06T07:00:00.000Z',
    });
    expect(parseScheduleIdempotencyKey('chat:abc')).to.equal(null);
  });
});
