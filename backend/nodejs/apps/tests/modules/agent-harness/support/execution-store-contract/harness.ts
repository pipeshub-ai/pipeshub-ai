/**
 * Behaviour every ExecutionStore + RunAdmissionStore pair must share. Runs the
 * same cases against the in-memory adapter (unit suite) and MongoDB
 * (`execution-store.mongo.itest.ts`, real replica set), so the two cannot drift.
 */
import type { AdmitRunCommand } from '../../../../../src/modules/agent-harness/domain/admission';
import type { PolicyContext } from '../../../../../src/modules/agent-harness/application/admission/context';
import type { AdmissionStep } from '../../../../../src/modules/agent-harness/application/admission/pipeline';
import type { RunHistoryContent } from '../../../../../src/modules/agent-harness/application/run-history-content';
import type { RunInputSeal } from '../../../../../src/modules/agent-harness/application/run-input-seal';
import type { Heads } from '../../../../../src/modules/control-plane';
import { testModelAccess } from '../model-access';
import { expect } from 'chai';
import { BudgetLedgerStore } from '../../../../../src/modules/agent-harness/ports/budget-ledger';
import { randomUUID } from 'crypto';
import { Types } from 'mongoose';
import { HarnessConflictError } from '../../../../../src/modules/agent-harness/domain/errors';

import { RunAdmissionResult, RunAdmissionStore } from '../../../../../src/modules/agent-harness/domain/admission';
import { HarnessRolloutDecision } from '../../../../../src/modules/agent-harness/domain/rollout';

import { HarnessRun, StoreClock } from '../../../../../src/modules/agent-harness/domain/execution';
import { AdmissionQuotas } from '../../../../../src/modules/agent-harness/domain/admission-quotas';
import { ExecutionStore } from '../../../../../src/modules/agent-harness/ports/execution-store';
import { ChildRunStore } from '../../../../../src/modules/agent-harness/ports/child-runs';
import { ExecutionService, JournalReader } from '../../../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../../../src/modules/agent-harness/application/journal-service';

import { PayloadStore } from '../../../../../src/modules/agent-harness/ports/payload-store';
import { RunAdmissionService, SubmitDurableRun, SubmitOutcome } from '../../../../../src/modules/agent-harness/application/run-admission-service';
import { fixedRollout, HARNESS_ENABLED } from '../rollout';

import { RecordReadAccess } from '../../../../../src/modules/agent-harness/ports/record-access';

import { agentDefinition as agentDefinitionFixture, grantingAccess } from '../agent-definition';
import { AgentRunDefinition } from '../../../../../src/modules/agent-harness/domain/agent-definition';
import { AgentAccessPolicy } from '../../../../../src/modules/agent-harness/ports/agent-access';

import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../../../src/modules/agent-harness/domain/capabilities';
import { CapabilitySource } from '../../../../../src/modules/agent-harness/ports/capability-probe';
import { InlineClaimHint } from '../../../../../src/modules/agent-harness/ports/inline-claim';

import { testCipher } from '../data-protection';

import { WorkerFeatureAvailability } from '../../../../../src/modules/agent-harness/application/worker-feature-availability';
import type { WorkerFeature } from '../../../../../src/modules/agent-harness/domain/worker-compat';

import { FakeEngineCeilings } from '../org-policy-fakes';

/** A deployment whose journal majority-acknowledges; nothing else proved. */
export const DURABLE_CAPABILITIES: CapabilitySource = {
  current: () =>
    capabilitySnapshot(
      { ...LEAST_CAPABILITIES, 'journal.durable': true },
      START,
    ),
};

export interface DispatchedCommand {
  commandId: string;
  kind: string;
}

/**
 * The conversation side of the transcript projection (P1.8), as the store
 * under test writes it: what landed, and a way to make the next write fail.
 */
export interface TranscriptProbe {
  /** Messages the run's session holds after the admission's user turn. */
  written(run: HarnessRun): Promise<Array<Record<string, unknown>>>;
  failNextWrite(): void;
  /** The person deletes the run's conversation while it runs (QA21-F4). */
  deleteConversation(run: HarnessRun): Promise<void>;
}

export interface ContractBackend {
  executionStore: ExecutionStore;
  transcripts?: TranscriptProbe;
  /** The journal `executionStore` appends to. */
  journal: JournalReader;
  payloads: PayloadStore;
  admissionStore: RunAdmissionStore;
  /** Dispatch commands recorded for a run, oldest first. */
  dispatches(orgId: string, runId: string): Promise<DispatchedCommand[]>;
  /** Removes a claim-checked payload (restore skew, GC race). */
  deletePayload(orgId: string, ref: string): Promise<void>;
  /** Clears shared state; the sweeper is global, so cases must not see each other. */
  reset?(): Promise<void>;
  /** Child admission and lookups (P6.1). */
  childRuns?: ChildRunStore;
  /** The ledger the child store opens children's accounts in (P6.5). */
  budgets?: BudgetLedgerStore;
  /** Where `admissionStore` keeps a run's history by reference (QA22-F2); absent: inline. */
  history?: RunHistoryContent;
  /** Where the stores seal a run's query (LCFIX); absent: plaintext. */
  inputs?: RunInputSeal;
  /** The revisions the stores hold a pin to (CPD-5, CPD-10), as their transaction reads them, and what moves each. */
  controlPlane?: ControlPlaneRevisions;
}

