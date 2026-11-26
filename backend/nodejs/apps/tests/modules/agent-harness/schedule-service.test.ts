/** P8a SV: who may do what with a schedule, over the real firer and the in-memory store. */
import { expect } from 'chai';
import { InMemoryScheduleStore } from '../../../src/modules/agent-harness/adapters/in-memory-schedules';
import { ScheduleFirer } from '../../../src/modules/agent-harness/application/schedule-firer';
import {
  CreateScheduleRequest,
  hashTriggerToken,
  ScheduleCaller,
  ScheduleService,
} from '../../../src/modules/agent-harness/application/schedule-service';
import { HarnessScheduleRefusedError } from '../../../src/modules/agent-harness/domain/errors';
import {
  ADMIN_USER,
  ALICE,
  BOB,
  FakeActors,
  FakeAdmission,
  FakeSetting,
  ORG,
  TestClock,
  adminReach,
} from './support/schedules';
import { testControlPlane } from '../control-plane/support/in-memory';

const alice: ScheduleCaller = { orgId: ORG, userId: ALICE, via: 'session' };
const bob: ScheduleCaller = { orgId: ORG, userId: BOB, via: 'session' };
const admin: ScheduleCaller = { orgId: ORG, userId: ADMIN_USER, via: 'session' };

const CRON: CreateScheduleRequest = {
  name: 'Weekly support digest',
  agentKey: 'support-agent',
  input: { query: 'Summarise new support tickets to #support-leads' },
  trigger: { kind: 'cron', cron: '0 9 * * 1', timeZone: 'Europe/Berlin' },
};

async function code(promise: Promise<unknown>): Promise<string> {
  try {
    await promise;
  } catch (error) {
    expect(error).to.be.instanceOf(HarnessScheduleRefusedError);
    return (error as HarnessScheduleRefusedError).code;
  }
  throw new Error('expected a refusal');
}

