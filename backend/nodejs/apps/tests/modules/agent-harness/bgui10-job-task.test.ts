/**
 * BGUI-10: a job's Brief showed the whole prompt the chat composed for its engine (the budget sentence written to
 * the model, the notes heading, a `load_skill` call's raw JSON result). The worker now sends the task's parts beside
 * the text on the child's admission; Node keeps them on the child's sealed input and its brief serves them as typed
 * fields. A job admitted before reads its request alone; an erased one reads nothing.
 */
import { expect } from 'chai';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { ChildRunService } from '../../../src/modules/agent-harness/application/child-run-service';
import { JobBriefService } from '../../../src/modules/agent-harness/application/job-briefs';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { RunInputSeal } from '../../../src/modules/agent-harness/application/run-input-seal';
import { InMemoryBudgetLedger } from '../../../src/modules/agent-harness/adapters/in-memory-budget-ledger';
import { jobBriefSchema } from '../../../src/modules/agent-harness/contracts/generated/ext/pipeshub/api/job-brief';
import type { RunSpec } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import { HarnessNotFoundError } from '../../../src/modules/agent-harness/domain/errors';
import type { RunInputSnapshot, RunTaskParts } from '../../../src/modules/agent-harness/domain/execution';
import { jobBrief, jobTitle, legacyEngineGoal, TASK_SPEC_SECTIONS } from '../../../src/modules/agent-harness/domain/job-brief';
import { CHILD_TASKS_FEATURE, NODE_CAPABILITIES } from '../../../src/modules/agent-harness/domain/node-capabilities';
import { workerInput } from '../../../src/modules/agent-harness/domain/worker-input';
import { spawnChildRunSchema } from '../../../src/modules/agent-harness/transport/schemas';
import { agentDefinition } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { contractClock, createContractHarness } from './support/execution-store-contract';
import { inMemoryBackend } from './support/in-memory-backend';
import { HARNESS_ENABLED } from './support/rollout';

const REQUEST = 'Can you prepare a pdf report of ipl 2026 event';
const TASK: RunTaskParts = {
  request: REQUEST,
  notes: 'Prepare a PDF report on the IPL 2026 season: the winner, the top run scorers and wicket takers.',
  earlier: '',
  fileName: null,
  dueAt: '2026-11-11T10:12:00Z',
  maxCostUsd: 5,
  carried: [{ tool: 'load_skill', failed: false, skill: 'pdf' }],
};
// The goal `TaskSpec.render` wrote for that hand-off, as a job admitted before BGUI-10 keeps it.
const COMPOSED =
  `${REQUEST}\n\n` +
  "This task's time and money: deliver your result by 10:12 UTC (about 56 minutes from now), with at most USD 5.00 of model use.\n\n" +
  `Notes from the chat agent that handed this task to you:\n${TASK.notes}\n\n` +
  'Tool calls this chat already made before handing over, with their results. Use them: do not call a tool again ' +
  'with the same arguments; call one only for what they do not cover, or to read in full a result shortened here.\n' +
  '- load_skill({"name": "pdf"}):\n  # PDF skill\nUse reportlab to lay out pages.';

const digest = `sha256:${'a'.repeat(64)}`;
const engineSpec = {
  grants: { tools: [], sandboxProfile: 'agent-code', egress: 'none' },
  executor: { kind: 'engine', engine: { kind: 'claude_code', version: '1', binaryDigest: digest, profileDigest: digest } },
  extensions: { 'x.pipeshub': {} },
} as unknown as RunSpec;
const loopSpec = { grants: { tools: [], sandboxProfile: 'none', egress: 'none' }, extensions: { 'x.pipeshub': {} } } as unknown as RunSpec;
const input = (overrides: Partial<RunInputSnapshot> = {}): RunInputSnapshot => ({
  query: COMPOSED,
  recordIds: [],
  filters: {},
  attachments: [],
  model: { chatMode: 'auto' },
  ...overrides,
});
const job = (childInput: RunInputSnapshot | null) => ({
  runId: 'run-2',
  sessionId: 'ses-1',
  parent: { runId: 'run-1', invocationId: 't1.0', depth: 1 },
  input: childInput,
});

