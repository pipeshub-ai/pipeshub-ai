/**
 * CTL-5b: a default-chat Agent-mode run that proceeds on the harness without some of the person's MCP servers (their
 * tools could not be named at admission) tells them so once, on the answer. Node journals the notice with run.admitted;
 * the projector shows it live and saves it as a part like the others.
 */
import 'reflect-metadata';
import { expect } from 'chai';
import { AgentAuthorizer } from '../../../src/modules/agent-harness/application/admission/policy-steps';
import { policyContext, SubmitDurableRun } from '../../../src/modules/agent-harness/application/admission/context';
import { admittedJournalAppend } from '../../../src/modules/agent-harness/domain/execution';
import { JournalEvent } from '../../../src/modules/agent-harness/domain/journal';
import { ASSISTANT_AGENT_DEFINITION_KEY } from '../../../src/modules/agent-harness/domain/rollout';
import { foldTimeline } from '../../../src/modules/agent-harness/projection/timeline';
import { buildParts } from '../../../src/modules/agent-harness/projection/transcript';
import { ArtifactAccess } from '../../../src/modules/agent-harness/projection/artifact-access';
import { agentDefinition } from './support/agent-definition';

const unlisted = agentDefinition({
  agentKey: ASSISTANT_AGENT_DEFINITION_KEY,
  definitionVersion: 1,
  models: [],
  defaultReasoningEffort: null,
  skills: [],
  webSearch: null,
  mcpServers: [{ instanceId: 'mcp-1', name: 'notes', typeId: 'notes', tools: [] }],
});

const assistant = (mcpServers: typeof unlisted.mcpServers) => agentDefinition({ ...unlisted, mcpServers });

const request = (sessionId?: string): SubmitDurableRun => ({
  orgId: 'org-1',
  actorId: 'user-1',
  ...(sessionId !== undefined ? { sessionId } : {}),
  idempotencyKey: 'k',
  deadlineMs: 60_000,
  authorization: 'Bearer caller',
  input: { query: 'q', recordIds: [], filters: {}, attachments: [], model: { chatMode: 'agent' } },
});

const authorizer = (definition = unlisted, logged: Array<{ event: string; meta: unknown }> = []) =>
  new AgentAuthorizer(
    { authorizeRun: async () => definition } as never,
    { info: (event: string, meta: unknown) => logged.push({ event, meta }) } as never,
  );

describe('CTL-5b: the notice when a durable assistant run proceeds without its MCP servers', () => {
  for (const [name, sessionId] of [['a new conversation', undefined], ['a conversation already on the harness', 'ses-1']] as const) {
    it(`${name} is admitted with the notice (MCPLIVE: never routed to today's loop)`, async () => {
      const context = policyContext(request(sessionId), {});
      await authorizer().run(context);
      expect(context.notices).to.deep.equal([{ code: 'mcp_unavailable', item: 'notes' }]);
    });
  }

  it('the notice is journaled with run.admitted, by Node', () => {
    const append = admittedJournalAppend(
      { orgId: 'org-1', sessionId: 'ses-1', runId: 'run-1', actorId: 'user-1' },
      'session',
      undefined,
      'interactive',
      [{ code: 'mcp_unavailable', item: 'Atlassian Rovo', reason: 'sign_in', instance: 'mcp-rovo' }],
    );
    expect(append.events.map((event) => event.type)).to.deep.equal(['run.admitted', 'x.pipeshub.run.notice']);
    expect(append.events[1]!.data).to.deep.equal({
      code: 'mcp_unavailable', item: 'Atlassian Rovo', reason: 'sign_in', instance: 'mcp-rovo',
    });
    expect(append.events[1]!.visibility).to.equal('owner');
  });

  it('the saved parts carry it once, first', () => {
    let seq = 0;
    const event = (type: string, data: Record<string, unknown>, writer = 'node'): JournalEvent =>
      ({
        orgId: 'org-1', sessionId: 'ses-1', streamId: 'ses-1', seq: (seq += 1), runId: 'run-1',
        type, schemaVersion: 1, critical: !type.startsWith('x.'), ts: '2026-10-15T12:00:00.000Z',
        writer, visibility: 'owner', data,
      }) as JournalEvent;
    const parts = buildParts(
      foldTimeline(
        [event('run.admitted', { runKind: 'interactive' }), event('x.pipeshub.run.notice', { code: 'mcp_unavailable' })],
        'run-1',
      ),
    );
    expect(parts).to.deep.equal([{ type: 'notice', code: 'mcp_unavailable', runId: 'run-1' }]);
  });
});

