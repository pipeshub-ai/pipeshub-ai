/**
 * CPD-5 S5: `CONTROL_PLANE_PIN=shadow`. The real admission service, in-memory
 * store and engine ceilings; the production pinner over CPD-3's resolver and
 * a snapshot the test sets. The shadow pins beside today's decision, counts
 * every dimension they disagree on and never changes what the run gets.
 */
import { expect } from 'chai';
import { releaseLayers } from '../../../src/modules/agent-harness/domain/pin-shadow';
import { randomUUID } from 'crypto';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { HeadCachedRolloutSettings } from '../../../src/modules/agent-harness/adapters/configuration/head-cached-rollout-settings';
import { policySnapshotLoader } from '../../../src/modules/agent-harness/adapters/control-plane/policy-snapshot-loader';
import { PolicyPinning } from '../../../src/modules/agent-harness/application/admission/policy-pinning';
import {
  RunAdmissionService,
  RunAdmissionServiceOptions,
  SubmitDurableRun,
} from '../../../src/modules/agent-harness/application/run-admission-service';
import { ASSISTANT_AGENT_DEFINITION_KEY } from '../../../src/modules/agent-harness/domain/rollout';
import type { PolicyLayer } from '../../../src/modules/agent-harness/contracts/generated/core/policy/policy-layer';
import type { PinShadowDimension } from '../../../src/modules/agent-harness/domain/pin-shadow';
import { PARENT_DIMENSIONS, PolicyResolver } from '../../../src/modules/agent-harness/domain/policy';
import type { HarnessRolloutSettings } from '../../../src/modules/agent-harness/domain/rollout';
import { profileExecutor } from '../../../src/modules/agent-harness/domain/run-spec';
import { canonicalDigest } from '../../../src/modules/agent-harness/contracts/canonical-json';
import {
  PolicyRunConfigPinner,
  prefixSources,
  ResolvedSnapshotCache,
  runSources,
  type ControlPlanePin,
  type PinSubject,
  type RunConfigPinner,
} from '../../../src/modules/control-plane';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { pinnerOf } from './support/a-pin';
import { testModelAccess } from './support/model-access';
import { HARNESS_ENABLED } from './support/rollout';
import { InMemoryHeadRevisions } from '../control-plane/support/head-revisions-contract';
import { FakeEngineCeilings, FakeModelAccessPolicies } from './support/org-policy-fakes';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const ORG = 'org-shadow';
const LABS: HarnessRolloutSettings = { infrastructureReady: true, killSwitch: false, orgEnabled: true, modelFamilies: null };

