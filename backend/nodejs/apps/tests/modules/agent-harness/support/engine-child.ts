/**
 * An engine child (WP-111) that a worker holds on a live lease, on any store pair, and an ExecutionService over the
 * same stores for which every engine is now switched off: the case where an admin's switch meets a claim for a run
 * another attempt still holds.
 */
import { expect } from 'chai';
import {
  childEngineAuthority,
  ChildRunService,
} from '../../../../src/modules/agent-harness/application/child-run-service';
import { ExecutionService } from '../../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../../src/modules/agent-harness/application/journal-service';
import { HarnessRun } from '../../../../src/modules/agent-harness/domain/execution';
import { readPinnedSpec } from '../../../../src/modules/agent-harness/domain/run-spec';
import { agentDefinition } from './agent-definition';
import { testCipher } from './data-protection';
import {
  ContractBackend,
  ContractHarness,
  DURABLE_CAPABILITIES,
} from './execution-store-contract';
import { testModelAccess } from './model-access';
import { HARNESS_ENABLED } from './rollout';
import { FakeEngineCeilings } from './org-policy-fakes';

export const ENGINE_HOLDER = 'engine-a';

/** A code_task child pinned to Claude Code, claimed by `ENGINE_HOLDER` for `leaseMs`. */
export async function heldEngineChild(
  h: ContractHarness,
  leaseMs: number,
): Promise<HarnessRun> {
  const children = h.backend.childRuns;
  if (children === undefined) return expect.fail('backend has no child store');
  h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 });
  h.setAgentDefinition((agentKey) =>
    agentDefinition({
      agentKey,
      maxChildren: 2,
      engineProfile: { engines: ['claude_code'] },
    }),
  );
  const { run } = await h.admit({
    agentKey: 'agent-1',
    authorization: 'Bearer user',
    deadlineMs: 600_000,
  });
  const parent = await h.claim(run, 'worker-p', leaseMs);
  const { childRunId } = await new ChildRunService({
    runs: h.backend.executionStore,
    children,
    journals: new JournalService(h.backend.payloads, testCipher()),
    engineAuthority: childEngineAuthority(
      new FakeEngineCeilings(),
    ),
    engineAccess: (subject) => testModelAccess().decide(subject),
  }).spawn(
    {
      orgId: parent.orgId,
      runId: parent.runId,
      workerId: 'worker-p',
      attemptId: parent.attemptId as string,
      fence: parent.fence,
    },
    {
      invocationId: 't1.engine',
      goal: 'research the two repositories',
      grants: {},
      executor: { kind: 'engine' },
    },
  );
  const subject = await h.backend.executionStore.readClaimSubject(
    parent.orgId,
    childRunId,
  );
  const executor = subject?.pinned
    ? readPinnedSpec(subject.pinned.spec).executor
    : undefined;
  expect(executor?.kind, 'the child is pinned to an engine').to.equal('engine');
  const child = await h.service.get(parent.orgId, childRunId);
  if (child === null) return expect.fail('child not admitted');
  return h.claim(child, ENGINE_HOLDER, leaseMs);
}

/** The run service over `backend` once an admin has switched every engine off. */
export function engineOffService(backend: ContractBackend): ExecutionService {
  return new ExecutionService({
    store: backend.executionStore,
    journal: backend.journal,
    journals: new JournalService(backend.payloads, testCipher()),
    history: backend.history ?? null,
    inputs: backend.inputs ?? null,
    capabilities: DURABLE_CAPABILITIES,
    children: backend.childRuns ?? null,
    engineEligibility: { check: async () => 'engine_off' },
  });
}
