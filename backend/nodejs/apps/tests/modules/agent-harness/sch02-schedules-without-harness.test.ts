/** ADR-SCH-02: scheduling is its own feature, and each firing runs on the runner the org's path picks when it fires. */
import { expect } from 'chai';
import { InMemoryScheduleStore } from '../../../src/modules/agent-harness/adapters/in-memory-schedules';
import { ScheduleFirer } from '../../../src/modules/agent-harness/application/schedule-firer';
import { HarnessScheduleRunner } from '../../../src/modules/agent-harness/application/harness-schedule-runner';
import {
  ASK_EXPIRED,
  type ClassicConversations,
  type ClassicConversationState,
  ClassicScheduleRunner,
  type ClassicTurnRequest,
  type ClassicTurns,
  classicDigest,
  classicTurnBody,
  DEADLINE_PASSED,
  scheduledConversationId,
} from '../../../src/modules/agent-harness/application/classic-schedule-runner';
import { orgSchedulesOn } from '../../../src/modules/agent-harness/config';
import { Container } from 'inversify';
import { ScheduleService } from '../../../src/modules/agent-harness/application/schedule-service';
import { startScheduleServices } from '../../../src/modules/agent-harness/composition';
import { AGENT_HARNESS_SCHEDULE_FIRER } from '../../../src/modules/agent-harness/binders/tokens';
import { HarnessScheduleRefusedError } from '../../../src/modules/agent-harness/domain/errors';
import { scheduleHttpError } from '../../../src/modules/agent-harness/transport/schedule-routes';
import { testControlPlane } from '../control-plane/support/in-memory';
import {
  type AgentSchedule,
  FIRING_LEASE_MS,
  FIRING_BACKOFF_MAX_MS,
} from '../../../src/modules/agent-harness/domain/schedules';
import {
  aSchedule,
  ALICE,
  FakeActors,
  FakeAdmission,
  FakeNotifier,
  FakeRuns,
  FakeSetting,
  ORG,
  TestClock,
} from './support/schedules';

const LIMITS = { perActor: 50, perOrg: 500 };

/** Classic turns as the chat pipeline answers them: one new conversation per started turn. */
class FakeTurns implements ClassicTurns {
  readonly requests: ClassicTurnRequest[] = [];
  refusal: string | null = null;
  constructor(private readonly conversations?: FakeConversations) {}
  /** The conversations the turns opened, in order. */
  get ids(): string[] {
    return [...new Set(this.requests.map((r) => r.mark.conversationId))];
  }
  async start(
    request: ClassicTurnRequest,
  ): Promise<{ conversationId: string } | { refused: string }> {
    this.requests.push(request);
    if (this.refusal !== null) return { refused: this.refusal };
    const id = request.mark.conversationId;
    // The store's unique id: a second create of the firing's conversation is refused, as SessionExistsError is.
    if (this.conversations?.states.has(id) === true) return { refused: 'unavailable' };
    this.conversations?.set(id, { status: 'in_progress' });
    return { conversationId: id };
  }
}

/** The firing conversations as the conversation store holds them. */
class FakeConversations implements ClassicConversations {
  readonly states = new Map<string, ClassicConversationState>();
  readonly expired: string[] = [];
  async read(
    orgId: string,
    id: string,
  ): Promise<ClassicConversationState | null> {
    if (orgId !== ORG) return null;
    return this.states.get(id) ?? null;
  }
  async expire(_orgId: string, id: string): Promise<void> {
    this.expired.push(id);
    const state = this.states.get(id);
    if (state !== undefined) {
      this.states.set(id, { ...state, status: 'failed', failReason: ASK_EXPIRED });
    }
  }
  set(id: string, state: Partial<ClassicConversationState>): void {
    this.states.set(id, {
      status: 'in_progress',
      answer: null,
      startedAt: 0,
      updatedAt: 0,
      ...state,
    });
  }
}

