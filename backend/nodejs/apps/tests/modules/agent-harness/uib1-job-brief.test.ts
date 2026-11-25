/**
 * UI-B1 G4, ADR-UI-01 D3: a job's brief. The pure reading of a child's pinned spec and input against its parent's,
 * and the service on a child the real admission path pinned (in-memory stores), for its conversation's owner only.
 */
import { expect } from 'chai';
import { ChildRunService } from '../../../src/modules/agent-harness/application/child-run-service';
import { JobBriefService } from '../../../src/modules/agent-harness/application/job-briefs';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { InMemoryBudgetLedger } from '../../../src/modules/agent-harness/adapters/in-memory-budget-ledger';
import type { RunSpec } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import type { HarnessRun, RunInputSnapshot } from '../../../src/modules/agent-harness/domain/execution';
import { briefTool, jobBrief } from '../../../src/modules/agent-harness/domain/job-brief';
import { jobBriefSchema } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/job-brief';
import { agentDefinition } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { contractClock, createContractHarness } from './support/execution-store-contract';
import { inMemoryBackend } from './support/in-memory-backend';
import { HARNESS_ENABLED } from './support/rollout';

const digest = `sha256:${'a'.repeat(64)}`;
const tool = (toolId: string) => ({ toolId, descriptorDigest: digest });
const spec = (overrides: {
  tools?: string[];
  egress?: string;
  sandboxProfile?: 'agent-code' | 'none';
  engine?: string;
  x?: Record<string, unknown>;
}): RunSpec =>
  ({
    grants: {
      tools: (overrides.tools ?? []).map(tool),
      sandboxProfile: overrides.sandboxProfile ?? 'none',
      egress: overrides.egress ?? 'none',
    },
    ...(overrides.engine !== undefined
      ? { executor: { kind: 'engine', engine: { kind: overrides.engine, version: '1', binaryDigest: digest, profileDigest: digest } } }
      : {}),
    extensions: { 'x.pipeshub': overrides.x ?? {} },
  }) as unknown as RunSpec;
const input = (overrides: Partial<RunInputSnapshot> = {}): RunInputSnapshot => ({
  query: 'Fix PAY-1423 and open a pull request',
  recordIds: [],
  filters: {},
  attachments: [],
  model: { chatMode: 'auto' },
  ...overrides,
});
const child = (overrides: Partial<HarnessRun> = {}) => ({
  runId: 'run-2',
  sessionId: 'ses-1',
  parent: { runId: 'run-1', invocationId: 't1.0', depth: 1 },
  input: input({ inputArtifacts: [{ artifactId: 'art-1', version: 2 }] }),
  ...overrides,
});