describe('MCPNOTE-1: the MCP notice names each server left out, and why', () => {
  const rovo = { instanceId: 'mcp-rovo', name: 'Atlassian Rovo', typeId: 'atlassian', tools: [] };
  const saas = {
    instanceId: 'mcp-saas', name: 'e2esaas', typeId: null,
    tools: [{ name: 'lookup_note', fullName: 'mcp_e2esaas_lookup_note' }],
  };

  it('a server with tools is used and named nowhere; each one without is named with its own reason', async () => {
    const logged: Array<{ event: string; meta: unknown }> = [];
    const context = policyContext(request(), {});
    await authorizer(
      assistant([
        saas,
        { ...rovo, unavailable: { reason: 'sign_in', signInHere: true } },
        { ...rovo, instanceId: 'mcp-linear', name: 'Linear', unavailable: { reason: 'not_approved' } },
        { ...rovo, instanceId: 'mcp-down', name: 'Wiki', unavailable: { reason: 'unreachable' } },
      ]),
      logged,
    ).run(context);
    expect(context.notices).to.deep.equal([
      { code: 'mcp_unavailable', item: 'Atlassian Rovo', reason: 'sign_in', instance: 'mcp-rovo' },
      // R27-SEAM: a server waiting for approval names itself, so the admin's link opens it.
      { code: 'mcp_unavailable', item: 'Linear', reason: 'not_approved', instance: 'mcp-linear' },
      { code: 'mcp_unavailable', item: 'Wiki', reason: 'unreachable' },
    ]);
    const line = logged.find((entry) => entry.event === 'run.assistant_mcp_unavailable');
    expect(line?.meta).to.include({ servers: 3, reasons: 'not_approved,sign_in,unreachable' });
    // The log names how many and why, never a server.
    expect(JSON.stringify(line?.meta)).not.to.include('Rovo');
  });

  it('only a sign-in the person can redo in the chat names the instance to sign in to', async () => {
    const context = policyContext(request(), {});
    await authorizer(
      assistant([
        { ...rovo, unavailable: { reason: 'sign_in', signInHere: false } },
        { ...rovo, instanceId: 'mcp-2', name: 'Two', unavailable: { reason: 'unreachable', signInHere: true } },
        { ...rovo, instanceId: 'not an id!', name: 'Three', unavailable: { reason: 'not_approved' } },
      ]),
    ).run(context);
    expect(context.notices).to.deep.equal([
      { code: 'mcp_unavailable', item: 'Atlassian Rovo', reason: 'sign_in' },
      { code: 'mcp_unavailable', item: 'Two', reason: 'unreachable' },
      { code: 'mcp_unavailable', item: 'Three', reason: 'not_approved' },
    ]);
  });

  it("an older authority's server is still named; a reason it does not know is dropped, not refused", async () => {
    const { agentRunDefinitionSchema } = await import('../../../src/modules/agent-harness/domain/agent-definition');
    const parsed = agentRunDefinitionSchema.parse({
      ...assistant([]),
      mcpServers: [{ ...rovo, unavailable: { reason: 'forgot' } }, { ...rovo, instanceId: 'mcp-2', name: 'Two' }],
    });
    const context = policyContext(request(), {});
    await authorizer(parsed).run(context);
    expect(context.notices).to.deep.equal([
      { code: 'mcp_unavailable', item: 'Atlassian Rovo' },
      { code: 'mcp_unavailable', item: 'Two' },
    ]);
  });

  it("a saved agent's servers are its editor's business: no notice", async () => {
    const context = policyContext(request(), {});
    await authorizer(agentDefinition({ mcpServers: [{ ...rovo, unavailable: { reason: 'sign_in' } }] })).run(context);
    expect(context.notices ?? []).to.deep.equal([]);
  });

  it('the saved parts carry each server once, with its reason and sign-in', () => {
    let seq = 0;
    const event = (type: string, data: Record<string, unknown>): JournalEvent =>
      ({
        orgId: 'org-1', sessionId: 'ses-1', streamId: 'ses-1', seq: (seq += 1), runId: 'run-1',
        type, schemaVersion: 1, critical: !type.startsWith('x.'), ts: '2026-11-11T12:00:00.000Z',
        writer: 'node', visibility: 'owner', data,
      }) as JournalEvent;
    const signIn = { code: 'mcp_unavailable', item: 'Atlassian Rovo', reason: 'sign_in', instance: 'mcp-rovo' };
    const parts = buildParts(
      foldTimeline(
        [
          event('run.admitted', { runKind: 'interactive' }),
          event('x.pipeshub.run.notice', signIn),
          event('x.pipeshub.run.notice', { code: 'mcp_unavailable', item: 'Linear', reason: 'not_approved' }),
          event('x.pipeshub.run.notice', signIn),
        ],
        'run-1',
      ),
    );
    expect(parts).to.deep.equal([
      { type: 'notice', ...signIn, runId: 'run-1' },
      { type: 'notice', code: 'mcp_unavailable', item: 'Linear', reason: 'not_approved', runId: 'run-1' },
    ]);
  });

  it("only the person whose run it is reads which of their servers and toolsets it went without", () => {
    const message = {
      content: 'Done.',
      parts: [
        { type: 'notice', code: 'mcp_unavailable', item: 'Atlassian Rovo', reason: 'sign_in', instance: 'mcp-rovo' },
        { type: 'notice', code: 'credential_missing', item: 'Jira', kind: 'toolset' },
        { type: 'notice', code: 'resumed' },
        { type: 'text', content: 'Done.', isFinal: true },
      ],
    };
    const [shared] = ArtifactAccess.showing([]).messages([message]) as Array<{ parts: Array<{ type: string; code?: string }> }>;
    expect(shared!.parts).to.deep.equal([
      { type: 'notice', code: 'resumed' },
      { type: 'text', content: 'Done.', isFinal: true },
    ]);
    expect(JSON.stringify(shared)).not.to.include('Rovo');
  });

  it('D-MCP-5: any agent names the tools the description scanner held back, owner-only, logged without names', async () => {
    const logged: Array<{ event: string; meta: unknown }> = [];
    const context = policyContext(request(), {});
    await authorizer(
      agentDefinition({ mcpServers: [{ ...saas, held: ['exfiltrate', 'leak'] }, { ...rovo, unavailable: { reason: 'flagged' } }] }),
      logged,
    ).run(context);
    expect(context.notices).to.deep.equal([{ code: 'mcp_tool_held', item: 'e2esaas', tools: ['exfiltrate', 'leak'] }]);
    const line = logged.find((entry) => entry.event === 'run.mcp_tools_held');
    expect(line?.meta).to.include({ servers: 1, tools: 2, reason: 'mcp_tool_description_flagged' });
    expect(JSON.stringify(line?.meta)).not.to.include('exfiltrate');
    const [shared] = ArtifactAccess.showing([]).messages([
      { content: 'x', parts: [{ type: 'notice', ...context.notices![0] as object }] },
    ]) as Array<{ parts: unknown[] }>;
    expect(shared!.parts).to.deep.equal([]);
  });

  it('D-MCP-5: the assistant says a server whose every tool was flagged went unused, and why', async () => {
    const context = policyContext(request(), {});
    await authorizer(assistant([{ ...rovo, unavailable: { reason: 'flagged' } }])).run(context);
    expect(context.notices).to.deep.equal([{ code: 'mcp_unavailable', item: 'Atlassian Rovo', reason: 'flagged' }]);
  });

  it('the held tools are journaled and saved as the notice carries them', () => {
    const append = admittedJournalAppend(
      { orgId: 'org-1', sessionId: 'ses-1', runId: 'run-1', actorId: 'user-1' },
      'session',
      undefined,
      'interactive',
      [{ code: 'mcp_tool_held', item: 'e2esaas', tools: ['exfiltrate'] }],
    );
    expect(append.events[1]!.data).to.deep.equal({ code: 'mcp_tool_held', item: 'e2esaas', tools: ['exfiltrate'] });
    const parts = buildParts(foldTimeline([
      { ...append.events[0]!, orgId: 'org-1', sessionId: 'ses-1', streamId: 'ses-1', seq: 1, runId: 'run-1', ts: '2026-11-11T12:00:00.000Z', writer: 'node' } as JournalEvent,
      { ...append.events[1]!, orgId: 'org-1', sessionId: 'ses-1', streamId: 'ses-1', seq: 2, runId: 'run-1', ts: '2026-11-11T12:00:00.000Z', writer: 'node' } as JournalEvent,
    ], 'run-1'));
    expect(parts).to.deep.include({ type: 'notice', code: 'mcp_tool_held', item: 'e2esaas', tools: ['exfiltrate'], runId: 'run-1' });
  });
});
