/**
 * R31HOT2, ADR-OWD-07 D-LAZY-5: a child inherits its parent's first-use pins of the MCP servers the parent has listed.
 *
 * Before, a code_task job of the default chat listed Atlassian Rovo again at its own first use: a second listing round
 * trip on the job's critical path, and a job that could see other tools than the ones its parent saw and approved
 * against. The parent's worker now carries its pins (`mcpPins`) into the spawn; Node pins them on the child narrowed
 * to what the child holds (`x.pipeshub.mcp[].pins`), and the child then needs a worker that reads them. The vector
 * table cases CM-9..CM-15 hold the narrowing; this file holds the wiring.
 */
import { expect } from 'chai';
import { readFileSync } from 'fs';
import { join } from 'path';
import { ChildRunService } from '../../../src/modules/agent-harness/application/child-run-service';
import { JournalService } from '../../../src/modules/agent-harness/application/journal-service';
import { InMemoryBudgetLedger } from '../../../src/modules/agent-harness/adapters/in-memory-budget-ledger';
import { RunSpec } from '../../../src/modules/agent-harness/contracts/generated/core/run-spec';
import {
  childFeatures,
  childGrants,
  childRunSpec,
  McpFirstUsePins,
  pipeshub,
} from '../../../src/modules/agent-harness/domain/child-runs';
import {
  engineExecutor,
  RELEASE_ENGINE_PROFILES,
} from '../../../src/modules/agent-harness/domain/engine-profiles';
import { CHILD_MCP_PINS_FEATURE, NODE_CAPABILITIES } from '../../../src/modules/agent-harness/domain/node-capabilities';
import { MCP_CARRIED_PINS_FEATURE, PinnedRunSpec } from '../../../src/modules/agent-harness/domain/run-spec';
import { KNOWN_WORKER_FEATURES, LEGACY_RUN_FEATURES } from '../../../src/modules/agent-harness/domain/worker-compat';
import { spawnChildRunSchema } from '../../../src/modules/agent-harness/transport/schemas';
import { agentDefinition } from './support/agent-definition';
import { testCipher } from './support/data-protection';
import { contractClock, createContractHarness } from './support/execution-store-contract';
import { inMemoryBackend } from './support/in-memory-backend';
import { HARNESS_ENABLED } from './support/rollout';

const table = JSON.parse(
  readFileSync(join(__dirname, '../../../../../../contracts/harness/vectors/child-mcp.json'), 'utf8'),
) as { parentSpec: RunSpec };

const CREATE = `sha256:${'1'.repeat(64)}`;
const GET = `sha256:${'2'.repeat(64)}`;
const LIST_PRS = `sha256:${'3'.repeat(64)}`;
const PINS: McpFirstUsePins = { 'mcp-rovo': { createJiraIssue: CREATE, getJiraIssue: GET } };

const pinnedOf = (spec: RunSpec): PinnedRunSpec => ({
  spec: structuredClone(spec),
  specDigest: `sha256:${'0'.repeat(64)}`,
  capabilities: {} as PinnedRunSpec['capabilities'],
  capabilitiesDigest: `sha256:${'0'.repeat(64)}`,
});
const childOf = (parent: RunSpec, tools: string[] | undefined, pins?: McpFirstUsePins, maxChildren = 0) => {
  const decision = childGrants(parent, tools === undefined ? {} : { tools }, 1, pins);
  if (decision.grants === null) expect.fail(`expected a grant, got ${JSON.stringify(decision.refusal)}`);
  return childRunSpec(
    pinnedOf(parent), { ...decision.grants, maxChildren }, undefined, undefined,
    engineExecutor(RELEASE_ENGINE_PROFILES[0]!), undefined, undefined, undefined, undefined, undefined, undefined,
    undefined, undefined, pins,
  );
};
const rovo = (spec: RunSpec) => pipeshub(spec)?.mcp.find((server) => server.instanceId === 'mcp-rovo');