describe('UI-B1 G4: a job brief (ADR-UI-01 D3)', () => {
  it("UIB1-B1: an engine job: its task, the files and sources it was sent, what stayed with the parent and why, its reach", () => {
    const brief = jobBrief(
      child(),
      spec({
        engine: 'claude_code',
        tools: ['github@inst-1/create_pull_request'],
        egress: 'allowlist:org',
        x: {
          inheritedLabel: {
            complete: true,
            sources: [
              { kind: 'indexed', recordId: 'rec-1' },
              { kind: 'attachment', recordId: 'rec-2' },
              { kind: 'live', connector: 'JIRA', connectorId: 'c-1', externalRecordId: 'PAY-1423' },
            ],
          },
          inheritedGrants: [{ toolId: 'web@searxng/fetch_url', key: digest, sensitivity: 'public' }],
          engineCarry: { runId: 'run-0', checkpoint: {} },
        },
      }),
      spec({
        tools: ['github@inst-1/create_pull_request', 'slack@inst-2/send_message'],
        egress: 'allowlist:org',
        x: { knowledge: { apps: ['app-1'], kb: ['kb-1'], strictScope: false }, skills: [{ name: 'triage', bundleDigest: digest }] },
      }),
      input({ attachments: [{ recordId: 'rec-9' }] }),
    );
    expect(jobBriefSchema.safeParse(brief).success, 'the brief is its published contract').to.equal(true);
    expect(brief.agent).to.deep.equal({ kind: 'engine', engine: 'claude_code' });
    expect(brief.goal).to.equal('Fix PAY-1423 and open a pull request');
    expect(brief.sent.files).to.deep.equal([{ artifactId: 'art-1', version: 2 }]);
    expect(brief.sent.sources).to.deep.equal([
      { kind: 'record', recordId: 'rec-1', attachment: false },
      { kind: 'record', recordId: 'rec-2', attachment: true },
      { kind: 'connector_item', connector: 'JIRA', externalRecordId: 'PAY-1423' },
    ]);
    expect(brief.sent.continuesRunId).to.equal('run-0');
    expect(brief.withheld).to.deep.equal([
      { what: 'conversation', reason: 'task_only' },
      { what: 'credentials', reason: 'outside_sandbox' },
      { what: 'tools', reason: 'not_granted', items: ['send_message'] },
      { what: 'knowledge', reason: 'not_granted', items: ['app-1', 'kb-1'] },
      { what: 'skills', reason: 'not_granted', items: ['triage'] },
    ]);
    expect(brief.reach).to.deep.equal({
      internet: 'allowed_sites',
      sandbox: true,
      tools: [{ toolId: 'github@inst-1/create_pull_request', app: 'github', name: 'create_pull_request' }],
      allowedWithoutAsking: 1,
    });
  });

  it('UIB1-B2: a sub-agent kept off the web, given no files, its goal erased: said so, never guessed', () => {
    const brief = jobBrief(
      child({ input: null }),
      spec({ x: { subAgent: { role: 'researcher', toolNames: [] } } }),
      spec({ egress: 'allowlist:org' }),
      input({ inputArtifacts: [{ artifactId: 'art-1' }] }),
    );
    expect(brief.agent).to.deep.equal({ kind: 'sub_agent', role: 'researcher' });
    expect(brief.goal).to.equal(null);
    expect(brief.withheld.map(({ what }) => what)).to.deep.equal(['conversation', 'internet', 'files']);
    expect(brief.reach).to.deep.include({ internet: 'none', sandbox: false, allowedWithoutAsking: 0 });
    expect(brief.sent).to.deep.include({ sourcesComplete: true, sources: [], continuesRunId: null });
  });

  it("UIB1-B3: a parent whose pin cannot be read: nothing is said withheld that only its grants would show", () => {
    const brief = jobBrief(child(), spec({ engine: 'codex' }), null, null);
    expect(brief.withheld.map(({ what }) => what)).to.deep.equal(['conversation', 'credentials']);
  });

  it('UIB1-B4: a tool id reads as its app and name; an id of another shape is its own name', () => {
    expect(briefTool('jira@inst-42/search_issues')).to.deep.equal({ toolId: 'jira@inst-42/search_issues', app: 'jira', name: 'search_issues' });
    expect(briefTool('run_code')).to.deep.equal({ toolId: 'run_code', app: 'run_code', name: 'run_code' });
  });

  describe('JobBriefService on a child the admission path pinned', () => {
    const family = async () => {
      const clock = contractClock();
      const backend = inMemoryBackend(clock, { budgets: new InMemoryBudgetLedger(() => clock.now()) });
      const h = createContractHarness(backend, clock);
      h.setRollout({ ...HARNESS_ENABLED, harnessMajor: 2 as const });
      h.setAgentDefinition((agentKey) => agentDefinition({ agentKey, maxChildren: 2 }));
      const { run } = await h.admit({ agentKey: 'agent-1', authorization: 'Bearer user' });
      const parent = await h.claim(run, 'worker-p', 5_000);
      const children = new ChildRunService({
        runs: backend.executionStore,
        children: backend.childRuns!,
        journals: new JournalService(backend.payloads, testCipher()),
      });
      const { childRunId } = await children.spawn(
        { orgId: parent.orgId, runId: parent.runId, workerId: 'worker-p', attemptId: 'attempt-worker-p', fence: parent.fence },
        { invocationId: 't1.0', goal: 'Look into PAY-1423', grants: { tools: ['jira@inst-42/search_issues'] } },
      );
      const service = (audience: 'owner' | 'shared') =>
        new JobBriefService({
          runs: {
            streamAccess: async (_scope, runId) => {
              const found = await h.service.get(parent.orgId, runId);
              if (found === null) throw new HarnessNotFoundError();
              return { run: found, access: { audience } as never };
            },
          },
          executions: h.service,
          subjects: backend.executionStore,
        });
      const scope = { orgId: parent.orgId, actorId: parent.actorId, sessionId: parent.sessionId };
      return { parent, childRunId, service, scope };
    };

    it("UIB1-B5: the owner reads the job's brief: its task, its one tool, and the parent's tools it was not handed", async () => {
      const { parent, childRunId, service, scope } = await family();
      const brief = await service('owner').brief(scope, childRunId);
      expect(jobBriefSchema.safeParse(brief).success).to.equal(true);
      expect(brief).to.deep.include({ runId: childRunId, parentRunId: parent.runId, goal: 'Look into PAY-1423' });
      expect(brief.reach.tools.map(({ toolId }) => toolId)).to.deep.equal(['jira@inst-42/search_issues']);
      expect(brief.withheld[0]).to.deep.equal({ what: 'conversation', reason: 'task_only' });
    });

    it('UIB1-B6: a shared reader, or a run that is no job, finds nothing', async () => {
      const { parent, childRunId, service, scope } = await family();
      for (const [audience, runId] of [
        ['shared', childRunId],
        ['owner', parent.runId],
      ] as const) {
        let error: unknown;
        try {
          await service(audience).brief(scope, runId);
        } catch (caught) {
          error = caught;
        }
        expect(error, `${audience} ${runId}`).to.be.instanceOf(HarnessNotFoundError);
      }
    });
  });
});
