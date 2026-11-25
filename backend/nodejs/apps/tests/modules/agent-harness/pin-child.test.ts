/**
 * CPD5-55, PIN-25 (child): a child spawn on the one decision path. Under
 * `CONTROL_PLANE_PIN=on` a child of a pinned parent is pinned under it, its
 * engine authority is the parent's pin (no second read of the org ceiling),
 * and its own pin is kept on its run; under `shadow` the spawn pins beside
 * today's decision and acts on nothing. The real admission and child
 * services over the in-memory store; the production pinner.
 */
import { expect } from 'chai';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { policySnapshotLoader } from '../../../src/modules/agent-harness/adapters/control-plane/policy-snapshot-loader';
import { ChildPinning } from '../../../src/modules/agent-harness/application/admission/child-pinning';
import { PolicyPinning } from '../../../src/modules/agent-harness/application/admission/policy-pinning';
import { ChildRunService } from '../../../src/modules/agent-harness/application/child-run-service';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { canonicalDigest } from '../../../src/modules/agent-harness/contracts/canonical-json';
import { runSpecSchema } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import type { PolicyLayer } from '../../../src/modules/agent-harness/contracts/generated/core/policy/policy-layer';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { releaseLayers, type PinShadowDimension } from '../../../src/modules/agent-harness/domain/pin-shadow';
import { PARENT_DIMENSIONS, PolicyResolver } from '../../../src/modules/agent-harness/domain/policy';
import { profileExecutor, runSpecResolver } from '../../../src/modules/agent-harness/domain/run-spec';
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
import { testCipher } from './support/data-protection';
import { testModelAccess } from './support/model-access';
import { HARNESS_ENABLED } from './support/rollout';
import { InMemoryHeadRevisions } from '../control-plane/support/head-revisions-contract';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const ORG = 'org-child';
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
const SILENT = { debug: () => undefined, info: () => undefined, warn: () => undefined, error: () => undefined };

