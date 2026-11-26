/**
 * CPD-10 §2: the second `RoutingDecider`, over the resolved run
 * configuration. LAT1's contract on it (CPD10-1); and an admission under
 * `CONTROL_PLANE_PIN=on`, where the route and the pin are one resolution on
 * one read of the heads (CPD10-3, CPD10-4). The production pinner, snapshot
 * cache and loader; the real admission service and in-memory store.
 */
import { expect } from 'chai';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { PolicyPinning } from '../../../src/modules/agent-harness/application/admission/policy-pinning';
import { ResolvedRoutingDecider, rolloutDecision } from '../../../src/modules/agent-harness/application/resolved-routing';
import { RoutingFactsReader } from '../../../src/modules/agent-harness/application/routing-facts';
import { RunAdmissionService, type SubmitDurableRun } from '../../../src/modules/agent-harness/application/run-admission-service';
import { HarnessRunConfigUnavailableError } from '../../../src/modules/agent-harness/domain/errors';
import type { HarnessRolloutSettings } from '../../../src/modules/agent-harness/domain/rollout';
import { profileExecutor } from '../../../src/modules/agent-harness/domain/run-spec';
import type { RoutingSubject } from '../../../src/modules/agent-harness/ports/routing-decider';
import { LEGACY_REASONS, type PinBasis, type RouteSubject, type RunConfigPinner } from '../../../src/modules/control-plane';
import { InMemoryHeadRevisions } from '../control-plane/support/head-revisions-contract';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { fixedRollout } from './support/rollout';
import { PROJECT, routingDeciderContract, SESSION, type RoutingWorld } from './support/routing-decider-contract';
import { controlPlaneOfWorld, factsOfWorld, resolvedRoutingFromWorld } from './support/routing-vectors';

routingDeciderContract('the resolved run configuration (ResolvedRoutingDecider)', resolvedRoutingFromWorld);

const ENABLED: HarnessRolloutSettings = { infrastructureReady: true, killSwitch: false, orgEnabled: true, modelFamilies: null };
const SILENT = { debug: () => undefined, info: () => undefined, warn: () => undefined, error: () => undefined };
const ORG = 'org-1';

const subject = (overrides: Partial<RoutingSubject> = {}): RoutingSubject => ({
  orgId: ORG,
  actorId: 'user-1',
  agentKey: 'agent-1',
  model: { chatMode: 'quick' },
  ...overrides,
});