const waiting = {
  parts: [
    { type: 'tool_call', toolName: 'jira_create_issue', status: 'awaiting_approval' },
  ],
  citations: 0,
};
const answered = {
  parts: [
    { type: 'tool_call', toolName: 'search', displayName: 'Searched', status: 'completed' },
    { type: 'tool_call', toolName: 'search', status: 'completed' },
  ],
  citations: 3,
};

interface World {
  clock: TestClock;
  store: InMemoryScheduleStore;
  actors: FakeActors;
  setting: FakeSetting;
  admission: FakeAdmission;
  runs: FakeRuns;
  notifier: FakeNotifier;
  turns: FakeTurns;
  conversations: FakeConversations;
  harnessOn: boolean;
  firer: ScheduleFirer;
}

function world(): World {
  const clock = new TestClock();
  const w = {
    clock,
    store: new InMemoryScheduleStore(clock),
    actors: new FakeActors(),
    setting: new FakeSetting(),
    admission: new FakeAdmission(),
    runs: new FakeRuns(),
    notifier: new FakeNotifier(),
    conversations: new FakeConversations(),
    harnessOn: false,
  } as World;
  w.turns = new FakeTurns(w.conversations);
  w.firer = new ScheduleFirer({
    store: w.store,
    harness: new HarnessScheduleRunner(w.admission, w.runs),
    classic: new ClassicScheduleRunner(w.turns, w.conversations, () =>
      w.clock.value,
    ),
    actors: w.actors,
    setting: w.setting,
    notifier: w.notifier,
    harnessOn: async () => w.harnessOn,
  });
  return w;
}

async function due(
  w: World,
  overrides: Partial<AgentSchedule> = {},
): Promise<AgentSchedule> {
  const schedule = await w.store.create(aSchedule(overrides, w.clock.value), LIMITS);
  w.clock.value = (schedule.nextFireAt as number) + 10_000;
  return schedule;
}

const onlyFiring = async (w: World, schedule: AgentSchedule) => {
  const [firing] = await w.store.firings(ORG, schedule.id, 10);
  return firing!;
};

