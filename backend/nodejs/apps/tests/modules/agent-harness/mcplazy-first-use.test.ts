/**
 * R26-LAZY stage (c), ADR-OWD-07 D-LAZY-2: admission lists no MCP server for a worker that lists them at first use.
 *
 * Node asks the agent authority for a definition that lists nothing (`mcpFirstUse`) only while a live worker advertises
 * `mcp_first_use`; a server the definition grants whole is pinned as `tools: at_first_use`, with no tool grant, and the
 * run then requires the feature, so an older worker of a rolling deploy never receives the field.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import sinon from 'sinon';
import { AIServiceCommand } from '../../../src/libs/commands/ai_service/ai.service.command';
import { HarnessAgentAccessPolicy } from '../../../src/modules/enterprise_search/adapters/harness-agent-access';
import { AgentAuthorizer, unlistedMcpNotices } from '../../../src/modules/agent-harness/application/admission/policy-steps';
import { policyContext, SubmitDurableRun } from '../../../src/modules/agent-harness/application/admission/context';
import { capabilitySnapshot, LEAST_CAPABILITIES } from '../../../src/modules/agent-harness/domain/capabilities';
import { HarnessRun } from '../../../src/modules/agent-harness/domain/execution';
import { ASSISTANT_AGENT_DEFINITION_KEY } from '../../../src/modules/agent-harness/domain/rollout';
import { MCP_FIRST_USE_FEATURE, resolveRunSpec } from '../../../src/modules/agent-harness/domain/run-spec';
import { admissionRequirements } from '../../../src/modules/agent-harness/domain/worker-compat';
import { agentDefinition } from './support/agent-definition';

const DURABLE = capabilitySnapshot({ ...LEAST_CAPABILITIES, 'journal.durable': true }, 1);

const whole = agentDefinition({
  agentKey: ASSISTANT_AGENT_DEFINITION_KEY,
  definitionVersion: 1,
  models: [],
  defaultReasoningEffort: null,
  skills: [],
  webSearch: null,
  mcpServers: [{ instanceId: 'mcp-rovo', name: 'Atlassian Rovo', typeId: 'rovo', tools: [], atFirstUse: true }],
});

const request: SubmitDurableRun = {
  orgId: 'org-1',
  actorId: 'user-1',
  idempotencyKey: 'k',
  deadlineMs: 60_000,
  authorization: 'Bearer caller',
  input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'agent' } },
};

const run = (features: string[]): HarnessRun =>
  ({
    runId: 'run-1', orgId: 'org-1', sessionId: 'ses-1', actorId: 'user-1', userMessageKey: 'm-1',
    idempotencyKey: 'k-1', payloadHash: 'h', harnessMajor: 2, profile: 'dispatched', status: 'queued',
    outcome: null, revision: 0, fence: 0, attemptId: null, workerId: null, leaseExpiresAt: null,
    absoluteDeadlineAt: Date.UTC(2026, 10, 11, 12), checkpoint: null,
    input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'agent' } },
    createdAt: Date.UTC(2026, 10, 11, 11), updatedAt: Date.UTC(2026, 10, 11, 11),
    requires: { foldVersion: 1, features: features as never },
  }) as HarnessRun;

describe('R26-LAZY: admission lists no MCP server for a worker that lists at first use (D-LAZY-2)', () => {
  afterEach(() => sinon.restore());

  for (const live of [true, false]) {
    it(`asks for a definition that lists nothing only while a worker advertises it (live: ${String(live)})`, async () => {
      const asked: Array<Record<string, unknown>> = [];
      const authorizer = new AgentAuthorizer(
        { authorizeRun: async (r: Record<string, unknown>) => (asked.push(r), whole) } as never,
        { info: () => undefined } as never,
        { live: async (feature: string) => feature === MCP_FIRST_USE_FEATURE && live } as never,
      );
      await authorizer.run(policyContext(request, {}));
      expect(asked).to.have.length(1);
      expect(asked[0]!['mcpFirstUse']).to.equal(live ? true : undefined);
    });
  }

  it('the authority is asked with mcpFirstUse only when admission sets it', async () => {
    const execute = sinon
      .stub(AIServiceCommand.prototype, 'execute')
      .resolves({ statusCode: 200, data: { definition: agentDefinition({ agentKey: 'agent-1' }) }, msg: '' } as never);
    const policy = new HarnessAgentAccessPolicy(() => 'http://q');
    const base = { orgId: 'org-1', actorId: 'user-1', agentKey: 'agent-1', authorization: 'Bearer t' };
    await policy.authorizeRun(base);
    await policy.authorizeRun({ ...base, agentVersion: 3, mcpFirstUse: true });
    const uris = execute.getCalls().map((call) => (call.thisValue as unknown as { uri: string }).uri);
    expect(uris).to.deep.equal([
      'http://q/api/v1/agent/agent-1/run-definition',
      'http://q/api/v1/agent/agent-1/run-definition?version=3&mcpFirstUse=true',
    ]);
  });

  it('a server granted whole is pinned at_first_use, with no tool grant, and is no unlisted server', () => {
    const spec = resolveRunSpec({ run: run([MCP_FIRST_USE_FEATURE]), agentDefinition: whole, via: 'session' }, DURABLE);
    const ext = spec.extensions?.['x.pipeshub'] as { mcp: Array<Record<string, unknown>> };
    expect(ext.mcp).to.have.length(1);
    expect(ext.mcp[0]!['instanceId']).to.equal('mcp-rovo');
    expect(ext.mcp[0]!['tools']).to.equal('at_first_use');
    expect(spec.grants.tools.filter((grant) => grant.toolId.startsWith('mcp@'))).to.deep.equal([]);
    expect(unlistedMcpNotices(whole)).to.deep.equal([]);
  });

  it('a run granting a server whole requires mcp_first_use; one that does not, never', () => {
    const listed = agentDefinition({ ...whole, mcpServers: [{ ...whole.mcpServers[0]!, atFirstUse: undefined }] });
    expect(admissionRequirements(run([]), [], 'loop', null, null, false, false, false, true, false, whole).features)
      .to.include(MCP_FIRST_USE_FEATURE);
    expect(admissionRequirements(run([]), [], 'loop', null, null, false, false, false, true, false, listed).features)
      .to.not.include(MCP_FIRST_USE_FEATURE);
  });
});