describe('CPD-5 a child spawn through the pinner (CPD5-55, PIN-25)', () => {
  let store: InMemoryExecutionStore;
  let payloads: InMemoryPayloadStore;
  let heads: InMemoryHeadRevisions;
  let stored: Map<string, PolicyLayer>;
  let pinned: PinSubject[];
  let counted: PinShadowDimension[];
  let pinner: RunConfigPinner;

  /** A loop parent admitted and claimed with `mode`, ready to spawn. */
  const parentIn = async (mode: ControlPlanePin): Promise<HarnessRun> => {
    const step = new PolicyPinning({ mode, pinner, metrics: { mismatch: () => undefined }, logger: SILENT });
    const outcome = await new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      agentAccess: grantingAccess((agentKey) => agentDefinition({ agentKey, maxChildren: 4 })),
      executors: profileExecutor,
      policySteps: [step],
    }).submit({
      orgId: ORG,
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer t',
      idempotencyKey: `k-${mode}-${Math.random()}`,
      deadlineMs: 600_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto', modelKey: 'model-1', modelName: 'gpt-x', modelProvider: 'openAI' } },
    });
    await step.idle();
    if (outcome.route !== 'durable') return expect.fail('expected a durable run');
    pinned = [];
    return (
      await new ExecutionService({
        store,
        journal: store.journal,
        journals: new JournalService(payloads, testCipher()),
        capabilities: { current: () => DURABLE },
        resolveSpec: runSpecResolver(profileExecutor),
      }).claim({ orgId: ORG, runId: outcome.admission.run.runId, workerId: 'w', attemptId: 'a-1', leaseDurationMs: 60_000 })
    ).run;
  };

  const childPinning = (mode: ControlPlanePin) =>
    new ChildPinning({ mode, pinner, metrics: { mismatch: (dimension) => void counted.push(dimension) }, logger: SILENT });

  /** An engine child of `parent`; no `engineAuthority` is given, so today's path alone would refuse it. */
  const spawn = (parent: HarnessRun, pinning: ChildPinning | null, profile?: Record<string, unknown>, invocationId = 't1.0', engine = true) =>
    new ChildRunService({
      runs: store,
      children: store,
      journals: new JournalService(payloads, testCipher()),
      workspaces: null,
      engineAccess: (subject) => testModelAccess().decide(subject),
      pinning,
    }).spawn(
      { orgId: ORG, runId: parent.runId, workerId: 'w', attemptId: 'a-1', fence: parent.fence },
      {
        invocationId,
        goal: 'build it',
        grants: {},
        ...(engine ? { executor: { kind: 'engine' as const, ...(profile !== undefined ? { profile } : {}) } } : {}),
      },
    );

  beforeEach(() => {
    store = new InMemoryExecutionStore();
    payloads = new InMemoryPayloadStore();
    heads = new InMemoryHeadRevisions();
    store.controlPlaneHeads = (orgId) => heads.current(orgId);
    stored = new Map([
      [`org:${ORG}`, { schemaVersion: 1, scope: { kind: 'org' }, engineProfile: { limits: { maxSteps: 30 } } }],
    ]);
    pinned = [];
    counted = [];
    const inner = new PolicyRunConfigPinner({
      snapshots: new ResolvedSnapshotCache({ load: policySnapshotLoader({ heads, layers: async () => stored }), heads }),
      engine: new PolicyResolver(),
      effectiveSet: { resolve: () => ({ items: [], digest: canonicalDigest([]) }) },
      releases: releaseLayers(),
      prefix: prefixSources(),
      run: runSources(PARENT_DIMENSIONS),
      parent: PARENT_DIMENSIONS,
    });
    pinner = { route: (subject) => inner.route(subject), pin: async (subject, basis) => (pinned.push(subject), inner.pin(subject, basis)) };
  });

  it('CPD5-55: on: the child is pinned under its parent; its engine authority is the parent’s pin; its pin is kept', async () => {
    const parent = await parentIn('on');
    const spawned = await spawn(parent, childPinning('on'));
    expect(pinned.map((subject) => subject.surface)).to.deep.equal(['child']);
    const child = await store.readClaimSubject(ORG, spawned.childRunId);
    const spec = runSpecSchema.parse(child?.pinned?.spec);
    if (spec.executor?.kind !== 'engine') return expect.fail('expected an engine child');
    expect(spec.executor.engine.profile?.limits.maxSteps).to.equal(30);
    const parentPin = (await store.readClaimSubject(ORG, parent.runId))?.resolved;
    expect(child?.resolved?.routing).to.deep.equal(parentPin?.routing);
    expect(child?.resolved?.effectiveConfig.dimensions['engineProfile.limits.maxSteps']?.value).to.equal(30);
    // OI-CPD3-1: the child's profile, and its pin, hold only the account kind it was admitted on.
    const kind = spec.model.policy[0]?.access?.accountKind;
    expect(spec.executor.engine.profile?.accountKinds).to.deep.equal([kind]);
    expect(child?.resolved?.effectiveConfig.dimensions['engineProfile.accountKinds']?.value).to.deep.equal([kind]);
  });

  it('CPD5-55: on: a child asking past its parent’s pin is refused CHILD_GRANT_TOO_LARGE, nothing admitted', async () => {
    const parent = await parentIn('on');
    const refused = await spawn(parent, childPinning('on'), { limits: { maxSteps: 40 } }).catch((e: unknown) => e);
    expect(refused).to.be.instanceOf(HarnessConflictError);
    expect((refused as HarnessConflictError).code).to.equal('CHILD_GRANT_TOO_LARGE');
    expect((await store.get(ORG, parent.runId))?.children?.spawned ?? 0).to.equal(0);
  });

  it('CPD5-55: on: a child never re-reads the org’s layers: its bound is its parent’s pin (a narrowing since is the live layer’s)', async () => {
    const parent = await parentIn('on');
    stored.set(`org:${ORG}`, { schemaVersion: 1, scope: { kind: 'org' }, engineProfile: { limits: { maxSteps: 20 } } });
    heads.commit(ORG);
    const spawned = await spawn(parent, childPinning('on'), { limits: { maxSteps: 25 } });
    const child = await store.readClaimSubject(ORG, spawned.childRunId);
    expect(child?.resolved?.effectiveConfig.dimensions['engineProfile.limits.maxSteps']).to.deep.include({ value: 25, setBy: 'request' });
    const widened = await spawn(parent, childPinning('on'), { limits: { maxSteps: 31 } }, 't1.1').catch((e: unknown) => e);
    expect((widened as HarnessConflictError).code).to.equal('CHILD_GRANT_TOO_LARGE');
  });

  it('on: a parent the control plane did not pin keeps today’s path (no pin, no engine authority here)', async () => {
    const parent = await parentIn('off');
    const refused = await spawn(parent, childPinning('on')).catch((e: unknown) => e);
    expect((refused as HarnessConflictError).code).to.equal('CHILD_GRANT_TOO_LARGE');
    expect(pinned).to.deep.equal([]);
  });

  it('PIN-25 (child): shadow pins the spawn once, as the parent’s actor, and acts on nothing', async () => {
    const parent = await parentIn('shadow');
    const pinning = childPinning('shadow');
    const spawned = await spawn(parent, pinning, undefined, 't1.0', false);
    await pinning.idle();
    // CPD-6: a shadow parent holds no pin, so it is pinned again in memory and the child under it (the chain `on` folds).
    expect(pinned.map((subject) => [subject.surface, subject.actorId, subject.agentKey, subject.parent !== undefined])).to.deep.equal([
      ['web', 'user-1', 'agent-1', false],
      ['child', 'user-1', 'agent-1', true],
    ]);
    expect((await store.readClaimSubject(ORG, spawned.childRunId))?.resolved).to.equal(null);
    expect(counted).to.deep.equal([]);
  });
});
