/**
 * OWD-14 / GA-01 on Node: a schedule pins a published agent version and keeps
 * it until its owner moves it (or opted in to `latest`); a schedule from before
 * versions runs version 1, the migration's; every run records the version it
 * ran, which its session shows and its worker never sees on the run.
 */
import { expect } from 'chai';
import sinon from 'sinon';
import { InMemoryScheduleStore } from '../../../src/modules/agent-harness/adapters/in-memory-schedules';
import { ActorAgentVersions } from '../../../src/modules/agent-harness/application/actor-agent-versions';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { ScheduleFirer } from '../../../src/modules/agent-harness/application/schedule-firer';
import {
  CreateScheduleRequest,
  ScheduleCaller,
  ScheduleService,
} from '../../../src/modules/agent-harness/application/schedule-service';
import {
  AdmitRunCommand,
  rootRunDocument,
} from '../../../src/modules/agent-harness/domain/admission';
import { HarnessScheduleRefusedError } from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { workerRun } from '../../../src/modules/agent-harness/domain/steering';
import type { AgentVersionResolver } from '../../../src/modules/agent-harness/ports/schedules';
import { testControlPlane } from '../control-plane/support/in-memory';
import { agentDefinition } from './support/agent-definition';
import { fixedRollout } from './support/rollout';
import {
  ADMIN_USER,
  ALICE,
  adminReach,
  aSchedule,
  FakeActors,
  FakeAdmission,
  FakeSetting,
  ORG,
  TestClock,
} from './support/schedules';

const alice: ScheduleCaller = { orgId: ORG, userId: ALICE, via: 'session' };
const admin: ScheduleCaller = { orgId: ORG, userId: ADMIN_USER, via: 'session' };

const WEEKLY: CreateScheduleRequest = {
  name: 'Weekly support digest',
  agentKey: 'support-agent',
  input: { query: 'Summarise new support tickets' },
  trigger: { kind: 'cron', cron: '0 9 * * 1', timeZone: 'Europe/Berlin' },
};

/** The agent authority's published versions of `support-agent`, as the actor may run them. */
class PublishedVersions implements AgentVersionResolver {
  latest = 3;
  readonly published = new Set([1, 2, 3]);
  readonly asked: Array<number | undefined> = [];

  async resolve(
    _orgId: string,
    _actorId: string,
    _agentKey: string,
    requested?: number,
  ): Promise<number | null> {
    this.asked.push(requested);
    if (requested === undefined) return this.latest;
    return this.published.has(requested) ? requested : null;
  }

  publish(): void {
    this.latest += 1;
    this.published.add(this.latest);
  }
}

async function refusal(promise: Promise<unknown>): Promise<HarnessScheduleRefusedError> {
  try {
    await promise;
  } catch (error) {
    expect(error).to.be.instanceOf(HarnessScheduleRefusedError);
    return error as HarnessScheduleRefusedError;
  }
  throw new Error('expected a refusal');
}