describe('ADR-SCH-02 schedules without the harness', () => {
  describe('D1: the gate is Labs scheduled runs alone', () => {
    it('reads the flag alone, and fails closed', async () => {
      expect(await orgSchedulesOn(ORG, async () => true)).to.equal(true);
      expect(await orgSchedulesOn(ORG, async () => false)).to.equal(false);
      expect(
        await orgSchedulesOn(ORG, async () => {
          throw new Error('settings down');
        }),
      ).to.equal(false);
      expect(await orgSchedulesOn(ORG, null)).to.equal(false);
      expect(await orgSchedulesOn('', async () => true)).to.equal(false);
    });

    it('a firing with the harness off runs, and with the flag off is refused', async () => {
      const w = world();
      const schedule = await due(w);
      await w.firer.tick();
      expect((await onlyFiring(w, schedule)).status).to.equal('admitted');

      const other = world();
      other.setting.on = false;
      const off = await due(other);
      await other.firer.tick();
      const firing = await onlyFiring(other, off);
      expect([firing.status, firing.reason]).to.deep.equal([
        'refused',
        'scheduled_runs_disabled',
      ]);
      expect(other.turns.requests).to.have.length(0);
    });
  });

  describe('D2: the runner is picked per firing and recorded on it', () => {
    it('harness off: the classic turn, as the owner, with a credential for the deadline', async () => {
      const w = world();
      const schedule = await due(w, { agentKey: undefined, maxCostUsd: 1 });
      await w.firer.tick();
      const firing = await onlyFiring(w, schedule);
      expect(firing).to.include({
        status: 'admitted',
        runner: 'classic',
        runRef: w.turns.ids[0]!,
        runId: null,
        sessionId: w.turns.ids[0]!,
        costCapIgnored: true,
      });
      expect(w.admission.requests).to.have.length(0);
      expect(w.actors.ttls).to.deep.equal([schedule.deadlineS]);
      const [turn] = w.turns.requests;
      expect(turn!.authorization).to.equal(`Bearer token-of-${ALICE}`);
      expect(turn!.agentKey).to.equal(undefined);
      expect(turn!.mark).to.deep.equal({
        scheduleId: schedule.id,
        firingId: firing.id,
        conversationId: scheduledConversationId(ORG, firing.id),
        title: schedule.name,
        deadlineAt: firing.watchUntil,
      });
      // `auto` is a harness mode: the assistant's classic route asks for its Agent mode.
      expect(turn!.body).to.include({ chatMode: 'agent' });
      expect((turn!.body as { query: string }).query).to.equal(
        'Summarise new support tickets to #support-leads',
      );
    });

    it('harness on: a durable run through admission, unchanged, with the default credential', async () => {
      const w = world();
      w.harnessOn = true;
      const schedule = await due(w);
      await w.firer.tick();
      const firing = await onlyFiring(w, schedule);
      expect(firing).to.include({
        runner: 'harness',
        runRef: 'run-1',
        runId: 'run-1',
        sessionId: 'ses-1',
      });
      expect(firing.costCapIgnored).to.equal(undefined);
      expect(w.turns.requests).to.have.length(0);
      expect(w.actors.ttls).to.deep.equal([undefined]);
    });

    it('a durable admission the rollout sends to the classic path runs as the classic turn', async () => {
      const w = world();
      w.harnessOn = true;
      w.admission.refusals.push('rollout_legacy');
      const schedule = await due(w);
      await w.firer.tick();
      expect(await onlyFiring(w, schedule)).to.include({
        runner: 'classic',
        runRef: w.turns.ids[0]!,
      });
    });

    it('a firing is watched by the runner that started it after the org changes path', async () => {
      const w = world();
      const schedule = await due(w);
      await w.firer.tick();
      w.harnessOn = true;
      w.conversations.set(w.turns.ids[0]!, { status: 'complete', answer: answered });
      await w.firer.watchRuns();
      expect(w.notifier.ends).to.have.length(1);
      expect(w.notifier.ends[0]).to.include({
        runner: 'classic',
        runId: w.turns.ids[0]!,
        sessionId: w.turns.ids[0]!,
        status: 'completed',
      });
      expect(w.notifier.ends[0]!.digest?.records.read).to.equal(3);
      // The next firing takes the harness, now on.
      expect(w.admission.requests).to.have.length(0);
      w.clock.value = (await w.store.get(ORG, schedule.id))!.nextFireAt! + 10_000;
      await w.firer.tick();
      expect(w.admission.requests).to.have.length(1);
    });

    it('a classic run that refused before its conversation settles the reason', async () => {
      const w = world();
      w.turns.refusal = 'agent_forbidden';
      const schedule = await due(w);
      await w.firer.tick();
      const firing = await onlyFiring(w, schedule);
      expect([firing.status, firing.reason]).to.deep.equal([
        'refused',
        'agent_forbidden',
      ]);
      expect((await w.store.get(ORG, schedule.id))!.status).to.equal('paused');
    });

    it('no runner for the org path: refused, never run on the other one', async () => {
      const w = world();
      const firer = new ScheduleFirer({
        store: w.store,
        actors: w.actors,
        setting: w.setting,
        harnessOn: async () => false,
      });
      const schedule = await due(w);
      await firer.tick();
      expect((await onlyFiring(w, schedule)).reason).to.equal('runner_unavailable');
    });

    it('overlap: a running classic turn queues the next slot behind its conversation', async () => {
      const w = world();
      const schedule = await due(w, {
        overlap: 'queue',
        trigger: { kind: 'cron', cron: '*/5 * * * *', timeZone: 'UTC', catchUp: 'skip' },
      });
      await w.firer.tick();
      w.conversations.set(w.turns.ids[0]!, { status: 'in_progress' });
      w.clock.value = (await w.store.get(ORG, schedule.id))!.nextFireAt! + 1_000;
      await w.firer.tick();
      const queued = await w.store.queuedFiring(ORG, schedule.id);
      expect(queued?.queuedBehind).to.equal(w.turns.ids[0]!);
      w.conversations.set(w.turns.ids[0]!, { status: 'complete', answer: answered });
      const first = (await w.store.firings(ORG, schedule.id, 10)).find(
        (f) => f.runRef === w.turns.ids[0]!,
      )!;
      await w.firer.runnerEnded(ORG, first.id);
      expect((await w.store.getFiring(ORG, queued!.id))!.leaseUntil).to.be.at.most(
        w.clock.value,
      );
    });
  });

  describe('D4: a classic turn that needs the person waits for them', () => {
    it('waiting_for_you tells the person once, as a durable run waiting for approval does', async () => {
      const w = world();
      await due(w);
      await w.firer.tick();
      w.conversations.set(w.turns.ids[0]!, { status: 'complete', answer: waiting });
      await w.firer.watchRuns();
      await w.firer.watchRuns();
      expect(w.notifier.notices).to.have.length(1);
      expect(w.notifier.notices[0]).to.include({
        runner: 'classic',
        sessionId: w.turns.ids[0]!,
        runId: w.turns.ids[0]!,
      });
      expect(w.notifier.ends).to.have.length(0);
    });

    it('an ask unanswered by the deadline ends the run as expired and says so', async () => {
      const w = world();
      const schedule = await due(w);
      await w.firer.tick();
      w.conversations.set(w.turns.ids[0]!, { status: 'complete', answer: waiting });
      const firing = await onlyFiring(w, schedule);
      w.clock.value = firing.watchUntil! + 1_000;
      await w.firer.watchRuns();
      expect(w.conversations.expired).to.deep.equal([w.turns.ids[0]!]);
      expect(w.notifier.ends[0]).to.include({
        status: 'failed',
        reason: ASK_EXPIRED,
      });
      expect(w.notifier.ends[0]!.digest?.outcome).to.equal('needs_input');
    });
  });

  describe('D3: the classic runner reads the run from its conversation', () => {
    const runner = (conversations: FakeConversations, now = 1_000) =>
      new ClassicScheduleRunner(new FakeTurns(), conversations, () => now);

    it('maps the conversation to the run status', async () => {
      const c = new FakeConversations();
      c.set('running', { status: 'in_progress' });
      c.set('late', { status: 'in_progress' });
      c.set('done', { status: 'complete', answer: answered });
      c.set('waits', { status: 'complete', answer: waiting });
      c.set('failed', { status: 'failed', failReason: 'stream_error' });
      c.set('stopped', { status: 'stopped' });
      c.set('deleted', { status: 'deleted' });
      const r = runner(c);
      expect(await r.status(ORG, 'running', 2_000)).to.deep.equal({ status: 'running' });
      expect(await r.status(ORG, 'late', 500)).to.deep.equal({
        status: 'failed',
        reason: DEADLINE_PASSED,
      });
      expect(await r.status(ORG, 'done', 2_000)).to.deep.equal({ status: 'completed' });
      expect(await r.status(ORG, 'waits', 2_000)).to.deep.equal({
        status: 'waiting_for_you',
      });
      expect(await r.status(ORG, 'failed', 2_000)).to.deep.equal({
        status: 'failed',
        reason: 'stream_error',
      });
      expect(await r.status(ORG, 'stopped', 2_000)).to.deep.equal({ status: 'cancelled' });
      expect(await r.status(ORG, 'deleted', 2_000)).to.deep.equal({ status: 'cancelled' });
      expect(await r.status('another-org', 'done', 2_000)).to.equal(null);
    });

    it('a digest of names and counts, never content', () => {
      const digest = classicDigest(
        {
          status: 'complete',
          answer: {
            parts: [
              { type: 'tool_call', toolName: 'search', displayName: 'Searched', status: 'completed' },
              { type: 'tool_call', toolName: 'search', status: 'failed' },
              {
                type: 'sub_agent',
                parts: [
                  { type: 'tool_call', toolName: 'jira_create', approved: true, approvalAnswer: 'allowed' },
                ],
              },
            ],
            citations: 2,
          },
          startedAt: 1_000,
          updatedAt: 4_000,
        },
        'completed',
      );
      expect(digest.outcome).to.equal('answered');
      expect(digest.actions).to.deep.equal({
        total: 3,
        failed: 1,
        refused: 0,
        tools: [
          { name: 'search', displayName: 'Searched', count: 2 },
          { name: 'jira_create', count: 1 },
        ],
      });
      expect(digest.approvals).to.include({ asked: 1, approved: 1 });
      expect(digest.records.read).to.equal(2);
      expect(digest.cost).to.equal(null);
      expect(digest.durationMs).to.equal(3_000);
      expect(JSON.stringify(digest)).not.to.contain('Summarise');
    });

    it('the chat request keeps the schedule model and a mode the classic route takes', () => {
      const input = aSchedule().input!;
      expect(
        classicTurnBody(
          { ...input, model: { chatMode: 'deep', modelKey: 'k', modelName: 'n' } },
          'support-agent',
        ),
      ).to.deep.equal({
        query: input.query,
        chatMode: 'deep',
        recordIds: [],
        filters: {},
        modelKey: 'k',
        modelName: 'n',
      });
      expect(classicTurnBody({ ...input, model: { chatMode: 'web_search' } }, undefined))
        .to.include({ chatMode: 'web_search' });
      expect(classicTurnBody(input, 'support-agent')).to.include({ chatMode: 'auto' });
    });
  });

  describe('Deviations: a MongoDB without a replica set has no scheduling', () => {
    const refusal = async (work: Promise<unknown>): Promise<HarnessScheduleRefusedError> => {
      try {
        await work;
      } catch (error) {
        return error as HarnessScheduleRefusedError;
      }
      throw new Error('expected a refusal');
    };

    it('a create and a run now are refused with a catalogued 503 that says to ask the admin', async () => {
      const w = world();
      const service = new ScheduleService({
        transaction: testControlPlane(),
        store: w.store,
        firer: w.firer,
        actors: w.actors,
        setting: w.setting,
        storeReady: () => false,
      });
      const caller = { orgId: ORG, userId: ALICE, via: 'session' as const };
      const made = await refusal(
        service.create(caller, {
          name: 'Joke',
          input: { query: 'Tell me a joke' },
          trigger: { kind: 'cron', cron: '0 9 * * 1', timeZone: 'UTC' },
        }),
      );
      expect(made.code).to.equal('SCHEDULED_RUNS_STORE_UNAVAILABLE');
      expect(made.message).to.contain('Ask your administrator');
      const http = scheduleHttpError(made) as { statusCode: number; code: string };
      expect([http.statusCode, http.code]).to.deep.equal([503, 'HTTP_AGENT_HARNESS_SCHEDULED_RUNS_STORE_UNAVAILABLE']);
      const existing = await w.store.create(aSchedule({ actorId: ALICE }), LIMITS);
      expect((await refusal(service.runNow(caller, existing.id, 'k'))).code).to.equal('SCHEDULED_RUNS_STORE_UNAVAILABLE');
    });

    it('the firer is not started, so no slot is claimed without its transaction', async () => {
      const container = new Container();
      let started = 0;
      container.bind(AGENT_HARNESS_SCHEDULE_FIRER).toConstantValue({ start: () => (started += 1) });
      const errors: string[] = [];
      const logger = { info: () => undefined, error: (message: string) => errors.push(message) };
      await startScheduleServices(container, logger, async () => ({ created: [] }), () => false);
      expect(started).to.equal(0);
      expect(errors.join(' ')).to.contain('replica set');
      await startScheduleServices(container, logger, async () => ({ created: [] }), () => true);
      expect(started).to.equal(1);
    });
  });

  describe('D3: a firing opens one classic conversation, however often it is started', () => {
    it('the conversation id is the firing’s: a 24-hex id every store takes, one per org and firing', () => {
      const id = scheduledConversationId(ORG, 'fir-1');
      expect(id).to.match(/^[0-9a-f]{24}$/);
      expect(scheduledConversationId(ORG, 'fir-1')).to.equal(id);
      expect(scheduledConversationId(ORG, 'fir-2')).to.not.equal(id);
      expect(scheduledConversationId('0000000000000000000000e2', 'fir-1')).to.not.equal(id);
    });

    it('a settle that fails after the turn started: the retry answers the same conversation and starts no turn', async () => {
      const w = world();
      const schedule = await due(w);
      const settle = w.store.settle.bind(w.store);
      let failures = 1;
      w.store.settle = async (...args) => {
        if (args[2].status === 'admitted' && failures > 0) {
          failures -= 1;
          throw new Error('store write lost');
        }
        return settle(...args);
      };
      await w.firer.tick();
      let firing = await onlyFiring(w, schedule);
      expect(firing.status).to.equal('pending');
      expect(w.turns.requests).to.have.length(1);
      w.clock.value += FIRING_BACKOFF_MAX_MS + FIRING_LEASE_MS;
      await w.firer.tick();
      firing = await onlyFiring(w, schedule);
      expect(firing).to.include({ status: 'admitted', runRef: scheduledConversationId(ORG, firing.id) });
      expect(w.turns.requests).to.have.length(1);
      expect(w.conversations.states.size).to.equal(1);
    });

    it('a crash before the settle: the stale firing retaken after its lease starts no second turn', async () => {
      const w = world();
      const schedule = await due(w);
      // The process dies once the turn started: neither the settle nor the release is written.
      w.store.settle = async () => {
        throw new Error('process died');
      };
      w.store.release = async () => {
        throw new Error('process died');
      };
      await w.firer.fireDue();
      expect(w.turns.requests).to.have.length(1);
      const restarted = new InMemoryScheduleStore(w.clock);
      Object.assign(w.store, {
        settle: restarted.settle.bind(w.store),
        release: restarted.release.bind(w.store),
      });
      w.clock.value += FIRING_LEASE_MS + 1_000;
      expect(await w.firer.retryStale()).to.equal(1);
      const firing = await onlyFiring(w, schedule);
      expect(firing).to.include({ status: 'admitted', runRef: scheduledConversationId(ORG, firing.id) });
      expect(w.turns.requests).to.have.length(1);
      expect(w.conversations.states.size).to.equal(1);
    });

    it('two starts of one firing at once: one conversation, the same run for both', async () => {
      const w = world();
      const runner = new ClassicScheduleRunner(w.turns, w.conversations, () => w.clock.value);
      const request = {
        orgId: ORG,
        actorId: ALICE,
        scheduleId: 'sch-x',
        scheduleName: 'Joke',
        firingId: 'fir-x',
        idempotencyKey: 'k',
        deadlineMs: 60_000,
        deadlineAt: w.clock.value + 60_000,
        input: aSchedule().input!,
        authorization: 'Bearer token-of-alice',
        trigger: { scheduleId: 'sch-x', firingId: 'fir-x', slotKey: 'm:x' },
      };
      const [a, b] = await Promise.all([runner.start(request), runner.start(request)]);
      expect(a).to.deep.equal(b);
      expect(a).to.include({ kind: 'started', runRef: scheduledConversationId(ORG, 'fir-x') });
      expect(w.conversations.states.size).to.equal(1);
      // Once it exists a further start reads it and opens no turn at all.
      const before = w.turns.requests.length;
      expect(await runner.start(request)).to.deep.equal(a);
      expect(w.turns.requests).to.have.length(before);
    });
  });
});
