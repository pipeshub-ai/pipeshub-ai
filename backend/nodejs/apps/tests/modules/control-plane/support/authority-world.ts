/**
 * A real admission under `CONTROL_PLANE_PIN` over the control plane's stores
 * (CPD-10 R3): the agent authority reviewed by the effective-set shadow, the
 * pinner on the snapshot loader, and a claim that resolves the spec, so a
 * case reads the spec a worker would receive and the pin beside it.
 */
import { randomUUID } from 'crypto';
import { policySnapshotLoader } from '../../../../src/modules/agent-harness/adapters/control-plane/policy-snapshot-loader';
import { InMemoryExecutionStore } from '../../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import { ChildPinning } from '../../../../src/modules/agent-harness/application/admission/child-pinning';
import { PolicyPinning } from '../../../../src/modules/agent-harness/application/admission/policy-pinning';
import { ChildRunService } from '../../../../src/modules/agent-harness/application/child-run-service';
import { ControlPlaneReviewedAgentAccess } from '../../../../src/modules/agent-harness/application/control-plane-agent-access';
import { ExecutionService } from '../../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../../src/modules/agent-harness/application/run-admission-service';
import type { AgentRunDefinition } from '../../../../src/modules/agent-harness/domain/agent-definition';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../../src/modules/agent-harness/domain/capabilities';
import type { HarnessRun } from '../../../../src/modules/agent-harness/domain/execution';
import { releaseLayers } from '../../../../src/modules/agent-harness/domain/pin-shadow';
import { PARENT_DIMENSIONS, PolicyResolver } from '../../../../src/modules/agent-harness/domain/policy';
import { pinRunSpec, profileExecutor } from '../../../../src/modules/agent-harness/domain/run-spec';
import type { LabsStagesSource } from '../../../../src/modules/agent-harness/ports/pin-sources';
import {
  CanonicalEffectiveSetResolver,
  EffectiveSetShadow,
  PolicyRunConfigPinner,
  prefixSources,
  ResolvedSnapshotCache,
  runSources,
  type ControlPlanePin,
  type ResolvedRunConfig,
  type ShadowDimension,
} from '../../../../src/modules/control-plane';
import { grantingAccess } from '../../agent-harness/support/agent-definition';
import { testCipher } from '../../agent-harness/support/data-protection';
import { testModelAccess } from '../../agent-harness/support/model-access';
import { HARNESS_ENABLED } from '../../agent-harness/support/rollout';
import { directTx, ORG_A } from './cp-objects';
import { InMemoryHeadRevisions } from './head-revisions-contract';
import { RecordingLog } from './in-memory';
import { InMemoryBindingStore, InMemoryCapabilityLedger, InMemoryGrantStore, InMemoryInstallationStore } from './in-memory-stores';
import { FakeEngineCeilings, FakeModelAccessPolicies } from '../../agent-harness/support/org-policy-fakes';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const LOGGER = { debug: () => undefined, info: () => undefined, warn: () => undefined, error: () => undefined };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
const WORKER = 'w1';
const attemptOf = (runId: string): string => `attempt-${runId}`;

export interface Admitted {
  readonly run: HarnessRun;
  /** `grants.tools[].toolId` of the spec the claim resolved. */
  readonly toolIds: string[];
  readonly mcpInstances: string[];
  readonly skills: string[];
  readonly knowledge: unknown;
  /** The run's stored pin; null when it was admitted without one. */
  readonly pin: ResolvedRunConfig | null;
}