describe('OWD-14 agent versions on Node', () => {
  let store: InMemoryScheduleStore;
  let admission: FakeAdmission;
  let versions: PublishedVersions;
  let service: ScheduleService;
  let firer: ScheduleFirer;
  let keys: number;

  beforeEach(() => {
    const clock = new TestClock(Date.parse('2026-07-01T00:00:00.000Z'));
    store = new InMemoryScheduleStore(clock);
    admission = new FakeAdmission();
    versions = new PublishedVersions();
    keys = 0;
    const actors = new FakeActors();
    const setting = new FakeSetting();
    firer = new ScheduleFirer({ store, admission, actors, setting });
    service = new ScheduleService({
      transaction: testControlPlane(),
      store,
      firer,
      actors,
      setting,
      agentVersions: versions,
      // The admin cases below act on another person's schedule, which takes a build's reach.
      reach: adminReach(actors),
    });
  });

  const fire = async (id: string): Promise<number | undefined> => {
    keys += 1;
    await service.runNow(alice, id, `k-${String(keys)}`);
    return admission.requests.at(-1)?.agentVersion;
  };

  describe('GA-01: a schedule keeps its pinned version', () => {
    it('GA-01: pins the latest version at creation and keeps it when the agent is published again', async () => {
      const { schedule } = await service.create(alice, WEEKLY);
      expect(schedule.agentVersion).to.equal(3);
      expect(await fire(schedule.id)).to.equal(3);

      versions.publish();

      expect(await fire(schedule.id)).to.equal(3);
      expect((await service.get(alice, schedule.id)).agentVersion).to.equal(3);
    });

    it('GA-01: its owner promotes it; a version that does not exist is refused and changes nothing', async () => {
      const { schedule } = await service.create(alice, WEEKLY);
      versions.publish();

      expect((await service.setAgentVersion(alice, schedule.id, 4)).agentVersion).to.equal(4);
      expect(await fire(schedule.id)).to.equal(4);

      const refused = await refusal(service.setAgentVersion(alice, schedule.id, 9));
      expect([refused.code, refused.field]).to.deep.equal(['SCHEDULE_INVALID', 'agentVersion']);
      expect(await fire(schedule.id)).to.equal(4);
    });

    it('GA-01: rolling a schedule back to an older version runs that version', async () => {
      const { schedule } = await service.create(alice, WEEKLY);

      await service.setAgentVersion(alice, schedule.id, 1);

      expect(await fire(schedule.id)).to.equal(1);
    });

    it('OWD-14: `latest` is an explicit opt-in: its firings ask for no version, so each runs the newest', async () => {
      const { schedule } = await service.create(alice, { ...WEEKLY, agentVersion: 'latest' });

      expect(schedule.agentVersion).to.equal('latest');
      expect(versions.asked).to.deep.equal([]);
      expect(await fire(schedule.id)).to.equal(undefined);
    });

    it('OWD-14: a version named at creation is checked against what the actor may run', async () => {
      expect((await service.create(alice, { ...WEEKLY, agentVersion: 2 })).schedule.agentVersion).to.equal(2);
      const refused = await refusal(service.create(alice, { ...WEEKLY, agentVersion: 7 }));
      expect(refused.field).to.equal('agentVersion');
    });

    it('GA-01: a schedule from before versions runs version 1, the one the migration made', async () => {
      const legacy = aSchedule({ actorId: ALICE });
      delete legacy.agentVersion;
      await store.create(legacy, { perActor: 50, perOrg: 500 });

      expect((await service.get(alice, legacy.id)).agentVersion).to.equal(1);
      expect(await fire(legacy.id)).to.equal(1);
    });

    it('OWD-14: an admin may pause the schedule but not change what runs as its owner', async () => {
      const { schedule } = await service.create(alice, WEEKLY);

      expect((await refusal(service.setAgentVersion(admin, schedule.id, 1))).code).to.equal('SCHEDULE_FORBIDDEN');
    });

    it('OWD-14: an assistant schedule has no versions to pin', async () => {
      const assistant = { ...WEEKLY, agentKey: undefined };
      const { schedule } = await service.create(alice, assistant);

      expect(schedule.agentVersion).to.equal(null);
      expect((await refusal(service.create(alice, { ...assistant, agentVersion: 2 }))).code).to.equal('SCHEDULE_INVALID');
      expect((await refusal(service.setAgentVersion(alice, schedule.id, 2))).code).to.equal('SCHEDULE_INVALID');
    });
  });

  describe('the version a schedule pins is asked with its owner credential', () => {
    it('OWD-14: asks the agent authority as the actor, for the version named', async () => {
      const authorizeRun = sinon.stub().resolves(agentDefinition({ definitionVersion: 2 }));
      const resolver = new ActorAgentVersions(new FakeActors(), { authorizeRun });

      expect(await resolver.resolve(ORG, ALICE, 'agent-1', 2)).to.equal(2);
      expect(authorizeRun.firstCall.args[0]).to.deep.equal({
        orgId: ORG,
        actorId: ALICE,
        agentKey: 'agent-1',
        authorization: `Bearer token-of-${ALICE}`,
        agentVersion: 2,
      });
    });

    it('OWD-14: an actor who may not run the agent pins nothing', async () => {
      const actors = new FakeActors();
      actors.refused.set(ALICE, 'actor_disabled');
      const denied = new ActorAgentVersions(new FakeActors(), { authorizeRun: async () => null });

      expect(await new ActorAgentVersions(actors, { authorizeRun: sinon.stub() }).resolve(ORG, ALICE, 'a')).to.equal(null);
      expect(await denied.resolve(ORG, ALICE, 'a')).to.equal(null);
    });
  });

  describe('runs record the version they ran', () => {
    const request = {
      orgId: 'org-1',
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer user-token',
      idempotencyKey: 'k-1',
      deadlineMs: 60_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
    };

    it("GA-01: a schedule's firing asks admission's agent authority for its pinned version", async () => {
      const authorizeRun = sinon.stub().resolves(agentDefinition({ definitionVersion: 2 }));
      const store = { admit: sinon.stub().resolves({}) };
      await new RunAdmissionService({ store: store as never, rollout: fixedRollout(), agentAccess: { authorizeRun } })
        .submit({ ...request, agentVersion: 2 });

      expect(authorizeRun.firstCall.args[0].agentVersion).to.equal(2);
      expect(store.admit.firstCall.args[0].agentDefinition.definitionVersion).to.equal(2);
    });

    it('OWD-14: a chat asks for no version, so it runs the latest published one', async () => {
      const authorizeRun = sinon.stub().resolves(agentDefinition());
      await new RunAdmissionService({
        store: { admit: sinon.stub().resolves({}) } as never,
        rollout: fixedRollout(),
        agentAccess: { authorizeRun },
      }).submit(request);

      expect(authorizeRun.firstCall.args[0]).to.not.have.property('agentVersion');
    });

    it("GA-01: the run records its agent's version; the worker reads it from the pinned spec only", () => {
      const command = {
        runId: 'run-1',
        orgId: 'org-1',
        actorId: 'user-1',
        agentKey: 'agent-1',
        agentDefinition: agentDefinition({ agentKey: 'agent-1', definitionVersion: 4 }),
        payloadHash: 'h',
        harnessMajor: 2,
      } as unknown as AdmitRunCommand;
      const run: HarnessRun = rootRunDocument(command, {
        sessionId: 'ses-1',
        userMessageKey: 'm',
        idempotencyKey: 'i',
        absoluteDeadlineAt: 10,
        input: request.input,
        now: 1,
      });

      expect(run.agentVersion).to.equal(4);
      expect(workerRun(run)).to.not.have.property('agentVersion');
    });

    it('OWD-14: a run of the built-in assistant records no agent version', () => {
      const command = {
        runId: 'run-1',
        orgId: 'org-1',
        actorId: 'user-1',
        agentDefinition: agentDefinition({ agentKey: 'agentIdPlaceholder', definitionVersion: 1_700_000_000_000 }),
        payloadHash: 'h',
        harnessMajor: 2,
      } as unknown as AdmitRunCommand;
      const run = rootRunDocument(command, {
        sessionId: 'ses-1',
        userMessageKey: 'm',
        idempotencyKey: 'i',
        absoluteDeadlineAt: 10,
        input: request.input,
        now: 1,
      });

      expect(run).to.not.have.property('agentVersion');
    });
  });
});
