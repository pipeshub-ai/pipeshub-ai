/**
 * CPS NO-6: a loop parent's engine child is held to the same layers admission
 * holds an engine run to: the org ceiling and the parent agent's own engine
 * profile layer (R2 F5: the authority read the org ceiling alone). The parent is
 * admitted and claimed in memory; its subject carries the agent layer.
 */
import { expect } from 'chai';
import { InMemoryExecutionStore } from '../../../src/modules/agent-harness/adapters/in-memory-execution-store';
import { InMemoryPayloadStore } from '../../../src/modules/agent-harness/adapters/in-memory-journal-store';
import {
  childEngineAuthority,
  ChildRunService,
} from '../../../src/modules/agent-harness/application/child-run-service';
import { ExecutionService } from '../../../src/modules/agent-harness/application/execution-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunAdmissionService } from '../../../src/modules/agent-harness/application/run-admission-service';
import { runSpecSchema } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { HarnessConflictError } from '../../../src/modules/agent-harness/domain/errors';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { profileExecutor, runSpecResolver } from '../../../src/modules/agent-harness/domain/run-spec';
import { agentDefinition, grantingAccess } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { testModelAccess } from './support/model-access';
import { HARNESS_ENABLED } from './support/rollout';
import { FakeEngineCeilings } from './support/org-policy-fakes';

const V2 = { ...HARNESS_ENABLED, harnessMajor: 2 as const };
const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);
const AGENT_LAYER = { limits: { maxSteps: 10 } };

describe('CPS NO-6: a loop parent grants an engine child no more than its agent allows', () => {
  let store: InMemoryExecutionStore;
  let settings: FakeEngineCeilings;
  let payloads: InMemoryPayloadStore;
  let parent: HarnessRun;

  beforeEach(async () => {
    const now = 1_000;
    store = new InMemoryExecutionStore({ now: () => now });
    payloads = new InMemoryPayloadStore();
    settings = new FakeEngineCeilings();
    await settings.write('org-1', { limits: { maxSteps: 30 } }, 'p_admin');
    const outcome = await new RunAdmissionService({
      store,
      rollout: { decide: async () => V2, decideForOrg: async () => ({ ...V2, harnessOn: true, resumeInFlightRuns: true }) },
      clock: { now: () => now },
      agentAccess: grantingAccess((agentKey) => agentDefinition({ agentKey, maxChildren: 2 })),
      executors: profileExecutor,
    }).submit({
      orgId: 'org-1',
      actorId: 'user-1',
      agentKey: 'agent-1',
      authorization: 'Bearer t',
      idempotencyKey: 'k-parent',
      deadlineMs: 600_000,
      input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'auto', modelKey: 'model-1', modelName: 'gpt-x', modelProvider: 'openAI' } },
    });
    if (outcome.route !== 'durable') expect.fail('expected a durable run');
    const claimed = await new ExecutionService({
      store,
      journal: store.journal,
      journals: new JournalService(payloads, testCipher()),
      capabilities: { current: () => DURABLE },
      resolveSpec: runSpecResolver(profileExecutor),
    }).claim({
      orgId: 'org-1',
      runId: outcome.admission.run.runId,
      workerId: 'w',
      attemptId: 'a-1',
      leaseDurationMs: 60_000,
    });
    parent = claimed.run;
  });

  /** The parent's subject as admission fixed it, its agent carrying an engine layer. */
  const withAgentLayer = {
    get: (orgId: string, runId: string) => store.get(orgId, runId),
    readClaimSubject: async (orgId: string, runId: string) => {
      const subject = await store.readClaimSubject(orgId, runId);
      return subject === null || subject.agentDefinition === null
        ? subject
        : { ...subject, agentDefinition: { ...subject.agentDefinition, engineProfile: AGENT_LAYER } };
    },
  };

  const spawn = (profile?: Record<string, unknown>) =>
    new ChildRunService({
      runs: withAgentLayer,
      children: store,
      journals: new JournalService(payloads, testCipher()),
      workspaces: null,
      engineAuthority: childEngineAuthority(settings),
      engineAccess: (subject) => testModelAccess().decide(subject),
    }).spawn(
      { orgId: 'org-1', runId: parent.runId, workerId: 'w', attemptId: 'a-1', fence: parent.fence },
      {
        invocationId: profile === undefined ? 't1.0' : 't1.1',
        goal: 'build it',
        grants: {},
        executor: { kind: 'engine', ...(profile !== undefined ? { profile } : {}) },
      },
    );

  it("the authority narrows the org ceiling by the agent's layer", async () => {
    const authority = childEngineAuthority(settings);
    expect((await authority({ orgId: 'org-1' }))?.limits.maxSteps).to.equal(30);
    expect((await authority({ orgId: 'org-1', agent: AGENT_LAYER }))?.limits.maxSteps).to.equal(10);
    expect(await authority({ orgId: 'org-1', agent: { limits: { maxSteps: 40 } } })).to.equal(null);
    expect(await childEngineAuthority(null)({ orgId: 'org-1', agent: AGENT_LAYER })).to.equal(null);
  });

  it("a child asking past the agent's layer is refused naming executor", async () => {
    let refused: unknown = null;
    try {
      await spawn({ limits: { maxSteps: 20 } });
    } catch (error) {
      refused = error;
    }
    expect(refused).to.be.instanceOf(HarnessConflictError);
    expect((refused as HarnessConflictError).code).to.equal('CHILD_GRANT_TOO_LARGE');
  });

  it("a child asking nothing is pinned with the agent's layer applied", async () => {
    const spawned = await spawn();
    const child = await store.readClaimSubject('org-1', spawned.childRunId);
    const spec = runSpecSchema.parse(child?.pinned?.spec);
    if (spec.executor?.kind !== 'engine') return expect.fail('expected an engine child');
    expect(spec.executor.engine.profile?.limits.maxSteps).to.equal(10);
  });
});