export function authorityWorld(
  mode: ControlPlanePin,
  definition: (agentKey: string) => AgentRunDefinition,
  stages?: LabsStagesSource,
) {
  const store = new InMemoryExecutionStore();
  const payloads = new InMemoryPayloadStore();
  const heads = new InMemoryHeadRevisions();
  store.controlPlaneHeads = (orgId) => heads.current(orgId);
  const installations = new InMemoryInstallationStore();
  const grants = new InMemoryGrantStore();
  const bindings = new InMemoryBindingStore();
  const ledger = new InMemoryCapabilityLedger(installations, grants);
  const counts: ShadowDimension[] = [];
  const snapshots = new ResolvedSnapshotCache({
    load: policySnapshotLoader({
      heads,
      capabilities: { installations, grants, bindings, ledger },
      ...(stages !== undefined ? { stages } : {}),
    }),
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
    mode,
    pinner,
    metrics: { mismatch: () => undefined },
    logger: LOGGER,
    rolloutFolded: false,
    refresh: (orgId) => snapshots.refresh(orgId),
  });
  const access = new ControlPlaneReviewedAgentAccess(
    grantingAccess(definition),
    new EffectiveSetShadow({
      mode,
      snapshots,
      resolver: new CanonicalEffectiveSetResolver(),
      counter: { inc: (dimension) => counts.push(dimension) },
      log: new RecordingLog(),
    }),
  );
  const admission = new RunAdmissionService({
    store,
    rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
    agentAccess: access,
    engineProfiles: new FakeEngineCeilings(),
    modelAccess: testModelAccess({
      policies: new FakeModelAccessPolicies(),
    }),
    executors: profileExecutor,
    policySteps: [step],
    repin: (context) => step.repin(context),
  });
  const execution = new ExecutionService({
    store,
    journal: store.journal,
    journals: new JournalService(payloads, testCipher()),
    capabilities: { current: () => DURABLE },
    resolveSpec: pinRunSpec,
  });
  const children = new ChildRunService({
    runs: store,
    children: store,
    journals: new JournalService(payloads, testCipher()),
    workspaces: null,
    engineAccess: (subject) => testModelAccess().decide(subject),
    pinning: new ChildPinning({ mode, pinner, metrics: { mismatch: () => undefined }, logger: LOGGER }),
  });
  /** A store write as a control-plane write lands it: the org head moves, so the next snapshot reloads. */
  const write = async (work: (tx: ReturnType<typeof directTx>) => Promise<unknown>, orgId = ORG_A): Promise<void> => {
    await work(directTx(orgId));
    heads.revisions.set(orgId, (heads.revisions.get(orgId) ?? 0) + 1);
  };

  async function admit(actorId = 'u3', agentKey = 'a7'): Promise<Admitted> {
    const result = await admission.submit({
      orgId: ORG_A,
      actorId,
      agentKey,
      authorization: `Bearer ${actorId}`,
      idempotencyKey: randomUUID(),
      deadlineMs: 600_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto' } },
    });
    if (result.route !== 'durable') throw new Error('expected a durable admission');
    await access.idle();
    const { run } = result.admission;
    const claimed = await execution.claim({ orgId: ORG_A, runId: run.runId, workerId: WORKER, attemptId: attemptOf(run.runId), leaseDurationMs: 600_000 });
    const spec = claimed.pinned!.spec;
    const extension = spec.extensions!['x.pipeshub'] as {
      mcp: Array<{ instanceId: string }>;
      skills: Array<{ name: string }>;
      knowledge: unknown;
    };
    return {
      run: claimed.run,
      toolIds: spec.grants.tools.map((grant) => grant.toolId),
      mcpInstances: extension.mcp.map((server) => server.instanceId),
      skills: extension.skills.map((skill) => skill.name),
      knowledge: extension.knowledge,
      pin: (await store.readClaimSubject(ORG_A, run.runId))!.resolved ?? null,
    };
  }

  /** A loop child of the claimed `parent`, spawned by its worker through the production child service. */
  async function spawn(parent: HarnessRun, invocationId = 't1.0'): Promise<{ childRunId: string; pin: ResolvedRunConfig | null; spec: unknown }> {
    const spawned = await children.spawn(
      { orgId: ORG_A, runId: parent.runId, workerId: WORKER, attemptId: attemptOf(parent.runId), fence: parent.fence },
      { invocationId, goal: 'summarise the notes', grants: {} },
    );
    const child = (await store.readClaimSubject(ORG_A, spawned.childRunId))!;
    return { childRunId: spawned.childRunId, pin: child.resolved ?? null, spec: child.pinned?.spec };
  }

  return { store, heads, installations, grants, bindings, ledger, snapshots, pinner, counts, write, admit, spawn, execution };
}