describe('P8a schedule service (SV)', () => {
  let clock: TestClock;
  let store: InMemoryScheduleStore;
  let admission: FakeAdmission;
  let actors: FakeActors;
  let setting: FakeSetting;
  let service: ScheduleService;
  let tokens: number;

  beforeEach(() => {
    clock = new TestClock(Date.parse('2026-07-01T00:00:00.000Z'));
    store = new InMemoryScheduleStore(clock);
    admission = new FakeAdmission();
    actors = new FakeActors();
    setting = new FakeSetting();
    tokens = 0;
    const firer = new ScheduleFirer({ store, admission, actors, setting });
    service = new ScheduleService({ transaction: testControlPlane(),
      store,
      firer,
      actors,
      setting,
      // The cases below that act on someone else's schedule are a build's reach; SV-2b holds the default.
      reach: adminReach(actors),
      newToken: () => `pxt_secret_${String((tokens += 1))}`,
    });
  });

  it('SV-1: create as the caller: active, cursor at the next slot, the template only to its owner', async () => {
    const { schedule, webhookToken } = await service.create(alice, CRON);
    expect(webhookToken).to.equal(undefined);
    expect(schedule).to.deep.include({
      actorId: ALICE,
      status: 'active',
      nextFireAt: '2026-07-06T07:00:00.000Z',
      deadlineS: 8 * 60 * 60,
      maxCostUsd: null,
    });
    expect(schedule.trigger).to.deep.equal({ ...CRON.trigger, catchUp: 'skip' });
    expect(schedule.input).to.deep.equal({ query: CRON.input.query, chatMode: 'auto' });
    expect((await service.get(admin, schedule.id)).input).to.equal(undefined);
  });

  it('SV-1: a webhook schedule answers its token once and stores only its hash', async () => {
    const { schedule, webhookToken } = await service.create(alice, {
      ...CRON,
      trigger: { kind: 'webhook', auth: 'token' },
    });
    expect(webhookToken).to.equal('pxt_secret_1');
    const stored = await store.get(ORG, schedule.id);
    expect(stored?.webhookTokenHash).to.equal(hashTriggerToken('pxt_secret_1'));
    expect(JSON.stringify(await service.get(alice, schedule.id))).to.not.include('pxt_secret_1');
  });

  it('UX3 SE-1: with durable agent chat off for the org, no schedule is made that could never run', async () => {
    let useHarness = false;
    const gated = new ScheduleService({ transaction: testControlPlane(),
      store,
      firer: new ScheduleFirer({ store, admission, actors, setting }),
      actors,
      setting,
      rollout: {
        decideForOrg: async () => ({ useHarness, harnessMajor: 2, reason: useHarness ? 'enabled' : 'kill_switch', harnessOn: true, resumeInFlightRuns: true }),
      },
    });
    expect(await code(gated.create(alice, CRON))).to.equal('SCHEDULED_RUNS_UNAVAILABLE');
    expect(await store.list(ORG, { actorId: ALICE }, 10)).to.deep.equal([]);
    useHarness = true;
    expect((await gated.create(alice, CRON)).schedule.status).to.equal('active');
  });

  it('SV-1: a service account cannot own a schedule; the flag off refuses', async () => {
    expect(await code(service.create({ ...alice, via: 'service' }, CRON))).to.equal('SCHEDULE_FORBIDDEN');
    setting.on = false;
    expect(await code(service.create(alice, CRON))).to.equal('SCHEDULED_RUNS_DISABLED');
  });

  it('SV-2: list own; everyone\'s only for a caller the reach lets see them; never another org', async () => {
    const a = (await service.create(alice, CRON)).schedule;
    const b = (await service.create(bob, CRON)).schedule;
    await service.create({ ...alice, orgId: 'org-2' }, CRON);
    expect((await service.list(alice, 50)).map((s) => s.id)).to.deep.equal([a.id]);
    expect(await code(service.list(alice, 50, true))).to.equal('SCHEDULE_FORBIDDEN');
    expect((await service.list(admin, 50, true)).map((s) => s.id).sort()).to.deep.equal([a.id, b.id].sort());
  });

  it('SV-2b: with no reach bound, nobody sees or acts on another person\'s schedule, an org admin included', async () => {
    const own = new ScheduleService({
      transaction: testControlPlane(),
      store,
      firer: new ScheduleFirer({ store, admission, actors, setting }),
      actors,
      setting,
    });
    const { schedule } = await own.create(alice, CRON);
    expect((await own.list(admin, 50)).map((s) => s.id)).to.deep.equal([]);
    expect(await code(own.list(admin, 50, true))).to.equal('SCHEDULE_FORBIDDEN');
    for (const call of [own.get(admin, schedule.id), own.pause(admin, schedule.id), own.delete(admin, schedule.id)]) {
      expect(await code(call)).to.equal('SCHEDULE_NOT_FOUND');
    }
    expect((await own.pause(alice, schedule.id)).status).to.equal('paused');
  });

  it("SV-3: another member's schedule does not exist for them", async () => {
    const { schedule } = await service.create(alice, CRON);
    for (const call of [
      service.get(bob, schedule.id),
      service.pause(bob, schedule.id),
      service.resume(bob, schedule.id),
      service.delete(bob, schedule.id),
      service.runNow(bob, schedule.id, 'k'),
      service.firings(bob, schedule.id, 10),
    ]) {
      expect(await code(call)).to.equal('SCHEDULE_NOT_FOUND');
    }
    expect(admission.requests).to.deep.equal([]);
  });

  it("SV-4: an admin may read, pause, resume and delete someone's schedule, never run it", async () => {
    const { schedule } = await service.create(alice, CRON);
    expect((await service.pause(admin, schedule.id))).to.deep.include({ status: 'paused', statusReason: 'by_admin', nextFireAt: null });
    expect((await service.resume(admin, schedule.id))).to.deep.include({ status: 'active', nextFireAt: '2026-07-06T07:00:00.000Z' });
    expect(await code(service.runNow(admin, schedule.id, 'k'))).to.equal('SCHEDULE_FORBIDDEN');
    expect(admission.requests).to.deep.equal([]);
    await service.delete(admin, schedule.id);
    expect(await store.get(ORG, schedule.id)).to.equal(null);
  });

  it('SV-5: run now is idempotent by key, acts as the actor, works while paused', async () => {
    const { schedule } = await service.create(alice, CRON);
    const first = await service.runNow(alice, schedule.id, 'key-1');
    const again = await service.runNow(alice, schedule.id, 'key-1');
    expect(first).to.deep.include({ kind: 'manual', status: 'admitted', runId: 'run-1', conversationId: 'ses-1' });
    expect(again).to.deep.equal(first);
    expect(admission.requests).to.have.length(1);
    expect(admission.requests[0]).to.deep.include({ actorId: ALICE, authorization: 'Bearer token-of-alice' });
    await service.pause(alice, schedule.id);
    const paused = await service.runNow(alice, schedule.id, 'key-2');
    expect(paused).to.deep.include({ status: 'admitted', runId: 'run-2' });
    expect(admission.requests).to.have.length(2);
  });

  it('SV-5/SV-8: a disabled schedule neither runs nor resumes', async () => {
    const { schedule } = await service.create(alice, CRON);
    // Its agent was deleted (H2): the schedule stays, disabled; a deleted user's go entirely (CLEANUP-G2).
    await store.eraseAgent(ORG, 'support-agent');
    expect(await code(service.runNow(alice, schedule.id, 'k'))).to.equal('SCHEDULE_DISABLED');
    expect(await code(service.resume(admin, schedule.id))).to.equal('SCHEDULE_DISABLED');
    expect(await code(service.pause(admin, schedule.id))).to.equal('SCHEDULE_DISABLED');
    // Deleting it is always possible.
    await service.delete(admin, schedule.id);
  });

  it('SV-6: a connector trigger needs an org admin', async () => {
    const connector: CreateScheduleRequest = {
      ...CRON,
      trigger: { kind: 'connector', connectorId: 'c1' },
    };
    expect(await code(service.create(alice, connector))).to.equal('SCHEDULE_FORBIDDEN');
    const { schedule } = await service.create(admin, connector);
    expect(schedule.trigger).to.deep.equal({ kind: 'connector', connectorId: 'c1', coalesceS: 300 });
  });

  it('SV-7: webhook: the right token fires once per window; a wrong token and a missing schedule answer alike', async () => {
    const { schedule, webhookToken } = await service.create(alice, {
      ...CRON,
      trigger: { kind: 'webhook', coalesceS: 60, auth: 'token' },
    });
    const hook = (token: string, id = schedule.id) => service.webhook(id, { token, body: Buffer.alloc(0) });
    const fired = await hook(webhookToken!);
    expect(fired.firing).to.deep.include({ kind: 'event', status: 'admitted' });
    expect((await hook(webhookToken!)).firing).to.equal(null);
    expect(admission.requests).to.have.length(1);
    expect(await code(hook('pxt_wrong'))).to.equal('SCHEDULE_TRIGGER_TOKEN_INVALID');
    expect(await code(hook(webhookToken!, 'missing'))).to.equal('SCHEDULE_TRIGGER_TOKEN_INVALID');
    const cron = (await service.create(alice, CRON)).schedule;
    expect(await code(hook(webhookToken!, cron.id))).to.equal('SCHEDULE_TRIGGER_TOKEN_INVALID');
    await service.pause(alice, schedule.id);
    clock.advance(120_000);
    expect(await hook(webhookToken!)).to.deep.equal({ firing: null, ignored: 'paused' });
    setting.on = false;
    expect(await code(hook(webhookToken!))).to.equal('SCHEDULED_RUNS_DISABLED');
  });

  it('SV-1: invalid schedules are refused naming the field', async () => {
    const bad = (await code(service.create(alice, { ...CRON, trigger: { kind: 'cron', cron: '* * * * *', timeZone: 'UTC' } })));
    expect(bad).to.equal('SCHEDULE_INVALID');
    expect(await code(service.create(alice, { ...CRON, deadlineS: 60 }))).to.equal('SCHEDULE_INVALID');
    expect(await code(service.create(alice, { ...CRON, trigger: { kind: 'webhook', coalesceS: 1 } }))).to.equal('SCHEDULE_INVALID');
  });

  it('BG-3: a firing budget above the ceiling is refused, nothing stored', async () => {
    expect(await code(service.create(alice, { ...CRON, maxCostUsd: 6 }))).to.equal('SCHEDULE_BUDGET_EXCEEDS_CEILING');
    expect(await store.list(ORG, {}, 10)).to.deep.equal([]);
    expect((await service.create(alice, { ...CRON, maxCostUsd: 0.25 })).schedule.maxCostUsd).to.equal(0.25);
  });

  it('SV-7: the per-actor limit', async function () {
    // 51 creates each probe 50 cron slots in a time zone (~150 ms on a loaded host).
    this.timeout(60_000);
    for (let i = 0; i < 50; i += 1) await service.create(alice, CRON);
    expect(await code(service.create(alice, CRON))).to.equal('SCHEDULE_LIMIT');
    await service.create(bob, CRON);
  });

  it('delete is allowed while the flag is off', async () => {
    const { schedule } = await service.create(alice, CRON);
    setting.on = false;
    expect(await code(service.get(alice, schedule.id))).to.equal('SCHEDULED_RUNS_DISABLED');
    await service.delete(alice, schedule.id);
    expect(await store.get(ORG, schedule.id)).to.equal(null);
  });
});