describe('BGUI-10: a job brief serves its task in parts', () => {
  it('BGUI10-N1: the parts kept on the input are the brief\'s task, and the goal stays what the engine was told', () => {
    const brief = jobBrief(job(input({ task: TASK })), engineSpec, null, null);
    expect(jobBriefSchema.safeParse(brief).success, 'the brief is its published contract').to.equal(true);
    expect(brief.task).to.deep.equal(TASK);
    expect(brief.goal).to.equal(COMPOSED);
    expect(jobTitle(brief.goal)).to.equal(REQUEST);
  });

  it('BGUI10-N2: a job admitted before the parts were kept reads its request, not the sections added for its engine', () => {
    const brief = jobBrief(job(input()), engineSpec, null, null);
    expect(brief.task).to.equal(null);
    expect(brief.goal).to.equal(REQUEST);
    expect(jobBriefSchema.safeParse(brief).success).to.equal(true);
    // A goal with none of them, or a sub-agent's (whose own text is not a TaskSpec), is its own.
    expect(legacyEngineGoal('Summarise the board notes.')).to.equal('Summarise the board notes.');
    expect(jobBrief(job(input()), loopSpec, null, null).goal).to.equal(COMPOSED);
    // A task with no request opens with its notes: the carried calls are still cut off.
    expect(legacyEngineGoal(`${TASK.notes}\n\n${TASK_SPEC_SECTIONS[3]} Use them.\n- load_skill({})`)).to.equal(TASK.notes);
  });

  it('BGUI10-N3: an erased job keeps neither its goal nor its parts', () => {
    const brief = jobBrief(job(null), engineSpec, null, null);
    expect(brief).to.deep.include({ goal: null, task: null });
  });

  it("BGUI10-N4: the headings are the ones Python's TaskSpec writes", () => {
    const python = readFileSync(
      path.resolve(__dirname, '../../../../../python/app/harness/assembly/task_spec.py'),
      'utf8',
    ).replace(/"\s*\n\s*"/g, '');
    for (const heading of TASK_SPEC_SECTIONS) {
      const words = heading.replace(/\{name\}|: $/g, '').trim();
      expect(python, heading).to.include(words);
    }
  });

  it('BGUI10-N5: the admission accepts the parts by the contract; a carried call with its arguments is refused', () => {
    const base = { attemptId: 'a-1', fence: 1, invocationId: 't1.0', goal: COMPOSED };
    expect(spawnChildRunSchema.shape.body.safeParse({ ...base, task: TASK }).success).to.equal(true);
    expect(spawnChildRunSchema.shape.body.safeParse(base).success).to.equal(true);
    const leaky = { ...TASK, carried: [{ tool: 'load_skill', failed: false, arguments: { name: 'pdf' } }] };
    expect(spawnChildRunSchema.shape.body.safeParse({ ...base, task: leaky }).success).to.equal(false);
    expect(NODE_CAPABILITIES.features).to.include(CHILD_TASKS_FEATURE);
  });

  it('MCPUSE-N1: a job handed connected sources keeps them on its brief, and its goal is cut before them', () => {
    const sources = [{ name: 'Atlassian Rovo', kind: 'mcp' as const, summary: 'Jira, Confluence, Bitbucket.', tools: 32 }];
    const base = { attemptId: 'a-1', fence: 1, invocationId: 't1.0', goal: COMPOSED };
    expect(spawnChildRunSchema.shape.body.safeParse({ ...base, task: { ...TASK, sources } }).success).to.equal(true);
    const brief = jobBrief(job(input({ task: { ...TASK, sources } })), engineSpec, null, null);
    expect(jobBriefSchema.safeParse(brief).success).to.equal(true);
    expect(brief.task?.sources).to.deep.equal(sources);
    const goal = `${REQUEST}\n\n${TASK_SPEC_SECTIONS[4]}\n- Atlassian Rovo — Jira, Confluence, Bitbucket.`;
    expect(legacyEngineGoal(goal)).to.equal(REQUEST);
    const unknownKind = [{ ...sources[0], kind: 'website' }];
    expect(spawnChildRunSchema.shape.body.safeParse({ ...base, task: { ...TASK, sources: unknownKind } }).success)
      .to.equal(false);
  });

  it('BGUI10-N6: the parts are sealed at rest and never sent to the worker that runs the job', async () => {
    const seal = new RunInputSeal(testCipher());
    const stored = await seal.sealInput('org-1', { runId: 'run-2' }, input({ task: TASK }));
    expect(stored).to.not.have.property('task');
    expect(JSON.stringify(stored)).to.not.include('IPL 2026');
    expect((await seal.openInput('org-1', { runId: 'run-2' }, stored)).task).to.deep.equal(TASK);
    const claimed = workerInput({ input: input({ task: TASK, locale: 'en-US' }) });
    expect(claimed).to.not.have.property('task');
    const plain = input();
    expect(workerInput({ input: plain })).to.equal(plain);
  });

  it('BGUI10-N7: a child admitted with its parts reads them back on its brief; without them, the brief says null', async () => {
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
    const attempt = { orgId: parent.orgId, runId: parent.runId, workerId: 'worker-p', attemptId: 'attempt-worker-p', fence: parent.fence };
    const kept = await children.spawn(attempt, { invocationId: 't1.0', goal: COMPOSED, grants: {}, task: TASK });
    const before = await children.spawn(attempt, { invocationId: 't1.1', goal: 'Look into PAY-1423', grants: {} });
    const service = new JobBriefService({
      runs: {
        streamAccess: async (_scope, runId) => {
          const found = await h.service.get(parent.orgId, runId);
          if (found === null) throw new HarnessNotFoundError();
          return { run: found, access: { audience: 'owner' } as never };
        },
      },
      executions: h.service,
      subjects: backend.executionStore,
    });
    const scope = { orgId: parent.orgId, actorId: parent.actorId, sessionId: parent.sessionId };
    const brief = await service.brief(scope, kept.childRunId);
    expect(jobBriefSchema.safeParse(brief).success).to.equal(true);
    expect(brief.task).to.deep.equal(TASK);
    expect((await service.brief(scope, before.childRunId)).task).to.equal(null);
  });
});
