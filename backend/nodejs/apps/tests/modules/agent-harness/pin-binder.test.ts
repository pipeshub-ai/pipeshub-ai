/**
 * CPD-6: what the pin binder reads. `orgLayers` is the one source of an org's
 * layers for the pin and the live layer: the deployment's, then the org
 * layer sources wired here; the
 * `resolved_config` decision reads the worker availability bound after it.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { Container } from 'inversify';
import { InMemoryConfigDocumentStore } from '../../../src/modules/agent-harness/adapters/in-memory-config-document-store';
import { InMemoryWorkerFeatureSightings } from '../../../src/modules/agent-harness/adapters/in-memory-worker-feature-sightings';
import type { PolicyPinning } from '../../../src/modules/agent-harness/application/admission/policy-pinning';
import { WorkerFeatureAvailability } from '../../../src/modules/agent-harness/application/worker-feature-availability';
import { OrgScopedRoutingDecider, ResolvedRoutingDecider, ShadowRoutingDecider } from '../../../src/modules/agent-harness/application/resolved-routing';
import {
  AGENT_HARNESS_POLICY_PINNING,
  AGENT_HARNESS_RESOLVED_ROUTING,
  bindControlPlanePin,
  orgLayers,
  pinningSteps,
  pinWorkerFeatures,
  routingDecider,
} from '../../../src/modules/agent-harness/binders/control-plane-pin';
import type { RoutingDecider } from '../../../src/modules/agent-harness/ports/routing-decider';
import { AGENT_HARNESS_POLICY_BACKING } from '../../../src/modules/agent-harness/binders/policy-store';
import { AGENT_HARNESS_WORKER_FEATURES } from '../../../src/modules/agent-harness/binders/tokens';
import type { AgentHarnessDependencies } from '../../../src/modules/agent-harness/composition';
import type { PolicyLayer } from '../../../src/modules/control-plane';
import { mergeOrgLayers } from '../../../src/modules/agent-harness/binders/org-layer-sources';
import {
  AGENT_HARNESS_EDITION,
  noopHarnessEdition,
  type HarnessEdition,
} from '../../../src/modules/agent-harness/edition';

const ORG = 'org-1';
const FLOW = { level: 'ask', above: 'public' } as const;

const dependencies = async (): Promise<AgentHarnessDependencies> => {
  const approvals = new InMemoryConfigDocumentStore<unknown>();
  await approvals.replace(null, { default: { writes: 'ask' } });
  return {
    rollout: { decide: async () => expect.fail('not asked'), decideForOrg: async () => expect.fail('not asked') },
    approvalSettings: { document: approvals, identities: {} },
  } as unknown as AgentHarnessDependencies;
};

describe('CPD-6 the pin binder', () => {
  const saved = process.env.CONTROL_PLANE_PIN;
  afterEach(() => {
    if (saved === undefined) delete process.env.CONTROL_PLANE_PIN;
    else process.env.CONTROL_PLANE_PIN = saved;
  });

  it('orgLayers: with no org layer source the deployment\'s layers alone', async () => {
    const container = new Container();
    const layers = await orgLayers(container, await dependencies(), FLOW)!(ORG);
    expect([...layers.keys()].sort()).to.deep.equal(['deployment', 'deployment:flow']);
    expect(layers.get('deployment')?.approvals).to.deep.equal({ writes: 'ask' });
    expect(orgLayers(container, { rollout: {} } as unknown as AgentHarnessDependencies, null)).to.equal(undefined);
  });

  it("orgLayers: an edition's source adds the org's layers below the deployment's; it never replaces a deployment scope", async () => {
    const container = new Container();
    const stored: PolicyLayer = { schemaVersion: 1, scope: { kind: 'org', id: ORG }, approvals: { classes: { read: 'ask' } } };
    const forged: PolicyLayer = { schemaVersion: 1, scope: { kind: 'deployment' }, approvals: { writes: 'allow' } };
    const edition: HarnessEdition = {
      ...noopHarnessEdition,
      orgPolicyLayers: () => async () => new Map([[`org:${ORG}`, stored], ['deployment', forged]]),
    };
    container.bind(AGENT_HARNESS_EDITION).toConstantValue(edition);
    const layers = await orgLayers(container, await dependencies(), null)!(ORG);
    expect(layers.get(`org:${ORG}`)).to.deep.equal(stored);
    expect(layers.get('deployment')?.approvals).to.deep.equal({ writes: 'ask' });
  });

  it('mergeOrgLayers: a later source replaces an earlier one\'s section of the same scope and keeps the rest', () => {
    const scope = { kind: 'org' as const, id: ORG };
    const merged = mergeOrgLayers([
      new Map([[`org:${ORG}`, { schemaVersion: 1, scope, budgets: { maxTurns: 5 }, rollout: {} } as PolicyLayer]]),
      new Map([[`org:${ORG}`, { schemaVersion: 1, scope, budgets: { maxTurns: 9 } } as PolicyLayer]]),
    ]);
    expect(merged.get(`org:${ORG}`)).to.deep.equal({ schemaVersion: 1, scope, budgets: { maxTurns: 9 }, rollout: {} });
  });

  it('resolved_config is required until an availability is bound, then only while a worker advertises it', async () => {
    const container = new Container();
    const workers = pinWorkerFeatures(container);
    expect(await workers.live('resolved_config')).to.equal(true);
    expect(() => workers.metrics.withheld('resolved_config')).not.to.throw();

    const withheld: string[] = [];
    let now = 1_000;
    const availability = new WorkerFeatureAvailability({
      sightings: new InMemoryWorkerFeatureSightings({ now: () => now }),
      clock: { now: () => now },
      metrics: { live: () => undefined, stranded: () => undefined, withheld: (feature) => void withheld.push(feature) },
    });
    container.bind(AGENT_HARNESS_WORKER_FEATURES).toConstantValue(availability);
    expect(await workers.live('resolved_config')).to.equal(false);
    workers.metrics.withheld('resolved_config');
    expect(withheld).to.deep.equal(['resolved_config']);
    await availability.observe(['resolved_config']);
    now += 60_000;
    expect(await workers.live('resolved_config')).to.equal(true);
  });

  it('CPD10-44: with the policy store as the record the pin is required whatever workers advertise; in dual the F2 rule stands', () => {
    const on = new Container();
    on.bind(AGENT_HARNESS_POLICY_BACKING).toConstantValue({ mode: 'on' });
    expect(pinWorkerFeatures(on).pinOnly).to.equal(true);
    const dual = new Container();
    dual.bind(AGENT_HARNESS_POLICY_BACKING).toConstantValue({ mode: 'dual' });
    expect(pinWorkerFeatures(dual).pinOnly).to.equal(false);
    expect(pinWorkerFeatures(new Container()).pinOnly).to.equal(false);
  });

  it('off binds nothing; shadow binds the pinning step for admission; a second bind keeps the first', async () => {
    const deps = await dependencies();
    const warned: string[] = [];
    const logger = { warn: (message: string) => void warned.push(message), info: () => undefined, debug: () => undefined, error: () => undefined };
    const off = new Container();
    process.env.CONTROL_PLANE_PIN = 'off';
    bindControlPlanePin({ container: off, dependencies: deps, logger } as never);
    expect(pinningSteps(off)).to.deep.equal({});

    const shadow = new Container();
    process.env.CONTROL_PLANE_PIN = 'shadow';
    bindControlPlanePin({ container: shadow, dependencies: deps, logger } as never);
    const bound = shadow.get<PolicyPinning>(AGENT_HARNESS_POLICY_PINNING);
    bindControlPlanePin({ container: shadow, dependencies: deps, logger } as never);
    expect(shadow.get<PolicyPinning>(AGENT_HARNESS_POLICY_PINNING)).to.equal(bound);
    expect(pinningSteps(shadow).policySteps).to.deep.equal([bound]);
  });

  it('CPD-10: who routes a turn follows the switch: off today’s decider, shadow today’s with the resolved one beside it, on the resolved one', async () => {
    const deps = await dependencies();
    const today: RoutingDecider = { decide: async () => ({ routing: { useHarness: true, harnessMajor: 2, reason: 'enabled' }, scope: {} }) };
    const bound = (mode: string) => {
      const container = new Container();
      process.env.CONTROL_PLANE_PIN = mode;
      bindControlPlanePin({ container, dependencies: deps } as never);
      return container;
    };

    const off = bound('off');
    expect(off.isBound(AGENT_HARNESS_RESOLVED_ROUTING)).to.equal(false);
    expect(routingDecider(off, today)).to.equal(today);

    const shadow = bound('shadow');
    const shadowed = routingDecider(shadow, today);
    expect(shadowed).to.be.instanceOf(ShadowRoutingDecider);
    // Today's answer, whatever the resolved decider makes of a control plane this test wires no store for.
    expect((await shadowed.decide({ orgId: ORG, actorId: 'u3', model: { chatMode: 'quick' } })).routing.reason).to.equal('enabled');
    await (shadowed as ShadowRoutingDecider).idle();

    const on = bound('on');
    // ADR-OPS-08: the resolved decider routes an org with the harness on; any other org's turn takes today's.
    expect(routingDecider(on, today)).to.be.instanceOf(OrgScopedRoutingDecider);
    expect(on.get(AGENT_HARNESS_RESOLVED_ROUTING)).to.be.instanceOf(ResolvedRoutingDecider);
  });

  it('a flow setting that is not one of its values is logged once at bind, by its name', async () => {
    const level = process.env.HARNESS_FLOW_GUARD_LEVEL;
    process.env.HARNESS_FLOW_GUARD_LEVEL = 'block';
    process.env.CONTROL_PLANE_PIN = 'on';
    const warned: Array<Record<string, unknown>> = [];
    try {
      bindControlPlanePin({
        container: new Container(),
        dependencies: await dependencies(),
        logger: { warn: (_message: string, meta: Record<string, unknown>) => void warned.push(meta), info: () => undefined, debug: () => undefined, error: () => undefined },
      } as never);
    } finally {
      if (level === undefined) delete process.env.HARNESS_FLOW_GUARD_LEVEL;
      else process.env.HARNESS_FLOW_GUARD_LEVEL = level;
    }
    expect(warned.map((meta) => [meta['reasonCode'], meta['setting']])).to.deep.equal([['flow_setting_invalid', 'level']]);
  });
});
