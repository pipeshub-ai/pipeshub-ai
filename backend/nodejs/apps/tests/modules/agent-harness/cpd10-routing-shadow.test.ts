/**
 * CPD-10 §4, `CONTROL_PLANE_PIN=shadow` for routing (CPD10-5): today's
 * decider answers, the resolved one is compared after the answer, a
 * difference in path or reason is counted and logged, and nothing is acted on.
 */
import { expect } from 'chai';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { PolicyPinning } from '../../../src/modules/agent-harness/application/admission/policy-pinning';
import { ShadowRoutingDecider } from '../../../src/modules/agent-harness/application/resolved-routing';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { HarnessRunConfigUnavailableError } from '../../../src/modules/agent-harness/domain/errors';
import type { PinShadowDimension } from '../../../src/modules/agent-harness/domain/pin-shadow';
import type { HarnessRolloutSettings } from '../../../src/modules/agent-harness/domain/rollout';
import { profileExecutor } from '../../../src/modules/agent-harness/domain/run-spec';
import type { RoutingDecider, RoutingDecision, RoutingSubject } from '../../../src/modules/agent-harness/ports/routing-decider';
import type { PinBasis, PinSubject, RunConfigPinner } from '../../../src/modules/control-plane';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { fixedRollout } from './support/rollout';
import type { RoutingWorld } from './support/routing-decider-contract';
import { controlPlaneOfWorld, factsOfWorld, resolvedRoutingFromWorld, rolloutRoutingFromWorld } from './support/routing-vectors';
import { ResolvedRoutingDecider } from '../../../src/modules/agent-harness/application/resolved-routing';

const ENABLED: HarnessRolloutSettings = { infrastructureReady: true, killSwitch: false, orgEnabled: true, modelFamilies: null };
const WORLD: RoutingWorld = { settings: ENABLED, workerLive: true };
const SUBJECT: RoutingSubject = { orgId: 'org-1', actorId: 'user-1', agentKey: 'agent-1', sessionId: 's-1', model: { chatMode: 'quick' } };