/** The backend's revisions; a backend whose stores admit pins names them. */
export function revisionsOf(backend: ContractBackend): ControlPlaneRevisions {
  if (backend.controlPlane === undefined) {
    throw new Error('The backend admits pins and names no control-plane revisions');
  }
  return backend.controlPlane;
}

export interface ControlPlaneRevisions {
  heads(orgId: string): Promise<Heads>;
  /** A committed control-plane write of the org. */
  commit(orgId: string): Promise<void>;
  /** A committed write of a document every org reads (an AI-model save, by whichever org). */
  moveDeployment(): Promise<void>;
}

/** Builds the adapter pair under test on the contract's clock (with quotas for the P1.14 cases). */
export type ContractBackendFactory = (
  clock: StoreClock,
  options?: { quotas?: AdmissionQuotas },
) => ContractBackend;

/**
 * The history `run` was admitted with, as its claim gives it to the worker
 * (QA22-F2), read without claiming the run.
 */
export async function admittedHistory(
  backend: ContractBackend,
  run: Pick<HarnessRun, 'orgId' | 'sessionId' | 'runId'>,
): Promise<Array<Record<string, unknown>>> {
  const subject = await backend.executionStore.readClaimSubject(
    run.orgId,
    run.runId,
  );
  const ref = subject?.history ?? null;
  if (ref === null) return subject?.run.input?.previousConversations ?? [];
  if (backend.history === undefined) {
    throw new Error('The backend names a stored history and reads none');
  }
  return backend.history.read(run, ref);
}

export interface ContractHooks {
  before?: () => Promise<void>;
  after?: () => Promise<void>;
}

export interface Tenant {
  orgId: string;
  actorId: string;
}

export interface ContractHarness {
  service: ExecutionService;
  backend: ContractBackend;
  tenant: Tenant;
  setNow(value: number): void;
  newTenant(): Tenant;
  /** What the rollout policy answers for every later submission. */
  setRollout(decision: HarnessRolloutDecision): void;
  /** Later admissions are inline and hinted here; undefined: dispatched. */
  setInlineHint(hint: InlineClaimHint | undefined): void;
  /** The record authorization later steering references are checked by (C-21). */
  setRecordAccess(access: RecordReadAccess | undefined): void;
  /** What the agent grants later admissions (the agent authority's answer). */
  setAgentDefinition(
    definition: (agentKey: string) => AgentRunDefinition,
  ): void;
  /** Later admissions ask this agent authority instead of the granting stand-in. */
  setAgentAccess(policy: AgentAccessPolicy): void;
  submit(
    overrides?: Partial<SubmitDurableRun>,
    runId?: string,
  ): Promise<SubmitOutcome>;
  /** A queued next run decided again as it drains (`RunAdmissionService.readmit`), with the same policy steps. */
  readmit(command: AdmitRunCommand): Promise<AdmitRunCommand>;
  /** Submits and requires the durable route. */
  admit(
    overrides?: Partial<SubmitDurableRun>,
    runId?: string,
  ): Promise<RunAdmissionResult>;
  claim(
    run: HarnessRun,
    workerId?: string,
    leaseDurationMs?: number,
  ): Promise<HarnessRun>;
  /** The store's clock (DB time). */
  clock: ContractClock;
  /** What later admissions read as the deployment's capabilities; undefined: none. */
  setCapabilities(source: CapabilitySource | undefined): void;
  /** Which features live workers advertise, for admission and the service (DUR2). */
  /** `guarded`: the features the sweeper releases stranded runs of (the deployment's `guardedWorkerFeatures`). */
  setWorkerFeatures(availability: WorkerFeatureAvailability, guarded?: readonly WorkerFeature[]): void;
  /** Steps later admissions run after the policy steps (the control plane's pinning); none by default. */
  setPolicySteps(steps: readonly AdmissionStep<PolicyContext>[]): void;
}

export const START = 1_000;
export const DEADLINE_MS = 9_000;

/** Plays the database clock for both the store and the admitting caller. */
export interface ContractClock extends StoreClock {
  set(value: number): void;
}

export const contractClock = (): ContractClock => {
  let now = START;
  return {
    now: () => now,
    set: (value: number) => {
      now = value;
    },
  };
};

const newTenant = (): Tenant => ({
  orgId: new Types.ObjectId().toString(),
  actorId: new Types.ObjectId().toString(),
});

/** `backend` must read `clock` as its store time. */
/**
 * Submissions of every contract harness still running. Mocha moves on from a case that timed out while its
 * admissions run on, and the sweeper is global: one that lands after the next case's reset is counted there.
 */
