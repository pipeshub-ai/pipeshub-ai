/**
 * ADR-SCH-01: a schedule asked for in chat. D1, the one-time trigger (validation, its one slot fired once whatever
 * the number of firers, lateness, resume and run now refused once it ran); D3, the worker's route that makes it only
 * on the person's own approval of exactly that call, read from the run's journal, once per decision.
 */
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { load as loadYaml } from 'js-yaml';
import sinon from 'sinon';
import { scheduleCreateArgumentsSchema } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/schedule-create-arguments';
import { InMemoryScheduleStore } from '../../../src/modules/agent-harness/adapters/in-memory-schedules';
import {
  type ChatScheduleOptions,
  ChatScheduleService,
  chatScheduleId,
  SCHEDULE_CREATE_TOOL_ID,
  SCHEDULE_CREATED_EVENT,
  type ScheduleToolArguments,
} from '../../../src/modules/agent-harness/application/chat-schedules';
import { RunAdmissionScheduledRuns } from '../../../src/modules/agent-harness/application/scheduled-run-admission';
import { ScheduleFirer } from '../../../src/modules/agent-harness/application/schedule-firer';
import {
  type ScheduleCaller,
  ScheduleService,
} from '../../../src/modules/agent-harness/application/schedule-service';
import { canonicalDigest, canonicalSha256 } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { HarnessScheduleRefusedError } from '../../../src/modules/agent-harness/domain/errors';
import { type HarnessRun, resolvedData } from '../../../src/modules/agent-harness/domain/execution';
import { approvalBindingDigests } from '../../../src/modules/agent-harness/domain/interactions';
import type { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import {
  assertOnce,
  decideOnce,
  firingInput,
  MAX_ONCE_LATENESS_MS,
  ONCE_TOO_LATE,
  onceSlotKey,
  RAN_ONCE,
  triggerContext,
} from '../../../src/modules/agent-harness/domain/schedules';
import { AgentHarnessChatScheduleController } from '../../../src/modules/agent-harness/transport/chat-schedule-controller';
import { testControlPlane } from '../control-plane/support/in-memory';
import {
  aSchedule,
  ALICE,
  BOB,
  FakeActors,
  FakeAdmission,
  FakeNotifier,
  FakeSetting,
  ORG,
  T0,
  TestClock,
} from './support/schedules';

const MIN = 60_000;
const alice: ScheduleCaller = { orgId: ORG, userId: ALICE, via: 'session' };
/** 47 minutes after T0 (06:00Z), written in Berlin's summer offset. */
const AT = '2026-07-06T08:47:00+02:00';
const AT_MS = Date.parse(AT);

async function code(promise: Promise<unknown>): Promise<string> {
  try {
    await promise;
  } catch (error) {
    expect(error).to.be.instanceOf(HarnessScheduleRefusedError);
    return (error as HarnessScheduleRefusedError).code;
  }
  throw new Error('expected a refusal');
}

describe('ADR-SCH-01 D1: a one-time schedule', () => {
  describe('domain', () => {
    it('takes an ISO 8601 instant with its offset, 60 s to 366 days ahead, in a known zone', () => {
      expect(assertOnce(AT, 'Europe/Berlin', T0)).to.equal(Date.parse('2026-07-06T06:47:00.000Z'));
      expect(assertOnce('2026-07-06T06:01:00Z', 'UTC', T0)).to.equal(T0 + MIN);
      const field = (at: string, zone = 'UTC', now = T0): string | undefined => {
        try {
          assertOnce(at, zone, now);
          return undefined;
        } catch (error) {
          return (error as HarnessScheduleRefusedError).field;
        }
      };
      expect(field('2026-07-06T06:00:59Z')).to.equal('at');
      expect(field('2026-07-06T08:47:00')).to.equal('at');
      expect(field('tomorrow at 9')).to.equal('at');
      expect(field('2027-07-08T06:00:00Z')).to.equal('at');
      expect(field('2027-07-07T06:00:00Z')).to.equal(undefined);
      expect(field(AT, 'Mars/Olympus')).to.equal('timeZone');
      const NEW_YEAR = Date.parse('2027-01-01T00:00:00Z');
      expect(field('2027-02-30T09:00:00Z', 'UTC', NEW_YEAR)).to.equal('at');
      expect(field('2027-04-31T09:00:00+05:30', 'UTC', NEW_YEAR)).to.equal('at');
      expect(field('2027-03-01T24:00:00Z', 'UTC', NEW_YEAR)).to.equal('at');
      expect(field('2028-02-29T09:00:00-08:00', 'UTC', Date.parse('2028-01-01T00:00:00Z'))).to.equal(undefined);
      expect(field('2027-02-29T09:00:00Z', 'UTC', NEW_YEAR)).to.equal('at');
    });

    it('fires its slot when due and up to 24 h late; later the slot is skipped; the cursor always goes', () => {
      expect(decideOnce(AT_MS, AT_MS - 1)).to.deep.equal({ kind: 'wait' });
      expect(decideOnce(AT_MS, AT_MS + 20 * MIN)).to.deep.include({ kind: 'fire', slotAt: AT_MS, nextFireAt: null, late: true });
      expect(decideOnce(AT_MS, AT_MS + MAX_ONCE_LATENESS_MS)).to.deep.include({ kind: 'fire' });
      expect(decideOnce(AT_MS, AT_MS + MAX_ONCE_LATENESS_MS + 1)).to.deep.include({ kind: 'skip', nextFireAt: null });
      expect(onceSlotKey(AT_MS)).to.equal('o:2026-07-06T06:47:00.000Z');
    });

    it('its run is told it is a one-time firing, on the person’s clock', () => {
      const schedule = aSchedule({ trigger: { kind: 'once', at: '2026-07-06T06:47:00.000Z', timeZone: 'Europe/Berlin' } });
      const context = triggerContext(schedule, { id: 'f1', kind: 'once', slotAt: AT_MS }, false);
      expect(context).to.deep.equal({ kind: 'once', scheduleId: schedule.id, firingId: 'f1', slotAt: '2026-07-06T06:47:00.000Z' });
      expect(firingInput(schedule, AT_MS, context)).to.deep.include({ timezone: 'Europe/Berlin', currentTime: '2026-07-06T06:47:00.000Z' });
    });
  });

  describe('service and firer', () => {
    let clock: TestClock;
    let store: InMemoryScheduleStore;
    let admission: FakeAdmission;
    let actors: FakeActors;
    let notifier: FakeNotifier;
    let service: ScheduleService;
    const firer = () =>
      new ScheduleFirer({ store, admission, actors, setting: new FakeSetting(), notifier });

    beforeEach(() => {
      clock = new TestClock();
      store = new InMemoryScheduleStore(clock);
      admission = new FakeAdmission();
      actors = new FakeActors();
      notifier = new FakeNotifier();
      service = new ScheduleService({
        transaction: testControlPlane(),
        store,
        firer: firer(),
        actors,
        setting: new FakeSetting(),
      });
    });

    const once = () =>
      service.create(alice, {
        name: 'Joke in a while',
        input: { query: 'Tell me a short, clean joke.' },
        trigger: { kind: 'once', at: AT, timeZone: 'Europe/Berlin' },
      });

    it('is made with its instant in UTC as its cursor; a time too soon is refused', async () => {
      const { schedule } = await once();
      expect(schedule).to.deep.include({
        status: 'active',
        nextFireAt: '2026-07-06T06:47:00.000Z',
        trigger: { kind: 'once', at: '2026-07-06T06:47:00.000Z', timeZone: 'Europe/Berlin' },
      });
      expect(
        await code(
          service.create(alice, {
            name: 'Too soon',
            input: { query: 'x' },
            trigger: { kind: 'once', at: '2026-07-06T06:00:30Z', timeZone: 'UTC' },
          }),
        ),
      ).to.equal('SCHEDULE_INVALID');
    });

    it('two firers fire its slot exactly once, and the claim disables it (ran_once)', async () => {
      const { schedule } = await once();
      clock.value = AT_MS + 5_000;
      const fired = await Promise.all([firer().fireDue(), firer().fireDue(), firer().fireDue()]);
      expect(fired.reduce((a, b) => a + b, 0)).to.equal(1);
      expect(admission.admitted).to.equal(1);
      expect(admission.requests[0]?.input.trigger).to.deep.include({ kind: 'once' });
      const after = await store.get(ORG, schedule.id);
      expect(after).to.deep.include({ status: 'disabled', statusReason: RAN_ONCE, nextFireAt: null });
      const firings = await store.firings(ORG, schedule.id, 10);
      expect(firings.map((f) => [f.kind, f.slotKey, f.status])).to.deep.equal([
        ['once', 'o:2026-07-06T06:47:00.000Z', 'admitted'],
      ]);
      clock.advance(60 * MIN);
      expect(await firer().fireDue()).to.equal(0);
    });

    it('a retried firing of its claimed slot still runs although the schedule is disabled', async () => {
      const { schedule } = await once();
      clock.value = AT_MS;
      admission.refusals.push('unavailable');
      await firer().fireDue();
      expect(admission.admitted).to.equal(0);
      clock.advance(10 * MIN);
      await firer().retryStale();
      expect(admission.admitted).to.equal(1);
      expect((await store.firings(ORG, schedule.id, 10))[0]?.status).to.equal('admitted');
    });

    it('a firing retaken more than a day after its slot is skipped as too late, and its person told', async () => {
      const { schedule } = await once();
      clock.value = AT_MS;
      admission.refusals.push('unavailable');
      await firer().fireDue();
      clock.advance(MAX_ONCE_LATENESS_MS + MIN);
      await firer().retryStale();
      expect(admission.admitted).to.equal(0);
      expect((await store.firings(ORG, schedule.id, 10)).map((f) => [f.status, f.reason])).to.deep.equal([
        ['skipped', ONCE_TOO_LATE],
      ]);
      expect(notifier.refusals.map((n) => n.reason)).to.deep.equal([ONCE_TOO_LATE]);
    });

    it('too late while the org has scheduled runs off: skipped and disabled, nobody told', async () => {
      const { schedule } = await once();
      clock.value = AT_MS + MAX_ONCE_LATENESS_MS + MIN;
      const off = new FakeSetting();
      off.on = false;
      await new ScheduleFirer({ store, admission, actors, setting: off, notifier }).fireDue();
      expect((await store.get(ORG, schedule.id))?.status).to.equal('disabled');
      expect(notifier.refusals).to.deep.equal([]);
      // ADR-SCH-02 D1: the org's harness is not part of scheduling, so with it off the person is still told.
      clock.value = T0;
      const { schedule: other } = await once();
      clock.value = AT_MS + 2 * MAX_ONCE_LATENESS_MS;
      await new ScheduleFirer({
        store,
        admission,
        actors,
        setting: new FakeSetting(),
        notifier,
        harnessOn: async () => false,
      }).fireDue();
      expect((await store.get(ORG, other.id))?.status).to.equal('disabled');
      expect(notifier.refusals.map((n) => n.reason)).to.deep.equal([ONCE_TOO_LATE]);
    });

    it('run now takes its one slot: two at once, or with a tick, make one firing; the loser is told it ran', async () => {
      const { schedule } = await once();
      const both = await Promise.allSettled([
        service.runNow(alice, schedule.id, 'k1'),
        service.runNow(alice, schedule.id, 'k2'),
      ]);
      expect(both.filter((r) => r.status === 'fulfilled')).to.have.length(1);
      const lost = both.find((r) => r.status === 'rejected') as PromiseRejectedResult;
      expect((lost.reason as HarnessScheduleRefusedError).code).to.equal('SCHEDULE_RAN_ONCE');
      expect(admission.admitted).to.equal(1);
      expect(await store.get(ORG, schedule.id)).to.deep.include({ status: 'disabled', statusReason: RAN_ONCE });
      expect(await code(service.runNow(alice, schedule.id, 'k3'))).to.equal('SCHEDULE_RAN_ONCE');

      clock.value = T0;
      const { schedule: due } = await once();
      clock.value = AT_MS;
      await Promise.allSettled([firer().fireDue(), service.runNow(alice, due.id, 'k4')]);
      expect(await store.firings(ORG, due.id, 10)).to.have.length(1);
      expect(admission.admitted).to.equal(2);
    });

    it('run now of a paused one-time schedule runs it once and disables it', async () => {
      const { schedule } = await once();
      await service.pause(alice, schedule.id);
      await service.runNow(alice, schedule.id, 'k1');
      expect(admission.admitted).to.equal(1);
      expect(await store.get(ORG, schedule.id)).to.deep.include({ status: 'disabled', statusReason: RAN_ONCE });
      expect(await code(service.resume(alice, schedule.id))).to.equal('SCHEDULE_RAN_ONCE');
    });

    it('due while the org’s harness is off and the deployment has no classic runner: refused, never admitted durable', async () => {
      const { schedule } = await once();
      clock.value = AT_MS;
      await new ScheduleFirer({
        store,
        admission,
        actors,
        setting: new FakeSetting(),
        notifier,
        harnessOn: async () => false,
      }).fireDue();
      expect(admission.admitted).to.equal(0);
      expect((await store.firings(ORG, schedule.id, 10)).map((f) => [f.status, f.reason])).to.deep.equal([
        ['refused', 'runner_unavailable'],
      ]);
      expect(notifier.refusals.map((n) => n.reason)).to.deep.equal(['runner_unavailable']);
    });

    it('more than a day late: skipped, disabled, and its person told', async () => {
      const { schedule } = await once();
      clock.value = AT_MS + MAX_ONCE_LATENESS_MS + MIN;
      expect(await firer().fireDue()).to.equal(0);
      expect(admission.admitted).to.equal(0);
      expect((await store.firings(ORG, schedule.id, 10)).map((f) => [f.status, f.reason])).to.deep.equal([
        ['skipped', ONCE_TOO_LATE],
      ]);
      expect((await store.get(ORG, schedule.id))?.status).to.equal('disabled');
      expect(notifier.refusals.map((n) => n.reason)).to.deep.equal([ONCE_TOO_LATE]);
    });

    it('once it ran, resume and run now are refused SCHEDULE_RAN_ONCE; delete removes it', async () => {
      const { schedule } = await once();
      clock.value = AT_MS;
      await firer().fireDue();
      expect(await code(service.resume(alice, schedule.id))).to.equal('SCHEDULE_RAN_ONCE');
      expect(await code(service.runNow(alice, schedule.id, 'k1'))).to.equal('SCHEDULE_RAN_ONCE');
      await service.delete(alice, schedule.id);
      expect(await store.get(ORG, schedule.id)).to.equal(null);
    });

    it('paused past its time, it cannot be resumed into a slot that never comes', async () => {
      const { schedule } = await once();
      await service.pause(alice, schedule.id);
      clock.value = AT_MS + MIN;
      expect(await code(service.resume(alice, schedule.id))).to.equal('SCHEDULE_INVALID');
      clock.value = T0 + MIN;
      expect((await service.resume(alice, schedule.id)).nextFireAt).to.equal('2026-07-06T06:47:00.000Z');
    });
  });
});

// Computed by the worker's own functions (backend/python, `harness_core.effects.types.args_digest` and
// `harness_core.interactions.hold.approval_interaction_id`); Node must name the same call with the same ids.
const PYTHON_FIXTURE = [
  {
    runId: 'run-0193ab',
    arguments: {
      name: 'Joke in five minutes',
      ask: 'Tell me a short, clean joke.',
      when: { kind: 'once', at: '2026-11-26T15:47:00-08:00', timeZone: 'America/Los_Angeles' },
      notify: ['in_app'],
    },
    argsDigest: 'sha256:2ef63f1dfc28d9dc86dadf75c7ce2345833238212d970adafdf9368ad960ae49',
    interactionId: 'ix.approve.k7653147571b6e391911ef030c4f92bd1b96d9004',
  },
  {
    runId: 'run-xyz',
    arguments: {
      name: 'Jira digest — 9 Uhr ☕',
      ask: 'Summarise my open Jira issues.\n"quoted"',
      when: { kind: 'cron', cron: '0 9 * * 1-5', timeZone: 'Europe/Berlin' },
    },
    argsDigest: 'sha256:419bb225c5685a75d5c18a7e06633918bb4a1f3fa013ae125f85c117f20664dc',
    interactionId: 'ix.approve.kad85bf84f06e9b1ddf5c77044c5d38ba8b936135',
  },
];

// The same call's approval request as each worker form journals it (`harness_core.interactions.journal.binding_digest`
// for an engine, `kernel/durable/driver.py` `_requested` for the loop), computed by Python.
const PYTHON_BINDINGS = [
  {
    form: 'engine',
    interactionId: 'ix.approve.k7653147571b6e391911ef030c4f92bd1b96d9004',
    bindingDigest: 'sha256:e38b47f427a9605567aef34a871ccb2427bc11eea1d5ba073f1c48387c757da9',
  },
  {
    form: 'loop',
    interactionId: 'ix.approve.call_7Hq2.0',
    bindingDigest: 'sha256:11fca7b58372316a0e149cbab54180c90a340f55dd380f9d10a8aaef51e625c8',
  },
];

/** An engine's approval id (`harness_core.interactions.hold.approval_interaction_id`). */
const engineInteractionId = (runId: string, argsDigest: string): string =>
  `ix.approve.k${canonicalSha256({ runId, toolId: SCHEDULE_CREATE_TOOL_ID, argsDigest }).slice(0, 40)}`;

describe('ADR-SCH-01 D3: a schedule made on the person’s approval in chat', () => {
  it('names a call as the worker does (canonical digest and both request binding forms, parity with Python)', () => {
    for (const c of PYTHON_FIXTURE) {
      expect(canonicalDigest(c.arguments)).to.equal(c.argsDigest);
      expect(engineInteractionId(c.runId, c.argsDigest)).to.equal(c.interactionId);
    }
    const [joke] = PYTHON_FIXTURE;
    for (const form of PYTHON_BINDINGS) {
      expect(approvalBindingDigests(form.interactionId, SCHEDULE_CREATE_TOOL_ID, joke!.argsDigest)).to.include(
        form.bindingDigest,
      );
    }
  });

  const RUN = 'run-chat-1';
  const ARGS: ScheduleToolArguments = {
    name: 'Joke in five minutes',
    ask: 'Tell me a short, clean joke.',
    when: { kind: 'once', at: AT, timeZone: 'Europe/Berlin' },
  };
  const ixOf = (args: unknown, runId = RUN): string => engineInteractionId(runId, canonicalDigest(args));

  /** The approval request the worker journaled for the call `asked` (its own arguments), in the engine's form. */
  const requested = (interactionId: string, asked: unknown, toolId = SCHEDULE_CREATE_TOOL_ID): JournalEvent =>
    ({
      type: 'interaction.requested',
      runId: RUN,
      data: {
        interactionId,
        kind: 'approval',
        bindingDigest: canonicalDigest({ interactionId, kind: 'approval', subject: toolId, argsDigest: canonicalDigest(asked) }),
      },
    }) as unknown as JournalEvent;
  const loopRequested = (invocationId: string, asked: unknown): JournalEvent =>
    ({
      type: 'interaction.requested',
      runId: RUN,
      data: {
        interactionId: `ix.approve.${invocationId}`,
        kind: 'approval',
        bindingDigest: canonicalDigest({
          invocationId,
          toolId: SCHEDULE_CREATE_TOOL_ID,
          argsDigest: canonicalDigest(asked),
          purpose: 'approve',
        }),
      },
    }) as unknown as JournalEvent;
  /** The call asked and approved as is. */
  const approvedCall = (args: unknown = ARGS): JournalEvent[] => [requested(ixOf(args), args), resolved(ixOf(args), 'approved')];

  interface World {
    controller: AgentHarnessChatScheduleController;
    events: JournalEvent[];
    marked: Array<{ interactionId: string; scheduleId: string }>;
    setting: FakeSetting;
    actors: FakeActors;
    store: InMemoryScheduleStore;
    clock: TestClock;
    schedules: ScheduleService;
    admission: FakeAdmission;
    firer: ScheduleFirer;
    call(body: Record<string, unknown>): Promise<{ status: number; body?: unknown; code?: string }>;
    /** The error the last refused call passed to `next`, as the error middleware would write it. */
    lastError(): unknown;
  }

  const resolved = (
    interactionId: string,
    decision: 'approved' | 'denied',
    extra: { by?: string; channel?: 'web' | 'system'; edits?: Record<string, unknown> } = {},
  ): JournalEvent =>
    ({
      type: 'interaction.resolved',
      runId: RUN,
      data: resolvedData(
        {
          interactionId,
          decision,
          channel: extra.channel ?? 'web',
          ...(extra.edits !== undefined ? { edits: extra.edits } : {}),
        },
        extra.by ?? `p_${ALICE}`,
      ),
    }) as unknown as JournalEvent;

  function world(engines?: ChatScheduleOptions['engines']): World {
    const clock = new TestClock();
    const store = new InMemoryScheduleStore(clock);
    const actors = new FakeActors();
    const setting = new FakeSetting();
    const admission = new FakeAdmission();
    const firer = new ScheduleFirer({ store, admission, actors, setting });
    const schedules = new ScheduleService({
      transaction: testControlPlane(),
      store,
      firer,
      actors,
      setting,
    });
    const run = {
      orgId: ORG,
      runId: RUN,
      sessionId: 'ses-chat',
      actorId: ALICE,
      agentKey: 'helper',
      agentVersion: 3,
      input: {
        query: 'can you tell me a joke after 5 mins',
        recordIds: [],
        filters: {},
        attachments: [],
        locale: 'de-DE',
        model: { chatMode: 'quick', modelKey: 'm-1', modelName: 'gpt-x' },
      },
    } as unknown as HarnessRun;
    const events: JournalEvent[] = [];
    const marked: Array<{ interactionId: string; scheduleId: string }> = [];
    const service = new ChatScheduleService({
      ...(engines !== undefined ? { engines } : {}),
      runs: {
        get: async (orgId, runId) => (orgId === ORG && runId === RUN ? run : null),
        runEvents: async () => events,
      },
      vias: { via: async () => 'session' },
      schedules,
      store,
      actors,
      // The run's journal: what Node notes is read back by the next request.
      marks: {
        record: async (_run, _attemptId, made) => {
          marked.push(made);
          events.push({ type: SCHEDULE_CREATED_EVENT, runId: RUN, data: { ...made } } as unknown as JournalEvent);
        },
      },
    });
    const controller = new AgentHarnessChatScheduleController(service, {
      authorizeAttempt: sinon.stub().resolves(),
    } as never);
    let refused: unknown;
    const call = async (body: Record<string, unknown>) => {
      const res = { statusCode: 0, payload: undefined as unknown, status: sinon.stub(), json: sinon.stub() };
      res.status.callsFake((s: number) => ((res.statusCode = s), res));
      res.json.callsFake((p: unknown) => ((res.payload = p), res));
      const next = sinon.spy();
      await controller.create(
        {
          params: { runId: RUN },
          body: { attemptId: 'a-1', fence: 1, ...body },
          method: 'POST',
          path: `/runs/${RUN}/schedules`,
          harnessAttempt: { orgId: ORG, runId: RUN, attemptId: 'a-1', fence: 1 },
        } as never,
        res as never,
        next,
      );
      if (next.called) {
        refused = next.firstCall.args[0];
        const error = refused as { statusCode: number; code?: string };
        return { status: error.statusCode, code: String(error.code) };
      }
      return { status: res.statusCode, body: res.payload };
    };
    return { controller, events, marked, setting, actors, store, clock, schedules, admission, firer, call, lastError: () => refused };
  }

  const body = (args: unknown = ARGS, interactionId = ixOf(ARGS)) => ({
    interactionId,
    toolId: SCHEDULE_CREATE_TOOL_ID,
    arguments: args,
  });

  it('approved: 201, the run’s person’s schedule with the chat’s agent and model, its origin recorded', async () => {
    const w = world();
    w.events.push(...approvedCall());
    const made = await w.call(body());
    expect(made.status).to.equal(201);
    const answer = made.body as { created: boolean; schedule: { id: string; status: string; nextFireAt: string } };
    expect(answer).to.deep.include({ created: true });
    expect(answer.schedule).to.deep.include({ status: 'active', nextFireAt: '2026-07-06T06:47:00.000Z' });
    const stored = await w.store.get(ORG, answer.schedule.id);
    expect(stored).to.deep.include({
      actorId: ALICE,
      agentKey: 'helper',
      agentVersion: 3,
      origin: { conversationId: 'ses-chat', runId: RUN, interactionId: ixOf(ARGS), agentKey: 'helper' },
    });
    expect(stored?.input).to.deep.include({ query: ARGS.ask, locale: 'de-DE' });
    expect(stored?.input?.model).to.deep.include({ chatMode: 'quick', modelKey: 'm-1', modelName: 'gpt-x' });
  });

  it('keeps the chat’s coding engine, and each firing’s new chat is asked to use it', async () => {
    // The owner's chat chose no engine; without this its run took their personal default (Codex) instead.
    const asked: Array<[string, string]> = [];
    const w = world({ sessionChoice: async (orgId, sessionId) => (asked.push([orgId, sessionId]), 'none') });
    w.events.push(...approvedCall());
    const made = (await w.call(body())).body as { schedule: { id: string; nextFireAt: string } };
    expect(asked).to.deep.equal([[ORG, 'ses-chat']]);
    expect((await w.store.get(ORG, made.schedule.id))?.codingEngine).to.equal('none');
    w.clock.set(made.schedule.nextFireAt);
    expect(await w.firer.fireDue()).to.equal(1);
    expect(w.admission.requests.map((r) => r.codingEngine)).to.deep.equal(['none']);
  });

  it('a chat with no engine choice, or one that cannot be read, makes the schedule on the person’s default', async () => {
    for (const engines of [{ sessionChoice: async () => null }, { sessionChoice: async () => Promise.reject(new Error('down')) }]) {
      const w = world(engines);
      w.events.push(...approvedCall());
      const made = (await w.call(body())).body as { schedule: { id: string; nextFireAt: string } };
      expect((await w.store.get(ORG, made.schedule.id))?.codingEngine).to.equal(undefined);
      w.clock.set(made.schedule.nextFireAt);
      await w.firer.fireDue();
      expect(w.admission.requests[0]).to.not.have.property('codingEngine');
    }
  });

  it('the firing asks admission for that engine as a new chat’s first message does (CMDUI2 checks it)', async () => {
    const submitted: unknown[] = [];
    const firing = (codingEngine?: string) =>
      new RunAdmissionScheduledRuns({
        submit: async (request) => {
          submitted.push(request);
          return { route: 'legacy' } as never;
        },
      }).admit({
        orgId: ORG,
        actorId: ALICE,
        idempotencyKey: 'k',
        deadlineMs: 9_000,
        input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'agent' } },
        authorization: 'Bearer user',
        trigger: { kind: 'schedule', scheduleId: 'sch-1', firingId: 'fire-1' } as never,
        ...(codingEngine !== undefined ? { codingEngine } : {}),
      });
    await firing('none');
    await firing();
    expect(submitted[0]).to.deep.include({ codingEngine: 'none', durableRequested: true });
    expect(submitted[1]).to.not.have.property('codingEngine');
  });

  it('a coding engine not named as an engine is refused SCHEDULE_INVALID', async () => {
    const w = world();
    const create = (codingEngine: string) =>
      w.schedules.create(alice, { name: 'n', input: { query: 'q' }, trigger: { kind: 'cron', cron: '0 9 * * 1', timeZone: 'Europe/Berlin' }, codingEngine });
    for (const bad of ['', 'Codex', '../x', 'x'.repeat(65), 'a b']) {
      const refused = await create(bad).then(() => null, (error: unknown) => error as { code?: string });
      expect(refused?.code, bad).to.equal('SCHEDULE_INVALID');
    }
    expect((await create('claude-code')).schedule.id).to.be.a('string');
    // A chat's choice may name a dotted kind; a schedule takes the same names.
    expect((await create('acp.qwen')).schedule.id).to.be.a('string');
  });

  it('a repeat of the same decision: 200, the same schedule, nothing new', async () => {
    const w = world();
    w.events.push(...approvedCall());
    const first = (await w.call(body())).body as { schedule: { id: string } };
    const again = await w.call(body());
    expect(again.status).to.equal(200);
    expect(again.body).to.deep.include({ created: false });
    expect((again.body as { schedule: { id: string } }).schedule.id).to.equal(first.schedule.id);
    expect(first.schedule.id).to.equal(chatScheduleId(ORG, RUN, ixOf(ARGS)));
    expect(await w.store.list(ORG, {}, 10)).to.have.length(1);
  });

  it('the loop\'s interaction ids repeat in every run, so the id is per run: two runs never share a schedule', () => {
    // The durable loop's first approval is ix.approve.t1.0 in every run; an id of the org and the interaction alone
    // answered every later run's t1.0 with the first run's schedule, someone else's included.
    const loopFirst = 'ix.approve.t1.0';
    expect(chatScheduleId(ORG, 'run-a', loopFirst)).to.not.equal(chatScheduleId(ORG, 'run-b', loopFirst));
    expect(chatScheduleId(ORG, 'run-a', loopFirst)).to.equal(chatScheduleId(ORG, 'run-a', loopFirst));
    expect(chatScheduleId(ORG, 'run-a', loopFirst)).to.not.equal(chatScheduleId('org-2', 'run-a', loopFirst));
  });

  it('a schedule at this decision\'s id that another run or person made is never answered with: 403, nothing leaked', async () => {
    const w = world();
    w.events.push(...approvedCall());
    const id = chatScheduleId(ORG, RUN, ixOf(ARGS));
    await w.store.create(
      aSchedule({
        id,
        orgId: ORG,
        actorId: BOB,
        name: 'Bob\'s private reminder',
        origin: { conversationId: 'ses-bob', runId: 'run-bob', interactionId: ixOf(ARGS) },
      }),
      { perOrg: 500, perActor: 50 },
    );
    const answer = await w.call(body());
    expect(answer).to.deep.equal({ status: 403, code: 'HTTP_AGENT_HARNESS_SCHEDULE_FORBIDDEN' });
    // What the response carries is the HttpError itself (scheduleHttpError): its message and every field it has.
    const error = w.lastError() as Error;
    expect(error.message).to.be.a('string').and.not.equal('');
    const carried = JSON.stringify(error, Object.getOwnPropertyNames(error));
    expect(carried).to.not.include('private');
    expect(carried).to.not.include('run-bob');
  });

  it('two requests of one decision at once make one schedule', async () => {
    const w = world();
    w.events.push(...approvedCall());
    const both = await Promise.all([w.call(body()), w.call(body())]);
    expect(both.map((r) => r.status).sort()).to.deep.equal([200, 201]);
    expect(await w.store.list(ORG, {}, 10)).to.have.length(1);
  });

  const NOT_APPROVED = 'HTTP_AGENT_HARNESS_SCHEDULE_NOT_APPROVED';

  it('refused SCHEDULE_NOT_APPROVED (403) without a decision, on a denial, or a decision Node or someone else made', async () => {
    const asked = requested(ixOf(ARGS), ARGS);
    const cases: Array<JournalEvent[]> = [
      [asked],
      [asked, resolved(ixOf(ARGS), 'denied')],
      [asked, resolved(ixOf(ARGS), 'approved', { channel: 'system' })],
      [asked, resolved(ixOf(ARGS), 'approved', { by: `p_${BOB}` })],
      [resolved(ixOf(ARGS), 'approved')],
    ];
    for (const events of cases) {
      const w = world();
      w.events.push(...events);
      expect(await w.call(body())).to.deep.equal({ status: 403, code: NOT_APPROVED });
      expect(await w.store.list(ORG, {}, 10)).to.have.length(0);
    }
  });

  it('refused for other arguments than approved, another run’s call, or another tool', async () => {
    const w = world();
    w.events.push(...approvedCall());
    const other = { ...ARGS, ask: 'Delete my files.' };
    expect(await w.call(body(other, ixOf(ARGS)))).to.deep.equal({ status: 403, code: NOT_APPROVED });
    const elsewhere = ixOf(ARGS, 'run-other');
    // Another run's request and decision: never this run's, though a journal read gave them back.
    w.events.push(
      ...[requested(elsewhere, ARGS), resolved(elsewhere, 'approved')].map((e) => ({ ...e, runId: 'run-other' })),
    );
    expect(await w.call(body(ARGS, elsewhere))).to.deep.equal({ status: 403, code: NOT_APPROVED });
    expect(await w.call({ ...body(), toolId: 'pipeshub@mail/send' })).to.deep.equal({ status: 403, code: NOT_APPROVED });
    expect(
      await w.call({ ...body(), callArgsDigest: canonicalDigest(other) }),
    ).to.deep.equal({ status: 403, code: NOT_APPROVED });
    expect(await w.store.list(ORG, {}, 10)).to.have.length(0);
  });

  it('the loop’s form of the request (`ix.approve.<invocationId>`): approved, 201', async () => {
    const w = world();
    w.events.push(loopRequested('call_1.0', ARGS), resolved('ix.approve.call_1.0', 'approved'));
    expect((await w.call(body(ARGS, 'ix.approve.call_1.0'))).status).to.equal(201);
  });

  it('a request that asked about another tool or other arguments makes nothing (403)', async () => {
    const other = { ...ARGS, ask: 'Delete my files.' };
    for (const asked of [requested(ixOf(ARGS), ARGS, 'pipeshub@mail/send'), requested(ixOf(ARGS), other)]) {
      const w = world();
      w.events.push(asked, resolved(ixOf(ARGS), 'approved'));
      expect(await w.call(body())).to.deep.equal({ status: 403, code: NOT_APPROVED });
    }
    const w = world();
    w.events.push(loopRequested('call_2.0', other), resolved('ix.approve.call_2.0', 'approved'));
    expect(await w.call(body(ARGS, 'ix.approve.call_2.0'))).to.deep.equal({ status: 403, code: NOT_APPROVED });
  });

  it('approved after its time came (the card waited): made, due at once, lateness counted from its own time', async () => {
    const late = { ...ARGS, when: { kind: 'once' as const, at: '2026-07-06T05:58:30Z', timeZone: 'UTC' } };
    const w = world();
    w.events.push(...approvedCall(late));
    const made = await w.call(body(late, ixOf(late)));
    expect(made.status).to.equal(201);
    expect((made.body as { schedule: { nextFireAt: string } }).schedule.nextFireAt).to.equal('2026-07-06T05:58:30.000Z');
    const stale = { ...ARGS, when: { kind: 'once' as const, at: '2026-07-05T05:00:00Z', timeZone: 'UTC' } };
    w.events.push(...approvedCall(stale));
    expect(await w.call(body(stale, ixOf(stale)))).to.deep.equal({ status: 400, code: 'HTTP_AGENT_HARNESS_SCHEDULE_INVALID' });
  });

  it('approved 23 h late and first ticked 2 h later: skipped as too late, its person told', async () => {
    const late = { ...ARGS, when: { kind: 'once' as const, at: '2026-07-05T07:00:00Z', timeZone: 'UTC' } };
    const w = world();
    w.events.push(...approvedCall(late));
    const id = ((await w.call(body(late, ixOf(late)))).body as { schedule: { id: string } }).schedule.id;
    w.clock.advance(2 * 60 * MIN);
    const admission = new FakeAdmission();
    const notifier = new FakeNotifier();
    await new ScheduleFirer({ store: w.store, admission, actors: w.actors, setting: w.setting, notifier }).fireDue();
    expect(admission.admitted).to.equal(0);
    expect((await w.store.firings(ORG, id, 10)).map((f) => [f.status, f.reason])).to.deep.equal([['skipped', ONCE_TOO_LATE]]);
    expect(notifier.refusals.map((n) => n.reason)).to.deep.equal([ONCE_TOO_LATE]);
  });

  it('one approval makes its schedule once: deleted, a repeat is refused and nothing is made', async () => {
    const w = world();
    w.events.push(...approvedCall());
    const id = ((await w.call(body())).body as { schedule: { id: string } }).schedule.id;
    expect(w.marked).to.deep.equal([{ interactionId: ixOf(ARGS), scheduleId: id }]);
    await w.schedules.delete(alice, id);
    expect(await w.call(body())).to.deep.equal({ status: 409, code: 'HTTP_AGENT_HARNESS_SCHEDULE_APPROVAL_USED' });
    expect(await w.store.list(ORG, {}, 10)).to.have.length(0);
    expect(w.marked).to.have.length(1);
  });

  it('a lost audit fact changes nothing: a repeat still answers the schedule, and after a delete is refused', async () => {
    const w = world();
    w.events.push(...approvedCall());
    const id = ((await w.call(body())).body as { schedule: { id: string } }).schedule.id;
    w.events.splice(w.events.findIndex((e) => e.type === SCHEDULE_CREATED_EVENT), 1);
    expect(await w.call(body())).to.deep.include({ status: 200 });
    await w.schedules.delete(alice, id);
    expect(await w.call(body())).to.deep.equal({ status: 409, code: 'HTTP_AGENT_HARNESS_SCHEDULE_APPROVAL_USED' });
    expect(await w.store.list(ORG, {}, 10)).to.have.length(0);
  });

  it('AP-06: an edited call is made with the edited arguments only', async () => {
    const edited = { ...ARGS, name: 'A joke, a bit later', when: { ...ARGS.when, at: '2026-07-06T09:00:00+02:00' } };
    const w = world();
    w.events.push(requested(ixOf(ARGS), ARGS), resolved(ixOf(ARGS), 'approved', { edits: edited }));
    expect(await w.call(body(ARGS))).to.deep.equal({ status: 403, code: NOT_APPROVED });
    expect(await w.call(body(edited, ixOf(ARGS)))).to.deep.equal({ status: 403, code: NOT_APPROVED });
    const made = await w.call({ ...body(edited, ixOf(ARGS)), callArgsDigest: canonicalDigest(ARGS) });
    expect(made.status).to.equal(201);
    expect((made.body as { schedule: { name: string; nextFireAt: string } }).schedule).to.deep.include({
      name: 'A joke, a bit later',
      nextFireAt: '2026-07-06T07:00:00.000Z',
    });
  });

  it('Labs scheduled runs off: the create’s own refusal, nothing made', async () => {
    const w = world();
    w.events.push(...approvedCall());
    w.setting.on = false;
    expect(await w.call(body())).to.deep.equal({ status: 404, code: 'HTTP_AGENT_HARNESS_SCHEDULED_RUNS_DISABLED' });
    expect(await w.store.list(ORG, {}, 10)).to.have.length(0);
  });

  it('the person’s authority is read again; a disabled person makes nothing', async () => {
    const w = world();
    w.events.push(...approvedCall());
    w.actors.refused.set(ALICE, 'actor_disabled');
    expect(await w.call(body())).to.deep.equal({ status: 403, code: 'HTTP_AGENT_HARNESS_SCHEDULE_FORBIDDEN' });
  });

  it('the create’s own checks hold: a cron more often than every five minutes is refused', async () => {
    const fast: ScheduleToolArguments = { ...ARGS, when: { kind: 'cron', cron: '* * * * *', timeZone: 'UTC' } };
    const w = world();
    w.events.push(...approvedCall(fast));
    expect(await w.call(body(fast, ixOf(fast)))).to.deep.equal({ status: 400, code: 'HTTP_AGENT_HARNESS_SCHEDULE_INVALID' });
  });

  it('the route takes exactly the arguments the shared contract vectors say are valid', async () => {
    const { vectors } = JSON.parse(
      readFileSync(resolve(__dirname, '../../../../../../contracts/harness/vectors/schedule-create-arguments.json'), 'utf8'),
    ) as { vectors: Array<{ name: string; arguments: unknown; valid: boolean }> };
    expect(vectors.length).to.be.greaterThan(20);
    for (const vector of vectors) {
      expect(scheduleCreateArgumentsSchema.safeParse(vector.arguments).success, vector.name).to.equal(vector.valid);
      const w = world();
      const answer = await w.call(body(vector.arguments, ixOf(vector.arguments)));
      // A valid shape passes to the approval check (no decision here: 403); an invalid one never gets that far.
      expect(answer.status, vector.name).to.equal(vector.valid ? 403 : 400);
    }
  });

  it('the OpenAPI component of the arguments holds the contract’s own constraints', () => {
    const KEPT = ['minLength', 'maxLength', 'pattern', 'enum', 'required', 'additionalProperties', 'minItems', 'maxItems', 'uniqueItems'];
    const constraints = (schema: Record<string, unknown>): unknown => {
      const out: Record<string, unknown> = {};
      for (const key of KEPT) if (schema[key] !== undefined) out[key] = schema[key];
      if (schema['const'] !== undefined) out['enum'] = [schema['const']];
      const props = schema['properties'] as Record<string, Record<string, unknown>> | undefined;
      if (props !== undefined) {
        out['properties'] = Object.fromEntries(Object.entries(props).map(([k, v]) => [k, constraints(v)]));
      }
      if (schema['items'] !== undefined) out['items'] = constraints(schema['items'] as Record<string, unknown>);
      const branches = (schema['anyOf'] ?? schema['oneOf']) as Array<Record<string, unknown>> | undefined;
      if (branches !== undefined) out['anyOf'] = branches.map(constraints);
      return out;
    };
    const root = resolve(__dirname, '../../../../../..');
    const contract = JSON.parse(
      readFileSync(resolve(root, 'contracts/harness/ext/pipeshub/api/schedule-create-arguments.schema.json'), 'utf8'),
    ) as Record<string, unknown>;
    const openapi = loadYaml(
      readFileSync(resolve(__dirname, '../../../src/modules/api-docs/pipeshub-openapi.yaml'), 'utf8'),
    ) as { components: { schemas: Record<string, Record<string, unknown>> } };
    const component = openapi.components.schemas['AgentHarnessScheduleCreateArguments'];
    expect(component).to.not.equal(undefined);
    expect(constraints(component!)).to.deep.equal(constraints(contract));
  });

  it('a malformed body is a 400 that names nothing of it', async () => {
    const w = world();
    expect((await w.call({ ...body(), arguments: { ...ARGS, extra: 1 } })).status).to.equal(400);
  });
});