describe('CPD-10 routing in shadow (CPD10-5)', () => {
  let counted: PinShadowDimension[];
  let logged: Array<{ event: string; meta: Record<string, unknown> }>;

  const shadowed = (today: RoutingDecider, resolved: RoutingDecider) =>
    new ShadowRoutingDecider({
      today,
      resolved,
      metrics: { mismatch: (dimension) => void counted.push(dimension) },
      logger: {
        debug: () => undefined,
        info: () => undefined,
        warn: (event, meta) => void logged.push({ event, meta: meta as Record<string, unknown> }),
        error: () => undefined,
      },
    });

  beforeEach(() => {
    counted = [];
    logged = [];
  });

  it('equal decisions count nothing, and the answer is today’s with what the control plane resolved beside it', async () => {
    const today = await rolloutRoutingFromWorld(WORLD).decide(SUBJECT);
    const decider = shadowed(rolloutRoutingFromWorld(WORLD), resolvedRoutingFromWorld(WORLD));
    const decision = await decider.decide(SUBJECT);
    expect({ routing: decision.routing, scope: decision.scope }).to.deep.equal(today);
    expect(decision.resolved).to.equal(undefined);
    await decider.idle();
    expect(counted).to.deep.equal([]);
    expect(logged).to.deep.equal([]);
    expect((await decision.shadow)?.facts.placement).to.equal(null);
    expect((await decision.shadow)?.basis.orgId).to.equal('org-1');
  });

  for (const [what, resolvedWorld] of [
    ['another path (the control plane sees the kill switch)', { ...WORLD, settings: { ...ENABLED, killSwitch: true } }],
    ['another reason on the same path', { ...WORLD, settings: { ...ENABLED, orgEnabled: false } }],
  ] as Array<[string, RoutingWorld]>) {
    it(`a planted difference is counted as routing and logged with ids only: ${what}`, async () => {
      const todayWorld: RoutingWorld = what.startsWith('another reason') ? { ...WORLD, settings: { ...ENABLED, killSwitch: true } } : WORLD;
      const decider = shadowed(rolloutRoutingFromWorld(todayWorld), resolvedRoutingFromWorld(resolvedWorld));
      const decision = await decider.decide(SUBJECT);
      expect(decision.routing).to.deep.equal((await rolloutRoutingFromWorld(todayWorld).decide(SUBJECT)).routing);
      await decider.idle();
      expect(counted).to.deep.equal(['routing']);
      expect(logged).to.have.length(1);
      expect(logged[0]?.event).to.equal('cp.shadow_mismatch');
      expect(logged[0]?.meta).to.deep.include({ orgId: 'org-1', sessionId: 's-1', actorId: 'user-1', agentKey: 'agent-1' });
      expect(logged[0]?.meta['dimensions']).to.deep.equal(['routing']);
      expect(JSON.stringify(logged[0]?.meta)).to.not.contain('quick');
    });
  }

  it('the turn does not wait for the shadow: the answer is given while the resolved decider is still held', async () => {
    let release: (decision: RoutingDecision) => void = () => undefined;
    const held: RoutingDecider = { decide: () => new Promise((resolve) => (release = resolve)) };
    const decider = shadowed(rolloutRoutingFromWorld(WORLD), held);
    const decision = await decider.decide(SUBJECT);
    expect(decision.routing.useHarness).to.equal(true);
    expect(counted).to.deep.equal([]);
    release({ routing: { useHarness: false, harnessMajor: 2, reason: 'kill_switch' }, scope: {} });
    await decider.idle();
    expect(counted).to.deep.equal(['routing']);
    expect(await decision.shadow).to.equal(undefined);
  });

  it('a resolved decider that cannot decide counts unavailable and changes nothing', async () => {
    const failing: RoutingDecider = { decide: async () => Promise.reject(new HarnessRunConfigUnavailableError()) };
    const decider = shadowed(rolloutRoutingFromWorld(WORLD), failing);
    const decision = await decider.decide(SUBJECT);
    expect(decision.routing.useHarness).to.equal(true);
    await decider.idle();
    expect(counted).to.deep.equal(['unavailable']);
    expect(logged[0]?.meta['dimensions']).to.deep.equal(['unavailable']);
    expect(await decision.shadow).to.equal(undefined);
  });

  it('today’s decider failing is the failure; nothing is compared', async () => {
    let asked = 0;
    const decider = shadowed(
      { decide: async () => Promise.reject(new Error('down')) },
      { decide: async (subject) => ((asked += 1), resolvedRoutingFromWorld(WORLD).decide(subject)) },
    );
    expect(await decider.decide(SUBJECT).catch((e: unknown) => (e as Error).message)).to.equal('down');
    await decider.idle();
    expect([asked, counted]).to.deep.equal([0, []]);
  });

  it('the shadow pin of a durable admission is made on the shadow route’s facts and snapshot', async () => {
    const world: RoutingWorld = { settings: ENABLED, workerLive: false };
    const { pinner: inner } = controlPlaneOfWorld(world);
    const pins: Array<{ subject: PinSubject; basis?: PinBasis }> = [];
    const routes: PinBasis[] = [];
    const pinner: RunConfigPinner = {
      route: async (subject) => {
        const route = await inner.route(subject);
        routes.push(route.basis);
        return route;
      },
      pin: (subject, basis) => {
        pins.push({ subject, ...(basis !== undefined ? { basis } : {}) });
        return inner.pin(subject, basis);
      },
    };
    const routing = shadowed(rolloutRoutingFromWorld(world), new ResolvedRoutingDecider({ pinner, facts: factsOfWorld(world) }));
    const step = new PolicyPinning({
      mode: 'shadow',
      pinner,
      metrics: { mismatch: (dimension) => void counted.push(dimension) },
      logger: { debug: () => undefined, info: () => undefined, warn: () => undefined, error: () => undefined },
    });
    const outcome = await new RunAdmissionService({
      store: new InMemoryExecutionStore({ now: () => 1_000 }),
      rollout: fixedRollout(),
      routing,
      agentAccess: grantingAccess((agentKey) => agentDefinition({ agentKey })),
      executors: profileExecutor,
      clock: { now: () => 1_000 },
      policySteps: [step],
    }).submit({
      orgId: 'org-1',
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer u',
      idempotencyKey: 'k-1',
      firstSubmission: true,
      deadlineMs: 60_000,
      trigger: { scheduleId: 's-1', firingId: 'f-1', slotKey: 'slot-1' },
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'quick' } },
    });
    expect(outcome.route).to.equal('durable');
    await routing.idle();
    await step.idle();
    expect(pins).to.have.length(1);
    expect(pins[0]?.basis).to.equal(routes[0]);
    expect(pins[0]?.subject.routingFacts).to.deep.include({ workerLive: false, scheduled: true });
    expect(counted).to.deep.equal([]);
  });
});
