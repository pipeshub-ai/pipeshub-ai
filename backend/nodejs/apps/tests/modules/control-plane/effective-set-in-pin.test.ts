/**
 * CPD-7 with CPD-5's pinner (`CONTROL_PLANE_PIN=on`): the run's pin carries
 * the effective set, resolved from the org's installations and the agent's
 * grant on the pinner's snapshot. The agent's owner comes from the definition
 * admission loaded, so what the owner shared through the agent is pinned
 * ready for a colleague's run (ESR-10), never `not_visible`. A child's spawn
 * has no definition in hand: it takes the owner its parent's pin names (CPD-6).
 */
import { expect } from 'chai';
import { randomUUID } from 'crypto';
import { policySnapshotLoader } from '../../../src/modules/agent-harness/adapters/control-plane/policy-snapshot-loader';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { ChildPinning } from '../../../src/modules/agent-harness/application/admission/child-pinning';
import { PolicyPinning } from '../../../src/modules/agent-harness/application/admission/policy-pinning';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { releaseLayers } from '../../../src/modules/agent-harness/domain/pin-shadow';
import { PARENT_DIMENSIONS, PolicyResolver } from '../../../src/modules/agent-harness/domain/policy';
import { profileExecutor } from '../../../src/modules/agent-harness/domain/run-spec';
import {
  CanonicalEffectiveSetResolver,
  PolicyRunConfigPinner,
  type ResolvedRunConfig,
  prefixSources,
  ResolvedSnapshotCache,
  runSources,
} from '../../../src/modules/control-plane';
import { agentDefinition, grantingAccess } from '../agent-harness/support/agent-definition';
import { testModelAccess } from '../agent-harness/support/model-access';
import { HARNESS_ENABLED } from '../agent-harness/support/rollout';
import { aGrant, anEntry, anInstallation, directTx, ORG_A } from './support/cp-objects';
import { InMemoryHeadRevisions } from './support/head-revisions-contract';
import { InMemoryGrantStore, InMemoryInstallationStore } from './support/in-memory-stores';
import { FakeEngineCeilings, FakeModelAccessPolicies } from '../agent-harness/support/org-policy-fakes';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const NOTES = 'skill_notes';

describe('CPD-7 the effective set in the pin (CONTROL_PLANE_PIN=on)', () => {
  const LOGGER = { debug: () => undefined, info: () => undefined, warn: () => undefined, error: () => undefined };
  const states = (pin: ResolvedRunConfig) =>
    pin.effectiveSet.items.map((item) => [item.installation, item.state, item.reason, item.provenance.visibleVia]);

  async function admitted(owner: string | undefined) {
    return states((await root(owner)).pinned);
  }

  async function root(owner: string | undefined) {
    const store = new InMemoryExecutionStore();
    const heads = new InMemoryHeadRevisions();
    store.controlPlaneHeads = (orgId) => heads.current(orgId);
    const installations = new InMemoryInstallationStore();
    const grants = new InMemoryGrantStore();
    const tx = directTx(ORG_A);
    const approved = anInstallation().approved!;
    await installations.create(
      tx,
      anInstallation({
        id: NOTES,
        kind: 'skill',
        item: 'skill:user/notes@1.0.0',
        owner: 'user:u1',
        visibility: 'owner',
        config: { name: 'notes' },
        approved: { ...approved, tools: { notes: approved.descriptorSetDigest } },
      }),
    );
    await grants.put(tx, aGrant({ entries: [anEntry(NOTES)] }), 0);
    const snapshots = new ResolvedSnapshotCache({
      load: policySnapshotLoader({ heads, capabilities: { installations, grants } }),
      heads,
    });
    const pinner = new PolicyRunConfigPinner({
      snapshots,
      engine: new PolicyResolver(),
      effectiveSet: new CanonicalEffectiveSetResolver(),
      releases: releaseLayers(),
      prefix: prefixSources(),
      run: runSources(PARENT_DIMENSIONS),
      parent: PARENT_DIMENSIONS,
    });
    const step = new PolicyPinning({
      mode: 'on',
      pinner,
      metrics: { mismatch: () => undefined },
      logger: LOGGER,
      rolloutFolded: false,
      refresh: (orgId) => snapshots.refresh(orgId),
    });
    const admission = new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      agentAccess: grantingAccess((agentKey) => agentDefinition({ agentKey, ...(owner === undefined ? {} : { owner }) })),
      engineProfiles: new FakeEngineCeilings(),
      modelAccess: testModelAccess({
        policies: new FakeModelAccessPolicies(),
      }),
      executors: profileExecutor,
      policySteps: [step],
      repin: (context) => step.repin(context),
    });
    const result = await admission.submit({
      orgId: ORG_A,
      actorId: 'u3',
      agentKey: 'a7',
      authorization: 'Bearer u3',
      idempotencyKey: randomUUID(),
      deadlineMs: 600_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
    });
    if (result.route !== 'durable') return expect.fail('expected a durable admission');
    const { run } = result.admission;
    return { run, pinner, pinned: (await store.readClaimSubject(ORG_A, run.runId))!.resolved! };
  }

  it("ESR-10: the owner's skill, shared through the agent, is pinned ready for a colleague's run", async () => {
    expect(await admitted('u1')).to.deep.equal([[NOTES, 'ready', null, 'owner']]);
  });

  it("CPD6-36: a child of that run is pinned with the owner its parent's pin names, so the skill is ready in it too", async () => {
    const { run, pinner, pinned } = await root('u1');
    expect(pinned.scope.agentOwner).to.equal('u1');
    const children = new ChildPinning({ mode: 'on', pinner, metrics: { mismatch: () => undefined }, logger: LOGGER });
    const child = await children.pin({ parent: run, parentResolved: pinned, grants: { maxTurns: 2, maxTokens: 1_000 } });
    expect(child!.resolved.scope.agentOwner).to.equal('u1');
    expect(states(child!.resolved)).to.deep.equal([[NOTES, 'ready', null, 'owner']]);
  });

  it("CPD6-36: a parent pinned without an owner names none, and its child's pin is as it was", async () => {
    const { run, pinner, pinned } = await root(undefined);
    expect(pinned.scope).to.deep.equal({ agentKey: 'a7', projectId: null });
    const children = new ChildPinning({ mode: 'on', pinner, metrics: { mismatch: () => undefined }, logger: LOGGER });
    const child = await children.pin({ parent: run, parentResolved: pinned, grants: { maxTurns: 2, maxTokens: 1_000 } });
    expect(child!.resolved.scope).to.deep.equal({ agentKey: 'a7', projectId: null });
    expect(states(child!.resolved)).to.deep.equal([[NOTES, 'unavailable', 'not_visible', null]]);
  });

  it('without the owner (an older authority omits it), or with another owner, it is not visible', async () => {
    expect(await admitted(undefined)).to.deep.equal([[NOTES, 'unavailable', 'not_visible', null]]);
    expect(await admitted('u9')).to.deep.equal([[NOTES, 'unavailable', 'not_visible', null]]);
  });
});