const inFlight = new Set<Promise<unknown>>();

/** Every submission a contract harness started has ended, the late ones of a case that timed out included. */
export async function contractSubmissionsIdle(): Promise<void> {
  while (inFlight.size > 0) await Promise.allSettled([...inFlight]);
}

/** The backend a case starts on: the earlier cases' work ended, then its shared state cleared. */
export async function freshContractBackend(
  createBackend: ContractBackendFactory,
  clock: StoreClock,
  options?: { quotas?: AdmissionQuotas },
): Promise<ContractBackend> {
  await contractSubmissionsIdle();
  const backend = createBackend(clock, options);
  await backend.reset?.();
  return backend;
}

export function createContractHarness(
  backend: ContractBackend,
  clock: ContractClock,
): ContractHarness {
  const service = new ExecutionService({
    store: backend.executionStore,
    journal: backend.journal,
    journals: new JournalService(backend.payloads, testCipher()),
    history: backend.history ?? null,
    inputs: backend.inputs ?? null,
    capabilities: DURABLE_CAPABILITIES,
    children: backend.childRuns ?? null,
  });
  const tenant = newTenant();
  let rollout = HARNESS_ENABLED;
  let inline: InlineClaimHint | undefined;
  let references: RecordReadAccess | undefined;
  let capabilities: CapabilitySource | undefined;
  let workerFeatures: WorkerFeatureAvailability | undefined;
  let policySteps: readonly AdmissionStep<PolicyContext>[] = [];
  let definition = (agentKey: string): AgentRunDefinition =>
    agentDefinitionFixture({ agentKey });
  let agentAccess: AgentAccessPolicy | undefined;
  const engineProfiles = new FakeEngineCeilings();
  const admissionService = (runId?: string): RunAdmissionService =>
    new RunAdmissionService({
      store: backend.admissionStore,
      rollout: fixedRollout(() => rollout),
      clock,
      newId: () => runId ?? randomUUID(),
      agentAccess:
        agentAccess ?? grantingAccess((agentKey) => definition(agentKey)),
      inline,
      references,
      engineProfiles,
      modelAccess: testModelAccess(),
      ...(capabilities !== undefined ? { capabilities } : {}),
      ...(workerFeatures !== undefined ? { workerFeatures } : {}),
      ...(policySteps.length > 0 ? { policySteps } : {}),
    });
  const submit = (
    overrides: Partial<SubmitDurableRun> = {},
    runId?: string,
  ): Promise<SubmitOutcome> => {
    const submitted = submitNow(overrides, runId);
    inFlight.add(submitted);
    void submitted.then(
      () => inFlight.delete(submitted),
      () => inFlight.delete(submitted),
    );
    return submitted;
  };
  const submitNow = (
    overrides: Partial<SubmitDurableRun>,
    runId: string | undefined,
  ): Promise<SubmitOutcome> =>
    admissionService(runId).submit({
      ...tenant,
      idempotencyKey: randomUUID(),
      deadlineMs: DEADLINE_MS,
      input: {
        query: 'contract query',
        recordIds: [],
        filters: {},
        attachments: [],
        model: { chatMode: 'auto' },
      },
      ...overrides,
    });
  return {
    service,
    backend,
    tenant,
    setNow: (value: number) => clock.set(value),
    newTenant,
    setRollout: (decision) => {
      rollout = decision;
    },
    setAgentDefinition: (next) => {
      definition = next;
    },
    setAgentAccess: (policy) => {
      agentAccess = policy;
    },
    setInlineHint: (hint) => {
      inline = hint;
    },
    setRecordAccess: (access) => {
      references = access;
    },
    clock,
    setCapabilities: (source) => {
      capabilities = source;
    },
    setWorkerFeatures: (availability, guarded) => {
      workerFeatures = availability;
      service.watchWorkerFeatures(availability, guarded);
    },
    setPolicySteps: (steps) => {
      policySteps = steps;
    },
    submit,
    readmit: (command) => admissionService().readmit(command, 'Bearer contract'),
    admit: async (overrides, runId) => {
      const outcome = await submit(overrides, runId);
      if (outcome.route !== 'durable') {
        expect.fail(
          `Expected a durable admission, got ${outcome.rollout.reason}`,
        );
      }
      return outcome.admission;
    },
    claim: async (run, workerId = 'worker-a', leaseDurationMs = 1_000) =>
      (
        await service.claim({
          orgId: run.orgId,
          runId: run.runId,
          workerId,
          attemptId: `attempt-${workerId}`,
          leaseDurationMs,
        })
      ).run,
  };
}

export const conflictCode = async (work: Promise<unknown>): Promise<string> => {
  try {
    await work;
  } catch (error) {
    expect(error).to.be.instanceOf(HarnessConflictError);
    return (error as HarnessConflictError).code;
  }
  expect.fail('Expected a HarnessConflictError');
};