describe('CPD-5 CONTROL_PLANE_PIN=shadow (PIN-26, CPD5-40, CPD5-41, CPD5-42)', () => {
  let store: InMemoryExecutionStore;
  let ceilings: FakeEngineCeilings;
  let heads: InMemoryHeadRevisions;
  let stored: Map<string, PolicyLayer>;
  let counted: PinShadowDimension[];
  let lines: string[];
  let labsReads: number;
  let pinned: PinSubject[];

  const pinnerOver = (): RunConfigPinner =>
    new PolicyRunConfigPinner({
      snapshots: new ResolvedSnapshotCache({
        load: policySnapshotLoader({
          heads,
          layers: async () => stored,
          rollout: { read: async () => LABS },
        }),
        heads,
      }),
      engine: new PolicyResolver(),
      effectiveSet: { resolve: () => ({ items: [], digest: canonicalDigest([]) }) },
      releases: releaseLayers(),
      prefix: prefixSources(),
      run: runSources(PARENT_DIMENSIONS),
      parent: PARENT_DIMENSIONS,
    });

  const spying = (inner: RunConfigPinner): RunConfigPinner => ({
    route: (subject) => inner.route(subject),
    pin: async (subject, basis) => {
      pinned.push(subject);
      return inner.pin(subject, basis);
    },
  });

  const pinning = (mode: ControlPlanePin, pinner: RunConfigPinner = spying(pinnerOver())) =>
    new PolicyPinning({
      mode,
      pinner,
      metrics: { mismatch: (dimension) => void counted.push(dimension) },
      logger: { debug: () => undefined, info: () => undefined, error: () => undefined, warn: (message) => void lines.push(message) },
    });

  const admit = async (
    step: PolicyPinning,
    definition = agentDefinition({ agentKey: 'a7' }),
    request: Partial<SubmitDurableRun> = {},
    codingEngine?: RunAdmissionServiceOptions['codingEngine'],
  ) => {
    const outcome = await new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      agentAccess: grantingAccess(() => definition),
      engineProfiles: ceilings,
      modelAccess: testModelAccess({
        policies: new FakeModelAccessPolicies(),
      }),
      executors: profileExecutor,
      policySteps: [step],
      ...(codingEngine !== undefined ? { codingEngine } : {}),
    }).submit({
      orgId: ORG,
      actorId: 'u3',
      agentKey: 'a7',
      authorization: 'Bearer u3',
      idempotencyKey: randomUUID(),
      deadlineMs: 600_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
      ...request,
    });
    await step.idle();
    if (outcome.route !== 'durable') return expect.fail('expected a durable admission');
    return outcome.admission;
  };

  beforeEach(() => {
    store = new InMemoryExecutionStore();
    ceilings = new FakeEngineCeilings();
    heads = new InMemoryHeadRevisions();
    stored = new Map();
    counted = [];
    lines = [];
    labsReads = 0;
    pinned = [];
  });

  it('PIN-26: the pin and today agree on a loop run: nothing is counted', async () => {
    await admit(pinning('shadow'));
    expect(pinned).to.have.length(1);
    expect(counted).to.deep.equal([]);
  });

  it('PIN-26: an engine run under an org ceiling agrees too', async () => {
    await ceilings.write(ORG, { limits: { maxSteps: 20 } }, 'p_admin');
    stored.set(`org:${ORG}`, { schemaVersion: 1, scope: { kind: 'org' }, engineProfile: { limits: { maxSteps: 20 } } });
    // An hour's deadline over the profile's 30-minute wall clock: one budget, met with the engine limit.
    await admit(pinning('shadow'), agentDefinition({ agentKey: 'a7', engineProfile: { engines: ['opencode'] } }), {
      deadlineMs: 3_600_000,
    });
    expect(counted).to.deep.equal([]);
  });

  it('ENGPICK: a default-chat Agent-mode turn handed the org’s coding engine pins what today decides: nothing is counted', async () => {
    const admitted = await admit(
      pinning('shadow'),
      agentDefinition({ agentKey: ASSISTANT_AGENT_DEFINITION_KEY }),
      { agentKey: undefined, durableRequested: true, input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'agent' } } },
      { forTurn: async () => ({ engine: 'opencode', source: 'org' }) },
    );
    expect(admitted.run.requires?.features).to.include('code_task_tool');
    expect(pinned).to.have.length(1);
    expect(pinned[0]?.agent?.engineProfile).to.deep.equal({ engines: ['opencode'] });
    expect(counted).to.deep.equal([]);
  });

  it('PIN-26: a planted difference is counted once under its dimension and changes nothing the run gets', async () => {
    const before = await admit(pinning('off'));
    stored.set(`org:${ORG}`, { schemaVersion: 1, scope: { kind: 'org' }, budgets: { maxCostUsd: 3 } });
    const after = await admit(pinning('shadow'));
    expect(counted).to.deep.equal(['budgets']);
    expect(lines).to.deep.equal(['cp.shadow_mismatch']);
    const [a, b] = await Promise.all([before, after].map((x) => store.readClaimSubject(ORG, x.run.runId)));
    expect(b?.engineProfile ?? null).to.deep.equal(a?.engineProfile ?? null);
    expect(b?.run.requires).to.deep.equal(a?.run.requires);
  });

  it('PIN-26: an engine ceiling only the pin knows of is counted as engine_profile', async () => {
    stored.set(`org:${ORG}`, { schemaVersion: 1, scope: { kind: 'org' }, engineProfile: { limits: { maxSteps: 20 } } });
    await admit(pinning('shadow'), agentDefinition({ agentKey: 'a7', engineProfile: { engines: ['opencode'] } }));
    expect(counted).to.deep.equal(['engine_profile']);
  });

  it('CPD5-40: a pin that fails or refuses changes nothing: counted unavailable or refused', async () => {
    const failing = pinning('shadow', pinnerOf(async () => Promise.reject(new Error('store down'))));
    const admitted = await admit(failing);
    expect(admitted.created).to.equal(true);
    expect(counted).to.deep.equal(['unavailable']);
    stored.set(`agent:a7`, { schemaVersion: 1, scope: { kind: 'agent' }, budgets: { activeTimeS: 60 } });
    counted = [];
    expect((await admit(pinning('shadow'))).created).to.equal(true);
    expect(counted).to.deep.equal(['refused']);
  });

  it('CPD5-43 (found on the fault-stack rig): a host whose rollout policy exposes no switches is not counted as a rollout mismatch', async () => {
    const withoutLabs = new PolicyRunConfigPinner({
      snapshots: new ResolvedSnapshotCache({ load: policySnapshotLoader({ heads, layers: async () => stored }), heads }),
      engine: new PolicyResolver(),
      effectiveSet: { resolve: () => ({ items: [], digest: canonicalDigest([]) }) },
      releases: releaseLayers(),
      prefix: prefixSources(),
      run: runSources(PARENT_DIMENSIONS),
      parent: PARENT_DIMENSIONS,
    });
    const step = (rolloutFolded: boolean) =>
      new PolicyPinning({
        mode: 'shadow',
        pinner: withoutLabs,
        rolloutFolded,
        metrics: { mismatch: (dimension) => void counted.push(dimension) },
        logger: { debug: () => undefined, info: () => undefined, error: () => undefined, warn: () => undefined },
      });
    await admit(step(false));
    expect(counted).to.deep.equal([]);
    await admit(step(true));
    expect(counted).to.deep.equal(['rollout']);
  });

  it('CPD5-41: off never pins', async () => {
    await admit(pinning('off'));
    expect(pinned).to.deep.equal([]);
  });

  it('CPD5-42: with the control plane on, the Labs document is read once per org head', async () => {
    const labs = new HeadCachedRolloutSettings(
      { read: async () => ((labsReads += 1), LABS) },
      heads,
      30_000,
      () => 0,
    );
    await labs.read(ORG);
    await labs.read(ORG);
    expect(labsReads).to.equal(1);
    heads.commit(ORG);
    await labs.read(ORG);
    expect(labsReads).to.equal(2);
  });
});