describe('R31HOT2: a child inherits its parent\'s first-use MCP pins (ADR-OWD-07 D-LAZY-5)', () => {
  it('HOT2-N1: a code_task job of a parent that listed the server is pinned to that listing and needs a worker that reads it', () => {
    const child = childOf(table.parentSpec, undefined, PINS);
    expect(rovo(child.spec)).to.deep.include({ tools: 'at_first_use', pins: { createJiraIssue: CREATE, getJiraIssue: GET } });
    expect(childFeatures(child)).to.include(MCP_CARRIED_PINS_FEATURE);
    expect(KNOWN_WORKER_FEATURES).to.include(MCP_CARRIED_PINS_FEATURE);
    expect(LEGACY_RUN_FEATURES).to.not.include(MCP_CARRIED_PINS_FEATURE);
    expect(NODE_CAPABILITIES.features).to.include(CHILD_MCP_PINS_FEATURE);
  });

  it('HOT2-N2: a parent that has not listed the server carries no pin: the child lists at its own first use, as before', () => {
    const child = childOf(table.parentSpec, undefined);
    expect(rovo(child.spec)).to.not.have.property('pins');
    expect(childFeatures(child)).to.not.include(MCP_CARRIED_PINS_FEATURE);
  });

  it('HOT2-N3: the pin is narrowed with the grant, and a grandchild holds only what its parent was pinned to', () => {
    const narrowed = childOf(table.parentSpec, ['mcp@mcp-rovo/getJiraIssue'], PINS, 1).spec;
    expect(rovo(narrowed)?.pins).to.deep.equal({ getJiraIssue: GET });
    // The narrowed child's worker carries the pin it was given; a wider one claimed by a worker changes nothing.
    expect(childGrants(narrowed, { tools: ['mcp@mcp-rovo/createJiraIssue'] }, 2, PINS).refusal?.items).to.deep.equal([
      'mcp@mcp-rovo/createJiraIssue',
    ]);
    const whole = childOf(table.parentSpec, undefined, PINS, 1).spec;
    const grandchild = childOf(whole, undefined).spec;
    expect(rovo(grandchild)?.pins, 'the spec pin follows with no worker report').to.deep.equal(PINS['mcp-rovo']);
    expect(childGrants(whole, { tools: ['mcp@mcp-rovo/deleteJiraIssue'] }, 2).refusal?.items).to.deep.equal([
      'mcp@mcp-rovo/deleteJiraIssue',
    ]);
  });

  it('HOT2-N4: the admission body takes well-formed pins only', () => {
    const base = { attemptId: 'a', fence: 1, invocationId: 'i', goal: 'g' };
    const parse = (mcpPins: unknown) => spawnChildRunSchema.shape.body.safeParse({ ...base, mcpPins }).success;
    expect(parse(PINS)).to.equal(true);
    expect(parse({})).to.equal(true);
    expect(parse({ 'mcp-rovo': { createJiraIssue: 'sha256:short' } })).to.equal(false);
    expect(parse({ 'bad id/with slash': { createJiraIssue: CREATE } })).to.equal(false);
    expect(parse({ 'mcp-rovo': Object.fromEntries(Array.from({ length: 501 }, (_, i) => [`t${i}`, CREATE])) })).to.equal(false);
  });

  it('HOT2-N5: a spawn carrying the parent\'s pins pins the child to them; a replay with other pins is the same child', async () => {
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
    const pins = { 'mcp-9': { list_prs: LIST_PRS, merge_pr: CREATE } };
    const pinned = await children.spawn(attempt, { invocationId: 't1.0', goal: 'Review the PRs', grants: {}, mcpPins: pins });
    const plain = await children.spawn(attempt, { invocationId: 't1.1', goal: 'Review the PRs', grants: {} });
    const specOf = async (runId: string) => (await backend.executionStore.readClaimSubject(parent.orgId, runId))!.pinned!.spec;
    const github = async (runId: string) => pipeshub(await specOf(runId))?.mcp.find((s) => s.instanceId === 'mcp-9');
    // mcp-9 grants list_prs alone: the pin keeps that tool, never merge_pr.
    expect((await github(pinned.childRunId))?.pins).to.deep.equal({ list_prs: LIST_PRS });
    expect(await github(plain.childRunId)).to.not.have.property('pins');
    const replay = await children.spawn(attempt, { invocationId: 't1.0', goal: 'Review the PRs', grants: {}, mcpPins: {} });
    expect(replay).to.deep.equal({ childRunId: pinned.childRunId, created: false });
  });
});