describe('CPD-10 ResolvedRoutingDecider', () => {
  describe('CPD10-1: what a decision carries for the pin and the legacy header', () => {
    it('a durable decision names the facts it was decided on and the snapshot it was resolved on', async () => {
      const world: RoutingWorld = { settings: ENABLED, workerLive: false, conversation: { durable: true } };
      const { resolved, routing } = await resolvedRoutingFromWorld(world).decide(subject({ sessionId: SESSION, model: { chatMode: 'quick', modelKey: 'model-9' } }));
      expect(routing).to.deep.equal({ useHarness: true, harnessMajor: 2, reason: 'enabled' });
      expect(resolved?.facts).to.deep.equal({
        infrastructureReady: true,
        workerLive: false,
        scheduled: false,
        placement: { durable: true, projectId: null },
        modelKey: 'model-9',
        existing: true,
        requested: null,
      });
      expect([resolved?.basis.orgId, resolved?.basis.agentKey, resolved?.basis.actorId]).to.deep.equal([ORG, 'agent-1', 'user-1']);
      expect(resolved?.config.dimensions['rollout.ENABLE_AGENT_HARNESS']?.value).to.equal(true);
      expect(resolved?.config.dimensions['rollout.durable.modes']?.value).to.deep.equal(['auto', 'deep', 'quick', 'react']);
    });

    it('a turn policy keeps on today’s loop is decided before the conversation or the workers are read', async () => {
      const read: string[] = [];
      const world: RoutingWorld = { settings: { ...ENABLED, killSwitch: true }, workerLive: true };
      const decider = new ResolvedRoutingDecider({
        pinner: controlPlaneOfWorld(world).pinner,
        facts: new RoutingFactsReader({
          infrastructureReady: () => true,
          sessions: { placement: async () => (read.push('placement'), null) },
          liveness: { live: async () => (read.push('liveness'), true) },
        }),
      });
      const { routing, resolved } = await decider.decide(subject({ sessionId: SESSION }));
      expect(routing.reason).to.equal('kill_switch');
      expect(read).to.deep.equal([]);
      expect(resolved?.config.dimensions['rollout.AGENT_HARNESS_KILL_SWITCH']?.value).to.equal(true);
      for (const mode of ['planExecute', 'verification']) {
        expect((await resolvedRoutingFromWorld({ settings: ENABLED, workerLive: true }).decide(subject({ model: { chatMode: mode } }))).routing.reason).to.equal(
          'chat_mode_not_supported',
        );
      }
    });

    it('the family rule reads the model key the request names, among every kind of configured model', async () => {
      const families: HarnessRolloutSettings = { ...ENABLED, modelFamilies: ['GPT '] };
      const decide = (modelKey: string | undefined) =>
        resolvedRoutingFromWorld({ settings: families, workerLive: true, modelFamily: 'gpt' }).decide(
          subject({ model: { chatMode: 'quick', ...(modelKey !== undefined ? { modelKey } : {}) } }),
        );
      expect((await decide(undefined)).routing.useHarness).to.equal(true);
      expect((await decide('model-1')).routing.useHarness).to.equal(true);
      expect((await decide('model-gone')).routing.reason).to.equal('model_family_unknown');
    });

    it('rolloutDecision: every reason of the routing table is a rollout reason, and no other string is a decision', () => {
      for (const reason of LEGACY_REASONS) {
        expect(rolloutDecision({ path: 'legacy', reason })).to.deep.equal({ useHarness: false, harnessMajor: 2, reason });
      }
      expect(() => rolloutDecision({ path: 'legacy', reason: 'inherited' })).to.throw(/routing table/);
      expect(() => rolloutDecision({ path: 'legacy', reason: null })).to.throw(/routing table/);
    });
  });

  describe('CPD10-7: a route that cannot be resolved is no decision', () => {
    it('an unreadable snapshot rejects RUN_CONFIG_UNAVAILABLE', async () => {
      const heads = new InMemoryHeadRevisions();
      heads.unreadable = true;
      const world: RoutingWorld = { settings: ENABLED, workerLive: true };
      const decider = new ResolvedRoutingDecider({ pinner: controlPlaneOfWorld(world, heads).pinner, facts: factsOfWorld(world) });
      const error = await decider.decide(subject()).catch((e: unknown) => e);
      expect(error).to.be.instanceOf(HarnessRunConfigUnavailableError);
      expect((error as HarnessRunConfigUnavailableError).code).to.equal('RUN_CONFIG_UNAVAILABLE');
    });

    it('so are workers that cannot be asked, and a project’s conversation that does not name its project', async () => {
      const world: RoutingWorld = { settings: ENABLED, workerLive: true };
      const { pinner } = controlPlaneOfWorld(world);
      const down = new ResolvedRoutingDecider({
        pinner,
        facts: new RoutingFactsReader({ infrastructureReady: () => true, liveness: { live: async () => Promise.reject(new Error('down')) } }),
      });
      expect(await down.decide(subject()).catch((e: unknown) => e)).to.be.instanceOf(HarnessRunConfigUnavailableError);
      const nameless = new ResolvedRoutingDecider({
        pinner,
        facts: new RoutingFactsReader({ infrastructureReady: () => true, sessions: { placement: async () => ({ inProject: true, durable: false }) } }),
      });
      expect(await nameless.decide(subject({ sessionId: SESSION })).catch((e: unknown) => e)).to.be.instanceOf(HarnessRunConfigUnavailableError);
    });

    it('a placement that cannot be read routes nothing by itself; with no session source or liveness source none is asked', async () => {
      const world: RoutingWorld = { settings: ENABLED, workerLive: true };
      const { pinner } = controlPlaneOfWorld(world);
      const unreadable = new ResolvedRoutingDecider({
        pinner,
        facts: new RoutingFactsReader({ infrastructureReady: () => true, sessions: { placement: async () => Promise.reject(new Error('down')) } }),
      });
      const decided = await unreadable.decide(subject({ sessionId: SESSION }));
      expect([decided.routing.useHarness, decided.resolved?.facts.placement, decided.resolved?.facts.workerLive]).to.deep.equal([true, null, true]);
    });

    it('through admission the refusal keeps its code, and nothing is written', async () => {
      const heads = new InMemoryHeadRevisions();
      heads.unreadable = true;
      const world: RoutingWorld = { settings: ENABLED, workerLive: true };
      const store = new InMemoryExecutionStore({ now: () => 1_000 });
      const admissions = new RunAdmissionService({
        store,
        rollout: fixedRollout(),
        routing: new ResolvedRoutingDecider({ pinner: controlPlaneOfWorld(world, heads).pinner, facts: factsOfWorld(world) }),
        clock: { now: () => 1_000 },
      });
      const error = await admissions.submit(turn()).catch((e: unknown) => e);
      expect(error).to.be.instanceOf(HarnessRunConfigUnavailableError);
      expect((store as unknown as { runs: Map<string, unknown> }).runs.size).to.equal(0);
    });
  });

  describe('an admission under CONTROL_PLANE_PIN=on', () => {
    const on = (world: RoutingWorld) => {
      const heads = new InMemoryHeadRevisions();
      const store = new InMemoryExecutionStore({ now: () => 1_000 });
      store.controlPlaneHeads = (orgId) => heads.current(orgId);
      const { pinner: inner, snapshots } = controlPlaneOfWorld(world, heads);
      const calls: Array<{ call: 'route' | 'pin'; subject: RouteSubject; basis?: PinBasis }> = [];
      const hooks = { afterPin: (): unknown => undefined };
      const pinner: RunConfigPinner = {
        route: async (routed) => {
          const route = await inner.route(routed);
          calls.push({ call: 'route', subject: routed, basis: route.basis });
          return route;
        },
        pin: async (pinned, basis) => {
          calls.push({ call: 'pin', subject: pinned, ...(basis !== undefined ? { basis } : {}) });
          const pin = await inner.pin(pinned, basis);
          await hooks.afterPin();
          return pin;
        },
      };
      const step = new PolicyPinning({
        mode: 'on',
        pinner,
        metrics: { mismatch: () => undefined },
        logger: SILENT,
        refresh: (orgId) => snapshots.refresh(orgId),
      });
      const admissions = new RunAdmissionService({
        store,
        rollout: fixedRollout(),
        routing: new ResolvedRoutingDecider({ pinner, facts: factsOfWorld(world) }),
        agentAccess: grantingAccess((agentKey) => agentDefinition({ agentKey })),
        executors: profileExecutor,
        clock: { now: () => 1_000 },
        policySteps: [step],
        repin: (context) => step.repin(context),
      });
      return { heads, store, admissions, calls, hooks };
    };

    it('CPD10-4: the heads are read once for the route and the pin together', async () => {
      const { heads, admissions } = on({ settings: ENABLED, workerLive: true });
      const first = await admissions.submit(turn({ agentKey: 'agent-1', authorization: 'Bearer u' }));
      expect(first.route).to.equal('durable');
      // A first turn also loads the snapshot, under the revisions the cache's check just read (PERFE2-9).
      expect(heads.reads).to.equal(1);
      heads.reads = 0;
      const second = await admissions.submit(turn({ agentKey: 'agent-1', authorization: 'Bearer u' }));
      expect(second.route).to.equal('durable');
      expect(heads.reads).to.equal(1);
    });

    it('CPD10-3: the pin is made on the facts and the snapshot the route was decided on, and the run keeps it', async () => {
      const { store, admissions, calls } = on({ settings: ENABLED, workerLive: false });
      const fired = await admissions.submit(turn({ agentKey: 'agent-1', authorization: 'Bearer u', trigger: { scheduleId: 's-1', firingId: 'f-1', slotKey: 'slot-1' } }));
      if (fired.route !== 'durable') return expect.fail('a schedule’s firing waits for a worker');
      const [route, pin] = calls;
      expect(calls.map(({ call }) => call)).to.deep.equal(['route', 'pin']);
      expect(pin?.basis).to.equal(route?.basis);
      expect(pin?.subject.routingFacts).to.deep.equal({
        infrastructureReady: true,
        workerLive: false,
        scheduled: true,
        placement: null,
        modelKey: null,
        existing: false,
        requested: null,
      });
      const kept = (await store.readClaimSubject(ORG, fired.admission.run.runId))?.resolved;
      expect(kept?.routing).to.deep.equal({ path: 'durable', reason: null });
      expect(kept?.scope).to.deep.equal({ agentKey: 'agent-1', projectId: null });
      expect(kept?.headRevision).to.equal(route?.basis?.headRevision);
    });

    it('a control-plane write between the pin and the commit: pinned again on a fresh snapshot, on the facts already read', async () => {
      const { heads, store, admissions, calls, hooks } = on({ settings: ENABLED, workerLive: true });
      hooks.afterPin = () => {
        hooks.afterPin = () => undefined;
        heads.commit(ORG);
      };
      const admitted = await admissions.submit(turn({ agentKey: 'agent-1', authorization: 'Bearer u' }));
      if (admitted.route !== 'durable') return expect.fail('durable');
      expect(calls.map(({ call }) => call)).to.deep.equal(['route', 'pin', 'route', 'pin']);
      const [first, , second, repinned] = calls;
      expect(second?.basis).to.not.equal(first?.basis);
      expect(repinned?.basis).to.equal(second?.basis);
      expect(repinned?.subject.routingFacts).to.deep.equal(first?.subject.routingFacts);
      expect((await store.readClaimSubject(ORG, admitted.admission.run.runId))?.resolved?.headRevision).to.equal(1);
    });

    it('a write that sends the turn to today’s loop while it is admitted: not admitted on the route it lost, nothing written', async () => {
      const world: RoutingWorld = { settings: { ...ENABLED }, workerLive: true };
      const { heads, store, admissions, hooks } = on(world);
      hooks.afterPin = () => {
        hooks.afterPin = () => undefined;
        world.settings.killSwitch = true;
        heads.commit(ORG);
      };
      const refused = await admissions.submit(turn({ agentKey: 'agent-1', authorization: 'Bearer u' })).catch((e: unknown) => e);
      expect(refused).to.be.instanceOf(HarnessRunConfigUnavailableError);
      expect((refused as Error).message).to.match(/changed its configuration during admission/);
      expect((store as unknown as { runs: Map<string, unknown> }).runs.size).to.equal(0);
      const next = await admissions.submit(turn({ agentKey: 'agent-1', authorization: 'Bearer u' }));
      expect(next.route === 'legacy' && next.rollout.reason).to.equal('kill_switch');
    });

    it('a legacy decision carries what was resolved, to the caller that serves the legacy route', async () => {
      const { store, admissions } = on({ settings: ENABLED, workerLive: true });
      const outcome = await admissions.submit(turn({ agentKey: 'agent-1', authorization: 'Bearer u', projectId: PROJECT }));
      if (outcome.route !== 'legacy') return expect.fail('a project’s turn stays on today’s loop');
      expect(outcome.rollout.reason).to.equal('project_scope');
      expect(outcome.resolved?.basis.headRevision).to.equal(0);
      expect(outcome.resolved?.facts.placement).to.equal(null);
      expect(Object.keys(outcome.resolved?.config.dimensions ?? {}).every((path) => path.startsWith('rollout.'))).to.equal(true);
      expect((store as unknown as { runs: Map<string, unknown> }).runs.size).to.equal(0);
    });
  });
});

function turn(overrides: Partial<SubmitDurableRun> = {}): SubmitDurableRun {
  return {
    orgId: ORG,
    actorId: 'user-1',
    idempotencyKey: `k-${String(Math.random())}`,
    firstSubmission: true,
    deadlineMs: 60_000,
    input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'quick' } },
    ...overrides,
  };
}
